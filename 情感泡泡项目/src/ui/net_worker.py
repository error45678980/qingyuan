"""UI 层网络宿主：`SyncManager`（QObject）+ QThread 启动/停止。

**本文件是 UI 层唯一允许持有 QThread / QTimer 与网络对象的文件**（架构文档 §6.1）。
它本身不做任何 HTTP 与 SQL，只做三件事：
    1. 把 `SyncEngine` 事件转成 `pyqtSignal`（跨线程自动排队到 UI 线程）；
    2. 在自己的线程里驱动 `pull` / `outbox` 两个定时器；
    3. 为 UI 提供"投递任务、立即返回"的 `request_*` 入口。

禁令：`src/ui/**` 不得 `import requests`（R-03 的结构性保障）。
"""

from __future__ import annotations

import logging
import queue
from typing import Any, Callable, Optional

from PyQt5.QtCore import QObject, QThread, QTimer, pyqtSignal, pyqtSlot

from src.core.constants import OUTBOX_INTERVAL_MS, PULL_INTERVAL_MS
from src.core.sync import (
    EVENT_ACTION_FAILED,
    EVENT_ACTION_OK,
    EVENT_BUBBLE_UPDATED,
    EVENT_BUBBLES_PULLED,
    EVENT_CHATS_UPDATED,
    EVENT_MINE_UPDATED,
    EVENT_ONLINE_CHANGED,
    EVENT_OUTBOX_CHANGED,
    EVENT_REPLIES_UPDATED,
    SyncEngine,
)

logger = logging.getLogger("ui.net_worker")

#: 单次 drain 最多执行的任务数（防止 UI 连点饿死定时器）。
_MAX_TASKS_PER_DRAIN: int = 20


