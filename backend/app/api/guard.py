"""认证与授权守卫（HTTP 边界层）。

统一拦截所有 /api/* 请求：验证 JWT、确认账号仍启用、按 RBAC 校验权限。
只保留登录 / 刷新 / 注销三个公开端点，其余业务接口必须登录后才能访问。
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request
from fastapi.responses import JSONResponse

from backend.app.core import security as sec
from backend.app.core.config import settings
from backend.app.core.errors import AppError, ForbiddenError, UnauthorizedError
from backend.app.db.session import SessionLocal
from backend.app.repositories import user_repo as repo

P = settings.api_prefix
PUBLIC_API = {
    f"{P}/auth/login",
    f"{P}/auth/refresh",
    f"{P}/auth/logout",
}
SELF_SERVICE_API = {
    f"{P}/auth/me",
    f"{P}/auth/password",
}
ADMIN_PREFIX = f"{P}/auth/users"
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@dataclass(frozen=True)
class AuthContext:
    user_id: int
    username: str
    role: str


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        return None
    return header[7:].strip() or None


def _error(exc: AppError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"error": exc.to_dict()})


def guard(request: Request) -> JSONResponse | None:
    """返回 None 表示放行；否则返回规范化错误响应。"""
    path = request.url.path
    if not path.startswith("/api/") or path in PUBLIC_API:
        return None

    token = _bearer(request)
    if not token:
        return _error(UnauthorizedError())
    try:
        claims = sec.decode_access_token(token)
        user_id = int(claims["sub"])
    except (UnauthorizedError, ValueError, KeyError, TypeError) as exc:
        return _error(exc if isinstance(exc, UnauthorizedError) else UnauthorizedError())

    with SessionLocal() as db:
        row = repo.get_by_id(db, user_id)
        if row is None or not row.is_active:
            return _error(UnauthorizedError("账号不可用，请重新登录"))
        # 角色以数据库实时状态为准，避免令牌里的旧权限继续生效
        ctx = AuthContext(user_id=row.id, username=row.username, role=row.role)
    request.state.auth = ctx

    if path.startswith(ADMIN_PREFIX) and ctx.role != "admin":
        return _error(ForbiddenError("只有管理员可以管理账号"))
    if (
        request.method in WRITE_METHODS
        and path not in SELF_SERVICE_API
        and not path.startswith(ADMIN_PREFIX)
        and ctx.role == "viewer"
    ):
        return _error(ForbiddenError("只读账号不能执行此操作"))
    return None
