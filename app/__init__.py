"""兼容入口：允许在**项目根目录**执行 ``uvicorn app.main:app``。

项目根下只有 backend/ 与 frontend/，没有名为 app 的包；此薄转发包让原命令
在根目录同样可用，最终仍加载同一个 ``backend.app.main:app`` 应用对象。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
