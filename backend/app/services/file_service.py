"""本地发票文件路径服务：边界校验 + 目录浏览 / 文件夹清单扫描（只读）。

支持两类输入：
- 单个发票文件：直接对这一张执行识别流程；
- 文件夹（或某个发票文件所在的文件夹）：只扫描出可识别发票清单，
  由人工在清单里一张一张选择，每一步确认都由用户点击触发，没有后台批量任务。
"""
from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select

from backend.app.core.config import settings
from backend.app.core.errors import BadRequestError
from backend.app.db import models as m
from backend.app.db.session import SessionLocal
from backend.app.schemas.folder import FolderFileOut, FolderScanOut
from backend.app.schemas.pipeline import FileBrowseOut, FileItemOut, PathCheckOut
from backend.app.services import legacy_adapter as legacy


def resolve(file_path: str) -> Path:
    raw = (file_path or "").strip().strip('"').strip("'")
    if not raw:
        raise BadRequestError("文件路径不能为空")
    path = Path(raw)
    if not path.is_absolute():
        path = settings.project_root / path
    return path.resolve()


def within_allowed_roots(path: Path) -> bool:
    if not settings.invoice_roots:
        return True  # 默认不限制（本地内网工具，只读访问）
    return any(path.is_relative_to(root) for root in settings.invoice_roots)


def is_supported(path: Path) -> bool:
    return path.suffix.lower() in legacy.supported_exts()


def check(file_path: str) -> PathCheckOut:
    try:
        path = resolve(file_path)
    except BadRequestError as exc:
        return PathCheckOut(file_path=file_path, exists=False, supported=False, message=exc.message)
    if path.is_dir():
        count = sum(1 for p in path.iterdir() if p.is_file() and is_supported(p))
        return PathCheckOut(
            file_path=str(path), exists=True, supported=False, kind="directory",
            message=f"这是一个文件夹，包含 {count} 个可识别发票文件，请扫描清单后逐张选择处理",
        )
    if not path.exists() or not path.is_file():
        return PathCheckOut(file_path=str(path), exists=False, supported=False,
                            message="文件不存在或不是普通文件")
    if not within_allowed_roots(path):
        return PathCheckOut(file_path=str(path), exists=True, supported=False,
                            message="路径不在 INVOICE_ROOTS 允许范围内")
    supported = is_supported(path)
    return PathCheckOut(
        file_path=str(path), exists=True, supported=supported,
        size=path.stat().st_size,
        message="可识别的发票文件" if supported else f"不支持的扩展名 {path.suffix}",
    )


def require_file(file_path: str) -> tuple[str, int]:
    result = check(file_path)
    if not result.exists:
        raise BadRequestError(f"文件不存在或不是普通文件: {result.file_path}")
    if not result.supported:
        raise BadRequestError(f"不支持的文件类型: {result.message}（支持 {', '.join(legacy.supported_exts())}）")
    return result.file_path, result.size


def browse(directory: str = "", limit: int = 200) -> FileBrowseOut:
    if directory.strip():
        try:
            root = resolve(directory)
        except BadRequestError as exc:
            raise BadRequestError(exc.message) from exc
    else:
        root = settings.project_root / "invoices"
        if not root.is_dir():
            root = settings.project_root
    if not root.is_dir():
        raise BadRequestError(f"目录不存在: {root}")
    if not within_allowed_roots(root):
        raise BadRequestError("目录不在 INVOICE_ROOTS 允许范围内")

    files: list[FileItemOut] = []
    truncated = False
    for entry in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if not entry.is_file():
            continue
        if len(files) >= limit:
            truncated = True
            break
        files.append(FileItemOut(
            name=entry.name, path=str(entry), size=entry.stat().st_size,
            supported=is_supported(entry),
        ))
    return FileBrowseOut(directory=str(root), files=files, truncated=truncated)


