"""流水线会话 / 对话消息 / 文件路径相关 Pydantic 模型。"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.app.schemas.common import StrictModel

ActionName = Literal[
    "confirm", "edit", "skip", "proceed", "approve", "reject", "restart", "help",
    "acknowledge",   # 业务规则中断后，人工点「确认」正常结束本张
]
MessageKind = Literal[
    "text", "ocr_result", "quality", "scenario", "reasoning",
    "approval", "result", "error", "system",
    "closed",        # 人工确认业务中断后的正常结束消息
]


class ActionOut(StrictModel):
    action: str
    label: str
    style: str = "default"  # primary | default | danger | warn


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    role: Literal["agent", "user", "system"]
    kind: str
    text: str
    ts: str
    actions: list[ActionOut] = Field(default_factory=list)
    data: dict[str, Any] | None = None


class DraftOut(BaseModel):
    """OCR 草稿：确认前可修改，确认后写入 invoices。"""
    invoice: dict[str, Any] = Field(default_factory=dict)
    items: list[dict[str, Any]] = Field(default_factory=list)


class SessionOut(StrictModel):
    id: str
    file_path: str
    invoice_number: str | None = None
    stage: str
    status: str
    draft: dict[str, Any] | None = None
    issues: list[dict[str, Any]] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)
    messages: list[MessageOut] = Field(default_factory=list)
    available_actions: list[ActionOut] = Field(default_factory=list)
    created_at: str
    updated_at: str


class SessionCreateIn(BaseModel):
    file_path: str = Field(min_length=1, max_length=1000)
    ocr_mode: Literal["auto", "live", "mock"] | None = None


class MessageIn(BaseModel):
    action: str | None = None
    text: str | None = None
    # edit 动作携带：{"invoice": {...}, "items": [{item_no, ...}]}
    patch: dict[str, Any] | None = None


class PathCheckIn(BaseModel):
    file_path: str = Field(min_length=1, max_length=1000)


class FileItemOut(StrictModel):
    name: str
    path: str
    size: int
    supported: bool


class FileBrowseOut(StrictModel):
    directory: str
    files: list[FileItemOut]
    truncated: bool = False


class PathCheckOut(StrictModel):
    file_path: str
    exists: bool
    supported: bool
    size: int = 0
    message: str
    kind: str = "file"  # file | directory（目录用于扫描逐张处理清单）


class SessionBriefOut(StrictModel):
    id: str
    file_path: str
    invoice_number: str | None = None
    stage: str
    status: str
    created_at: str
    updated_at: str
