"""客户端仓储（架构文档 §6.2 / §6.3）。

硬规则：
    * **禁止 `SELECT *`、禁止位置下标**：一律显式列名 + `sqlite3.Row` + `models` 转换；
    * 每线程一个连接（`threading.local`），WAL + `busy_timeout=5000`；
    * 所有多语句写操作包在 `transaction()`（`BEGIN IMMEDIATE`）内；
    * 唯一写者是 NetWorker 线程；UI 线程只调用读方法。
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from contextlib import contextmanager
from typing import Any, Iterator, Optional, Sequence

from .constants import (
    DEFAULT_STATUS,
    MINE_GROUPS,
    PAGE_SIZE_DEFAULT,
    PUBLIC_STATUSES,
    SYNC_STATE_PENDING,
    SYNC_STATE_SYNCED,
)
from .migrations import ensure_client_schema
from .models import (
    Bubble,
    ChatMessage,
    Reply,
    row_to_bubble,
    row_to_chat,
    row_to_reply,
    rows_to_bubbles,
    rows_to_chats,
    rows_to_replies,
)

logger = logging.getLogger("core.repo")

#: 泡泡查询的统一列清单（含两个计算字段）。
_BUBBLE_COLUMNS: str = """
    b.id                AS id,
    b.author_nickname   AS author_nickname,
    b.author_client_id  AS author_client_id,
    b.content           AS content,
    b.emotion_tag       AS emotion_tag,
    b.reply_mode        AS reply_mode,
    b.status            AS status,
    b.version           AS version,
    b.created_at        AS created_at,
    b.resolved_at       AS resolved_at,
    b.saved_at          AS saved_at,
    b.sync_state        AS sync_state,
    (SELECT COUNT(*) FROM replies r WHERE r.bubble_id = b.id) AS reply_count,
    (SELECT COUNT(*) FROM chat_messages c WHERE c.bubble_id = b.id) AS chat_count
