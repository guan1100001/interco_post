"""
第2个文件: 本体推理
业务: 关联交易发票识别及SAP过账本体中心 - 本体定义层 + 推理层(纯规则代码, 不使用大模型)
链路: SQLite本体(公司/科目/成本中心/规则/场景) -> LangGraph五步推理
      (场景匹配->规则挑选->链路组装->逻辑推演->结论生成) -> SHACL校验
      -> JSON-LD双主体过账载荷 -> 回写本体实例
"""
import argparse
import json
import sys
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any, Optional, TypedDict

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from invoice_ocr import (
    DB_PATH,
    blocking_issues,
    get_conn,
    init_db,
    match_scenario,
    write_audit,
)

ONTOLOGY_CONTEXT = "http://www.example.com/ontology/group-interco#"

OWN_COMPANIES = {
    "北京智米科技有限公司": "7110",
    "北京智米电子科技有限公司": "7120",
    "北京门捷检测技术有限公司": "7121",
}

PARTNER_COMPANIES = {
    "常州新祺晟高分子科技有限公司": "101518",
    "贝能电子(福建)有限公司": "101558",
    "深圳兴奇宏科技有限公司": "101578",
}

ITEM_ACCOUNT_MAP = {
    "检测费": "6601070100",
    "检测费（": "6601070100",
    "*生产生活服务*检测费*": "6601070100",
    "检测费用": "6601070100",
    "技术服务费": "6601070200",
    "技术开发费": "6601070300",
    "咨询费": "6601080100",
    "咨询服务费": "6601080100",
    "办公用品": "6601010100",
    "办公耗材": "6601010100",
    "电子设备": "6601010200",
    "电脑配件": "6601010200",
    "维修费": "6601090100",
    "维修服务费": "6601090100",
    "运输费": "6601030100",
    "物流费": "6601030100",
    "房租": "6601020100",
    "租赁费": "6601020100",
    "水电费": "6601020200",
    "物业管理费": "6601020300",
    "差旅费": "6601040100",
    "住宿费": "6601040100",
    "餐饮费": "6601040200",
    "通信费": "6601050100",
    "广告费": "6601060100",
    "推广费": "6601060100",
    "培训费": "6601100100",
    "会议费": "6601110100",
    "保险费": "6601120100",
    "审计费": "6601130100",
    "法律服务费": "6601130200",
    "软件费": "6601140100",
    "系统服务费": "6601140200",
}

COMPANY_COSTCENTER_MAP = {
    "7110": "7110106010",
    "7120": "7120106010",
    "7121": "7121106010",
    "7130": "7130106010",
    "7140": "7140106010",
}

STANDARD_ACCOUNTS = [
    ("1122", "应收账款", "AR"),
    ("6001", "主营业务收入", "REV"),
    ("2221", "销项税额", "TAX"),
    ("2222", "进项税额", "TAX"),
    ("2999", "应付账款", "AP"),
    ("6601", "销售费用", "EXP"),
]

RELATIONSHIPS = [
    ("Invoice", "hasSeller", "Company", "N:1", "发票的销售方"),
    ("Invoice", "hasBuyer", "Company", "N:1", "发票的购买方"),
    ("Invoice", "issuer", "Company", "N:1", "开票方（出票主体）"),
    ("Invoice", "receiver", "Company", "N:1", "收票方（受票主体）"),
    ("Company", "affiliatedWith", "Company", "N:N", "关联方关系（对称）"),
    ("Invoice", "hasLineItem", "InvoiceItem", "1:N", "发票包含行项目"),
    ("AccountingDocument", "basedOn", "Invoice", "N:1", "凭证基于发票"),
    ("AccountingDocument", "derivedDoc", "AccountingDocument", "N:1", "凭证派生关系（镜像凭证）"),
    ("InvoiceItem", "mapsToAccount", "Account", "N:1", "行项目映射科目"),
    ("InvoiceItem", "assignedCostCenter", "CostCenter", "N:1", "归属成本中心"),
    ("AccountingDocument", "postsCompanyCode", "Company", "N:1", "过账公司代码"),
    ("Contract", "partyA", "Company", "N:1", "合同甲方"),
    ("Contract", "partyB", "Company", "N:1", "合同乙方"),
]

