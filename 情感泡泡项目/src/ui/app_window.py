"""主窗口（T-12）：页面栈 + 抽屉叠加 + 信号接线 + 关闭清理。

线程约定（架构 §6.1）：
    * UI 线程只做布局、绘制、**只读**本地库、投递任务；
    * 一切 HTTP / 写库在 `SyncManager`（NetWorker 线程）里；
    * 网络结果通过 `pyqtSignal` 排队回到 UI 线程。

因此"关闭服务器后点卡片"仍然是本地读库 + 渲染，耗时 < 300ms（R-03）。
"""

from __future__ import annotations

import logging
from typing import Optional

from PyQt5.QtCore import QRect, Qt
from PyQt5.QtGui import QCloseEvent, QKeySequence
from PyQt5.QtWidgets import (
    QApplication,
    QDesktopWidget,
    QMainWindow,
    QShortcut,
    QStackedWidget,
    QWidget,
)

from src.core.api import ApiClient
from src.core.config import AppConfig
from src.core.constants import APP_TITLE
from src.core.errors import ConfigError
from src.core.models import Bubble, ChatMessage, Reply
from src.core.repo import Repo
from src.core.sync import SyncEngine
from src.ui.net_worker import SyncThread
from src.ui.pages.chat_drawer import ChatDrawer
from src.ui.pages.detail_drawer import DetailDrawer
from src.ui.pages.login_page import LoginPage
from src.ui.pages.my_page import MyPage
from src.ui.pages.publish_page import PublishPage
from src.ui.pages.wall_page import WallPage
from src.ui.widgets.animation import BubbleAnimator
from src.ui.widgets.drawer import Drawer
from src.ui.widgets.toast import Toast

logger = logging.getLogger("ui.app_window")

#: 默认窗口尺寸。
DEFAULT_SIZE: tuple[int, int] = (1200, 800)
#: 最小窗口尺寸。
MIN_SIZE: tuple[int, int] = (1000, 700)
#: 首屏本地缓存条数。
LOCAL_PAGE_SIZE: int = 50

#: 全局文案。
TEXTS: dict[str, str] = {
    "published": "泡泡飘出去了 🫧",
    "replied": "回复成功 💬",
    "resolved": "泡泡已炸掉 💥",
    "saved_public": "已公开保留 📦",
    "saved_private": "已私密保存 🔒",
    "offline_action": "当前离线，已记在本地，联网后自动发出",
}


