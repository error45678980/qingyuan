"""服务端 v2 业务路由（架构文档 §4.2 E01~E11）。

约定：
    * 成功 `{"code":"ok","data":{...},"message":""}`；失败 `{"code":...,"data":null,"message":...}`；
    * 所有写操作必须带 `op_id`，同 `op_id` 重放返回首次结果（`op_log` 幂等）；
    * `is_owner / can_reply / can_chat` 一律由 `permissions.evaluate()` 计算后下发。

共享校验/权限在 `server_common.py`；旧端点兼容在 `server_legacy_api.py`。
"""

from __future__ import annotations

import logging
import uuid

from flask import Blueprint, request

from src.core.constants import (
    MAX_BUBBLE_LEN,
    MAX_CHAT_LEN,
    MAX_ID_LEN,
    MAX_NICKNAME_LEN,
    MAX_REPLY_LEN,
    MAX_SEARCH_LEN,
    MINE_GROUPS,
)
from src.core.dto import chat_to_dto, reply_to_dto
from src.core.errors import BadRequestError, ConflictError, ForbiddenError, NotFoundError
from src.core.timeutil import now_iso
from src.server_auth import (
    lookup_client_id_by_nickname,
    normalize_nickname,
    register_session,
    resolve_identity,
)
from src.server_common import (
    _bubble_dto,
    _cached,
    _json_body,
    _parse_base_version,
    _parse_limit,
    _permissions_for,
    _store,
    _validate_emotion,
    _validate_op_id,
    _validate_reply_mode,
    _validate_text,
    ok,
)

logger = logging.getLogger("server.api")

bp_v2 = Blueprint("api_v2", __name__, url_prefix="/api/v2")


# ---------------------------------------------------------------------------
# E01 健康检查
# ---------------------------------------------------------------------------


@bp_v2.get("/health")
def health():
    """E01：健康检查（不限流、不要求身份）。"""
    store = _store()
    return ok(
        {
            "status": "ok",
            "server_time": now_iso(),
            "schema_version": store.schema_version,
        }
    )


# ---------------------------------------------------------------------------
# E02 会话
# ---------------------------------------------------------------------------


@bp_v2.post("/session")
def session():
    """E02：登记身份并下发 token。"""
    body = _json_body()
    client_id = str(body.get("client_id") or "").strip()
    if not client_id:
        raise BadRequestError("缺少 client_id")
    if len(client_id) > 128:
        raise BadRequestError("client_id 过长")
    data = register_session(
        _store(), client_id=client_id, nickname=body.get("nickname")
    )
    return ok(data)


# ---------------------------------------------------------------------------
# E03 / E04 泡泡集合
# ---------------------------------------------------------------------------


@bp_v2.get("/bubbles")
def list_bubbles():
    """E03：公开泡泡分页列表。"""
    store = _store()
    me = resolve_identity(store, request)
    limit = _parse_limit(request.args.get("limit"))
    emotion = request.args.get("emotion") or None
    if emotion:
        emotion = _validate_emotion(emotion)
    q = request.args.get("q") or None
    if q is not None:
        q = _validate_text(q, max_len=MAX_SEARCH_LEN, field="搜索词")
    since = request.args.get("since") or None
    page = store.list_bubbles(
        limit=limit,
        cursor=request.args.get("cursor") or None,
        emotion=emotion,
        q=q,
        since=since,
    )
    return ok(
        {
            "items": [_bubble_dto(store, bubble, me) for bubble in page.items],
            "total": page.total,
            "has_more": page.has_more,
            "next_cursor": page.next_cursor,
            "server_time": now_iso(),
        }
    )


@bp_v2.post("/bubbles")
def create_bubble():
    """E04：发布泡泡（`op_id` 幂等，`id` 缺省由服务端生成）。"""
    store = _store()
    me = resolve_identity(store, request)
    body = _json_body()
    op_id = _validate_op_id(body)
    cached = _cached(op_id, store)
    if cached is not None:
        return ok(cached, 200)

    content = _validate_text(body.get("content"), max_len=MAX_BUBBLE_LEN, field="正文")
    emotion = _validate_emotion(body.get("emotion_tag"))
    mode = _validate_reply_mode(body.get("reply_mode"))
    bubble_id = str(body.get("id") or "").strip() or str(uuid.uuid4())
    if len(bubble_id) > MAX_ID_LEN:
        raise BadRequestError(f"泡泡 id 最多 {MAX_ID_LEN} 字")
    created_at = str(body.get("created_at") or "").strip() or now_iso()
    nickname = normalize_nickname(body.get("author") or me.nickname) or me.nickname or "匿名"

    with store.transaction():
        again = _cached(op_id, store)
        if again is not None:
            return ok(again, 200)
        existing = store.get_bubble(bubble_id)
        if existing is not None:
            owner_by_id = bool(existing.author_client_id) and (
                existing.author_client_id == me.client_id
            )
            owner_by_legacy = (not existing.author_client_id) and (
                existing.author_nickname == nickname
            )
            if not (owner_by_id or owner_by_legacy):
                raise ConflictError("这个泡泡 id 已被占用")
            result = _bubble_dto(store, existing, me)
            store.put_op_log(
                op_id=op_id, client_id=me.client_id, op_type="publish", result=result, at=now_iso()
            )
            return ok(result, 200)
        bubble = store.create_bubble(
            bubble_id=bubble_id,
            author_nickname=nickname,
            author_client_id=me.client_id,
            content=content,
            emotion_tag=emotion,
            reply_mode=mode,
            created_at=created_at,
        )
        result = _bubble_dto(store, bubble, me)
        store.put_op_log(
            op_id=op_id, client_id=me.client_id, op_type="publish", result=result, at=now_iso()
        )
    return ok(result, 201)