RULES = [
    (
        "R001", "公司代码推理规则",
        "grp:发票(?i) -> grp:销售方名称(?i,?s) ^ grp:购买方名称(?i,?b) -> "
        "grp:销售方公司代码(?i, matchCompany(?s)) ^ grp:购买方公司代码(?i, matchCompany(?b)) ^ "
        "grp:公司类型(?s/?b, own|partner)",
        "销售方名称、购买方名称", "销售方公司代码、购买方公司代码、公司类型", "",
    ),
    (
        "R002", "科目映射规则",
        "grp:发票行项目(?it) ^ grp:行描述(?it,?d) -> grp:科目代码(?it, matchItemAccount(?d))",
        "行描述", "科目代码", "R001",
    ),
    (
        "R003", "成本中心推理规则",
        "grp:公司代码(?c) -> grp:成本中心(?c, COMPANY_COSTCENTER_MAP(?c))",
        "公司代码", "成本中心", "R001,R002",
    ),
    (
        "R004", "关联交易判断规则",
        "grp:销售方类型(?s,own) ^ grp:购买方类型(?b,own) -> grp:should_post(?i,true) ^ "
        "grp:reason(?i,\"双方均为内部公司（内部交易），允许过账\"); 否则 should_post=false",
        "销售方类型、购买方类型", "过账判断、原因", "R001",
    ),
]

SCENARIOS = [
    (
        "SC001", "标准关联交易开票",
        "集团内公司A向公司B开具服务费发票",
        "发票存在开票方和收票方；开票方和收票方属于同一集团；发票类型为增值税专用发票；"
        "不存在折扣/退款等特殊条款",
        "R001,R002,R003,R004",
    ),
]


def seed_ontology() -> None:
    """参考数据以本文件常量为准, 每次运行全量重同步(修复常量改后DB不一致)"""
    conn = get_conn()
    try:
        for table in ("companies", "accounts", "cost_centers",
                      "ontology_relationships", "ontology_rules", "ontology_scenarios"):
            conn.execute(f"DELETE FROM {table}")
        for name, code in OWN_COMPANIES.items():
            conn.execute(
                "INSERT INTO companies(company_code, company_name, company_type, status) "
                "VALUES (?,?,?,?)", (code, name, "own", "ACTIVE"),
            )
        for name, code in PARTNER_COMPANIES.items():
            conn.execute(
                "INSERT INTO companies(company_code, company_name, company_type, status) "
                "VALUES (?,?,?,?)", (code, name, "partner", "ACTIVE"),
            )
        for code, name, atype in STANDARD_ACCOUNTS:
            conn.execute(
                "INSERT OR IGNORE INTO accounts(account_code, account_name, account_type) "
                "VALUES (?,?,?)", (code, name, atype),
            )
        for desc, code in ITEM_ACCOUNT_MAP.items():
            conn.execute(
                "INSERT OR IGNORE INTO accounts(account_code, account_name, account_type) "
                "VALUES (?,?,?)", (code, desc, "EXP"),
            )
        for company, cc in COMPANY_COSTCENTER_MAP.items():
            conn.execute(
                "INSERT INTO cost_centers(cost_center_code, name, company_code) "
                "VALUES (?,?,?)", (cc, f"成本中心-{cc}", company),
            )
        for rel in RELATIONSHIPS:
            conn.execute(
                "INSERT INTO ontology_relationships"
                "(from_object, relation_name, to_object, cardinality, description) "
                "VALUES (?,?,?,?,?)", rel,
            )
        for rule in RULES:
            conn.execute(
                "INSERT INTO ontology_rules"
                "(rule_id, rule_name, swrl, inputs, outputs, preconditions) "
                "VALUES (?,?,?,?,?,?)", rule,
            )
        for sc in SCENARIOS:
            conn.execute(
                "INSERT INTO ontology_scenarios"
                "(scenario_id, scenario_name, description, conditions, rule_ids) "
                "VALUES (?,?,?,?,?)", sc,
            )
        if conn.execute("SELECT COUNT(*) c FROM audit_log").fetchone()["c"] == 0:
            write_audit(conn, "ONTOLOGY_SEEDED", {
                "objects": 9, "relationships": len(RELATIONSHIPS),
                "rules": len(RULES), "scenarios": len(SCENARIOS),
            })
        conn.commit()
    finally:
        conn.close()


def _fuzzy(query: str, target: str) -> float:
    if not query or not target:
        return 0.0
    q, t = query.lower().strip(), target.lower().strip()
    if q == t:
        return 1.0
    if q in t or t in q:
        return 0.9
    return SequenceMatcher(None, q, t).ratio()


