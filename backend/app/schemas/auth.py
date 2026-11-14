"""认证相关请求/响应模型：所有输入在 HTTP 边界处校验。"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from backend.app.schemas.common import StrictModel

ROLES = ("admin", "operator", "viewer")


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class UserOut(StrictModel):
    id: int
    username: str
    display_name: str = ""
    role: str
    is_active: bool
    last_login_at: str | None = None
    created_at: str | None = None


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int
    user: UserOut


class RefreshOut(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int


class PasswordChangeIn(BaseModel):
    old_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class UserCreateIn(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(default="", max_length=128)
    role: str = Field(default="operator")


class UserUpdateIn(BaseModel):
    display_name: str | None = Field(default=None, max_length=128)
    role: str | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=8, max_length=128)


class OkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    ok: bool = True
    message: str = "操作成功"
