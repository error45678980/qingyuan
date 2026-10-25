"""文件级小工具：SQLite 库的安全改名。

为什么需要这个模块：SQLite 在 WAL 模式下，已提交的事务可能还在 `-wal` 边车文件里，
主库文件本身并不包含最新数据。**只 rename 主文件会静默丢数据**。
因此改名必须先 `wal_checkpoint(TRUNCATE)` 把 WAL 落盘，再把主库与 `-wal`/`-shm`
三个文件一起搬走；checkpoint 失败（例如库被占用）时宁可放弃改名，也不冒险。
"""

from __future__ import annotations

import logging
import os
import sqlite3

logger = logging.getLogger("qingyuan.fileops")

#: SQLite WAL 模式的两个边车文件后缀。
SIDECAR_SUFFIXES: tuple[str, ...] = ("-wal", "-shm")


def _checkpoint(db_file: str) -> bool:
    """把 WAL 内容合并回主库。

    Args:
        db_file: 主库路径。

    Returns:
        是否成功（库不存在也视为成功）。
    """
    if not os.path.exists(db_file):
        return True
    try:
        conn = sqlite3.connect(db_file, timeout=2.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            conn.close()
        return True
    except sqlite3.Error:
        logger.exception("WAL 落盘失败，放弃改名: %s", db_file)
        return False


def rename_sqlite_db(old_path: str, new_path: str) -> bool:
    """安全地把 SQLite 库改名（含 `-wal` / `-shm` 边车文件）。

    Args:
        old_path: 原路径。
        new_path: 新路径（不得已存在）。

    Returns:
        是否改名成功。失败时调用方应继续使用 `old_path`。
    """
    if os.path.exists(new_path) or not os.path.exists(old_path):
        return False
    if not _checkpoint(old_path):
        return False
    try:
        os.rename(old_path, new_path)
    except OSError:
        logger.exception("主库改名失败: %s -> %s", old_path, new_path)
        return False
    for suffix in SIDECAR_SUFFIXES:
        sidecar = old_path + suffix
        if not os.path.exists(sidecar):
            continue
        try:
            os.replace(sidecar, new_path + suffix)
        except OSError:
            logger.exception("边车文件改名失败: %s", sidecar)
    logger.info("SQLite 库已改名: %s -> %s", old_path, new_path)
    return True


__all__ = ["rename_sqlite_db", "SIDECAR_SUFFIXES"]
