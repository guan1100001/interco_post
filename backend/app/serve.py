"""开发服务器启动器：可在任意工作目录运行（含 backend/app 目录）。

背景
----
项目内所有模块统一使用绝对导入 ``backend.app.*``，因此 Python 的导入根必须是
项目根目录 ``D:/workbuddy/qddev``，uvicorn 的应用路径也必须写成
``backend.app.main:app``。在 ``backend/app`` 目录下直接执行
``uvicorn app.main:app`` 会报 ``No module named 'app'``：
  1. 该目录下并不存在名为 app 的子包（main.py 就在当前目录里）；
  2. 即便改成 ``main:app``，main.py 内部的 ``from backend.app.api import ...``
     仍会因为项目根不在 sys.path 而失败。

本脚本自动把项目根注入 sys.path 并把 cwd 切到项目根，因此在任何目录执行都可以。

用法
----
    python serve.py                              # 默认 127.0.0.1:8000 + 热重载
    python serve.py --host 0.0.0.0 --port 8000  # 允许局域网访问
    python serve.py --no-reload                  # 关闭热重载（单进程）

等价的原生命令（必须先 cd 到项目根目录）::

    uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# backend/app/serve.py -> parents[0]=app, [1]=backend, [2]=项目根
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)  # .env、相对路径与 --reload 监视范围都以项目根为基准

import uvicorn  # noqa: E402

from backend.app.core.config import settings  # noqa: E402

APP = "backend.app.main:app"


def main() -> None:
    parser = argparse.ArgumentParser(description="启动关联交易发票识别及SAP过账控制台")
    parser.add_argument("--host", default=settings.host, help=f"监听地址（默认 {settings.host}）")
    parser.add_argument("--port", type=int, default=settings.port, help=f"端口（默认 {settings.port}）")
    parser.add_argument("--no-reload", action="store_true", help="关闭热重载")
    parser.add_argument("--log-level", default=settings.log_level.lower(), help="日志级别")
    args = parser.parse_args()

    reload_enabled = not args.no_reload
    print(f"项目根目录 : {PROJECT_ROOT}")
    print(f"应用路径   : {APP}")
    print(f"服务地址   : http://{args.host}:{args.port}   (API 文档 /docs)")
    print(f"数据库     : {settings.db_path}")
    print(f"OCR / SAP  : {settings.ocr_mode} / {settings.sap_mode}   热重载: {reload_enabled}")

    kwargs: dict = {
        "host": args.host,
        "port": args.port,
        "reload": reload_enabled,
        "log_level": args.log_level,
        "access_log": False,
        "log_config": None,  # 沿用应用内结构化 JSON 日志
    }
    if reload_enabled:
        kwargs["reload_dirs"] = [str(PROJECT_ROOT / "backend")]
    uvicorn.run(APP, **kwargs)


if __name__ == "__main__":
    main()
