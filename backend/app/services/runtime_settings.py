"""运行时可变设置：过账模式可由界面切换，进程内立即生效。

设计取舍：
- 不写数据库、不改 .env：真实 SAP 过账不可逆，服务重启后自动回落到 .env 的
  SAP_MODE 默认值（通常是 mock），避免「上次有人切到 live」在无人知晓的情况下
  于重启后继续对真实系统过账。
- 单 uvicorn 进程内用线程锁保护即可，不引入额外缓存或消息中间件。
- 服务层不依赖任何 HTTP 类型；控制器负责鉴权、审计与响应格式化。
"""
from __future__ import annotations

import threading

from backend.app.core.config import SAP_MODES, sap_live_missing, settings
from backend.app.core.errors import BadRequestError
from backend.app.core.logging import get_logger

log = get_logger(__name__)

_lock = threading.RLock()
_sap_mode: str = settings.sap_mode


def get_sap_mode() -> str:
    """当前生效的过账模式；过账链路必须调用这里，而不是只读 settings.sap_mode。"""
    with _lock:
        return _sap_mode


def sap_live_ready() -> bool:
    """live 是否具备真实 SAP 连接凭据（只判断存在性，不读取或输出凭据内容）。"""
    return not sap_live_missing()


def missing_sap_config() -> list[str]:
    return sap_live_missing()


def is_runtime_override() -> bool:
    with _lock:
        return _sap_mode != settings.sap_mode


def set_sap_mode(mode: str) -> str:
    """切换过账模式。允许切到 live，但配置缺失由控制器/前端明确告警，
    绝不在缺凭据时静默降级成模拟过账（legacy_adapter.post 的 live 分支会直接报错）。
    """
    global _sap_mode
    value = (mode or "").strip().lower()
    if value not in SAP_MODES:
        raise BadRequestError(
            f"过账模式不合法：{mode!r}；可选值：{', '.join(SAP_MODES)}"
        )
    with _lock:
        previous = _sap_mode
        _sap_mode = value
    log.info("sap_mode_changed", extra={"extra": {
        "previous": previous, "current": value, "live_ready": sap_live_ready(),
    }})
    return value
