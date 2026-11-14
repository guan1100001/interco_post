"""流水线会话仓储：会话行的增删改查与 JSON 字段编解码。"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db import models as m


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default


def create(db: Session, **fields: Any) -> m.PipelineSession:
    row = m.PipelineSession(**fields)
    db.add(row)
    db.commit()
    return row


def get(db: Session, session_id: str) -> m.PipelineSession | None:
    return db.get(m.PipelineSession, session_id)


def save(db: Session, row: m.PipelineSession) -> None:
    db.add(row)
    db.commit()


def list_recent(db: Session, limit: int = 20) -> list[m.PipelineSession]:
    return list(db.scalars(
        select(m.PipelineSession)
        .order_by(m.PipelineSession.created_at.desc())
        .limit(limit)
    ).all())
