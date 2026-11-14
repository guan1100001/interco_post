"""流水线状态机服务（业务编排层，不依赖任何 HTTP 类型）。

把 CLI 中两处阻塞式 input() 交互改造成持久化的会话状态机：

    OCR识别 -> [对话确认 y/e/s] -> 落库 -> SC001场景匹配 -> 本体推理
            -> SHACL校验 -> [对话审批 y/n] -> 双主体过账 -> 完成

    业务规则中断（场景未命中 / 校验未通过）时不再往下执行，会话停在中断态，
    由人工点「确认」正常结束（ACKNOWLEDGED，无凭证）或点「重新识别这张」重来。

每个阶段的结果以结构化消息写回会话，前端通过轮询会话快照即可还原全过程。
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from backend.app.core.errors import AppError, ConflictError, NotFoundError
from backend.app.core.logging import get_logger
from backend.app.core.numbers import to_optional_float
from backend.app.repositories import session_repo as repo
from backend.app.schemas.pipeline import (
    ActionOut, MessageOut, SessionBriefOut, SessionOut,
)
from backend.app.services import (
    file_service, legacy_adapter as legacy, runtime_settings as runtime,
)

log = get_logger(__name__)

# 阶段 / 状态
STAGE_OCR = "AWAIT_OCR_CONFIRM"
STAGE_SCENARIO = "SCENARIO"
STAGE_REASONING = "REASONING"
STAGE_APPROVAL = "AWAIT_APPROVAL"
STAGE_DONE = "DONE"
STATUS_WAITING = "WAITING"
STATUS_RUNNING = "RUNNING"
STATUS_COMPLETED = "COMPLETED"
STATUS_FAILED = "FAILED"
STATUS_SKIPPED = "SKIPPED"
STATUS_CANCELLED = "CANCELLED"
# 业务规则中断（如场景未命中）经人工点「确认」后，转为正常结束，
# 与 COMPLETED（已过账）严格区分：此状态没有任何本体推理与过账凭证。
STATUS_ACKNOWLEDGED = "ACKNOWLEDGED"

ACT_CONFIRM = {"action": "confirm", "label": "确认保存 (y)", "style": "primary"}
ACT_EDIT = {"action": "edit", "label": "修改字段 (e)", "style": "default"}
ACT_SKIP = {"action": "skip", "label": "跳过 (s)", "style": "warn"}
ACT_APPROVE = {"action": "approve", "label": "确认过账 (y)", "style": "primary"}
ACT_REJECT = {"action": "reject", "label": "取消过账 (n)", "style": "danger"}
ACT_RESTART = {"action": "restart", "label": "重新导入", "style": "default"}
ACT_QUERY = {"action": "query", "label": "查询该发票", "style": "default"}
# 「确认」排在 ACT_RESTART 前面：前端按 available_actions 顺序渲染，确认在左、重新识别在右。
ACT_ACKNOWLEDGE = {"action": "acknowledge", "label": "确认", "style": "primary"}

HEADER_FIELDS = {
    "invoice_number", "invoice_type", "issue_date", "seller_name", "buyer_name",
    "seller_tax_id", "buyer_tax_id", "gross_amount", "tax_rate", "net_amount",
    "tax_amount", "currency", "remark", "invoice_issuer",
}
ITEM_FIELDS = {"description", "quantity", "unit", "unit_price", "amount", "tax_rate", "tax_amount"}
FLOAT_FIELDS = {"gross_amount", "net_amount", "tax_amount", "quantity", "unit_price", "amount"}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _cast(key: str, value: Any) -> Any:
    if key in FLOAT_FIELDS:
        # 「修改字段」表单里数值输入框留空会传 ''。SQLite 不校验列类型，
        # 原样落库会把 REAL 列写成 TEXT，之后「查看详情」的数值校验就会 500。
        # 这里统一转成 float 或 None（SQL NULL），不可解析的值也不再写入脏字符串。
        return to_optional_float(value)
    return "" if value is None else str(value)


def _msg(role: str, kind: str, text: str, *, actions: list | None = None,
         data: dict | None = None) -> dict:
    return {
        "id": uuid.uuid4().hex[:10], "role": role, "kind": kind, "text": text,
        "ts": datetime.now().strftime("%H:%M:%S"),
        "actions": actions or [], "data": data,
    }


def parse_intent(text: str, stage: str) -> str | None:
    """轻量意图识别：快捷动作为主，自然语言输入作为补充。"""
    t = (text or "").strip().lower()
    if not t:
        return None
    if t in ("s", "skip", "跳过", "取消"):
        return "reject" if stage == STAGE_APPROVAL else "skip"
    if t in ("e", "edit", "修改", "编辑"):
        return "edit"
    if t in ("y", "yes", "ok", "confirm", "确认", "确定", "保存", "同意"):
        return "approve" if stage == STAGE_APPROVAL else "confirm"
    if t in ("n", "no", "cancel", "拒绝", "不"):
        return "reject" if stage == STAGE_APPROVAL else "skip"
    if t in ("help", "?", "帮助"):
        return "help"
    if stage == STAGE_APPROVAL:
        if any(k in t for k in ("取消", "拒绝", "不要", "不批", "reject", "cancel")):
            return "reject"
        if any(k in t for k in ("过账", "确认", "同意", "批准", "执行", "提交", "approve", "post")):
            return "approve"
    else:
        if any(k in t for k in ("修改", "编辑", "改一下", "edit", "change")):
            return "edit"
        if any(k in t for k in ("跳过", "不用了", "skip")):
            return "skip"
        if any(k in t for k in ("确认", "保存", "没问题", "正确", "confirm")):
            return "confirm"
    if any(k in t for k in ("重新", "重来", "restart")):
        return "restart"
    return None


class PipelineService:
    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------ 持久化辅助
    def _write(
        self, row, *, stage: str | None = None, status: str | None = None,
        actions: list | None = None, ctx_patch: dict | None = None,
        messages: list | None = None, draft: dict | None = None,
        issues: list | None = None, invoice_number: str | None = None,
    ) -> None:
        """一次提交写完会话的所有变更：保证调用遗留写函数前 ORM 事务已释放。"""
        if stage is not None:
            row.stage = stage
        if status is not None:
            row.status = status
        if invoice_number is not None:
            row.invoice_number = invoice_number
        if draft is not None:
            row.draft_json = repo.dumps(draft)
        if issues is not None:
            row.issues_json = repo.dumps(issues)
        ctx = repo.loads(row.context_json, {})
        if ctx_patch:
            ctx.update(ctx_patch)
        if actions is not None:
            ctx["available_actions"] = actions
        row.context_json = repo.dumps(ctx)
        if messages:
            history = repo.loads(row.messages_json, [])
            history.extend(messages)
            row.messages_json = repo.dumps(history[-200:])
        row.updated_at = _now()
        repo.save(self.db, row)

    def _fail(self, row, text: str, *, ctx_patch: dict | None = None) -> None:
        """技术性失败（OCR/落库/推理异常）：只能重新识别，不提供确认结束。"""
        self._write(row, stage=STAGE_DONE, status=STATUS_FAILED,
                    actions=[ACT_RESTART], ctx_patch=ctx_patch,
                    messages=[_msg("agent", "error", text)])

    def _business_stop(
        self, row, *, error: dict, ctx_patch: dict | None = None,
        messages: list | None = None, reason: str | None = None,
    ) -> None:
        """按业务规则中断：不进入本体推理与过账，等待人工点「确认」正常结束。

        与 _fail 的区别：这是可预期的业务结论，用户除了「重新识别这张」，
        还可以点「确认」把本张正常收尾，不必一直停在「中断 / 错误」。
        """
        patch = dict(ctx_patch or {})
        patch.update({
            "outcome": "BUSINESS_STOP",
            "outcome_reason": reason or error["text"],
            "acknowledge_pending": True,
        })
        self._write(
            row, stage=STAGE_DONE, status=STATUS_FAILED,
            actions=[ACT_ACKNOWLEDGE, ACT_RESTART],
            ctx_patch=patch,
            messages=[*(messages or []), error],
        )

    def _on_acknowledge(self, row) -> None:
        """人工确认业务中断结论：会话正常结束，全程不产生任何推理与过账凭证。"""
        ctx = repo.loads(row.context_json, {})
        if row.status != STATUS_FAILED or not ctx.get("acknowledge_pending"):
            raise ConflictError("当前会话不在「等待确认业务中断」状态")
        reason = ctx.get("outcome_reason") or "按业务规则中断。"
        self._write(
            row, stage=STAGE_DONE, status=STATUS_ACKNOWLEDGED,
            actions=[ACT_RESTART],
            ctx_patch={
                "acknowledge_pending": False, "acknowledged": True,
                "acknowledged_at": _now(), "outcome": "BUSINESS_STOP_ACKNOWLEDGED",
            },
            messages=[_msg(
                "agent", "closed",
                f"已人工确认：{reason}本张发票按业务规则正常结束，"
                "未进入本体推理与过账，未生成会计凭证。可手动开始下一张。",
            )],
        )

    # ------------------------------------------------------------------ 创建
    def create(self, file_path: str, ocr_mode: str | None = None) -> SessionOut:
        legacy.init_legacy_db()
        resolved, size = file_service.require_file(file_path)
        now = _now()
        row = repo.create(
            self.db, id=uuid.uuid4().hex, file_path=resolved,
            invoice_number=None, stage="INIT", status=STATUS_RUNNING,
            draft_json="{}", issues_json="[]", context_json=repo.dumps({"file_size": size}),
            messages_json="[]", created_at=now, updated_at=now,
        )
        self._run_ocr(row, ocr_mode)
        return self.to_out(row)

    def _run_ocr(self, row, ocr_mode: str | None = None) -> None:
        """阶段1：OCR -> 结构化草稿 -> 数据质量校验，等待人工确认。"""
        try:
            raw, source = legacy.fetch_ocr(row.file_path, ocr_mode)
            draft = legacy.transform(raw, row.file_path)
            issues = legacy.check_quality(draft["invoice"], draft["items"])
        except AppError as exc:
            # 具体原因（代理拦截 / 超时 / 地址不通）与处理建议由适配层给出中文诊断。
            self._fail(row, f"OCR识别失败：{exc.message}")
            return
        except Exception as exc:  # noqa: BLE001
            log.exception("ocr_stage_failed")
            self._fail(row, f"OCR识别阶段异常：{type(exc).__name__}: {exc}")
            return

        blocking = legacy.blocking(issues)
        inv = draft["invoice"]
        messages = [
            _msg("system", "system", f"开始识别发票文件：{row.file_path}"),
            _msg("agent", "ocr_result",
                 f"识别完成（{source}）：发票号 {inv.get('invoice_number') or '缺失'}，"
                 f"{inv.get('seller_name')} → {inv.get('buyer_name')}，"
                 f"价税合计 {inv.get('gross_amount')} {inv.get('currency')}，"
                 f"行项目 {len(draft['items'])} 条。请核对后确认。",
                 data={"invoice": inv, "items": draft["items"], "ocr_source": source}),
        ]
        if "模拟" in source:
            messages.append(_msg(
                "system", "system",
                "重要提示：本次识别使用的是模拟OCR数据（真实OCR不可用或已手动选择 mock），"
                "仅供演示流程，不能作为真实发票的过账依据。"
                "如需真实识别，请检查 OCR_URL / 内网连通性后以 live 或 auto 模式重新识别。",
            ))
        if issues:
            messages.append(_msg(
                "agent", "quality",
                f"数据质量校验：{len(blocking)} 个阻断项、{len(issues) - len(blocking)} 个警告。",
                data={"issues": issues, "blocking": len(blocking)},
            ))
        else:
            messages.append(_msg("agent", "quality", "数据质量校验：全部通过。",
                                 data={"issues": [], "blocking": 0}))
        if blocking:
            messages.append(_msg("agent", "error",
                                 "存在阻断项，请先「修改字段」补全后再确认保存："
                                 + "；".join(i["detail"] for i in blocking)))
        self._write(
            row, stage=STAGE_OCR, status=STATUS_WAITING,
            actions=[ACT_CONFIRM, ACT_EDIT, ACT_SKIP],
            ctx_patch={
                "ocr_source": source, "scenario": None, "reasoning": None,
                "shacl": None, "payload_preview": None, "posting": None,
                # 重新识别时必须清掉上一次业务中断标记，否则下一轮「确认，继续」
                # 会被误判成「确认结束」。
                "outcome": None, "outcome_reason": None,
                "acknowledge_pending": False, "acknowledged": False,
            },
            messages=messages, draft=draft, issues=issues,
        )

    # -------------------------------------------------------------- 对话驱动
    def handle(self, session_id: str, action: str | None, text: str | None,
               patch: dict | None) -> SessionOut:
        row = repo.get(self.db, session_id)
        if row is None:
            raise NotFoundError(f"会话不存在: {session_id}")

        normalized = (action or "").strip().lower() or parse_intent(text or "", row.stage)
        if not normalized:
            self._write(row, messages=[
                _msg("user", "text", text or ""),
                _msg("agent", "text",
                     "没有理解这条指令。可以直接点击下方的快捷按钮，或输入 y（确认）/ e（修改）/ s（跳过）；"
                     "过账阶段输入 y（确认过账）/ n（取消）。"),
            ])
            return self.to_out(row)

        ctx = repo.loads(row.context_json, {})
        # 业务规则中断态下，输入框输入「确认」与点快捷按钮「确认」等价：
        # 不能走 _on_confirm（那是识别结果确认，只在 AWAIT_OCR_CONFIRM 有效）。
        if normalized == "confirm" and ctx.get("acknowledge_pending"):
            normalized = "acknowledge"

        if row.status != STATUS_WAITING and normalized not in (
            "restart", "help", "query", "acknowledge"
        ):
            raise ConflictError(
                f"会话当前状态为 {row.status}/{row.stage}，仅支持「确认」结束或「重新导入」"
            )

        user_text = text or {
            "confirm": "确认识别结果", "edit": "修改识别字段", "skip": "跳过这张发票",
            "approve": "确认双主体过账", "reject": "取消过账", "restart": "重新导入",
            "acknowledge": "确认业务中断，正常结束本张", "help": "查看可用操作",
        }.get(normalized, normalized)
        self._write(row, messages=[_msg("user", "text", user_text)])

        if normalized == "confirm":
            self._on_confirm(row)
        elif normalized == "acknowledge":
            self._on_acknowledge(row)
        elif normalized == "edit":
            self._on_edit(row, patch)
        elif normalized == "skip":
            self._write(row, stage=STAGE_DONE, status=STATUS_SKIPPED, actions=[ACT_RESTART],
                        messages=[_msg("agent", "text", "已跳过，本张发票未写入数据库。")])
        elif normalized == "approve":
            self._on_approve(row)
        elif normalized == "reject":
            legacy.cancel_posting(row.invoice_number or "")
            self._write(row, stage=STAGE_DONE, status=STATUS_CANCELLED, actions=[ACT_RESTART],
                        messages=[_msg("agent", "text", "已取消过账，未生成会计凭证。")])
        elif normalized == "restart":
            self._write(row, stage="INIT", status=STATUS_RUNNING, actions=[],
                        ctx_patch={"outcome": None, "outcome_reason": None,
                                   "acknowledge_pending": False, "acknowledged": False},
                        messages=[_msg("system", "system", "重新开始识别流程。")])
            self._run_ocr(row)
        else:  # help / query
            self._write(row, messages=[_msg(
                "agent", "text",
                "当前可执行操作请见下方按钮。流程：OCR识别 → 人工确认 → SC001场景匹配 → "
                "本体推理 → SHACL校验 → 过账审批 → 双主体过账。"
            )])
        return self.to_out(row)

    # ------------------------------------------------------------ 各阶段实现
    def _on_edit(self, row, patch: dict | None) -> None:
        if not patch:
            self._write(row, messages=[_msg(
                "agent", "text", "请在字段表单中修改后提交，我会重新做一次数据质量校验。",
                data={"open_editor": True},
            )])
            return
        draft = repo.loads(row.draft_json, {"invoice": {}, "items": []})
        for key, value in (patch.get("invoice") or {}).items():
            if key in HEADER_FIELDS:
                draft["invoice"][key] = _cast(key, value)
        for change in patch.get("items") or []:
            target = next(
                (it for it in draft["items"] if it.get("item_no") == change.get("item_no")), None
            )
            if target is None:
                continue
            for key, value in change.items():
                if key in ITEM_FIELDS:
                    target[key] = _cast(key, value)
        issues = legacy.check_quality(draft["invoice"], draft["items"])
        blocking = legacy.blocking(issues)
        inv = draft["invoice"]
        self._write(
            row, stage=STAGE_OCR, status=STATUS_WAITING,
            actions=[ACT_CONFIRM, ACT_EDIT, ACT_SKIP],
            messages=[
                _msg("agent", "ocr_result", "字段已更新，以下是修改后的识别结果：",
                     data={"invoice": inv, "items": draft["items"],
                           "ocr_source": repo.loads(row.context_json, {}).get("ocr_source")}),
                _msg("agent", "quality",
                     f"重新校验：{len(blocking)} 个阻断项、{len(issues) - len(blocking)} 个警告。",
                     data={"issues": issues, "blocking": len(blocking)}),
            ],
            draft=draft, issues=issues,
        )

    def _on_confirm(self, row) -> None:
        if row.stage != STAGE_OCR:
            raise ConflictError("当前不在「等待确认识别结果」阶段")
        draft = repo.loads(row.draft_json, {})
        invoice, items = draft.get("invoice") or {}, draft.get("items") or []
        issues = legacy.check_quality(invoice, items)
        blocking = legacy.blocking(issues)
        if not invoice.get("invoice_number"):
            self._write(row, messages=[_msg(
                "agent", "error", "发票号码为空，无法入库。请「修改字段」补全发票号码。"
            )])
            return
        if blocking:
            self._write(row, messages=[_msg(
                "agent", "error", "仍有阻断项，暂不能入库：" + "；".join(i["detail"] for i in blocking)
            )])
            return

        try:
            legacy.save_invoice(invoice, items, issues)
        except Exception as exc:  # noqa: BLE001
            log.exception("save_invoice_failed")
            self._fail(row, f"写入发票数据库失败：{type(exc).__name__}: {exc}")
            return
        self._write(row, invoice_number=invoice["invoice_number"], messages=[
            _msg("agent", "text", f"已确认并写入 SQLite：发票 {invoice['invoice_number']}。")
        ])
        self._advance(row, invoice)

    def _advance(self, row, invoice: dict) -> None:
        """阶段2-4前半：场景匹配 -> 本体推理 -> SHACL -> 等待过账审批。"""
        number = invoice["invoice_number"]
        try:
            legacy.seed_ontology()
            scenario = legacy.match_scenario(invoice)
        except Exception as exc:  # noqa: BLE001
            log.exception("scenario_stage_failed")
            self._fail(row, f"SC001场景匹配失败：{type(exc).__name__}: {exc}")
            return

        sc_data = {
            "matched": scenario["matched"], "scenario_id": scenario["scenario_id"],
            "reasoning": scenario["reasoning"], "checks": scenario["checks"],
            "failures": scenario["failures"], "seller": scenario["seller"],
            "buyer": scenario["buyer"],
        }
        sc_msg = _msg(
            "agent", "scenario",
            f"SC001场景匹配：{'命中' if scenario['matched'] else '未命中'}。{scenario['reasoning']}",
            data=sc_data,
        )
        if not scenario["matched"]:
            self._business_stop(
                row, ctx_patch={"scenario": sc_data}, messages=[sc_msg],
                error=_msg("agent", "error",
                           "场景未命中，按业务规则中断，不进入本体推理与过账。"),
            )
            return

        try:
            final = legacy.reason(number, "SC001")
        except AppError as exc:
            self._fail(row, f"本体推理失败：{exc.message}", ctx_patch={"scenario": sc_data})
            return
        except Exception as exc:  # noqa: BLE001
            log.exception("reasoning_stage_failed")
            self._fail(row, f"本体推理异常：{type(exc).__name__}: {exc}",
                       ctx_patch={"scenario": sc_data})
            return

        validation = final.get("validation") or {}
        conforms = bool(validation.get("conforms"))
        reasoning_data = {
            "scenario_id": (final.get("scenario") or {}).get("scenario_id"),
            "chain": [c.get("rule_id") for c in (final.get("chain") or [])],
            "summary": final.get("summary"),
            "confidence": final.get("confidence"),
            "conforms": conforms,
            "errors": final.get("errors") or [],
            "checks": {k: v for k, v in validation.items() if isinstance(v, bool)},
        }
        rs_msg = _msg(
            "agent", "reasoning",
            f"本体推理完成：规则链 {'→'.join(reasoning_data['chain']) or '—'}，"
            f"SHACL/借贷/税额校验 {'通过' if conforms else '未通过'}。{final.get('summary') or ''}",
            data=reasoning_data,
        )
        if not conforms:
            self._business_stop(
                row, ctx_patch={"scenario": sc_data, "reasoning": reasoning_data},
                messages=[sc_msg, rs_msg],
                error=_msg(
                    "agent", "error",
                    "推理结果未通过校验，按业务规则中断，不调用SAP过账。原因："
                    + "；".join(str(e) for e in reasoning_data["errors"]),
                ),
            )
            return

        try:
            payload = legacy.load_payload(number)
            if not payload:
                raise ValueError("未找到已持久化的推理载荷")
            report = legacy.validate_payload(payload)
            preview = legacy.preview_payload(payload)
        except Exception as exc:  # noqa: BLE001
            log.exception("payload_stage_failed")
            self._fail(row, f"过账载荷准备失败：{type(exc).__name__}: {exc}",
                       ctx_patch={"scenario": sc_data, "reasoning": reasoning_data})
            return

        public_report = legacy.public_report(report)
        if not report.get("conforms"):
            self._business_stop(
                row, ctx_patch={"scenario": sc_data, "reasoning": reasoning_data,
                                "shacl": public_report},
                messages=[sc_msg, rs_msg],
                error=_msg("agent", "error", "SHACL复检未通过，阻断过账。",
                           data={"shacl": public_report}),
            )
            return

        # 过账模式可在界面运行时切换：审批提示展示「当前」模式，
        # 实际执行时再读一次最新值，避免用户切换模式后仍按旧模式过账。
        approval_mode = runtime.get_sap_mode()
        mode_note = {
            "mock": "当前为【模拟过账】模式：会生成模拟凭证并完整写入本地数据库，不连接真实SAP。",
            "dry_run": "当前为【预览(dry-run)】模式：只校验与预览载荷，不写入凭证。",
            "live": "当前为【真实SAP】模式：将调用 ZfmAico002 执行双主体同步过账，不可撤销。",
        }[approval_mode]
        ap_msg = _msg(
            "agent", "approval",
            f"SHACL复检全部通过，双主体过账载荷已就绪（"
            f"{preview['company_a']['company_code']} + {preview['company_b']['company_code']}）。"
            f"{mode_note} 实际以点「确认过账」时左侧设置的过账模式为准。"
            f"双主体同步过账需要审批，确认过账吗？(y/n)",
            data={"shacl": public_report, "preview": preview, "sap_mode": approval_mode},
        )
        self._write(
            row, stage=STAGE_APPROVAL, status=STATUS_WAITING,
            actions=[ACT_APPROVE, ACT_REJECT],
            ctx_patch={"scenario": sc_data, "reasoning": reasoning_data,
                       "shacl": public_report, "payload_preview": preview,
                       "sap_mode": approval_mode},
            messages=[sc_msg, rs_msg, ap_msg],
        )

    def _on_approve(self, row) -> None:
        if row.stage != STAGE_APPROVAL:
            raise ConflictError("当前不在「等待过账审批」阶段")
        number = row.invoice_number or ""
        payload = legacy.load_payload(number)
        if not payload:
            self._fail(row, "未找到推理载荷，无法过账。")
            return
        # 用「点确认过账这一刻」的运行时模式，保证界面切到 live 后真的走真实 SAP。
        posting_mode = runtime.get_sap_mode()
        try:
            result = legacy.post(payload, posting_mode)
        except Exception as exc:  # noqa: BLE001
            log.exception("posting_stage_failed")
            self._write(row, messages=[_msg(
                "agent", "error", f"过账执行异常：{type(exc).__name__}: {exc}。可重试或取消。"
            )])
            return
        if not result.get("success"):
            # 把 SAP 返回的 EMsg / 适配层的 reason 原样带出，避免只显示「上游返回失败」
            # 而看不到真实原因（例如缺 zeep、WSDL 不可达、SAP 侧业务报错）。
            response = result.get("response") or {}
            reason = (
                result.get("reason")
                or response.get("EMsg")
                or result.get("summary")
                or "上游返回失败"
            )
            self._write(row, messages=[_msg(
                "agent", "error", f"过账未成功：{reason}。可重试确认过账或取消。"
            )])
            return

        response = result.get("response") or {}
        posting_data = {
            "doc_no": response.get("EBelnr") or ("PREVIEW" if result.get("dry_run") else ""),
            "mode": posting_mode,
            "mock": bool(result.get("mock")),
            "dry_run": bool(result.get("dry_run")),
            "summary": result.get("summary") or "载荷预览完成（dry-run，未生成凭证）",
            "response": response,
        }
        if result.get("dry_run"):
            text = "过账载荷预览完成（dry-run）：未调用SAP、未生成会计凭证。"
        elif result.get("mock"):
            text = (f"双主体模拟过账完成：凭证 {posting_data['doc_no']}，"
                    f"已写入 accounting_documents / posting_events / audit_log。"
                    f"可在右侧「发票数据库」中查询该发票。")
        else:
            text = (f"SAP双主体过账成功：凭证 {posting_data['doc_no']}，"
                    f"状态 {response.get('EStatus')}，消息 {response.get('EMsg')}。")
        self._write(row, stage=STAGE_DONE, status=STATUS_COMPLETED,
                    actions=[ACT_QUERY, ACT_RESTART], ctx_patch={"posting": posting_data},
                    messages=[_msg("agent", "result", text, data=posting_data)])

    # ------------------------------------------------------------------ 查询
    def to_out(self, row) -> SessionOut:
        ctx = repo.loads(row.context_json, {})
        return SessionOut(
            id=row.id, file_path=row.file_path, invoice_number=row.invoice_number,
            stage=row.stage, status=row.status,
            draft=repo.loads(row.draft_json, {}),
            issues=repo.loads(row.issues_json, []),
            context=ctx,
            messages=[MessageOut(**m) for m in repo.loads(row.messages_json, [])],
            available_actions=[ActionOut(**a) for a in ctx.get("available_actions", [])],
            created_at=row.created_at, updated_at=row.updated_at,
        )

    def get(self, session_id: str) -> SessionOut:
        row = repo.get(self.db, session_id)
        if row is None:
            raise NotFoundError(f"会话不存在: {session_id}")
        return self.to_out(row)

    def list_recent(self, limit: int = 20) -> list[SessionBriefOut]:
        return [SessionBriefOut.model_validate(r) for r in repo.list_recent(self.db, limit)]
