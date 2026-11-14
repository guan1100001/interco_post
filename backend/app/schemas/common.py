"""通用响应结构。"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: Any = None
    request_id: str = "-"


class ErrorResponse(BaseModel):
    error: ErrorDetail


class HealthOut(BaseModel):
    status: str
    service: str = "interco-invoice-api"
    version: str = "1.0.0"
    db: str


class MetaOut(BaseModel):
    ocr_mode: str
    sap_mode: str
    ocr_url: str
    db_path: str
    api_prefix: str
    invoice_roots: list[str] = Field(default_factory=list)
    supported_exts: list[str] = Field(default_factory=list)
    # 过账模式运行时信息：界面据此渲染可切换下拉与 live 风险提示
    sap_mode_options: list[str] = Field(default_factory=list)
    sap_live_ready: bool = False
    sap_missing_config: list[str] = Field(default_factory=list)
    sap_mode_runtime_override: bool = False
