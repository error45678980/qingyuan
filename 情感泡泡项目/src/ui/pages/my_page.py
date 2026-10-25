"""我的页面（T-10，R-02）。

四组：我发布的 / 🔓 公开保留 / 🔒 私密保存 / ✅ 已解决，每组带数量。
私密组的内容只在本机可见（服务端对非作者一律 403，见架构 §5.2）。
"""

from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QHBoxLayout, QScrollArea, QStackedWidget, QVBoxLayout, QWidget

from src.core.constants import MINE_GROUPS
from src.core.models import Bubble
from src.ui import theme
from src.ui.widgets.animation import BubbleAnimator
from src.ui.widgets.bubble_view import BubbleView
from src.ui.widgets.common import EmptyState, GradientPage, NavButton, make_label
from src.ui.widgets.flow_layout import FlowContainer
from src.ui.widgets.top_bar import TopBar

#: 分组展示文案与图标（顺序与 `MINE_GROUPS` 一致）。
GROUP_META: dict[str, tuple[str, str, str]] = {
    "published": ("我发布的", "🫧", "你还没有发布过泡泡哦～"),
    "saved_public": ("公开保留", "🔓", "还没有公开保留的泡泡"),
    "saved_private": ("私密保存", "🔒", "还没有私密保存的泡泡"),
    "resolved": ("已解决", "✅", "还没有已解决的泡泡"),
}

#: 本页文案。
TEXTS: dict[str, str] = {
    "title": "我的泡泡",
    "hint": "私密保存的泡泡只有你自己能看到",
}