def match_company(company_name: str) -> dict:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT company_name, company_code, company_type FROM companies"
        ).fetchall()
    finally:
        conn.close()
    candidates = {r["company_name"]: {"code": r["company_code"], "type": r["company_type"]}
                  for r in rows}
    for name, code in OWN_COMPANIES.items():
        candidates.setdefault(name, {"code": code, "type": "own"})
    for name, code in PARTNER_COMPANIES.items():
        candidates.setdefault(name, {"code": code, "type": "partner"})

    if not company_name:
        return {"matched": False, "name": company_name, "code": "", "type": "unknown",
                "score": 0.0, "input": company_name}
    q = company_name.strip()
    best, best_score = None, 0.0
    for key in candidates:
        k = key.strip()
        if q == k:
            best, best_score = key, 1.0
            break
        if q in k or k in q:
            if 0.9 > best_score:
                best, best_score = key, 0.9
        else:
            score = _fuzzy(q, k)
            if score >= 0.85 and score > best_score:
                best, best_score = key, round(score, 3)
    if best and best_score >= 0.85:
        return {
            "matched": True, "name": best, "code": candidates[best]["code"],
            "type": candidates[best]["type"], "score": best_score,
            "input": company_name,
        }
    return {
        "matched": False, "name": company_name, "code": "", "type": "unknown",
        "score": 0.0, "input": company_name,
    }


def match_item_account(item_name: str) -> dict:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT account_code, account_name FROM accounts WHERE account_type='EXP'"
        ).fetchall()
    finally:
        conn.close()
    candidates = {r["account_name"]: r["account_code"] for r in rows}
    for name, code in ITEM_ACCOUNT_MAP.items():
        candidates.setdefault(name, code)

    if not item_name:
        return {"matched": False, "account_code": "", "matched_name": "", "score": 0,
                "input": item_name}
    for keyword, code in candidates.items():
        if keyword in item_name or item_name in keyword:
            return {"matched": True, "account_code": code, "matched_name": keyword,
                    "score": 1.0, "input": item_name}
    best, best_score = None, 0.0
    for key in candidates:
        score = _fuzzy(item_name, key)
        if score > best_score:
            best, best_score = key, score
    if best and best_score >= 0.5:
        return {"matched": True, "account_code": candidates[best], "matched_name": best,
                "score": round(best_score, 3), "input": item_name}
    return {"matched": False, "account_code": "", "matched_name": "", "score": 0,
            "input": item_name}


def match_cost_center(company_code: str) -> dict:
    if not company_code:
        return {"cost_center": "", "input_company": company_code}
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT cost_center_code FROM cost_centers WHERE company_code=? OR cost_center_code=?",
            (company_code, company_code),
        ).fetchone()
    finally:
        conn.close()
    cc = row["cost_center_code"] if row else COMPANY_COSTCENTER_MAP.get(
        company_code, company_code + "000000")
    return {"cost_center": cc, "input_company": company_code}


def should_post_invoice(seller_name: str, buyer_name: str) -> dict:
    seller = match_company(seller_name)
    buyer = match_company(buyer_name)
    seller_own = seller["matched"] and seller["type"] == "own"
    buyer_own = buyer["matched"] and buyer["type"] == "own"
    if seller_own and buyer_own:
        should, reason = True, "双方均为内部公司（内部交易），允许过账"
    else:
        should = False
        parts = []
        if not seller_own:
            parts.append(f"销售方'{seller_name}'非内部公司")
        if not buyer_own:
            parts.append(f"购买方'{buyer_name}'非内部公司")
        reason = "；".join(parts) + "，R004判定不允许过账"
    return {"should_post": should, "reason": reason, "seller": seller, "buyer": buyer}


def resolve_invoice_ontology(invoice: dict, items: list) -> dict:
    decision = should_post_invoice(invoice.get("seller_name", ""), invoice.get("buyer_name", ""))
    seller, buyer = decision["seller"], decision["buyer"]
    seller_cc = match_cost_center(seller["code"]) if seller["matched"] else {"cost_center": ""}
    buyer_cc = match_cost_center(buyer["code"]) if buyer["matched"] else {"cost_center": ""}
    base_cc_code = buyer["code"] if buyer["matched"] else (seller["code"] if seller["matched"] else "")

    item_results = []
    for it in items:
        account = match_item_account(it.get("description", ""))
        item_cc = match_cost_center(base_cc_code) if base_cc_code else {"cost_center": ""}
        item_results.append({
            "item_no": it.get("item_no"),
            "item_name": it.get("description", ""),
            "amount": it.get("amount"),
            "tax_rate": it.get("tax_rate"),
            "tax_amount": it.get("tax_amount"),
            "account": account,
            "cost_center": item_cc,
        })
    return {
        "posting_decision": decision,
        "seller": seller, "buyer": buyer,
        "seller_cost_center": seller_cc, "buyer_cost_center": buyer_cc,
        "items": item_results,
    }


