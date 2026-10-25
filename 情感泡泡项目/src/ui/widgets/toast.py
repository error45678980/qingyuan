"""底部浮出提示（T-10，R-13）。

用于"↑ 有 N 个新泡泡"这类**不打断当前视图**的提示：2.5s 自动消失，可点击。
"""

from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QRectF, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QPainter, QPainterPath, QPen
from PyQt5.QtWidgets import QGraphicsOpacityEffect, QHBoxLayout, QLabel, QWidget

from src.ui import theme
from src.ui.widgets.common import make_label

#: 自动消失时间。
AUTO_HIDE_MS: int = 2500


class Toast(QWidget):
    """浮在页面底部的轻提示。"""

    action_clicked = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        """构造。

        Args:
            parent: 宿主页面（Toast 是它的子控件，位置由 `show_text` 计算）。
        """
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.setCursor(Qt.PointingHandCursor)
        self.setVisible(False)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 0, 20, 0)
        layout.setSpacing(12)
        self._text = make_label("", size=10, color=theme.INK)
        self._action = make_label("", size=10, color=theme.PRIMARY, bold=True)
        layout.addWidget(self._text)
        layout.addWidget(self._action)

        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setOpacity(0.0)
        self.setGraphicsEffect(self._effect)
        self._fade = QPropertyAnimation(self._effect, b"opacity", self)
        self._fade.setDuration(180)
        self._fade.setEasingCurve(QEasingCurve.OutCubic)
        self._fade.finished.connect(self._on_fade_finished)
        self._fading_out = False

        # 自动消失定时器：只在本控件内，与泡泡动画无关（不违反规则 A-1 的本意）
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._fade_out)

    # ------------------------------------------------------------------
    # 展示
    # ------------------------------------------------------------------
    def show_text(self, text: str, action_text: str = "") -> None:
        """展示提示。

        Args:
            text: 主文案。
            action_text: 右侧可点击动作文案（空则不显示）。
        """
        self._text.setText(text)
        self._action.setText(action_text)
        self._action.setVisible(bool(action_text))
        self.adjustSize()
        self.resize(self.sizeHint().width() + 40, 40)
        self._reposition()
        self.show()
        self.raise_()
        self._effect.setOpacity(0.0)
        self._fading_out = False
        self._fade.stop()
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.start()
        self._hide_timer.start(AUTO_HIDE_MS)

    def _reposition(self) -> None:
        """定位到父控件底部居中（避开右下角发布按钮）。"""
        parent = self.parentWidget()
        if parent is None:
            return
        x = (parent.width() - self.width()) // 2
        y = parent.height() - self.height() - 92
        self.move(QPoint(max(0, x), max(0, y)))

    def _fade_out(self) -> None:
        """淡出（结束后在 `_on_fade_finished` 里隐藏）。"""
        self._fading_out = True
        self._fade.stop()
        self._fade.setStartValue(self._effect.opacity())
        self._fade.setEndValue(0.0)
        self._fade.start()

    def _on_fade_finished(self) -> None:
        """淡出结束：仅在淡出方向上隐藏。"""
        if self._fading_out:
            self.hide()
            self._fading_out = False

    def hide_now(self) -> None:
        """立即隐藏（页面切换时调用）。"""
        self._fading_out = False
        self._hide_timer.stop()
        self.hide()

    # ------------------------------------------------------------------
    # 交互与绘制
    # ------------------------------------------------------------------
    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """点击整条提示都视为点击动作。"""
        if event.button() == Qt.LeftButton:
            self.action_clicked.emit()
            self.hide_now()
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """绘制圆角白底 + 紫色描边。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, rect.height() / 2.0, rect.height() / 2.0)
        painter.fillPath(path, theme.qcolor("#FFFFFF", 246))
        painter.setPen(QPen(theme.qcolor(theme.PRIMARY_BORDER), 1))
        painter.drawPath(path)
        painter.end()


__all__ = ["Toast", "AUTO_HIDE_MS"]
