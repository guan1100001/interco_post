"""兼容入口：允许在 ``backend/app`` 目录下直接执行 ``uvicorn app.main:app``。

该目录本身就是应用包（main.py 在下一层），cwd 里并没有名为 ``app`` 的子包，
所以原命令会报 ``No module named 'app'``。这里补一个薄转发包，把导入桥接到
真正的 ``backend.app.main``，业务代码一行都不用改。
"""
from __future__ import annotations

import sys
from pathlib import Path

# backend/app/app/__init__.py -> parents[3] = 项目根
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
for _path in (str(_PROJECT_ROOT), str(_PROJECT_ROOT / "backend")):
    if _path not in sys.path:
        sys.path.insert(0, _path)
