"""旧端点兼容层（`/api/*`）—— 薄适配层（架构文档 §4.3）。

从 `server_api.py` 拆出，目的：单文件 ≤ 500 行（铁律 5）。
行为：解析 v1 的 body（`author`/`replier`/`sender`/`viewer`）→ 用 `X-Client-Id`
（缺失则 `legacy-anon-<ip>`）构造内部请求 → 复用 v2 的校验与权限判定 →
按 v1 的响应形状返回。

修复点（P0-5）：`GET /api/bubbles/<id>?viewer=X` 在无匹配时 `chats` 必须为空；
`POST .../chat` 只回传请求方参与的对话。
旧端点**不豁免**限流与长度校验；`op_id` 缺失时按 `sha1(client_id|path|body)` 合成。
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from flask import Blueprint, jsonify, request

from src.core.constants import (
    MAX_BUBBLE_LEN,
    MAX_CHAT_LEN,
    MAX_NICKNAME_LEN,
    MAX_REPLY_LEN,
    PAGE_SIZE_DEFAULT,
)
from src.core.errors import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
)
from src.core.models import Bubble
from src.core.permissions import Identity, evaluate
from src.core.timeutil import now_iso
from src.server_common import (
    _json_body,
    _permissions_for,
    _store,
    _validate_emotion,
    _validate_reply_mode,
    _validate_text,
)
from src.server_auth import lookup_client_id_by_nickname, resolve_identity

logger = logging.getLogger("server.api.legacy")

bp_legacy = Blueprint("api_legacy", __name__, url_prefix="/api")


# ---------------------------------------------------------------------------
# 旧端点兼容（/api/*）—— 薄适配层
# ---------------------------------------------------------------------------


def _legacy_bubble(bubble: Bubble) -> dict[str, Any]:
    """v1 泡泡形状。"""
    return {
        "id": bubble.id,
        "author": bubble.author_nickname,
        "author_node_id": bubble.author_client_id,
        "content": bubble.content,
        "emotion_tag": bubble.emotion_tag,
        "reply_mode": bubble.reply_mode,
        "status": bubble.status,
        "created_at": bubble.created_at,
        "resolved_at": bubble.resolved_at,
    }


def _legacy_reply(reply) -> dict[str, Any]:
    """v1 回复形状。"""
    return {
        "id": reply.id,
        "bubble_id": reply.bubble_id,
        "replier": reply.replier_nickname,
        "content": reply.content,
        "created_at": reply.created_at,
    }


def _legacy_chat(chat) -> dict[str, Any]:
    """v1 对话形状。"""
    return {
        "id": chat.id,
        "bubble_id": chat.bubble_id,
        "sender": chat.sender_nickname,
        "receiver": chat.receiver_nickname,
        "content": chat.content,
        "created_at": chat.created_at,
    }


def _legacy_op_id(me: Identity, body: dict[str, Any]) -> str:
    """为 v1 客户端合成稳定 `op_id`（保证可重放）。"""
    raw = f"{me.client_id}|{request.path}|{sorted(body.items())}"
    return "legacy-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()


@bp_legacy.get("/ping")
def legacy_ping():
    """`GET /api/ping`：保持原样。"""
    return jsonify({"status": "ok"})


@bp_legacy.get("/bubbles")
def legacy_list_bubbles():
    """`GET /api/bubbles`：等价 E03，返回数组（v1 形状）。"""
    store = _store()
    emotion = request.args.get("emotion") or None
    if emotion:
        emotion = _validate_emotion(emotion)
    page = store.list_bubbles(limit=PAGE_SIZE_DEFAULT, emotion=emotion)
    return jsonify([_legacy_bubble(bubble) for bubble in page.items])


@bp_legacy.post("/bubbles")
def legacy_create_bubble():
    """`POST /api/bubbles`：等价 E04，`id` 必填。"""
    store = _store()
    me = resolve_identity(store, request)
    body = _json_body()
    bubble_id = str(body.get("id") or "").strip()
    if not bubble_id:
        raise BadRequestError("缺少 id")
    author = _validate_text(
        body.get("author"), max_len=MAX_NICKNAME_LEN, field="昵称"
    )
    content = _validate_text(body.get("content"), max_len=MAX_BUBBLE_LEN, field="正文")
    emotion = _validate_emotion(body.get("emotion_tag"))
    mode = _validate_reply_mode(body.get("reply_mode"))
    created_at = str(body.get("created_at") or "").strip() or now_iso()
    op_id = _legacy_op_id(me, body)

    existing = store.get_bubble(bubble_id)
    if existing is not None:
        if existing.author_client_id == me.client_id or (
            not existing.author_client_id and existing.author_nickname == author
        ):
            return jsonify(_legacy_bubble(existing)), 200
        raise ConflictError("这个泡泡 id 已被占用")
    with store.transaction():
        bubble = store.create_bubble(
            bubble_id=bubble_id,
            author_nickname=author,
            author_client_id=me.client_id,
            content=content,
            emotion_tag=emotion,
            reply_mode=mode,
            created_at=created_at,
        )
        store.put_op_log(
            op_id=op_id,
            client_id=me.client_id,
            op_type="publish",
            result={"id": bubble_id},
            at=now_iso(),
        )
    return jsonify(_legacy_bubble(bubble)), 201


@bp_legacy.get("/bubbles/<bubble_id>")
def legacy_get_bubble(bubble_id: str):
    """`GET /api/bubbles/<id>`：等价 E05，**修 P0-5**（无匹配则 chats 为空）。"""
    store = _store()
    bubble = store.get_bubble(bubble_id)
    if bubble is None:
        raise NotFoundError()
    viewer = (request.args.get("viewer") or "").strip()
    if viewer:
        viewer_id = lookup_client_id_by_nickname(store, viewer)
        identity = (
            Identity(viewer_id, viewer) if viewer_id else Identity("", viewer)
        )
    else:
        identity = resolve_identity(store, request)
    perms = evaluate(
        bubble,
        identity,
        my_reply_count=(
            store.count_replies_by_client(bubble.id, identity.client_id, identity.nickname)
            if identity.client_id
            else 0
        ),
        has_chatted=(
            store.has_chatted(bubble.id, identity.client_id, identity.nickname)
            if identity.client_id
            else False
        ),
    )
    data = _legacy_bubble(bubble)
    data["replies"] = [_legacy_reply(reply) for reply in store.list_replies(bubble_id)]
    if perms.can_read_all_chats:
        chats = store.list_chats(bubble_id)
    elif identity.client_id:
        chats = store.list_chats(
            bubble_id, client_id=identity.client_id, nickname=identity.nickname
        )
    else:
        chats = []
    data["chats"] = [_legacy_chat(chat) for chat in chats]
    return jsonify(data)


@bp_legacy.post("/bubbles/<bubble_id>/reply")
def legacy_post_reply(bubble_id: str):
    """`POST .../reply`：等价 E06（409 时返回 `{"code":"conflict"}`）。"""
    store = _store()
    me = resolve_identity(store, request)
    body = _json_body()
    content = _validate_text(body.get("content"), max_len=MAX_REPLY_LEN, field="回复")
    bubble = store.get_bubble(bubble_id)
    if bubble is None:
        raise NotFoundError()
    perms = _permissions_for(store, bubble, me)
    if not perms.can_reply:
        if perms.status_code == 409:
            raise ConflictError(perms.deny_reason)
        raise ForbiddenError(perms.deny_reason)
    op_id = _legacy_op_id(me, body)
    with store.transaction():
        store.add_reply(
            bubble_id=bubble_id,
            replier_nickname=me.nickname or "匿名",
            replier_client_id=me.client_id,
            content=content,
            created_at=now_iso(),
            op_id=op_id,
        )
        store.bump_version(bubble_id)
    return jsonify(
        {
            "bubble_id": bubble_id,
            "replies": [_legacy_reply(reply) for reply in store.list_replies(bubble_id)],
        }
    ), 201


@bp_legacy.post("/bubbles/<bubble_id>/chat")
def legacy_post_chat(bubble_id: str):
    """`POST .../chat`：等价 E08，**修 P0-5**（只回传请求方参与的对话）。"""
    store = _store()
    me = resolve_identity(store, request)
    body = _json_body()
    content = _validate_text(body.get("content"), max_len=MAX_CHAT_LEN, field="消息")
    receiver = _validate_text(
        body.get("receiver"), max_len=MAX_NICKNAME_LEN, field="接收方"
    )
    bubble = store.get_bubble(bubble_id)
    if bubble is None:
        raise NotFoundError()
    perms = _permissions_for(store, bubble, me)
    if not perms.can_chat:
        raise ForbiddenError(perms.deny_reason or "你无权查看这个对话")
    op_id = _legacy_op_id(me, body)
    receiver_client_id = lookup_client_id_by_nickname(
        store, receiver, prefer_client_id=me.client_id
    )
    with store.transaction():
        store.add_chat(
            bubble_id=bubble_id,
            sender_nickname=me.nickname or "匿名",
            sender_client_id=me.client_id,
            receiver_nickname=receiver,
            receiver_client_id=receiver_client_id,
            content=content,
            created_at=now_iso(),
            op_id=op_id,
        )
        store.bump_version(bubble_id)
    if perms.can_read_all_chats:
        chats = store.list_chats(bubble_id)
    else:
        chats = store.list_chats(bubble_id, client_id=me.client_id, nickname=me.nickname)
    return jsonify(
        {"bubble_id": bubble_id, "chats": [_legacy_chat(chat) for chat in chats]}
    ), 201


@bp_legacy.post("/bubbles/<bubble_id>/resolve")
def legacy_resolve(bubble_id: str):
    """`POST .../resolve`：等价 E09（`base_version` 缺省按作者覆盖语义）。"""
    store = _store()
    me = resolve_identity(store, request)
    bubble = store.get_bubble(bubble_id)
    if bubble is None:
        raise NotFoundError()
    perms = _permissions_for(store, bubble, me)
    if not perms.can_manage:
        raise ForbiddenError("只有作者可以结束这个泡泡")
    with store.transaction():
        if bubble.status != "resolved":
            store.set_bubble_status(bubble_id, "resolved", at=now_iso())
    return jsonify({"bubble_id": bubble_id, "status": "resolved"})


@bp_legacy.post("/bubbles/<bubble_id>/save")
def legacy_save(bubble_id: str):
    """`POST .../save`：等价 E10。"""
    store = _store()
    me = resolve_identity(store, request)
    body = _json_body()
    is_public = body.get("is_public")
    if not isinstance(is_public, bool):
        raise BadRequestError("is_public 必须是布尔值")
    bubble = store.get_bubble(bubble_id)
    if bubble is None:
        raise NotFoundError()
    perms = _permissions_for(store, bubble, me)
    if not perms.can_manage:
        raise ForbiddenError("只有作者可以保存这个泡泡")
    target = "saved_public" if is_public else "saved_private"
    with store.transaction():
        store.set_bubble_status(bubble_id, target, at=now_iso())
    return jsonify({"bubble_id": bubble_id, "status": target})



__all__ = ["bp_legacy"]
