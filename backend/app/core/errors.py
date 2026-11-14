"""类型化错误体系 + 全局处理器：客户端只看到规范化的错误结构。"""
from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from backend.app.core.logging import get_logger, get_request_id

log = get_logger(__name__)


class AppError(Exception):
    status_code = 500
    code = "INTERNAL_ERROR"
    message = "服务内部错误"

    def __init__(self, message: str | None = None, details: Any = None):
        self.message = message or self.message
        self.details = details
        super().__init__(self.message)

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "details": self.details,
            "request_id": get_request_id(),
        }


class BadRequestError(AppError):
    status_code, code, message = 400, "BAD_REQUEST", "请求参数不合法"


class NotFoundError(AppError):
    status_code, code, message = 404, "NOT_FOUND", "资源不存在"


class UnauthorizedError(AppError):
    status_code, code, message = 401, "UNAUTHORIZED", "未登录或登录已失效，请重新登录"


class ForbiddenError(AppError):
    status_code, code, message = 403, "FORBIDDEN", "当前账号没有执行此操作的权限"


class AccountLockedError(AppError):
    status_code, code, message = 423, "ACCOUNT_LOCKED", "连续登录失败次数过多，账号已临时锁定"


class ConflictError(AppError):
    status_code, code, message = 409, "STATE_CONFLICT", "当前状态不允许该操作"


class UpstreamError(AppError):
    status_code, code, message = 502, "UPSTREAM_ERROR", "上游服务调用失败"


class InternalError(AppError):
    status_code, code, message = 500, "INTERNAL_ERROR", "服务内部错误"


async def app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
    log.warning("app_error", extra={"extra": {"code": exc.code, "message": exc.message}})
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.to_dict()},
    )


async def unhandled_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    # 不向客户端泄漏堆栈与内部细节，只记录到结构化日志。
    log.exception("unhandled_exception", extra={"extra": {"type": type(exc).__name__}})
    if isinstance(exc, ValidationError):
        # 输出模型与库里历史数据不匹配时，给出可定位的中文提示（只带字段名，不带数据值），
        # 避免用户只看到笼统的「服务内部错误」而不知道是哪张发票、哪个字段有问题。
        fields = [
            ".".join(str(p) for p in err.get("loc", ())) or "unknown"
            for err in exc.errors()[:5]
        ]
        detail = InternalError().to_dict()
        detail["code"] = "DATA_VALIDATION_ERROR"
        detail["message"] = "发票数据格式异常，无法展示（字段：" + "、".join(fields) + "）"
        return JSONResponse(status_code=500, content={"error": detail})
    return JSONResponse(
        status_code=500,
        content={"error": InternalError().to_dict()},
    )
