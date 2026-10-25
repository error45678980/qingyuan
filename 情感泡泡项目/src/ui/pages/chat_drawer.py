"""对话抽屉内容（T-12，PRD §4.4，R-06 / R-07）。

**R-07 的关键**：`peer` 是构造必填参数，由调用方（详情抽屉 / 泡泡墙 / 我的）
按"我是作者 → 对方是回复者；我不是作者 → 对方是作者"算好后传入。
本类内部**绝不**自行推导 `receiver`（v1 的 `receiver = author if nickname != author else bubble[1]`
就是错在这里）。
"""

from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QKeyEvent
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.core.constants import MAX_CHAT_LEN
from src.core.models import Bubble, ChatMessage
from src.ui import theme
from src.ui.widgets.common import PrimaryButton, RoundIconButton, make_label
from src.ui.widgets.message import MessageBubble

#: 本抽屉文案。
TEXTS: dict[str, str] = {
    "title": "💬 与 {peer} 的对话",
    "summary": "📌 关于：{content}",
    "empty": "你们还没有聊过，说点什么吧",
    "no_reply_yet": "对方还没有回复",
    "offline": "当前离线，消息将在联网后发送",
    "closed": "这个泡泡已结束，无法继续发送",
    "denied": "你无权查看这个对话",
    "back": "返回",
    "placeholder": "说点什么…（Enter 发送）",
    "placeholder_tip": "Enter 发送 / Shift+Enter 换行",
    "send": "发送",
}


