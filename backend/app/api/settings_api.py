"""运行时设置控制器：只做请求解析、鉴权、审计与响应格式化，业务在 runtime_settings 服务层。

PUT /api/v1/settings/sap-mode 受全局 guard 保护：viewer 的写请求会被 403 拦截。
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from backend.app.api.deps import current_user
from backend.app.api.guard import AuthContext
from backend.app.core.config import SAP_MODES, settings
from backend.app.db.session import get_db
from backend.app.repositories import user_repo as repo
from backend.app.schemas.settings import SapModeIn, SapModeOut
from backend.app.services import runtime_settings as runtime

router = APIRouter(prefix=f"{settings.api_prefix}/settings", tags=["settings"])


def _warning(mode: str) -> str:
    if mode != "live":
        return ""
    missing = runtime.missing_sap_config()
    if not missing:
        return "已切换到 live：后续「确认过账」将调用真实 SAP 接口，请务必核对载荷。"
    return (
        "已切换到 live（真实 SAP），但当前环境缺少连接配置："
        + "、".join(missing)
        + "。此时点「确认过账」会直接报错，不会生成模拟凭证、也不会连到真实 SAP；"
          "请在 .env 补齐这 4 项并重启后端后再过账。"
    )


def _snapshot(previous: str | None = None) -> SapModeOut:
    mode = runtime.get_sap_mode()
    return SapModeOut(
        sap_mode=mode,
        sap_mode_options=list(SAP_MODES),
        sap_live_ready=runtime.sap_live_ready(),
        sap_missing_config=runtime.missing_sap_config(),
        sap_mode_runtime_override=runtime.is_runtime_override(),
        previous_sap_mode=previous,
        warning=_warning(mode),
    )


@router.get("", response_model=SapModeOut)
def get_runtime_settings():
    """当前生效的过账模式（与 /meta 中的 sap_mode 一致）。"""
    return _snapshot()


@router.put("/sap-mode", response_model=SapModeOut)
def update_sap_mode(
    body: SapModeIn,
    request: Request,
    ctx: AuthContext = Depends(current_user),
    db: Session = Depends(get_db),
):
    previous = runtime.get_sap_mode()
    runtime.set_sap_mode(body.sap_mode)
    repo.add_audit(
        db,
        username=ctx.username,
        action="sap_mode_changed",
        success=1,
        ip=request.client.host if request.client else None,
        user_agent=(request.headers.get("user-agent") or "")[:200] or None,
        detail=f"{previous} -> {runtime.get_sap_mode()}（运行时生效，重启回落 .env）",
        created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )
    db.commit()
    return _snapshot(previous)
