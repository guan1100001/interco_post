"""密码哈希与 JWT 令牌原语。

- 密码：PBKDF2-SHA256 + 每账号独立随机盐，摘要格式为
  ``pbkdf2_sha256$迭代数$salt_b64$digest_b64``；比较使用 hmac.compare_digest 防时序侧信道。
- 令牌：PyJWT HS256 签名，带 iss/aud/iat/exp/jti/typ，访问令牌无状态、短时效。
- 刷新令牌：随机串只以 SHA-256 摘要入库，明文仅经 HttpOnly Cookie 传给浏览器。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt

from backend.app.core.config import settings
from backend.app.core.errors import BadRequestError, UnauthorizedError

PBKDF2_ITERATIONS = 210_000
_SALT_BYTES = 16
_ALGORITHM = "HS256"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None = None) -> str:
    return (dt or utcnow()).astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def validate_username(username: str) -> str:
    value = (username or "").strip()
    if not (3 <= len(value) <= 64):
        raise BadRequestError("用户名长度需为 3–64 个字符")
    if not all(ch.isalnum() or ch in "._-@" for ch in value):
        raise BadRequestError("用户名只能包含字母、数字和 . _ - @")
    return value.lower()  # 统一小写，避免大小写导致登录查不到账号


def validate_password(password: str) -> str:
    value = password or ""
    if len(value) < 8 or len(value) > 128:
        raise BadRequestError("密码长度需为 8–128 个字符")
    return value


def hash_password(password: str) -> str:
    validate_password(password)
    salt = os.urandom(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return "$".join((
        "pbkdf2_sha256", str(PBKDF2_ITERATIONS),
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    ))


def verify_password(password: str, encoded: str | None) -> bool:
    """无论密码错误还是摘要格式异常，都返回 False，不泄漏账号是否存在。"""
    if not encoded or not password:
        return False
    try:
        scheme, iterations, salt_b64, digest_b64 = encoded.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, int(iterations)
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def make_access_token(user: Any) -> tuple[str, int]:
    """返回 (JWT, 有效期秒数)。"""
    now = utcnow()
    ttl = settings.auth_access_ttl_min * 60
    payload = {
        "sub": str(user.id),
        "username": user.username,
        "role": user.role,
        "iss": settings.auth_issuer,
        "aud": settings.auth_audience,
        "iat": now,
        "exp": now + timedelta(seconds=ttl),
        "jti": uuid.uuid4().hex,
        "typ": "access",
    }
    return jwt.encode(payload, settings.auth_secret_key, algorithm=_ALGORITHM), ttl


def decode_access_token(token: str) -> dict:
    try:
        claims = jwt.decode(
            token,
            settings.auth_secret_key,
            algorithms=[_ALGORITHM],
            audience=settings.auth_audience,
            issuer=settings.auth_issuer,
            options={"require": ["exp", "iat", "sub", "iss", "aud", "typ"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise UnauthorizedError("登录已过期，请重新登录") from exc
    except jwt.InvalidTokenError as exc:
        raise UnauthorizedError("登录凭证无效，请重新登录") from exc
    if claims.get("typ") != "access":
        raise UnauthorizedError("登录凭证类型不正确，请重新登录")
    return claims


def new_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def refresh_expires_at() -> str:
    return iso(utcnow() + timedelta(days=settings.auth_refresh_ttl_days))


def token_digest(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
