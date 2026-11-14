"""健康检查 / 就绪检查 / 运行元信息。"""
from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from backend.app.core.config import settings
from backend.app.core.errors import UpstreamError
from backend.app.db.session import engine
from backend.app.schemas.common import HealthOut, MetaOut
from backend.app.services import legacy_adapter as legacy, runtime_settings as runtime
from backend.app.core.config import SAP_MODES

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthOut)
def health():
    return HealthOut(status="ok", db="connected" if settings.db_path.exists() else "missing")


@router.get("/ready")
def ready():
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        raise UpstreamError(f"数据库不可用: {exc}") from exc
    return {"status": "ready", "db": str(settings.db_path)}


@router.get(f"{settings.api_prefix}/meta", response_model=MetaOut, tags=["system"])
def meta():
    return MetaOut(
        ocr_mode=settings.ocr_mode, sap_mode=runtime.get_sap_mode(),
        ocr_url=settings.ocr_url, db_path=str(settings.db_path),
        api_prefix=settings.api_prefix,
        invoice_roots=[str(p) for p in settings.invoice_roots],
        supported_exts=legacy.supported_exts(),
        sap_mode_options=list(SAP_MODES),
        sap_live_ready=runtime.sap_live_ready(),
        sap_missing_config=runtime.missing_sap_config(),
        sap_mode_runtime_override=runtime.is_runtime_override(),
    )
