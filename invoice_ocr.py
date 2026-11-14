"""
第1个文件: OCR发票识别 + 人工确认 + SC001场景匹配 + 全流程编排
业务: 关联交易发票识别及SAP过账本体中心 - 发票识别层
链路: 发票文件 -> OCR服务(http://10.238.67.9:8014/ocr) -> 代码直接转化为JSON(不使用大模型)
      -> 数据质量校验 -> 人工确认(y确认/e修改/s跳过) -> SQLite(识别文本原样保存, 此阶段不引入本体)
      -> SC001场景匹配(此处才加载本体: 同一集团 + 增值税专用发票)
      -> 命中: ontology_reasoning.py推理 -> SHACL校验 -> sap_posting.py过账
      -> 未命中/校验失败: 全流程中断
"""
import argparse
import json
import mimetypes
import os
import re
import sqlite3
import sys
from datetime import datetime
from typing import Any, Optional

import requests
from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

OCR_URL = os.getenv("OCR_URL", "http://10.238.67.9:8014/ocr")
_db_path = os.getenv("DB_PATH", "interco_ontology.db")
DB_PATH = _db_path if os.path.isabs(_db_path) else os.path.join(BASE_DIR, _db_path)
SUPPORTED_EXTS = {".pdf", ".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif"}


def _ocr_use_env_proxy() -> bool:
    """内网 OCR 默认直连，避免 HTTP_PROXY 把请求转给本地代理导致 ProxyError(10054)。

    与 Web 后端 OCR_USE_ENV_PROXY 配置保持一致；仅当 OCR 服务确实需要代理时才设为 true。
    """
    return os.getenv("OCR_USE_ENV_PROXY", "false").strip().lower() in ("1", "true", "yes", "on")


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_code TEXT NOT NULL,
    company_name TEXT NOT NULL,
    group_id TEXT NOT NULL DEFAULT 'GRPMJ',
    company_type TEXT NOT NULL DEFAULT 'own',
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(company_code, company_name)
);
CREATE TABLE IF NOT EXISTS invoices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_number TEXT NOT NULL UNIQUE,
    invoice_type TEXT,
    issue_date TEXT,
    seller_name TEXT,
    buyer_name TEXT,
    seller_tax_id TEXT,
    buyer_tax_id TEXT,
    issuer_code TEXT,
    receiver_code TEXT,
    gross_amount REAL,
    tax_rate TEXT,
    net_amount REAL,
    tax_amount REAL,
    currency TEXT DEFAULT 'CNY',
    remark TEXT,
    invoice_issuer TEXT,
    source_file TEXT,
    quality_issues TEXT,
    status TEXT NOT NULL DEFAULT 'OCR_DONE',
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS invoice_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_number TEXT NOT NULL,
    item_no INTEGER NOT NULL,
    description TEXT,
    quantity REAL,
    unit TEXT,
    unit_price REAL,
    amount REAL,
    tax_rate TEXT,
    tax_amount REAL,
    account_code TEXT,
    cost_center_code TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(invoice_number, item_no)
);
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_code TEXT NOT NULL,
    account_name TEXT NOT NULL,
    account_type TEXT NOT NULL,
    company_code TEXT NOT NULL DEFAULT '*',
    UNIQUE(account_code, company_code)
);
CREATE TABLE IF NOT EXISTS cost_centers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cost_center_code TEXT NOT NULL UNIQUE,
    name TEXT,
    company_code TEXT
);
CREATE TABLE IF NOT EXISTS contracts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_no TEXT NOT NULL UNIQUE,
    party_a_code TEXT,
    party_b_code TEXT,
    effective_date TEXT,
    amount REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS interco_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    invoice_number TEXT,
    seller_code TEXT,
    buyer_code TEXT,
    event_type TEXT NOT NULL DEFAULT '关联交易开票',
    amount REAL,
    status TEXT NOT NULL DEFAULT 'CREATED',
    scenario_id TEXT,
    reasoning_payload TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS accounting_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_no TEXT NOT NULL,
    company_code TEXT NOT NULL,
    doc_type TEXT,
    posting_date TEXT,
    total_debit REAL,
    total_credit REAL,
    status TEXT NOT NULL DEFAULT 'POSTED',
    invoice_ref TEXT,
    derived_doc TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(doc_no, company_code, invoice_ref)
);
CREATE TABLE IF NOT EXISTS posting_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    posting_id TEXT NOT NULL UNIQUE,
    doc_ref TEXT,
    company_code TEXT,
    bapi_status TEXT,
    message TEXT,
    payload_json TEXT,
    timestamp TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS ontology_relationships (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_object TEXT NOT NULL,
    relation_name TEXT NOT NULL,
    to_object TEXT NOT NULL,
    cardinality TEXT,
    description TEXT,
    UNIQUE(from_object, relation_name, to_object)
);
CREATE TABLE IF NOT EXISTS ontology_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id TEXT NOT NULL UNIQUE,
    rule_name TEXT NOT NULL,
    swrl TEXT,
    inputs TEXT,
    outputs TEXT,
    preconditions TEXT
);
CREATE TABLE IF NOT EXISTS ontology_scenarios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario_id TEXT NOT NULL UNIQUE,
    scenario_name TEXT NOT NULL,
    description TEXT,
    conditions TEXT,
    rule_ids TEXT
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    actor TEXT NOT NULL DEFAULT 'AI_Agent',
    detail TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
