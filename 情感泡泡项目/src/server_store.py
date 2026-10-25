"""服务端仓储：连接、事务、身份、幂等缓存与统计（架构文档 §3.2 / §4）。

从 `server_store.py` 拆出内容查询（`server_queries.ContentQueriesMixin`）与
DDL/迁移（`server_schema`）后，本模块只保留"连接与横切关注点"，
保证单文件 ≤ 500 行（铁律 5）。
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from src.server_queries import ContentQueriesMixin, Page
from src.server_schema import (
    CLIENTS_CREATE_SQL,
    OP_LOG_CREATE_SQL,
    SERVER_BUBBLES_CREATE_SQL,
    SERVER_CHAT_CREATE_SQL,
    SERVER_DATA_DIR,
    SERVER_DB_NAME,
    SERVER_EXTRA_TABLES,
    SERVER_INDEX_STATEMENTS,
    SERVER_LOG_NAME,
    SERVER_REPLIES_CREATE_SQL,
    SERVER_TABLE_MIGRATIONS,
    SERVER_TARGET_COLUMNS,
    ServerMigrator,
    default_server_backup_dir,
    default_server_db_path,
    default_server_log_path,
    ensure_server_schema,
)

logger = logging.getLogger("server.store")

@dataclass(frozen=True)
class ClientRecord:
    """已登记的设备身份。"""

    client_id: str
    nickname: str
    token: str
    created_at: str
    last_seen_at: str


class ServerStore(ContentQueriesMixin):
    """服务端仓储（多线程：waitress 8 线程，每线程独立连接）。"""

    def __init__(self, db_path: Optional[str] = None) -> None:
        """构造（不建表、不迁移）。

        Args:
            db_path: 服务端库路径，缺省 `default_server_db_path()`。
        """
        self._db_path = os.path.abspath(db_path or default_server_db_path())
        self._local = threading.local()

    # ------------------------------------------------------------------
    # 连接
    # ------------------------------------------------------------------
    @property
    def db_path(self) -> str:
        """库文件路径。"""
        return self._db_path

    def _conn(self) -> sqlite3.Connection:
        """取当前线程连接（懒建）。"""
        conn: Optional[sqlite3.Connection] = getattr(self._local, "conn", None)
        if conn is not None:
            return conn
        directory = os.path.dirname(self._db_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        conn = sqlite3.connect(self._db_path, isolation_level=None, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA foreign_keys = ON")
        self._local.conn = conn
        return conn

    def transaction(self) -> "_Tx":
        """写事务上下文（`BEGIN IMMEDIATE`）。"""
        return _Tx(self._conn())

    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        """执行单条语句。"""
        return self._conn().execute(sql, tuple(params))

    def query_all(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        """查询多行。"""
        return self._conn().execute(sql, tuple(params)).fetchall()

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> Optional[sqlite3.Row]:
        """查询单行。"""
        return self._conn().execute(sql, tuple(params)).fetchone()

    def close(self) -> None:
        """关闭当前线程连接。"""
        conn: Optional[sqlite3.Connection] = getattr(self._local, "conn", None)
        if conn is None:
            return
        try:
            conn.close()
        except sqlite3.Error as exc:
            logger.warning("关闭服务端连接失败: %s", exc)
        self._local.conn = None

    # ------------------------------------------------------------------
    # meta
    # ------------------------------------------------------------------
    def get_meta(self, key: str, default: str = "") -> str:
        """读取 `meta` 键。"""
        row = self.query_one("SELECT value FROM meta WHERE key = ?", (key,))
        return str(row["value"]) if row is not None else default

    def set_meta(self, key: str, value: str) -> None:
        """写入 `meta` 键。"""
        self.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, str(value))
        )

    @property
    def schema_version(self) -> int:
        """库的 schema 版本（读不到时返回 0）。"""
        raw = self.get_meta("schema_version", "0")
        try:
            return int(raw)
        except (TypeError, ValueError):
            return 0

    # ------------------------------------------------------------------
    # clients
    # ------------------------------------------------------------------
    def get_client(self, client_id: str) -> Optional[ClientRecord]:
        """按 `client_id` 取身份记录。"""
        row = self.query_one(
            "SELECT client_id, nickname, token, created_at, last_seen_at "
            "FROM clients WHERE client_id = ?",
            (client_id,),
        )
        if row is None:
            return None
        return ClientRecord(
            client_id=str(row["client_id"]),
            nickname=str(row["nickname"]),
            token=str(row["token"]),
            created_at=str(row["created_at"]),
            last_seen_at=str(row["last_seen_at"]),
        )

    def get_client_by_token(self, token: str) -> Optional[ClientRecord]:
        """按 token 反查身份（用于 token 校验）。"""
        row = self.query_one(
            "SELECT client_id, nickname, token, created_at, last_seen_at "
            "FROM clients WHERE token = ?",
            (token,),
        )
        if row is None:
            return None
        return ClientRecord(
            client_id=str(row["client_id"]),
            nickname=str(row["nickname"]),
            token=str(row["token"]),
            created_at=str(row["created_at"]),
            last_seen_at=str(row["last_seen_at"]),
        )

    def upsert_client(
        self, *, client_id: str, nickname: str, token: str, at: str
    ) -> tuple[ClientRecord, bool]:
        """登记或更新身份。

        Args:
            client_id: 设备 ID。
            nickname: 昵称（非空才覆盖已有昵称）。
            token: token（已存在时保留原 token）。
            at: 当前时间。

        Returns:
            `(记录, 是否新建)`。
        """
        existing = self.get_client(client_id)
        if existing is None:
            self.execute(
                "INSERT INTO clients (client_id, nickname, token, created_at, last_seen_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (client_id, nickname, token, at, at),
            )
            record = self.get_client(client_id)
            assert record is not None
            return record, True
        self.execute(
            "UPDATE clients SET nickname = ?, last_seen_at = ? WHERE client_id = ?",
            (nickname or existing.nickname, at, client_id),
        )
        record = self.get_client(client_id)
        assert record is not None
        return record, False

    def touch_client(self, client_id: str, at: str) -> None:
        """更新 `last_seen_at`。"""
        self.execute(
            "UPDATE clients SET last_seen_at = ? WHERE client_id = ?", (at, client_id)
        )

    # ------------------------------------------------------------------
    # op_log（幂等重放缓存）
    # ------------------------------------------------------------------
    def get_op_log(self, op_id: str) -> Optional[dict[str, Any]]:
        """取某 `op_id` 的首次结果（未命中返回 None）。"""
        if not op_id:
            return None
        row = self.query_one(
            "SELECT op_id, client_id, op_type, result_json, created_at "
            "FROM op_log WHERE op_id = ?",
            (op_id,),
        )
        if row is None:
            return None
        try:
            result = json.loads(row["result_json"])
        except ValueError:
            logger.exception("op_log 结果 JSON 损坏 op_id=%s", op_id)
            result = {}
        return {
            "op_id": str(row["op_id"]),
            "client_id": str(row["client_id"]),
            "op_type": str(row["op_type"]),
            "result": result,
            "created_at": str(row["created_at"]),
        }

    def put_op_log(
        self, *, op_id: str, client_id: str, op_type: str, result: dict[str, Any], at: str
    ) -> None:
        """写入幂等缓存（同 `op_id` 只保留首次结果）。"""
        self.execute(
            "INSERT OR IGNORE INTO op_log (op_id, client_id, op_type, result_json, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (op_id, client_id, op_type, json.dumps(result, ensure_ascii=False), at),
        )

    # ------------------------------------------------------------------
    # 统计
    # ------------------------------------------------------------------
    def stats(self) -> dict[str, int]:
        """健康检查用的统计。"""
        result: dict[str, int] = {}
        for name in ("bubbles", "replies", "chat_messages", "clients", "op_log"):
            row = self.query_one(f"SELECT COUNT(*) AS n FROM {name}")
            result[name] = int(row["n"]) if row is not None else 0
        return result


class _Tx:
    """`ServerStore.transaction()` 的上下文实现。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def __enter__(self) -> sqlite3.Connection:
        self._conn.execute("BEGIN IMMEDIATE")
        return self._conn

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        if exc_type is None:
            self._conn.execute("COMMIT")
        else:
            self._conn.execute("ROLLBACK")
        return False



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
    "Page",
    "ClientRecord",
    "ServerStore",
]
