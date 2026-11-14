"""认证控制器：只做请求解析、服务调用与响应格式化，不承载业务规则。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from backend.app.api.deps import current_db_user, current_user
from backend.app.core import security as sec
from backend.app.core.config import settings
from backend.app.core.errors import BadRequestError
from backend.app.db import models as m
from backend.app.db.session import get_db
from backend.app.repositories import user_repo as repo
from backend.app.schemas.auth import (
    LoginIn, OkOut, PasswordChangeIn, RefreshOut, TokenOut, UserCreateIn,
    UserOut, UserUpdateIn,
)
from backend.app.services import auth_service as svc

router = APIRouter(prefix=f"{settings.api_prefix}/auth", tags=["auth"])
_COOKIE_PATH = f"{settings.api_prefix}/auth"


def _client(request: Request) -> tuple[str | None, str | None]:
    ip = request.client.host if request.client else None
    return ip, request.headers.get("user-agent")


def _set_refresh_cookie(response: Response, raw: str) -> None:
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=raw,
        max_age=settings.auth_refresh_ttl_days * 86400,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        path=_COOKIE_PATH,
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        key=settings.auth_cookie_name,
        path=_COOKIE_PATH,
        secure=settings.auth_cookie_secure,
        samesite="lax",
    )


def _optional_username(request: Request) -> str:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        return ""
    try:
        return str(sec.decode_access_token(header[7:].strip()).get("username", ""))
    except Exception:  # noqa: BLE001  注销时令牌失效也要能清 Cookie
        return ""


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn, request: Request, response: Response,
          db: Session = Depends(get_db)):
    ip, ua = _client(request)
    user, access, ttl, raw = svc.login(db, body.username, body.password, ip, ua)
    _set_refresh_cookie(response, raw)
    return TokenOut(access_token=access, expires_in=ttl, user=UserOut.model_validate(user))


@router.post("/refresh", response_model=RefreshOut)
def refresh(request: Request, response: Response, db: Session = Depends(get_db)):
    ip, ua = _client(request)
    raw = request.cookies.get(settings.auth_cookie_name)
    _user, access, ttl, new_raw = svc.refresh(db, raw, ip, ua)
    _set_refresh_cookie(response, new_raw)
    return RefreshOut(access_token=access, expires_in=ttl)


@router.post("/logout", response_model=OkOut)
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    ip, ua = _client(request)
    svc.logout(db, request.cookies.get(settings.auth_cookie_name), ip, ua,
               username=_optional_username(request))
    _clear_refresh_cookie(response)
    return OkOut(message="已退出登录")


@router.get("/me", response_model=UserOut)
def me(user: m.User = Depends(current_db_user)):
    return UserOut.model_validate(user)


@router.post("/password", response_model=OkOut)
def change_password(body: PasswordChangeIn, user: m.User = Depends(current_db_user),
                    db: Session = Depends(get_db)):
    svc.change_password(db, user, body.old_password, body.new_password)
    return OkOut(message="密码已修改，请重新登录")


# ---------------------------------------------------------------- 管理员账号管理
@router.get("/users", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db)):
    return [UserOut.model_validate(row) for row in repo.list_users(db)]


@router.post("/users", response_model=UserOut)
def create_user(body: UserCreateIn, db: Session = Depends(get_db)):
    return UserOut.model_validate(svc.create_user(db, body))


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(user_id: int, body: UserUpdateIn, db: Session = Depends(get_db),
                ctx=Depends(current_user)):
    if user_id == ctx.user_id and (body.is_active is False or body.role not in (None, "admin")):
        raise BadRequestError("不能停用自己的账号或降低自己的角色")
    return UserOut.model_validate(svc.update_user(db, user_id, body, ctx.username))
