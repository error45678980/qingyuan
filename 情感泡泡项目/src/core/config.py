"""路径解析与 `config.json` 读写。

设计要点（Q-1）：
    * `client_id` 首次启动生成 UUID4，持久化到 `~/.Qingyuan/config.json`，此后复用；
    * 昵称仅用于展示，不参与身份合并；
    * `token` 由服务端 `POST /api/v2/session` 下发，长期复用。

环境变量（测试与多实例用，优先级高于默认值）：
    QINGYUAN_HOME        覆盖应用目录（默认 `~/.Qingyuan`）
    QINGYUAN_DB          覆盖客户端库路径
    QINGYUAN_SERVER_URL  覆盖服务端地址
    （旧名 `EMOTION_BUBBLE_*` 仍然兼容，仅在新名未设置时生效）

品牌更名：v2.0 由「情感泡泡」更名为「情缘」，旧数据目录 `~/.EmotionBubble`
会在首次启动时**原地改名**为 `~/.Qingyuan`（同卷 `os.rename`，原子操作，不复制不丢失）。
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from .constants import DEFAULT_SERVER_URL
from .errors import ConfigError
from .fileops import rename_sqlite_db

logger = logging.getLogger("qingyuan.config")

#: 应用目录名（位于用户主目录下）。
APP_DIR_NAME: str = ".Qingyuan"
#: 旧版应用目录名（用于一次性改名迁移）。
LEGACY_APP_DIR_NAME: str = ".EmotionBubble"
#: 配置文件名。
CONFIG_FILE_NAME: str = "config.json"
#: 客户端数据库文件名。
DB_FILE_NAME: str = "qingyuan.db"
#: 旧版客户端数据库文件名。
LEGACY_DB_FILE_NAME: str = "emotion_bubble.db"
#: 备份目录名。
BACKUP_DIR_NAME: str = "backup"
#: 日志文件名。
LOG_FILE_NAME: str = "app.log"

ENV_HOME: str = "QINGYUAN_HOME"
ENV_DB: str = "QINGYUAN_DB"
ENV_SERVER_URL: str = "QINGYUAN_SERVER_URL"
#: 旧版环境变量（兼容保留，新名优先）。
LEGACY_ENV_HOME: str = "EMOTION_BUBBLE_HOME"
LEGACY_ENV_DB: str = "EMOTION_BUBBLE_DB"
LEGACY_ENV_SERVER_URL: str = "EMOTION_BUBBLE_SERVER_URL"


def _env(name: str, legacy: str = "") -> str:
    """读环境变量（新名优先，旧名兜底）。

    Args:
        name: 新环境变量名。
        legacy: 旧环境变量名，可为空。

    Returns:
        值；都没设置时返回空串。
    """
    return os.environ.get(name) or (os.environ.get(legacy) if legacy else "") or ""


def new_client_id() -> str:
    """生成新的设备标识。"""
    return str(uuid.uuid4())


def _migrate_legacy_app_dir() -> None:
    """把旧应用目录原地改名为新目录（仅当新目录不存在且旧目录存在）。"""
    home = os.path.expanduser("~")
    new_dir = os.path.join(home, APP_DIR_NAME)
    old_dir = os.path.join(home, LEGACY_APP_DIR_NAME)
    if os.path.exists(new_dir) or not os.path.isdir(old_dir):
        return
    try:
        os.rename(old_dir, new_dir)
        logger.info("应用目录已更名: %s -> %s", old_dir, new_dir)
    except OSError:
        # 改名失败（被占用/权限）时保留旧目录，不影响启动
        logger.exception("应用目录更名失败，继续使用旧目录 %s", old_dir)


def app_dir() -> str:
    """应用数据目录（不自动创建）。"""
    override = _env(ENV_HOME, LEGACY_ENV_HOME)
    if override:
        return os.path.abspath(os.path.expanduser(override))
    _migrate_legacy_app_dir()
    new_dir = os.path.join(os.path.expanduser("~"), APP_DIR_NAME)
    if os.path.isdir(new_dir):
        return new_dir
    return os.path.join(os.path.expanduser("~"), APP_DIR_NAME)


def ensure_app_dir() -> str:
    """确保应用目录存在并返回路径。"""
    directory = app_dir()
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError as exc:
        raise ConfigError(f"无法创建应用目录: {directory}") from exc
    return directory


def config_path() -> str:
    """配置文件路径。"""
    return os.path.join(app_dir(), CONFIG_FILE_NAME)


def db_path() -> str:
    """客户端数据库路径（可用环境变量覆盖）。

    兼容更名前的库文件名：`~/.Qingyuan/emotion_bubble.db` 会被原地改名为
    `qingyuan.db`（同目录 `os.rename`，原子操作），改名失败则继续用旧文件名，
    保证数据一条都不丢。
    """
    override = _env(ENV_DB, LEGACY_ENV_DB)
    if override:
        return os.path.abspath(os.path.expanduser(override))
    directory = app_dir()
    new_file = os.path.join(directory, DB_FILE_NAME)
    old_file = os.path.join(directory, LEGACY_DB_FILE_NAME)
    if os.path.exists(new_file) or not os.path.exists(old_file):
        return new_file
    if rename_sqlite_db(old_file, new_file):
        return new_file
    logger.warning("客户端库更名未完成，继续使用旧文件名 %s", old_file)
    return old_file


def backup_dir() -> str:
    """客户端迁移备份目录。"""
    return os.path.join(app_dir(), BACKUP_DIR_NAME)


def log_path() -> str:
    """客户端日志路径。"""
    return os.path.join(app_dir(), LOG_FILE_NAME)


def default_server_url() -> str:
    """服务端地址（环境变量优先，新名 `QINGYUAN_SERVER_URL` 优先于旧名）。"""
    return _env(ENV_SERVER_URL, LEGACY_ENV_SERVER_URL) or DEFAULT_SERVER_URL


@dataclass
class AppConfig:
    """客户端配置。字段与 `config.json` 一一对应。"""

    client_id: str = field(default_factory=new_client_id)
    nickname: str = ""
    token: str = ""
    server_url: str = field(default_factory=default_server_url)
    #: 窗口几何记忆（x/y/w/h），下次启动恢复；为空表示首次启动。
    window: dict[str, int] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # 序列化
    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        """转成可 JSON 序列化的 dict。"""
        return {
            "client_id": self.client_id,
            "nickname": self.nickname,
            "token": self.token,
            "server_url": self.server_url,
            "window": dict(self.window),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "AppConfig":
        """从 dict 构造，缺字段用默认值补齐。

        `client_id` 缺失或为空时生成新的 UUID4（保证任何情况下都有身份）。
        """
        client_id = str(payload.get("client_id") or "").strip() or new_client_id()
        window_raw = payload.get("window")
        window: dict[str, int] = {}
        if isinstance(window_raw, Mapping):
            for key in ("x", "y", "w", "h"):
                value = window_raw.get(key)
                if isinstance(value, (int, float)):
                    window[key] = int(value)
        return cls(
            client_id=client_id,
            nickname=str(payload.get("nickname") or ""),
            token=str(payload.get("token") or ""),
            server_url=str(payload.get("server_url") or default_server_url()),
            window=window,
        )

    # ------------------------------------------------------------------
    # 读写
    # ------------------------------------------------------------------
    @classmethod
    def load(cls, path: Optional[str] = None) -> "AppConfig":
        """读取配置；文件不存在时返回带新 `client_id` 的默认配置。

        Args:
            path: 配置文件路径，缺省 `config_path()`。

        Returns:
            `AppConfig` 实例。

        Raises:
            ConfigError: 文件存在但无法读取或 JSON 非法。
        """
        target = path or config_path()
        if not os.path.exists(target):
            return cls()
        try:
            with open(target, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError) as exc:
            raise ConfigError(f"配置文件无法读取: {target}") from exc
        if not isinstance(payload, dict):
            raise ConfigError(f"配置文件格式不正确: {target}")
        return cls.from_dict(payload)

    def save(self, path: Optional[str] = None) -> None:
        """原子写入配置（先写临时文件再 `os.replace`）。

        Args:
            path: 配置文件路径，缺省 `config_path()`。

        Raises:
            ConfigError: 写入失败。
        """
        target = path or config_path()
        directory = os.path.dirname(os.path.abspath(target))
        try:
            if directory:
                os.makedirs(directory, exist_ok=True)
            temp_path = target + ".tmp"
            with open(temp_path, "w", encoding="utf-8") as handle:
                json.dump(self.to_dict(), handle, ensure_ascii=False, indent=2)
            os.replace(temp_path, target)
        except OSError as exc:
            raise ConfigError(f"配置文件无法写入: {target}") from exc

    # ------------------------------------------------------------------
    # 便捷方法
    # ------------------------------------------------------------------
    def with_nickname(self, nickname: str) -> "AppConfig":
        """返回替换昵称后的新配置（不修改自身）。"""
        return AppConfig(
            client_id=self.client_id,
            nickname=nickname,
            token=self.token,
            server_url=self.server_url,
            window=dict(self.window),
        )

    def with_token(self, token: str) -> "AppConfig":
        """返回替换 token 后的新配置（不修改自身）。"""
        return AppConfig(
            client_id=self.client_id,
            nickname=self.nickname,
            token=token,
            server_url=self.server_url,
            window=dict(self.window),
        )

    def with_window(self, x: int, y: int, width: int, height: int) -> "AppConfig":
        """返回替换窗口几何后的新配置（不修改自身）。"""
        return AppConfig(
            client_id=self.client_id,
            nickname=self.nickname,
            token=self.token,
            server_url=self.server_url,
            window={"x": int(x), "y": int(y), "w": int(width), "h": int(height)},
        )


def load_or_create(path: Optional[str] = None) -> AppConfig:
    """读取配置；不存在则创建并落盘（首次启动路径）。

    Args:
        path: 配置文件路径，缺省 `config_path()`。

    Returns:
        已落盘的 `AppConfig`。
    """
    target = path or config_path()
    if os.path.exists(target):
        return AppConfig.load(target)
    ensure_app_dir()
    config = AppConfig()
    config.save(target)
    return config


__all__ = [
    "APP_DIR_NAME",
    "CONFIG_FILE_NAME",
    "DB_FILE_NAME",
    "BACKUP_DIR_NAME",
    "LOG_FILE_NAME",
    "ENV_HOME",
    "ENV_DB",
    "ENV_SERVER_URL",
    "new_client_id",
    "app_dir",
    "ensure_app_dir",
    "config_path",
    "db_path",
    "backup_dir",
    "log_path",
    "default_server_url",
    "AppConfig",
    "load_or_create",
]
