"""现有 SQLite 表到 SQLAlchemy 的映射 + 新增会话表。

前 12 张表由原有 invoice_ocr.py 的 SCHEMA_SQL 创建，本文件只做只读映射，
不声明外键约束以外的结构变更；pipeline_sessions 是本次新增的增量表。
"""
from __future__ import annotations

from sqlalchemy import Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base


class Company(Base):
    __tablename__ = "companies"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_code: Mapped[str] = mapped_column(String)
    company_name: Mapped[str] = mapped_column(String)
    group_id: Mapped[str | None] = mapped_column(String)
    company_type: Mapped[str | None] = mapped_column(String)
    status: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[str | None] = mapped_column(String)


class Invoice(Base):
    __tablename__ = "invoices"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    invoice_number: Mapped[str] = mapped_column(String, unique=True, index=True)
    invoice_type: Mapped[str | None] = mapped_column(String)
    issue_date: Mapped[str | None] = mapped_column(String)
    seller_name: Mapped[str | None] = mapped_column(String)
    buyer_name: Mapped[str | None] = mapped_column(String)
    seller_tax_id: Mapped[str | None] = mapped_column(String)
    buyer_tax_id: Mapped[str | None] = mapped_column(String)
    issuer_code: Mapped[str | None] = mapped_column(String)
    receiver_code: Mapped[str | None] = mapped_column(String)
    gross_amount: Mapped[float | None] = mapped_column(Float)
    tax_rate: Mapped[str | None] = mapped_column(String)
    net_amount: Mapped[float | None] = mapped_column(Float)
    tax_amount: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str | None] = mapped_column(String)
    remark: Mapped[str | None] = mapped_column(String)
    invoice_issuer: Mapped[str | None] = mapped_column(String)
    source_file: Mapped[str | None] = mapped_column(String)
    quality_issues: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String, index=True)
    created_at: Mapped[str | None] = mapped_column(String)
    updated_at: Mapped[str | None] = mapped_column(String)


class InvoiceItem(Base):
    __tablename__ = "invoice_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    invoice_number: Mapped[str] = mapped_column(String, index=True)
    item_no: Mapped[int] = mapped_column(Integer)
    description: Mapped[str | None] = mapped_column(String)
    quantity: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String)
    unit_price: Mapped[float | None] = mapped_column(Float)
    amount: Mapped[float | None] = mapped_column(Float)
    tax_rate: Mapped[str | None] = mapped_column(String)
    tax_amount: Mapped[float | None] = mapped_column(Float)
    account_code: Mapped[str | None] = mapped_column(String)
    cost_center_code: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[str | None] = mapped_column(String)


class IntercoEvent(Base):
    __tablename__ = "interco_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[str] = mapped_column(String, unique=True)
    invoice_number: Mapped[str | None] = mapped_column(String, index=True)
    seller_code: Mapped[str | None] = mapped_column(String)
    buyer_code: Mapped[str | None] = mapped_column(String)
    event_type: Mapped[str | None] = mapped_column(String)
    amount: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str | None] = mapped_column(String)
    scenario_id: Mapped[str | None] = mapped_column(String)
    reasoning_payload: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str | None] = mapped_column(String)
    updated_at: Mapped[str | None] = mapped_column(String)


class AccountingDocument(Base):
    __tablename__ = "accounting_documents"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    doc_no: Mapped[str] = mapped_column(String)
    company_code: Mapped[str] = mapped_column(String)
    doc_type: Mapped[str | None] = mapped_column(String)
    posting_date: Mapped[str | None] = mapped_column(String)
    total_debit: Mapped[float | None] = mapped_column(Float)
    total_credit: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str | None] = mapped_column(String)
    invoice_ref: Mapped[str | None] = mapped_column(String, index=True)
    derived_doc: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[str | None] = mapped_column(String)


class PostingEvent(Base):
    __tablename__ = "posting_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    posting_id: Mapped[str] = mapped_column(String, unique=True)
    doc_ref: Mapped[str | None] = mapped_column(String)
    company_code: Mapped[str | None] = mapped_column(String)
    bapi_status: Mapped[str | None] = mapped_column(String)
    message: Mapped[str | None] = mapped_column(String)
    payload_json: Mapped[str | None] = mapped_column(Text)
    timestamp: Mapped[str | None] = mapped_column(String)


