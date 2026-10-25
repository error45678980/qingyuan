"""v2 路由的共享基础设施：包络、校验、权限与幂等缓存。

从 `server_api.py` 拆出，目的：单文件 ≤ 500 行（铁律 5）。
本模块是**较低层**（不 import `server_api` / `server_legacy_api`），因此两个蓝图模块
都可以安全地复用它，不会产生循环依赖。

命名沿用下划线前缀，表示"包内共享、非对外 API"。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from flask import current_app, jsonify, request

from src.core.constants import (
    EMOTIONS,
    MAX_ID_LEN,
    PAGE_SIZE_DEFAULT,
    PAGE_SIZE_MAX,
    REPLY_MODES,
)
from src.core.dto import bubble_to_dto
from src.core.errors import BadRequestError, ConflictError
from src.core.models import Bubble, normalize_limit
from src.core.permissions import Identity, Permissions, evaluate
from src.core.timeutil import now_iso
from src.server_store import ServerStore

logger = logging.getLogger("server.api")


# ---------------------------------------------------------------------------
# 基础设施
# ---------------------------------------------------------------------------


def _store() -> ServerStore:
    """取当前 app 绑定的仓储。"""
    return current_app.extensions["eb_store"]


def ok(data: Any, status: int = 200):
    """成功包络。"""
    return jsonify({"code": "ok", "data": data, "message": ""}), status


def _json_body() -> dict[str, Any]:
    """取 JSON 请求体（非对象直接 400）。"""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise BadRequestError("请求体必须是 JSON 对象")
    return payload


def _validate_text(value: Any, *, max_len: int, field: str, allow_empty: bool = False) -> str:
    """校验必填文本长度。"""
    text = str(value if value is not None else "").strip()
    if not text and not allow_empty:
        raise BadRequestError(f"{field}不能为空")
    if len(text) > max_len:
        raise BadRequestError(f"{field}最多 {max_len} 字")
    return text


def _validate_emotion(value: Any) -> str:
    """校验情感标签。"""
    text = str(value or "").strip()
    if text not in EMOTIONS:
        raise BadRequestError("情感标签不合法")
    return text


def _validate_reply_mode(value: Any) -> str:
    """校验回复模式。"""
    text = str(value or "").strip()
    if text not in REPLY_MODES:
        raise BadRequestError("回复模式不合法")
    return text


def _validate_op_id(body: dict[str, Any]) -> str:
    """校验并返回 `op_id`。"""
    op_id = str(body.get("op_id") or "").strip()
    if not op_id:
        raise BadRequestError("缺少 op_id")
    if len(op_id) > 128:
        raise BadRequestError("op_id 过长")
    return op_id


def _parse_base_version(body: dict[str, Any]) -> int:
    """解析 `base_version`（缺省 0）。"""
    raw = body.get("base_version", 0)
    if raw in (None, ""):
        return 0
    try:
        return max(0, int(raw))
    except (TypeError, ValueError) as exc:
        raise BadRequestError("base_version 必须是整数") from exc


def _parse_limit(raw: Optional[str], *, default: int = PAGE_SIZE_DEFAULT) -> int:
    """解析 `limit`（非法 → 400）。"""
    try:
        return normalize_limit(raw, default=default)
    except ValueError as exc:
        raise BadRequestError(f"limit 必须是 1~{PAGE_SIZE_MAX} 的整数") from exc


def _permissions_for(store: ServerStore, bubble: Bubble, me: Identity) -> Permissions:
    """计算某泡泡对某身份的权限（唯一入口）。"""
    return evaluate(
        bubble,
        me,
        my_reply_count=store.count_replies_by_client(
            bubble.id, me.client_id, me.nickname
        ),
        has_chatted=store.has_chatted(bubble.id, me.client_id, me.nickname),
    )


def _bubble_dto(store: ServerStore, bubble: Bubble, me: Identity) -> dict[str, Any]:
    """把泡泡渲染成 DTO（含权限标志）。"""
    perms = _permissions_for(store, bubble, me)
    return bubble_to_dto(
        bubble,
        is_owner=perms.is_owner,
        can_reply=perms.can_reply,
        can_chat=perms.can_chat,
        legacy_owner=perms.legacy_owner,
    )


def _cached(op_id: str, store: ServerStore) -> Optional[dict[str, Any]]:
    """命中幂等缓存时返回首次结果（`None` 表示未命中）。"""
    entry = store.get_op_log(op_id)
    if entry is None:
        return None
    result = entry.get("result") or {}
    logger.info("op_id=%s 命中幂等缓存 op_type=%s", op_id, entry.get("op_type"))
    return result



__all__ = [
    "_store",
    "ok",
    "_json_body",
    "_validate_text",
    "_validate_emotion",
    "_validate_reply_mode",
    "_validate_op_id",
    "_parse_base_version",
    "_parse_limit",
    "_permissions_for",
    "_bubble_dto",
    "_cached",
]
