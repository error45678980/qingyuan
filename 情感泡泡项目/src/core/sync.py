"""同步引擎：拉取 / 在线探测 / 生命周期（架构文档 §6.2、§7）。

**纯 Python 实现，不 import PyQt5**（因此可被 `tests/` 直接导入）。
UI 层的 `QObject` 包装在 `src/ui/net_worker.py`（`SyncManager`），它只负责：
    1. 把引擎事件转成 `pyqtSignal`；
    2. 在自己的线程里驱动定时器。

写操作与队列重放在 `sync_actions.SyncActionsMixin`（避免单文件超 500 行）。
线程模型：本类所有方法都在 **NetWorker 线程**执行；唯一写者是它，避免 `database is locked`。
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from .api import ApiClient, ApiResult
from .config import AppConfig
from .constants import PAGE_SIZE_DEFAULT
from .dto import bubble_from_dto, chat_from_dto, reply_from_dto
from .errors import AppError
from .models import Bubble, ChatMessage, Reply
from .outbox import Outbox
from .repo import Repo
from .sync_actions import (
    EVENT_ACTION_FAILED,
    EVENT_ACTION_OK,
    EVENT_BUBBLE_UPDATED,
    EVENT_BUBBLES_PULLED,
    EVENT_CHATS_UPDATED,
    EVENT_MINE_UPDATED,
    EVENT_ONLINE_CHANGED,
    EVENT_OUTBOX_CHANGED,
    EVENT_REPLIES_UPDATED,
    RESULT_CONFLICT,
    RESULT_DONE,
    RESULT_FAILED,
    RESULT_RETRY,
    SyncActionsMixin,
)
from .timeutil import now_iso

logger = logging.getLogger("core.sync")

#: 事件监听器签名：`(event_name, payload) -> None`。
EventListener = Callable[[str, Any], None]

class SyncEngine(SyncActionsMixin):
    """同步引擎（线程内单实例）。"""

    def __init__(
        self,
        repo: Repo,
        api: ApiClient,
        config: AppConfig,
        *,
        listener: Optional[EventListener] = None,
        outbox: Optional[Outbox] = None,
    ) -> None:
        """构造。

        Args:
            repo: 客户端仓储。
            api: HTTP 客户端。
            config: 客户端配置（昵称、token）。
            listener: 事件监听器（UI 层用来转信号）。
            outbox: 离线队列，缺省基于 `repo` 构造。
        """
        self._repo = repo
        self._api = api
        self._config = config
        self._outbox = outbox or Outbox(repo)
        self._listener = listener
        self._online = True
        self._fail_count = 0

    # ------------------------------------------------------------------
    # 事件
    # ------------------------------------------------------------------
    def set_listener(self, listener: Optional[EventListener]) -> None:
        """替换事件监听器。"""
        self._listener = listener

    def _emit(self, event: str, payload: Any = None) -> None:
        """派发事件（监听器异常不得打断同步）。"""
        if self._listener is None:
            return
        try:
            self._listener(event, payload)
        except Exception:  # 监听器是外部代码，必须隔离
            logger.exception("事件监听器处理 %s 失败", event)

    # ------------------------------------------------------------------
    # 在线状态
    # ------------------------------------------------------------------
    @property
    def is_online(self) -> bool:
        """当前是否判定为在线。"""
        return self._online

    @property
    def pending_count(self) -> int:
        """待同步条数。"""
        return self._outbox.pending_count()

    def _mark_online(self) -> None:
        """标记在线（状态变化才发事件）。"""
        self._fail_count = 0
        if not self._online:
            self._online = True
            self._emit(EVENT_ONLINE_CHANGED, True)

    def _mark_offline(self) -> None:
        """累计网络失败，达到阈值才标记离线（防抖动）。"""
        from .constants import OFFLINE_FAILURE_THRESHOLD

        self._fail_count += 1
        if self._online and self._fail_count >= OFFLINE_FAILURE_THRESHOLD:
            self._online = False
            self._emit(EVENT_ONLINE_CHANGED, False)

    def _note_result(self, result: ApiResult) -> None:
        """根据一次调用结果更新在线状态。"""
        if result.ok:
            self._mark_online()
        elif result.is_network_error:
            self._mark_offline()
        else:
            # 服务端有响应（4xx/5xx）说明网络可达
            self._fail_count = 0

    def probe(self) -> bool:
        """主动探测服务端是否可达。"""
        result = self._api.health()
        self._note_result(result)
        return result.ok

    # ------------------------------------------------------------------
    # 拉取
    # ------------------------------------------------------------------
    def pull(
        self,
        *,
        emotion: Optional[str] = None,
        q: Optional[str] = None,
        limit: int = PAGE_SIZE_DEFAULT,
        cursor: Optional[str] = None,
    ) -> bool:
        """拉取公开泡泡列表并落库。

        Args:
            emotion: 情感筛选。
            q: 关键词。
            limit: 每页条数。
            cursor: 分页游标。

        Returns:
            是否成功。
        """
        result = self._api.list_bubbles(
            limit=limit, cursor=cursor, emotion=emotion, q=q
        )
        if not result.ok:
            self._note_result(result)
            logger.info("pull 失败 status=%s code=%s", result.status_code, result.code)
            return False
        self._mark_online()
        data = result.data if isinstance(result.data, dict) else {}
        items: list[Bubble] = []
        for dto in data.get("items") or []:
            if not isinstance(dto, dict):
                continue
            items.append(bubble_from_dto(dto))
        for bubble in items:
            self._repo.upsert_bubble(bubble)
        self._repo.set_meta("last_pull_at", now_iso())
        next_cursor = data.get("next_cursor")
        if isinstance(next_cursor, str) and next_cursor:
            self._repo.set_meta("last_pull_cursor", next_cursor)
        self._emit(
            EVENT_BUBBLES_PULLED,
            (items, int(data.get("total") or 0), bool(data.get("has_more"))),
        )
        logger.info(
            "pull 完成 items=%d total=%s has_more=%s",
            len(items),
            data.get("total"),
            data.get("has_more"),
        )
        return True

    def pull_mine(self, group: str = "published", *, limit: int = PAGE_SIZE_DEFAULT) -> bool:
        """拉取「我的」某个分组。

        Args:
            group: `published` / `saved_public` / `saved_private` / `resolved`。
            limit: 每页条数。

        Returns:
            是否成功。
        """
        result = self._api.mine(group=group, limit=limit)
        if not result.ok:
            self._note_result(result)
            return False
        self._mark_online()
        data = result.data if isinstance(result.data, dict) else {}
        items = [
            bubble_from_dto(dto)
            for dto in (data.get("items") or [])
            if isinstance(dto, dict)
        ]
        for bubble in items:
            self._repo.upsert_bubble(bubble)
        self._emit(
            EVENT_MINE_UPDATED,
            (group, items, data.get("counts") or {}, bool(data.get("has_more"))),
        )
        return True

    def detail(self, bubble_id: str) -> bool:
        """拉取单条泡泡详情（含回复与本人可见的对话）。"""
        result = self._api.get_bubble(bubble_id)
        if not result.ok:
            self._note_result(result)
            return False
        self._mark_online()
        data = result.data if isinstance(result.data, dict) else {}
        try:
            bubble = bubble_from_dto(data)
        except AppError:
            logger.exception("详情响应缺少必需字段 bubble=%s", bubble_id)
            return False
        self._repo.upsert_bubble(bubble)
        replies: list[Reply] = []
        for dto in data.get("replies") or []:
            if not isinstance(dto, dict):
                continue
            reply = reply_from_dto(dto, bubble_id=bubble_id)
            if not reply.op_id:
                continue
            self._repo.upsert_reply(reply)
            replies.append(reply)
        chats: list[ChatMessage] = []
        for dto in data.get("chats") or []:
            if not isinstance(dto, dict):
                continue
            message = chat_from_dto(dto, bubble_id=bubble_id)
            if not message.op_id:
                continue
            self._repo.upsert_chat(message)
            chats.append(message)
        self._emit(EVENT_BUBBLE_UPDATED, bubble)
        self._emit(EVENT_REPLIES_UPDATED, (bubble_id, self._repo.list_replies(bubble_id)))
        self._emit(EVENT_CHATS_UPDATED, (bubble_id, self._repo.list_chats(bubble_id)))
        return True

    # ------------------------------------------------------------------
    # 启动 / 关闭
    # ------------------------------------------------------------------
    def bootstrap(self) -> None:
        """启动时的准备工作：崩溃恢复 + 会话登记。"""
        restored = self._outbox.reset_stale_syncing()
        if restored:
            self._emit(EVENT_OUTBOX_CHANGED, self._outbox.pending_count())
        self.ensure_session()

    def ensure_session(self) -> bool:
        """确保服务端已登记本设备并拿到 token。

        Returns:
            是否成功。
        """
        if not self._config.nickname:
            return False
        result = self._api.session(self._config.nickname)
        self._note_result(result)
        if result.ok and isinstance(result.data, dict):
            token = str(result.data.get("token") or "")
            if token:
                self._config = self._config.with_token(token)
            return True
        return False



__all__ = [
    "SyncEngine",
    "EventListener",
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