def scan_folder(directory: str = "", recursive: bool = False, limit: int = 500) -> FolderScanOut:
    """扫描文件夹里的可识别发票清单，并附带每张的已过账状态与最近处理记录。

    传入具体文件时自动改用其父目录，用户直接粘贴发票全路径也能拿到同目录清单。
    本函数是纯读操作：不创建会话、不启动任务，处理一律由用户逐张点选后开始。
    """
    raw = (directory or "").strip().strip('"').strip("'")
    if raw:
        root = resolve(raw)
    else:
        root = settings.project_root / "invoices"
        if not root.is_dir():
            root = settings.project_root

    source_kind = "directory"
    if root.is_file():
        source_kind = "file"
        root = root.parent
    if not root.is_dir():
        raise BadRequestError(f"目录不存在: {root}")
    if not within_allowed_roots(root):
        raise BadRequestError("目录不在 INVOICE_ROOTS 允许范围内")

    candidates: list[Path] = []
    ignored = 0
    truncated = False
    for entry in sorted(root.rglob("*") if recursive else root.iterdir(),
                        key=lambda p: str(p).lower()):
        if not entry.is_file():
            continue
        if not is_supported(entry):
            ignored += 1
            continue
        if len(candidates) >= limit:
            truncated = True
            break
        candidates.append(entry.resolve())

    # 单次 IN 查询取回已入库状态与最近会话，避免在循环里 N+1；同一文件多条记录取最新一条。
    paths = [str(p) for p in candidates]
    latest: dict[str, tuple[str, str]] = {}
    sessions: dict[str, tuple[str, str, str, str | None, bool]] = {}
    if paths:
        with SessionLocal() as db:
            rows = db.execute(
                select(m.Invoice.source_file, m.Invoice.status, m.Invoice.invoice_number)
                .where(m.Invoice.source_file.in_(paths))
                .order_by(m.Invoice.id.desc())
            ).all()
            for src, status, number in rows:
                if src and src not in latest:
                    latest[src] = (status or "", number or "")

            srows = db.execute(
                select(
                    m.PipelineSession.file_path, m.PipelineSession.id,
                    m.PipelineSession.status, m.PipelineSession.invoice_number,
                    m.PipelineSession.context_json,
                )
                .where(m.PipelineSession.file_path.in_(paths))
                .order_by(m.PipelineSession.created_at.desc())
            ).all()
            for src, sid, s_status, s_number, ctx_json in srows:
                if src and src not in sessions:
                    doc_no, ack_pending = None, False
                    try:
                        ctx = json.loads(ctx_json or "{}")
                        doc_no = (ctx.get("posting") or {}).get("doc_no")
                        ack_pending = bool(ctx.get("acknowledge_pending"))
                    except (json.JSONDecodeError, TypeError, AttributeError):
                        pass
                    sessions[src] = (sid, s_status or "", s_number or "", doc_no, ack_pending)

    files: list[FolderFileOut] = []
    for path in candidates:
        key = str(path)
        status, number = latest.get(key, ("", ""))
        sid, s_status, s_number, doc_no, ack_pending = sessions.get(
            key, (None, "", "", None, False)
        )
        files.append(FolderFileOut(
            name=path.name, path=key, size=path.stat().st_size,
            supported=True, processed=status == "POSTED" if status else False,
            invoice_number=number or s_number or None, status=status or None,
            last_session_id=sid, last_status=s_status or None, doc_no=doc_no,
            acknowledge_pending=ack_pending,
        ))

    posted = sum(1 for f in files if f.processed)
    parts = [
        f"共发现 {len(files)} 个可识别发票文件",
        f"其中 {posted} 个已过账",
        f"{len(files) - posted} 个可逐张选择处理",
    ]
    if ignored:
        parts.append(f"忽略 {ignored} 个非发票文件")
    if truncated:
        parts.append(f"已达 {limit} 个上限，其余未列出")
    return FolderScanOut(
        directory=str(root), source_kind=source_kind, files=files,
        total=len(files), ignored=ignored, truncated=truncated,
        message="，".join(parts) + "。",
    )
