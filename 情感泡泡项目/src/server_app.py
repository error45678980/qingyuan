"""Flask app 工厂 + 蓝图注册 + 限流 + 统一错误处理（架构文档 §4.1）。

用法：
    * 生产：`python server.py` → `waitress.serve(get_app(), ...)`；
    * 测试：`create_app(db_path=..., rate_limit=1000)` → `app.test_client()`；
    * 兼容：`from src.server_app import app`（惰性创建，import 时无副作用）。
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Optional

from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException

from src.core.constants import (
    APP_TITLE,
    DEFAULT_SERVER_PORT,
    RATE_LIMIT_PER_MIN,
    RATE_LIMIT_WINDOW_SECONDS,
)
from src.core.errors import AppError, RateLimitedError
from src.core.logging_setup import get_logger, setup_logging
from src.server_api import bp_v2
from src.server_legacy_api import bp_legacy
from src.server_store import (
    ServerStore,
    default_server_backup_dir,
    default_server_db_path,
    default_server_log_path,
    ensure_server_schema,
)

logger = get_logger("server.app")

#: 豁免限流的端点（健康检查）。
EXEMPT_ENDPOINTS: frozenset[str] = frozenset({"api_v2.health", "api_legacy.legacy_ping"})

#: HTTP 状态码 → 业务错误码。
_HTTP_CODE_MAP: dict[int, str] = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "bad_request",
    409: "conflict",
    413: "bad_request",
    429: "rate_limited",
    500: "server_error",
}

#: HTTP 状态码 → 默认中文提示。
_HTTP_MESSAGE_MAP: dict[int, str] = {
    400: "请求内容不太对哦",
    401: "身份验证失败，请重新进入",
    403: "你没有这个操作的权限哦",
    404: "找不到这个泡泡了",
    405: "请求方式不对哦",
    413: "内容太长了",
    429: "慢一点点，休息一下再试吧",
    500: "服务器打了个盹，稍后再试",
}


class RateLimiter:
    """进程内滑动窗口限流（架构文档 U6：单进程 8 线程，`dict + Lock` 即可）。"""

    def __init__(
        self, limit: int = RATE_LIMIT_PER_MIN, window: int = RATE_LIMIT_WINDOW_SECONDS
    ) -> None:
        """构造。

        Args:
            limit: 窗口内允许的请求数。
            window: 窗口长度（秒）。
        """
        self._limit = max(1, int(limit))
        self._window = max(1, int(window))
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str) -> tuple[bool, int]:
        """记一次请求并判断是否超限。

        Args:
            key: 限流键（通常是 IP）。

        Returns:
            `(是否放行, Retry-After 秒数)`。
        """
        now = time.monotonic()
        cutoff = now - self._window
        with self._lock:
            stamps = self._hits.setdefault(key, [])
            while stamps and stamps[0] <= cutoff:
                stamps.pop(0)
            if len(stamps) >= self._limit:
                retry_after = int(self._window - (now - stamps[0])) + 1
                return False, max(1, retry_after)
            stamps.append(now)
            if len(self._hits) > 4096:
                self._prune_locked(cutoff)
            return True, 0

    def _prune_locked(self, cutoff: float) -> None:
        """清理窗口内已无请求的键（调用方需持锁）。"""
        stale = [key for key, stamps in self._hits.items() if not stamps or stamps[-1] <= cutoff]
        for key in stale:
            self._hits.pop(key, None)

    def reset(self) -> None:
        """清空计数（测试用）。"""
        with self._lock:
            self._hits.clear()


def _client_key() -> str:
    """限流键：优先反向代理头，其次 remote_addr。"""
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip() or "unknown"
    return request.remote_addr or "unknown"


def _error_response(code: str, message: str, status: int, *, retry_after: Optional[int] = None):
    """构造失败包络响应。"""
    response = jsonify({"code": code, "data": None, "message": message})
    response.status_code = status
    if retry_after is not None:
        response.headers["Retry-After"] = str(retry_after)
    return response


def _register_error_handlers(app: Flask) -> None:
    """注册统一错误处理（AppError → 包络；HTTPException → 包络）。"""

    @app.errorhandler(AppError)
    def _handle_app_error(error: AppError):
        """业务异常 → 包络。"""
        if isinstance(error, RateLimitedError):
            logger.warning("限流命中: %s", error.message)
            return _error_response(
                error.code, error.message, error.status_code, retry_after=error.retry_after
            )
        if error.status_code >= 500:
            logger.error("%s %s 失败 code=%s: %s", request.method, request.path, error.code, error)
        else:
            logger.info("%s %s → %s code=%s", request.method, request.path, error.status_code, error.code)
        return _error_response(error.code, error.message, error.status_code)

    @app.errorhandler(HTTPException)
    def _handle_http_error(error: HTTPException):
        """Flask 内建异常（404 路由不存在 / 405 等）→ 包络。"""
        status = int(error.code or 500)
        code = _HTTP_CODE_MAP.get(status, "server_error")
        message = _HTTP_MESSAGE_MAP.get(status, error.description or "请求失败")
        return _error_response(code, message, status)

    @app.errorhandler(Exception)
    def _handle_unexpected(error: Exception):
        """兜底：绝不把 traceback 泄给客户端，但必须落日志。"""
        logger.exception("未预期异常 %s %s", request.method, request.path)
        return _error_response("server_error", _HTTP_MESSAGE_MAP[500], 500)


def create_app(
    *,
    db_path: Optional[str] = None,
    backup_dir: Optional[str] = None,
    store: Optional[ServerStore] = None,
    log_path: Optional[str] = None,
    rate_limit: int = RATE_LIMIT_PER_MIN,
    rate_window: int = RATE_LIMIT_WINDOW_SECONDS,
) -> Flask:
    """构造 Flask app。

    Args:
        db_path: 服务端库路径，缺省 `server_data/qingyuan_server.db`。
        backup_dir: 迁移备份目录，缺省 `server_data/backup`。
        store: 已构造的仓储（测试可注入）。
        log_path: 日志路径；传 `""` 表示不落盘。
        rate_limit: 每分钟请求上限。
        rate_window: 限流窗口秒数。

    Returns:
        配置完成的 `Flask` app。
    """
    app = Flask(__name__)
    app.json.ensure_ascii = False
    app.url_map.strict_slashes = False

    resolved_log = default_server_log_path() if log_path is None else log_path
    if resolved_log:
        setup_logging(resolved_log, console=False)

    if store is None:
        resolved_db = db_path or default_server_db_path()
        result = ensure_server_schema(resolved_db, backup_dir or default_server_backup_dir())
        logger.info("服务端库就绪: %s（%s）", resolved_db, result.outcome)
        store = ServerStore(resolved_db)

    app.extensions["eb_store"] = store
    app.extensions["eb_rate_limiter"] = RateLimiter(rate_limit, rate_window)
    app.extensions["eb_title"] = APP_TITLE

    app.register_blueprint(bp_v2)
    app.register_blueprint(bp_legacy)

    @app.before_request
    def _enforce_rate_limit():
        """限流前置钩子（健康检查与 ping 豁免）。"""
        if request.endpoint in EXEMPT_ENDPOINTS:
            return None
        limiter: RateLimiter = app.extensions["eb_rate_limiter"]
        allowed, retry_after = limiter.hit(_client_key())
        if allowed:
            return None
        logger.warning("限流拦截 path=%s ip=%s", request.path, _client_key())
        raise RateLimitedError(retry_after=retry_after)

    _register_error_handlers(app)
    return app


# ---------------------------------------------------------------------------
# 惰性全局 app（`from src.server_app import app` 仍然可用，且 import 无副作用）
# ---------------------------------------------------------------------------

_APP_INSTANCE: Optional[Flask] = None
_APP_LOCK = threading.Lock()


def get_app() -> Flask:
    """取进程内唯一的 app（首次调用时创建并迁移数据库）。"""
    global _APP_INSTANCE
    if _APP_INSTANCE is not None:
        return _APP_INSTANCE
    with _APP_LOCK:
        if _APP_INSTANCE is None:
            _APP_INSTANCE = create_app()
    return _APP_INSTANCE


def __getattr__(name: str) -> Any:
    """PEP 562：`src.server_app.app` 惰性解析。"""
    if name == "app":
        return get_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def run(host: str = "0.0.0.0", port: int = DEFAULT_SERVER_PORT, threads: int = 8) -> None:
    """用 waitress 启动服务（`server.py` 调用）。"""
    from waitress import serve

    application = get_app()
    logger.info("情缘服务端启动 http://%s:%d threads=%d", host, port, threads)
    serve(application, host=host, port=port, threads=threads)


__all__ = [
    "EXEMPT_ENDPOINTS",
    "RateLimiter",
    "create_app",
    "get_app",
    "run",
]

# 兼容旧的 `from src.server_app import app` 用法（PEP 562 已在模块级提供）。
if os.environ.get("EB_EAGER_APP") == "1":  # pragma: no cover - 仅调试用
    _APP_INSTANCE = create_app()
    logging.getLogger("server.app").info("已按 EB_EAGER_APP=1 预创建 app")