"""

#: 作者判定：设备 ID 优先，旧数据（空 ID）回退昵称。
_AUTHOR_CONDITION: str = (
    "(b.author_client_id = ? OR (b.author_client_id = '' AND b.author_nickname = ?))"
)


def _escape_like(text: str) -> str:
    """转义 LIKE 通配符，避免用户输入 `%`/`_` 变成通配。"""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class Repo:
    """线程安全的客户端仓储。"""

    def __init__(self, db_path: str) -> None:
        """构造（不建表、不迁移；调用方需先跑 `migrations`）。

        Args:
            db_path: 客户端库路径。
        """
        self._db_path = os.path.abspath(db_path)
        self._local = threading.local()

    # ------------------------------------------------------------------
    # 连接与事务
    # ------------------------------------------------------------------
    @property
    def db_path(self) -> str:
        """库文件路径。"""
        return self._db_path

    def _conn(self) -> sqlite3.Connection:
        """取当前线程的连接（懒建）。"""
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

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """写事务（`BEGIN IMMEDIATE`，异常回滚）。"""
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except Exception:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")

    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        """执行单条语句（参数化）。"""
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
            logger.warning("关闭连接失败: %s", exc)
        self._local.conn = None

    # ------------------------------------------------------------------
    # 读：泡泡
    # ------------------------------------------------------------------
    def list_public(
        self,
        *,
        emotion: Optional[str] = None,
        q: Optional[str] = None,
        limit: int = PAGE_SIZE_DEFAULT,
        offset: int = 0,
    ) -> list[Bubble]:
        """公开泡泡列表（`active` / `saved_public`）。

        Args:
            emotion: 情感筛选（等值）。
            q: 关键词，匹配正文或作者昵称。
            limit: 条数上限。
            offset: 偏移（本地分页用）。

        Returns:
            `Bubble` 列表，按 `created_at DESC, id DESC` 排序。
        """
        placeholders = ", ".join("?" for _ in PUBLIC_STATUSES)
        clauses = [f"b.status IN ({placeholders})"]
        params: list[Any] = list(PUBLIC_STATUSES)
        if emotion:
            clauses.append("b.emotion_tag = ?")
            params.append(emotion)
        if q:
            clauses.append("(b.content LIKE ? ESCAPE '\\' OR b.author_nickname LIKE ? ESCAPE '\\')")
            pattern = f"%{_escape_like(q)}%"
            params.extend([pattern, pattern])
        sql = (
            f"SELECT {_BUBBLE_COLUMNS} FROM bubbles b "
            f"WHERE {' AND '.join(clauses)} "
            "ORDER BY b.created_at DESC, b.id DESC LIMIT ? OFFSET ?"
        )
        params.extend([max(1, int(limit)), max(0, int(offset))])
        return rows_to_bubbles(self.query_all(sql, params))

    def get_bubble(self, bubble_id: str) -> Optional[Bubble]:
        """按 id 取单个泡泡。"""
        sql = f"SELECT {_BUBBLE_COLUMNS} FROM bubbles b WHERE b.id = ?"
        row = self.query_one(sql, (bubble_id,))
        return row_to_bubble(row) if row is not None else None

    def list_mine(self, group: str, *, client_id: str, nickname: str) -> list[Bubble]:
        """「我的」页某一组。

        Args:
            group: `published` / `saved_public` / `saved_private` / `resolved`。
            client_id: 我的设备 ID。
            nickname: 我的昵称（旧数据回退用）。

        Returns:
            `Bubble` 列表。

        Raises:
            ValueError: group 非法。
        """
        if group not in MINE_GROUPS:
            raise ValueError(f"未知分组: {group}")
        clauses = [_AUTHOR_CONDITION]
        params: list[Any] = [client_id, nickname]
        if group == "published":
            clauses.append("b.status != 'saved_private'")
        else:
            clauses.append("b.status = ?")
            params.append(group)
        sql = (
            f"SELECT {_BUBBLE_COLUMNS} FROM bubbles b "
            f"WHERE {' AND '.join(clauses)} "
            "ORDER BY b.created_at DESC, b.id DESC"
        )
        return rows_to_bubbles(self.query_all(sql, params))

    def count_by_group(self, *, client_id: str, nickname: str) -> dict[str, int]:
        """「我的」页 4 个分组的计数。"""
        counts: dict[str, int] = {}
        for group in MINE_GROUPS:
            clauses = [_AUTHOR_CONDITION]
            params: list[Any] = [client_id, nickname]
            if group == "published":
                clauses.append("b.status != 'saved_private'")
            else:
                clauses.append("b.status = ?")
                params.append(group)
            sql = (
                f"SELECT COUNT(*) AS n FROM bubbles b WHERE {' AND '.join(clauses)}"
            )
            row = self.query_one(sql, params)
            counts[group] = int(row["n"]) if row is not None else 0
        return counts

    # ------------------------------------------------------------------
    # 读：回复 / 对话
    # ------------------------------------------------------------------
    def list_replies(self, bubble_id: str) -> list[Reply]:
        """某泡泡的全部回复（时间升序）。"""
        sql = (
            "SELECT id, bubble_id, replier_nickname, replier_client_id, content, "
            "created_at, op_id, sync_state FROM replies WHERE bubble_id = ? "
            "ORDER BY created_at ASC, id ASC"
        )
        return rows_to_replies(self.query_all(sql, (bubble_id,)))

    def list_chats(self, bubble_id: str, *, peer: Optional[str] = None) -> list[ChatMessage]:
        """某泡泡的对话消息。

        Args:
            bubble_id: 泡泡 id。
            peer: 只取与该昵称相关的消息（发送或接收），为空则全部。

        Returns:
            时间升序的消息列表。
        """
        sql = (
            "SELECT id, bubble_id, sender_nickname, sender_client_id, receiver_nickname, "
            "receiver_client_id, content, created_at, op_id, sync_state "
            "FROM chat_messages WHERE bubble_id = ?"
        )
        params: list[Any] = [bubble_id]
        if peer:
            sql += " AND (sender_nickname = ? OR receiver_nickname = ?)"
            params.extend([peer, peer])
        sql += " ORDER BY created_at ASC, id ASC"
        return rows_to_chats(self.query_all(sql, params))

    def my_reply_count(self, bubble_id: str, client_id: str, nickname: str) -> int:
        """我在该泡泡下已回复的次数（设备 ID 优先，旧数据回退昵称）。"""
        sql = (
            "SELECT COUNT(*) AS n FROM replies WHERE bubble_id = ? "
            "AND (replier_client_id = ? OR (replier_client_id = '' AND replier_nickname = ?))"
        )
        row = self.query_one(sql, (bubble_id, client_id, nickname))
        return int(row["n"]) if row is not None else 0

    def has_chatted(self, bubble_id: str, client_id: str, nickname: str) -> bool:
        """我是否在该泡泡下参与过对话。"""
        sql = (
            "SELECT 1 AS hit FROM chat_messages WHERE bubble_id = ? AND ("
            " sender_client_id = ? OR receiver_client_id = ?"
            " OR (sender_client_id = '' AND sender_nickname = ?)"
            ") LIMIT 1"
        )
        return self.query_one(sql, (bubble_id, client_id, client_id, nickname)) is not None

    def pending_count(self) -> int:
        """待同步条数（`pending` + `syncing`）。"""
        row = self.query_one(
            "SELECT COUNT(*) AS n FROM outbox WHERE state IN ('pending','syncing')"
        )
        return int(row["n"]) if row is not None else 0

    # ------------------------------------------------------------------
    # 写：泡泡 / 回复 / 对话
    # ------------------------------------------------------------------
    def upsert_bubble(self, bubble: Bubble) -> None:
        """插入或更新泡泡。

        冲突时**保留本地 `pending` 状态**：服务端拉取不能把"待同步"冲掉。
        """
        sql = (
            "INSERT INTO bubbles (id, author_nickname, author_client_id, content, emotion_tag, "
            "reply_mode, status, version, created_at, resolved_at, saved_at, sync_state) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "author_nickname = excluded.author_nickname, "
            "author_client_id = excluded.author_client_id, "
            "content = excluded.content, "
            "emotion_tag = excluded.emotion_tag, "
            "reply_mode = excluded.reply_mode, "
            "status = excluded.status, "
            "version = excluded.version, "
            "created_at = excluded.created_at, "
            "resolved_at = excluded.resolved_at, "
            "saved_at = excluded.saved_at, "
            "sync_state = CASE WHEN bubbles.sync_state = 'pending' THEN 'pending' "
            "ELSE excluded.sync_state END"
        )
        self.execute(
            sql,
            (
                bubble.id,
                bubble.author_nickname,
                bubble.author_client_id,
                bubble.content,
                bubble.emotion_tag,
                bubble.reply_mode,
                bubble.status or DEFAULT_STATUS,
                bubble.version,
                bubble.created_at,
                bubble.resolved_at,
                bubble.saved_at,
                bubble.sync_state or SYNC_STATE_SYNCED,
            ),
        )

    def upsert_reply(self, reply: Reply) -> None:
        """插入或更新回复（按 `op_id` 去重）。

        Raises:
            ValueError: `op_id` 为空（否则唯一索引无法区分两条本地乐观回复）。
        """
        if not reply.op_id:
            raise ValueError("回复必须带 op_id 才能幂等写入")
        sql = (
            "INSERT INTO replies (bubble_id, replier_nickname, replier_client_id, content, "
            "created_at, op_id, sync_state) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(op_id) DO UPDATE SET "
            "content = excluded.content, created_at = excluded.created_at, "
            "sync_state = excluded.sync_state"
        )
        self.execute(
            sql,
            (
                reply.bubble_id,
                reply.replier_nickname,
                reply.replier_client_id,
                reply.content,
                reply.created_at,
                reply.op_id,
                reply.sync_state or SYNC_STATE_SYNCED,
            ),
        )

    def upsert_chat(self, message: ChatMessage) -> None:
        """插入或更新对话消息（按 `op_id` 去重）。

        Raises:
            ValueError: `op_id` 为空。
        """
        if not message.op_id:
            raise ValueError("对话消息必须带 op_id 才能幂等写入")
        sql = (
            "INSERT INTO chat_messages (bubble_id, sender_nickname, sender_client_id, "
            "receiver_nickname, receiver_client_id, content, created_at, op_id, sync_state) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(op_id) DO UPDATE SET "
            "content = excluded.content, created_at = excluded.created_at, "
            "sync_state = excluded.sync_state"
        )
        self.execute(
            sql,
            (
                message.bubble_id,
                message.sender_nickname,
                message.sender_client_id,
                message.receiver_nickname,
                message.receiver_client_id,
                message.content,
                message.created_at,
                message.op_id,
                message.sync_state or SYNC_STATE_SYNCED,
            ),
        )

    def mark_bubble_status(self, bubble_id: str, status: str, *, version: int, at: str) -> None:
        """更新泡泡状态与版本。

        Args:
            bubble_id: 泡泡 id。
            status: 目标状态。
            version: 新版本号。
            at: 变更时间（`resolved` 写 `resolved_at`，`saved_*` 写 `saved_at`）。
        """
        if status == "resolved":
            self.execute(
                "UPDATE bubbles SET status = ?, version = ?, resolved_at = ? WHERE id = ?",
                (status, version, at, bubble_id),
            )
        elif status in ("saved_public", "saved_private"):
            self.execute(
                "UPDATE bubbles SET status = ?, version = ?, saved_at = ? WHERE id = ?",
                (status, version, at, bubble_id),
            )
        else:
            self.execute(
                "UPDATE bubbles SET status = ?, version = ?, resolved_at = NULL, "
                "saved_at = NULL WHERE id = ?",
                (status, version, bubble_id),
            )

    def set_bubble_sync_state(self, bubble_id: str, state: str) -> None:
        """更新泡泡同步状态。"""
        self.execute("UPDATE bubbles SET sync_state = ? WHERE id = ?", (state, bubble_id))

    def set_reply_sync_state(self, op_id: str, state: str) -> None:
        """更新回复同步状态。"""
        self.execute("UPDATE replies SET sync_state = ? WHERE op_id = ?", (state, op_id))

    def set_chat_sync_state(self, op_id: str, state: str) -> None:
        """更新对话同步状态。"""
        self.execute("UPDATE chat_messages SET sync_state = ? WHERE op_id = ?", (state, op_id))

    def delete_reply_by_op(self, op_id: str) -> int:
        """按 `op_id` 删除本地乐观回复，返回删除行数。"""
        cursor = self.execute("DELETE FROM replies WHERE op_id = ?", (op_id,))
        return cursor.rowcount

    def delete_chat_by_op(self, op_id: str) -> int:
        """按 `op_id` 删除本地乐观消息，返回删除行数。"""
        cursor = self.execute("DELETE FROM chat_messages WHERE op_id = ?", (op_id,))
        return cursor.rowcount

    def delete_bubble(self, bubble_id: str) -> int:
        """删除泡泡（含级联删除回复与对话）。"""
        cursor = self.execute("DELETE FROM bubbles WHERE id = ?", (bubble_id,))
        return cursor.rowcount

    # ------------------------------------------------------------------
    # meta
    # ------------------------------------------------------------------
    def get_meta(self, key: str, default: str = "") -> str:
        """读取 `meta` 键。"""
        row = self.query_one("SELECT value FROM meta WHERE key = ?", (key,))
        if row is None:
            return default
        return str(row["value"])

    def set_meta(self, key: str, value: str) -> None:
        """写入 `meta` 键。"""
        self.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, str(value))
        )

    # ------------------------------------------------------------------
    # 便捷
    # ------------------------------------------------------------------
    @classmethod
    def from_default_path(cls, db_path: str, backup_dir: Optional[str] = None) -> "Repo":
        """先确保 schema 再返回仓储实例（首次启动路径）。

        Args:
            db_path: 客户端库路径。
            backup_dir: 迁移备份目录。

        Returns:
            `Repo` 实例。
        """
        ensure_client_schema(db_path, backup_dir)
        return cls(db_path)


__all__ = ["Repo"]