def load_invoice(invoice_number: str) -> Optional[dict]:
    conn = get_conn()
    try:
        inv = conn.execute(
            "SELECT * FROM invoices WHERE invoice_number=?", (invoice_number,)
        ).fetchone()
        if not inv:
            return None
        items = [dict(r) for r in conn.execute(
            "SELECT * FROM invoice_items WHERE invoice_number=? ORDER BY item_no",
            (invoice_number,),
        ).fetchall()]
        return {"invoice": dict(inv), "items": items}
    finally:
        conn.close()


def latest_invoice_number() -> Optional[str]:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT invoice_number FROM invoices ORDER BY updated_at DESC, id DESC LIMIT 1"
        ).fetchone()
        return row["invoice_number"] if row else None
    finally:
        conn.close()


def _round2(v: Any) -> float:
    try:
        return round(float(v or 0), 2)
    except (ValueError, TypeError):
        return 0.0


def shacl_related_party(invoice: dict, resolution: dict) -> dict:
    seller_ok = resolution["seller"]["matched"] and resolution["seller"]["type"] == "own"
    buyer_ok = resolution["buyer"]["matched"] and resolution["buyer"]["type"] == "own"
    conforms = bool(seller_ok and buyer_ok)
    results = [{
        "shape": "RelatedPartyShape", "target": invoice.get("invoice_number"),
        "conforms": conforms,
        "message": "销售方与购买方均为内部公司" if conforms else
                   f"关联方检查未通过: seller={resolution['seller']}, buyer={resolution['buyer']}",
        "severity": "Violation" if not conforms else "OK",
    }]
    return {"shape": "RelatedPartyShape", "conforms": conforms, "results": results}


def shacl_dual_posting(payload: dict) -> dict:
    a = payload.get("company_a_posting") or {}
    b = payload.get("company_b_posting") or {}
    a_lines = a.get("line_items") or []
    b_lines = b.get("line_items") or []
    doc_count = (1 if a.get("company_code") and a_lines else 0) + \
                (1 if b.get("company_code") and b_lines else 0)
    conforms = doc_count >= 2
    results = [{
        "shape": "DualPostingShape", "target": payload.get("invoice_ref"),
        "conforms": conforms,
        "message": f"双主体凭证数={doc_count} (要求>=2)" if not conforms else
                   f"双主体凭证齐全: {a.get('company_code')} + {b.get('company_code')}",
        "severity": "Violation" if not conforms else "OK",
    }]
    return {"shape": "DualPostingShape", "conforms": conforms, "results": results}


def check_balance(side: dict) -> bool:
    debit = sum(_round2(l.get("debit")) for l in side.get("line_items", []))
    credit = sum(_round2(l.get("credit")) for l in side.get("line_items", []))
    return abs(debit - credit) <= 0.01


def check_tax_calculation(invoice: dict, items: list) -> bool:
    net, tax, gross = _round2(invoice.get("net_amount")), _round2(invoice.get("tax_amount")), \
        _round2(invoice.get("gross_amount"))
    item_sum = round(sum(_round2(i.get("amount")) for i in items), 2)
    return abs(net + tax - gross) <= 0.01 and abs(item_sum - net) <= 0.01


