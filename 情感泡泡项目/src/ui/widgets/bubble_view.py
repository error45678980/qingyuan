"""圆形泡泡控件（T-09，视觉规格 §1 / §6，R-08）。

要点：
    * 泡泡是**画出来的正圆**：`QPainter.drawEllipse` + `QRadialGradient` + 外发光，
      **不是** QSS `border-radius` 的圆角矩形；
    * 直径按内容长度分档 96 / 120 / 144 / 168px；
    * 浮动由 `BubbleAnimator` 的单一全局定时器驱动（本文件**不建 QTimer**）；
    * 悬停放大 1.06（只改绘制半径，不改 geometry，避免布局抖动）；
    * "已解决"播放 700ms 爆破动画，播完才发 `burst_finished`，由墙决定何时移除。
"""

from __future__ import annotations

import math
import random
from typing import Optional

from PyQt5.QtCore import (
    QEasingCurve,
    QPointF,
    QRectF,
    QSize,
    Qt,
    QPropertyAnimation,
    pyqtProperty,
    pyqtSignal,
)
from PyQt5.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QPainter,
    QPen,
    QRadialGradient,
)
from PyQt5.QtWidgets import QWidget

from src.core.models import Bubble
from src.ui import theme
from src.ui.widgets.common import wrap_text_lines

#: 光晕外扩边距（绘制在控件内，故控件尺寸 = 直径 + 2 * 边距）。
GLOW_MARGIN: int = 12
#: 爆破动画时长（R-08 要求 ≥ 600ms）。
BURST_DURATION_MS: int = 700
#: 悬浮振幅（视觉规格：±4px）。
FLOAT_AMPLITUDE: float = 4.0
#: 内接正方形边长 / 直径（1/√2 ≈ 0.707，取 0.72：四角本就是空白，文字居中不触边）。
INSCRIBED_RATIO: float = 0.72
#: emoji 与正文之间的间距（像素）。
EMOJI_GAP: int = 2
#: 底部时间带高度（像素）。
TIME_BAND: int = 13
#: 正文最多行数。
MAX_TEXT_LINES: int = 3
#: 模式徽标尺寸（宽、高）。
MODE_PILL_W: int = 22
MODE_PILL_H: int = 14


def diameter_for(content: str) -> int:
    """按内容长度返回泡泡直径（视觉规格 §8.2）。

    Args:
        content: 正文。

    Returns:
        112 / 136 / 160 / 184。
    """
    length = len(" ".join(str(content).split()))
    if length <= 20:
        return 112
    if length <= 50:
        return 136
    if length <= 100:
        return 160
    return 184


