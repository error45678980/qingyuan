"""具名数据模型与行/传输转换（架构文档 §2）。

硬规则：
    * D-1 所有数据库读取必须 `row_factory = sqlite3.Row` + 显式列名；
    * D-2 Row → dataclass 的**唯一入口**是本模块，禁止 `row[i]` 位置下标；
    * 缺列必须抛 `SchemaMismatchError`，**禁止静默补默认值**（v1 静默错位的反面）。
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional

from .constants import (
    DEFAULT_REPLY_MODE,
    DEFAULT_STATUS,
    PAGE_SIZE_DEFAULT,
    PREVIEW_LEN,
    PUBLIC_STATUSES,
    SYNC_STATE_SYNCED,
)
from .errors import InvalidCursorError, SchemaMismatchError
from .timeutil import fmt_display, fmt_face, fmt_short

# ---------------------------------------------------------------------------
# 必需列清单（缺一即抛 SchemaMismatchError）
# ---------------------------------------------------------------------------

BUBBLE_REQUIRED_COLUMNS: tuple[str, ...] = (
    "id",
    "author_nickname",
    "author_client_id",
    "content",
    "emotion_tag",
    "reply_mode",
    "status",
    "created_at",
)

REPLY_REQUIRED_COLUMNS: tuple[str, ...] = (
    "id",
    "bubble_id",
    "replier_nickname",
    "replier_client_id",
    "content",
    "created_at",
)

CHAT_REQUIRED_COLUMNS: tuple[str, ...] = (
    "id",
    "bubble_id",
    "sender_nickname",
    "sender_client_id",
    "receiver_nickname",
    "content",
    "created_at",
)

OUTBOX_REQUIRED_COLUMNS: tuple[str, ...] = (
    "id",
    "op_id",
    "op_type",
    "bubble_id",
    "payload",
    "state",
    "attempts",
    "next_attempt_at",
    "created_at",
    "updated_at",
)


def _keys(row: Mapping[str, Any]) -> set[str]:
    """取行对象的列名集合（兼容 `sqlite3.Row` 与普通 dict）。"""
    try:
        return set(row.keys())  # type: ignore[attr-defined]
    except AttributeError as exc:
        raise SchemaMismatchError("数据库行不是 Mapping / sqlite3.Row，无法按列名读取") from exc


def _require(row: Mapping[str, Any], table: str, required: Iterable[str]) -> set[str]:
    """断言必需列齐全，返回列名集合。"""
    keys = _keys(row)
    missing = [name for name in required if name not in keys]
    if missing:
        raise SchemaMismatchError(
            f"{table} 缺少必需列: {', '.join(missing)}（实际列: {', '.join(sorted(keys))}）"
        )
    return keys


def _as_int(value: Any, default: int = 0) -> int:
    """宽松地把列值转 int（NULL → default）。"""
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_opt_str(value: Any) -> Optional[str]:
    """把列值转成 `Optional[str]`，空串视为 None。"""
    if value is None:
        return None
    text = str(value)
    return text if text else None


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Bubble:
    """一个泡泡。

    Attributes:
        id: 主键（UUID4 或历史 id 如 `test-pub-001`）。
        author_nickname: 展示昵称。
        author_client_id: 作者设备 ID；旧数据为空串（回退昵称判定）。
        content: 正文 1~500 字。
        emotion_tag: `constants.EMOTIONS` 之一。
        reply_mode: `single` | `dialog`。
        status: `active` | `resolved` | `saved_public` | `saved_private`。
        version: 服务端每次变更 +1（Q-4）。
        created_at: 存储格式时间串。
        resolved_at: 标记已解决时间。
        saved_at: 公开/私密保留时间（与 `resolved_at` 分离）。
        sync_state: 仅客户端：`synced` | `pending` | `failed`。
        reply_count: 计算字段（非数据库列）。
        chat_count: 计算字段（非数据库列）。
        is_owner: 服务端下发的作者标志（**权限只信服务端**，UI 不自行推导）。
        can_reply: 服务端下发的可回复标志。
        can_chat: 服务端下发的可对话标志。
        perms_known: 是否来自带权限标志的服务端 DTO（离线读库时为 False）。
    """

    id: str
    author_nickname: str
    author_client_id: str
    content: str
    emotion_tag: str
    reply_mode: str
    status: str
    version: int = 1
    created_at: str = ""
    resolved_at: Optional[str] = None
    saved_at: Optional[str] = None
    sync_state: str = SYNC_STATE_SYNCED
    reply_count: int = 0
    chat_count: int = 0
    is_owner: bool = False
    can_reply: bool = False
    can_chat: bool = False
    perms_known: bool = False

    # -- 派生属性 ---------------------------------------------------------
    @property
    def is_public_visible(self) -> bool:
        """是否出现在公开泡泡墙（`active` / `saved_public`）。"""
        return self.status in PUBLIC_STATUSES

    @property
    def is_private(self) -> bool:
        """是否私密保存。"""
        return self.status == "saved_private"

    @property
    def is_resolved(self) -> bool:
        """是否已解决。"""
        return self.status == "resolved"

    @property
    def content_preview(self) -> str:
        """泡泡正面展示用的短文本：压缩空白 + 截断到 `PREVIEW_LEN` 字。"""
        flat = " ".join(self.content.split())
        if len(flat) <= PREVIEW_LEN:
            return flat
        return flat[:PREVIEW_LEN] + "…"

    @property
    def display_time(self) -> str:
        """展示格式时间 `YYYY-MM-DD HH:MM`。"""
        return fmt_display(self.created_at)

    @property
    def short_time(self) -> str:
        """对话内短格式 `MM-DD HH:MM`。"""
        return fmt_short(self.created_at)

    @property
    def face_time(self) -> str:
        """泡泡正面（圆形底部窄带）用的极短时间：今天 `HH:MM`、今年 `MM-DD`、跨年 `YYYY-MM`。"""
        return fmt_face(self.created_at)

    @property
    def is_pending(self) -> bool:
        """是否待同步。"""
        return self.sync_state != SYNC_STATE_SYNCED

    @property
    def diff_key(self) -> tuple:
        """UI 按 id diff 复用 widget 时的比较键（R-14）。"""
        return (
            self.content,
            self.emotion_tag,
            self.reply_mode,
            self.status,
            self.version,
            self.reply_count,
        )


@dataclass(frozen=True)
class Reply:
    """一条回复。"""

    id: int
    bubble_id: str
    replier_nickname: str
    replier_client_id: str
    content: str
    created_at: str
    op_id: str = ""
    sync_state: str = SYNC_STATE_SYNCED

    @property
    def display_time(self) -> str:
        """展示格式时间。"""
        return fmt_display(self.created_at)

    @property
    def short_time(self) -> str:
        """对话内短格式。"""
        return fmt_short(self.created_at)


@dataclass(frozen=True)
class ChatMessage:
    """一条对话消息。"""

    id: int
    bubble_id: str
    sender_nickname: str
    sender_client_id: str
    receiver_nickname: str
    content: str
    created_at: str
    receiver_client_id: Optional[str] = None
    op_id: str = ""
    sync_state: str = SYNC_STATE_SYNCED

    @property
    def display_time(self) -> str:
        """展示格式时间。"""
        return fmt_display(self.created_at)

    @property
    def short_time(self) -> str:
        """对话内短格式。"""
        return fmt_short(self.created_at)


@dataclass(frozen=True)
class OutboxItem:
    """离线队列中的一条待同步操作。"""

    id: int
    op_id: str
    op_type: str
    bubble_id: str
    payload: str
    base_version: int = 0
    state: str = "pending"
    attempts: int = 0
    next_attempt_at: str = ""
    last_error: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""


# ---------------------------------------------------------------------------
# Row → dataclass（唯一入口）
# ---------------------------------------------------------------------------


def row_to_bubble(row: Mapping[str, Any]) -> Bubble:
    """把一行 `bubbles` 记录转成 `Bubble`。

    Args:
        row: `sqlite3.Row` 或 dict，必须显式包含 `BUBBLE_REQUIRED_COLUMNS`。

    Returns:
        `Bubble` 实例。

    Raises:
        SchemaMismatchError: 缺少必需列（**不做兜底**，兜底会掩盖迁移失败）。
    """
    keys = _require(row, "bubbles", BUBBLE_REQUIRED_COLUMNS)
    return Bubble(
        id=str(row["id"]),
        author_nickname=str(row["author_nickname"]),
        author_client_id=str(row["author_client_id"]),
        content=str(row["content"]),
        emotion_tag=str(row["emotion_tag"]),
        reply_mode=str(row["reply_mode"]),
        status=str(row["status"]),
        version=_as_int(row["version"], 1) if "version" in keys else 1,
        created_at=str(row["created_at"]),
        resolved_at=_as_opt_str(row["resolved_at"]) if "resolved_at" in keys else None,
        saved_at=_as_opt_str(row["saved_at"]) if "saved_at" in keys else None,
        sync_state=str(row["sync_state"]) if "sync_state" in keys else SYNC_STATE_SYNCED,
        reply_count=_as_int(row["reply_count"], 0) if "reply_count" in keys else 0,
        chat_count=_as_int(row["chat_count"], 0) if "chat_count" in keys else 0,
    )


def row_to_reply(row: Mapping[str, Any]) -> Reply:
    """把一行 `replies` 记录转成 `Reply`。"""
    keys = _require(row, "replies", REPLY_REQUIRED_COLUMNS)
    return Reply(
        id=_as_int(row["id"]),
        bubble_id=str(row["bubble_id"]),
        replier_nickname=str(row["replier_nickname"]),
        replier_client_id=str(row["replier_client_id"]),
        content=str(row["content"]),
        created_at=str(row["created_at"]),
        op_id=str(row["op_id"]) if "op_id" in keys and row["op_id"] is not None else "",
        sync_state=str(row["sync_state"]) if "sync_state" in keys else SYNC_STATE_SYNCED,
    )


def row_to_chat(row: Mapping[str, Any]) -> ChatMessage:
    """把一行 `chat_messages` 记录转成 `ChatMessage`。"""
    keys = _require(row, "chat_messages", CHAT_REQUIRED_COLUMNS)
    return ChatMessage(
        id=_as_int(row["id"]),
        bubble_id=str(row["bubble_id"]),
        sender_nickname=str(row["sender_nickname"]),
        sender_client_id=str(row["sender_client_id"]),
        receiver_nickname=str(row["receiver_nickname"]),
        content=str(row["content"]),
        created_at=str(row["created_at"]),
        receiver_client_id=(
            _as_opt_str(row["receiver_client_id"]) if "receiver_client_id" in keys else None
        ),
        op_id=str(row["op_id"]) if "op_id" in keys and row["op_id"] is not None else "",
        sync_state=str(row["sync_state"]) if "sync_state" in keys else SYNC_STATE_SYNCED,
    )


def row_to_outbox(row: Mapping[str, Any]) -> OutboxItem:
    """把一行 `outbox` 记录转成 `OutboxItem`。"""
    keys = _require(row, "outbox", OUTBOX_REQUIRED_COLUMNS)
    return OutboxItem(
        id=_as_int(row["id"]),
        op_id=str(row["op_id"]),
        op_type=str(row["op_type"]),
        bubble_id=str(row["bubble_id"]),
        payload=str(row["payload"]),
        base_version=_as_int(row["base_version"], 0) if "base_version" in keys else 0,
        state=str(row["state"]),
        attempts=_as_int(row["attempts"]),
        next_attempt_at=str(row["next_attempt_at"]),
        last_error=_as_opt_str(row["last_error"]) if "last_error" in keys else None,
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def rows_to_bubbles(rows: Iterable[Mapping[str, Any]]) -> list[Bubble]:
    """批量转换泡泡行。"""
    return [row_to_bubble(row) for row in rows]


def rows_to_replies(rows: Iterable[Mapping[str, Any]]) -> list[Reply]:
    """批量转换回复行。"""
    return [row_to_reply(row) for row in rows]


def rows_to_chats(rows: Iterable[Mapping[str, Any]]) -> list[ChatMessage]:
    """批量转换对话行。"""
    return [row_to_chat(row) for row in rows]


def rows_to_outbox(rows: Iterable[Mapping[str, Any]]) -> list[OutboxItem]:
    """批量转换队列行。"""
    return [row_to_outbox(row) for row in rows]


# ---------------------------------------------------------------------------
# 分页游标（§3.4）
# ---------------------------------------------------------------------------


def encode_cursor(created_at: str, bubble_id: str) -> str:
    """把 `(created_at, bubble_id)` 编码为 base64url 游标（无填充）。

    Args:
        created_at: 排序主键。
        bubble_id: 排序次键（保证同秒记录不跳页）。

    Returns:
        可安全放进 URL 查询串的字符串。
    """
    raw = f"{created_at}|{bubble_id}".encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> tuple[str, str]:
    """游标解码。

    Args:
        cursor: `encode_cursor` 的输出。

    Returns:
        `(created_at, bubble_id)`。

    Raises:
        InvalidCursorError: 非 base64url、非 UTF-8、缺分隔符或字段为空。
    """
    if not cursor:
        raise InvalidCursorError("游标为空")
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        text = raw.decode("utf-8")
    except (binascii.Error, UnicodeError, ValueError) as exc:
        raise InvalidCursorError("游标格式不正确") from exc
    if "|" not in text:
        raise InvalidCursorError("游标缺少分隔符")
    created_at, bubble_id = text.split("|", 1)
    if not created_at or not bubble_id:
        raise InvalidCursorError("游标内容为空")
    return created_at, bubble_id


def normalize_limit(limit: Any, *, default: int = PAGE_SIZE_DEFAULT) -> int:
    """把外部传入的 limit 规整到 `[1, PAGE_SIZE_MAX]`。

    Args:
        limit: 原始值（可能为 None / 字符串 / 越界整数）。
        default: 缺省值。

    Returns:
        合法 limit。

    Raises:
        ValueError: 无法转成整数。
    """
    from .constants import PAGE_SIZE_MAX  # 局部导入避免顶层循环

    if limit is None or limit == "":
        return default
    value = int(limit)
    if value < 1:
        raise ValueError("limit 必须为正整数")
    return min(value, PAGE_SIZE_MAX)


__all__ = [
    "BUBBLE_REQUIRED_COLUMNS",
    "REPLY_REQUIRED_COLUMNS",
    "CHAT_REQUIRED_COLUMNS",
    "OUTBOX_REQUIRED_COLUMNS",
    "Bubble",
    "Reply",
    "ChatMessage",
    "OutboxItem",
    "row_to_bubble",
    "row_to_reply",
    "row_to_chat",
    "row_to_outbox",
    "rows_to_bubbles",
    "rows_to_replies",
    "rows_to_chats",
    "rows_to_outbox",
    "encode_cursor",
    "decode_cursor",
    "normalize_limit",
]

# 传输层序列化（DTO）已拆分到 `src/core/dto.py`，避免单文件超过 500 行。
