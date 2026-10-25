"""服务端身份：`X-Client-Id` 登记、token 校验、当前请求方解析（Q-1）。

身份模型：
    * `client_id` 是权威标识（uuid4，客户端首次启动生成并持久化）；
    * 服务端首次见到某 `client_id` 时登记并下发 `token`（可长期复用）；
    * 昵称仅用于展示，**重名不合并**；
    * 缺失 `X-Client-Id` 的旧客户端退化为 `legacy-anon-<ip>`。

token 校验策略（刻意宽松，避免把老客户端打死）：
    * 带 `X-Client-Token` 且与服务端记录不一致 → 401；
    * 不带 token → 放行（v1 客户端无 token 概念）。
"""

from __future__ import annotations

import logging
import secrets
import uuid
from typing import Optional

from src.core.constants import (
    LEGACY_ANON_PREFIX,
    MAX_NICKNAME_LEN,
)
from src.core.errors import BadRequestError, UnauthorizedError
from src.core.logging_setup import mask_token
from src.core.permissions import Identity
from src.core.timeutil import now_iso
from src.server_store import ClientRecord, ServerStore

logger = logging.getLogger("server.auth")

#: 请求头名。
HEADER_CLIENT_ID: str = "X-Client-Id"
HEADER_CLIENT_TOKEN: str = "X-Client-Token"
HEADER_CLIENT_NICKNAME: str = "X-Client-Nickname"


def new_token() -> str:
    """生成新 token（48 位十六进制）。"""
    return uuid.uuid4().hex + secrets.token_hex(8)


def client_ip(request) -> str:
    """取请求来源 IP（兼容反向代理头）。

    Args:
        request: Flask `request` 对象。

    Returns:
        IP 字符串。
    """
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip() or "unknown"
    return request.remote_addr or "unknown"


def legacy_anonymous_id(request) -> str:
    """旧客户端无 `X-Client-Id` 时的退化标识。"""
    return f"{LEGACY_ANON_PREFIX}{client_ip(request)}"


def normalize_nickname(raw: object) -> str:
    """校验并规整昵称。

    Args:
        raw: 原始值。

    Returns:
        去空白后的昵称（可能为空串，表示"仅登记、暂不设昵称"）。

    Raises:
        BadRequestError: 超过 `MAX_NICKNAME_LEN`。
    """
    nickname = str(raw or "").strip()
    if len(nickname) > MAX_NICKNAME_LEN:
        raise BadRequestError(f"昵称最多 {MAX_NICKNAME_LEN} 个字")
    return nickname


def register_session(
    store: ServerStore, *, client_id: str, nickname: str, at: Optional[str] = None
) -> dict:
    """`POST /api/v2/session` 的落地逻辑。

    Args:
        store: 服务端仓储。
        client_id: 设备 ID。
        nickname: 昵称（非空）。
        at: 当前时间，缺省取 `now_iso()`。

    Returns:
        `{"client_id","nickname","token","is_new"}`。

    Raises:
        BadRequestError: `client_id` 为空或昵称非法。
    """
    if not client_id:
        raise BadRequestError("缺少 client_id")
    clean = normalize_nickname(nickname)
    if not clean:
        raise BadRequestError("昵称不能为空")
    record, created = store.upsert_client(
        client_id=client_id, nickname=clean, token=new_token(), at=at or now_iso()
    )
    logger.info(
        "session 登记 client_id=%s nickname=%s is_new=%s token=%s",
        record.client_id,
        record.nickname,
        created,
        mask_token(record.token),
    )
    return {
        "client_id": record.client_id,
        "nickname": record.nickname,
        "token": record.token,
        "is_new": created,
    }


def _extract_client_id(request) -> str:
    """从请求头 / 查询串 / JSON body 中取 `client_id`。"""
    header = (request.headers.get(HEADER_CLIENT_ID) or "").strip()
    if header:
        return header
    query_value = (request.args.get("client_id") or "").strip()
    if query_value:
        return query_value
    payload = request.get_json(silent=True)
    if isinstance(payload, dict):
        body_value = str(payload.get("client_id") or "").strip()
        if body_value:
            return body_value
    return ""


def resolve_identity(store: ServerStore, request) -> Identity:
    """解析当前请求方身份（必要时自动登记）。

    Args:
        store: 服务端仓储。
        request: Flask `request`。

    Returns:
        `Identity`。

    Raises:
        UnauthorizedError: token 与服务端记录不一致。
    """
    client_id = _extract_client_id(request) or legacy_anonymous_id(request)
    record: Optional[ClientRecord] = store.get_client(client_id)
    if record is None:
        hint = normalize_nickname(request.headers.get(HEADER_CLIENT_NICKNAME) or "")
        record, created = store.upsert_client(
            client_id=client_id, nickname=hint, token=new_token(), at=now_iso()
        )
        if created:
            logger.info("自动登记新设备 client_id=%s", client_id)
    else:
        store.touch_client(client_id, now_iso())

    token = (request.headers.get(HEADER_CLIENT_TOKEN) or "").strip()
    if token and token != record.token:
        logger.warning(
            "token 不匹配 client_id=%s got=%s want=%s",
            client_id,
            mask_token(token),
            mask_token(record.token),
        )
        raise UnauthorizedError()
    return Identity(client_id=record.client_id, nickname=record.nickname)


def lookup_client_id_by_nickname(
    store: ServerStore, nickname: str, *, prefer_client_id: str = ""
) -> Optional[str]:
    """按昵称反查 `client_id`（旧端点兼容：`viewer` 传的是昵称）。

    Args:
        store: 服务端仓储。
        nickname: 昵称。
        prefer_client_id: 若有多个同名，优先返回该设备（保证稳定）。

    Returns:
        匹配的 `client_id`，无匹配返回 None。
    """
    clean = str(nickname or "").strip()
    if not clean:
        return None
    rows = store.query_all(
        "SELECT client_id FROM clients WHERE nickname = ? ORDER BY last_seen_at DESC",
        (clean,),
    )
    candidates = [str(row["client_id"]) for row in rows]
    if not candidates:
        return None
    if prefer_client_id and prefer_client_id in candidates:
        return prefer_client_id
    return candidates[0]


__all__ = [
    "HEADER_CLIENT_ID",
    "HEADER_CLIENT_TOKEN",
    "HEADER_CLIENT_NICKNAME",
    "new_token",
    "client_ip",
    "legacy_anonymous_id",
    "normalize_nickname",
    "register_session",
    "resolve_identity",
    "lookup_client_id_by_nickname",
]