# ---------------------------------------------------------------------------
# E05 详情
# ---------------------------------------------------------------------------


@bp_v2.get("/bubbles/<bubble_id>")
def get_bubble(bubble_id: str):
    """E05：泡泡详情（含回复与按权限过滤的对话）。"""
    store = _store()
    me = resolve_identity(store, request)
    if len(bubble_id) > MAX_ID_LEN:
        raise BadRequestError(f"泡泡 id 最多 {MAX_ID_LEN} 字")
    bubble = store.get_bubble(bubble_id)
    if bubble is None:
        raise NotFoundError()
    perms = _permissions_for(store, bubble, me)
    if not perms.can_read:
        raise ForbiddenError(perms.deny_reason)

    data = _bubble_dto(store, bubble, me)
    data["replies"] = [reply_to_dto(reply) for reply in store.list_replies(bubble_id)]
    if perms.can_read_all_chats:
        chats = store.list_chats(bubble_id)
    else:
        chats = store.list_chats(
            bubble_id, client_id=me.client_id, nickname=me.nickname
        )
    data["chats"] = [chat_to_dto(chat) for chat in chats]
    my_reply = store.get_my_reply(bubble_id, me.client_id, me.nickname)
    data["my_reply"] = reply_to_dto(my_reply) if my_reply is not None else None
    return ok(data)


# ---------------------------------------------------------------------------
# E06 回复
# ---------------------------------------------------------------------------


@bp_v2.post("/bubbles/<bubble_id>/reply")
def post_reply(bubble_id: str):
    """E06：发回复（作者 403；`single` 二次回复 409）。"""
    store = _store()
    me = resolve_identity(store, request)
    body = _json_body()
    op_id = _validate_op_id(body)
    cached = _cached(op_id, store)
    if cached is not None:
        return ok(cached, 200)

    content = _validate_text(body.get("content"), max_len=MAX_REPLY_LEN, field="回复")
    bubble = store.get_bubble(bubble_id)
    if bubble is None:
        raise NotFoundError()
    perms = _permissions_for(store, bubble, me)
    if not perms.can_reply:
        if perms.status_code == 409:
            raise ConflictError(perms.deny_reason)
        raise ForbiddenError(perms.deny_reason)

    with store.transaction():
        reply = store.add_reply(
            bubble_id=bubble_id,
            replier_nickname=me.nickname or "匿名",
            replier_client_id=me.client_id,
            content=content,
            created_at=now_iso(),
            op_id=op_id,
        )
        store.bump_version(bubble_id)
        result = {
            "reply": reply_to_dto(reply),
            "reply_count": store.count_replies(bubble_id),
        }
        store.put_op_log(
            op_id=op_id, client_id=me.client_id, op_type="reply", result=result, at=now_iso()
        )
    return ok(result, 201)


# ---------------------------------------------------------------------------
# E07 / E08 对话
# ---------------------------------------------------------------------------


@bp_v2.get("/bubbles/<bubble_id>/chat")
def get_chat(bubble_id: str):
    """E07：读对话（非参与者 403；非作者只看自己参与的）。"""
    store = _store()
    me = resolve_identity(store, request)
    bubble = store.get_bubble(bubble_id)
    if bubble is None:
        raise NotFoundError()
    perms = _permissions_for(store, bubble, me)
    if not perms.can_read:
        raise ForbiddenError(perms.deny_reason)
    if not perms.is_participant:
        raise ForbiddenError("你无权查看这个对话")
    peer = (request.args.get("peer") or "").strip() or None
    if perms.can_read_all_chats:
        chats = store.list_chats(bubble_id, peer=peer)
    else:
        chats = store.list_chats(
            bubble_id, client_id=me.client_id, nickname=me.nickname, peer=peer
        )
    return ok(
        {
            "messages": [chat_to_dto(chat) for chat in chats],
            "peer": peer or "",
            "has_unread": False,
        }
    )


