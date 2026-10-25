"""发布页（T-11，视觉规格 §2，PRD §4.2）。

形态：整页 + 背后压暗 + 居中白卡（圆角 28、大阴影）。
字段顺序严格照参考图：内容输入框 → 情感 chip 组 → 回复模式两选一 → 主按钮。

校验规则（PRD §4.2）：
    * 内容为空点发布 → 输入框红边 + "写点什么再发布吧"（不弹错误框）；
    * 超过 500 字 → 计数变红 + 主按钮禁用；
    * 未选情感 → chip 区提示；
    * 提交中 → 按钮变"发布中…"并禁用，界面不冻结。
"""

from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import QRectF, QSize, Qt, pyqtSignal
from PyQt5.QtGui import QPainter, QPainterPath, QPen
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.core.constants import DEFAULT_REPLY_MODE, EMOTIONS, MAX_BUBBLE_LEN
from src.ui import theme
from src.ui.widgets.common import (
    Card,
    Chip,
    GradientPage,
    PrimaryButton,
    RoundIconButton,
    make_label,
)

#: 本页文案。
TEXTS: dict[str, str] = {
    "title": "💭 发布心情",
    "placeholder": "写下你现在的心情…分享你的感受，会有人看见的 ✨",
    "emotion_label": "情感标签",
    "mode_label": "回复方式",
    "submit": "🎈 发布泡泡",
    "submitting": "发布中…",
    "empty": "写点什么再发布吧",
    "too_long": "已超出 {count} 字",
    "no_emotion": "请选择一个情感标签",
    "counter": "{used} / {limit}",
    "hint": "Enter 换行 · 最多 500 字 · 离线时先存本地，联网后自动发出",
    "single_title": "📝 单次回复",
    "single_desc": "每个人只能回复一次",
    "dialog_title": "💬 短时对话",
    "dialog_desc": "可以持续聊一会儿",
}

#: 卡片宽度（参考图比例）。
CARD_WIDTH: int = 620


class _Mask(QWidget):
    """压暗遮罩（背后泡泡墙模糊感的替代：统一半透明压暗）。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        """构造。"""
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """铺一层深色半透明。"""
        painter = QPainter(self)
        painter.fillRect(self.rect(), theme.qcolor(theme.INK, 42))
        painter.end()


class ModeOption(QWidget):
    """回复模式选项卡片（两选一）。"""

    clicked = pyqtSignal(str)

    def __init__(
        self,
        value: str,
        title: str,
        description: str,
        *,
        parent: Optional[QWidget] = None,
    ) -> None:
        """构造。"""
        super().__init__(parent)
        self._value = value
        self._title = title
        self._description = description
        self._selected = False
        self._hover = False
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFixedHeight(66)

    def value(self) -> str:
        """选项值。"""
        return self._value

    def is_selected(self) -> bool:
        """是否选中。"""
        return self._selected

    def set_selected(self, selected: bool) -> None:
        """设置选中态。"""
        if self._selected == bool(selected):
            return
        self._selected = bool(selected)
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt 命名
        """首选尺寸。"""
        return QSize(240, 66)

    def enterEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """悬停。"""
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """离开。"""
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """点击选中。"""
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self._value)
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """绘制选项卡片。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(rect, 16, 16)
        if self._selected:
            painter.fillPath(path, theme.qcolor(theme.PRIMARY_SOFT))
            painter.setPen(QPen(theme.qcolor(theme.PRIMARY), 2))
        else:
            painter.fillPath(path, theme.qcolor("#FFFFFF" if not self._hover else "#FBFAFF"))
            painter.setPen(QPen(theme.qcolor(theme.PRIMARY_BORDER if self._hover else "#E9E5F5"), 1))
        painter.drawPath(path)

        painter.setFont(theme.font(11, bold=True))
        painter.setPen(QPen(theme.qcolor(theme.PRIMARY if self._selected else theme.INK)))
        painter.drawText(
            QRectF(rect.x() + 16, rect.y() + 12, rect.width() - 32, 22),
            Qt.AlignLeft | Qt.AlignVCenter,
            self._title,
        )
        painter.setFont(theme.font(9))
        painter.setPen(QPen(theme.qcolor(theme.INK_FAINT)))
        painter.drawText(
            QRectF(rect.x() + 16, rect.y() + 34, rect.width() - 32, 20),
            Qt.AlignLeft | Qt.AlignVCenter,
            self._description,
        )
        painter.end()


