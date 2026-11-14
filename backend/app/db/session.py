"""数据库引擎 / 会话工厂 / 依赖注入 / 幂等迁移入口。"""
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from backend.app.core.config import settings
from backend.app.db.base import Base

# SQLite + FastAPI 线程池：check_same_thread=False；单进程写，由业务层保证短事务。
engine = create_engine(
    settings.db_url,
    connect_args={"check_same_thread": False, "timeout": 15},
    pool_pre_ping=True,
    echo=False,
)


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_connection, _connection_record):
    """WAL 让 ORM 连接与遗留 sqlite3 连接的读写可以并发，避免 database is locked。"""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.fetchone()
    cursor.execute("PRAGMA busy_timeout=15000")
    cursor.close()

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_database() -> None:
    """幂等的增量迁移：只新建缺失的表，不修改既有表结构与数据。"""
    import backend.app.db.models  # noqa: F401  确保模型已注册

    Base.metadata.create_all(bind=engine, checkfirst=True)