"""


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    conn = get_conn()
    try:
        conn.executescript(SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()


def write_audit(conn: sqlite3.Connection, action: str, detail: Any, actor: str = "AI_Agent") -> None:
    conn.execute(
        "INSERT INTO audit_log(action, actor, detail) VALUES (?,?,?)",
        (action, actor, json.dumps(detail, ensure_ascii=False, default=str)),
    )


def _ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        return "__EOF__"


def get_mime_type(file_path: str) -> str:
    mime_type, _ = mimetypes.guess_type(file_path)
    return mime_type or "application/octet-stream"


def call_ocr_service(file_path: str) -> Optional[dict]:
    mime_type = get_mime_type(file_path)
    filename = os.path.basename(file_path)
    print(f"  调用OCR服务: {OCR_URL}")
    try:
        with requests.Session() as session:
            session.trust_env = _ocr_use_env_proxy()
            with open(file_path, "rb") as f:
                resp = session.post(
                    OCR_URL,
                    files={"file": (filename, f, mime_type)},
                    timeout=180,
                )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok", True):
            print(f"  OCR返回失败: {data}")
            return None
        print("  OCR识别完成")
        return data
    except requests.exceptions.RequestException as e:
        print(f"  OCR服务调用失败: {e}")
        return None


def call_ocr_with_pdf_fallback(file_path: str) -> Optional[dict]:
    result = call_ocr_service(file_path)
    if result is not None:
        return result
    if not file_path.lower().endswith(".pdf"):
        return None
    print("  PDF直传失败，转图片后重试...")
    try:
        import tempfile

        import pymupdf
    except ImportError:
        return None
    image_paths = []
    try:
        doc = pymupdf.open(file_path)
        for page in doc:
            pix = page.get_pixmap(dpi=150)
            fd, tmp = tempfile.mkstemp(suffix=".png")
            os.close(fd)
            pix.save(tmp)
            image_paths.append(tmp)
        doc.close()
        if not image_paths:
            return None
        return call_ocr_service(image_paths[0])
    finally:
        for p in image_paths:
            if os.path.exists(p):
                os.remove(p)


def _ocr_payload(ocr_result: dict) -> dict:
    if isinstance(ocr_result.get("merged"), dict):
        return ocr_result["merged"]
    pages = ocr_result.get("pages") or []
    if pages:
        return pages[0]
    return ocr_result


def _to_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(str(value).replace(",", "").replace("￥", "").strip())
    except (ValueError, TypeError):
        return default


def _norm_date(date_str: str) -> str:
    if not date_str:
        return ""
    s = str(date_str).strip().replace("年", "-").replace("月", "-").replace("日", "")
    s = re.sub(r"[^\d\-/.]", "", s)
    m = re.match(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$", s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.match(r"^(\d{4})(\d{2})(\d{2})$", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return str(date_str).strip()


def _norm_tax_rate(rate: Any) -> str:
    if rate is None:
        return ""
    s = str(rate).strip().replace("%", "").replace("％", "")
    try:
        v = float(s)
        if v <= 1 and v > 0:
            v *= 100
        return str(int(v)) if v == int(v) else str(v)
    except (ValueError, TypeError):
        return s


def _detect_invoice_type(data: dict) -> str:
    explicit = str(data.get("invoice_type") or data.get("inv_type") or "").strip()
    if explicit:
        if "专用" in explicit:
            return "增值税专用发票"
        if "普通" in explicit:
            return "增值税普通发票"
        return explicit
    text = " ".join(str(v) for v in data.values() if isinstance(v, str))
    if "普通发票" in text or "普票" in text:
        return "增值税普通发票"
    return "增值税专用发票"


def normalize_invoice(data: dict, raw_items: list) -> dict:
    rates = {_norm_tax_rate(it.get("tax_rate")) for it in raw_items if it.get("tax_rate")}
    rates.discard("")
    tax_rate = rates.pop() if len(rates) == 1 else (sorted(rates)[0] if rates else "")
    return {
        "invoice_type": _detect_invoice_type(data),
        "currency": str(data.get("currency") or "CNY"),
        "tax_rate": tax_rate,
        "remark": str(data.get("remark") or ""),
    }


def transform_invoice(ocr_result: dict, source_file: str = "") -> dict:
    data = _ocr_payload(ocr_result)
    raw_items = data.get("items") or []
    norm = normalize_invoice(data, raw_items)

    amount_sum = _to_float(data.get("amount_sum"))
    tax_sum = _to_float(data.get("tax_sum"))
    total_with_tax = _to_float(data.get("total_with_tax"), amount_sum + tax_sum)
    if abs(amount_sum + tax_sum - total_with_tax) > 0.01 and total_with_tax:
        amount_sum = round(total_with_tax - tax_sum, 2)

    items = []
    for idx, it in enumerate(raw_items, 1):
        items.append({
            "item_no": idx,
            "description": str(it.get("name") or ""),
            "quantity": _to_float(it.get("qty_num")) if it.get("qty_num") is not None else None,
            "unit": it.get("unit") or "",
            "unit_price": _to_float(it.get("price_num")) if it.get("price_num") is not None else None,
            "amount": _to_float(it.get("amount_num"), _to_float(it.get("amount_str"))),
            "tax_rate": _norm_tax_rate(it.get("tax_rate")) or norm["tax_rate"],
            "tax_amount": _to_float(it.get("tax_num"), _to_float(it.get("tax_str"))),
        })

    invoice = {
        "invoice_number": str(data.get("invoice_number") or "").strip(),
        "invoice_type": norm["invoice_type"],
        "issue_date": _norm_date(str(data.get("invoice_date") or "")),
        "seller_name": str(data.get("seller_name") or "").strip(),
        "buyer_name": str(data.get("buyer_name") or "").strip(),
        "seller_tax_id": str(data.get("seller_tax_id") or "").strip(),
        "buyer_tax_id": str(data.get("buyer_tax_id") or "").strip(),
        "issuer_code": "",
        "receiver_code": "",
        "gross_amount": round(total_with_tax, 2),
        "tax_rate": norm["tax_rate"],
        "net_amount": round(amount_sum, 2),
        "tax_amount": round(tax_sum, 2),
        "currency": norm["currency"],
        "remark": norm["remark"],
        "invoice_issuer": str(data.get("invoice_issuer") or "").strip(),
        "source_file": source_file,
        "status": "OCR_DONE",
    }
    return {"invoice": invoice, "items": items}


def check_data_quality(invoice: dict, items: list) -> list:
    issues = []

    def add(level, rule, detail):
        issues.append({"level": level, "rule": rule, "detail": detail})

    if not invoice.get("invoice_number"):
        add("阻断", "Invoice缺少invoice_number", "发票号码缺失, 无法唯一识别发票")
    if not invoice.get("tax_rate"):
        add("阻断", "Invoice缺少tax_rate", "无法推导税额, 阻断推理")
    if not invoice.get("seller_name") or not invoice.get("buyer_name"):
        add("阻断", "Invoice缺少销售方/购买方名称", "无法执行R001公司代码推理")
    if not invoice.get("currency"):
        add("警告", "Invoice缺少currency", "币种不完整, 影响金额换算")
    if not items:
        add("阻断", "Invoice缺少行项目", "无行项目无法映射科目")
    for it in items:
        if not it.get("amount"):
            add("阻断", "InvoiceItem缺少amount", f"行{it.get('item_no')}金额缺失或为0")
        if not it.get("description"):
            add("警告", "InvoiceItem缺少description", f"行{it.get('item_no')}描述缺失, 影响科目映射")
    return issues


def blocking_issues(issues: list) -> list:
    return [i for i in issues if i["level"] == "阻断"]


def save_invoice(invoice: dict, items: list, issues: list) -> None:
    conn = get_conn()
    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        status = "BLOCKED" if blocking_issues(issues) else "OCR_DONE"
        exists = conn.execute(
            "SELECT id FROM invoices WHERE invoice_number=?", (invoice["invoice_number"],)
        ).fetchone()
        fields = [
            "invoice_type", "issue_date", "seller_name", "buyer_name", "seller_tax_id",
            "buyer_tax_id", "issuer_code", "receiver_code", "gross_amount", "tax_rate",
            "net_amount", "tax_amount", "currency", "remark", "invoice_issuer",
            "source_file",
        ]
        issues_json = json.dumps(issues, ensure_ascii=False)
        if exists:
            sets = ", ".join(f"{f}=?" for f in fields)
            conn.execute(
                f"UPDATE invoices SET {sets}, status=?, quality_issues=?, updated_at=? "
                "WHERE invoice_number=?",
                [invoice.get(f) for f in fields] + [
                    status, issues_json, now, invoice["invoice_number"]
                ],
            )
        else:
            cols = ["invoice_number"] + fields + ["status", "quality_issues"]
            placeholders = ", ".join("?" for _ in cols)
            values = [invoice["invoice_number"]] + [invoice.get(f) for f in fields] + [
                status, issues_json
            ]
            conn.execute(
                f"INSERT INTO invoices({', '.join(cols)}) VALUES ({placeholders})", values
            )

        conn.execute("DELETE FROM invoice_items WHERE invoice_number=?", (invoice["invoice_number"],))
        for it in items:
            conn.execute(
                """INSERT INTO invoice_items
                   (invoice_number, item_no, description, quantity, unit, unit_price,
                    amount, tax_rate, tax_amount)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    invoice["invoice_number"], it["item_no"], it["description"],
                    it.get("quantity"), it.get("unit"), it.get("unit_price"),
                    it.get("amount"), it.get("tax_rate"), it.get("tax_amount"),
                ),
            )

        event_id = f"EVT-{invoice['invoice_number']}"
        has_event = conn.execute(
            "SELECT id FROM interco_events WHERE event_id=?", (event_id,)
        ).fetchone()
        if not has_event:
            conn.execute(
                """INSERT INTO interco_events
                   (event_id, invoice_number, event_type, amount, status)
                   VALUES (?,?,?,?,?)""",
                (event_id, invoice["invoice_number"], "关联交易开票",
                 invoice.get("gross_amount"), "CREATED" if status == "OCR_DONE" else "BLOCKED"),
            )
        else:
            conn.execute(
                "UPDATE interco_events SET amount=?, status=?, scenario_id=NULL, "
                "reasoning_payload=NULL, updated_at=? WHERE event_id=?",
                (invoice.get("gross_amount"),
                 "CREATED" if status == "OCR_DONE" else "BLOCKED", now, event_id),
            )

        write_audit(conn, "OCR_RECOGNIZED", {
            "invoice_number": invoice["invoice_number"],
            "source_file": invoice.get("source_file"),
            "items": len(items),
            "blocking": len(blocking_issues(issues)),
            "warnings": len(issues) - len(blocking_issues(issues)),
        })
        conn.commit()
    finally:
        conn.close()