class PublishPage(GradientPage):
    """发布页。"""

    submitted = pyqtSignal(dict)
    cancelled = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        """构造。"""
        super().__init__(parent)
        self._emotion: str = ""
        self._mode: str = DEFAULT_REPLY_MODE
        self._submitting: bool = False
        self._build_ui()
        self._sync_button()

    # ==================================================================
    # 构建
    # ==================================================================
    def _build_ui(self) -> None:
        """搭建界面。"""
        self._mask = _Mask(self)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setAlignment(Qt.AlignCenter)

        card = Card(self, radius=28)
        card.setFixedWidth(CARD_WIDTH)
        outer.addWidget(card, alignment=Qt.AlignCenter)
        self._card = card

        inner = QVBoxLayout(card)
        inner.setContentsMargins(34, 26, 34, 28)
        inner.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(8)
        header.addWidget(make_label(TEXTS["title"], size=14, color=theme.PRIMARY, bold=True))
        header.addStretch(1)
        close = RoundIconButton("✕", diameter=28)
        close.clicked.connect(self.cancelled.emit)
        header.addWidget(close)
        inner.addLayout(header)

        self.editor = QTextEdit()
        self.editor.setObjectName("composerEdit")
        self.editor.setStyleSheet(theme.QSS_TEXT_EDIT)
        self.editor.setPlaceholderText(TEXTS["placeholder"])
        self.editor.setFont(theme.font(11))
        self.editor.setFixedHeight(150)
        self.editor.textChanged.connect(self._on_text_changed)
        inner.addWidget(self.editor)

        meta = QHBoxLayout()
        meta.setSpacing(8)
        meta.addWidget(make_label(TEXTS["hint"], size=8, color=theme.INK_FAINT))
        meta.addStretch(1)
        self._counter = make_label("", size=9, color=theme.INK_FAINT)
        meta.addWidget(self._counter)
        inner.addLayout(meta)

        inner.addSpacing(6)
        inner.addWidget(make_label(TEXTS["emotion_label"], size=10, color=theme.INK_SOFT, bold=True))
        self._chips: dict[str, Chip] = {}
        for chunk in (EMOTIONS[:4], EMOTIONS[4:]):
            row = QWidget()
            row.setFixedHeight(34)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(8)
            for emotion in chunk:
                chip = Chip(emotion, emotion, icon=theme.emotion_icon(emotion))
                chip.clicked.connect(self._on_emotion)
                self._chips[emotion] = chip
                row_layout.addWidget(chip)
            row_layout.addStretch(1)
            inner.addWidget(row)

        inner.addSpacing(4)
        inner.addWidget(make_label(TEXTS["mode_label"], size=10, color=theme.INK_SOFT, bold=True))
        mode_row = QHBoxLayout()
        mode_row.setSpacing(12)
        self._modes: dict[str, ModeOption] = {}
        for value, title_key, desc_key in (
            ("single", "single_title", "single_desc"),
            ("dialog", "dialog_title", "dialog_desc"),
        ):
            option = ModeOption(value, TEXTS[title_key], TEXTS[desc_key])
            option.clicked.connect(self._on_mode)
            self._modes[value] = option
            mode_row.addWidget(option)
        inner.addLayout(mode_row)

        self._error = make_label("", size=9, color=theme.DANGER)
        inner.addWidget(self._error)

        self._submit = PrimaryButton(TEXTS["submit"], height=46)
        self._submit.clicked.connect(self._on_submit)
        inner.addWidget(self._submit)

        self._on_text_changed()
        self._on_mode(self._mode)

    # ==================================================================
    # 交互
    # ==================================================================
    def _on_text_changed(self) -> None:
        """字数计数与按钮状态。"""
        used = len(self.editor.toPlainText())
        over = used > MAX_BUBBLE_LEN
        self._counter.setText(TEXTS["counter"].format(used=used, limit=MAX_BUBBLE_LEN))
        self._counter.setStyleSheet(
            f"QLabel {{ color: {theme.DANGER if over else theme.INK_FAINT};"
            " background: transparent; }}"
        )
        if over:
            self._error.setText(TEXTS["too_long"].format(count=used - MAX_BUBBLE_LEN))
        elif self._error.text() == TEXTS["too_long"].format(count=used - MAX_BUBBLE_LEN):
            self._error.setText("")
        self._sync_button()

    def _sync_button(self) -> None:
        """按当前状态启用/禁用主按钮。"""
        over = len(self.editor.toPlainText()) > MAX_BUBBLE_LEN
        self._submit.setEnabled(not self._submitting and not over)
        self._submit.setText(TEXTS["submitting"] if self._submitting else TEXTS["submit"])

    def _on_emotion(self, emotion: str) -> None:
        """选择情感标签。"""
        self._emotion = emotion
        for value, chip in self._chips.items():
            chip.set_selected(value == emotion)
        if self._error.text() == TEXTS["no_emotion"]:
            self._error.setText("")

    def _on_mode(self, mode: str) -> None:
        """选择回复模式。"""
        self._mode = mode if mode in ("single", "dialog") else DEFAULT_REPLY_MODE
        for value, option in self._modes.items():
            option.set_selected(value == self._mode)

    def _on_submit(self) -> None:
        """校验并提交。"""
        content = self.editor.toPlainText().strip()
        if not content:
            self._error.setText(TEXTS["empty"])
            self.editor.setStyleSheet(
                theme.QSS_TEXT_EDIT
                + "QTextEdit#composerEdit { border: 1px solid #E5484D; }"
            )
            self.editor.setFocus(Qt.OtherFocusReason)
            return
        if len(content) > MAX_BUBBLE_LEN:
            self._error.setText(TEXTS["too_long"].format(count=len(content) - MAX_BUBBLE_LEN))
            return
        if not self._emotion:
            self._error.setText(TEXTS["no_emotion"])
            return
        self.editor.setStyleSheet(theme.QSS_TEXT_EDIT)
        self._error.setText("")
        self.submitted.emit(
            {
                "content": content,
                "emotion_tag": self._emotion,
                "reply_mode": self._mode,
            }
        )

    # ==================================================================
    # 对外接口
    # ==================================================================
    def reset(self) -> None:
        """清空表单（发布成功后调用）。"""
        self._emotion = ""
        self._mode = DEFAULT_REPLY_MODE
        self._submitting = False
        self.editor.clear()
        self.editor.setStyleSheet(theme.QSS_TEXT_EDIT)
        self._error.setText("")
        for chip in self._chips.values():
            chip.set_selected(False)
        self._on_mode(self._mode)
        self._sync_button()

    def set_submitting(self, submitting: bool) -> None:
        """设置提交中状态（不冻结界面）。"""
        self._submitting = bool(submitting)
        self._sync_button()

    def show_error(self, message: str) -> None:
        """展示错误（不清空输入，R-05）。"""
        self._error.setText(message)
        self._submitting = False
        self._sync_button()

    def draft(self) -> dict[str, str]:
        """当前草稿（返回墙/再次进入时保留）。"""
        return {
            "content": self.editor.toPlainText(),
            "emotion_tag": self._emotion,
            "reply_mode": self._mode,
        }

    def apply_draft(self, draft: dict[str, str]) -> None:
        """恢复草稿。"""
        if not draft:
            return
        self.editor.setPlainText(draft.get("content", ""))
        emotion = draft.get("emotion_tag", "")
        if emotion in self._chips:
            self._on_emotion(emotion)
        self._on_mode(draft.get("reply_mode", DEFAULT_REPLY_MODE))

    def focus_editor(self) -> None:
        """聚焦输入框。"""
        self.editor.setFocus(Qt.OtherFocusReason)

    # ==================================================================
    # 布局
    # ==================================================================
    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """遮罩铺满、卡片居中。"""
        super().resizeEvent(event)
        self._mask.setGeometry(self.rect())
        self._mask.lower()
        self._card.raise_()

    def error_text(self) -> str:
        """当前错误文案（测试用）。"""
        return self._error.text()

    def selected_emotion(self) -> str:
        """当前选中的情感（测试用）。"""
        return self._emotion

    def selected_mode(self) -> str:
        """当前选中的模式（测试用）。"""
        return self._mode


__all__ = ["PublishPage", "ModeOption", "TEXTS", "CARD_WIDTH"]
