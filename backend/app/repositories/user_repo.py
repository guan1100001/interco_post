"""账号 / 刷新令牌 / 认证审计的数据访问层。

约定：本层只做 add / flush / 查询，不做 commit —— 由服务层在事务边界统一提交，
保证「撤销旧刷新令牌 + 签发新令牌」等多步写入的原子性。
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.db import models as m


# ---------------------------------------------------------------- users
def get_by_username(db: Session, username: str) -> m.User | None:
    return db.scalar(select(m.User).where(m.User.username == username))


def get_by_id(db: Session, user_id: int) -> m.User | None:
    return db.get(m.User, user_id)


def list_users(db: Session) -> list[m.User]:
    return list(db.scalars(select(m.User).order_by(m.User.id.asc())).all())


def count_users(db: Session) -> int:
    return int(db.scalar(select(func.count()).select_from(m.User)) or 0)


def add_user(db: Session, **fields) -> m.User:
    row = m.User(**fields)
    db.add(row)
    db.flush()
    return row


# ---------------------------------------------------------------- refresh tokens
def add_refresh(db: Session, **fields) -> m.RefreshToken:
    row = m.RefreshToken(**fields)
    db.add(row)
    db.flush()
    return row


def get_refresh(db: Session, token_hash: str) -> m.RefreshToken | None:
    return db.scalar(select(m.RefreshToken).where(m.RefreshToken.token_hash == token_hash))


def list_active_refresh(db: Session, user_id: int) -> list[m.RefreshToken]:
    return list(db.scalars(
        select(m.RefreshToken).where(
            m.RefreshToken.user_id == user_id,
            m.RefreshToken.revoked_at.is_(None),
        )
    ).all())


# ---------------------------------------------------------------- audit
def add_audit(db: Session, **fields) -> m.AuthAuditLog:
    row = m.AuthAuditLog(**fields)
    db.add(row)
    db.flush()
    return row