class Account(Base):
    __tablename__ = "accounts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_code: Mapped[str] = mapped_column(String)
    account_name: Mapped[str] = mapped_column(String)
    account_type: Mapped[str] = mapped_column(String)
    company_code: Mapped[str | None] = mapped_column(String)


class CostCenter(Base):
    __tablename__ = "cost_centers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cost_center_code: Mapped[str] = mapped_column(String, unique=True)
    name: Mapped[str | None] = mapped_column(String)
    company_code: Mapped[str | None] = mapped_column(String)


class Contract(Base):
    __tablename__ = "contracts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contract_no: Mapped[str] = mapped_column(String, unique=True)
    party_a_code: Mapped[str | None] = mapped_column(String)
    party_b_code: Mapped[str | None] = mapped_column(String)
    effective_date: Mapped[str | None] = mapped_column(String)
    amount: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[str | None] = mapped_column(String)


class OntologyRelationship(Base):
    __tablename__ = "ontology_relationships"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    from_object: Mapped[str] = mapped_column(String)
    relation_name: Mapped[str] = mapped_column(String)
    to_object: Mapped[str] = mapped_column(String)
    cardinality: Mapped[str | None] = mapped_column(String)
    description: Mapped[str | None] = mapped_column(String)


class OntologyRule(Base):
    __tablename__ = "ontology_rules"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule_id: Mapped[str] = mapped_column(String, unique=True)
    rule_name: Mapped[str] = mapped_column(String)
    swrl: Mapped[str | None] = mapped_column(Text)
    inputs: Mapped[str | None] = mapped_column(Text)
    outputs: Mapped[str | None] = mapped_column(Text)
    preconditions: Mapped[str | None] = mapped_column(Text)


class OntologyScenario(Base):
    __tablename__ = "ontology_scenarios"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scenario_id: Mapped[str] = mapped_column(String, unique=True)
    scenario_name: Mapped[str] = mapped_column(String)
    description: Mapped[str | None] = mapped_column(Text)
    conditions: Mapped[str | None] = mapped_column(Text)
    rule_ids: Mapped[str | None] = mapped_column(Text)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    action: Mapped[str] = mapped_column(String)
    actor: Mapped[str | None] = mapped_column(String)
    detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str | None] = mapped_column(String)


class PipelineSession(Base):
    """全栈交互会话：把 CLI 的 input() 人工确认点转成可持久化的状态机。"""
    __tablename__ = "pipeline_sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    file_path: Mapped[str] = mapped_column(Text)
    invoice_number: Mapped[str | None] = mapped_column(String)
    stage: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32))
    draft_json: Mapped[str] = mapped_column(Text, default="{}")
    issues_json: Mapped[str] = mapped_column(Text, default="[]")
    context_json: Mapped[str] = mapped_column(Text, default="{}")
    messages_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[str] = mapped_column(String(32))
    updated_at: Mapped[str] = mapped_column(String(32))


# ---------------------------------------------------------------- 认证与授权
class User(Base):
    """系统账号。password_hash 为 PBKDF2-SHA256 加盐摘要，任何情况下都不存明文密码。"""
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(128), default="")
    role: Mapped[str] = mapped_column(String(32), default="operator", index=True)  # admin / operator / viewer
    is_active: Mapped[int] = mapped_column(Integer, default=1)
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[str | None] = mapped_column(String(32))
    last_login_at: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[str] = mapped_column(String(32))
    updated_at: Mapped[str] = mapped_column(String(32))


class RefreshToken(Base):
    """服务端刷新令牌：只存 SHA-256 摘要，支持轮换、撤销与过期清理。"""
    __tablename__ = "refresh_tokens"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    expires_at: Mapped[str] = mapped_column(String(32))
    revoked_at: Mapped[str | None] = mapped_column(String(32))
    replaced_by_hash: Mapped[str | None] = mapped_column(String(128))
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(32))


class AuthAuditLog(Base):
    """登录、刷新、改密与账号变更审计，不记录任何密码或令牌内容。"""
    __tablename__ = "auth_audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), index=True)
    action: Mapped[str] = mapped_column(String(48), index=True)
    success: Mapped[int] = mapped_column(Integer, default=1)
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(32))

