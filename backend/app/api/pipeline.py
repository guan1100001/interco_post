"""流水线会话控制器：只做请求解析与响应格式化，业务全部在服务层。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status

from backend.app.api.deps import get_pipeline_service
from backend.app.core.config import settings
from backend.app.schemas.pipeline import (
    MessageIn, SessionBriefOut, SessionCreateIn, SessionOut,
)
from backend.app.services.pipeline_service import PipelineService

router = APIRouter(prefix=f"{settings.api_prefix}/pipeline", tags=["pipeline"])


@router.post("/sessions", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
def create_session(body: SessionCreateIn, svc: PipelineService = Depends(get_pipeline_service)):
    return svc.create(body.file_path, body.ocr_mode)


@router.get("/sessions", response_model=list[SessionBriefOut])
def list_sessions(
    limit: int = Query(20, ge=1, le=100),
    svc: PipelineService = Depends(get_pipeline_service),
):
    return svc.list_recent(limit)


@router.get("/sessions/{session_id}", response_model=SessionOut)
def get_session(session_id: str, svc: PipelineService = Depends(get_pipeline_service)):
    return svc.get(session_id)


@router.post("/sessions/{session_id}/messages", response_model=SessionOut)
def send_message(
    session_id: str, body: MessageIn,
    svc: PipelineService = Depends(get_pipeline_service),
):
    return svc.handle(session_id, body.action, body.text, body.patch)
