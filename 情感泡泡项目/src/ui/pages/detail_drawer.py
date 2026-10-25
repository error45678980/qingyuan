"""详情抽屉内容（T-11，PRD §4.3）。

按角色渲染（权限只读服务端 DTO 的 `is_owner/can_reply/can_chat`；离线时用
`core.permissions.evaluate()` 兜底，仅用于展示）：
    * 作者：顶部"这是你的泡泡" + 「已解决」「申请保留」两个按钮，**不显示**回复框；
    * 访客 · 单次未回复：回复输入框 + 发送；
    * 访客 · 单次已回复：只读条"✅ 你已回复过这个泡泡"；
    * 访客 · 对话模式：「💬 发起对话」按钮 + 首条消息输入框。
"""

from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import QRectF, Qt, pyqtSignal
from PyQt5.QtGui import QPainter, QPainterPath, QPen
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from src.core.constants import MAX_REPLY_LEN
from src.core.models import Bubble, ChatMessage, Reply
from src.core.permissions import Identity, Permissions, evaluate
from src.ui import theme
from src.ui.widgets.common import (
    GhostButton,
    PrimaryButton,
    RoundIconButton,
    make_label,
)

#: 本抽屉文案。
TEXTS: dict[str, str] = {
    "owner": "这是你的泡泡",
    "private": "🔒 私密保存 · 仅自己可见",
    "saved": "📦 已公开保留",
    "offline": "离线：内容可能不是最新",
    "resolved": "这个泡泡已结束",
    "missing": "这个泡泡已经不在了 🌫️",
    "reply_placeholder": "写一句温暖的话…",
    "reply_send": "发送",
    "replied": "✅ 你已回复过这个泡泡",
    "no_reply_box": "这是你自己的泡泡，不能回复自己哦",
    "resolve": "🎈 已解决",
    "save": "💾 申请保留",
    "confirm_resolve": "确定要炸掉这个泡泡吗？",
    "confirm_yes": "确定炸掉",
    "confirm_no": "再想想",
    "replies": "回复 ({count})",
    "chats": "对话",
    "no_replies": "还没有人回复，泡泡还在安静地飘着",
    "start_chat": "💬 发起对话",
    "chat_with": "💬 与 {peer} 的对话",
    "save_title": "怎么保留这个泡泡？",
    "save_public": "🔓 公开保留（其他人也能看到）",
    "save_private": "🔒 私密保存（仅自己可见）",
    "save_ok": "确定",
    "cancel": "取消",
    "reply_too_long": "回复不能超过 200 字",
}


