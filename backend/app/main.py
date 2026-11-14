"""FastAPI 应用入口：中间件顺序、全局错误、健康检查、前端静态托管、优雅停机。"""
from __future__ import annotations

import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from backend.app.api import auth, files, guard, health, invoices, pipeline, settings_api
from backend.app.core.config import settings
from backend.app.core.errors import (
    AppError, NotFoundError, app_error_handler, unhandled_error_handler,
)
from backend.app.core.logging import (
    configure_logging, get_logger, new_request_id, set_request_id,
)
from backend.app.db.session import engine, init_database
from backend.app.services import auth_service, legacy_adapter as legacy

configure_logging(settings.log_level)
log = get_logger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_database()             # 幂等创建 pipeline_sessions + 认证表
    legacy.init_legacy_db()     # 幂等创建/复用原有业务表
    auth_service.ensure_bootstrap_admin()  # 首次启动初始化唯一管理员
    log.info("service_started", extra={"extra": {
        "db": str(settings.db_path), "ocr_mode": settings.ocr_mode,
        "sap_mode": settings.sap_mode, "port": settings.port,
    }})
    try:
        yield
    finally:
        engine.dispose()
        log.info("service_stopped")


app = FastAPI(
    title="关联交易发票识别及SAP过账 · 全栈控制台",
    version="1.0.0",
    description=(
        "选择文件夹 → 一次只处理一张：OCR → 人工确认 → SC001场景匹配 → "
        "本体推理 → SHACL → 人工确认过账 → 手动处理下一张（无批量自动过账）"
    ),
    lifespan=lifespan,
)

# 显式来源白名单，生产不使用通配符
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_exception_handler(AppError, app_error_handler)
app.add_exception_handler(Exception, unhandled_error_handler)


@app.middleware("http")
async def request_context(request: Request, call_next):
    """结构化日志 + 贯穿请求的 request id。"""
    rid = request.headers.get("X-Request-ID") or new_request_id()
    set_request_id(rid)
    started = time.perf_counter()
    # 认证守卫是最内层业务检查：先生成 request id，保证 401/403 响应也带同一 id。
    response = guard.guard(request) or await call_next(request)
    response.headers["X-Request-ID"] = rid
    log.info("http_request", extra={"extra": {
        "method": request.method, "path": request.url.path,
        "status": response.status_code,
        "duration_ms": round((time.perf_counter() - started) * 1000, 1),
    }})
    return response


app.include_router(health.router)
app.include_router(settings_api.router)
app.include_router(auth.router)
app.include_router(files.router)
app.include_router(pipeline.router)
app.include_router(invoices.router)


@app.get("/{full_path:path}", include_in_schema=False)
def frontend_spa(full_path: str):
    """生产模式由后端托管 Vue3 构建产物；未构建时给出可执行的提示。"""
    if full_path.startswith("api/"):
        return JSONResponse(
            status_code=404, content={"error": NotFoundError("接口不存在").to_dict()}
        )
    dist = settings.frontend_dist
    index = dist / "index.html"
    if full_path:
        candidate = (dist / full_path).resolve()
        if candidate.is_relative_to(dist) and candidate.is_file():
            return FileResponse(candidate)
    if index.is_file():
        return FileResponse(index)
    return HTMLResponse(
        "<!doctype html><html lang='zh-CN'><meta charset='utf-8'>"
        "<body style='font-family:system-ui;padding:40px;color:#1f2937'>"
        "<h2>后端已启动，前端尚未构建</h2>"
        "<p>API 文档：<a href='/docs'>/docs</a></p>"
        "<pre>开发模式：cd frontend &amp;&amp; npm install &amp;&amp; npm run dev\n"
        "生产构建：cd frontend &amp;&amp; npm run build（然后刷新本页）</pre>"
        "</body></html>",
        status_code=200,
    )
