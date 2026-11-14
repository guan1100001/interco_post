"""发票文件路径控制器：单文件校验、目录浏览、文件夹清单扫描（只读）。

扫描只返回清单与已有处理状态，不启动任何后台任务；逐张识别与过账由人工在前端触发。
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from backend.app.core.config import settings
from backend.app.schemas.folder import FolderScanOut
from backend.app.schemas.pipeline import FileBrowseOut, PathCheckIn, PathCheckOut
from backend.app.services import file_service

router = APIRouter(prefix=f"{settings.api_prefix}/files", tags=["files"])


@router.post("/check", response_model=PathCheckOut)
def check_path(body: PathCheckIn):
    return file_service.check(body.file_path)


@router.get("/browse", response_model=FileBrowseOut)
def browse(directory: str = "", limit: int = Query(200, ge=1, le=500)):
    return file_service.browse(directory, limit)


@router.get("/scan", response_model=FolderScanOut)
def scan_folder(
    directory: str = "",
    recursive: bool = Query(False, description="是否包含子目录"),
    limit: int = Query(500, ge=1, le=1000),
):
    """扫描文件夹里的发票清单（只读）。传文件路径时自动扫描其所在文件夹。"""
    return file_service.scan_folder(directory, recursive=recursive, limit=limit)
