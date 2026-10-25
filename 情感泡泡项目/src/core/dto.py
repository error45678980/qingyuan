"""传输层序列化：`Bubble/Reply/ChatMessage` ↔ DTO（架构文档 §2.2、§4.1）。

从 `models.py` 拆出，目的：单文件 ≤ 500 行（铁律 5）。
`models.py` 仍是 **Row → dataclass** 的唯一入口（规则 D-2），本模块只负责
"dataclass ↔ JSON dict"。

注意：`author` / `replier` / `sender` / `receiver` 键名保留，用于兼容 v1 客户端与既有脚本。
"""

from __future__ import annotations

from typing import Any, Mapping

from .constants import DEFAULT_REPLY_MODE, DEFAULT_STATUS
from .errors import SchemaMismatchError
from .models import Bubble, ChatMessage, Reply, _as_int, _as_opt_str, _keys


def bubble_to_dto(
    bubble: Bubble,
    *,
    is_owner: bool,
    can_reply: bool,
    can_chat: bool,
    legacy_owner: bool = False,
) -> dict[str, Any]:
    """`Bubble` → `BubbleDTO`。

    Args:
        bubble: 数据模型。
        is_owner: 请求方是否作者（由 `permissions.evaluate()` 计算）。
        can_reply: 请求方是否可回复。
        can_chat: 请求方是否可发起对话。
        legacy_owner: 是否通过昵称回退判定为作者。

    Returns:
        可直接 `jsonify` 的 dict。
    """
    return {
        "id": bubble.id,
        "author": bubble.author_nickname,
        "author_client_id": bubble.author_client_id,
        "content": bubble.content,
        "emotion_tag": bubble.emotion_tag,
        "reply_mode": bubble.reply_mode,
        "status": bubble.status,
        "version": bubble.version,
        "created_at": bubble.created_at,
        "resolved_at": bubble.resolved_at,
        "saved_at": bubble.saved_at,
        "reply_count": bubble.reply_count,
        "chat_count": bubble.chat_count,
        "is_owner": is_owner,
        "can_reply": can_reply,
        "can_chat": can_chat,
        "legacy_owner": legacy_owner,
    }


def bubble_from_dto(dto: Mapping[str, Any]) -> Bubble:
    """`BubbleDTO`（或旧响应）→ `Bubble`。

    兼容点：`author` 与 `author_nickname` 二者取其一；缺 `status` 时用 `active`。

    Raises:
        SchemaMismatchError: 缺少 `id` 或 `content`。
    """
    keys = _keys(dto)
    if "id" not in keys or "content" not in keys:
        raise SchemaMismatchError("泡泡响应缺少 id/content 字段")
    if "author" in keys:
        nickname = dto.get("author")
    else:
        nickname = dto.get("author_nickname")
    return Bubble(
        id=str(dto["id"]),
        author_nickname=str(nickname or ""),
        author_client_id=str(dto.get("author_client_id") or ""),
        content=str(dto["content"]),
        emotion_tag=str(dto.get("emotion_tag") or ""),
        reply_mode=str(dto.get("reply_mode") or DEFAULT_REPLY_MODE),
        status=str(dto.get("status") or DEFAULT_STATUS),
        version=_as_int(dto.get("version"), 1),
        created_at=str(dto.get("created_at") or ""),
        resolved_at=_as_opt_str(dto.get("resolved_at")),
        saved_at=_as_opt_str(dto.get("saved_at")),
        reply_count=_as_int(dto.get("reply_count"), 0),
        chat_count=_as_int(dto.get("chat_count"), 0),
        is_owner=bool(dto.get("is_owner") or False),
        can_reply=bool(dto.get("can_reply") or False),
        can_chat=bool(dto.get("can_chat") or False),
        perms_known="is_owner" in keys,
    )


def reply_to_dto(reply: Reply) -> dict[str, Any]:
    """`Reply` → `ReplyDTO`（`replier` 键名兼容 v1）。"""
    return {
        "id": reply.id,
        "bubble_id": reply.bubble_id,
        "replier": reply.replier_nickname,
        "replier_client_id": reply.replier_client_id,
        "content": reply.content,
        "created_at": reply.created_at,
        "op_id": reply.op_id,
    }


def reply_from_dto(dto: Mapping[str, Any], *, bubble_id: str = "") -> Reply:
    """`ReplyDTO` → `Reply`。"""
    keys = _keys(dto)
    nickname = dto.get("replier", dto.get("replier_nickname"))
    return Reply(
        id=_as_int(dto.get("id")),
        bubble_id=str(dto.get("bubble_id") or bubble_id),
        replier_nickname=str(nickname or ""),
        replier_client_id=str(dto.get("replier_client_id") or ""),
        content=str(dto.get("content") or ""),
        created_at=str(dto.get("created_at") or ""),
        op_id=str(dto.get("op_id") or "") if "op_id" in keys else "",
    )


def chat_to_dto(message: ChatMessage) -> dict[str, Any]:
    """`ChatMessage` → `ChatDTO`（`sender`/`receiver` 键名兼容 v1）。"""
    return {
        "id": message.id,
        "bubble_id": message.bubble_id,
        "sender": message.sender_nickname,
        "sender_client_id": message.sender_client_id,
        "receiver": message.receiver_nickname,
        "receiver_client_id": message.receiver_client_id,
        "content": message.content,
        "created_at": message.created_at,
        "op_id": message.op_id,
    }


def chat_from_dto(dto: Mapping[str, Any], *, bubble_id: str = "") -> ChatMessage:
    """`ChatDTO` → `ChatMessage`。"""
    keys = _keys(dto)
    sender = dto.get("sender", dto.get("sender_nickname"))
    receiver = dto.get("receiver", dto.get("receiver_nickname"))
    return ChatMessage(
        id=_as_int(dto.get("id")),
        bubble_id=str(dto.get("bubble_id") or bubble_id),
        sender_nickname=str(sender or ""),
        sender_client_id=str(dto.get("sender_client_id") or ""),
        receiver_nickname=str(receiver or ""),
        content=str(dto.get("content") or ""),
        created_at=str(dto.get("created_at") or ""),
        receiver_client_id=_as_opt_str(dto.get("receiver_client_id")),
        op_id=str(dto.get("op_id") or "") if "op_id" in keys else "",
    )


__all__ = [
    "bubble_to_dto",
    "bubble_from_dto",
    "reply_to_dto",
    "reply_from_dto",
    "chat_to_dto",
    "chat_from_dto",
]