class MyPage(GradientPage):
    """「我的」页面。"""

    open_detail = pyqtSignal(str)
    back_to_wall = pyqtSignal()
    switch_user = pyqtSignal()
    group_changed = pyqtSignal(str)
    request_group = pyqtSignal(str)

    def __init__(
        self,
        animator: Optional[BubbleAnimator] = None,
        *,
        nickname: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        """构造。

        Args:
            animator: 全局泡泡动画器。
            nickname: 当前昵称。
            parent: 父控件。
        """
        super().__init__(parent)
        self._animator = animator if animator is not None else BubbleAnimator(self)
        self._group: str = MINE_GROUPS[0]
        self._views: dict[str, BubbleView] = {}
        self._counts: dict[str, int] = {group: 0 for group in MINE_GROUPS}
        self._build_ui(nickname)
        self._update_empty()

    # ==================================================================
    # 构建
    # ==================================================================
    def _build_ui(self, nickname: str) -> None:
        """搭建界面。"""
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.top_bar = TopBar(nickname)
        self.top_bar.set_active("mine")
        self.top_bar.wall_clicked.connect(self.back_to_wall.emit)
        self.top_bar.mine_clicked.connect(lambda: self.request_group.emit(self._group))
        self.top_bar.switch_user_clicked.connect(self.switch_user.emit)
        root.addWidget(self.top_bar)

        header = QWidget()
        header.setFixedHeight(64)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(24, 12, 24, 4)
        header_layout.setSpacing(10)
        header_layout.addWidget(
            make_label(TEXTS["title"], size=15, color=theme.INK, bold=True)
        )
        header_layout.addWidget(make_label(TEXTS["hint"], size=9, color=theme.INK_FAINT))
        header_layout.addStretch(1)
        root.addWidget(header)

        tabs = QWidget()
        tabs.setFixedHeight(46)
        tabs_layout = QHBoxLayout(tabs)
        tabs_layout.setContentsMargins(24, 4, 24, 6)
        tabs_layout.setSpacing(8)
        self._tabs: dict[str, NavButton] = {}
        for group in MINE_GROUPS:
            label, icon, _ = GROUP_META[group]
            button = NavButton(f"{icon} {label}")
            button.setCheckable(True)
            button.setChecked(group == self._group)
            button.clicked.connect(lambda _checked=False, name=group: self.set_group(name))
            tabs_layout.addWidget(button)
            self._tabs[group] = button
        tabs_layout.addStretch(1)
        root.addWidget(tabs)

        self._stack = QStackedWidget()
        self._stack.setStyleSheet("QStackedWidget { background: transparent; }")
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }" + theme.QSS_SCROLLBAR
        )
        self._scroll.viewport().setStyleSheet("background: transparent;")
        self._container = FlowContainer()
        self._flow = self._container.flow
        self._scroll.setWidget(self._container)
        self._stack.addWidget(self._scroll)

        self._empty = EmptyState()
        self._stack.addWidget(self._empty)
        root.addWidget(self._stack, 1)

    # ==================================================================
    # 数据
    # ==================================================================
    def set_group(self, group: str) -> None:
        """切换分组。"""
        if group not in MINE_GROUPS:
            return
        if group == self._group and self._views:
            return
        self._group = group
        for name, button in self._tabs.items():
            button.setChecked(name == group)
        self._clear_views()
        self._update_empty()
        self.group_changed.emit(group)
        self.request_group.emit(group)

    def current_group(self) -> str:
        """当前分组。"""
        return self._group

    def apply_group(self, group: str, bubbles: list[Bubble]) -> None:
        """应用某个分组的拉取结果。

        Args:
            group: 分组名。
            bubbles: 泡泡列表。
        """
        if group != self._group:
            return
        self._apply(bubbles)

    def apply_counts(self, counts: dict[str, int]) -> None:
        """更新分组计数（R-02：每组带数量）。"""
        for group in MINE_GROUPS:
            self._counts[group] = int(counts.get(group, 0) or 0)
            label, icon, _ = GROUP_META[group]
            self._tabs[group].setText(f"{icon} {label} ({self._counts[group]})")

    def _apply(self, bubbles: list[Bubble]) -> None:
        """diff 复用（与墙同一套策略，避免切换分组时闪烁）。"""
        incoming = {bubble.id: bubble for bubble in bubbles}
        for bubble_id in list(self._views.keys()):
            if bubble_id not in incoming:
                self._detach(bubble_id)
        for bubble in bubbles:
            view = self._views.get(bubble.id)
            if view is None:
                self._create_view(bubble)
            else:
                view.update_from(bubble)
        order = [bubble.id for bubble in bubbles if bubble.id in self._views]
        current = [view.bubble_id for view in self._iter_views()]
        if current != order:
            for _ in range(self._flow.count()):
                self._flow.takeAt(0)
            for bubble_id in order:
                view = self._views.get(bubble_id)
                if view is not None:
                    self._flow.addWidget(view)
        self._container.relayout()
        self._update_empty()

    def _iter_views(self) -> list[BubbleView]:
        """按当前布局顺序返回泡泡控件。"""
        result: list[BubbleView] = []
        for index in range(self._flow.count()):
            item = self._flow.itemAt(index)
            widget = item.widget() if item is not None else None
            if isinstance(widget, BubbleView):
                result.append(widget)
        return result

    def _create_view(self, bubble: Bubble) -> None:
        """创建一个泡泡控件。"""
        view = BubbleView(bubble, animator=self._animator)
        view.clicked.connect(self.open_detail.emit)
        self._views[bubble.id] = view
        self._flow.addWidget(view)

    def _detach(self, bubble_id: str) -> None:
        """摘除一个泡泡控件。"""
        view = self._views.pop(bubble_id, None)
        if view is None:
            return
        view.detach()
        self._flow.removeWidget(view)
        view.setParent(None)
        view.deleteLater()

    def _clear_views(self) -> None:
        """清空当前分组的所有控件。"""
        for bubble_id in list(self._views.keys()):
            self._detach(bubble_id)
        self._container.relayout()

    def _update_empty(self) -> None:
        """切换空态。"""
        if self._views:
            self._stack.setCurrentIndex(0)
            return
        _, icon, hint = GROUP_META[self._group]
        self._empty.set_text(icon, hint, "")
        self._stack.setCurrentIndex(1)

    # ==================================================================
    # 状态
    # ==================================================================
    def set_nickname(self, nickname: str) -> None:
        """更新昵称。"""
        self.top_bar.set_nickname(nickname)

    def set_online(self, online: bool) -> None:
        """在线状态。"""
        self.top_bar.set_online(online)

    def set_pending(self, count: int) -> None:
        """待同步条数。"""
        self.top_bar.set_pending(count)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """宽度变化后重排。"""
        super().resizeEvent(event)
        self._container.relayout()

    def bubble_count(self) -> int:
        """当前展示的泡泡数（测试用）。"""
        return len(self._views)

    def ordered_ids(self) -> list[str]:
        """当前展示顺序（测试用）。"""
        return [view.bubble_id for view in self._iter_views()]


__all__ = ["MyPage", "GROUP_META", "TEXTS"]