def _display_result(result: dict, issues: list) -> None:
    invoice, items = result["invoice"], result["items"]
    print("\n  [识别结果]")
    print(f"    发票号码: {invoice['invoice_number']}")
    print(f"    开票日期: {invoice['issue_date']}  类型: {invoice['invoice_type']}")
    print(f"    开票方: {invoice['seller_name']}")
    print(f"    收票方: {invoice['buyer_name']}")
    print(f"    价税合计: {invoice['gross_amount']} {invoice['currency']} "
          f"(不含税 {invoice['net_amount']} + 税 {invoice['tax_amount']}, "
          f"税率 {invoice['tax_rate']}%)")
    print(f"    行项目 {len(items)} 条:")
    for it in items:
        print(f"      {it['item_no']}. {it['description']}  "
              f"金额 {it['amount']}  税 {it['tax_amount']}")
    if issues:
        print("    数据质量:")
        for i in issues:
            print(f"      [{i['level']}] {i['rule']}: {i['detail']}")
    else:
        print("    数据质量: 通过")


def _edit_invoice(result: dict) -> dict:
    edited = json.loads(json.dumps(result, ensure_ascii=False))
    inv = edited["invoice"]
    print("\n  [修改发票信息] (直接回车保留原值)")
    header_fields = [
        ("发票号码", "invoice_number", "str"),
        ("开票日期", "issue_date", "str"),
        ("发票类型", "invoice_type", "str"),
        ("开票方主体", "seller_name", "str"),
        ("收票方主体", "buyer_name", "str"),
        ("价税合计", "gross_amount", "float"),
        ("不含税金额", "net_amount", "float"),
        ("税额", "tax_amount", "float"),
        ("税率(%)", "tax_rate", "str"),
        ("币种", "currency", "str"),
        ("备注", "remark", "str"),
    ]
    for label, key, conv in header_fields:
        old = str(inv.get(key, ""))
        new = _ask(f"    {label} [{old}]: ")
        if new == "__EOF__" or not new:
            if new == "__EOF__":
                print("\n    输入结束, 停止修改")
                break
            continue
        try:
            inv[key] = float(new) if conv == "float" else new
        except ValueError:
            print(f"      输入无效, 保留原值: {old}")
    item_fields = [("名称", "description", "str"), ("金额", "amount", "float"),
                   ("税额", "tax_amount", "float"), ("税率", "tax_rate", "str")]
    for i, item in enumerate(edited["items"], 1):
        print(f"    --- 行项目 {i} ---")
        for label, key, conv in item_fields:
            old = str(item.get(key, ""))
            new = _ask(f"      {label} [{old}]: ")
            if new == "__EOF__":
                print("\n      输入结束, 停止修改")
                return edited
            if not new:
                continue
            try:
                item[key] = float(new) if conv == "float" else new
            except ValueError:
                print(f"        输入无效, 保留原值: {old}")
    return edited


