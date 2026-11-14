"""发票查询相关 Pydantic 模型（数据库 -> API 的类型边界）。"""
from __future__ import annotations

from typing import Annotated, Any

from pydantic import BeforeValidator, Field

from backend.app.core.numbers import to_optional_float
from backend.app.schemas.common import StrictModel

# SQLite 弱类型：REAL 列可能被旧数据或表单空值写成 '' / 带格式字符串。
# 在读取边界统一归一化，避免一条行项目让整个「查看详情」接口 500。
OptionalFloat = Annotated[float | None, BeforeValidator(to_optional_float)]


class InvoiceItemOut(StrictModel):
    id: int
    item_no: int
    description: str | None = None
    quantity: OptionalFloat = None
    unit: str | None = None
    unit_price: OptionalFloat = None
    amount: OptionalFloat = None
    tax_rate: str | None = None
    tax_amount: OptionalFloat = None
    account_code: str | None = None
    cost_center_code: str | None = None


class IntercoEventOut(StrictModel):
    event_id: str
    seller_code: str | None = None
    buyer_code: str | None = None
    status: str | None = None
    scenario_id: str | None = None
    amount: OptionalFloat = None
    updated_at: str | None = None


class AccountingDocumentOut(StrictModel):
    doc_no: str
    company_code: str
    doc_type: str | None = None
    posting_date: str | None = None
    total_debit: OptionalFloat = None
    total_credit: OptionalFloat = None
    status: str | None = None
    derived_doc: str | None = None


class PostingEventOut(StrictModel):
    posting_id: str
    doc_ref: str | None = None
    company_code: str | None = None
    bapi_status: str | None = None
    message: str | None = None
    timestamp: str | None = None


class AuditLogOut(StrictModel):
    id: int
    action: str
    actor: str | None = None
    detail: str | None = None
    created_at: str | None = None


class InvoiceOut(StrictModel):
    id: int
    invoice_number: str
    invoice_type: str | None = None
    issue_date: str | None = None
    seller_name: str | None = None
    buyer_name: str | None = None
    issuer_code: str | None = None
    receiver_code: str | None = None
    gross_amount: OptionalFloat = None
    net_amount: OptionalFloat = None
    tax_amount: OptionalFloat = None
    tax_rate: str | None = None
    currency: str | None = None
    source_file: str | None = None
    status: str
    created_at: str | None = None
    updated_at: str | None = None


class InvoicePageOut(StrictModel):
    items: list[InvoiceOut]
    total: int
    page: int
    page_size: int


class InvoiceDetailOut(InvoiceOut):
    quality_issues: list[dict[str, Any]] = Field(default_factory=list)
    items: list[InvoiceItemOut] = Field(default_factory=list)
    event: IntercoEventOut | None = None
    documents: list[AccountingDocumentOut] = Field(default_factory=list)
    posting_events: list[PostingEventOut] = Field(default_factory=list)
    audit_logs: list[AuditLogOut] = Field(default_factory=list)
    reasoning_payload: dict[str, Any] | None = None


class StatsOut(StrictModel):
    total_invoices: int
    total_amount: float
    status_counts: dict[str, int] = Field(default_factory=dict)
    total_documents: int
    total_events: int
    total_sessions: int
