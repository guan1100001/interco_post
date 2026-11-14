"""可回滚的增量迁移。

用法: python -m backend.app.db.migrate up|down
既有业务表由 invoice_ocr.init_db() 的 SCHEMA_SQL 负责，不在此重复定义；
本模块只负责全栈应用新增的表（会话 + 认证/授权）。
"""
from __future__ import annotations

import sys

from sqlalchemy import text

from backend.app.db.session import engine, init_database

APP_TABLES = (
    "pipeline_sessions",
    "users",
    "refresh_tokens",
    "auth_audit_log",
)


def upgrade() -> None:
    init_database()  # Base.metadata.create_all(checkfirst=True)，幂等
    with engine.begin() as conn:
        rows = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table'")
        ).fetchall()
    existing = {row[0] for row in rows}
    for table in APP_TABLES:
        print(f"upgrade: {table:20s} {'已存在' if table in existing else '已创建'}")
    print("提示：首次启动服务会自动创建管理员账号（见 .env / .initial_admin_password）")


def downgrade() -> None:
    print("警告：回滚会删除账号、刷新令牌、认证审计与流程会话数据，且不可恢复。")
    with engine.begin() as conn:
        for table in APP_TABLES:
            conn.execute(text(f"DROP TABLE IF EXISTS {table}"))
            print(f"downgrade: {table} 已删除")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "up"
    if cmd == "up":
        upgrade()
    elif cmd == "down":
        downgrade()
    else:
        raise SystemExit("用法: python -m backend.app.db.migrate up|down")
