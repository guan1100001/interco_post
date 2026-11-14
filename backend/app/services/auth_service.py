"""认证业务服务：登录、令牌轮换、注销、改密与账号管理。

服务层只接收普通数据（字符串、字典），不依赖 Request/Response 等 HTTP 类型；
事务边界统一在这里 commit / rollback。
"""
from __future__ import annotations

import secrets
from datetime import timedelta

from sqlalchemy.orm import Session

from backend.app.core import security as sec
from backend.app.core.config import settings
from backend.app.core.errors import (
    AccountLockedError, BadRequestError, ConflictError, NotFoundError, UnauthorizedError,
)
from backend.app.core.logging import get_logger
from backend.app.db import models as m
from backend.app.db.session import SessionLocal
from backend.app.repositories import user_repo as repo
from backend.app.schemas.auth import ROLES

log = get_logger(__name__)


def _role(value: str) -> str:
    role = (value or "").strip().lower()
    if role not in ROLES:
        raise BadRequestError(f"角色只能是 {', '.join(ROLES)}")
    return role


def _audit(db: Session, username: str, action: str, success: bool,
           ip: str | None = None, ua: str | None = None, detail: str | None = None) -> None:
    repo.add_audit(
        db, username=username, action=action, success=1 if success else 0,
        ip=(ip or "")[:64] or None, user_agent=(ua or "")[:500] or None,
        detail=detail, created_at=sec.iso(),
    )


# ---------------------------------------------------------------- 首次启动引导
def ensure_bootstrap_admin() -> None:
    """users 为空时创建唯一管理员。密码来自 .env；未配置则随机生成并存到本机文件。"""
    with SessionLocal() as db:
        try:
            if repo.count_users(db) > 0:
                return
            username = sec.validate_username(settings.bootstrap_admin_username)
            password = settings.bootstrap_admin_password
            generated = not password
            if generated:
                password = secrets.token_urlsafe(12)
            sec.validate_password(password)
            now = sec.iso()
            repo.add_user(
                db, username=username, password_hash=sec.hash_password(password),
                display_name="系统管理员", role="admin", is_active=1,
                failed_attempts=0, created_at=now, updated_at=now,
            )
            _audit(db, username, "bootstrap_admin", True, detail="首次启动初始化管理员账号")
            db.commit()
            if generated:
                target = settings.runtime_dir / ".initial_admin_password"
                target.write_text(
                    f"username: {username}\npassword: {password}\n"
                    "该密码仅首次启动时生成，请登录后立即修改并删除本文件。\n",
                    encoding="utf-8",
                )
                log.warning("initial_admin_password_generated", extra={"extra": {
                    "username": username, "password_file": str(target),
                }})
            else:
                log.info("bootstrap_admin_created", extra={"extra": {"username": username}})
        except Exception:
            db.rollback()
            raise


# ---------------------------------------------------------------- 登录
def login(db: Session, username: str, password: str,
          ip: str | None = None, ua: str | None = None) -> tuple[m.User, str, int, str]:
    name = (username or "").strip().lower()
    if not name or not password:
        raise UnauthorizedError("请输入用户名和密码")
    now = sec.utcnow()
    row = repo.get_by_username(db, name)

    if row is None:
        _audit(db, name, "login_failed", False, ip, ua, "账号不存在")
        db.commit()
        raise UnauthorizedError("用户名或密码不正确")

    if not row.is_active:
        _audit(db, name, "login_failed", False, ip, ua, "账号已停用")
        db.commit()
        raise UnauthorizedError("账号已被停用，请联系管理员")

    locked_until = sec.parse_iso(row.locked_until)
    if locked_until and locked_until > now:
        _audit(db, name, "login_failed", False, ip, ua, "账号处于锁定期")
        db.commit()
        raise AccountLockedError(f"连续登录失败过多，账号已锁定至 {row.locked_until}")
    if locked_until:
        row.failed_attempts = 0
        row.locked_until = None

    if not sec.verify_password(password, row.password_hash):
        row.failed_attempts = (row.failed_attempts or 0) + 1
        if row.failed_attempts >= settings.auth_max_failed_attempts:
            row.locked_until = sec.iso(now + timedelta(minutes=settings.auth_lock_minutes))
            row.failed_attempts = 0
        row.updated_at = sec.iso(now)
        _audit(db, name, "login_failed", False, ip, ua, "密码错误")
        db.commit()
        raise UnauthorizedError("用户名或密码不正确")

    row.failed_attempts = 0
    row.locked_until = None
    row.last_login_at = sec.iso(now)
    row.updated_at = sec.iso(now)
    access, ttl = sec.make_access_token(row)
    raw = sec.new_refresh_token()
    repo.add_refresh(
        db, user_id=row.id, token_hash=sec.token_digest(raw),
        expires_at=sec.refresh_expires_at(), ip=(ip or "")[:64] or None,
        user_agent=(ua or "")[:500] or None, created_at=sec.iso(now),
    )
    _audit(db, name, "login_success", True, ip, ua)
    db.commit()
    return row, access, ttl, raw


