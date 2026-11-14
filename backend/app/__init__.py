"""应用包初始化：补齐导入根，兼容 ``uvicorn app.main:app`` 启动方式。

项目内模块统一使用绝对导入 ``backend.app.*``（见 main.py），其导入根是
**项目根目录**。但按要求执行 ``uvicorn app.main:app`` 时，Python 首先加载的
是名为 ``app`` 的包；此时项目根未必在 sys.path 中，main.py 里的
``from backend.app.api import ...`` 会抛 ``No module named 'backend'``。

因此在包被导入的第一时间，把「项目根」与「backend 目录」补进 sys.path：
  * 项目根     -> 使 ``backend.app.*`` 可解析；
  * backend 目录 -> 使 ``app.*`` 在任意启动目录下都能指向本包。
该逻辑幂等，不影响从项目根以 ``backend.app.main:app`` 启动的标准方式。
"""
from __future__ import annotations

import sys
from pathlib import Path

_APP_DIR = Path(__file__).resolve().parent          # .../backend/app
_BACKEND_DIR = _APP_DIR.parent                     # .../backend
_PROJECT_ROOT = _BACKEND_DIR.parent                # 项目根

for _path in (str(_PROJECT_ROOT), str(_BACKEND_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)
