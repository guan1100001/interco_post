"""发票查询仓储：只负责数据访问，一次性取回关联数据避免 N+1。"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from backend.app.db import models as m


def list_invoices(
    db: Session, *, query: str = "", status: str = "", page: int = 1, page_size: int = 20
) -> tuple[list[m.Invoice], int]:
    conds = []
    if query:
        like = f"%{query}%"
        conds.append(or_(
            m.Invoice.invoice_number.like(like),
            m.Invoice.seller_name.like(like),
            m.Invoice.buyer_name.like(like),
            m.Invoice.source_file.like(like),
        ))
    if status:
        conds.append(m.Invoice.status == status)
    total = db.scalar(select(func.count(m.Invoice.id)).where(*conds)) or 0
    rows = db.scalars(
        select(m.Invoice).where(*conds)
        .order_by(m.Invoice.updated_at.desc(), m.Invoice.id.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ).all()
    return list(rows), int(total)


def _audit_for(db: Session, number: str, limit: int = 30) -> list[dict[str, Any]]:
    """audit_log.detail 是 JSON 文本，按发票号在最近记录中筛选（数据量小，避免全表扫描）。"""
    recent = db.scalars(
        select(m.AuditLog).order_by(m.AuditLog.id.desc()).limit(400)
    ).all()
    matched: list[dict[str, Any]] = []
    for row in recent:
        if number not in (row.detail or ""):
            continue
        matched.append({
            "id": row.id, "action": row.action or "", "actor": row.actor,
            "detail": row.detail, "created_at": row.created_at,
        })
        if len(matched) >= limit:
            break
    return matched


def get_detail(db: Session, number: str) -> dict[str, Any] | None:
    inv = db.scalar(select(m.Invoice).where(m.Invoice.invoice_number == number))
    if inv is None:
        return None

    items = db.scalars(
        select(m.InvoiceItem).where(m.InvoiceItem.invoice_number == number)
        .order_by(m.InvoiceItem.item_no)
    ).all()
    event = db.scalar(
        select(m.IntercoEvent).where(m.IntercoEvent.invoice_number == number)
    )
    docs = db.scalars(
        select(m.AccountingDocument).where(m.AccountingDocument.invoice_ref == number)
        .order_by(m.AccountingDocument.id)
    ).all()
    doc_nos = [d.doc_no for d in docs]
    posting_events = db.scalars(
        select(m.PostingEvent).where(m.PostingEvent.doc_ref.in_(doc_nos))
        .order_by(m.PostingEvent.id.desc())
    ).all() if doc_nos else []

    try:
        quality_issues = json.loads(inv.quality_issues or "[]")
    except (json.JSONDecodeError, TypeError):
        quality_issues = []
    # 输出模型要求 list[dict] / dict；历史数据可能是别的 JSON 结构，这里先规整，
    # 避免一条旧记录让整个详情接口 500。
    if isinstance(quality_issues, dict):
        quality_issues = [quality_issues]
    if not isinstance(quality_issues, list):
        quality_issues = []
    quality_issues = [q for q in quality_issues if isinstance(q, dict)]

    payload: dict[str, Any] | None = None
    if event and event.reasoning_payload:
        try:
            parsed = json.loads(event.reasoning_payload)
            payload = parsed if isinstance(parsed, dict) else None
        except (json.JSONDecodeError, TypeError):
            payload = None

    return {
        "invoice": inv,
        "quality_issues": quality_issues,
        "items": list(items),
        "event": event,
        "documents": list(docs),
        "posting_events": list(posting_events),
        "audit_logs": _audit_for(db, number),
        "reasoning_payload": payload,
    }


def stats(db: Session) -> dict[str, Any]:
    status_rows = db.execute(
        select(m.Invoice.status, func.count(m.Invoice.id)).group_by(m.Invoice.status)
    ).all()
    return {
        "total_invoices": int(db.scalar(select(func.count(m.Invoice.id))) or 0),
        "total_amount": float(db.scalar(select(func.coalesce(func.sum(m.Invoice.gross_amount), 0))) or 0),
        "status_counts": {str(k): int(v) for k, v in status_rows},
        "total_documents": int(db.scalar(select(func.count(m.AccountingDocument.id))) or 0),
        "total_events": int(db.scalar(select(func.count(m.IntercoEvent.id))) or 0),
        "total_sessions": int(db.scalar(select(func.count(m.PipelineSession.id))) or 0),
    }


def status_options(db: Session) -> list[str]:
    return [str(r) for r in db.scalars(select(m.Invoice.status).distinct()).all() if r]