class SyncManager(QObject):
    """运行在 NetWorker 线程内的同步协调器；对 UI 只暴露信号与投递入口。"""

    # -- 信号（全部跨线程自动排队）--------------------------------------
    online_changed = pyqtSignal(bool)
    bubbles_pulled = pyqtSignal(object, int, bool)
    bubble_updated = pyqtSignal(object)
    replies_updated = pyqtSignal(str, object)
    chats_updated = pyqtSignal(str, object)
    outbox_changed = pyqtSignal(int)
    action_failed = pyqtSignal(str, str, int)
    action_ok = pyqtSignal(str, object)
    mine_updated = pyqtSignal(str, object, object, bool)

    def __init__(self, engine: SyncEngine, parent: Optional[QObject] = None) -> None:
        """构造。

        Args:
            engine: 同步引擎（纯 Python，不依赖 Qt）。
            parent: Qt 父对象。
        """
        super().__init__(parent)
        self._engine = engine
        self._queue: "queue.Queue[Optional[Callable[[], None]]]" = queue.Queue()
        self._pull_timer: Optional[QTimer] = None
        self._outbox_timer: Optional[QTimer] = None
        self._drain_timer: Optional[QTimer] = None
        self._pulling = False
        self._flushing = False
        self._running = False
        self._filter: dict[str, Any] = {"emotion": None, "q": None}
        engine.set_listener(self._on_engine_event)

    # ------------------------------------------------------------------
    # 引擎事件 → Qt 信号
    # ------------------------------------------------------------------
    def _on_engine_event(self, event: str, payload: Any) -> None:
        """在 worker 线程被引擎调用；只发信号，不碰 widget。"""
        if event == EVENT_ONLINE_CHANGED:
            self.online_changed.emit(bool(payload))
        elif event == EVENT_BUBBLES_PULLED:
            items, total, has_more = payload
            self.bubbles_pulled.emit(items, int(total), bool(has_more))
        elif event == EVENT_BUBBLE_UPDATED:
            self.bubble_updated.emit(payload)
        elif event == EVENT_REPLIES_UPDATED:
            bubble_id, replies = payload
            self.replies_updated.emit(str(bubble_id), replies)
        elif event == EVENT_CHATS_UPDATED:
            bubble_id, chats = payload
            self.chats_updated.emit(str(bubble_id), chats)
        elif event == EVENT_OUTBOX_CHANGED:
            self.outbox_changed.emit(int(payload or 0))
        elif event == EVENT_ACTION_FAILED:
            action, message, status = payload
            self.action_failed.emit(str(action), str(message), int(status))
        elif event == EVENT_ACTION_OK:
            action, detail = payload
            self.action_ok.emit(str(action), detail)
        elif event == EVENT_MINE_UPDATED:
            group, items, counts, has_more = payload
            self.mine_updated.emit(str(group), items, counts, bool(has_more))
        else:  # pragma: no cover - 引擎新增事件时提醒
            logger.debug("未映射的引擎事件: %s", event)

    # ------------------------------------------------------------------
    # 生命周期（必须在线程启动后调用）
    # ------------------------------------------------------------------
    @pyqtSlot()
    def start(self) -> None:
        """建定时器并做首轮拉取（`thread.started` 信号连到这里）。"""
        if self._running:
            return
        self._running = True
        self._engine.bootstrap()

        self._pull_timer = QTimer(self)
        self._pull_timer.setInterval(PULL_INTERVAL_MS)
        self._pull_timer.timeout.connect(self._tick_pull)
        self._pull_timer.start()

        self._outbox_timer = QTimer(self)
        self._outbox_timer.setInterval(OUTBOX_INTERVAL_MS)
        self._outbox_timer.timeout.connect(self._tick_outbox)
        self._outbox_timer.start()

        self._drain_timer = QTimer(self)
        self._drain_timer.setInterval(30)
        self._drain_timer.timeout.connect(self._drain)
        self._drain_timer.start()

        self.outbox_changed.emit(self._engine.pending_count)
        self._tick_outbox()
        self._tick_pull()
        logger.info("SyncManager 已启动")

    @pyqtSlot()
    def stop(self) -> None:
        """停定时器并投递哨兵。"""
        if not self._running:
            return
        self._running = False
        for timer in (self._pull_timer, self._outbox_timer, self._drain_timer):
            if timer is not None:
                timer.stop()
        self._queue.put(None)
        logger.info("SyncManager 已停止")

    # ------------------------------------------------------------------
    # UI 投递入口（只入队，立即返回）
    # ------------------------------------------------------------------
    @pyqtSlot()
    def request_pull(
        self,
        *,
        emotion: Optional[str] = None,
        q: Optional[str] = None,
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> None:
        """请求拉取泡泡列表。"""
        self._filter = {"emotion": emotion, "q": q}

        def task() -> None:
            self._engine.pull(emotion=emotion, q=q, limit=limit, cursor=cursor)

        self._queue.put(task)

    @pyqtSlot()
    def request_pull_mine(self, group: str = "published") -> None:
        """请求拉取「我的」某分组。"""
        self._queue.put(lambda: self._engine.pull_mine(group))

    @pyqtSlot()
    def request_detail(self, bubble_id: str) -> None:
        """请求拉取详情。"""
        self._queue.put(lambda: self._engine.detail(bubble_id))

    @pyqtSlot()
    def request_publish(self, *, content: str, emotion_tag: str, reply_mode: str) -> None:
        """请求发布。"""
        self._queue.put(
            lambda: self._engine.publish(
                content=content, emotion_tag=emotion_tag, reply_mode=reply_mode
            )
        )

    @pyqtSlot()
    def request_reply(self, *, bubble_id: str, content: str) -> None:
        """请求回复。"""
        self._queue.put(lambda: self._engine.reply(bubble_id=bubble_id, content=content))

    @pyqtSlot()
    def request_chat(self, *, bubble_id: str, receiver: str, content: str) -> None:
        """请求发对话。"""
        self._queue.put(
            lambda: self._engine.chat(
                bubble_id=bubble_id, receiver=receiver, content=content
            )
        )

    @pyqtSlot()
    def request_resolve(self, *, bubble_id: str) -> None:
        """请求标记已解决。"""
        self._queue.put(lambda: self._engine.resolve(bubble_id=bubble_id))

    @pyqtSlot()
    def request_save(self, *, bubble_id: str, is_public: bool) -> None:
        """请求公开/私密保留。"""
        self._queue.put(
            lambda: self._engine.save(bubble_id=bubble_id, is_public=is_public)
        )

    @pyqtSlot()
    def request_flush(self) -> None:
        """请求立刻重放离线队列（离线提示条"重试"按钮）。"""
        self._queue.put(self._engine.flush)

    # ------------------------------------------------------------------
    # 线程内执行
    # ------------------------------------------------------------------
    def _drain(self) -> None:
        """执行 UI 投递的任务队列。"""
        for _ in range(_MAX_TASKS_PER_DRAIN):
            try:
                task = self._queue.get_nowait()
            except queue.Empty:
                return
            if task is None:
                return
            try:
                task()
            except Exception:  # 任务失败不得打死定时器
                logger.exception("投递任务执行失败")

    def _tick_pull(self) -> None:
        """定时拉取（防重入）。"""
        if self._pulling or not self._running:
            return
        self._pulling = True
        try:
            self._engine.pull(
                emotion=self._filter.get("emotion"), q=self._filter.get("q")
            )
        finally:
            self._pulling = False

    def _tick_outbox(self) -> None:
        """定时重放（防重入）。"""
        if self._flushing or not self._running:
            return
        self._flushing = True
        try:
            self._engine.flush()
        finally:
            self._flushing = False


class SyncThread:
    """`QThread` + `SyncManager` 的组装器（供 `AppWindow` 使用）。"""

    def __init__(self, engine: SyncEngine) -> None:
        """构造（不启动）。

        Args:
            engine: 同步引擎。
        """
        self.thread = QThread()
        self.manager = SyncManager(engine)
        self.manager.moveToThread(self.thread)
        self.thread.started.connect(self.manager.start)
        self.thread.finished.connect(self.manager.deleteLater)

    def start(self) -> None:
        """启动线程（`SyncManager.start` 在子线程执行）。"""
        self.thread.start()

    def stop(self) -> None:
        """停止线程并等待退出。"""
        try:
            self.manager.stop()
        finally:
            self.thread.quit()
            self.thread.wait(3000)


__all__ = ["SyncManager", "SyncThread"]
