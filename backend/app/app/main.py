"""把 ``app.main:app`` 桥接到真实的 FastAPI 应用对象。"""
from backend.app.main import app  # noqa: F401

__all__ = ["app"]
