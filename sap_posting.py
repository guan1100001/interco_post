"""
第3个文件: 过账到SAP智能体
业务: 关联交易发票识别及SAP过账本体中心 - 执行验证层
链路: 读取本体推理JSON-LD载荷 -> SHACL校验(关联方/双主体/借贷平衡/税额)
      -> 审批(双主体同步过账需Approval) -> SAP ZCOA_003 ZfmAico002 BAPI过账
      -> 回写会计凭证/过账事件/审计日志到SQLite
"""
import argparse
import json
import os
import sys
from datetime import datetime
from typing import Any, Optional

from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from invoice_ocr import (
    DB_PATH,
    get_conn,
    init_db,
    write_audit,
)
from ontology_reasoning import (
    check_balance,
    check_tax_calculation,
    shacl_dual_posting,
    shacl_related_party,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

WSDL_URL = os.getenv("WSDL_URL_ZCOA003")
SERVICE_URL = os.getenv("SERVICE_URL_ZCOA003")
SAP_USERNAME = os.getenv("USERNAME_300")
SAP_PASSWORD = os.getenv("PASSWORD_300")


class SAPWebServiceClient:
    def __init__(self, wsdl: str, service_url: str, username: str, password: str):
        from requests import Session
        from requests.auth import HTTPBasicAuth
        try:
            from zeep import Client, Settings
            from zeep.transports import Transport
        except ImportError as exc:
            raise RuntimeError(
                f"当前 Python 环境的 SOAP 依赖不可用（{exc}），无法调用真实 SAP。"
                f"请在启动后端的解释器（{sys.executable}）执行 "
                "python -m pip install \"zeep>=4.2\" \"lxml>=4.6\" 并重启后端。"
            ) from exc

        session = Session()
        # SAP 为内网直连：默认绕过本机 HTTP_PROXY/HTTPS_PROXY（与 OCR 的处理一致），
        # 否则 WSDL 拉取与 SOAP 请求会被 127.0.0.1 之类本地代理拦截而失败。
        # 部署环境确实需要走代理时，显式设置 SAP_USE_ENV_PROXY=true。
        use_proxy = (os.getenv("SAP_USE_ENV_PROXY") or "").strip().lower() in ("1", "true", "yes", "on")
        session.trust_env = use_proxy
        session.auth = HTTPBasicAuth(username, password)
        transport = Transport(session=session, operation_timeout=180)
        self.client = Client(wsdl=wsdl, transport=transport,
                             settings=Settings(strict=False))
        if service_url:
            self.client.service._binding_options["address"] = service_url

    def call_zfm_aico_002(self, header: dict, items: list) -> dict:
        from zeep.exceptions import Fault, TransportError, ValidationError

        request: dict[str, Any] = {"IHeader": header}
        if items:
            request["IItem"] = {"item": items}
        try:
            response = self.client.service.ZfmAico002(**request)
        except (Fault, TransportError, ValidationError) as e:
            raise RuntimeError(f"SAP SOAP调用失败: {e}") from e

        response_items = []
        if getattr(response, "IItem", None) and getattr(response.IItem, "item", None):
            for item in response.IItem.item:
                response_items.append({
                    "Itemno": getattr(item, "Itemno", ""),
                    "Racct": getattr(item, "Racct", ""),
                    "Costcenter": getattr(item, "Costcenter", ""),
                })
        return {
            "EBelnr": getattr(response, "EBelnr", ""),
            "ECompany": getattr(response, "ECompany", ""),
            "EGjahr": getattr(response, "EGjahr", ""),
            "EMsg": getattr(response, "EMsg", ""),
            "EStatus": getattr(response, "EStatus", ""),
            "IItem": response_items,
        }


def _sap_env(name: str) -> str:
    """实时读取连接配置：优先进程环境变量，其次回读 .env 文件。

    模块级的 WSDL_URL 等在导入时就已固定；若服务启动时 .env 尚未配置，
    之后补齐配置且未重启进程时，旧常量会误判为「缺少配置」而不发起 SOAP。
    """
    value = (os.getenv(name) or "").strip()
    if value:
        return value
    try:
        from dotenv import dotenv_values
        return str(dotenv_values(os.path.join(BASE_DIR, ".env")).get(name) or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def get_sap_client() -> SAPWebServiceClient:
    keys = ("WSDL_URL_ZCOA003", "SERVICE_URL_ZCOA003", "USERNAME_300", "PASSWORD_300")
    values = {k: _sap_env(k) for k in keys}
    missing = [k for k in keys if not values[k]]
    if missing:
        raise RuntimeError(f".env缺少SAP配置: {', '.join(missing)}")
    return SAPWebServiceClient(values[keys[0]], values[keys[1]],
                               values[keys[2]], values[keys[3]])


def load_payload(invoice_number: Optional[str]) -> tuple[Optional[dict], Optional[str]]:
    conn = get_conn()
    try:
        if invoice_number:
            row = conn.execute(
                "SELECT invoice_number, reasoning_payload, status FROM interco_events "
                "WHERE invoice_number=?", (invoice_number,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT invoice_number, reasoning_payload, status FROM interco_events "
                "WHERE reasoning_payload IS NOT NULL "
                "ORDER BY updated_at DESC, id DESC LIMIT 1"
            ).fetchone()
        if not row or not row["reasoning_payload"]:
            return None, None
        if row["status"] != "REASONED":
            print(f"  发票 {row['invoice_number']} 状态为 {row['status']}, "
                  f"需要先完成本体推理(REASONED)")
        return json.loads(row["reasoning_payload"]), row["invoice_number"]
    finally:
        conn.close()


def latest_invoice_number() -> Optional[str]:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT invoice_number FROM interco_events WHERE reasoning_payload IS NOT NULL "
            "ORDER BY updated_at DESC, id DESC LIMIT 1"
        ).fetchone()
        return row["invoice_number"] if row else None
    finally:
        conn.close()


def run_shacl_validation(payload: dict) -> dict:
    invoice_ref = payload.get("invoice_ref")
    conn = get_conn()
    try:
        inv_row = conn.execute(
            "SELECT * FROM invoices WHERE invoice_number=?", (invoice_ref,)
        ).fetchone()
        item_rows = conn.execute(
            "SELECT * FROM invoice_items WHERE invoice_number=? ORDER BY item_no",
            (invoice_ref,),
        ).fetchall()
    finally:
        conn.close()
    invoice = dict(inv_row) if inv_row else {"invoice_number": invoice_ref}
    items = [dict(r) for r in item_rows]

    a = payload.get("company_a_posting") or {}
    b = payload.get("company_b_posting") or {}
    resolution_stub = {
        "seller": {"matched": bool(a.get("company_code")), "type": "own",
                   "code": a.get("company_code", "")},
        "buyer": {"matched": bool(b.get("company_code")), "type": "own",
                  "code": b.get("company_code", "")},
        "posting_decision": {"should_post": bool(
            a.get("company_code") and b.get("company_code"))},
        "items": [],
    }
    related = shacl_related_party(invoice, resolution_stub)
    dual = shacl_dual_posting(payload)
    balance_ok = check_balance(a) and check_balance(b)
    tax_ok = check_tax_calculation(invoice, items) if items else False
    related_check = bool(
        payload.get("validation", {}).get("related_party_check", related["conforms"])
        and related["conforms"]
    )
    report = {
        "balance_check": balance_ok,
        "related_party_check": related_check,
        "tax_calculation_check": tax_ok,
        "dual_posting_check": dual["conforms"],
        "shapes": {"RelatedPartyShape": related, "DualPostingShape": dual},
        "conforms": bool(balance_ok and tax_ok and dual["conforms"] and related_check),
    }
    return report


def build_sap_header(payload: dict) -> dict:
    a = payload["company_a_posting"]
    b = payload["company_b_posting"]
    amounts = payload.get("amounts") or {}
    gross = amounts.get("gross_amount")
    tax = amounts.get("tax_amount")
    if gross is None:
        gross = sum(float(l.get("debit") or 0) for l in a.get("line_items", []))
    if tax is None:
        tax = sum(float(l.get("credit") or 0) for l in a.get("line_items", [])
                  if str(l.get("account")) == "2221")
    return {
        "Seller": str(a.get("company_code", ""))[:4],
        "Buyer": str(b.get("company_code", ""))[:4],
        "Invoicedate": str(payload.get("posting_date", ""))[:10],
        "Invoicenumber": str(payload.get("invoice_ref", ""))[:25],
        "Waers": str(payload.get("currency", "CNY"))[:5],
        "Amountwtax": f"{float(gross):.2f}"[:15],
        "Amounttax": f"{float(tax):.2f}"[:15],
        "Remark": str(payload.get("audit_trail", {}).get("summary", ""))[:50] or "关联交易双主体过账",
    }


def build_sap_items(payload: dict) -> list:
    items = []
    for line in payload["company_b_posting"].get("line_items", []):
        if not line.get("cost_center"):
            continue
        items.append({
            "Itemno": str(len(items) + 1).zfill(3),
            "Amountwtax": f"{float(line.get('debit') or 0) + float(line.get('tax_amount') or 0):.2f}"[:13],
            "Amounttax": f"{float(line.get('tax_amount') or 0):.2f}"[:13],
            "Taxrate": str(line.get("tax_rate") or "").replace("%", "")[:5],
            "Itemname": str(line.get("description", ""))[:20],
            "Racct": str(line.get("account", ""))[:10],
            "Costcenter": str(line.get("cost_center", ""))[:10],
        })
    if not items:
        for idx, line in enumerate(payload["company_b_posting"].get("line_items", []), 1):
            if str(line.get("account")) in ("2222", "2999"):
                continue
            items.append({
                "Itemno": str(idx).zfill(3),
                "Amountwtax": f"{float(line.get('debit') or 0):.2f}"[:13],
                "Amounttax": f"{float(line.get('tax_amount') or 0):.2f}"[:13],
                "Taxrate": str(line.get("tax_rate") or "").replace("%", "")[:5],
                "Itemname": str(line.get("description", ""))[:20],
                "Racct": str(line.get("account", ""))[:10],
                "Costcenter": "",
            })
    return items


def _side_totals(side: dict) -> tuple[float, float]:
    debit = round(sum(float(l.get("debit") or 0) for l in side.get("line_items", [])), 2)
    credit = round(sum(float(l.get("credit") or 0) for l in side.get("line_items", [])), 2)
    return debit, credit


def _is_success(response: dict) -> bool:
    status = str(response.get("EStatus") or "").strip().upper()
    if status in ("E", "ERROR", "FAILED", "失败"):
        return False
    return bool(str(response.get("EBelnr") or "").strip())


def save_posting_result(payload: dict, header: dict, sap_items: list,
                        response: dict, success: bool) -> None:
    invoice_ref = payload.get("invoice_ref")
    a = payload["company_a_posting"]
    b = payload["company_b_posting"]
    a_debit, a_credit = _side_totals(a)
    b_debit, b_credit = _side_totals(b)
    doc_no = str(response.get("EBelnr") or "").strip() or "N/A"
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    status = "POSTED" if success else "POST_FAILED"
    conn = get_conn()
    try:
        conn.execute(
            "DELETE FROM accounting_documents WHERE invoice_ref=?", (invoice_ref,)
        )
        conn.execute(
            """INSERT INTO accounting_documents
               (doc_no, company_code, doc_type, posting_date, total_debit, total_credit,
                status, invoice_ref, derived_doc)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (doc_no, a.get("company_code"), a.get("document_type"),
             a.get("posting_date"), a_debit, a_credit, status, invoice_ref, ""),
        )
        conn.execute(
            """INSERT INTO accounting_documents
               (doc_no, company_code, doc_type, posting_date, total_debit, total_credit,
                status, invoice_ref, derived_doc)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (doc_no, b.get("company_code"), b.get("document_type"),
             b.get("posting_date"), b_debit, b_credit, status, invoice_ref,
             f"{doc_no}@{a.get('company_code')}"),
        )

        posting_id = f"POST-{invoice_ref}-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
        conn.execute(
            """INSERT INTO posting_events
               (posting_id, doc_ref, company_code, bapi_status, message, payload_json)
               VALUES (?,?,?,?,?,?)""",
            (posting_id, doc_no, f"{a.get('company_code')}/{b.get('company_code')}",
             str(response.get("EStatus") or ""), str(response.get("EMsg") or ""),
             json.dumps({"header": header, "items": sap_items,
                         "response": response}, ensure_ascii=False, default=str)),
        )
        conn.execute(
            "UPDATE interco_events SET status=?, updated_at=? WHERE invoice_number=?",
            (status, now, invoice_ref),
        )
        conn.execute(
            "UPDATE invoices SET status=?, updated_at=? WHERE invoice_number=?",
            (status, now, invoice_ref),
        )
        write_audit(conn, "SAP_POST_SUCCESS" if success else "SAP_POST_FAILED", {
            "invoice_number": invoice_ref,
            "doc_no": doc_no,
            "company_a": a.get("company_code"),
            "company_b": b.get("company_code"),
            "e_status": response.get("EStatus"),
            "e_msg": response.get("EMsg"),
            "reasoning_chain": payload.get("audit_trail", {}).get("reasoning_chain"),
            "engine": "rule_code",
            "approval": "APPROVED_BY_OPERATOR",
        })
        conn.commit()
    finally:
        conn.close()


def audit_summary(payload: dict, response: dict, success: bool) -> str:
    return (
        f"双主体过账{'成功' if success else '失败'}: {payload.get('invoice_ref')} "
        f"{payload['company_a_posting'].get('company_code')}+"
        f"{payload['company_b_posting'].get('company_code')} "
        f"凭证号{response.get('EBelnr')} 状态{response.get('EStatus')}"
    )


def post_to_sap(payload: dict, dry_run: bool = False, auto_approve: bool = False) -> dict:
    invoice_ref = payload.get("invoice_ref")
    print("=" * 64)
    print(f"  SAP双主体过账: {invoice_ref}")
    print("=" * 64)

    report = run_shacl_validation(payload)
    print("  SHACL校验报告:")
    print(f"    RelatedPartyShape(关联方): {report['shapes']['RelatedPartyShape']['conforms']}")
    print(f"    DualPostingShape(双主体>=2凭证): {report['shapes']['DualPostingShape']['conforms']}")
    print(f"    借贷平衡: {report['balance_check']}")
    print(f"    税额勾稽: {report['tax_calculation_check']}")
    print(f"    结论: {'conforms=True' if report['conforms'] else 'conforms=False'}")

    if not report["conforms"]:
        print("  校验未通过, 阻断过账")
        write_audit_conn("SHACL_BLOCKED", {"invoice_number": invoice_ref,
                                           "report": _public_report(report)})
        return {"success": False, "reason": "SHACL校验未通过", "report": report}

    a, b = payload["company_a_posting"], payload["company_b_posting"]
    header = build_sap_header(payload)
    sap_items = build_sap_items(payload)
    print("  过账载荷(开票方/收票方):")
    print(f"    A {a.get('company_code')} {a.get('document_type')} "
          f"借{sum(float(l.get('debit') or 0) for l in a['line_items']):.2f} / "
          f"贷{sum(float(l.get('credit') or 0) for l in a['line_items']):.2f} "
          f"({len(a['line_items'])}行)")
    print(f"    B {b.get('company_code')} {b.get('document_type')} "
          f"借{sum(float(l.get('debit') or 0) for l in b['line_items']):.2f} / "
          f"贷{sum(float(l.get('credit') or 0) for l in b['line_items']):.2f} "
          f"({len(b['line_items'])}行)")
    print(f"  BAPI ZfmAico002 Header: {json.dumps(header, ensure_ascii=False)}")
    print(f"  行项目 {len(sap_items)} 条:")
    for it in sap_items:
        print(f"    {it['Itemno']} {it['Itemname']} 科目{it['Racct']} "
              f"成本中心{it['Costcenter']} 含税{it['Amountwtax']} 税{it['Amounttax']}")

    if dry_run:
        print("  --dry-run 模式, 不调用SAP")
        return {"success": True, "dry_run": True, "header": header,
                "items": sap_items, "report": report}

    if not auto_approve:
        try:
            answer = input("  双主体同步过账需审批, 确认过账? (y/n): ").strip().lower()
        except EOFError:
            answer = ""
        if answer != "y":
            print("  已取消, 未过账")
            write_audit_conn("POSTING_CANCELLED", {"invoice_number": invoice_ref})
            return {"success": False, "reason": "操作员取消", "report": report}

    write_audit_conn("POSTING_APPROVED", {"invoice_number": invoice_ref,
                                          "mode": "双主体同步过账"})
    print("  正在调用SAP BAPI...")
    try:
        client = get_sap_client()
        response = client.call_zfm_aico_002(header, sap_items)
    except Exception as e:
        response = {"EBelnr": "", "ECompany": "", "EGjahr": "",
                    "EMsg": str(e), "EStatus": "E", "IItem": []}
        success = False
        print(f"  SAP调用异常: {e}")
    else:
        success = _is_success(response)
        print(f"  SAP返回: 凭证号={response.get('EBelnr')} 公司={response.get('ECompany')} "
              f"年度={response.get('EGjahr')} 状态={response.get('EStatus')} "
              f"消息={response.get('EMsg')}")

    save_posting_result(payload, header, sap_items, response, success)
    summary = audit_summary(payload, response, success)
    print(f"  审计摘要: {summary}")
    print(f"  凭证/过账事件/审计日志已写入: {DB_PATH}")
    return {"success": success, "response": response, "report": report,
            "summary": summary}


def write_audit_conn(action: str, detail: Any) -> None:
    conn = get_conn()
    try:
        write_audit(conn, action, detail)
        conn.commit()
    finally:
        conn.close()


def _public_report(report: dict) -> dict:
    return {k: v for k, v in report.items() if k != "shapes"} | {
        "shapes": {k: v["conforms"] for k, v in report.get("shapes", {}).items()}
    }


def list_events() -> None:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT event_id, invoice_number, seller_code, buyer_code, amount, status, "
            "scenario_id, updated_at FROM interco_events ORDER BY updated_at DESC LIMIT 20"
        ).fetchall()
        docs = conn.execute(
            "SELECT doc_no, company_code, doc_type, status, invoice_ref, created_at "
            "FROM accounting_documents ORDER BY id DESC LIMIT 10"
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        print("暂无交易事件, 请先运行 invoice_ocr.py 与 ontology_reasoning.py")
        return
    print(f"{'事件':<28} {'状态':<12} {'金额':>12}  方向")
    for r in rows:
        print(f"{r['event_id']:<28} {r['status']:<12} {r['amount'] or 0:>12}  "
              f"{r['seller_code'] or '?'} -> {r['buyer_code'] or '?'}")
    if docs:
        print("\n会计凭证:")
        for d in docs:
            print(f"  {d['doc_no']} {d['company_code']} {d['doc_type']} "
                  f"{d['status']} 发票{d['invoice_ref']}")


def main():
    parser = argparse.ArgumentParser(description="SAP双主体过账智能体")
    parser.add_argument("--invoice", help="指定发票号码")
    parser.add_argument("--latest", action="store_true", help="过账最近一张已推理发票")
    parser.add_argument("--dry-run", action="store_true", help="仅校验与载荷预览, 不调用SAP")
    parser.add_argument("--yes", "-y", action="store_true", help="跳过审批确认")
    parser.add_argument("--list", action="store_true", help="列出交易事件与凭证")
    args = parser.parse_args()

    init_db()
    if args.list:
        list_events()
        return

    invoice_number = args.invoice or (latest_invoice_number() if args.latest else None)
    while not invoice_number:
        try:
            invoice_number = input("请输入发票号码 (--list可查看): ").strip()
        except EOFError:
            return
        if invoice_number.lower() in ("exit", "quit"):
            return

    payload, _ = load_payload(invoice_number)
    if not payload:
        print(f"未找到已推理载荷: {invoice_number}")
        print(f"请先运行: python ontology_reasoning.py --invoice {invoice_number}")
        raise SystemExit(1)
    if not payload.get("validation", {}).get("conforms") and not args.dry_run:
        print("推理阶段校验未通过, 阻断过账:")
        print(json.dumps(payload.get("errors") or payload.get("validation"),
                         ensure_ascii=False, indent=2))
        raise SystemExit(1)

    result = post_to_sap(payload, dry_run=args.dry_run, auto_approve=args.yes)
    if not result.get("success"):
        raise SystemExit(2)
    print("\n完成")


if __name__ == "__main__":
    main()
