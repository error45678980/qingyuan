"""日志与全局异常钩子（架构文档 §11.3）。

格式：`%(asctime)s [%(levelname)-5s] %(name)s: %(message)s`
轮转：`RotatingFileHandler(maxBytes=5MB, backupCount=3, encoding="utf-8")`
logger 名：`core.<模块>` / `server.<模块>` / `ui.<模块>`
禁令：日志中不得出现 token 全文（用 `mask_token` 截断为前 6 位）。
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from typing import Optional

LOG_FORMAT: str = "%(asctime)s [%(levelname)-5s] %(name)s: %(message)s"
DATE_FORMAT: str = "%Y-%m-%d %H:%M:%S"
MAX_BYTES: int = 5 * 1024 * 1024
BACKUP_COUNT: int = 3

#: 挂在 handler 上的自定义标记，用于识别"已安装过"，保证 setup 幂等。
_HANDLER_TAG = "_qingyuan_handler"

#: 根 logger 名（客户端与服务端共用一个根，子 logger 各自命名）。
ROOT_LOGGER_NAME: str = "qingyuan"


def get_logger(name: str) -> logging.Logger:
    """获取命名 logger。

    Args:
        name: 形如 `core.models` / `server.api` / `ui.wall` 的短名。

    Returns:
        对应 logger；未配置 handler 时消息不会落盘（不会报错）。
    """
    return logging.getLogger(name)


def mask_token(token: Optional[str]) -> str:
    """把 token 截断为可安全写日志的形式。

    Args:
        token: 原始 token。

    Returns:
        前 6 位 + `…`；空值返回 `"-"`。
    """
    if not token:
        return "-"
    text = str(token)
    if len(text) <= 6:
        return text[:2] + "…"
    return text[:6] + "…"


def setup_logging(
    log_path: Optional[str] = None,
    *,
    level: int = logging.INFO,
    console: bool = False,
) -> logging.Logger:
    """安装轮转文件日志，幂等（重复调用不会重复挂 handler）。

    Args:
        log_path: 日志文件路径；为 `None` 时只配置格式不落盘（测试用）。
        level: 日志级别。
        console: 是否同时输出到 stderr。

    Returns:
        根 logger。
    """
    root = logging.getLogger(ROOT_LOGGER_NAME)
    root.setLevel(level)
    root.propagate = False

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    if log_path:
        directory = os.path.dirname(os.path.abspath(log_path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        existing = _find_handler(root, "file", log_path)
        if existing is None:
            file_handler = RotatingFileHandler(
                log_path,
                maxBytes=MAX_BYTES,
                backupCount=BACKUP_COUNT,
                encoding="utf-8",
            )
            file_handler.setLevel(level)
            file_handler.setFormatter(formatter)
            setattr(file_handler, _HANDLER_TAG, ("file", os.path.abspath(log_path)))
            root.addHandler(file_handler)
        else:
            existing.setLevel(level)
            existing.setFormatter(formatter)

    if console:
        existing_console = _find_handler(root, "console", "")
        if existing_console is None:
            stream_handler = logging.StreamHandler(stream=sys.stderr)
            stream_handler.setLevel(level)
            stream_handler.setFormatter(formatter)
            setattr(stream_handler, _HANDLER_TAG, ("console", ""))
            root.addHandler(stream_handler)

    if not root.handlers:
        # 兜底：避免 "No handlers" 警告，且不污染 stderr。
        root.addHandler(logging.NullHandler())
    return root


def _find_handler(root: logging.Logger, kind: str, path: str) -> Optional[logging.Handler]:
    """在 root 上查找已安装的同类 handler。"""
    target = os.path.abspath(path) if path else ""
    for handler in root.handlers:
        tag = getattr(handler, _HANDLER_TAG, None)
        if not tag:
            continue
        tag_kind, tag_path = tag
        if tag_kind == kind and tag_path == target:
            return handler
    return None


def install_excepthook(logger: Optional[logging.Logger] = None) -> None:
    """安装全局未捕获异常钩子，把 traceback 写进日志（替代 `print`）。

    Args:
        logger: 目标 logger，缺省使用 `qingyuan` 根 logger。
    """
    target = logger or logging.getLogger(ROOT_LOGGER_NAME)

    def _hook(exc_type, exc_value, exc_tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        target.critical("未捕获异常", exc_info=(exc_type, exc_value, exc_tb))

    sys.excepthook = _hook


__all__ = [
    "LOG_FORMAT",
    "DATE_FORMAT",
    "MAX_BYTES",
    "BACKUP_COUNT",
    "ROOT_LOGGER_NAME",
    "get_logger",
    "mask_token",
    "setup_logging",
    "install_excepthook",
]