class AppWindow(QMainWindow):
    """应用主窗口。"""

    def __init__(
        self,
        config: AppConfig,
        repo: Repo,
        api: ApiClient,
        parent: Optional[QWidget] = None,
    ) -> None:
        """构造。

        Args:
            config: 客户端配置。
            repo: 客户端仓储（只读使用）。
            api: HTTP 客户端（交给同步线程）。
            parent: 父窗口。
        """
        super().__init__(parent)
        self._config = config
        self._repo = repo
        self._api = api
        self._engine: Optional[SyncEngine] = None
        self._sync: Optional[SyncThread] = None
        self._online = True
        self._detail: Optional[DetailDrawer] = None
        self._chat: Optional[ChatDrawer] = None
        self._detail_id: Optional[str] = None
        self._chat_bubble_id: Optional[str] = None
        self._cache: dict[str, Bubble] = {}

        self.setWindowTitle(APP_TITLE)
        self.setMinimumSize(*MIN_SIZE)
        self._restore_geometry()

        self._animator = BubbleAnimator(parent=self)
        self._build_ui()
        self._bind_shortcuts()

        if self._config.nickname:
            self._enter_wall(self._config.nickname)
        else:
            self._goto(self._login)
            self._login.focus_input()

    # ==================================================================
    # 构建
    # ==================================================================
    def _build_ui(self) -> None:
        """搭建页面栈与抽屉。"""
        self._root = QWidget(self)
        self.setCentralWidget(self._root)

        self._stack = QStackedWidget(self._root)
        self._stack.setGeometry(self._root.rect())
        self._stack.setStyleSheet("QStackedWidget { background: transparent; }")

        self._login = LoginPage(self._config.nickname)
        self._login.login_success.connect(self._on_login)

        self._wall = WallPage(self._animator, nickname=self._config.nickname)
        self._wall.open_detail.connect(self._open_detail)
        self._wall.request_publish.connect(self._open_publish)
        self._wall.request_refresh.connect(self._request_pull)
        self._wall.request_more.connect(self._request_pull)
        self._wall.request_flush.connect(self._request_flush)
        self._wall.switch_user.connect(self._switch_user)
        self._wall.show_mine.connect(self._open_mine)
        self._wall.filter_changed.connect(lambda _emotion: self._request_pull())
        self._wall.search_changed.connect(lambda _query: None)

        self._my = MyPage(self._animator, nickname=self._config.nickname)
        self._my.open_detail.connect(self._open_detail)
        self._my.back_to_wall.connect(lambda: self._goto(self._wall))
        self._my.switch_user.connect(self._switch_user)
        self._my.request_group.connect(self._load_mine_group)

        self._publish = PublishPage()
        self._publish.submitted.connect(self._on_publish_submitted)
        self._publish.cancelled.connect(lambda: self._goto(self._wall))

        self._stack.addWidget(self._login)
        self._stack.addWidget(self._wall)
        self._stack.addWidget(self._my)
        self._stack.addWidget(self._publish)

        self._drawer = Drawer(self._root)
        self._drawer.closed.connect(self._on_drawer_closed)
        self._toast = Toast(self._root)

    def _bind_shortcuts(self) -> None:
        """键盘可达性：Esc / Ctrl+N / Ctrl+F。"""
        QShortcut(QKeySequence(Qt.Key_Escape), self, activated=self._on_escape)
        QShortcut(QKeySequence("Ctrl+N"), self, activated=self._open_publish)
        QShortcut(QKeySequence("Ctrl+F"), self, activated=self._on_focus_search)

    # ==================================================================
    # 窗口几何记忆
    # ==================================================================
    def _restore_geometry(self) -> None:
        """恢复上次窗口几何；越界则回到主屏居中（避免窗口跑到屏幕外）。"""
        width, height = DEFAULT_SIZE
        state = dict(self._config.window or {})
        if state:
            width = max(MIN_SIZE[0], int(state.get("w", width)))
            height = max(MIN_SIZE[1], int(state.get("h", height)))
            candidate = QRect(
                int(state.get("x", 0)), int(state.get("y", 0)), width, height
            )
            if self._is_visible_on_some_screen(candidate):
                self.setGeometry(candidate)
                return
        screen = QDesktopWidget().availableGeometry()
        width = min(width, max(MIN_SIZE[0], screen.width() - 80))
        height = min(height, max(MIN_SIZE[1], screen.height() - 80))
        self.resize(width, height)
        frame = self.frameGeometry()
        frame.moveCenter(screen.center())
        self.move(frame.topLeft())

    @staticmethod
    def _is_visible_on_some_screen(rect: QRect) -> bool:
        """矩形是否与任一屏幕的可用区域有足够交集。"""
        for screen in QApplication.screens():
            available = screen.availableGeometry()
            inter = available.intersected(rect)
            if inter.width() >= 200 and inter.height() >= 150:
                return True
        return False

    def _save_geometry(self) -> None:
        """把当前几何写入 `config.json`。"""
        if self.isMaximized() or self.isFullScreen():
            return
        geometry = self.geometry()
        self._config = self._config.with_window(
            geometry.x(), geometry.y(), geometry.width(), geometry.height()
        )
        try:
            self._config.save()
        except ConfigError:
            logger.exception("窗口几何保存失败")

    # ==================================================================
    # 页面切换
    # ==================================================================
    def _goto(self, page: QWidget) -> None:
        """切换页面。"""
        self._stack.setCurrentWidget(page)
        self._stack.setGeometry(self._root.rect())
        if page is self._wall:
            self._wall.set_active("wall")
            self._animator.start()
        elif page is self._my:
            self._animator.start()
        else:
            self._animator.set_paused(True)

    def current_page(self) -> QWidget:
        """当前页面（测试用）。"""
        return self._stack.currentWidget()

    # ==================================================================
    # 登录 / 切换用户
    # ==================================================================
    def _on_login(self, nickname: str) -> None:
        """登录成功：写配置 → 起同步线程 → 先进墙（先渲染本地缓存）。"""
        self._config = self._config.with_nickname(nickname)
        try:
            self._config.save()
        except ConfigError:
            logger.exception("昵称保存失败")
        self._enter_wall(nickname)

    def _enter_wall(self, nickname: str) -> None:
        """进入泡泡墙（含同步线程启动）。"""
        self._wall.set_nickname(nickname)
        self._my.set_nickname(nickname)
        self._goto(self._wall)
        self._load_local_wall()
        self._start_sync()
        self._request_pull()

    def _switch_user(self) -> None:
        """切换用户：停同步、清缓存、回登录页。"""
        self._stop_sync()
        self._drawer.close_drawer()
        self._cache.clear()
        self._wall.apply_bubbles([], total=0, has_more=False)
        self._my.apply_group(self._my.current_group(), [])
        self._config = self._config.with_nickname("")
        try:
            self._config.save()
        except ConfigError:
            logger.exception("清空昵称失败")
        self._goto(self._login)
        self._login.focus_input()

    # ==================================================================
    # 同步线程
    # ==================================================================
    def _start_sync(self) -> None:
        """构造引擎与线程并接线。"""
        if self._sync is not None:
            return
        self._engine = SyncEngine(self._repo, self._api, self._config)
        self._sync = SyncThread(self._engine)
        manager = self._sync.manager
        manager.online_changed.connect(self._on_online)
        manager.bubbles_pulled.connect(self._on_bubbles_pulled)
        manager.bubble_updated.connect(self._on_bubble_updated)
        manager.replies_updated.connect(self._on_replies_updated)
        manager.chats_updated.connect(self._on_chats_updated)
        manager.outbox_changed.connect(self._on_outbox_changed)
        manager.action_failed.connect(self._on_action_failed)
        manager.action_ok.connect(self._on_action_ok)
        manager.mine_updated.connect(self._on_mine_updated)
        self._sync.start()

    def _stop_sync(self) -> None:
        """停线程。"""
        if self._sync is None:
            return
        try:
            self._sync.stop()
        except RuntimeError:
            logger.exception("同步线程停止异常")
        self._sync = None
        self._engine = None

    def _request_pull(self) -> None:
        """请求刷新泡泡墙。"""
        if self._engine is not None:
            self._engine_request("pull")

    def _request_flush(self) -> None:
        """请求重放离线队列。"""
        if self._engine is not None:
            self._engine_request("flush")

    def _engine_request(self, kind: str) -> None:
        """通过 `SyncManager` 投递任务（保持 UI 线程零网络）。"""
        if self._sync is None:
            return
        manager = self._sync.manager
        if kind == "pull":
            manager.request_pull(limit=LOCAL_PAGE_SIZE)
        elif kind == "flush":
            manager.request_flush()

    # ==================================================================
    # 本地缓存加载（离线优先，UI 线程只读）
    # ==================================================================
    def _load_local_wall(self) -> None:
        """先用本地库渲染首屏，网络回来再 diff 刷新。"""
        bubbles = self._repo.list_public(limit=LOCAL_PAGE_SIZE)
        for bubble in bubbles:
            self._cache[bubble.id] = bubble
        self._wall.apply_bubbles(bubbles, total=len(bubbles), has_more=False)

    def _open_mine(self) -> None:
        """进入「我的」。"""
        self._goto(self._my)
        self._load_mine_group(self._my.current_group())

    def _load_mine_group(self, group: str) -> None:
        """加载某个分组的本地数据并请求服务端刷新。"""
        client_id = self._config.client_id
        nickname = self._config.nickname
        bubbles = self._repo.list_mine(group, client_id=client_id, nickname=nickname)
        counts = self._repo.count_by_group(client_id=client_id, nickname=nickname)
        self._my.apply_counts(counts)
        self._my.apply_group(group, bubbles)
        if self._engine is not None and self._sync is not None:
            self._sync.manager.request_pull_mine(group)

    # ==================================================================
    # 抽屉：详情 / 对话
    # ==================================================================
    def _open_publish(self) -> None:
        """打开发布页（Ctrl+N）。"""
        if not self._config.nickname:
            self._goto(self._login)
            return
        self._goto(self._publish)
        self._publish.focus_editor()

    def _open_detail(self, bubble_id: str) -> None:
        """打开详情抽屉（先渲染本地缓存，再异步拉最新）。"""
        bubble = self._cache.get(bubble_id) or self._repo.get_bubble(bubble_id)
        self._detail_id = bubble_id
        content = DetailDrawer(self._config.nickname, self._config.client_id)
        content.close_requested.connect(self._drawer.close_drawer)
        content.resolve_requested.connect(self._on_resolve_requested)
        content.save_requested.connect(self._on_save_requested)
        content.reply_requested.connect(self._on_reply_requested)
        content.open_chat.connect(self._open_chat)
        self._detail = content
        self._drawer.open(content)
        if bubble is None:
            content.show_missing()
        else:
            self._render_detail()
        if self._engine is not None and self._sync is not None:
            self._sync.manager.request_detail(bubble_id)

    def _render_detail(self) -> None:
        """按本地库渲染详情。"""
        if self._detail is None or self._detail_id is None:
            return
        bubble = self._cache.get(self._detail_id) or self._repo.get_bubble(self._detail_id)
        if bubble is None:
            self._detail.show_missing()
            return
        replies: list[Reply] = self._repo.list_replies(self._detail_id)
        chats: list[ChatMessage] = self._repo.list_chats(self._detail_id)
        count = self._repo.my_reply_count(
            self._detail_id, self._config.client_id, self._config.nickname
        )
        chatted = self._repo.has_chatted(
            self._detail_id, self._config.client_id, self._config.nickname
        )
        self._detail.render(
            bubble,
            replies,
            chats=chats,
            my_reply_count=count,
            has_chatted=chatted,
        )
        self._detail.set_offline(not self._online)

    def _open_chat(self, bubble_id: str, peer: str) -> None:
        """打开对话抽屉（`peer` 由详情/墙面算好传入，R-07）。"""
        bubble = self._cache.get(bubble_id) or self._repo.get_bubble(bubble_id)
        if bubble is None or not peer:
            return
        self._chat_bubble_id = bubble_id
        content = ChatDrawer(self._config.nickname, bubble, peer)
        content.close_requested.connect(self._drawer.close_drawer)
        content.send_requested.connect(self._on_send_chat)
        self._chat = content
        self._drawer.open(content)
        self._render_chat()
        if self._engine is not None and self._sync is not None:
            self._sync.manager.request_detail(bubble_id)

    def _render_chat(self, chats: Optional[list[ChatMessage]] = None) -> None:
        """渲染对话（按 peer 过滤，R-06）。"""
        if self._chat is None or self._chat_bubble_id is None:
            return
        bubble = self._cache.get(self._chat_bubble_id) or self._repo.get_bubble(
            self._chat_bubble_id
        )
        if bubble is None:
            return
        if chats is None:
            chats = self._repo.list_chats(self._chat_bubble_id, peer=self._chat.peer())
        self._chat.render(bubble, self._chat.peer(), chats)
        self._chat.set_offline(not self._online)
        self._chat.set_closed_bubble(bubble.status == "resolved")

    def _on_drawer_closed(self) -> None:
        """抽屉关闭后清引用。"""
        self._detail = None
        self._chat = None
        self._detail_id = None
        self._chat_bubble_id = None

    def _on_escape(self) -> None:
        """Esc：先关抽屉，再退出发布页。"""
        if self._drawer.is_open():
            self._drawer.close_drawer()
            return
        if self.current_page() is self._publish:
            self._goto(self._wall)

    def _on_focus_search(self) -> None:
        """Ctrl+F：聚焦搜索框。"""
        if self.current_page() is self._wall:
            self._wall.focus_search()

    # ==================================================================
    # 用户动作 → 投递任务
    # ==================================================================
    def _on_publish_submitted(self, payload: dict) -> None:
        """发布：投递到同步线程（离线时自动入队）。"""
        self._publish.set_submitting(True)
        if self._sync is None:
            self._publish.show_error(TEXTS["offline_action"])
            return
        self._sync.manager.request_publish(
            content=str(payload.get("content") or ""),
            emotion_tag=str(payload.get("emotion_tag") or ""),
            reply_mode=str(payload.get("reply_mode") or "single"),
        )

    def _on_reply_requested(self, bubble_id: str, content: str) -> None:
        """回复。"""
        if self._sync is not None:
            self._sync.manager.request_reply(bubble_id=bubble_id, content=content)

    def _on_send_chat(self, bubble_id: str, receiver: str, content: str) -> None:
        """发对话（`receiver` 由抽屉传入，不再推导）。"""
        if self._sync is not None:
            self._sync.manager.request_chat(
                bubble_id=bubble_id, receiver=receiver, content=content
            )

    def _on_resolve_requested(self, bubble_id: str) -> None:
        """标记已解决。"""
        if self._sync is not None:
            self._sync.manager.request_resolve(bubble_id=bubble_id)

    def _on_save_requested(self, bubble_id: str, is_public: bool) -> None:
        """公开/私密保留。"""
        if self._sync is not None:
            self._sync.manager.request_save(bubble_id=bubble_id, is_public=is_public)

    # ==================================================================
    # 同步线程回调（全部在 UI 线程执行）
    # ==================================================================
    def _on_online(self, online: bool) -> None:
        """在线状态变化。"""
        self._online = bool(online)
        self._wall.set_online(self._online)
        self._my.set_online(self._online)
        if self._detail is not None:
            self._detail.set_offline(not self._online)
        if self._chat is not None:
            self._chat.set_offline(not self._online)

    def _on_bubbles_pulled(self, items: object, total: int, has_more: bool) -> None:
        """拉取完成：diff 刷新泡泡墙。

        服务端返回的是"服务器上有的泡泡"，但本地可能还有**只存在于本机**的泡泡
        （离线发布、或从未成功同步过）。这些泡泡如果直接以服务端结果覆盖，
        就会从墙上凭空消失——用户的感受是"我刚发的泡泡没了"。
        因此这里做一次合并：服务端结果 + 本机未同步 / 我发布但服务端没有的泡泡。
        """
        bubbles = [item for item in (items or []) if isinstance(item, Bubble)]
        for bubble in bubbles:
            self._cache[bubble.id] = bubble
        if self.current_page() is self._wall:
            merged = self._merge_local_only(bubbles)
            self._wall.apply_bubbles(merged, total=int(total), has_more=bool(has_more))

    def _merge_local_only(self, remote: list[Bubble]) -> list[Bubble]:
        """把"本机独有"的泡泡合并进服务端结果（按 id 去重，保持时间倒序）。

        Args:
            remote: 服务端返回的当前页泡泡。

        Returns:
            合并后的列表。
        """
        seen = {bubble.id for bubble in remote}
        try:
            local = self._repo.list_public(limit=LOCAL_PAGE_SIZE)
        except Exception:  # 读本地失败不应影响整墙刷新
            logger.exception("读取本地泡泡失败，本次仅展示服务端结果")
            return remote
        extra = [
            bubble
            for bubble in local
            if bubble.id not in seen
            and (bubble.sync_state != "synced" or self._is_mine(bubble))
        ]
        if not extra:
            return remote
        for bubble in extra:
            self._cache.setdefault(bubble.id, bubble)
        return sorted(remote + extra, key=lambda item: (item.created_at, item.id), reverse=True)

    def _is_mine(self, bubble: Bubble) -> bool:
        """是否是我发布的（设备 ID 优先，旧数据回退昵称）。"""
        if bubble.author_client_id:
            return bubble.author_client_id == self._config.client_id
        return bool(self._config.nickname) and bubble.author_nickname == self._config.nickname

    def _on_bubble_updated(self, bubble: object) -> None:
        """单条泡泡变化（发布 / 解决 / 保存 / 详情刷新）。"""
        if not isinstance(bubble, Bubble):
            return
        self._cache[bubble.id] = bubble
        self._wall.update_bubble(bubble)
        if self._detail_id == bubble.id:
            self._render_detail()

    def _on_replies_updated(self, bubble_id: str, replies: object) -> None:
        """回复列表变化。"""
        if self._detail_id == str(bubble_id):
            self._render_detail()

    def _on_chats_updated(self, bubble_id: str, chats: object) -> None:
        """对话列表变化。"""
        if self._chat_bubble_id != str(bubble_id) or self._chat is None:
            return
        messages = [item for item in (chats or []) if isinstance(item, ChatMessage)]
        peer = self._chat.peer()
        filtered = [
            message
            for message in messages
            if peer in (message.sender_nickname, message.receiver_nickname)
        ]
        self._render_chat(filtered)

    def _on_outbox_changed(self, count: int) -> None:
        """待同步条数。"""
        self._wall.set_pending(int(count))
        self._my.set_pending(int(count))

    def _on_mine_updated(
        self, group: str, items: object, counts: object, has_more: bool
    ) -> None:
        """「我的」分组刷新。"""
        bubbles = [item for item in (items or []) if isinstance(item, Bubble)]
        for bubble in bubbles:
            self._cache[bubble.id] = bubble
        if isinstance(counts, dict):
            self._my.apply_counts({str(key): int(value) for key, value in counts.items()})
        self._my.apply_group(str(group), bubbles)

    def _on_action_ok(self, action: str, payload: object) -> None:
        """写操作成功。"""
        bubble_id = str(payload or "")
        if action == "publish":
            self._publish.reset()
            self._goto(self._wall)
            bubble = self._repo.get_bubble(bubble_id)
            if bubble is not None:
                self._cache[bubble.id] = bubble
                self._wall.prepend_bubble(bubble)
            self._show_toast(TEXTS["published"])
            return
        if action == "reply":
            if self._detail is not None:
                self._detail.clear_reply_input()
                self._render_detail()
            self._show_toast(TEXTS["replied"])
            return
        if action == "chat":
            if self._chat is not None:
                self._chat.clear_input()
                self._render_chat()
            return
        if action == "resolve":
            self._drawer.close_drawer()
            self._wall.remove_bubble_animated(bubble_id)
            self._show_toast(TEXTS["resolved"])
            self._refresh_mine_if_visible()
            return
        if action == "save":
            self._drawer.close_drawer()
            bubble = self._cache.get(bubble_id) or self._repo.get_bubble(bubble_id)
            is_private = bubble is not None and bubble.status == "saved_private"
            if is_private:
                self._wall.drop_bubble(bubble_id)
                self._show_toast(TEXTS["saved_private"])
            else:
                self._show_toast(TEXTS["saved_public"])
            self._refresh_mine_if_visible()

    def _on_action_failed(self, action: str, message: str, status_code: int) -> None:
        """写操作失败（中文提示，不清空输入）。"""
        text = message or "操作失败，请稍后再试"
        if action == "publish":
            self._publish.set_submitting(False)
            self._publish.show_error(text)
            return
        if action == "reply" and self._detail is not None:
            self._detail.flash(text)
            return
        if action == "chat" and self._chat is not None:
            self._chat.set_closed_bubble(status_code == 403)
            self._show_toast(text)
            return
        self._show_toast(text)

    def _refresh_mine_if_visible(self) -> None:
        """在「我的」页时刷新当前分组。"""
        if self.current_page() is self._my:
            self._load_mine_group(self._my.current_group())

    def _show_toast(self, text: str) -> None:
        """在当前页底部浮出提示。"""
        self._toast.show_text(text)

    # ==================================================================
    # 事件
    # ==================================================================
    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """同步子控件几何（栈 + 抽屉 + 提示）。"""
        super().resizeEvent(event)
        self._stack.setGeometry(self._root.rect())
        self._drawer.setGeometry(self._root.rect())
        if self._toast.isVisible():
            self._toast.raise_()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt 命名
        """关闭：保存几何 → 停线程 → 关连接。"""
        self._save_geometry()
        self._stop_sync()
        self._animator.stop()
        try:
            self._repo.close()
        except RuntimeError:
            logger.exception("关闭数据库连接失败")
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # 测试辅助
    # ------------------------------------------------------------------
    def wall(self) -> WallPage:
        """泡泡墙页面。"""
        return self._wall

    def my_page(self) -> MyPage:
        """我的页面。"""
        return self._my

    def publish_page(self) -> PublishPage:
        """发布页面。"""
        return self._publish

    def login_page(self) -> LoginPage:
        """登录页面。"""
        return self._login

    def drawer(self) -> Drawer:
        """抽屉容器。"""
        return self._drawer

    def detail_widget(self) -> Optional[DetailDrawer]:
        """当前详情内容（测试用）。"""
        return self._detail

    def chat_widget(self) -> Optional[ChatDrawer]:
        """当前对话内容（测试用）。"""
        return self._chat


__all__ = ["AppWindow", "TEXTS", "DEFAULT_SIZE", "MIN_SIZE"]
