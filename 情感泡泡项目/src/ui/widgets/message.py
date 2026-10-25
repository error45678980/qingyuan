"""对话消息气泡（T-08 / T-12）。

一条消息 = 一行 `QWidget`：我方右对齐紫色渐变气泡、对方左对齐白色气泡。
气泡背景用 `QPainter` 自绘（`border-radius` 对 `QWidget` 无效，视觉规格 §6-2）。
"""

from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import QRectF, QSize, Qt
from PyQt5.QtGui import QBrush, QColor, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPen
from PyQt5.QtWidgets import QSizePolicy, QWidget

from src.core.models import ChatMessage
from src.ui import theme
from src.ui.widgets.common import wrap_text_lines

#: 气泡最大内容宽度（抽屉宽 420，留出两侧内边距）。
BODY_MAX_WIDTH: int = 268
#: 气泡内边距。
PADDING_X: int = 14
PADDING_Y: int = 9


class MessageBubble(QWidget):
    """一条对话消息。"""

    def __init__(
        self,
        message: ChatMessage,
        *,
        mine: bool,
        peer_name: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        """构造。

        Args:
            message: 消息数据。
            mine: 是否我方发送（决定左右与配色）。
            peer_name: 对方昵称（仅对方消息展示在气泡上方）。
            parent: 父控件。
        """
        super().__init__(parent)
        self._message = message
        self._mine = bool(mine)
        self._peer = peer_name
        self.setFont(theme.font(10))
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._lines: list[str] = []
        self._body_font = theme.font(10)
        self._time_font = theme.font(8)
        self._recompute()

    # ------------------------------------------------------------------
    # 数据
    # ------------------------------------------------------------------
    @property
    def message(self) -> ChatMessage:
        """底层消息。"""
        return self._message

    def update_message(self, message: ChatMessage) -> bool:
        """更新消息内容，返回是否真的有变化（供 diff 复用）。"""
        if (
            message.content == self._message.content
            and message.sync_state == self._message.sync_state
        ):
            return False
        self._message = message
        self._recompute()
        self.update()
        return True

    def _recompute(self) -> None:
        """重算折行与高度。"""
        self._lines = wrap_text_lines(
            self._message.content, self._body_font, BODY_MAX_WIDTH - PADDING_X * 2, 30
        ) or [""]
        metrics = QFontMetrics(self._body_font)
        body_height = len(self._lines) * metrics.height() + PADDING_Y * 2
        extra = 18 if not self._mine else 0
        self.setFixedHeight(body_height + extra + 18)

    # ------------------------------------------------------------------
    # 尺寸
    # ------------------------------------------------------------------
    def sizeHint(self) -> QSize:  # noqa: N802 - Qt 命名
        """首选尺寸（宽度交给布局，高度按内容）。"""
        return QSize(BODY_MAX_WIDTH + PADDING_X * 2, self.height())

    # ------------------------------------------------------------------
    # 绘制
    # ------------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """绘制气泡。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setFont(self._body_font)
        metrics = QFontMetrics(self._body_font)

        content_width = max(
            metrics.horizontalAdvance(line) for line in self._lines
        ) if self._lines else 0
        body_width = min(BODY_MAX_WIDTH, content_width + PADDING_X * 2)
        body_width = max(body_width, 72)
        top = 18 if not self._mine else 0
        body_rect = QRectF(
            (self.width() - body_width - 4) if self._mine else 4,
            top,
            body_width,
            len(self._lines) * metrics.height() + PADDING_Y * 2,
        )

        if not self._mine and self._peer:
            painter.setFont(theme.font(8))
            painter.setPen(QPen(theme.qcolor(theme.INK_FAINT)))
            painter.drawText(
                QRectF(8, 0, self.width() - 16, 16),
                Qt.AlignLeft | Qt.AlignVCenter,
                self._peer,
            )
            painter.setFont(self._body_font)

        path = QPainterPath()
        path.addRoundedRect(body_rect, 14, 14)
        if self._mine:
            gradient = QLinearGradient(body_rect.topLeft(), body_rect.bottomRight())
            gradient.setColorAt(0.0, theme.qcolor(theme.PRIMARY_HOVER))
            gradient.setColorAt(1.0, theme.qcolor(theme.PRIMARY))
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(gradient))
            painter.drawPath(path)
            text_color = theme.qcolor("#FFFFFF")
            time_color = theme.qcolor("#FFFFFF", 190)
        else:
            painter.setPen(QPen(theme.qcolor("#E9E5F5"), 1))
            painter.setBrush(theme.qcolor("#FFFFFF"))
            painter.drawPath(path)
            text_color = theme.qcolor(theme.INK)
            time_color = theme.qcolor(theme.INK_FAINT)

        painter.setFont(self._body_font)
        painter.setPen(QPen(text_color))
        line_height = metrics.height()
        for index, line in enumerate(self._lines):
            painter.drawText(
                QRectF(
                    body_rect.x() + PADDING_X,
                    body_rect.y() + PADDING_Y + index * line_height,
                    body_rect.width() - PADDING_X * 2,
                    line_height,
                ),
                Qt.AlignLeft | Qt.AlignVCenter,
                line,
            )

        painter.setFont(self._time_font)
        painter.setPen(QPen(time_color))
        stamp = self._message.short_time
        if self._message.sync_state != "synced":
            stamp = f"⏳ {stamp}"
        painter.drawText(
            QRectF(body_rect.x(), body_rect.bottom() + 1, body_rect.width(), 16),
            (Qt.AlignRight if self._mine else Qt.AlignLeft) | Qt.AlignVCenter,
            stamp,
        )
        painter.end()


__all__ = ["MessageBubble", "BODY_MAX_WIDTH"]
