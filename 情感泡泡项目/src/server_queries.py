"""服务端仓储的内容查询部分（`ContentQueriesMixin`）。

从 `server_store.py` 拆出，目的：单文件 ≤ 500 行（铁律 5）。
宿主 `ServerStore` 提供 `_conn / query_all / query_one / execute`。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from src.core.constants import (
    DEFAULT_STATUS,
    MINE_GROUPS,
    PAGE_SIZE_DEFAULT,
    PAGE_SIZE_MAX,
    PUBLIC_STATUSES,
)
from src.core.models import (
    Bubble, ChatMessage, Reply, decode_cursor, encode_cursor, row_to_bubble,
    row_to_chat, row_to_reply, rows_to_bubbles, rows_to_chats, rows_to_replies,
)

@dataclass(frozen=True)
class Page:
    """一页泡泡数据。"""

    items: list[Bubble]
    total: int
    has_more: bool
    next_cursor: Optional[str] = None


# ---------------------------------------------------------------------------
# 仓储
# ---------------------------------------------------------------------------

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
    (SELECT COUNT(*) FROM replies r WHERE r.bubble_id = b.id) AS reply_count,
    (SELECT COUNT(*) FROM chat_messages c WHERE c.bubble_id = b.id) AS chat_count
"""

_REPLY_COLUMNS: str = (
    "id, bubble_id, replier_nickname, replier_client_id, content, created_at, op_id"
)

_CHAT_COLUMNS: str = (
    "id, bubble_id, sender_nickname, sender_client_id, receiver_nickname, "
    "receiver_client_id, content, created_at, op_id"
)

_AUTHOR_CONDITION: str = (
    "(b.author_client_id = ? OR (b.author_client_id = '' AND b.author_nickname = ?))"
)


def _escape_like(text: str) -> str:
    """转义 LIKE 通配符。"""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")