def process_file(file_path: str) -> Optional[dict]:
    if not os.path.exists(file_path):
        print(f"  文件不存在: {file_path}")
        return None
    ocr_result = call_ocr_with_pdf_fallback(file_path)
    if ocr_result is None:
        return None
    result = transform_invoice(ocr_result, source_file=file_path)

    while True:
        issues = check_data_quality(result["invoice"], result["items"])
        _display_result(result, issues)
        raw = _ask("  确认识别结果? (y=确认保存 / e=修改 / s=跳过): ")
        if raw == "__EOF__":
            print("  输入结束, 默认跳过")
            return {"skipped": True}
        choice = raw.lower()
        if choice == "y":
            if not result["invoice"].get("invoice_number"):
                print("  发票号码为空, 无法保存, 请修改(e)或跳过(s)")
                continue
            save_invoice(result["invoice"], result["items"], issues)
            result["issues"] = issues
            print(f"  已写入SQLite: {DB_PATH}")
            return result
        if choice == "e":
            result = _edit_invoice(result)
            continue
        if choice == "s":
            print("  已跳过, 未入库")
            return {"skipped": True}
        print("  请输入 y / e / s")


def scan_folder(folder_path: str) -> list:
    files = []
    for root, _dirs, names in os.walk(folder_path):
        for name in sorted(names):
            if os.path.splitext(name)[1].lower() in SUPPORTED_EXTS:
                files.append(os.path.join(root, name))
    return files


