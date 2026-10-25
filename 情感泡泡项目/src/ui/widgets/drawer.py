"""右侧抽屉容器（T-11，视觉规格 §3 / §4）。

规格：宽 420、圆角只在左侧、滑入 220ms、背后遮罩压暗。
遮罩与面板都在本控件内绘制（一个控件负责遮罩 + 面板 + 内容定位），
这样不需要额外窗口，也不会踩"半透明无边框窗口丢阴影"的坑（视觉规格 §6-4）。
"""

from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import QEasingCurve, QPropertyAnimation, QRect, QRectF, Qt, pyqtProperty, pyqtSignal
from PyQt5.QtGui import QLinearGradient, QPainter, QPainterPath, QPen
from PyQt5.QtWidgets import QWidget

from src.ui import theme

#: 抽屉宽度（视觉规格 §3：宽约 420px）。
DRAWER_WIDTH: int = 420
#: 左侧圆角半径。
DRAWER_RADIUS: int = 24
#: 滑入时长。
SLIDE_MS: int = 220


class Drawer(QWidget):
    """覆盖在页面之上的右侧抽屉。"""

    closed = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None, *, width: int = DRAWER_WIDTH) -> None:
        """构造（不显示）。

        Args:
            parent: 宿主（通常是主窗口的中央控件，抽屉铺满它）。
            width: 面板宽度。
        """
        super().__init__(parent)
        self._width = max(280, int(width))
        self._slide = 0.0
        self._content: Optional[QWidget] = None
        self._open = False

        self.setVisible(False)
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setFocusPolicy(Qt.StrongFocus)

        self._anim = QPropertyAnimation(self, b"slide", self)
        self._anim.setDuration(SLIDE_MS)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.finished.connect(self._on_anim_finished)

    # ------------------------------------------------------------------
    # 滑动属性
    # ------------------------------------------------------------------
    def _get_slide(self) -> float:
        """滑入进度 0~1。"""
        return self._slide

    def _set_slide(self, value: float) -> None:
        """设置进度并重排。"""
        self._slide = max(0.0, min(1.0, float(value)))
        self._apply_geometry()
        self.update()

    slide = pyqtProperty(float, _get_slide, _set_slide)

    # ------------------------------------------------------------------
    # 开关
    # ------------------------------------------------------------------
    def open(self, content: QWidget) -> None:
        """打开抽屉并放入内容控件。

        Args:
            content: 抽屉内容（`DetailDrawer` / `ChatDrawer` 的内容体）。
        """
        parent = self.parentWidget()
        if parent is not None:
            self.setGeometry(parent.rect())
        if self._content is not None and self._content is not content:
            self._content.setParent(None)
            self._content.deleteLater()
        self._content = content
        content.setParent(self)
        content.show()
        self._open = True
        self.setVisible(True)
        self.raise_()
        self._apply_geometry()
        self._anim.stop()
        self._anim.setStartValue(self._slide)
        self._anim.setEndValue(1.0)
        self._anim.start()
        self.setFocus(Qt.OtherFocusReason)

    def close_drawer(self) -> None:
        """关闭抽屉（动画结束后发 `closed`）。"""
        if not self._open:
            return
        self._open = False
        self._anim.stop()
        self._anim.setStartValue(self._slide)
        self._anim.setEndValue(0.0)
        self._anim.start()

    def is_open(self) -> bool:
        """是否处于打开状态。"""
        return self._open

    def content(self) -> Optional[QWidget]:
        """当前内容控件。"""
        return self._content

    def _on_anim_finished(self) -> None:
        """动画结束：关到底则隐藏并通知。"""
        if self._open:
            return
        self.setVisible(False)
        if self._content is not None:
            self._content.setParent(None)
            self._content.deleteLater()
            self._content = None
        self.closed.emit()

    # ------------------------------------------------------------------
    # 几何
    # ------------------------------------------------------------------
    def panel_rect(self) -> QRect:
        """当前面板矩形（按滑入进度偏移）。"""
        offset = int(self._width * (1.0 - self._slide))
        return QRect(self.width() - self._width + offset, 0, self._width, self.height())

    def _apply_geometry(self) -> None:
        """把内容控件放进面板内（留出 1px 描边空间）。"""
        if self._content is None:
            return
        rect = self.panel_rect().adjusted(1, 0, 0, 0)
        self._content.setGeometry(rect)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """宿主尺寸变化时跟随。"""
        super().resizeEvent(event)
        self._apply_geometry()

    # ------------------------------------------------------------------
    # 交互
    # ------------------------------------------------------------------
    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """点击遮罩区域关闭抽屉。"""
        if event.button() == Qt.LeftButton and event.pos().x() < self.panel_rect().left():
            self.close_drawer()
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """Esc 关闭。"""
        if event.key() == Qt.Key_Escape:
            self.close_drawer()
            event.accept()
            return
        super().keyPressEvent(event)

    # ------------------------------------------------------------------
    # 绘制
    # ------------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """绘制遮罩 + 面板（左侧圆角 + 左侧投影）。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        panel = QRectF(self.panel_rect())
        mask_alpha = int(theme.MASK_RGBA[3] * self._slide)

        painter.fillRect(self.rect(), theme.qcolor(theme.INK, mask_alpha))

        shadow = QLinearGradient(panel.left() - 18, 0, panel.left(), 0)
        shadow.setColorAt(0.0, theme.qcolor("#1F1233", 0))
        shadow.setColorAt(1.0, theme.qcolor("#1F1233", int(38 * self._slide)))
        painter.fillRect(
            QRectF(panel.left() - 18, 0, 18, panel.height()),
            shadow,
        )

        path = QPainterPath()
        radius = float(DRAWER_RADIUS)
        path.moveTo(panel.right(), panel.top())
        path.lineTo(panel.left() + radius, panel.top())
        path.quadTo(panel.left(), panel.top(), panel.left(), panel.top() + radius)
        path.lineTo(panel.left(), panel.bottom() - radius)
        path.quadTo(panel.left(), panel.bottom(), panel.left() + radius, panel.bottom())
        path.lineTo(panel.right(), panel.bottom())
        path.closeSubpath()
        painter.fillPath(path, theme.qcolor(theme.CARD_BG))
        painter.setPen(QPen(theme.qcolor("#E9E5F5"), 1))
        painter.drawPath(path)
        painter.end()


__all__ = ["Drawer", "DRAWER_WIDTH", "DRAWER_RADIUS", "SLIDE_MS"]
