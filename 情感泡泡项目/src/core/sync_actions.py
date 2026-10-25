"""同步引擎的"写操作 + 重放"部分（`SyncActionsMixin`）。

从 `sync.py` 拆出，目的：单文件 ≤ 500 行（铁律 5）。
本模块是**较低层**：只定义事件名常量与写/重放逻辑，不 import `sync.py`，
因此 `sync.py` 可以安全地 `from .sync_actions import ...`（无循环依赖）。
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import replace
from typing import Any, Optional

from .api import ApiResult
from .constants import (
    EMOTION_FALLBACK,
    OUTBOX_BATCH_SIZE,
    OUTBOX_PENDING,
    OUTBOX_SYNCING,
)
from .dto import bubble_from_dto, chat_from_dto, reply_from_dto
from .models import Bubble, ChatMessage, Reply
from .timeutil import now_iso

logger = logging.getLogger("core.sync")

#: 引擎事件名（与 `SyncManager` 的信号一一对应）。
EVENT_ONLINE_CHANGED = "online_changed"
EVENT_BUBBLES_PULLED = "bubbles_pulled"
EVENT_BUBBLE_UPDATED = "bubble_updated"
EVENT_REPLIES_UPDATED = "replies_updated"
EVENT_CHATS_UPDATED = "chats_updated"
EVENT_OUTBOX_CHANGED = "outbox_changed"
EVENT_ACTION_FAILED = "action_failed"
EVENT_ACTION_OK = "action_ok"
EVENT_MINE_UPDATED = "mine_updated"

#: 单条重放结果。
RESULT_DONE = "done"
RESULT_RETRY = "retry"
RESULT_CONFLICT = "conflict"
RESULT_FAILED = "failed"



class SyncActionsMixin:
    """离线优先写操作 + 队列重放。

    依赖宿主（`SyncEngine`）提供以下属性/方法：
        _repo: Repo          _api: ApiClient       _config: AppConfig
        _outbox: Outbox      _emit(event, payload) _mark_online()
        _mark_offline()      detail(bubble_id) -> bool
    """

    # ------------------------------------------------------------------
    # 写入（离线优先）
    # ------------------------------------------------------------------
    def publish(self, *, content: str, emotion_tag: str, reply_mode: str) -> str:
        """发布泡泡：先写本地（`pending`）再入队，最后尝试立即同步。

        Returns:
            本地泡泡 id。
        """
        bubble_id = str(uuid.uuid4())
        op_id = str(uuid.uuid4())
        created_at = now_iso()
        bubble = Bubble(
            id=bubble_id,
            author_nickname=self._config.nickname,
            author_client_id=self._api.client_id,
            content=content,
            emotion_tag=emotion_tag,
            reply_mode=reply_mode,
            status="active",
            version=1,
            created_at=created_at,
            sync_state="pending",
        )
        self._repo.upsert_bubble(bubble)
        self._outbox.enqueue(
            op_type="publish",
            bubble_id=bubble_id,
            payload={
                "id": bubble_id,
                "content": content,
                "emotion_tag": emotion_tag,
                "reply_mode": reply_mode,
                "created_at": created_at,
            },
            base_version=0,
            op_id=op_id,
        )
        self._emit(EVENT_BUBBLE_UPDATED, bubble)
        self._emit(EVENT_OUTBOX_CHANGED, self._outbox.pending_count())
        self.flush(limit=1)
        return bubble_id

    def _ensure_bubble_row(self, bubble_id: str) -> Optional[Bubble]:
        """确保本地存在该泡泡的行（回复/对话的外键依赖）。

        正常流程下 UI 会先拉详情再回复，此处只做防御：若本地确实没有，
        写入一条 `status='resolved'` 的占位行（不出现在泡泡墙、不进「我的」），
        等下次 `detail()` 拉到真实数据时被 `upsert_bubble` 覆盖。

        Args:
            bubble_id: 泡泡 id。

        Returns:
            本地泡泡（占位时返回占位行）。
        """
        bubble = self._repo.get_bubble(bubble_id)
        if bubble is not None:
            return bubble
        stub = Bubble(
            id=bubble_id,
            author_nickname="",
            author_client_id="",
            content="",
            emotion_tag=EMOTION_FALLBACK,
            reply_mode="single",
            status="resolved",
            version=0,
            created_at=now_iso(),
            sync_state="synced",
        )
        self._repo.upsert_bubble(stub)
        logger.info("本地缺少泡泡 %s，写入占位行以承载乐观写入", bubble_id)
        return stub

    def reply(self, *, bubble_id: str, content: str) -> str:
        """发回复：先写本地乐观回复再入队。

        Returns:
            本次操作的 `op_id`。
        """
        bubble = self._ensure_bubble_row(bubble_id)
        base_version = bubble.version if bubble is not None else 0
        op_id = str(uuid.uuid4())
        optimistic = Reply(
            id=0,
            bubble_id=bubble_id,
            replier_nickname=self._config.nickname,
            replier_client_id=self._api.client_id,
            content=content,
            created_at=now_iso(),
            op_id=op_id,
            sync_state="pending",
        )
        self._repo.upsert_reply(optimistic)
        self._outbox.enqueue(
            op_type="reply",
            bubble_id=bubble_id,
            payload={"content": content},
            base_version=base_version,
            op_id=op_id,
        )
        self._emit(EVENT_REPLIES_UPDATED, (bubble_id, self._repo.list_replies(bubble_id)))
        self._emit(EVENT_OUTBOX_CHANGED, self._outbox.pending_count())
        self.flush(limit=1)
        return op_id

    def chat(self, *, bubble_id: str, receiver: str, content: str) -> str:
        """发对话：先写本地乐观消息再入队。

        `saved_private` 泡泡**拒绝**入队（Q-3 最小暴露面）。

        Returns:
            本次操作的 `op_id`；被拒绝时返回空串并派发 `action_failed`。
        """
        bubble = self._ensure_bubble_row(bubble_id)
        if bubble is not None and bubble.status == "saved_private":
            logger.warning("拒绝为私密泡泡入队对话 bubble=%s", bubble_id)
            self._emit(EVENT_ACTION_FAILED, ("chat", "私密泡泡的对话不会上传", 403))
            return ""
        base_version = bubble.version if bubble is not None else 0
        op_id = str(uuid.uuid4())
        optimistic = ChatMessage(
            id=0,
            bubble_id=bubble_id,
            sender_nickname=self._config.nickname,
            sender_client_id=self._api.client_id,
            receiver_nickname=receiver,
            content=content,
            created_at=now_iso(),
            op_id=op_id,
            sync_state="pending",
        )
        self._repo.upsert_chat(optimistic)
        self._outbox.enqueue(
            op_type="chat",
            bubble_id=bubble_id,
            payload={"receiver": receiver, "content": content},
            base_version=base_version,
            op_id=op_id,
        )
        self._emit(EVENT_CHATS_UPDATED, (bubble_id, self._repo.list_chats(bubble_id)))
        self._emit(EVENT_OUTBOX_CHANGED, self._outbox.pending_count())
        self.flush(limit=1)
        return op_id

    def resolve(self, *, bubble_id: str) -> str:
        """标记已解决（作者操作，Q-4 本地优先）。"""
        bubble = self._repo.get_bubble(bubble_id)
        if bubble is None:
            self._emit(EVENT_ACTION_FAILED, ("resolve", "找不到这个泡泡了", 404))
            return ""
        op_id = str(uuid.uuid4())
        self._repo.mark_bubble_status(
            bubble_id, "resolved", version=bubble.version + 1, at=now_iso()
        )
        self._repo.set_bubble_sync_state(bubble_id, "pending")
        self._outbox.enqueue(
            op_type="resolve",
            bubble_id=bubble_id,
            payload={},
            base_version=bubble.version,
            op_id=op_id,
        )
        self._emit(EVENT_BUBBLE_UPDATED, self._repo.get_bubble(bubble_id))
        self._emit(EVENT_OUTBOX_CHANGED, self._outbox.pending_count())
        self.flush(limit=1)
        return op_id

    def save(self, *, bubble_id: str, is_public: bool) -> str:
        """公开/私密保留（作者操作）。

        私密保存时（Q-3）：
            * 若该泡泡**从未上传过**（`publish` 仍在队列），直接取消上传，不产生 `save` 项；
            * 同时取消该泡泡待上传的 `reply` / `chat`，保证私密内容不外泄。

        Returns:
            本次操作的 `op_id`（未上传时为空串）。
        """
        bubble = self._repo.get_bubble(bubble_id)
        if bubble is None:
            self._emit(EVENT_ACTION_FAILED, ("save", "找不到这个泡泡了", 404))
            return ""
        target = "saved_public" if is_public else "saved_private"
        self._repo.mark_bubble_status(
            bubble_id, target, version=bubble.version + 1, at=now_iso()
        )
        self._repo.set_bubble_sync_state(bubble_id, "pending")

        if is_public:
            op_id = str(uuid.uuid4())
            self._outbox.enqueue(
                op_type="save",
                bubble_id=bubble_id,
                payload={"is_public": True},
                base_version=bubble.version,
                op_id=op_id,
            )
        else:
            pending_publish = [
                item
                for item in self._outbox.list_for_bubble(bubble_id)
                if item.op_type == "publish" and item.state in (OUTBOX_PENDING, OUTBOX_SYNCING)
            ]
            self._cancel_uploads(bubble_id, ("reply", "chat"))
            if pending_publish:
                # 从未上传过：取消发布，私密内容只留本地
                self._outbox.delete_pending_for_bubble(bubble_id, ("publish",))
                self._repo.set_bubble_sync_state(bubble_id, "synced")
                logger.info("私密保存：取消未上传的发布 bubble=%s", bubble_id)
                op_id = ""
            else:
                op_id = str(uuid.uuid4())
                self._outbox.enqueue(
                    op_type="save",
                    bubble_id=bubble_id,
                    payload={"is_public": False},
                    base_version=bubble.version,
                    op_id=op_id,
                )
        self._emit(EVENT_BUBBLE_UPDATED, self._repo.get_bubble(bubble_id))
        self._emit(EVENT_OUTBOX_CHANGED, self._outbox.pending_count())
        self.flush(limit=1)
        return op_id

    def _cancel_uploads(self, bubble_id: str, op_types: tuple[str, ...]) -> int:
        """取消某泡泡待上传的指定类型操作，并把对应本地行标记 `failed`。"""
        items = [
            item
            for item in self._outbox.list_for_bubble(bubble_id)
            if item.op_type in op_types and item.state in (OUTBOX_PENDING, OUTBOX_SYNCING)
        ]
        for item in items:
            if item.op_type == "reply":
                self._repo.set_reply_sync_state(item.op_id, "failed")
            elif item.op_type == "chat":
                self._repo.set_chat_sync_state(item.op_id, "failed")
        return self._outbox.delete_pending_for_bubble(bubble_id, op_types)

    # ------------------------------------------------------------------
    # 重放
    # ------------------------------------------------------------------
    def flush(self, *, limit: int = OUTBOX_BATCH_SIZE, now: Optional[str] = None) -> int:
        """重放离线队列（FIFO，串行）。

        Args:
            limit: 单次最多处理条数。
            now: 基准时间（测试可注入，用于跳过退避等待）。

        Returns:
            成功条数。
        """
        done = 0
        for item in self._outbox.next_batch(now=now or now_iso(), limit=limit):
            outcome = self._flush_one(item)
            if outcome == RESULT_DONE:
                done += 1
            elif outcome == RESULT_RETRY:
                break  # 网络问题，本轮停止，等下一个 tick
        self._emit(EVENT_OUTBOX_CHANGED, self._outbox.pending_count())
        return done

    def _flush_one(self, item) -> str:
        """重放单条，返回 `done` / `retry` / `conflict` / `failed`。"""
        bubble = self._repo.get_bubble(item.bubble_id)
        if item.op_type in ("reply", "chat") and bubble is not None and bubble.status == "saved_private":
            logger.info("跳过私密泡泡的 %s 上传 bubble=%s", item.op_type, item.bubble_id)
            self._outbox.mark_failed(item.op_id, error="私密泡泡的内容不会上传")
            self._drop_local(item)
            self._emit(EVENT_ACTION_FAILED, (item.op_type, "私密泡泡的内容不会上传", 403))
            return RESULT_FAILED

        self._outbox.mark_syncing(item.op_id)
        try:
            payload = json.loads(item.payload)
        except ValueError:
            logger.exception("outbox payload 损坏 op=%s", item.op_id)
            self._outbox.mark_failed(item.op_id, error="本地队列数据损坏")
            return RESULT_FAILED
        if not isinstance(payload, dict):
            self._outbox.mark_failed(item.op_id, error="本地队列数据损坏")
            return RESULT_FAILED

        result = self._dispatch(item, payload)
        if result.ok:
            self._mark_online()
            self._apply_success(item, result)
            self._outbox.mark_done(item.op_id)
            return RESULT_DONE
        if result.is_network_error:
            self._mark_offline()
            self._outbox.mark_retry(item.op_id, error=result.message)
            return RESULT_RETRY
        if result.is_retryable:
            self._outbox.mark_retry(
                item.op_id, error=f"{result.status_code} {result.message}".strip()
            )
            return RESULT_RETRY
        if result.status_code == 409:
            return self._handle_conflict(item, result)

        self._outbox.mark_failed(
            item.op_id, error=f"{result.status_code} {result.message}".strip()
        )
        self._drop_local(item)
        self._emit(EVENT_ACTION_FAILED, (item.op_type, result.message, result.status_code))
        logger.info(
            "重放永久失败 op=%s type=%s status=%s", item.op_id, item.op_type, result.status_code
        )
        return RESULT_FAILED

    def _dispatch(self, item, payload: dict[str, Any]) -> ApiResult:
        """按 `op_type` 调用对应 API。"""
        if item.op_type == "publish":
            return self._api.create_bubble(
                op_id=item.op_id,
                content=str(payload.get("content") or ""),
                emotion_tag=str(payload.get("emotion_tag") or ""),
                reply_mode=str(payload.get("reply_mode") or ""),
                bubble_id=str(payload.get("id") or item.bubble_id),
                created_at=payload.get("created_at"),
                base_version=item.base_version,
            )
        if item.op_type == "reply":
            return self._api.reply(
                item.bubble_id, op_id=item.op_id, content=str(payload.get("content") or "")
            )
        if item.op_type == "chat":
            return self._api.post_chat(
                item.bubble_id,
                op_id=item.op_id,
                receiver=str(payload.get("receiver") or ""),
                content=str(payload.get("content") or ""),
            )
        if item.op_type == "resolve":
            return self._api.resolve(
                item.bubble_id, op_id=item.op_id, base_version=item.base_version
            )
        if item.op_type == "save":
            return self._api.save(
                item.bubble_id,
                op_id=item.op_id,
                is_public=bool(payload.get("is_public")),
                base_version=item.base_version,
            )
        logger.error("未知 op_type=%s op=%s", item.op_type, item.op_id)
        self._outbox.mark_failed(item.op_id, error=f"未知操作类型 {item.op_type}")
        return ApiResult(False, 400, "bad_request", "未知操作类型")

    def _handle_conflict(self, item, result: ApiResult) -> str:
        """409 处理（严格按 Q-4）。"""
        if item.op_type in ("resolve", "save"):
            # 作者优先：本地目标状态即最终状态，视为成功
            self._outbox.mark_done(item.op_id)
            self._repo.set_bubble_sync_state(item.bubble_id, "synced")
            self._emit(EVENT_ACTION_OK, (item.op_type, item.bubble_id))
            logger.info("409 作者优先，本地状态生效 op=%s", item.op_id)
            return RESULT_DONE
        # 非作者冲突：服务端优先，丢弃本地乐观写入并拉取覆盖
        self._outbox.mark_failed(item.op_id, error=result.message or "服务端数据已更新")
        self._drop_local(item)
        self.detail(item.bubble_id)
        self._emit(
            EVENT_ACTION_FAILED,
            (item.op_type, result.message or "服务端数据已更新", 409),
        )
        return RESULT_CONFLICT

    def _drop_local(self, item) -> None:
        """丢弃本地乐观写入（按 `op_id`）。"""
        if item.op_type == "reply":
            self._repo.delete_reply_by_op(item.op_id)
            self._emit(EVENT_REPLIES_UPDATED, (item.bubble_id, self._repo.list_replies(item.bubble_id)))
        elif item.op_type == "chat":
            self._repo.delete_chat_by_op(item.op_id)
            self._emit(EVENT_CHATS_UPDATED, (item.bubble_id, self._repo.list_chats(item.bubble_id)))
        elif item.op_type == "publish":
            # 内容非法时保留本地内容（用户可复制），只标失败
            self._repo.set_bubble_sync_state(item.bubble_id, "failed")

    def _apply_success(self, item, result: ApiResult) -> None:
        """把服务端返回落到本地。"""
        data = result.data if isinstance(result.data, dict) else {}
        if item.op_type in ("publish", "resolve", "save"):
            if data:
                bubble = bubble_from_dto(data)
                self._repo.upsert_bubble(bubble)
                self._repo.set_bubble_sync_state(bubble.id, "synced")
                self._emit(EVENT_BUBBLE_UPDATED, self._repo.get_bubble(bubble.id) or bubble)
        elif item.op_type == "reply":
            dto = data.get("reply")
            if isinstance(dto, dict):
                reply = reply_from_dto(dto, bubble_id=item.bubble_id)
                if not reply.op_id:
                    # 服务端理应回显 op_id；缺失时用本地的，避免产生重复行
                    reply = replace(reply, op_id=item.op_id)
                self._repo.upsert_reply(reply)
                self._repo.set_reply_sync_state(item.op_id, "synced")
            self._emit(
                EVENT_REPLIES_UPDATED, (item.bubble_id, self._repo.list_replies(item.bubble_id))
            )
        elif item.op_type == "chat":
            dto = data.get("message")
            if isinstance(dto, dict):
                message = chat_from_dto(dto, bubble_id=item.bubble_id)
                if not message.op_id:
                    message = replace(message, op_id=item.op_id)
                self._repo.upsert_chat(message)
                self._repo.set_chat_sync_state(item.op_id, "synced")
            self._emit(
                EVENT_CHATS_UPDATED, (item.bubble_id, self._repo.list_chats(item.bubble_id))
            )
        self._emit(EVENT_ACTION_OK, (item.op_type, item.bubble_id))


__all__ = [
    "SyncActionsMixin",
    "EVENT_ONLINE_CHANGED",
    "EVENT_BUBBLES_PULLED",
    "EVENT_BUBBLE_UPDATED",
    "EVENT_REPLIES_UPDATED",
    "EVENT_CHATS_UPDATED",
    "EVENT_OUTBOX_CHANGED",
    "EVENT_ACTION_FAILED",
    "EVENT_ACTION_OK",
    "EVENT_MINE_UPDATED",
    "RESULT_DONE",
    "RESULT_RETRY",
    "RESULT_CONFLICT",
    "RESULT_FAILED",
]
