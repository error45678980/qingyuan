"""服务端库的 DDL、迁移声明与迁移入口（架构文档 §3.2）。

从 `server_store.py` 拆出，目的：单文件 ≤ 500 行（铁律 5）。
`bubbles` / `replies` / `chat_messages` 与客户端**完全同构同序**（P0-1 的根治手段），
差异仅三处：无 `sync_state`、无 `outbox`、多 `clients` 与 `op_log`。
列名映射表达式复用 `core.migration_tables`，保证两端规则不漂移。
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from src.core.constants import SCHEMA_VERSION
from src.core.fileops import rename_sqlite_db
from src.core.migrate_engine import SchemaMigrator
from src.core.migration_tables import (
    META_CREATE_SQL,
    TableMigration,
    bubble_expressions,
    chat_expressions,
    reply_expressions,
)

# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------

_PROJECT_ROOT: str = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER_DATA_DIR: str = os.path.join(_PROJECT_ROOT, "server_data")
SERVER_DB_NAME: str = "qingyuan_server.db"
LEGACY_SERVER_DB_NAME: str = "emotion_bubble_server.db"
SERVER_LOG_NAME: str = "server.log"


def default_server_db_path() -> str:
    """默认服务端库路径 `<项目根>/server_data/qingyuan_server.db`。

    兼容更名前的 `emotion_bubble_server.db`：存在旧文件且新文件不存在时原地改名。
    """
    new_path = os.path.join(SERVER_DATA_DIR, SERVER_DB_NAME)
    old_path = os.path.join(SERVER_DATA_DIR, LEGACY_SERVER_DB_NAME)
    if os.path.exists(new_path) or not os.path.exists(old_path):
        return new_path
    if rename_sqlite_db(old_path, new_path):
        return new_path
    logging.getLogger("qingyuan.server.schema").warning(
        "服务端库更名未完成，继续使用旧文件名 %s", old_path
    )
    return old_path


def default_server_backup_dir() -> str:
    """默认服务端备份目录 `<项目根>/server_data/backup`。"""
    return os.path.join(SERVER_DATA_DIR, "backup")


def default_server_log_path() -> str:
    """默认服务端日志路径 `<项目根>/server_data/server.log`。"""
    return os.path.join(SERVER_DATA_DIR, SERVER_LOG_NAME)


# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

SERVER_BUBBLES_CREATE_SQL: str = """
CREATE TABLE {table} (
  id                TEXT    PRIMARY KEY,
  author_nickname   TEXT    NOT NULL,
  author_client_id  TEXT    NOT NULL DEFAULT '',
  content           TEXT    NOT NULL,
  emotion_tag       TEXT    NOT NULL
                    CHECK (emotion_tag IN ('开心','难过','孤独','焦虑','寻求建议','找人倾诉')),
  reply_mode        TEXT    NOT NULL CHECK (reply_mode IN ('single','dialog')),
  status            TEXT    NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active','resolved','saved_public','saved_private')),
  version           INTEGER NOT NULL DEFAULT 1,
  created_at        TEXT    NOT NULL,
  resolved_at       TEXT,
  saved_at          TEXT
)
"""

SERVER_REPLIES_CREATE_SQL: str = """
CREATE TABLE {table} (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  bubble_id          TEXT    NOT NULL REFERENCES {ref}(id) ON DELETE CASCADE,
  replier_nickname   TEXT    NOT NULL,
  replier_client_id  TEXT    NOT NULL DEFAULT '',
  content            TEXT    NOT NULL,
  created_at         TEXT    NOT NULL,
  op_id              TEXT    NOT NULL
)
"""

SERVER_CHAT_CREATE_SQL: str = """
CREATE TABLE {table} (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  bubble_id           TEXT    NOT NULL REFERENCES {ref}(id) ON DELETE CASCADE,
  sender_nickname     TEXT    NOT NULL,
  sender_client_id    TEXT    NOT NULL DEFAULT '',
  receiver_nickname   TEXT    NOT NULL,
  receiver_client_id  TEXT,
  content             TEXT    NOT NULL,
  created_at          TEXT    NOT NULL,
  op_id               TEXT    NOT NULL
)
"""

CLIENTS_CREATE_SQL: str = """
CREATE TABLE {table} (
  client_id     TEXT PRIMARY KEY,
  nickname      TEXT NOT NULL,
  token         TEXT NOT NULL UNIQUE,
  created_at    TEXT NOT NULL,
  last_seen_at  TEXT NOT NULL
)
"""

OP_LOG_CREATE_SQL: str = """
CREATE TABLE {table} (
  op_id       TEXT PRIMARY KEY,
  client_id   TEXT NOT NULL,
  op_type     TEXT NOT NULL,
  result_json TEXT NOT NULL,
  created_at  TEXT NOT NULL
)
"""

SERVER_INDEX_STATEMENTS: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS idx_clients_token ON clients(token)",
    "CREATE INDEX IF NOT EXISTS idx_bub_status_created ON bubbles(status, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_bub_emotion_created ON bubbles(emotion_tag, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_bub_author ON bubbles(author_client_id, status)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_rep_op ON replies(op_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_chat_op ON chat_messages(op_id)",
    "CREATE INDEX IF NOT EXISTS idx_rep_bubble_created ON replies(bubble_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_chat_bubble_created ON chat_messages(bubble_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_chat_participants "
    "ON chat_messages(bubble_id, sender_client_id, receiver_client_id)",
)


def _server_bubbles_select(columns: frozenset[str]) -> tuple[str, tuple[Any, ...]]:
    """服务端 `bubbles` 映射（无 `sync_state`）。"""
    return ", ".join(bubble_expressions(columns)), ()


def _server_replies_select(columns: frozenset[str]) -> tuple[str, tuple[Any, ...]]:
    """服务端 `replies` 映射（无 `sync_state`）。"""
    return ", ".join(reply_expressions(columns)), ()


def _server_chat_select(columns: frozenset[str]) -> tuple[str, tuple[Any, ...]]:
    """服务端 `chat_messages` 映射（无 `sync_state`）。"""
    return ", ".join(chat_expressions(columns)), ()


SERVER_TARGET_COLUMNS: dict[str, tuple[str, ...]] = {
    "bubbles": (
        "id",
        "author_nickname",
        "author_client_id",
        "content",
        "emotion_tag",
        "reply_mode",
        "status",
        "version",
        "created_at",
        "resolved_at",
        "saved_at",
    ),
    "replies": (
        "id",
        "bubble_id",
        "replier_nickname",
        "replier_client_id",
        "content",
        "created_at",
        "op_id",
    ),
    "chat_messages": (
        "id",
        "bubble_id",
        "sender_nickname",
        "sender_client_id",
        "receiver_nickname",
        "receiver_client_id",
        "content",
        "created_at",
        "op_id",
    ),
}

SERVER_TABLE_MIGRATIONS: tuple[TableMigration, ...] = (
    TableMigration("bubbles", SERVER_BUBBLES_CREATE_SQL, _server_bubbles_select),
    TableMigration("replies", SERVER_REPLIES_CREATE_SQL, _server_replies_select, fk_parent="bubbles"),
    TableMigration(
        "chat_messages", SERVER_CHAT_CREATE_SQL, _server_chat_select, fk_parent="bubbles"
    ),
)

SERVER_EXTRA_TABLES: tuple[tuple[str, str], ...] = (
    ("clients", CLIENTS_CREATE_SQL),
    ("op_log", OP_LOG_CREATE_SQL),
    ("meta", META_CREATE_SQL),
)


class ServerMigrator:
    """服务端库迁移器。"""

    def __init__(self, db_path: Optional[str] = None, backup_dir: Optional[str] = None) -> None:
        """构造。

        Args:
            db_path: 服务端库路径，缺省 `default_server_db_path()`。
            backup_dir: 备份目录，缺省 `default_server_backup_dir()`。
        """
        self._engine = SchemaMigrator(
            db_path=db_path or default_server_db_path(),
            backup_dir=backup_dir or default_server_backup_dir(),
            schema_version=SCHEMA_VERSION,
            tables=SERVER_TABLE_MIGRATIONS,
            target_columns=SERVER_TARGET_COLUMNS,
            extra_tables=SERVER_EXTRA_TABLES,
            index_statements=SERVER_INDEX_STATEMENTS,
        )

    def run(self):
        """执行迁移并返回 `MigrationResult`。"""
        return self._engine.run()


def ensure_server_schema(db_path: Optional[str] = None, backup_dir: Optional[str] = None):
    """便捷函数：确保服务端库为 v2 结构。"""
    return ServerMigrator(db_path, backup_dir).run()



__all__ = [
    "SERVER_DATA_DIR",
    "SERVER_DB_NAME",
    "SERVER_LOG_NAME",
    "default_server_db_path",
    "default_server_backup_dir",
    "default_server_log_path",
    "SERVER_BUBBLES_CREATE_SQL",
    "SERVER_REPLIES_CREATE_SQL",
    "SERVER_CHAT_CREATE_SQL",
    "CLIENTS_CREATE_SQL",
    "OP_LOG_CREATE_SQL",
    "SERVER_INDEX_STATEMENTS",
    "SERVER_TARGET_COLUMNS",
    "SERVER_TABLE_MIGRATIONS",
    "SERVER_EXTRA_TABLES",
    "ServerMigrator",
    "ensure_server_schema",
]