class ContentQueriesMixin:
    """泡泡 / 回复 / 对话的查询与写入。

    依赖宿主提供：`_conn()`、`query_all()`、`query_one()`、`execute()`。
    """

    # ------------------------------------------------------------------
    # bubbles
    # ------------------------------------------------------------------
    def create_bubble(
        self,
        *,
        bubble_id: str,
        author_nickname: str,
        author_client_id: str,
        content: str,
        emotion_tag: str,
        reply_mode: str,
        created_at: str,
        status: str = DEFAULT_STATUS,
    ) -> Bubble:
        """插入泡泡（版本从 1 开始）。"""
        self.execute(
            "INSERT INTO bubbles (id, author_nickname, author_client_id, content, emotion_tag, "
            "reply_mode, status, version, created_at, resolved_at, saved_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, NULL, NULL)",
            (
                bubble_id,
                author_nickname,
                author_client_id,
                content,
                emotion_tag,
                reply_mode,
                status,
                created_at,
            ),
        )
        bubble = self.get_bubble(bubble_id)
        assert bubble is not None
        return bubble

    def get_bubble(self, bubble_id: str) -> Optional[Bubble]:
        """按 id 取泡泡。"""
        row = self.query_one(
            f"SELECT {_BUBBLE_COLUMNS} FROM bubbles b WHERE b.id = ?", (bubble_id,)
        )
        return row_to_bubble(row) if row is not None else None

    def bubble_exists(self, bubble_id: str) -> bool:
        """是否存在。"""
        return self.query_one("SELECT 1 AS hit FROM bubbles WHERE id = ?", (bubble_id,)) is not None

    def _build_filters(
        self,
        *,
        emotion: Optional[str],
        q: Optional[str],
        since: Optional[str],
        cursor: Optional[str],
        author_client_id: Optional[str] = None,
        author_nickname: Optional[str] = None,
        status: Optional[str] = None,
        exclude_private: bool = False,
    ) -> tuple[list[str], list[Any], Optional[tuple[str, str]]]:
        """构造公共 WHERE 片段。

        Returns:
            `(clauses, params, cursor_pair)`。
        """
        clauses: list[str] = []
        params: list[Any] = []
        if author_client_id is not None:
            clauses.append(_AUTHOR_CONDITION)
            params.extend([author_client_id, author_nickname or ""])
        if status is not None:
            clauses.append("b.status = ?")
            params.append(status)
        elif exclude_private:
            clauses.append("b.status != 'saved_private'")
        else:
            placeholders = ", ".join("?" for _ in PUBLIC_STATUSES)
            clauses.append(f"b.status IN ({placeholders})")
            params.extend(PUBLIC_STATUSES)
        if emotion:
            clauses.append("b.emotion_tag = ?")
            params.append(emotion)
        if q:
            clauses.append(
                "(b.content LIKE ? ESCAPE '\\' OR b.author_nickname LIKE ? ESCAPE '\\')"
            )
            pattern = f"%{_escape_like(q)}%"
            params.extend([pattern, pattern])
        if since:
            clauses.append("b.created_at > ?")
            params.append(since)
        cursor_pair: Optional[tuple[str, str]] = None
        if cursor:
            cursor_pair = decode_cursor(cursor)
        return clauses, params, cursor_pair

    def list_bubbles(
        self,
        *,
        limit: int = PAGE_SIZE_DEFAULT,
        cursor: Optional[str] = None,
        emotion: Optional[str] = None,
        q: Optional[str] = None,
        since: Optional[str] = None,
    ) -> Page:
        """公开泡泡分页（E03）。

        Args:
            limit: 每页条数（1~100）。
            cursor: 分页游标。
            emotion: 情感筛选。
            q: 关键词。
            since: 增量时间下限（`created_at > since`）。

        Returns:
            `Page`。
        """
        safe_limit = max(1, min(int(limit), PAGE_SIZE_MAX))
        clauses, params, cursor_pair = self._build_filters(
            emotion=emotion, q=q, since=since, cursor=cursor
        )
        total_row = self.query_one(
            f"SELECT COUNT(*) AS n FROM bubbles b WHERE {' AND '.join(clauses)}", params
        )
        total = int(total_row["n"]) if total_row is not None else 0

        page_clauses = list(clauses)
        page_params = list(params)
        if cursor_pair is not None:
            page_clauses.append("(b.created_at, b.id) < (?, ?)")
            page_params.extend(list(cursor_pair))
        sql = (
            f"SELECT {_BUBBLE_COLUMNS} FROM bubbles b "
            f"WHERE {' AND '.join(page_clauses)} "
            "ORDER BY b.created_at DESC, b.id DESC LIMIT ?"
        )
        page_params.append(safe_limit + 1)
        rows = self.query_all(sql, page_params)
        bubbles = rows_to_bubbles(rows)
        has_more = len(bubbles) > safe_limit
        items = bubbles[:safe_limit]
        next_cursor = (
            encode_cursor(items[-1].created_at, items[-1].id) if has_more and items else None
        )
        return Page(items=items, total=total, has_more=has_more, next_cursor=next_cursor)

    def list_mine(
        self,
        group: str,
        *,
        client_id: str,
        nickname: str,
        limit: int = PAGE_SIZE_DEFAULT,
        cursor: Optional[str] = None,
    ) -> Page:
        """「我的」分组分页（E11）。

        Args:
            group: `published` / `saved_public` / `saved_private` / `resolved`。
            client_id: 我的设备 ID。
            nickname: 我的昵称（旧数据回退）。
            limit: 每页条数。
            cursor: 分页游标。

        Returns:
            `Page`。

        Raises:
            ValueError: group 非法。
        """
        if group not in MINE_GROUPS:
            raise ValueError(f"未知分组: {group}")
        safe_limit = max(1, min(int(limit), PAGE_SIZE_MAX))
        clauses, params, cursor_pair = self._build_filters(
            emotion=None,
            q=None,
            since=None,
            cursor=cursor,
            author_client_id=client_id,
            author_nickname=nickname,
            status=None if group == "published" else group,
            exclude_private=group == "published",
        )
        total_row = self.query_one(
            f"SELECT COUNT(*) AS n FROM bubbles b WHERE {' AND '.join(clauses)}", params
        )
        total = int(total_row["n"]) if total_row is not None else 0
        page_clauses = list(clauses)
        page_params = list(params)
        if cursor_pair is not None:
            page_clauses.append("(b.created_at, b.id) < (?, ?)")
            page_params.extend(list(cursor_pair))
        sql = (
            f"SELECT {_BUBBLE_COLUMNS} FROM bubbles b "
            f"WHERE {' AND '.join(page_clauses)} "
            "ORDER BY b.created_at DESC, b.id DESC LIMIT ?"
        )
        page_params.append(safe_limit + 1)
        bubbles = rows_to_bubbles(self.query_all(sql, page_params))
        has_more = len(bubbles) > safe_limit
        items = bubbles[:safe_limit]
        next_cursor = (
            encode_cursor(items[-1].created_at, items[-1].id) if has_more and items else None
        )
        return Page(items=items, total=total, has_more=has_more, next_cursor=next_cursor)

    def count_by_group(self, *, client_id: str, nickname: str) -> dict[str, int]:
        """「我的」4 组计数（E11 的 `counts`）。"""
        counts: dict[str, int] = {}
        for group in MINE_GROUPS:
            clauses, params, _ = self._build_filters(
                emotion=None,
                q=None,
                since=None,
                cursor=None,
                author_client_id=client_id,
                author_nickname=nickname,
                status=None if group == "published" else group,
                exclude_private=group == "published",
            )
            row = self.query_one(
                f"SELECT COUNT(*) AS n FROM bubbles b WHERE {' AND '.join(clauses)}", params
            )
            counts[group] = int(row["n"]) if row is not None else 0
        return counts

    def set_bubble_status(self, bubble_id: str, status: str, *, at: str) -> Optional[Bubble]:
        """改状态并 `version + 1`（作者无条件覆盖，Q-4）。

        Args:
            bubble_id: 泡泡 id。
            status: 目标状态。
            at: 变更时间。

        Returns:
            更新后的 `Bubble`（不存在返回 None）。
        """
        if status == "resolved":
            self.execute(
                "UPDATE bubbles SET status = ?, version = version + 1, resolved_at = ? "
                "WHERE id = ?",
                (status, at, bubble_id),
            )
        elif status in ("saved_public", "saved_private"):
            self.execute(
                "UPDATE bubbles SET status = ?, version = version + 1, saved_at = ? "
                "WHERE id = ?",
                (status, at, bubble_id),
            )
        else:
            self.execute(
                "UPDATE bubbles SET status = ?, version = version + 1, "
                "resolved_at = NULL, saved_at = NULL WHERE id = ?",
                (status, bubble_id),
            )
        return self.get_bubble(bubble_id)

    def bump_version(self, bubble_id: str) -> int:
        """`version + 1` 并返回新版本。"""
        self.execute("UPDATE bubbles SET version = version + 1 WHERE id = ?", (bubble_id,))
        row = self.query_one("SELECT version FROM bubbles WHERE id = ?", (bubble_id,))
        return int(row["version"]) if row is not None else 0

    # ------------------------------------------------------------------
    # replies
    # ------------------------------------------------------------------
    def add_reply(
        self,
        *,
        bubble_id: str,
        replier_nickname: str,
        replier_client_id: str,
        content: str,
        created_at: str,
        op_id: str,
    ) -> Reply:
        """插入回复（同 `op_id` 幂等）。"""
        self.execute(
            "INSERT INTO replies (bubble_id, replier_nickname, replier_client_id, content, "
            "created_at, op_id) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(op_id) DO NOTHING",
            (bubble_id, replier_nickname, replier_client_id, content, created_at, op_id),
        )
        row = self.query_one(
            f"SELECT {_REPLY_COLUMNS} FROM replies WHERE op_id = ?", (op_id,)
        )
        assert row is not None
        return row_to_reply(row)

    def list_replies(self, bubble_id: str) -> list[Reply]:
        """某泡泡的回复（时间升序）。"""
        rows = self.query_all(
            f"SELECT {_REPLY_COLUMNS} FROM replies WHERE bubble_id = ? "
            "ORDER BY created_at ASC, id ASC",
            (bubble_id,),
        )
        return rows_to_replies(rows)

    def count_replies_by_client(self, bubble_id: str, client_id: str, nickname: str) -> int:
        """某设备在某泡泡下的回复数（旧数据回退昵称）。"""
        row = self.query_one(
            "SELECT COUNT(*) AS n FROM replies WHERE bubble_id = ? "
            "AND (replier_client_id = ? OR (replier_client_id = '' AND replier_nickname = ?))",
            (bubble_id, client_id, nickname),
        )
        return int(row["n"]) if row is not None else 0

    def get_my_reply(self, bubble_id: str, client_id: str, nickname: str) -> Optional[Reply]:
        """取我在该泡泡下的第一条回复。"""
        row = self.query_one(
            f"SELECT {_REPLY_COLUMNS} FROM replies WHERE bubble_id = ? "
            "AND (replier_client_id = ? OR (replier_client_id = '' AND replier_nickname = ?)) "
            "ORDER BY created_at ASC, id ASC LIMIT 1",
            (bubble_id, client_id, nickname),
        )
        return row_to_reply(row) if row is not None else None

    def count_replies(self, bubble_id: str) -> int:
        """某泡泡的回复总数。"""
        row = self.query_one(
            "SELECT COUNT(*) AS n FROM replies WHERE bubble_id = ?", (bubble_id,)
        )
        return int(row["n"]) if row is not None else 0

    # ------------------------------------------------------------------
    # chats
    # ------------------------------------------------------------------
    def add_chat(
        self,
        *,
        bubble_id: str,
        sender_nickname: str,
        sender_client_id: str,
        receiver_nickname: str,
        receiver_client_id: Optional[str],
        content: str,
        created_at: str,
        op_id: str,
    ) -> ChatMessage:
        """插入对话消息（同 `op_id` 幂等）。"""
        self.execute(
            "INSERT INTO chat_messages (bubble_id, sender_nickname, sender_client_id, "
            "receiver_nickname, receiver_client_id, content, created_at, op_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(op_id) DO NOTHING",
            (
                bubble_id,
                sender_nickname,
                sender_client_id,
                receiver_nickname,
                receiver_client_id,
                content,
                created_at,
                op_id,
            ),
        )
        row = self.query_one(
            f"SELECT {_CHAT_COLUMNS} FROM chat_messages WHERE op_id = ?", (op_id,)
        )
        assert row is not None
        return row_to_chat(row)

    def list_chats(
        self,
        bubble_id: str,
        *,
        client_id: Optional[str] = None,
        nickname: Optional[str] = None,
        peer: Optional[str] = None,
    ) -> list[ChatMessage]:
        """某泡泡的对话消息。

        Args:
            bubble_id: 泡泡 id。
            client_id: 只看该设备参与的（None = 全部，仅作者可用）。
            nickname: 旧数据回退昵称。
            peer: 只看与该昵称相关的消息。

        Returns:
            时间升序消息列表。
        """
        sql = f"SELECT {_CHAT_COLUMNS} FROM chat_messages WHERE bubble_id = ?"
        params: list[Any] = [bubble_id]
        if client_id is not None:
            sql += (
                " AND (sender_client_id = ? OR receiver_client_id = ?"
                " OR (sender_client_id = '' AND sender_nickname = ?))"
            )
            params.extend([client_id, client_id, nickname or ""])
        if peer:
            sql += " AND (sender_nickname = ? OR receiver_nickname = ?)"
            params.extend([peer, peer])
        sql += " ORDER BY created_at ASC, id ASC"
        return rows_to_chats(self.query_all(sql, params))

    def has_chatted(self, bubble_id: str, client_id: str, nickname: str) -> bool:
        """该设备是否在该泡泡下参与过对话。"""
        row = self.query_one(
            "SELECT 1 AS hit FROM chat_messages WHERE bubble_id = ? AND ("
            " sender_client_id = ? OR receiver_client_id = ?"
            " OR (sender_client_id = '' AND sender_nickname = ?)) LIMIT 1",
            (bubble_id, client_id, client_id, nickname),
        )
        return row is not None

    def count_chats(self, bubble_id: str) -> int:
        """某泡泡的对话消息数。"""
        row = self.query_one(
            "SELECT COUNT(*) AS n FROM chat_messages WHERE bubble_id = ?", (bubble_id,)
        )
        return int(row["n"]) if row is not None else 0


__all__ = ["Page", "ContentQueriesMixin"]