@bp_v2.post("/bubbles/<bubble_id>/chat")
def post_chat(bubble_id: str):
    """E08：发对话（非参与者 / 已解决 / 他人私密 → 403）。"""
    store = _store()
    me = resolve_identity(store, request)
    body = _json_body()
    op_id = _validate_op_id(body)
    cached = _cached(op_id, store)
    if cached is not None:
        return ok(cached, 200)

    content = _validate_text(body.get("content"), max_len=MAX_CHAT_LEN, field="消息")
    receiver = _validate_text(body.get("receiver"), max_len=MAX_NICKNAME_LEN, field="接收方")
    bubble = store.get_bubble(bubble_id)
    if bubble is None:
        raise NotFoundError()
    perms = _permissions_for(store, bubble, me)
    if not perms.can_chat:
        raise ForbiddenError(perms.deny_reason or "你无权查看这个对话")

    receiver_client_id = lookup_client_id_by_nickname(
        store, receiver, prefer_client_id=me.client_id
    )
    with store.transaction():
        message = store.add_chat(
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
        result = {"message": chat_to_dto(message)}
        store.put_op_log(
            op_id=op_id, client_id=me.client_id, op_type="chat", result=result, at=now_iso()
        )
    return ok(result, 201)


# ---------------------------------------------------------------------------
# E09 / E10 作者管理
# ---------------------------------------------------------------------------


@bp_v2.post("/bubbles/<bubble_id>/resolve")
def resolve_bubble(bubble_id: str):
    """E09：标记已解决（仅作者；作者按 Q-4 无条件覆盖）。"""
    store = _store()
    me = resolve_identity(store, request)
    body = _json_body()
    op_id = _validate_op_id(body)
    _parse_base_version(body)
    cached = _cached(op_id, store)
    if cached is not None:
        return ok(cached, 200)

    bubble = store.get_bubble(bubble_id)
    if bubble is None:
        raise NotFoundError()
    perms = _permissions_for(store, bubble, me)
    if not perms.can_manage:
        raise ForbiddenError("只有作者可以结束这个泡泡")

    with store.transaction():
        if bubble.status == "resolved":
            updated = bubble  # 幂等：状态已一致，不重复 +1
        else:
            updated = store.set_bubble_status(bubble_id, "resolved", at=now_iso())
        assert updated is not None
        result = _bubble_dto(store, updated, me)
        store.put_op_log(
            op_id=op_id, client_id=me.client_id, op_type="resolve", result=result, at=now_iso()
        )
    return ok(result)


@bp_v2.post("/bubbles/<bubble_id>/save")
def save_bubble(bubble_id: str):
    """E10：公开/私密保留（仅作者）。"""
    store = _store()
    me = resolve_identity(store, request)
    body = _json_body()
    op_id = _validate_op_id(body)
    _parse_base_version(body)
    is_public = body.get("is_public")
    if not isinstance(is_public, bool):
        raise BadRequestError("is_public 必须是布尔值")
    cached = _cached(op_id, store)
    if cached is not None:
        return ok(cached, 200)

    bubble = store.get_bubble(bubble_id)
    if bubble is None:
        raise NotFoundError()
    perms = _permissions_for(store, bubble, me)
    if not perms.can_manage:
        raise ForbiddenError("只有作者可以保存这个泡泡")

    target = "saved_public" if is_public else "saved_private"
    with store.transaction():
        updated = store.set_bubble_status(bubble_id, target, at=now_iso())
        assert updated is not None
        result = _bubble_dto(store, updated, me)
        store.put_op_log(
            op_id=op_id, client_id=me.client_id, op_type="save", result=result, at=now_iso()
        )
    return ok(result)


# ---------------------------------------------------------------------------
# E11 我的
# ---------------------------------------------------------------------------


@bp_v2.get("/mine")
def mine():
    """E11：我的四个分组。"""
    store = _store()
    me = resolve_identity(store, request)
    group = (request.args.get("group") or "published").strip()
    if group not in MINE_GROUPS:
        raise BadRequestError(f"group 必须是 {'/'.join(MINE_GROUPS)} 之一")
    limit = _parse_limit(request.args.get("limit"))
    page = store.list_mine(
        group,
        client_id=me.client_id,
        nickname=me.nickname,
        limit=limit,
        cursor=request.args.get("cursor") or None,
    )
    return ok(
        {
            "items": [_bubble_dto(store, bubble, me) for bubble in page.items],
            "counts": store.count_by_group(client_id=me.client_id, nickname=me.nickname),
            "total": page.total,
            "has_more": page.has_more,
            "next_cursor": page.next_cursor,
        }
    )


__all__ = ["bp_v2"]
