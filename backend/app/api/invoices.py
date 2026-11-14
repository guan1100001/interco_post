"""发票数据库查询控制器。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.core.errors import NotFoundError
from backend.app.db.session import get_db
from backend.app.repositories import invoice_repo
from backend.app.schemas.invoice import (
    AccountingDocumentOut, AuditLogOut, IntercoEventOut, InvoiceDetailOut,
    InvoiceItemOut, InvoiceOut, InvoicePageOut, PostingEventOut, StatsOut,
)

router = APIRouter(prefix=f"{settings.api_prefix}/invoices", tags=["invoices"])


@router.get("", response_model=InvoicePageOut)
def list_invoices(
    query: str = Query("", max_length=200),
    status: str = Query("", max_length=40),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    rows, total = invoice_repo.list_invoices(
        db, query=query.strip(), status=status.strip(), page=page, page_size=page_size
    )
    return InvoicePageOut(
        items=[InvoiceOut.model_validate(r) for r in rows],
        total=total, page=page, page_size=page_size,
    )


@router.get("/stats/overview", response_model=StatsOut)
def overview(db: Session = Depends(get_db)):
    return StatsOut(**invoice_repo.stats(db))


@router.get("/status/options", response_model=list[str])
def status_options(db: Session = Depends(get_db)):
    return invoice_repo.status_options(db)


@router.get("/{invoice_number}", response_model=InvoiceDetailOut)
def get_invoice(invoice_number: str, db: Session = Depends(get_db)):
    detail = invoice_repo.get_detail(db, invoice_number)
    if detail is None:
        raise NotFoundError(f"发票不存在: {invoice_number}")
    payload = InvoiceOut.model_validate(detail["invoice"]).model_dump()
    payload.update({
        "quality_issues": detail["quality_issues"],
        "items": [InvoiceItemOut.model_validate(x) for x in detail["items"]],
        "event": IntercoEventOut.model_validate(detail["event"]) if detail["event"] else None,
        "documents": [AccountingDocumentOut.model_validate(x) for x in detail["documents"]],
        "posting_events": [PostingEventOut.model_validate(x) for x in detail["posting_events"]],
        "audit_logs": [AuditLogOut(**x) for x in detail["audit_logs"]],
        "reasoning_payload": detail["reasoning_payload"],
    })
    return InvoiceDetailOut(**payload)
