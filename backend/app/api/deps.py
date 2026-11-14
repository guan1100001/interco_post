"""依赖注入：控制器只声明依赖，不自行创建会话或解析身份。"""
from __future__ import annotations

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from backend.app.api.guard import AuthContext
from backend.app.core.errors import UnauthorizedError
from backend.app.db import models as m
from backend.app.db.session import get_db
from backend.app.repositories import user_repo as repo
from backend.app.services.pipeline_service import PipelineService


def get_pipeline_service(db: Session = Depends(get_db)) -> PipelineService:
    return PipelineService(db)


def current_user(request: Request) -> AuthContext:
    ctx = getattr(request.state, "auth", None)
    if ctx is None:
        raise UnauthorizedError()
    return ctx


def current_db_user(request: Request, db: Session = Depends(get_db)) -> m.User:
    ctx = current_user(request)
    row = repo.get_by_id(db, ctx.user_id)
    if row is None:
        raise UnauthorizedError("账号不存在，请重新登录")
    return row