class ChatInput(QTextEdit):
    """对话输入框：Enter 发送、Shift+Enter 换行。"""

    submitted = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        """构造。"""
        super().__init__(parent)
        self.setObjectName("composerEdit")
        self.setStyleSheet(theme.QSS_TEXT_EDIT)
        self.setPlaceholderText(TEXTS["placeholder"])
        self.setToolTip(TEXTS["placeholder_tip"])
        self.setFont(theme.font(10))
        self.setFixedHeight(58)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt 命名
        """Enter 发送、Shift+Enter 换行。"""
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not (
            event.modifiers() & Qt.ShiftModifier
        ):
            self.submitted.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class ChatDrawer(QWidget):
    """对话抽屉内容体。"""

    send_requested = pyqtSignal(str, str, str)
    close_requested = pyqtSignal()

    def __init__(
        self,
        me_nickname: str,
        bubble: Bubble,
        peer: str,
        parent: Optional[QWidget] = None,
    ) -> None:
        """构造。

        Args:
            me_nickname: 我的昵称（决定左右归属）。
            bubble: 关联泡泡。
            peer: **对方昵称（必填）**，由调用方计算，本类不推导。
            parent: 父控件。
        """
        super().__init__(parent)
        if not peer:
            raise ValueError("ChatDrawer 需要明确的对方昵称（R-07）")
        self._me = me_nickname
        self._bubble = bubble
        self._peer = peer
        self._offline = False
        self._closed = False
        self._rows: dict[str, MessageBubble] = {}
        self._build_ui()
        self.render(bubble, peer, [])

    # ==================================================================
    # 构建
    # ==================================================================
    def _build_ui(self) -> None:
        """搭建界面。"""
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(8)

        header = QHBoxLayout()
        header.setSpacing(8)
        back = RoundIconButton("←", diameter=28)
        back.clicked.connect(self.close_requested.emit)
        header.addWidget(back)
        self._title = make_label("", size=12, color=theme.INK, bold=True)
        header.addWidget(self._title)
        header.addStretch(1)
        close = RoundIconButton("✕", diameter=28)
        close.clicked.connect(self.close_requested.emit)
        header.addWidget(close)
        root.addLayout(header)

        self._summary = make_label("", size=9, color=theme.INK_FAINT, wrap=True)
        root.addWidget(self._summary)

        self._notice = make_label("", size=9, color=theme.WARN_INK, wrap=True)
        self._notice.setStyleSheet(
            f"QLabel {{ color: {theme.WARN_INK}; background: {theme.WARN_BG};"
            " border-radius: 8px; padding: 4px 8px; }}"
        )
        self._notice.setVisible(False)
        root.addWidget(self._notice)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }" + theme.QSS_SCROLLBAR
        )
        self._scroll.viewport().setStyleSheet("background: transparent;")
        self._list = QWidget()
        self._list.setStyleSheet("QWidget { background: transparent; }")
        self._list_layout = QVBoxLayout(self._list)
        self._list_layout.setContentsMargins(0, 4, 6, 4)
        self._list_layout.setSpacing(10)
        self._list_layout.setAlignment(Qt.AlignTop)
        self._scroll.setWidget(self._list)
        root.addWidget(self._scroll, 1)

        self._empty = make_label(TEXTS["empty"], size=9, color=theme.INK_FAINT, wrap=True)
        self._empty.setAlignment(Qt.AlignCenter)
        root.addWidget(self._empty)

        self._no_reply = make_label(TEXTS["no_reply_yet"], size=8, color=theme.INK_FAINT)
        self._no_reply.setAlignment(Qt.AlignCenter)
        self._no_reply.setVisible(False)
        root.addWidget(self._no_reply)

        composer = QHBoxLayout()
        composer.setSpacing(8)
        self._input = ChatInput()
        self._input.submitted.connect(self._on_send)
        composer.addWidget(self._input)
        self._send = PrimaryButton(TEXTS["send"], height=40)
        self._send.setFixedWidth(76)
        self._send.clicked.connect(self._on_send)
        composer.addWidget(self._send)
        root.addLayout(composer)

    # ==================================================================
    # 渲染
    # ==================================================================
    def render(self, bubble: Bubble, peer: str, messages: list[ChatMessage]) -> None:
        """整体渲染（首次进入或刷新）。

        Args:
            bubble: 关联泡泡。
            peer: 对方昵称（同样由调用方给定）。
            messages: 消息列表（时间升序）。
        """
        self._bubble = bubble
        self._peer = peer
        self._title.setText(TEXTS["title"].format(peer=peer))
        preview = bubble.content_preview
        self._summary.setText(TEXTS["summary"].format(content=preview))
        self._clear_rows()
        for message in messages:
            self._append_row(message)
        self._refresh_state()

    def append_message(self, message: ChatMessage) -> None:
        """追加一条消息（按 `op_id` 去重）。"""
        key = message.op_id or f"{message.created_at}-{message.content}"
        if key in self._rows:
            row = self._rows[key]
            if row.update_message(message):
                self._scroll_to_bottom()
            self._refresh_state()
            return
        self._append_row(message)
        self._refresh_state()

    def _append_row(self, message: ChatMessage) -> None:
        """创建并挂载一行消息。"""
        mine = message.sender_nickname == self._me
        row = MessageBubble(message, mine=mine, peer_name=self._peer)
        key = message.op_id or f"{message.created_at}-{message.content}"
        self._rows[key] = row
        self._list_layout.addWidget(row)
        self._scroll_to_bottom()

    def _clear_rows(self) -> None:
        """清空消息行。"""
        self._rows.clear()
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def _refresh_state(self) -> None:
        """刷新空态、未回复提示、输入可用性。"""
        has_rows = bool(self._rows)
        self._empty.setVisible(not has_rows)
        last_mine = False
        if has_rows:
            last = list(self._rows.values())[-1]
            last_mine = last.message.sender_nickname == self._me
        self._no_reply.setVisible(has_rows and last_mine and not self._closed)
        enabled = not self._closed
        self._input.setEnabled(enabled)
        self._send.setEnabled(enabled)
        if self._closed:
            self._notice.setText(TEXTS["closed"])
            self._notice.setVisible(True)
        elif self._offline:
            self._notice.setText(TEXTS["offline"])
            self._notice.setVisible(True)
        else:
            self._notice.setVisible(False)

    def _scroll_to_bottom(self) -> None:
        """滚动到底部。"""
        bar = self._scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    # ==================================================================
    # 发送
    # ==================================================================
    def _on_send(self) -> None:
        """发送消息（receiver 固定为本抽屉的 peer）。"""
        if self._closed:
            return
        content = self._input.toPlainText().strip()
        if not content:
            return
        if len(content) > MAX_CHAT_LEN:
            content = content[:MAX_CHAT_LEN]
        self.send_requested.emit(self._bubble.id, self._peer, content)

    def clear_input(self) -> None:
        """清空输入框（发送成功后调用）。"""
        self._input.clear()

    # ==================================================================
    # 状态
    # ==================================================================
    def set_offline(self, offline: bool) -> None:
        """离线状态（黄条提示）。"""
        self._offline = bool(offline)
        self._refresh_state()

    def set_closed_bubble(self, closed: bool) -> None:
        """泡泡已结束：禁用输入。"""
        self._closed = bool(closed)
        self._refresh_state()

    def show_denied(self) -> None:
        """非参与者提示（R-06：不泄漏任何消息）。"""
        self._clear_rows()
        self._empty.setText(TEXTS["denied"])
        self._empty.setVisible(True)
        self._no_reply.setVisible(False)
        self._closed = True
        self._notice.setText(TEXTS["denied"])
        self._notice.setVisible(True)
        self._input.setEnabled(False)
        self._send.setEnabled(False)

    def peer(self) -> str:
        """当前对方昵称（测试用）。"""
        return self._peer

    def message_count(self) -> int:
        """当前渲染的消息数（测试用）。"""
        return len(self._rows)

    def input_widget(self) -> ChatInput:
        """输入控件（测试用）。"""
        return self._input


__all__ = ["ChatDrawer", "ChatInput", "TEXTS"]