class _Panel(QWidget):
    """自绘圆角面板（白底 + 描边），给回复行/对话行复用。"""

    def __init__(self, parent: Optional[QWidget] = None, *, radius: int = 14) -> None:
        """构造。"""
        super().__init__(parent)
        self._radius = radius
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """绘制面板底。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, self._radius, self._radius)
        painter.fillPath(path, theme.qcolor("#FBFAFE"))
        painter.setPen(QPen(theme.qcolor("#EFECF8"), 1))
        painter.drawPath(path)
        painter.end()


class ReplyRow(_Panel):
    """一条回复。"""

    def __init__(self, reply: Reply, *, parent: Optional[QWidget] = None) -> None:
        """构造。"""
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(4)
        head = QHBoxLayout()
        head.setSpacing(6)
        head.addWidget(
            make_label(reply.replier_nickname or "匿名", size=9, color=theme.PRIMARY, bold=True)
        )
        head.addStretch(1)
        stamp = reply.short_time
        if reply.sync_state != "synced":
            stamp = f"⏳ {stamp}"
        head.addWidget(make_label(stamp, size=8, color=theme.INK_FAINT))
        layout.addLayout(head)
        body = make_label(reply.content, size=10, color=theme.INK, wrap=True)
        body.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        layout.addWidget(body)


class ChatPeerRow(_Panel):
    """对话入口行（作者视角：每个对话方一行）。"""

    open_clicked = pyqtSignal(str)

    def __init__(self, peer: str, count: int, *, parent: Optional[QWidget] = None) -> None:
        """构造。"""
        super().__init__(parent)
        self._peer = peer
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 8, 10, 8)
        layout.setSpacing(8)
        layout.addWidget(make_label(f"💬 与 {peer} 的对话", size=9, color=theme.INK))
        if count > 0:
            layout.addWidget(make_label(f"{count} 条", size=8, color=theme.INK_FAINT))
        layout.addStretch(1)
        button = GhostButton("进入", height=26)
        button.setFixedWidth(60)
        button.clicked.connect(lambda: self.open_clicked.emit(self._peer))
        layout.addWidget(button)


class DetailDrawer(QWidget):
    """详情抽屉内容体。"""

    resolve_requested = pyqtSignal(str)
    save_requested = pyqtSignal(str, bool)
    reply_requested = pyqtSignal(str, str)
    open_chat = pyqtSignal(str, str)
    close_requested = pyqtSignal()

    def __init__(
        self,
        me_nickname: str = "",
        me_client_id: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        """构造。

        Args:
            me_nickname: 当前用户昵称（展示 + 离线兜底判定）。
            me_client_id: 当前设备 ID（离线兜底判定作者身份）。
            parent: 父控件。
        """
        super().__init__(parent)
        self._me = me_nickname
        self._me_client_id = me_client_id
        self._bubble: Optional[Bubble] = None
        self._replies: list[Reply] = []
        self._chats: list[ChatMessage] = []
        self._perms: Optional[Permissions] = None
        self._offline = False
        self._save_choice: Optional[bool] = None
        self._build_ui()

    # ==================================================================
    # 构建
    # ==================================================================
    def _build_ui(self) -> None:
        """搭建界面骨架。"""
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

        meta = QHBoxLayout()
        meta.setSpacing(8)
        self._author = make_label("", size=9, color=theme.INK_SOFT)
        self._time = make_label("", size=9, color=theme.INK_FAINT)
        meta.addWidget(self._author)
        meta.addWidget(self._time)
        meta.addStretch(1)
        root.addLayout(meta)

        self._banner = make_label("", size=9, color=theme.PRIMARY)
        self._banner.setWordWrap(True)
        root.addWidget(self._banner)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }" + theme.QSS_SCROLLBAR
        )
        self._scroll.viewport().setStyleSheet("background: transparent;")
        self._body = QWidget()
        self._body.setStyleSheet("QWidget { background: transparent; }")
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(0, 4, 6, 4)
        self._body_layout.setSpacing(10)
        self._body_layout.setAlignment(Qt.AlignTop)
        self._scroll.setWidget(self._body)
        root.addWidget(self._scroll, 1)

        self._action_area = QWidget()
        self._action_area.setStyleSheet("QWidget { background: transparent; }")
        self._action_layout = QVBoxLayout(self._action_area)
        self._action_layout.setContentsMargins(0, 4, 0, 0)
        self._action_layout.setSpacing(8)
        root.addWidget(self._action_area)

        self._build_save_overlay()
        self._save_panel.setVisible(False)
        root.addWidget(self._save_panel)

    def _build_save_overlay(self) -> None:
        """申请保留的两个互斥选项（未选则确定按钮禁用）。"""
        self._save_panel = _Panel(self, radius=16)
        layout = QVBoxLayout(self._save_panel)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)
        layout.addWidget(make_label(TEXTS["save_title"], size=10, color=theme.INK, bold=True))
        self._save_public = GhostButton(TEXTS["save_public"], height=34)
        self._save_public.clicked.connect(lambda: self._choose_save(True))
        layout.addWidget(self._save_public)
        self._save_private = GhostButton(TEXTS["save_private"], height=34)
        self._save_private.clicked.connect(lambda: self._choose_save(False))
        layout.addWidget(self._save_private)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        cancel = GhostButton(TEXTS["cancel"], height=32)
        cancel.clicked.connect(self._hide_save_panel)
        buttons.addWidget(cancel)
        self._save_ok = PrimaryButton(TEXTS["save_ok"], height=32)
        self._save_ok.setEnabled(False)
        self._save_ok.clicked.connect(self._confirm_save)
        buttons.addWidget(self._save_ok)
        layout.addLayout(buttons)

    # ==================================================================
    # 渲染
    # ==================================================================
    def render(
        self,
        bubble: Bubble,
        replies: list[Reply],
        *,
        chats: Optional[list[ChatMessage]] = None,
        my_reply_count: int = 0,
        has_chatted: bool = False,
    ) -> None:
        """渲染详情。

        Args:
            bubble: 泡泡。
            replies: 全部回复。
            chats: 我能看到的对话（作者为全部）。
            my_reply_count: 我已回复次数（离线兜底判定用）。
            has_chatted: 我是否参与过对话（离线兜底判定用）。
        """
        self._bubble = bubble
        self._replies = list(replies)
        self._chats = list(chats or [])
        self._perms = self._resolve_permissions(bubble, my_reply_count, has_chatted)

        main_hex, deep_hex, _ = theme.emotion_colors(bubble.emotion_tag)
        self._title.setText(f"{theme.emotion_icon(bubble.emotion_tag)} {bubble.emotion_tag}")
        self._title.setStyleSheet(
            f"QLabel {{ color: {deep_hex}; background: transparent; }}"
        )
        self._author.setText(f"{bubble.author_nickname or '匿名'} · {bubble.display_time}")
        self._time.setText(
            theme.REPLY_MODE_LABELS.get(bubble.reply_mode, bubble.reply_mode)
        )
        self._banner.setText(self._banner_text(bubble))
        self._banner.setVisible(bool(self._banner.text()))
        self._banner.setStyleSheet(
            f"QLabel {{ color: {main_hex if bubble.status == 'active' else theme.INK_SOFT};"
            " background: transparent; }}"
        )
        self._rebuild_body(bubble)
        self._rebuild_actions(bubble)
        self._hide_save_panel()

    def _resolve_permissions(
        self, bubble: Bubble, my_reply_count: int, has_chatted: bool
    ) -> Permissions:
        """取权限：服务端下发的标志优先，离线时用同一套 `evaluate()` 兜底。"""
        if getattr(bubble, "perms_known", False):
            return Permissions(
                is_owner=bubble.is_owner,
                is_replier=my_reply_count > 0,
                is_participant=bubble.is_owner or my_reply_count > 0 or has_chatted,
                can_read=True,
                can_reply=bubble.can_reply,
                can_chat=bubble.can_chat,
                can_manage=bubble.is_owner,
                can_read_all_chats=bubble.is_owner,
            )
        identity = Identity(client_id=self._me_client_id, nickname=self._me)
        return evaluate(
            bubble,
            identity,
            my_reply_count=my_reply_count,
            has_chatted=has_chatted,
        )

    def _banner_text(self, bubble: Bubble) -> str:
        """顶部状态条文案。"""
        if self._offline:
            return TEXTS["offline"]
        if self._perms is not None and self._perms.is_owner:
            if bubble.status == "saved_private":
                return TEXTS["private"]
            if bubble.status == "saved_public":
                return TEXTS["saved"]
            return TEXTS["owner"]
        if bubble.status == "resolved":
            return TEXTS["resolved"]
        return ""

    def _clear_body(self) -> None:
        """清空正文区（重建时调用）。"""
        while self._body_layout.count():
            item = self._body_layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def _rebuild_body(self, bubble: Bubble) -> None:
        """重建正文 + 回复列表 + 对话入口。"""
        self._clear_body()

        content_panel = _Panel(self._body, radius=16)
        content_layout = QVBoxLayout(content_panel)
        content_layout.setContentsMargins(16, 14, 16, 14)
        content_layout.setSpacing(6)
        body = make_label(bubble.content, size=11, color=theme.INK, wrap=True)
        body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        content_layout.addWidget(body)
        self._body_layout.addWidget(content_panel)

        self._body_layout.addWidget(
            make_label(
                TEXTS["replies"].format(count=len(self._replies)),
                size=10,
                color=theme.INK_SOFT,
                bold=True,
            )
        )
        if self._replies:
            for reply in self._replies:
                self._body_layout.addWidget(ReplyRow(reply, parent=self._body))
        else:
            self._body_layout.addWidget(
                make_label(TEXTS["no_replies"], size=9, color=theme.INK_FAINT, wrap=True)
            )

        peers = self._chat_peers(bubble)
        if peers:
            self._body_layout.addWidget(
                make_label(TEXTS["chats"], size=10, color=theme.INK_SOFT, bold=True)
            )
            for peer, count in peers:
                row = ChatPeerRow(peer, count, parent=self._body)
                row.open_clicked.connect(self._on_open_chat)
                self._body_layout.addWidget(row)
        self._body_layout.addStretch(1)

    def _chat_peers(self, bubble: Bubble) -> list[tuple[str, int]]:
        """计算对话入口列表（R-07：对方必须是真实对话方）。"""
        if self._perms is None:
            return []
        if not self._perms.can_chat:
            return []
        if not self._perms.is_owner:
            return [(bubble.author_nickname, len(self._chats))]
        counts: dict[str, int] = {}
        for message in self._chats:
            peer = (
                message.receiver_nickname
                if message.sender_nickname == self._me
                else message.sender_nickname
            )
            if peer and peer != self._me:
                counts[peer] = counts.get(peer, 0) + 1
        for reply in self._replies:
            if reply.replier_nickname and reply.replier_nickname != self._me:
                counts.setdefault(reply.replier_nickname, 0)
        return sorted(counts.items(), key=lambda item: (-item[1], item[0]))

    # ==================================================================
    # 底部操作区
    # ==================================================================
    def _clear_actions(self) -> None:
        """清空底部操作区。"""
        while self._action_layout.count():
            item = self._action_layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
            layout = item.layout() if item is not None else None
            if layout is not None:
                layout.deleteLater()

    def _rebuild_actions(self, bubble: Bubble) -> None:
        """按角色重建底部操作区。"""
        self._clear_actions()
        if self._perms is None:
            return
        if self._perms.is_owner:
            row = QHBoxLayout()
            row.setSpacing(10)
            resolve = GhostButton(TEXTS["resolve"], height=40)
            resolve.clicked.connect(lambda: self._ask_resolve(bubble))
            row.addWidget(resolve)
            save = PrimaryButton(TEXTS["save"], height=40)
            save.clicked.connect(self._show_save_panel)
            row.addWidget(save)
            self._action_layout.addLayout(row)
            return

        if not self._perms.can_reply and not self._perms.can_chat:
            self._action_layout.addWidget(
                make_label(TEXTS["replied"], size=9, color=theme.INK_FAINT)
            )
            return

        if bubble.reply_mode == "dialog" and self._perms.can_chat:
            chat_button = PrimaryButton(TEXTS["start_chat"], height=40)
            chat_button.clicked.connect(
                lambda: self.open_chat.emit(bubble.id, bubble.author_nickname)
            )
            self._action_layout.addWidget(chat_button)

        if self._perms.can_reply:
            self._action_layout.addLayout(self._build_reply_box(bubble))
        elif bubble.reply_mode == "single":
            self._action_layout.addWidget(
                make_label(TEXTS["replied"], size=9, color=theme.INK_FAINT)
            )

    def _build_reply_box(self, bubble: Bubble) -> QHBoxLayout:
        """回复输入行（Enter 发送）。"""
        row = QHBoxLayout()
        row.setSpacing(8)
        edit = QLineEdit()
        edit.setObjectName("lineInput")
        edit.setStyleSheet(theme.QSS_LINE_EDIT)
        edit.setPlaceholderText(TEXTS["reply_placeholder"])
        edit.setMaxLength(MAX_REPLY_LEN)
        edit.setFixedHeight(38)
        edit.returnPressed.connect(lambda: self._submit_reply(bubble, edit))
        row.addWidget(edit)
        send = PrimaryButton(TEXTS["reply_send"], height=38)
        send.setFixedWidth(76)
        send.clicked.connect(lambda: self._submit_reply(bubble, edit))
        row.addWidget(send)
        self._reply_edit = edit
        return row

    def _submit_reply(self, bubble: Bubble, edit: QLineEdit) -> None:
        """提交回复（不清空，等成功信号再清）。"""
        content = edit.text().strip()
        if not content:
            return
        if len(content) > MAX_REPLY_LEN:
            self.flash(TEXTS["reply_too_long"])
            return
        self.reply_requested.emit(bubble.id, content)

    def _ask_resolve(self, bubble: Bubble) -> None:
        """二次确认后再发"已解决"。"""
        self._clear_actions()
        self._action_layout.addWidget(
            make_label(TEXTS["confirm_resolve"], size=9, color=theme.DANGER)
        )
        row = QHBoxLayout()
        row.setSpacing(10)
        yes = PrimaryButton(TEXTS["confirm_yes"], height=38)
        yes.clicked.connect(lambda: self.resolve_requested.emit(bubble.id))
        no = GhostButton(TEXTS["confirm_no"], height=38)
        no.clicked.connect(lambda: self._rebuild_actions(bubble))
        row.addWidget(yes)
        row.addWidget(no)
        self._action_layout.addLayout(row)

    # ==================================================================
    # 保留面板
    # ==================================================================
    def _show_save_panel(self) -> None:
        """展开保留选项。"""
        self._save_choice = None
        self._save_ok.setEnabled(False)
        self._save_public.setEnabled(True)
        self._save_private.setEnabled(True)
        self._save_panel.setVisible(True)

    def _hide_save_panel(self) -> None:
        """收起保留选项。"""
        self._save_choice = None
        self._save_ok.setEnabled(False)
        self._save_panel.setVisible(False)

    def _choose_save(self, is_public: bool) -> None:
        """选择公开/私密（互斥）。"""
        self._save_choice = bool(is_public)
        self._save_ok.setEnabled(True)

    def _confirm_save(self) -> None:
        """确认保留。"""
        if self._save_choice is None or self._bubble is None:
            return
        self.save_requested.emit(self._bubble.id, self._save_choice)
        self._hide_save_panel()

    # ==================================================================
    # 对外状态
    # ==================================================================
    def set_offline(self, offline: bool) -> None:
        """离线状态（顶部黄条）。"""
        self._offline = bool(offline)
        if self._bubble is not None:
            self._banner.setText(self._banner_text(self._bubble))
            self._banner.setVisible(bool(self._banner.text()))

    def flash(self, message: str) -> None:
        """临时提示（写在顶部条）。"""
        self._banner.setText(message)
        self._banner.setVisible(bool(message))

    def clear_reply_input(self) -> None:
        """回复成功后清空输入框。"""
        edit = getattr(self, "_reply_edit", None)
        if edit is not None:
            edit.clear()

    def show_missing(self) -> None:
        """泡泡已不存在。"""
        self._clear_body()
        self._clear_actions()
        self._title.setText("")
        self._author.setText("")
        self._time.setText("")
        self._banner.setText(TEXTS["missing"])
        self._banner.setVisible(True)
        self._body_layout.addWidget(
            make_label(TEXTS["missing"], size=11, color=theme.INK_FAINT, wrap=True)
        )

    def bubble(self) -> Optional[Bubble]:
        """当前泡泡（测试用）。"""
        return self._bubble

    def permissions(self) -> Optional[Permissions]:
        """当前权限（测试用）。"""
        return self._perms

    def reply_edit(self) -> Optional[QLineEdit]:
        """回复输入框（测试用）。"""
        return getattr(self, "_reply_edit", None)

    def save_panel_visible(self) -> bool:
        """保留面板是否展开（测试用）。"""
        return self._save_panel.isVisible()


__all__ = ["DetailDrawer", "ReplyRow", "ChatPeerRow", "TEXTS"]
