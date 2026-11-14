"""集中配置：全部来自环境变量，导入时集中校验并快速失败。

注意：本模块必须在任何遗留模块（invoice_ocr / ontology_reasoning / sap_posting）
被导入之前加载，因为它会统一 DB_PATH / OCR_URL，保证 SQLAlchemy 与
原有 sqlite3 连接操作的是同一个数据库文件。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[3]


# 过账模式：界面可在运行时切换（见 services/runtime_settings.py），此处定义合法枚举。
SAP_MODES: tuple[str, ...] = ("mock", "dry_run", "live")
# live 需要真实 SAP SOAP 连接凭据；缺任意一项时 post_to_sap 会直接报错，绝不回退成模拟过账。
SAP_LIVE_REQUIRED: tuple[str, ...] = (
    "WSDL_URL_ZCOA003", "SERVICE_URL_ZCOA003", "USERNAME_300", "PASSWORD_300",
)


def _dotenv_values() -> dict[str, str]:
    """实时回读项目根目录 .env；文件不存在或不可读时返回空字典。"""
    try:
        from dotenv import dotenv_values
        return {k: str(v or "").strip() for k, v in dotenv_values(PROJECT_ROOT / ".env").items()}
    except Exception:  # noqa: BLE001
        return {}


def sap_live_missing() -> list[str]:
    """返回当前缺失的 SAP live 连接配置项（只返回变量名，不含任何值）。

    sap_posting.py 是延迟导入的遗留模块，其模块级常量在首次导入时即被固定；
    这里以进程环境变量优先、再实时回读 .env，使「运行过程中补齐配置」
    无需重启也能被 live 过账感知。
    """
    file_values = _dotenv_values()
    return [
        k for k in SAP_LIVE_REQUIRED
        if not (os.getenv(k, "").strip() or file_values.get(k, ""))
    ]


def _enum(name: str, default: str, allowed: tuple[str, ...]) -> str:
    value = os.getenv(name, default).strip().lower()
    if value not in allowed:
        raise RuntimeError(f"配置项 {name}={value!r} 非法，可选值: {', '.join(allowed)}")
    return value


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError as exc:
        raise RuntimeError(f"配置项 {name} 必须是数字") from exc


def _paths(name: str) -> tuple[Path, ...]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return ()
    return tuple(Path(p.strip()).resolve() for p in raw.split(",") if p.strip())


def _int(name: str, default: int, minimum: int = 1) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise RuntimeError(f"配置项 {name} 必须是整数") from exc
    if value < minimum:
        raise RuntimeError(f"配置项 {name} 必须 >= {minimum}")
    return value


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _bypass_proxy_for(url: str) -> None:
    """把内网 OCR 主机写入 NO_PROXY，避免系统代理拦截。

    Windows/终端常设置 HTTP_PROXY=http://127.0.0.1:53824 之类的本地代理，
    requests 默认会读它，把 10.238.67.9 这类内网地址也交给代理转发；
    代理不稳定时就会报 ProxyError / ConnectionResetError(10054)。
    OCR 服务是内网直连，因此默认绕过代理，其它外网请求不受影响。
    """
    host = urlparse(url).hostname or ""
    if not host:
        return
    for key in ("NO_PROXY", "no_proxy"):
        entries = [x.strip() for x in os.environ.get(key, "").split(",") if x.strip()]
        if host in entries or "*" in entries:
            continue
        entries.append(host)
        os.environ[key] = ",".join(entries)


def _auth_secret(runtime_dir: Path) -> str:
    """JWT 签名密钥：优先环境变量；否则生成一次并持久化，避免重启后旧令牌全部失效。

    密钥文件放在运行期数据目录（DB_PATH 所在目录）：本机开发时就是项目根，
    容器部署时指向持久卷，避免只读文件系统写入失败、以及组件重建后密钥漂移
    导致所有已登录用户被强制登出。生成的文件已加入 .gitignore，不进入代码库。
    生产环境务必显式配置 AUTH_SECRET_KEY。
    """
    configured = os.getenv("AUTH_SECRET_KEY", "").strip()
    if configured:
        return configured
    import secrets

    secret_file = runtime_dir / ".auth_secret"
    if secret_file.is_file():
        existing = secret_file.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    value = secrets.token_urlsafe(48)
    secret_file.write_text(value, encoding="utf-8")
    return value


@dataclass(frozen=True)
class Settings:
    project_root: Path
    db_path: Path
    # 运行期可变文件（.auth_secret / .initial_admin_password）的存放目录，
    # 与数据库同目录，容器部署时随持久卷一起保留。
    runtime_dir: Path
    ocr_url: str
    ocr_mode: str
    ocr_timeout: float
    ocr_down_cooldown_sec: float
    ocr_use_env_proxy: bool
    sap_mode: str
    cors_origins: tuple[str, ...]
    host: str
    port: int
    frontend_dist: Path
    invoice_roots: tuple[Path, ...]
    log_level: str
    # 认证与授权
    auth_secret_key: str
    auth_issuer: str
    auth_audience: str
    auth_access_ttl_min: int
    auth_refresh_ttl_days: int
    auth_cookie_name: str
    auth_cookie_secure: bool
    auth_max_failed_attempts: int
    auth_lock_minutes: int
    bootstrap_admin_username: str
    bootstrap_admin_password: str
    api_prefix: str = "/api/v1"

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_path.as_posix()}"


def load_settings() -> Settings:
    load_dotenv(PROJECT_ROOT / ".env", override=False)

    raw_db = os.getenv("DB_PATH", "interco_ontology.db").strip()
    db_path = Path(raw_db) if Path(raw_db).is_absolute() else PROJECT_ROOT / raw_db
    db_path = db_path.resolve()
    if not db_path.parent.is_dir():
        raise RuntimeError(f"DB_PATH 父目录不存在: {db_path.parent}")
    runtime_dir = db_path.parent

    ocr_url = os.getenv("OCR_URL", "http://10.238.67.9:8014/ocr").strip()
    if not ocr_url.startswith(("http://", "https://")):
        raise RuntimeError("OCR_URL 必须是 http/https 地址")

    # 内网 OCR 默认直连，不受系统代理影响；确需代理时显式配置 OCR_USE_ENV_PROXY=true。
    ocr_use_env_proxy = _bool("OCR_USE_ENV_PROXY", False)
    if not ocr_use_env_proxy:
        _bypass_proxy_for(ocr_url)

    # .env 里直接配 SAP_MODE=live 仍走启动期强校验：缺凭据就快速失败，避免带病启动。
    # 界面上的运行时切换不改这个默认值，因此不受此处校验限制，但会把缺失项明确回报给前端。
    sap_mode = _enum("SAP_MODE", "mock", SAP_MODES)
    if sap_mode == "live":
        missing = sap_live_missing()
        if missing:
            raise RuntimeError(f"SAP_MODE=live 缺少 .env 配置: {', '.join(missing)}")

    origins = tuple(
        o.strip() for o in os.getenv(
            "CORS_ORIGINS", "http://127.0.0.1:5173,http://localhost:5173"
        ).split(",") if o.strip()
    )
    try:
        port = int(os.getenv("PORT", "8000"))
    except ValueError as exc:
        raise RuntimeError("PORT 必须是整数") from exc

    # 让遗留模块与 ORM 指向同一份 SQLite；setdefault 不会覆盖 .env 中的显式配置。
    os.environ.setdefault("DB_PATH", str(db_path))
    os.environ.setdefault("OCR_URL", ocr_url)

    return Settings(
        project_root=PROJECT_ROOT,
        db_path=db_path,
        runtime_dir=runtime_dir,
        ocr_url=ocr_url,
        ocr_mode=_enum("OCR_MODE", "auto", ("auto", "live", "mock")),
        ocr_timeout=_float("OCR_TIMEOUT", 8.0),
        ocr_down_cooldown_sec=_float("OCR_DOWN_COOLDOWN_SEC", 300.0),
        ocr_use_env_proxy=ocr_use_env_proxy,
        sap_mode=sap_mode,
        cors_origins=origins,
        host=os.getenv("HOST", "127.0.0.1"),
        port=port,
        frontend_dist=(
            Path(os.environ["FRONTEND_DIST"]).resolve()
            if os.getenv("FRONTEND_DIST", "").strip()
            else (PROJECT_ROOT / "frontend" / "dist").resolve()
        ),
        invoice_roots=_paths("INVOICE_ROOTS"),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        auth_secret_key=_auth_secret(runtime_dir),
        auth_issuer=os.getenv("AUTH_ISSUER", "interco-invoice-console").strip(),
        auth_audience=os.getenv("AUTH_AUDIENCE", "interco-console-web").strip(),
        auth_access_ttl_min=_int("AUTH_ACCESS_TTL_MIN", 30),
        auth_refresh_ttl_days=_int("AUTH_REFRESH_TTL_DAYS", 7),
        auth_cookie_name=os.getenv("AUTH_COOKIE_NAME", "interco_refresh").strip(),
        auth_cookie_secure=_bool("AUTH_COOKIE_SECURE", False),
        auth_max_failed_attempts=_int("AUTH_MAX_FAILED_ATTEMPTS", 5),
        auth_lock_minutes=_int("AUTH_LOCK_MINUTES", 15),
        bootstrap_admin_username=os.getenv("BOOTSTRAP_ADMIN_USERNAME", "admin").strip() or "admin",
        bootstrap_admin_password=os.getenv("BOOTSTRAP_ADMIN_PASSWORD", "").strip(),
    )


settings = load_settings()