def _company_group(company_code: str) -> Optional[str]:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT group_id FROM companies WHERE company_code=? LIMIT 1",
            (company_code,),
        ).fetchone()
        return row["group_id"] if row else None
    finally:
        conn.close()


def match_scenario(invoice: dict) -> dict:
    from ontology_reasoning import match_company

    seller = match_company(invoice.get("seller_name", ""))
    buyer = match_company(invoice.get("buyer_name", ""))
    invoice_type = str(invoice.get("invoice_type") or "").strip()

    seller_group = _company_group(seller["code"]) if seller["matched"] else None
    buyer_group = _company_group(buyer["code"]) if buyer["matched"] else None
    both_matched = bool(seller["matched"] and buyer["matched"])
    same_group = bool(both_matched and seller_group and seller_group == buyer_group)
    special_invoice = "专用" in invoice_type

    failures = []
    if not seller["matched"]:
        failures.append(f"开票方'{invoice.get('seller_name')}'未匹配本体公司")
    if not buyer["matched"]:
        failures.append(f"收票方'{invoice.get('buyer_name')}'未匹配本体公司")
    if both_matched and not same_group:
        failures.append(f"开票方与收票方不属于同一集团({seller_group} vs {buyer_group})")
    if not special_invoice:
        failures.append(f"发票类型'{invoice_type}'不是增值税专用发票")

    matched = same_group and special_invoice and both_matched
    if matched:
        reasoning = (f"SC001标准关联交易开票: 开票方{seller['name']}({seller['code']})与"
                     f"收票方{buyer['name']}({buyer['code']})同属集团{seller_group}, "
                     f"发票类型为{invoice_type}")
    else:
        reasoning = "SC001条件不满足: " + "；".join(failures)
    return {
        "matched": matched,
        "scenario_id": "SC001" if matched else None,
        "confidence": 1.0 if matched else 0.0,
        "reasoning": reasoning,
        "seller": seller,
        "buyer": buyer,
        "checks": {
            "both_companies_matched": both_matched,
            "same_group": same_group,
            "group_id": seller_group if same_group else None,
            "special_invoice": special_invoice,
            "invoice_type": invoice_type,
        },
        "failures": failures,
    }