def build_posting_payload(invoice: dict, resolution: dict) -> dict:
    seller, buyer = resolution["seller"], resolution["buyer"]
    net = _round2(invoice.get("net_amount"))
    tax = _round2(invoice.get("tax_amount"))
    gross = _round2(invoice.get("gross_amount"))

    a_lines = [
        {"account": "1122", "description": "应收账款", "debit": gross, "credit": 0.0},
        {"account": "6001", "description": "主营业务收入", "debit": 0.0, "credit": net},
        {"account": "2221", "description": "销项税额", "debit": 0.0, "credit": tax},
    ]
    b_lines = []
    for it in resolution["items"]:
        b_lines.append({
            "account": it["account"]["account_code"],
            "description": it["item_name"],
            "debit": _round2(it.get("amount")),
            "credit": 0.0,
            "tax_rate": str(it.get("tax_rate") or "").replace("%", ""),
            "tax_amount": _round2(it.get("tax_amount")),
            "cost_center": it["cost_center"]["cost_center"],
        })
    b_lines.append({"account": "2222", "description": "进项税额", "debit": tax, "credit": 0.0})
    b_lines.append({"account": "2999", "description": "应付账款", "debit": 0.0, "credit": gross})

    balance_ok = check_balance({"line_items": a_lines}) and check_balance({"line_items": b_lines})
    related_ok = bool(
        resolution["posting_decision"]["should_post"]
        and seller["type"] == "own" and buyer["type"] == "own"
    )
    tax_ok = check_tax_calculation(invoice, resolution["items"])

    return {
        "@context": ONTOLOGY_CONTEXT,
        "@type": "PostingPayload",
        "invoice_ref": invoice.get("invoice_number"),
        "currency": invoice.get("currency") or "CNY",
        "posting_date": invoice.get("issue_date") or datetime.now().strftime("%Y-%m-%d"),
        "amounts": {"gross_amount": gross, "net_amount": net, "tax_amount": tax},
        "company_a_posting": {
            "company_code": seller.get("code", ""),
            "company_role": "开票方/销售方",
            "document_type": "销售",
            "posting_date": invoice.get("issue_date") or datetime.now().strftime("%Y-%m-%d"),
            "line_items": a_lines,
        },
        "company_b_posting": {
            "company_code": buyer.get("code", ""),
            "company_role": "收票方/购买方",
            "document_type": "采购",
            "posting_date": invoice.get("issue_date") or datetime.now().strftime("%Y-%m-%d"),
            "line_items": b_lines,
        },
        "validation": {
            "balance_check": balance_ok,
            "related_party_check": related_ok,
            "tax_calculation_check": tax_ok,
        },
        "audit_trail": {
            "reasoning_chain": ["R001", "R002", "R003", "R004"],
            "execution_timestamp": datetime.now().isoformat(timespec="seconds"),
            "engine": "rule_code",
            "confidence_score": 0.0,
        },
    }


class ReasoningState(TypedDict, total=False):
    invoice_number: str
    invoice: dict
    items: list
    scenario_hint: str
    scenario: dict
    rules: list
    chain: list
    execution: dict
    resolution: dict
    payload: dict
    validation: dict
    summary: str
    confidence: float
    errors: list


def node_scenario_match(state: ReasoningState) -> ReasoningState:
    print("\n[Step1 场景匹配] 这是一个什么场景?")
    state["errors"] = state.get("errors", [])
    hint = str(state.get("scenario_hint") or "")
    if hint:
        scenario = {
            "scenario_id": hint,
            "confidence": 1.0,
            "reasoning": f"上游invoice_ocr场景预检已确认({hint})",
            "source": "pipeline_precheck",
        }
    else:
        sc = match_scenario(state["invoice"])
        scenario = {
            "scenario_id": sc["scenario_id"],
            "confidence": sc["confidence"],
            "reasoning": sc["reasoning"],
            "source": "code_rule",
        }
        if not sc["matched"]:
            state["errors"].append("场景匹配失败: " + "；".join(sc["failures"]))
    state["scenario"] = scenario
    print(f"  场景: {scenario.get('scenario_id')} 置信度: {scenario['confidence']} "
          f"来源: {scenario['source']}")
    print(f"  理由: {scenario['reasoning']}")
    return state


def node_rule_select(state: ReasoningState) -> ReasoningState:
    print("\n[Step2 规则挑选] 这个场景需要哪些规则?")
    scenario = state.get("scenario") or {}
    scenario_id = scenario.get("scenario_id")
    rule_ids = [r.strip() for r in _scenario_rule_ids(scenario_id).split(",") if r.strip()] \
        if scenario_id else []
    rules = []
    if rule_ids:
        conn = get_conn()
        try:
            placeholders = ",".join("?" for _ in rule_ids)
            rules = [dict(r) for r in conn.execute(
                f"SELECT * FROM ontology_rules WHERE rule_id IN ({placeholders}) "
                "ORDER BY rule_id", rule_ids,
            ).fetchall()]
        finally:
            conn.close()
    if not rules:
        rules = _all_rules()
        if scenario_id:
            state.setdefault("errors", []).append(
                f"场景{scenario_id}未绑定规则, 回退全量规则链")
    state["rules"] = rules
    print(f"  选中规则: {[r['rule_id'] for r in rules]}")
    for r in rules:
        print(f"    {r['rule_id']} {r['rule_name']} (前置: {r['preconditions'] or '—'})")
    return state


