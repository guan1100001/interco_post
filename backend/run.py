"""服务启动入口：python backend/run.py"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import uvicorn  # noqa: E402

from backend.app.core.config import settings  # noqa: E402


if __name__ == "__main__":
    print(f"服务地址: http://{settings.host}:{settings.port}  (API文档 /docs)")
    print(f"数据库: {settings.db_path}")
    print(f"OCR模式: {settings.ocr_mode}  |  SAP模式: {settings.sap_mode}")
    uvicorn.run(
        "backend.app.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        access_log=False,
        log_config=None,  # 沿用应用内结构化 JSON 日志
    )
