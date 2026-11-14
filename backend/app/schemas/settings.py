"""运行时设置接口的输入输出模型（请求边界处的类型校验）。"""
from __future__ import annotations

from pydantic import BaseModel, Field


class SapModeIn(BaseModel):
    # 枚举合法性由服务层校验，便于返回带可选值的中文 400 提示，而不是 FastAPI 的裸 422。
    sap_mode: str = Field(min_length=1, max_length=16)


class SapModeOut(BaseModel):
    sap_mode: str
    sap_mode_options: list[str] = Field(default_factory=list)
    sap_live_ready: bool = False
    sap_missing_config: list[str] = Field(default_factory=list)
    sap_mode_runtime_override: bool = False
    previous_sap_mode: str | None = None
    warning: str = ""