def _scenario_rule_ids(scenario_id: str) -> str:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT rule_ids FROM ontology_scenarios WHERE scenario_id=?", (scenario_id,)
        ).fetchone()
        return (row["rule_ids"] or "") if row else ""
    finally:
        conn.close()


def _all_rules() -> list:
    conn = get_conn()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM ontology_rules ORDER BY rule_id"
        ).fetchall()]
    finally:
        conn.close()


def node_chain_assemble(state: ReasoningState) -> ReasoningState:
    print("\n[Step3 链路组装] 这些规则按什么顺序执行?")
    pending = {r["rule_id"]: r for r in (state.get("rules") or [])}
    ordered = []
    while pending:
        progressed = False
        for rid in list(pending):
            pre = {p.strip() for p in str(pending[rid]["preconditions"] or "").split(",")
                   if p.strip()}
            if pre & (set(pending) - {rid}):
                continue
            ordered.append(pending.pop(rid))
            progressed = True
        if not progressed:
            ordered.extend(pending.values())
            break
    chain = [{
        "step": i + 1,
        "rule_id": r["rule_id"],
        "name": r["rule_name"],
        "depends_on": [p.strip() for p in str(r["preconditions"] or "").split(",") if p.strip()],
    } for i, r in enumerate(ordered)]
    state["chain"] = chain
    print("  执行链: " + " -> ".join(f"{c['rule_id']}({c['name']})" for c in chain))
    return state


def node_reasoning_execute(state: ReasoningState) -> ReasoningState:
    print("\n[Step4 逻辑推演] 按规则计算出结果")
    chain = state.get("chain") or []
    execution: dict[str, Any] = {}
    resolution = resolve_invoice_ontology(state["invoice"], state["items"])

    for entry in chain:
        rid = entry["rule_id"]
        if rid == "R001":
            execution["R001"] = {
                "seller": resolution["seller"], "buyer": resolution["buyer"],
            }
            print(f"  R001 公司代码推理: 销售方 {state['invoice'].get('seller_name')} -> "
                  f"{resolution['seller'].get('code') or '未匹配'} ({resolution['seller'].get('type')}, "
                  f"score {resolution['seller'].get('score')})")
            print(f"           购买方 {state['invoice'].get('buyer_name')} -> "
                  f"{resolution['buyer'].get('code') or '未匹配'} ({resolution['buyer'].get('type')}, "
                  f"score {resolution['buyer'].get('score')})")
        elif rid == "R002":
            execution["R002"] = {"items": [
                {"item_no": i["item_no"], "item_name": i["item_name"],
                 "account": i["account"]} for i in resolution["items"]
            ]}
            for i in resolution["items"]:
                acc = i["account"]
                if acc["matched"]:
                    print(f"  R002 科目映射: {i['item_name']} -> {acc['account_code']} "
                          f"(匹配:{acc['matched_name']}, score {acc['score']})")
                else:
                    print(f"  R002 科目映射: {i['item_name']} -> 未匹配 [阻断]")
        elif rid == "R003":
            execution["R003"] = {
                "seller_cost_center": resolution["seller_cost_center"],
                "buyer_cost_center": resolution["buyer_cost_center"],
                "item_cost_centers": [
                    {"item_no": i["item_no"], "cost_center": i["cost_center"]}
                    for i in resolution["items"]
                ],
            }
            print(f"  R003 成本中心: 销售方 {resolution['seller_cost_center'].get('cost_center') or '—'} / "
                  f"购买方 {resolution['buyer_cost_center'].get('cost_center') or '—'} "
                  f"(行项目跟随购买方)")
        elif rid == "R004":
            decision = resolution["posting_decision"]
            execution["R004"] = {"should_post": decision["should_post"],
                                 "reason": decision["reason"]}
            print(f"  R004 关联交易判断: should_post={decision['should_post']}")
            print(f"           {decision['reason']}")

    state["execution"] = execution
    state["resolution"] = resolution
    return state


