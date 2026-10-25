"""中央服务器入口脚本。

运行 `python server.py` 启动服务（waitress，单进程 8 线程）。
健康检查：`GET http://127.0.0.1:5000/api/v2/health`

环境变量：
    QINGYUAN_SERVER_HOST   监听地址（默认 0.0.0.0）
    QINGYUAN_SERVER_PORT   监听端口（默认 5000）
    QINGYUAN_SERVER_DB     服务端库路径（默认 server_data/qingyuan_server.db）

    旧名 `EMOTION_BUBBLE_SERVER_*` 仍然兼容（新名未设置时生效）。
"""

from __future__ import annotations

import os
import sys

from src.core.constants import DEFAULT_SERVER_HOST, DEFAULT_SERVER_PORT, SERVER_THREADS
from src.core.logging_setup import get_logger, install_excepthook
from src.server_app import create_app, get_app
from src.server_store import ServerStore, ensure_server_schema

logger = get_logger("server.main")


def _env(name: str, legacy: str) -> str:
    """读环境变量（新名优先，旧名兜底）。"""
    return os.environ.get(name) or os.environ.get(legacy) or ""


def main() -> int:
    """启动 waitress 服务。

    Returns:
        进程退出码。
    """
    host = _env("QINGYUAN_SERVER_HOST", "EMOTION_BUBBLE_SERVER_HOST") or DEFAULT_SERVER_HOST
    port_text = _env("QINGYUAN_SERVER_PORT", "EMOTION_BUBBLE_SERVER_PORT")
    try:
        port = int(port_text) if port_text else DEFAULT_SERVER_PORT
    except ValueError:
        logger.error("QINGYUAN_SERVER_PORT 不是合法端口，使用默认 %d", DEFAULT_SERVER_PORT)
        port = DEFAULT_SERVER_PORT

    db_path = _env("QINGYUAN_SERVER_DB", "EMOTION_BUBBLE_SERVER_DB")
    try:
        if db_path:
            ensure_server_schema(db_path)
            app = create_app(db_path=db_path, store=ServerStore(db_path))
        else:
            app = get_app()
    except Exception:  # 迁移失败必须中止启动，并写清原因
        logger.exception("服务端初始化失败，已中止启动")
        return 1

    install_excepthook(logger)
    from waitress import serve

    logger.info("情缘服务端启动 http://%s:%d threads=%d", host, port, SERVER_THREADS)
    serve(app, host=host, port=port, threads=SERVER_THREADS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
