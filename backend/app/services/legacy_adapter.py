"""遗留业务模块适配层。

三个 CLI 脚本（invoice_ocr / ontology_reasoning / sap_posting）是领域层，
本层只做：延迟导入、超时可控的 OCR 调用、LangGraph 缺失时的等价降级、
以及无 SAP 环境下的模拟过账。业务规则本身一律复用原函数，不重写。
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from backend.app.core.config import sap_live_missing, settings
from backend.app.core.errors import BadRequestError, InternalError, UpstreamError
from backend.app.core.logging import get_logger
from backend.app.core.numbers import to_optional_float
from backend.app.services import runtime_settings as runtime

log = get_logger(__name__)

# 三个脚本位于项目根目录，且彼此以顶层模块名互相 import。
if str(settings.project_root) not in sys.path:
    sys.path.insert(0, str(settings.project_root))


def _ocr_mod():
    import invoice_ocr
    return invoice_ocr


def _ont_mod():
    import ontology_reasoning
    return ontology_reasoning


def _sap_mod():
    import sap_posting
    return sap_posting


def supported_exts() -> list[str]:
    return sorted(_ocr_mod().SUPPORTED_EXTS)


def init_legacy_db() -> None:
    _ocr_mod().init_db()


# ---------------------------------------------------------------- OCR 阶段

def _ocr_session() -> requests.Session:
    """OCR 是内网直连：默认忽略 HTTP_PROXY / HTTPS_PROXY 等环境代理。

    系统代理（例如 127.0.0.1:53824）常把 10.238.67.9 这类内网地址也一并转发，
    代理抖动时 requests 会抛 ProxyError / ConnectionResetError(10054)。
    trust_env=False 让本次请求完全不走环境代理；确需代理时设置
    OCR_USE_ENV_PROXY=true 即可恢复原行为。
    """
    session = requests.Session()
    session.trust_env = settings.ocr_use_env_proxy
    return session


def _ocr_error_message(exc: Exception) -> str:
    """把 requests 的原始异常转成用户可操作的中文提示，避免抛出大段英文堆栈。"""
    raw = f"{type(exc).__name__}: {exc}"
    proxy = (os.getenv("HTTP_PROXY") or os.getenv("http_proxy")
             or os.getenv("HTTPS_PROXY") or os.getenv("https_proxy") or "")
    if isinstance(exc, requests.exceptions.ProxyError) or "ProxyError" in raw:
        return (
            f"OCR 服务调用失败（{settings.ocr_url}）：本机系统代理 {proxy} 拦截/断开了内网请求"
            "（ProxyError / 10054）。程序已默认绕过代理直连；若仍失败，请确认 OCR_URL 正确、"
            "本机已连接内网或 VPN，也可临时把 OCR 模式切为 auto / mock。"
        )
    if isinstance(exc, requests.exceptions.Timeout):
        return (
            f"OCR 服务调用超时（{settings.ocr_url}，{settings.ocr_timeout}s）："
            "请检查服务是否繁忙或调大 OCR_TIMEOUT；也可临时切为 auto / mock。"
        )
    if isinstance(exc, requests.exceptions.ConnectionError):
        return (
            f"无法连接 OCR 服务（{settings.ocr_url}）：请确认地址正确、本机已连接内网或 VPN。"
            "程序已默认绕过系统代理；如需走代理请设置 OCR_USE_ENV_PROXY=true。"
        )
    return (
        f"OCR 服务调用失败（{settings.ocr_url}）：{raw[:240]}。"
        "请检查 OCR_URL / 内网连通性，或临时切为 auto / mock。"
    )


def _post_ocr(file_path: str, filename: str | None = None) -> dict:
    ocr = _ocr_mod()
    mime = ocr.get_mime_type(file_path)
    with _ocr_session() as session:
        with open(file_path, "rb") as fh:
            resp = session.post(
                settings.ocr_url,
                files={"file": (filename or os.path.basename(file_path), fh, mime)},
                timeout=settings.ocr_timeout,
            )
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict):
        raise UpstreamError("OCR 服务返回格式不是 JSON 对象")
    if data.get("ok", True) is False:
        raise UpstreamError(f"OCR 服务返回失败: {data}")
    return data


def _live_ocr(file_path: str) -> dict:
    """直传；PDF 失败时用 pymupdf 转首页图片重试（可选依赖，缺失则直接失败）。"""
    try:
        return _post_ocr(file_path)
    except Exception as first_error:  # noqa: BLE001 - 需要降级到图片重试
        if not file_path.lower().endswith(".pdf"):
            raise
        try:
            import pymupdf
        except ImportError:
            raise first_error from None
        tmp_paths: list[str] = []
        try:
            doc = pymupdf.open(file_path)
            for page in doc:
                fd, tmp = tempfile.mkstemp(suffix=".png")
                os.close(fd)
                page.get_pixmap(dpi=150).save(tmp)
                tmp_paths.append(tmp)
            doc.close()
            if not tmp_paths:
                raise first_error
            return _post_ocr(tmp_paths[0], filename=os.path.basename(tmp_paths[0]))
        finally:
            for path in tmp_paths:
                if os.path.exists(path):
                    os.remove(path)


def _mock_ocr(file_path: str) -> dict:
    """与真实 OCR 的 merged 结构一致的样例数据，用于内网 OCR 不可达时演示全链路。

    样例满足 SC001：集团内两家 own 公司 + 增值税专用发票 + 可映射科目「检测费」，
    且净额/税额/价税合计与行项目勾稽一致，能通过 SHACL 与借贷平衡校验。
    """
    seed = f"{Path(file_path).stem}-{time.time_ns()}"
    number = str(int(hashlib.sha256(seed.encode()).hexdigest(), 16) % 10 ** 20).zfill(20)
    return {
        "ok": True,
        "merged": {
            "invoice_number": number,
            "invoice_date": datetime.now().strftime("%Y-%m-%d"),
            "invoice_type": "增值税专用发票",
            "seller_name": "北京智米科技有限公司",
            "buyer_name": "北京智米电子科技有限公司",
            "seller_tax_id": "91110108MA00000001",
            "buyer_tax_id": "91110108MA00000002",
            "currency": "CNY",
            "amount_sum": "10000.00",
            "tax_sum": "600.00",
            "total_with_tax": "10600.00",
            "remark": "关联交易检测服务费（模拟OCR数据）",
            "invoice_issuer": "模拟开票员",
            "items": [{
                "name": "*生产生活服务*检测费",
                "qty_num": 1, "unit": "项", "price_num": "10000.00",
                "amount_num": "10000.00", "amount_str": "10000.00",
                "tax_rate": "6%", "tax_num": "600.00", "tax_str": "600.00",
            }],
        },
    }


# auto 模式的失败短路：真实 OCR 每次调用要等 PDF 直传 + 转图重试两次超时，
# 逐张处理时重复等待体验很差。确认服务不可达后，在冷却期内直接降级。
_ocr_down_until = 0.0
_ocr_down_lock = threading.Lock()


def _ocr_short_circuited() -> bool:
    with _ocr_down_lock:
        return _ocr_down_until > time.monotonic()


def mark_ocr_down(reason: str) -> None:
    """标记真实 OCR 暂不可用，冷却期内 auto 模式直接降级。"""
    global _ocr_down_until
    with _ocr_down_lock:
        _ocr_down_until = time.monotonic() + max(0.0, settings.ocr_down_cooldown_sec)
    log.warning("ocr_marked_unavailable", extra={"extra": {
        "cooldown_sec": settings.ocr_down_cooldown_sec, "reason": reason[:160],
    }})


def probe_ocr_service(timeout: float = 0.8) -> bool:
    """毫秒级 TCP 连通性探测：端口不通就没必要等待两次 HTTP 超时。"""
    url = urlparse(settings.ocr_url)
    host = url.hostname
    port = url.port or (443 if url.scheme == "https" else 80)
    if not host:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _mark_ocr_up() -> None:
    global _ocr_down_until
    with _ocr_down_lock:
        _ocr_down_until = 0.0


def fetch_ocr(file_path: str, mode: str | None = None) -> tuple[dict, str]:
    """返回 (OCR原始结果, 来源标签)。auto 模式在真实服务不可用时降级为模拟数据。"""
    mode = (mode or settings.ocr_mode).lower()
    if mode == "mock":
        return _mock_ocr(file_path), "模拟OCR"
    if mode == "live":
        try:
            result = _live_ocr(file_path)
        except Exception as exc:  # noqa: BLE001
            raise UpstreamError(_ocr_error_message(exc)) from exc
        _mark_ocr_up()
        return result, "真实OCR"

    if _ocr_short_circuited():
        log.info("ocr_auto_short_circuit", extra={"extra": {"file": os.path.basename(file_path)}})
        return _mock_ocr(file_path), "模拟OCR（真实OCR服务暂不可用，已跳过重复等待）"
    # 直连探测：单张流程同样不必先等两次 HTTP 超时。
    # 显式配置走代理时，直连探测没有意义，交给 requests 按代理策略判断。
    if not settings.ocr_use_env_proxy and not probe_ocr_service():
        mark_ocr_down(f"TCP 探测失败: {settings.ocr_url}")
        return _mock_ocr(file_path), "模拟OCR（真实OCR服务未连通，已跳过等待）"
    try:
        result = _live_ocr(file_path)
    except Exception as exc:  # noqa: BLE001
        reason = _ocr_error_message(exc)
        mark_ocr_down(reason)
        log.warning("ocr_auto_fallback", extra={"extra": {
            "file": os.path.basename(file_path), "reason": reason[:240],
        }})
        return _mock_ocr(file_path), "模拟OCR（真实OCR不可用，已自动降级，不可用于真实过账）"
    _mark_ocr_up()
    return result, "真实OCR"


def transform(ocr_result: dict, source_file: str) -> dict:
    return _ocr_mod().transform_invoice(ocr_result, source_file=source_file)


def check_quality(invoice: dict, items: list) -> list[dict]:
    return _ocr_mod().check_data_quality(invoice, items)


def blocking(issues: list[dict]) -> list[dict]:
    return _ocr_mod().blocking_issues(issues)


# 数值字段：写库前统一清洗。SQLite 是弱类型，空字符串会原样存进 REAL 列，
# 读取时 Pydantic 无法解析，导致「查看详情」500。这里做最后一道防线，
# 覆盖编辑表单、CLI 或其它调用方传入的任何非数值内容。
_NUMERIC_FIELDS = {"gross_amount", "net_amount", "tax_amount", "quantity", "unit_price", "amount"}


def _clean_numbers(record: dict) -> dict:
    return {
        key: (to_optional_float(value) if key in _NUMERIC_FIELDS else value)
        for key, value in record.items()
    }


def save_invoice(invoice: dict, items: list, issues: list[dict]) -> None:
    clean_items = [_clean_numbers(it) if isinstance(it, dict) else it for it in items]
    _ocr_mod().save_invoice(_clean_numbers(invoice), clean_items, issues)


# ---------------------------------------------------------- 场景匹配与推理

def seed_ontology() -> None:
    _ont_mod().seed_ontology()


def match_scenario(invoice: dict) -> dict:
    return _ocr_mod().match_scenario(invoice)


def _sequential_reasoning(ont, invoice_number: str, scenario_hint: str) -> dict:
    """LangGraph 不可用时，按原有 5 个节点顺序执行，结果与图执行等价。"""
    ont.init_db()
    ont.seed_ontology()
    data = ont.load_invoice(invoice_number)
    if not data:
        raise BadRequestError(f"发票不存在: {invoice_number}")
    state: dict[str, Any] = {
        "invoice_number": invoice_number,
        "invoice": data["invoice"], "items": data["items"],
        "scenario_hint": scenario_hint, "errors": [],
    }
    for node in (
        ont.node_scenario_match, ont.node_rule_select, ont.node_chain_assemble,
        ont.node_reasoning_execute, ont.node_conclusion_gen,
    ):
        state = node(state)
    ont.persist_reasoning(state)
    return state


def reason(invoice_number: str, scenario_hint: str = "SC001") -> dict:
    ont = _ont_mod()
    try:
        return ont.run_reasoning(invoice_number, scenario_hint=scenario_hint)
    except SystemExit as exc:
        raise BadRequestError(str(exc)) from exc
    except (ImportError, ModuleNotFoundError) as exc:
        if "langgraph" not in str(exc):
            raise
        log.warning("langgraph_missing_use_sequential_chain")
        return _sequential_reasoning(ont, invoice_number, scenario_hint)
    except Exception as exc:  # noqa: BLE001 - 统一转成领域错误，避免泄漏堆栈
        raise InternalError(f"本体推理执行失败: {type(exc).__name__}: {exc}") from exc


# ---------------------------------------------------------------- SAP 阶段

def load_payload(invoice_number: str) -> dict | None:
    payload, _ = _sap_mod().load_payload(invoice_number)
    return payload


def validate_payload(payload: dict) -> dict:
    return _sap_mod().run_shacl_validation(payload)


def preview_payload(payload: dict) -> dict:
    sp = _sap_mod()
    a = payload.get("company_a_posting") or {}
    b = payload.get("company_b_posting") or {}
    return {
        "invoice_ref": payload.get("invoice_ref"),
        "currency": payload.get("currency"),
        "posting_date": payload.get("posting_date"),
        "amounts": payload.get("amounts"),
        "company_a": {
            "company_code": a.get("company_code"), "document_type": a.get("document_type"),
            "line_count": len(a.get("line_items") or []),
            "debit": round(sum(float(x.get("debit") or 0) for x in a.get("line_items") or []), 2),
            "credit": round(sum(float(x.get("credit") or 0) for x in a.get("line_items") or []), 2),
        },
        "company_b": {
            "company_code": b.get("company_code"), "document_type": b.get("document_type"),
            "line_count": len(b.get("line_items") or []),
            "debit": round(sum(float(x.get("debit") or 0) for x in b.get("line_items") or []), 2),
            "credit": round(sum(float(x.get("credit") or 0) for x in b.get("line_items") or []), 2),
        },
        "sap_header": sp.build_sap_header(payload),
        "sap_items": sp.build_sap_items(payload),
    }


def post(payload: dict, mode: str | None = None) -> dict:
    """执行过账。mock=生成模拟凭证并完整落库；dry_run=仅预览；live=真实 SAP SOAP。"""
    # 未显式指定时以界面当前设置的运行时模式为准（不再只读 .env 的启动默认值）。
    mode = mode or runtime.get_sap_mode()
    sp = _sap_mod()
    if mode == "dry_run":
        return sp.post_to_sap(payload, dry_run=True, auto_approve=True)
    if mode == "live":
        missing = sap_live_missing()
        if missing:
            # 提前阻断：缺真实连接凭据时绝不调用 post_to_sap。
            # 否则它会把一次失败调用写成本地凭证/审计记录，也绝不静默降级成模拟过账。
            return {
                "success": False,
                "reason": ("live 模式缺少 SAP 连接配置：" + "、".join(missing)
                           + "。请在 .env 补齐这 4 项并重启后端；本次未生成凭证、未连接 SAP。"),
                "report": {}, "mock": False,
            }
        # zeep 是真实 SOAP 过账的延迟依赖，不同 Python 3.14.2 环境需分别安装。
        # 提前检查，避免 post_to_sap 捕获异常后仍写一条 POST_FAILED 假凭证。
        try:
            import zeep  # noqa: F401
        except ImportError as exc:
            # 把底层异常原样带出（例如 lxml ABI 不匹配的 cannot import name 'etree'），
            # 避免用户只看到「过账未成功」却不知道是哪个 Python 环境缺/坏了依赖。
            return {
                "success": False,
                "reason": (f"当前 Python 环境的 SOAP 依赖不可用（{exc}），无法调用真实 SAP。"
                           "请在启动后端所用的解释器执行 "
                           "python -m pip install \"zeep>=4.2\" \"lxml>=4.6\" "
                           "并重启后端；本次未生成凭证、未连接 SAP。"),
                "report": {}, "mock": False,
            }
        return sp.post_to_sap(payload, dry_run=False, auto_approve=True)

    report = sp.run_shacl_validation(payload)
    if not report.get("conforms"):
        sp.write_audit_conn("SHACL_BLOCKED", {
            "invoice_number": payload.get("invoice_ref"),
            "report": sp._public_report(report),
        })
        return {"success": False, "reason": "SHACL校验未通过", "report": report, "mock": True}

    header = sp.build_sap_header(payload)
    sap_items = sp.build_sap_items(payload)
    # 毫秒精度：连续逐张处理时多张发票可能在同一秒完成，避免共用同一个凭证号
    doc_no = "SIM" + datetime.now().strftime("%Y%m%d%H%M%S%f")[:-3]
    response = {
        "EBelnr": doc_no,
        "ECompany": header.get("Seller", ""),
        "EGjahr": str(datetime.now().year),
        "EMsg": "模拟过账成功（SAP_MODE=mock，未调用真实SAP）",
        "EStatus": "S",
        "IItem": [],
    }
    sp.save_posting_result(payload, header, sap_items, response, True)
    sp.write_audit_conn("SAP_POST_MOCK", {
        "invoice_number": payload.get("invoice_ref"),
        "doc_no": doc_no, "mode": "mock",
        "note": "本地模拟过账，未连接SAP",
    })
    return {
        "success": True, "mock": True, "response": response, "report": report,
        "summary": f"模拟双主体过账完成，凭证 {doc_no}（未调用真实SAP）",
    }


def cancel_posting(invoice_number: str) -> None:
    _sap_mod().write_audit_conn("POSTING_CANCELLED", {"invoice_number": invoice_number})


def public_report(report: dict) -> dict:
    """去掉 SHACL shape 中的大对象，只保留前端需要的结论。"""
    sp = _sap_mod()
    return sp._public_report(report)