def run_pipeline(file_path: str, ocr_only: bool = False,
                 no_sap: bool = False, dry_run: bool = False,
                 auto_approve: bool = False) -> str:
    print("\n" + "=" * 64)
    print(f"  阶段1 OCR识别 + 人工确认: {os.path.basename(file_path)}")
    print("=" * 64)
    result = process_file(file_path)
    if result is None:
        print("  [中断] OCR识别失败")
        return "failed"
    if result.get("skipped"):
        return "skipped"
    issues = result.get("issues") or []
    if blocking_issues(issues):
        print("  [中断] 数据质量存在阻断项, 不进入场景匹配")
        return "failed"
    invoice = result["invoice"]
    invoice_number = invoice["invoice_number"]

    if ocr_only:
        print("  --ocr-only: 仅完成识别落库, 结束")
        return "ok"

    print("\n" + "=" * 64)
    print("  阶段2 SC001场景匹配 (同一集团 + 增值税专用发票)")
    print("=" * 64)
    from ontology_reasoning import seed_ontology
    seed_ontology()
    scenario = match_scenario(invoice)
    print(f"  命中场景: {scenario['scenario_id'] or '无'}")
    checks = scenario["checks"]
    print(f"  条件1 开票方/收票方同属集团: {checks['same_group']}"
          + (f" ({checks['group_id']})" if checks["group_id"] else ""))
    print(f"  条件2 增值税专用发票: {checks['special_invoice']} ({checks['invoice_type']})")
    print(f"  结论: {scenario['reasoning']}")
    if not scenario["matched"]:
        print("  [中断] 场景匹配未通过, 不调用本体推理与SAP过账")
        return "failed"

    print("\n" + "=" * 64)
    print("  阶段3 本体推理 (ontology_reasoning.py)")
    print("=" * 64)
    from ontology_reasoning import run_reasoning
    final = run_reasoning(invoice_number, scenario_hint=scenario["scenario_id"])
    validation = final.get("validation") or {}
    if not validation.get("conforms"):
        print("  [中断] 推理结果未通过SHACL校验, 不调用SAP过账")
        return "failed"
    print("  SHACL校验: 通过")

    if no_sap:
        print("  --no-sap: 推理完成, 结束")
        return "ok"

    print("\n" + "=" * 64)
    print("  阶段4 SAP过账 (sap_posting.py)")
    print("=" * 64)
    from sap_posting import load_payload, post_to_sap
    payload, _found = load_payload(invoice_number)
    if not payload:
        print("  [中断] 未找到推理载荷")
        return "failed"
    if not payload.get("validation", {}).get("conforms"):
        print("  [中断] 载荷SHACL校验未通过")
        return "failed"
    post_result = post_to_sap(payload, dry_run=dry_run, auto_approve=auto_approve)
    if not post_result.get("success"):
        print("  [中断] SAP过账未成功")
        return "failed"
    print("  全流程完成")
    return "ok"


