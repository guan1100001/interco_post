"""文件夹发票清单的 Pydantic 边界模型（只读）。

用途：用户选择文件夹后拿到一张「逐张处理清单」，由人工点选某一张开始识别；
清单里同时带回每张的最近会话与凭证号，方便刷新页面后继续处理、回看记录。
系统不提供批量自动过账，所有确认动作都由前端人工触发。
"""
from __future__ import annotations

from backend.app.schemas.common import StrictModel


class FolderFileOut(StrictModel):
    name: str
    path: str
    size: int
    supported: bool = True
    processed: bool = False          # 该源文件是否已有 POSTED 发票记录
    invoice_number: str | None = None
    status: str | None = None        # invoices.status（POSTED / DRAFT / CANCELLED ...）
    last_session_id: str | None = None   # 最近一次流水线会话，用于「查看记录」
    last_status: str | None = None       # 该会话状态（WAITING / COMPLETED / FAILED / ACKNOWLEDGED ...）
    doc_no: str | None = None            # 该会话过账生成的凭证号
    acknowledge_pending: bool = False    # 业务规则中断、等待人工点「确认」正常结束


class FolderScanOut(StrictModel):
    directory: str
    source_kind: str = "directory"   # directory | file（传文件时自动取其父目录）
    files: list[FolderFileOut]
    total: int
    ignored: int = 0                 # 被忽略的非发票文件数
    truncated: bool = False
    message: str = ""
