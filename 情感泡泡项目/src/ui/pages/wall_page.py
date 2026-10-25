"""泡泡墙（T-10，R-08 / R-11 / R-12 / R-13 / R-14）。

刷新策略（规则 A-2）：
    * 按 `bubble.id` **diff 复用**已有 `BubbleView`，只有新增才创建 widget；
    * 数据没变（`Bubble.diff_key` 全等）就完全不碰 widget；
    * 顺序变化才重排（`layout.takeAt` + 重新 `addWidget`）；
    * 重排前后记录/恢复滚动位置，连续刷新滚动偏移 ≤10px。

新泡泡不打断视图（R-13）：用户已经滚动过时，新泡泡不立即插入，只累加计数并浮出
"↑ 有 N 个新泡泡"，点击才回到顶部展示。
"""

from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import QRect, Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from src.core.constants import EMOTIONS, MAX_SEARCH_LEN
from src.core.models import Bubble
from src.ui import theme
from src.ui.widgets.animation import BubbleAnimator
from src.ui.widgets.bubble_view import BubbleView
from src.ui.widgets.common import (
    Chip,
    EmptyState,
    FloatingButton,
    GhostButton,
    GradientPage,
    make_label,
)
from src.ui.widgets.flow_layout import FlowContainer
from src.ui.widgets.toast import Toast
from src.ui.widgets.top_bar import TopBar

#: 本页文案（架构 §11.1）。
TEXTS: dict[str, str] = {
    "brand": "💭 情缘",
    "all": "全部",
    "search_placeholder": "🔍 搜索泡泡或昵称…",
    "empty_icon": "💫",
    "empty_title": "还没有泡泡哦～",
    "empty_hint": "点击右下角的 ＋ 发布你的第一个心情吧！",
    "filter_empty": "这个分类下还没有泡泡",
    "search_empty": "没有找到相关泡泡",
    "loading": "正在寻找泡泡…",
    "offline": "⚠️ 离线（显示本地缓存）",
    "retry": "重试",
    "new_bubbles": "↑ 有 {count} 个新泡泡",
    "view": "查看",
}

#: 发布按钮右下角边距。
FAB_MARGIN: int = 32