def main():
    parser = argparse.ArgumentParser(description="OCR识别 + 人工确认 + SC001场景匹配 + 推理过账流水线")
    parser.add_argument("path", nargs="?", help="发票文件或文件夹路径")
    parser.add_argument("--ocr-only", action="store_true", help="仅OCR识别并写入SQLite, 不进入场景匹配")
    parser.add_argument("--no-sap", action="store_true", help="场景匹配+推理+SHACL通过后不调用SAP")
    parser.add_argument("--dry-run", action="store_true", help="SAP阶段仅预览载荷, 不实际过账")
    parser.add_argument("--yes", "-y", action="store_true", help="跳过SAP过账审批确认")
    args = parser.parse_args()

    init_db()
    print("=" * 64)
    print("  关联交易发票识别及SAP过账本体中心 (全流程)")
    print(f"  OCR服务: {OCR_URL}")
    print(f"  数据库: {DB_PATH}")
    print("  流程: OCR -> 人工确认 -> SQLite -> SC001场景匹配 -> 本体推理 -> SHACL -> SAP过账")
    print("=" * 64)

    path = args.path
    while not path:
        path = _ask("请输入发票文件/文件夹路径 (exit退出): ")
        if path == "__EOF__":
            return
        if path.lower() in ("exit", "quit"):
            return
    if not os.path.exists(path):
        print(f"路径不存在: {path}")
        return

    files = scan_folder(path) if os.path.isdir(path) else [path]
    if not files:
        print("未找到支持的发票文件 (pdf/png/jpg/jpeg/bmp/tiff)")
        return

    counts = {"ok": 0, "skipped": 0, "failed": 0}
    for idx, fp in enumerate(files, 1):
        print(f"\n[{idx}/{len(files)}] {os.path.basename(fp)}")
        status = run_pipeline(
            fp,
            ocr_only=args.ocr_only,
            no_sap=args.no_sap,
            dry_run=args.dry_run,
            auto_approve=args.yes,
        )
        counts[status] += 1
    print(f"\n完成: 成功 {counts['ok']}, 跳过 {counts['skipped']}, "
          f"中断/失败 {counts['failed']}")
    if counts["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
