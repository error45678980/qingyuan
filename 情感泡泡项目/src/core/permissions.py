"""权限判定的**唯一实现**（架构文档 §5）。

放在 `core` 而非 `server` 的原因：PRD §7-2 要求"权限只算一次"。服务端（权威）与客户端
（离线兜底）调用同一个 `evaluate()`，避免两套规则漂移。

本模块是**纯函数、无 IO**，不 import PyQt5 / Flask / requests。
"""

from __future__ import annotations

from dataclasses import dataclass

from .constants import (
    DENY_REASON_NOT_PARTICIPANT,
    DENY_REASON_NOT_VISIBLE,
    DENY_REASON_OWN_BUBBLE,
    DENY_REASON_RESOLVED,
    DENY_REASON_SINGLE_REPLIED,
    PUBLIC_STATUSES,
)
from .models import Bubble


@dataclass(frozen=True)
class Identity:
    """请求方身份（Q-1：以 `client_id` 为准，昵称仅用于展示与旧数据回退）。"""

    client_id: str
    nickname: str


@dataclass(frozen=True)
class Permissions:
    """一次判定结果。

    Attributes:
        is_owner: 是否作者（设备 ID 优先，旧数据回退昵称）。
        is_replier: 是否回复过该泡泡。
        is_participant: 作者 / 回复者 / 参与过对话。
        can_read: 是否可读该泡泡。
        can_reply: 是否可发回复。
        can_chat: 是否可发对话。
        can_manage: 是否可解决 / 保存（仅作者）。
        can_read_all_chats: 是否可读全部对话（仅作者）。
        deny_reason: 中文拒绝文案（无阻断时为空串）。
        status_code: 主阻断对应的 HTTP 状态码（409 = 单次回复已用过）。
        legacy_owner: 是否通过昵称回退判定为作者（下发给客户端做展示）。
    """

    is_owner: bool
    is_replier: bool
    is_participant: bool
    can_read: bool
    can_reply: bool
    can_chat: bool
    can_manage: bool
    can_read_all_chats: bool
    deny_reason: str = ""
    status_code: int = 200
    legacy_owner: bool = False


def is_legacy_owner(bubble: Bubble, me: Identity) -> bool:
    """旧数据（`author_client_id` 为空）的作者判定：昵称相同即视为作者。

    Args:
        bubble: 泡泡。
        me: 请求方身份。

    Returns:
        是否昵称回退命中。
    """
    if bubble.author_client_id:
        return False
    return bool(me.nickname) and bubble.author_nickname == me.nickname


def evaluate(
    bubble: Bubble,
    me: Identity,
    *,
    my_reply_count: int,
    has_chatted: bool,
    legacy_owner: bool = False,
) -> Permissions:
    """判定 4 种角色 × 7 种操作的权限（§5.2 判定表的唯一实现）。

    Args:
        bubble: 目标泡泡。
        me: 请求方身份。
        my_reply_count: 我在该泡泡下已回复的次数（服务端用 `count_replies_by_client`，
            客户端用 `Repo.my_reply_count`）。
        has_chatted: 我是否在该泡泡下参与过对话。
        legacy_owner: 调用方是否已额外确认作者身份（旧数据回退）。

    Returns:
        `Permissions`。
    """
    owner_by_id = bool(bubble.author_client_id) and bubble.author_client_id == me.client_id
    owner_by_nickname = is_legacy_owner(bubble, me)
    owner = owner_by_id or owner_by_nickname or legacy_owner
    effective_legacy = legacy_owner or owner_by_nickname

    reply_count = max(0, int(my_reply_count))
    replier = reply_count > 0
    participant = owner or replier or bool(has_chatted)
    visible = bubble.status in PUBLIC_STATUSES

    can_read = owner or visible
    # 作者不能回复自己；只有公开可见状态可回复；single 模式一人一次
    can_reply = (
        (not owner)
        and visible
        and (bubble.reply_mode == "dialog" or reply_count == 0)
    )
    # 已解决不可再聊；仅参与者可聊（作者 / 回复者 / 参与过对话的人）
    # 且必须"读得到"才能聊 —— 否则 `saved_private` 会对回复者留下一个能写不能读的洞
    can_chat = can_read and bubble.status != "resolved" and participant
    can_manage = owner
    can_read_all_chats = owner

    deny_reason, status_code = _deny_reason(
        bubble=bubble,
        owner=owner,
        can_read=can_read,
        can_chat=can_chat,
        reply_count=reply_count,
    )
    return Permissions(
        is_owner=owner,
        is_replier=replier,
        is_participant=participant,
        can_read=can_read,
        can_reply=can_reply,
        can_chat=can_chat,
        can_manage=can_manage,
        can_read_all_chats=can_read_all_chats,
        deny_reason=deny_reason,
        status_code=status_code,
        legacy_owner=effective_legacy,
    )


def _deny_reason(
    *,
    bubble: Bubble,
    owner: bool,
    can_read: bool,
    can_chat: bool,
    reply_count: int,
) -> tuple[str, int]:
    """按优先级计算主阻断原因与状态码。

    优先级（先命中先返回）：
        1. 不可读 → 403
        2. 已解决 → 403
        3. 作者回复自己 → 403
        4. 单次回复已用过 → **409**（唯一需要 409 的场景）
        5. 非参与者发对话 → 200（服务端在 E08 单独转 403）
        6. 无阻断 → 200

    Args:
        bubble: 泡泡。
        owner: 是否作者。
        can_read: 是否可读。
        can_chat: 是否可聊。
        reply_count: 我的回复次数。

    Returns:
        `(deny_reason, status_code)`。
    """
    if not can_read:
        return DENY_REASON_NOT_VISIBLE, 403
    if bubble.status == "resolved":
        return DENY_REASON_RESOLVED, 403
    if owner:
        return DENY_REASON_OWN_BUBBLE, 403
    if reply_count > 0 and bubble.reply_mode == "single":
        return DENY_REASON_SINGLE_REPLIED, 409
    if not can_chat:
        return DENY_REASON_NOT_PARTICIPANT, 200
    return "", 200


__all__ = ["Identity", "Permissions", "is_legacy_owner", "evaluate"]