# ---------------------------------------------------------------- 刷新 / 注销
def refresh(db: Session, raw: str | None,
            ip: str | None = None, ua: str | None = None) -> tuple[m.User, str, int, str]:
    if not raw:
        raise UnauthorizedError("登录已失效，请重新登录")
    row = repo.get_refresh(db, sec.token_digest(raw))
    now = sec.utcnow()
    if row is None:
        raise UnauthorizedError("登录已失效，请重新登录")

    # 已撤销令牌被再次使用：疑似令牌泄露，立即撤销该用户全部会话。
    if row.revoked_at:
        for active in repo.list_active_refresh(db, row.user_id):
            active.revoked_at = sec.iso(now)
        user = repo.get_by_id(db, row.user_id)
        _audit(db, user.username if user else "unknown", "refresh_replay_detected", False,
               ip, ua, "检测到已撤销刷新令牌重放，已撤销全部会话")
        db.commit()
        raise UnauthorizedError("登录状态异常，请重新登录")

    expires = sec.parse_iso(row.expires_at)
    if expires is None or expires <= now:
        raise UnauthorizedError("登录已过期，请重新登录")

    user = repo.get_by_id(db, row.user_id)
    if user is None or not user.is_active:
        raise UnauthorizedError("账号不可用，请联系管理员")

    new_raw = sec.new_refresh_token()
    new_hash = sec.token_digest(new_raw)
    row.revoked_at = sec.iso(now)
    row.replaced_by_hash = new_hash
    repo.add_refresh(
        db, user_id=user.id, token_hash=new_hash, expires_at=sec.refresh_expires_at(),
        ip=(ip or "")[:64] or None, user_agent=(ua or "")[:500] or None,
        created_at=sec.iso(now),
    )
    access, ttl = sec.make_access_token(user)
    _audit(db, user.username, "token_refresh", True, ip, ua)
    db.commit()
    return user, access, ttl, new_raw


def logout(db: Session, raw: str | None,
           ip: str | None = None, ua: str | None = None, username: str = "") -> None:
    if raw:
        row = repo.get_refresh(db, sec.token_digest(raw))
        if row and not row.revoked_at:
            row.revoked_at = sec.iso()
    _audit(db, username or "unknown", "logout", True, ip, ua)
    db.commit()


# ---------------------------------------------------------------- 改密 / 账号管理
def change_password(db: Session, user: m.User, old_password: str, new_password: str) -> None:
    if not sec.verify_password(old_password, user.password_hash):
        raise BadRequestError("原密码不正确")
    sec.validate_password(new_password)
    user.password_hash = sec.hash_password(new_password)
    user.updated_at = sec.iso()
    for active in repo.list_active_refresh(db, user.id):
        active.revoked_at = sec.iso()
    _audit(db, user.username, "password_changed", True, detail="改密后已撤销全部刷新令牌")
    db.commit()


def create_user(db: Session, payload) -> m.User:
    username = sec.validate_username(payload.username)
    sec.validate_password(payload.password)
    role = _role(payload.role)
    if repo.get_by_username(db, username):
        raise ConflictError(f"用户名 {username} 已存在")
    now = sec.iso()
    row = repo.add_user(
        db, username=username, password_hash=sec.hash_password(payload.password),
        display_name=(payload.display_name or "").strip(), role=role, is_active=1,
        failed_attempts=0, created_at=now, updated_at=now,
    )
    _audit(db, username, "user_created", True, detail=f"角色 {role}")
    db.commit()
    return row


def update_user(db: Session, user_id: int, payload, operator: str) -> m.User:
    row = repo.get_by_id(db, user_id)
    if row is None:
        raise NotFoundError("用户不存在")
    if payload.display_name is not None:
        row.display_name = payload.display_name.strip()
    if payload.role is not None:
        row.role = _role(payload.role)
    if payload.is_active is not None:
        row.is_active = 1 if payload.is_active else 0
    if payload.password:
        sec.validate_password(payload.password)
        row.password_hash = sec.hash_password(payload.password)
        for active in repo.list_active_refresh(db, row.id):
            active.revoked_at = sec.iso()
    row.updated_at = sec.iso()
    _audit(db, operator, "user_updated", True, detail=f"目标用户 {row.username}")
    db.commit()
    return row