class WallPage(GradientPage):
    """泡泡墙页面。"""

    open_detail = pyqtSignal(str)
    request_publish = pyqtSignal()
    request_refresh = pyqtSignal()
    request_more = pyqtSignal()
    request_flush = pyqtSignal()
    switch_user = pyqtSignal()
    show_mine = pyqtSignal()
    filter_changed = pyqtSignal(object)
    search_changed = pyqtSignal(str)

    def __init__(
        self,
        animator: Optional[BubbleAnimator] = None,
        *,
        nickname: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        """构造。

        Args:
            animator: 全局泡泡动画器（单例，由 `AppWindow` 持有）。
            nickname: 当前昵称。
            parent: 父控件。
        """
        super().__init__(parent)
        self._animator = animator if animator is not None else BubbleAnimator(self)
        self._all: list[Bubble] = []
        self._views: dict[str, BubbleView] = {}
        self._order: list[str] = []
        self._emotion: Optional[str] = None
        self._query: str = ""
        self._new_count: int = 0
        self._first_load_done: bool = False
        self._has_more: bool = False
        self._more_pending: bool = False
        self._scroll_anchor: int = 0

        self._build_ui(nickname)
        self._update_empty()

    # ==================================================================
    # 构建
    # ==================================================================
    def _build_ui(self, nickname: str) -> None:
        """搭建界面骨架。"""
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.top_bar = TopBar(nickname)
        self.top_bar.wall_clicked.connect(lambda: self.set_filter(None))
        self.top_bar.mine_clicked.connect(self.show_mine.emit)
        self.top_bar.switch_user_clicked.connect(self.switch_user.emit)
        root.addWidget(self.top_bar)

        root.addWidget(self._build_offline_bar())
        root.addWidget(self._build_filter_row())

        self._stack = QStackedWidget()
        self._stack.setStyleSheet("QStackedWidget { background: transparent; }")
        self._stack.addWidget(self._build_scroll_area())
        self._empty = EmptyState()
        self._empty.set_text(TEXTS["empty_icon"], TEXTS["empty_title"], TEXTS["empty_hint"])
        self._stack.addWidget(self._empty)
        root.addWidget(self._stack, 1)

        self._fab = FloatingButton(self)
        self._fab.clicked.connect(self.request_publish.emit)

        self._toast = Toast(self)
        self._toast.action_clicked.connect(self._on_toast_action)

    def _build_offline_bar(self) -> QWidget:
        """离线提示条（R-03：明确告知"显示本地缓存"并可手动重试）。"""
        self._offline_bar = QWidget()
        self._offline_bar.setFixedHeight(32)
        self._offline_bar.setStyleSheet(
            f"QWidget {{ background: {theme.WARN_BG};"
            f" border-bottom: 1px solid {theme.WARN_BORDER}; }}"
        )
        layout = QHBoxLayout(self._offline_bar)
        layout.setContentsMargins(24, 0, 24, 0)
        layout.setSpacing(10)
        self._offline_label = make_label(TEXTS["offline"], size=9, color=theme.WARN_INK)
        layout.addWidget(self._offline_label)
        layout.addStretch(1)
        retry = GhostButton(TEXTS["retry"], height=22)
        retry.setFixedWidth(56)
        retry.clicked.connect(self.request_flush.emit)
        layout.addWidget(retry)
        self._offline_bar.setVisible(False)
        return self._offline_bar

    def _build_filter_row(self) -> QWidget:
        """情感 chip 组 + 搜索框（R-11）。"""
        row = QWidget()
        row.setFixedHeight(58)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(24, 8, 24, 8)
        layout.setSpacing(8)

        self._chips: dict[str, Chip] = {}
        all_chip = Chip("", TEXTS["all"], icon="🫧")
        all_chip.set_selected(True)
        all_chip.clicked.connect(self._on_chip)
        layout.addWidget(all_chip)
        self._chips[""] = all_chip
        for emotion in EMOTIONS:
            chip = Chip(emotion, emotion, icon=theme.emotion_icon(emotion))
            chip.clicked.connect(self._on_chip)
            layout.addWidget(chip)
            self._chips[emotion] = chip

        layout.addStretch(1)

        self._search = QLineEdit()
        self._search.setObjectName("searchInput")
        self._search.setStyleSheet(theme.QSS_SEARCH)
        self._search.setPlaceholderText(TEXTS["search_placeholder"])
        self._search.setMaxLength(MAX_SEARCH_LEN)
        self._search.setFixedWidth(240)
        self._search.setFixedHeight(34)
        self._search.textChanged.connect(self._on_search)
        layout.addWidget(self._search)
        return row

    def _build_scroll_area(self) -> QWidget:
        """可滚动的泡泡容器。"""
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            + theme.QSS_SCROLLBAR
        )
        self._scroll.viewport().setStyleSheet("background: transparent;")
        self._container = FlowContainer()
        self._flow = self._container.flow
        self._scroll.setWidget(self._container)
        self._scroll.verticalScrollBar().valueChanged.connect(self._on_scroll)
        return self._scroll

    # ==================================================================
    # 数据应用（规则 A-2：按 id diff）
    # ==================================================================
    def apply_bubbles(
        self,
        bubbles: list[Bubble],
        *,
        total: int = 0,
        has_more: bool = False,
    ) -> None:
        """应用一次拉取结果。

        Args:
            bubbles: 服务端返回的当前页泡泡（已按时间倒序）。
            total: 服务端总数（仅展示用）。
            has_more: 是否还有下一页。
        """
        self._all = list(bubbles)
        self._has_more = bool(has_more)
        self._more_pending = False
        if not self._first_load_done:
            self._first_load_done = True
            self._rebuild_visible()
            return

        visible = self._filtered()
        new_ids = [bubble.id for bubble in visible if bubble.id not in self._views]
        scrolled = self._scroll.verticalScrollBar().value() > 0
        if new_ids and scrolled:
            # R-13：不打断当前视图，只累加提示
            self._new_count += len(new_ids)
            self._toast.show_text(
                TEXTS["new_bubbles"].format(count=self._new_count), TEXTS["view"]
            )
            self._apply_visible(visible, allow_new=False)
            return
        self._new_count = 0
        self._apply_visible(visible, allow_new=True)

    def prepend_bubble(self, bubble: Bubble) -> None:
        """把新发布的泡泡放到首位（发布成功/离线入队后调用）。"""
        self._all = [bubble] + [item for item in self._all if item.id != bubble.id]
        self._rebuild_visible()
        self._scroll.verticalScrollBar().setValue(0)

    def update_bubble(self, bubble: Bubble) -> None:
        """单条泡泡变化（不重建其它 widget）。

        规则：
            * `saved_private` → 立即从墙上移除（PRD：私密保存后从公开墙消失）；
            * `resolved` → **不动**，交给"已解决"流程统一播放爆破动画后再移除；
            * 其它 → 原地 `update_from`（数据没变则什么都不做）。
        """
        if bubble.status == "saved_private":
            self.drop_bubble(bubble.id)
            return
        if bubble.status == "resolved":
            return
        view = self._views.get(bubble.id)
        if view is not None:
            if view.update_from(bubble):
                self._container.relayout()
        self._all = [
            bubble if item.id == bubble.id else item for item in self._all
        ]

    def remove_bubble_animated(self, bubble_id: str) -> None:
        """播放爆破动画后再移除（R-08：≥600ms）。"""
        view = self._views.get(bubble_id)
        if view is None:
            self._remove_from_data(bubble_id)
            return
        view.start_burst()

    def drop_bubble(self, bubble_id: str) -> None:
        """立即移除（不发动画，用于"私密保存后从墙消失"）。"""
        self._detach(bubble_id)
        self._remove_from_data(bubble_id)

    def _remove_from_data(self, bubble_id: str) -> None:
        """从数据缓存里移除并刷新空态。"""
        self._all = [item for item in self._all if item.id != bubble_id]
        self._order = [bid for bid in self._order if bid != bubble_id]
        self._container.relayout()
        self._update_empty()

    def _on_burst_finished(self, bubble_id: str) -> None:
        """爆破结束：此刻才真正从墙上移除。"""
        self._detach(bubble_id)
        self._remove_from_data(bubble_id)

    def _apply_visible(self, visible: list[Bubble], *, allow_new: bool) -> None:
        """核心 diff 逻辑。"""
        scrollbar = self._scroll.verticalScrollBar()
        self._scroll_anchor = scrollbar.value()
        incoming = {bubble.id: bubble for bubble in visible}

        # 1) 删除：不在新集合里且不在爆破中
        for bubble_id in list(self._views.keys()):
            if bubble_id in incoming:
                continue
            view = self._views.get(bubble_id)
            if view is not None and view.is_bursting():
                continue
            self._detach(bubble_id)

        # 2) 新增 / 更新
        for bubble in visible:
            view = self._views.get(bubble.id)
            if view is None:
                if not allow_new:
                    continue
                self._create_view(bubble)
            else:
                view.update_from(bubble)

        # 3) 顺序（只在变化时重排）
        order = [bubble.id for bubble in visible if bubble.id in self._views]
        self._reorder(order)

        # 4) 恢复滚动位置（R-14 ①）
        scrollbar.setValue(min(self._scroll_anchor, scrollbar.maximum()))
        self._update_empty()

    def _create_view(self, bubble: Bubble) -> BubbleView:
        """创建并挂载一个新泡泡（**只在新增时**调用）。"""
        view = BubbleView(bubble, animator=self._animator)
        view.clicked.connect(self.open_detail.emit)
        view.burst_finished.connect(self._on_burst_finished)
        self._views[bubble.id] = view
        self._flow.addWidget(view)
        return view

    def _detach(self, bubble_id: str) -> None:
        """摘除并销毁一个泡泡。"""
        view = self._views.pop(bubble_id, None)
        if view is None:
            return
        view.detach()
        self._flow.removeWidget(view)
        view.setParent(None)
        view.deleteLater()
        self._order = [bid for bid in self._order if bid != bubble_id]

    def _reorder(self, order: list[str]) -> None:
        """按目标顺序重排（顺序未变则不做任何事）。"""
        if order == self._order:
            return
        for _ in range(self._flow.count()):
            self._flow.takeAt(0)
        for bubble_id in order:
            view = self._views.get(bubble_id)
            if view is not None:
                self._flow.addWidget(view)
        self._order = list(order)
        self._container.relayout()

    # ==================================================================
    # 筛选 / 搜索（R-11：本地过滤，200ms 内呈现）
    # ==================================================================
    def _filtered(self) -> list[Bubble]:
        """本地过滤（情感 + 关键字）。"""
        query = self._query.strip().lower()
        result: list[Bubble] = []
        for bubble in self._all:
            if self._emotion and bubble.emotion_tag != self._emotion:
                continue
            if query:
                haystack = f"{bubble.content} {bubble.author_nickname}".lower()
                if query not in haystack:
                    continue
            result.append(bubble)
        return result

    def _rebuild_visible(self) -> None:
        """重建当前可见集合（筛选/搜索/首屏时调用）。"""
        self._new_count = 0
        self._toast.hide_now()
        self._apply_visible(self._filtered(), allow_new=True)

    def _on_chip(self, value: str) -> None:
        """chip 点击。"""
        self.set_filter(value or None)

    def set_filter(self, emotion: Optional[str]) -> None:
        """设置情感筛选（`None` 表示全部）。"""
        self._emotion = emotion or None
        for value, chip in self._chips.items():
            chip.set_selected((value or None) == self._emotion)
        self._rebuild_visible()
        self.filter_changed.emit(self._emotion)

    def _on_search(self, text: str) -> None:
        """搜索框变化。"""
        self._query = text or ""
        self._rebuild_visible()
        self.search_changed.emit(self._query)

    def set_query(self, text: str) -> None:
        """外部设置搜索词（测试用）。"""
        self._search.setText(text)

    def focus_search(self) -> None:
        """聚焦搜索框（Ctrl+F）。"""
        self._search.setFocus(Qt.OtherFocusReason)
        self._search.selectAll()

    def current_filter(self) -> Optional[str]:
        """当前情感筛选。"""
        return self._emotion

    def current_query(self) -> str:
        """当前搜索词。"""
        return self._query

    # ==================================================================
    # 状态
    # ==================================================================
    def set_online(self, online: bool) -> None:
        """在线状态变化（R-03）。"""
        self.top_bar.set_online(online)
        self._offline_bar.setVisible(not online)

    def set_pending(self, count: int) -> None:
        """待同步条数。"""
        self.top_bar.set_pending(count)

    def set_nickname(self, nickname: str) -> None:
        """更新昵称展示。"""
        self.top_bar.set_nickname(nickname)

    def set_active(self, page: str) -> None:
        """顶栏高亮（`wall` / `mine`）。"""
        self.top_bar.set_active(page)

    def show_loading(self) -> None:
        """首屏加载态。"""
        self._stack.setCurrentIndex(1)
        self._empty.set_text("🫧", TEXTS["loading"], "")

    def _update_empty(self) -> None:
        """根据数据量切换空态 / 列表。"""
        if self._views:
            self._stack.setCurrentIndex(0)
            return
        if self._query.strip():
            self._empty.set_text("🔍", TEXTS["search_empty"], "换个关键词试试")
        elif self._emotion:
            self._empty.set_text("🫧", TEXTS["filter_empty"], "切回「全部」看看")
        elif not self._first_load_done:
            self._empty.set_text("🫧", TEXTS["loading"], "")
        else:
            self._empty.set_text(
                TEXTS["empty_icon"], TEXTS["empty_title"], TEXTS["empty_hint"]
            )
        self._stack.setCurrentIndex(1)

    def _on_scroll(self, value: int) -> None:
        """滚到底部时请求下一页（R-12）。"""
        scrollbar = self._scroll.verticalScrollBar()
        if not self._has_more or self._more_pending:
            return
        if scrollbar.maximum() <= 0:
            return
        if value >= scrollbar.maximum() - 120:
            self._more_pending = True
            self.request_more.emit()

    def _on_toast_action(self) -> None:
        """点击"查看"：回到顶部并展示新内容。"""
        self._new_count = 0
        self._rebuild_visible()
        self._scroll.verticalScrollBar().setValue(0)
        self.request_refresh.emit()

    # ==================================================================
    # 尺寸 / 测试辅助
    # ==================================================================
    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """重排并摆放右下角发布按钮与提示。"""
        super().resizeEvent(event)
        self._fab.move(
            self.width() - self._fab.width() - FAB_MARGIN,
            self.height() - self._fab.height() - FAB_MARGIN,
        )
        self._fab.raise_()
        if self._toast.isVisible():
            self._toast.raise_()
        self._container.relayout()

    def bubble_count(self) -> int:
        """当前墙上的泡泡 widget 数（R-14 断言用）。"""
        return len(self._views)

    def view_for(self, bubble_id: str) -> Optional[BubbleView]:
        """取某个泡泡的 widget（测试用）。"""
        return self._views.get(bubble_id)

    def ordered_ids(self) -> list[str]:
        """当前展示顺序。"""
        return list(self._order)

    def toast(self) -> Toast:
        """底部提示控件（测试/截图用）。"""
        return self._toast

    def new_count(self) -> int:
        """未读新泡泡数。"""
        return self._new_count

    def content_rect(self) -> QRect:
        """内容区矩形（截图辅助）。"""
        return self._scroll.geometry()


__all__ = ["WallPage", "TEXTS", "FAB_MARGIN"]