class BubbleView(QWidget):
    """单个圆形泡泡。"""

    clicked = pyqtSignal(str)
    burst_finished = pyqtSignal(str)

    def __init__(
        self,
        bubble: Bubble,
        *,
        animator: Optional[object] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        """构造。

        Args:
            bubble: 泡泡数据。
            animator: 全局动画器（`BubbleAnimator`），可后置调用 `set_animator`。
            parent: 父控件。
        """
        super().__init__(parent)
        self._bubble = bubble
        self._animator = None
        self._diameter = diameter_for(bubble.content)
        self._dy = 0.0
        self._period_ms = random.randint(4000, 7000)
        self._phase_ms = random.randint(0, self._period_ms)
        self._hovered = False
        self._hover_scale = 1.0
        self._press_scale = 1.0
        self._burst = 0.0
        self._bursting = False
        self._lines: list[str] = []
        self._text_font = theme.font(11)
        self._time_font = theme.font(8)
        self._emoji_size = 22

        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)
        self.setToolTip(bubble.content)

        self._hover_anim = QPropertyAnimation(self, b"hoverScale", self)
        self._hover_anim.setDuration(150)
        self._hover_anim.setEasingCurve(QEasingCurve.OutCubic)

        self._press_anim = QPropertyAnimation(self, b"pressScale", self)
        self._press_anim.setDuration(110)
        self._press_anim.setEasingCurve(QEasingCurve.OutCubic)

        self._burst_anim = QPropertyAnimation(self, b"burstProgress", self)
        self._burst_anim.setDuration(BURST_DURATION_MS)
        self._burst_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._burst_anim.finished.connect(self._on_burst_finished)

        self._apply_size()
        if animator is not None:
            self.set_animator(animator)

    # ------------------------------------------------------------------
    # Qt 属性（供 QPropertyAnimation 驱动）
    # ------------------------------------------------------------------
    def _get_hover_scale(self) -> float:
        """悬停缩放系数。"""
        return self._hover_scale

    def _set_hover_scale(self, value: float) -> None:
        """设置悬停缩放并重绘。"""
        self._hover_scale = float(value)
        self.update()

    hoverScale = pyqtProperty(float, _get_hover_scale, _set_hover_scale)

    def _get_press_scale(self) -> float:
        """按下缩放系数。"""
        return self._press_scale

    def _set_press_scale(self, value: float) -> None:
        """设置按下缩放并重绘。"""
        self._press_scale = float(value)
        self.update()

    pressScale = pyqtProperty(float, _get_press_scale, _set_press_scale)

    def _get_burst(self) -> float:
        """爆破进度 0~1。"""
        return self._burst

    def _set_burst(self, value: float) -> None:
        """设置爆破进度并重绘。"""
        self._burst = max(0.0, min(1.0, float(value)))
        self.update()

    burstProgress = pyqtProperty(float, _get_burst, _set_burst)

    # ------------------------------------------------------------------
    # 数据
    # ------------------------------------------------------------------
    @property
    def bubble(self) -> Bubble:
        """底层数据。"""
        return self._bubble

    @property
    def bubble_id(self) -> str:
        """泡泡 id。"""
        return self._bubble.id

    @property
    def diameter(self) -> int:
        """当前直径（像素）。"""
        return self._diameter

    def update_from(self, bubble: Bubble) -> bool:
        """用新数据刷新（R-14：数据没变就不动，返回是否真的变化）。

        Args:
            bubble: 新数据。

        Returns:
            是否发生变化。
        """
        if (
            bubble.diff_key == self._bubble.diff_key
            and bubble.sync_state == self._bubble.sync_state
        ):
            return False
        old_diameter = self._diameter
        self._bubble = bubble
        self._apply_size()
        if old_diameter == self._diameter:
            self.update()
        return True

    def set_phase(self, phase: float) -> None:
        """设置浮动相位（0~1，测试与错落感调节用）。"""
        self._phase_ms = int(max(0.0, min(1.0, float(phase))) * self._period_ms)

    # ------------------------------------------------------------------
    # 动画注册
    # ------------------------------------------------------------------
    def set_animator(self, animator: object) -> None:
        """绑定全局动画器并注册。"""
        if self._animator is animator:
            return
        self._animator = animator
        tick = getattr(animator, "tick", None)
        if tick is not None:
            tick.connect(self._on_tick)
        register = getattr(animator, "register", None)
        if callable(register):
            register(self)

    def detach(self) -> None:
        """从动画器注销（widget 即将销毁时调用）。"""
        if self._animator is None:
            return
        unregister = getattr(self._animator, "unregister", None)
        if callable(unregister):
            unregister(self)
        self._animator = None

    def _on_tick(self, elapsed_ms: int) -> None:
        """全局定时器 tick：算浮动位移并重绘。"""
        if self._bursting:
            return
        angle = 2.0 * math.pi * ((elapsed_ms + self._phase_ms) / float(self._period_ms))
        dy = FLOAT_AMPLITUDE * math.sin(angle)
        if abs(dy - self._dy) < 0.35:
            return
        self._dy = dy
        self.update()

    # ------------------------------------------------------------------
    # 尺寸与文本
    # ------------------------------------------------------------------
    def _apply_size(self) -> None:
        """按内容重算直径、控件尺寸与折行文本。"""
        self._diameter = diameter_for(self._bubble.content)
        side = self._diameter + GLOW_MARGIN * 2
        if self.width() != side or self.height() != side:
            self.setFixedSize(side, side)
        self._emoji_size = max(16, int(self._diameter * 0.17))
        self._text_font = theme.font(self._text_size())
        metrics = QFontMetrics(self._text_font)
        text_width, max_lines = self._text_box(metrics.height())
        self._lines = wrap_text_lines(
            self._bubble.content, self._text_font, text_width, max_lines
        )

    def _text_box(self, line_height: int) -> tuple[int, int]:
        """按内接正方形推算正文可用宽高（视觉规格 §6-1）。

        正圆内接正方形边长 = 直径 × 0.707，再内缩约 4% 留呼吸感；
        上段给 emoji、下段给时间，中间才是正文，故正文行数由剩余高度决定。

        Args:
            line_height: 当前字号的行高（像素）。

        Returns:
            `(正文宽度, 最大行数)`。
        """
        side = self._diameter * INSCRIBED_RATIO
        text_width = max(40, int(side))
        emoji_h = int(self._diameter * 0.17) + EMOJI_GAP
        available = side - emoji_h - TIME_BAND
        max_lines = max(1, int(available // max(1, line_height)))
        return text_width, min(max_lines, MAX_TEXT_LINES)

    def _text_size(self) -> int:
        """按直径选择文字磅值。"""
        if self._diameter <= 112:
            return 10
        if self._diameter <= 160:
            return 11
        return 12

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt 命名
        """首选尺寸 = 直径 + 光晕边距。"""
        side = self._diameter + GLOW_MARGIN * 2
        return QSize(side, side)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt 命名
        """最小尺寸。"""
        return self.sizeHint()

    # ------------------------------------------------------------------
    # 交互
    # ------------------------------------------------------------------
    def enterEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """悬停放大 + 光晕增强。"""
        self._hovered = True
        self._animate_hover(1.06)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """离开恢复。"""
        self._hovered = False
        self._animate_hover(1.0)
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """按下回弹。"""
        if event.button() == Qt.LeftButton and not self._bursting:
            self._animate_press(0.96)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """释放回弹并派发点击。"""
        if event.button() != Qt.LeftButton:
            super().mouseReleaseEvent(event)
            return
        self._animate_press(1.0)
        if not self._bursting and self.rect().contains(event.pos()):
            self.clicked.emit(self._bubble.id)
        super().mouseReleaseEvent(event)

    def _animate_hover(self, target: float) -> None:
        """执行悬停缩放动画。"""
        self._hover_anim.stop()
        self._hover_anim.setStartValue(self._hover_scale)
        self._hover_anim.setEndValue(float(target))
        self._hover_anim.start()

    def _animate_press(self, target: float) -> None:
        """执行按下缩放动画。"""
        self._press_anim.stop()
        self._press_anim.setStartValue(self._press_scale)
        self._press_anim.setEndValue(float(target))
        self._press_anim.start()

    # ------------------------------------------------------------------
    # 爆破
    # ------------------------------------------------------------------
    def is_bursting(self) -> bool:
        """是否正在爆破（动画已启动即视为爆破中，不等第一帧）。"""
        return self._bursting

    def start_burst(self) -> None:
        """开始爆破（≥600ms 后才发 `burst_finished`）。"""
        if self._bursting:
            return
        self._bursting = True
        self._hover_anim.stop()
        self._hover_scale = 1.0
        self._burst_anim.stop()
        self._burst_anim.setStartValue(0.0)
        self._burst_anim.setEndValue(1.0)
        self._burst_anim.start()

    def _on_burst_finished(self) -> None:
        """爆破结束回调。"""
        self.burst_finished.emit(self._bubble.id)

    # ------------------------------------------------------------------
    # 绘制
    # ------------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """绘制光晕 + 圆形本体 + 内容 + 爆破粒子。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)

        main_hex, deep_hex, light_hex = theme.emotion_colors(self._bubble.emotion_tag)
        main = theme.qcolor(main_hex)
        deep = theme.qcolor(deep_hex)
        light = theme.qcolor(light_hex)

        scale = self._hover_scale * self._press_scale * (1.0 + 0.35 * self._burst)
        center = QPointF(self.width() / 2.0, self.height() / 2.0 + self._dy)
        radius = self._diameter / 2.0 * scale
        fade = max(0.0, 1.0 - self._burst)

        # 1) 外发光
        glow_radius = radius * 1.24
        glow = QRadialGradient(center, glow_radius)
        glow_alpha = int((80 if self._hovered else 58) * fade)
        glow.setColorAt(0.0, theme.qcolor(main_hex, glow_alpha))
        glow.setColorAt(0.62, theme.qcolor(main_hex, int(glow_alpha * 0.5)))
        glow.setColorAt(1.0, theme.qcolor(main_hex, 0))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(glow))
        painter.drawEllipse(center, glow_radius, glow_radius)

        # 2) 本体（径向渐变：左上高光 → 主色 → 边缘加深）
        highlight = theme.mix(light, QColor("#FFFFFF"), 0.45)
        body = QRadialGradient(
            QPointF(center.x() - radius * 0.30, center.y() - radius * 0.34),
            radius * 1.30,
        )
        body.setColorAt(0.0, theme.qcolor(highlight.name(), int(252 * fade)))
        body.setColorAt(0.55, theme.qcolor(main_hex, int(238 * fade)))
        body.setColorAt(1.0, theme.qcolor(deep_hex, int(232 * fade)))
        painter.setBrush(QBrush(body))
        painter.setPen(QPen(QColor(deep_hex).lighter(140), 1))
        painter.drawEllipse(center, radius, radius)

        # 3) 内容
        painter.setOpacity(fade)
        self._paint_content(painter, center, radius)
        painter.setOpacity(1.0)

        # 4) 爆破粒子
        if self._burst > 0.0:
            self._paint_burst(painter, center, radius, main_hex, deep_hex)

        painter.end()

    def _paint_content(self, painter: QPainter, center: QPointF, radius: float) -> None:
        """绘制 emoji、正文、时间与角标。

        所有内容都排布在**正圆内接正方形**里（边长 = 直径 × `INSCRIBED_RATIO`），
        再切成"上 emoji+徽标 / 中正文 / 下时间"三段，从几何上保证不溢出圆外。
        """
        diameter = radius * 2.0
        side = diameter * INSCRIBED_RATIO
        left = center.x() - side / 2.0
        top = center.y() - side / 2.0

        # 上段：emoji 居左、模式/状态徽标居右（同一行，互不重叠）
        emoji_size = max(14, int(diameter * 0.17))
        emoji_h = emoji_size + EMOJI_GAP
        painter.setFont(theme.emoji_font(emoji_size))
        painter.setPen(QPen(theme.qcolor(theme.INK)))
        painter.drawText(
            QRectF(left - 2, top, emoji_size * 1.6, emoji_h),
            Qt.AlignLeft | Qt.AlignVCenter,
            theme.emotion_icon(self._bubble.emotion_tag),
        )

        pill = QRectF(left + side - MODE_PILL_W, top + (emoji_h - MODE_PILL_H) / 2.0,
                      MODE_PILL_W, MODE_PILL_H)
        badge = self._badge_text()
        if badge:
            painter.setFont(theme.emoji_font(11))
            painter.setPen(QPen(theme.qcolor(theme.PRIMARY_DEEP, 230)))
            painter.drawText(pill, Qt.AlignCenter, badge)
        else:
            self._paint_mode_pill(painter, pill)

        # 中段：正文（在剩余高度里垂直居中）
        metrics = QFontMetrics(self._text_font)
        painter.setFont(self._text_font)
        painter.setPen(QPen(theme.qcolor(theme.INK)))
        line_height = metrics.height()
        text_top = top + emoji_h
        text_height = max(line_height, side - emoji_h - TIME_BAND)
        block_height = len(self._lines) * line_height
        cursor_y = text_top + max(0.0, (text_height - block_height) / 2.0)
        for line in self._lines:
            painter.drawText(
                QRectF(left, cursor_y, side, line_height),
                Qt.AlignHCenter | Qt.AlignVCenter,
                line,
            )
            cursor_y += line_height

        # 下段：时间（+ 回复数）。用比正文更宽的一带，避免被裁切
        label = self._bubble.face_time
        if self._bubble.reply_count > 0:
            label = f"{label} · {min(self._bubble.reply_count, 99)}"
        band_width = min(diameter * 0.86, side + 18)
        painter.setFont(self._time_font)
        painter.setPen(QPen(theme.qcolor(theme.INK_SOFT, 205)))
        painter.drawText(
            QRectF(center.x() - band_width / 2.0, top + side - TIME_BAND,
                   band_width, TIME_BAND),
            Qt.AlignHCenter | Qt.AlignVCenter,
            label,
        )

    def _paint_mode_pill(self, painter: QPainter, rect: QRectF) -> None:
        """右上角模式徽标：`单` / `聊`（单次回复 / 短时对话）。"""
        _, deep_hex, _ = theme.emotion_colors(self._bubble.emotion_tag)
        label = "聊" if self._bubble.reply_mode == "dialog" else "单"
        painter.setPen(Qt.NoPen)
        painter.setBrush(theme.qcolor(deep_hex, 205))
        painter.drawRoundedRect(rect, 7, 7)
        painter.setFont(theme.font(7, bold=True))
        painter.setPen(QPen(theme.qcolor("#FFFFFF")))
        painter.drawText(rect, Qt.AlignCenter, label)

    def _badge_text(self) -> str:
        """右上角角标：待同步 / 已保留 / 已解决。"""
        if self._bubble.sync_state == "pending":
            return "⏳"
        if self._bubble.sync_state == "failed":
            return "⚠️"
        if self._bubble.status == "saved_public":
            return "📦"
        if self._bubble.status == "saved_private":
            return "🔒"
        return ""

    def _paint_burst(
        self,
        painter: QPainter,
        center: QPointF,
        radius: float,
        main_hex: str,
        deep_hex: str,
    ) -> None:
        """绘制四散粒子（12 条放射短弧）。"""
        progress = self._burst
        alpha = int(210 * (1.0 - progress))
        if alpha <= 0:
            return
        pen = QPen(theme.qcolor(main_hex, alpha), 3)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        for index in range(12):
            angle = math.radians(index * 30 + 8)
            inner = radius * (1.02 + 0.55 * progress)
            outer = inner + 10 * (1.0 - 0.4 * progress)
            start = QPointF(
                center.x() + math.cos(angle) * inner,
                center.y() + math.sin(angle) * inner,
            )
            end = QPointF(
                center.x() + math.cos(angle) * outer,
                center.y() + math.sin(angle) * outer,
            )
            painter.drawLine(start, end)
        painter.setPen(QPen(theme.qcolor(deep_hex, alpha // 2), 1))
        painter.drawEllipse(center, radius * (1.0 + 0.30 * progress), radius * (1.0 + 0.30 * progress))


__all__ = ["BubbleView", "diameter_for", "GLOW_MARGIN", "BURST_DURATION_MS"]
