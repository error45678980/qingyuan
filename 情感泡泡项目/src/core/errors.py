"""异常家族（架构文档 §11.2）。

规则：
    * 禁止裸 `except:`，禁止 `except Exception: pass`；
    * 所有对外错误必须能映射为 `(code, status_code, message)` 三元组；
    * 服务端直接把 `AppError` 渲染成 `{"code","data","message"}` 包络。

每个异常携带三个类属性，实例可覆盖：
    code        机器可读错误码（bad_request / forbidden / conflict ...）
    status_code HTTP 状态码
    message     默认中文提示
"""

from __future__ import annotations

from typing import Any, Optional


class AppError(Exception):
    """所有业务异常的基类。"""

    code: str = "app_error"
    status_code: int = 500
    message: str = "出了点小问题"

    def __init__(
        self,
        message: Optional[str] = None,
        *,
        code: Optional[str] = None,
        status_code: Optional[int] = None,
    ) -> None:
        """构造异常。

        Args:
            message: 中文提示，缺省使用类属性 `message`。
            code: 覆盖类属性 `code`。
            status_code: 覆盖类属性 `status_code`。
        """
        self.message: str = message if message is not None else type(self).message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        super().__init__(self.message)

    def to_dict(self) -> dict[str, Any]:
        """渲染为响应包络（失败形状）。"""
        return {"code": self.code, "data": None, "message": self.message}

    def __str__(self) -> str:  # pragma: no cover - 仅用于日志
        return f"[{self.code}/{self.status_code}] {self.message}"


class ConfigError(AppError):
    """配置文件读写失败。"""

    code = "config_error"
    status_code = 500
    message = "配置文件读写出错了"


class MigrationError(AppError):
    """数据库迁移失败（启动必须中止）。"""

    code = "migration_error"
    status_code = 500
    message = "数据升级失败，已保留原始备份"


class SchemaMismatchError(AppError):
    """读库时缺少必需列 —— 禁止静默兜底，必须抛错。"""

    code = "schema_mismatch"
    status_code = 500
    message = "数据表结构不匹配，请重新启动以完成升级"


class NetworkError(AppError):
    """连接失败 / 超时。"""

    code = "network_error"
    status_code = 0
    message = "网络好像断了，先记在本地啦"


class ApiError(AppError):
    """服务端返回非 2xx。

    Attributes:
        status_code: 服务端返回的 HTTP 状态码。
        code: 服务端返回的错误码。
        data: 服务端返回的 data（通常为 None）。
    """

    code = "api_error"
    status_code = 500
    message = "服务器返回了错误"

    def __init__(
        self,
        message: Optional[str] = None,
        *,
        code: Optional[str] = None,
        status_code: Optional[int] = None,
        data: Any = None,
    ) -> None:
        super().__init__(message, code=code, status_code=status_code)
        self.data = data


class ConflictError(ApiError):
    """409：版本冲突或规则冲突。"""

    code = "conflict"
    status_code = 409
    message = "数据刚刚更新过，请稍后再试"


class ForbiddenError(ApiError):
    """403：无权限。"""

    code = "forbidden"
    status_code = 403
    message = "你没有这个操作的权限哦"


class NotFoundError(ApiError):
    """404：资源不存在。"""

    code = "not_found"
    status_code = 404
    message = "找不到这个泡泡了"


class UnauthorizedError(ApiError):
    """401：身份校验失败。"""

    code = "unauthorized"
    status_code = 401
    message = "身份验证失败，请重新进入"


class BadRequestError(ApiError):
    """400：参数非法。

    说明：架构文档 §11.2 的清单未列出本类，但服务端 400 分支需要一个具体类型，
    否则只能用 `AppError(status_code=400)` 手工拼装。此处为**新增**子类，
    不改变既有类名与签名。
    """

    code = "bad_request"
    status_code = 400
    message = "请求内容不太对哦"


class RateLimitedError(AppError):
    """429：请求过于频繁。新增子类，理由同 `BadRequestError`。"""

    code = "rate_limited"
    status_code = 429
    message = "慢一点点，休息一下再试吧"

    def __init__(self, message: Optional[str] = None, *, retry_after: int = 60) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class InvalidCursorError(AppError):
    """分页游标非法 → 400。"""

    code = "bad_request"
    status_code = 400
    message = "翻页参数不正确"


#: `code` → 异常类，供适配层按错误码反查。
CODE_TO_ERROR: dict[str, type[AppError]] = {
    "bad_request": BadRequestError,
    "unauthorized": UnauthorizedError,
    "forbidden": ForbiddenError,
    "not_found": NotFoundError,
    "conflict": ConflictError,
    "rate_limited": RateLimitedError,
    "network_error": NetworkError,
    "app_error": AppError,
}


def error_from_response(status_code: int, code: str, message: str, data: Any = None) -> ApiError:
    """按服务端响应构造对应的 `ApiError` 子类实例。

    Args:
        status_code: HTTP 状态码。
        code: 服务端错误码。
        message: 中文提示。
        data: 响应中的 data 字段。

    Returns:
        匹配 `status_code` 的异常实例（优先按状态码，其次按错误码）。
    """
    cls: type[ApiError] = ApiError
    if status_code == 400:
        cls = BadRequestError
    elif status_code == 401:
        cls = UnauthorizedError
    elif status_code == 403:
        cls = ForbiddenError
    elif status_code == 404:
        cls = NotFoundError
    elif status_code == 409:
        cls = ConflictError
    elif status_code == 429:
        cls = RateLimitedError  # type: ignore[assignment]
    elif status_code >= 500:
        cls = ApiError
    else:
        cls = CODE_TO_ERROR.get(code, ApiError)  # type: ignore[assignment]
    return cls(message or ERROR_FALLBACK_MESSAGE, code=code or cls.code, status_code=status_code, data=data)


ERROR_FALLBACK_MESSAGE: str = "服务器返回了错误"

__all__ = [
    "AppError",
    "ConfigError",
    "MigrationError",
    "SchemaMismatchError",
    "NetworkError",
    "ApiError",
    "ConflictError",
    "ForbiddenError",
    "NotFoundError",
    "UnauthorizedError",
    "BadRequestError",
    "RateLimitedError",
    "InvalidCursorError",
    "CODE_TO_ERROR",
    "error_from_response",
]