def node_conclusion_gen(state: ReasoningState) -> ReasoningState:
    print("\n[Step5 结论生成] 生成可执行的过账JSON")
    invoice, resolution = state["invoice"], state.get("resolution") or {}
    errors = state.get("errors", [])
    decision = (resolution.get("posting_decision") or {})
    confidence = float((state.get("scenario") or {}).get("confidence") or 0.0)
    chain_ids = [c["rule_id"] for c in state.get("chain", [])]

    unmapped = [i for i in resolution.get("items", []) if not i["account"]["matched"]]
    if unmapped:
        errors.append("InvoiceItem缺少account_code: " +
                      ", ".join(i["item_name"] for i in unmapped) + " (阻断过账)")
    if not decision.get("should_post"):
        errors.append(decision.get("reason") or "R004判定不允许过账")

    if not errors:
        payload = build_posting_payload(invoice, resolution)
        related = shacl_related_party(invoice, resolution)
        dual = shacl_dual_posting(payload)
        payload["validation"]["shacl"] = {
            "RelatedPartyShape": related, "DualPostingShape": dual,
        }
        payload["validation"]["conforms"] = bool(
            payload["validation"]["balance_check"]
            and payload["validation"]["related_party_check"]
            and payload["validation"]["tax_calculation_check"]
            and related["conforms"] and dual["conforms"]
        )
        if not payload["validation"]["conforms"]:
            failed = [k for k, v in payload["validation"].items()
                      if isinstance(v, bool) and not v]
            errors.append("SHACL/校验未通过: " + ", ".join(failed))
        payload["audit_trail"]["reasoning_chain"] = chain_ids
        payload["audit_trail"]["confidence_score"] = round(confidence, 2)
        payload["audit_trail"]["errors"] = errors
    else:
        payload = {"@context": ONTOLOGY_CONTEXT, "@type": "PostingPayload",
                   "invoice_ref": invoice.get("invoice_number"),
                   "validation": {"conforms": False}, "errors": errors}

    state["summary"] = (
        f"场景 {(state.get('scenario') or {}).get('scenario_id')} | "
        f"规则链 {'->'.join(chain_ids)} | "
        f"过账判断 {decision.get('should_post')} | "
        f"校验 {'通过' if payload['validation'].get('conforms') else '未通过'}"
    )
    state["confidence"] = confidence
    if isinstance(payload.get("audit_trail"), dict):
        payload["audit_trail"]["summary"] = state["summary"]
    state["payload"] = payload
    state["validation"] = payload.get("validation", {})
    state["errors"] = errors

    print(f"  校验: {json.dumps(payload.get('validation', {}), ensure_ascii=False, default=str)[:400]}")
    if errors:
        print("  阻断原因:")
        for e in errors:
            print(f"    - {e}")
    print(f"  结论: {state['summary']}")
    return state


def build_graph():
    from langgraph.graph import END, START, StateGraph

    builder = StateGraph(ReasoningState)
    builder.add_node("scenario_match", node_scenario_match)
    builder.add_node("rule_select", node_rule_select)
    builder.add_node("chain_assemble", node_chain_assemble)
    builder.add_node("reasoning_execute", node_reasoning_execute)
    builder.add_node("conclusion_gen", node_conclusion_gen)
    builder.add_edge(START, "scenario_match")
    builder.add_edge("scenario_match", "rule_select")
    builder.add_edge("rule_select", "chain_assemble")
    builder.add_edge("chain_assemble", "reasoning_execute")
    builder.add_edge("reasoning_execute", "conclusion_gen")
    builder.add_edge("conclusion_gen", END)
    return builder.compile()


def persist_reasoning(state: ReasoningState) -> None:
    invoice = state["invoice"]
    resolution = state.get("resolution") or {}
    payload = state.get("payload") or {}
    errors = state.get("errors") or []
    conforms = bool((payload.get("validation") or {}).get("conforms"))
    status = "REASONED" if conforms else "REJECTED"
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    conn = get_conn()
    try:
        conn.execute(
            "UPDATE invoices SET issuer_code=?, receiver_code=?, status=?, updated_at=? "
            "WHERE invoice_number=?",
            (resolution.get("seller", {}).get("code", ""),
             resolution.get("buyer", {}).get("code", ""), status, now,
             invoice["invoice_number"]),
        )
        for i in resolution.get("items", []):
            conn.execute(
                "UPDATE invoice_items SET account_code=?, cost_center_code=? "
                "WHERE invoice_number=? AND item_no=?",
                (i["account"].get("account_code", ""),
                 i["cost_center"].get("cost_center", ""),
                 invoice["invoice_number"], i["item_no"]),
            )
        event_id = f"EVT-{invoice['invoice_number']}"
        conn.execute(
            "UPDATE interco_events SET seller_code=?, buyer_code=?, scenario_id=?, "
            "reasoning_payload=?, status=?, updated_at=? WHERE event_id=?",
            (resolution.get("seller", {}).get("code", ""),
             resolution.get("buyer", {}).get("code", ""),
             (state.get("scenario") or {}).get("scenario_id"),
             json.dumps(payload, ensure_ascii=False, default=str),
             status, now, event_id),
        )
        write_audit(conn, "REASONING_COMPLETED", {
            "invoice_number": invoice["invoice_number"],
            "scenario_id": (state.get("scenario") or {}).get("scenario_id"),
            "chain": [c["rule_id"] for c in state.get("chain", [])],
            "should_post": (resolution.get("posting_decision") or {}).get("should_post"),
            "conforms": conforms,
            "errors": errors,
            "engine": "rule_code",
            "confidence": state.get("confidence"),
            "summary": state.get("summary"),
        })
        conn.commit()
    finally:
        conn.close()


