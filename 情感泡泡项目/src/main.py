"""情缘 · 客户端入口（`python src/main.py`）。

启动顺序（架构 §3.3 / §6.5）：
    1. 装日志与异常钩子；
    2. 读/建 `~/.Qingyuan/config.json`（`client_id` 持久化，Q-1）；
    3. **迁移客户端库**（首次启动把旧库无损升级到 v2，迁移前自动备份）；
    4. 建 `Repo` / `ApiClient`，交给 `AppWindow`（同步线程在登录后才启动）；
    5. 进入 QApplication 事件循环。

任何一步失败都写日志 + 弹中文提示，不静默退出。
"""

from __future__ import annotations

import os
import sys

# 确保能找到项目模块（双击运行 / 从任意 cwd 运行都成立）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import logging  # noqa: E402 - 必须在 sys.path 调整之后

from PyQt5.QtWidgets import QApplication, QMessageBox  # noqa: E402

logger = logging.getLogger("ui.main")

#: 启动失败提示（中文，C-4）。
TEXTS: dict[str, str] = {
    "migration_title": "数据库升级失败",
    "migration_body": "客户端数据库升级没有成功，程序已停止以免破坏数据。\n\n{detail}\n\n日志：{log}",
    "startup_title": "启动失败",
    "startup_body": "程序启动失败：{detail}\n\n日志：{log}",
}


def _show_error(title: str, body: str) -> None:
    """弹中文错误框（无 GUI 环境下退化为日志）。"""
    try:
        QMessageBox.critical(None, title, body)
    except RuntimeError:
        logger.error("%s: %s", title, body)


def _prepare_core():
    """初始化日志 / 配置 / 数据库迁移，返回 `(config, repo, api)`。"""
    from src.core.api import ApiClient
    from src.core.config import backup_dir, db_path, load_or_create, log_path
    from src.core.logging_setup import install_excepthook, setup_logging
    from src.core.migrations import ensure_client_schema
    from src.core.repo import Repo

    setup_logging(log_path(), console=False)
    install_excepthook(logging.getLogger("app"))

    config = load_or_create()
    result = ensure_client_schema(db_path(), backup_dir())
    logger.info(
        "客户端库就绪 outcome=%s schema_version=%s before=%s after=%s backup=%s",
        result.outcome,
        result.schema_version,
        dict(result.before),
        dict(result.after),
        result.backup_path or "-",
    )
    repo = Repo(db_path())
    api = ApiClient(
        config.server_url,
        client_id=config.client_id,
        token=config.token,
    )
    return config, repo, api


def main() -> int:
    """应用入口。"""
    from src.core.config import log_path
    from src.core.errors import MigrationError

    app = QApplication(sys.argv)
    app.setApplicationName("情缘")

    try:
        config, repo, api = _prepare_core()
    except MigrationError as exc:
        logger.exception("数据库迁移失败")
        _show_error(
            TEXTS["migration_title"],
            TEXTS["migration_body"].format(detail=str(exc), log=log_path()),
        )
        return 2
    except Exception as exc:  # 启动期任何异常都要有中文提示 + 日志
        logger.exception("启动准备失败")
        _show_error(
            TEXTS["startup_title"],
            TEXTS["startup_body"].format(detail=str(exc), log=log_path()),
        )
        return 1

    from src.ui import theme
    from src.ui.app_window import AppWindow

    app.setFont(theme.font(10))
    window = AppWindow(config, repo, api)
    window.show()
    return int(app.exec_())


if __name__ == "__main__":
    sys.exit(main())