def run_reasoning(invoice_number: str, scenario_hint: Optional[str] = None) -> dict:
    init_db()
    seed_ontology()
    data = load_invoice(invoice_number)
    if not data:
        raise SystemExit(f"发票不存在: {invoice_number}")

    invoice, items = data["invoice"], data["items"]
    issues = []
    try:
        issues = json.loads(invoice.get("quality_issues") or "[]")
    except json.JSONDecodeError:
        pass
    blocked = blocking_issues(issues)
    if not invoice.get("tax_rate"):
        blocked.append({"level": "阻断", "rule": "Invoice缺少tax_rate",
                        "detail": "无法推导税额, 阻断推理"})
    if not items:
        blocked.append({"level": "阻断", "rule": "Invoice缺少行项目", "detail": "无行项目"})

    print("=" * 64)
    print(f"  本体推理: {invoice_number}")
    print(f"  数据库: {DB_PATH}")
    print("=" * 64)

    if blocked:
        print("\n数据质量阻断, Agent停止推断:")
        for b in blocked:
            print(f"  [阻断] {b['rule']}: {b['detail']}")
        conn = get_conn()
        try:
            conn.execute("UPDATE invoices SET status='BLOCKED', updated_at=? "
                         "WHERE invoice_number=?",
                         (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), invoice_number))
            conn.execute("UPDATE interco_events SET status='BLOCKED', updated_at=? "
                         "WHERE invoice_number=?",
                         (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), invoice_number))
            write_audit(conn, "REASONING_BLOCKED",
                        {"invoice_number": invoice_number, "issues": blocked})
            conn.commit()
        finally:
            conn.close()
        return {"errors": blocked, "payload": {}, "validation": {"conforms": False}}

    graph = build_graph()
    state: ReasoningState = {
        "invoice_number": invoice_number,
        "invoice": invoice,
        "items": items,
        "errors": [],
    }
    if scenario_hint:
        state["scenario_hint"] = scenario_hint
    final = graph.invoke(state)
    persist_reasoning(final)

    print("\n" + "-" * 64)
    print(f"  推理状态: {'REASONED' if (final.get('validation') or {}).get('conforms') else 'REJECTED'}")
    print(f"  过账载荷已保存到 interco_events.reasoning_payload")
    print("  下一步: python sap_posting.py --latest")
    print("-" * 64)
    return final


def list_invoices() -> None:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT invoice_number, seller_name, buyer_name, gross_amount, status, updated_at "
            "FROM invoices ORDER BY updated_at DESC LIMIT 20"
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        print("暂无发票, 请先运行 python invoice_ocr.py <发票文件>")
        return
    print(f"{'发票号':<24} {'状态':<10} {'价税合计':>12}  销售方 -> 购买方")
    for r in rows:
        print(f"{r['invoice_number']:<24} {r['status']:<10} {r['gross_amount'] or 0:>12}  "
              f"{r['seller_name']} -> {r['buyer_name']}")


def main():
    parser = argparse.ArgumentParser(description="本体推理智能体")
    parser.add_argument("--invoice", help="指定发票号码")
    parser.add_argument("--latest", action="store_true", help="推理最近一张发票")
    parser.add_argument("--list", action="store_true", help="列出发票")
    args = parser.parse_args()

    init_db()
    seed_ontology()

    if args.list:
        list_invoices()
        return
    invoice_number = args.invoice or (latest_invoice_number() if args.latest else None)
    while not invoice_number:
        try:
            invoice_number = input("请输入发票号码 (--list可查看): ").strip()
        except EOFError:
            return
        if invoice_number.lower() in ("exit", "quit"):
            return
    run_reasoning(invoice_number)


if __name__ == "__main__":
    main()
