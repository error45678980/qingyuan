"""通用组件基座（T-08）：渐变页、卡片、胶囊、空态、头像、按钮、文本工具。

设计约束（视觉规格 §6）：
    * 自绘控件一律 `QPainter` 绘制圆角/圆形，**不用** QSS `border-radius`
      （`border-radius` 对 `QWidget` 子类无效）；
    * 容器背景**不用** `QFrame { ... }` 选择器（会连带 QLabel 出现白方块），
      需要背景就自绘或使用 `#objectName` 选择器；
    * 渐变背景只在本文件的 `GradientPage.paintEvent` 绘制一次（R-18）。
"""

from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import QPointF, QRect, QRectF, QSize, Qt, pyqtSignal
from PyQt5.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)
from PyQt5.QtWidgets import (
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from src.ui import theme

# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def apply_shadow(
    widget: QWidget,
    *,
    blur: int = 26,
    color: Optional[QColor] = None,
    dy: int = 6,
) -> QGraphicsDropShadowEffect:
    """给控件挂一层柔和阴影（Qt 自带 effect，无需图片资源）。"""
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setColor(color if color is not None else theme.qcolor(theme.PRIMARY, 55))
    effect.setOffset(0, dy)
    widget.setGraphicsEffect(effect)
    return effect


def make_label(
    text: str,
    *,
    size: int = 10,
    color: str = theme.INK,
    bold: bool = False,
    wrap: bool = False,
) -> QLabel:
    """构造统一风格的 `QLabel`（显式透明背景，避免白方块）。"""
    label = QLabel(text)
    label.setFont(theme.font(size, bold=bold))
    label.setStyleSheet(f"QLabel {{ color: {color}; background: transparent; }}")
    label.setWordWrap(wrap)
    label.setAttribute(Qt.WA_TranslucentBackground, True)
    return label


def _is_cjk(char: str) -> bool:
    """判断是否为需要逐字断行的中日韩字符。"""
    code = ord(char)
    return (
        0x3000 <= code <= 0x303F      # 中日韩标点
        or 0x3040 <= code <= 0x30FF   # 假名
        or 0x3400 <= code <= 0x4DBF   # 扩展 A
        or 0x4E00 <= code <= 0x9FFF   # 基本区
        or 0xF900 <= code <= 0xFAFF   # 兼容区
        or 0xFF00 <= code <= 0xFFEF   # 全角
    )


def _tokenize(text: str) -> list[str]:
    """把文本切成"断行单元"：CJK 逐字、空格独立、拉丁单词整体。

    Args:
        text: 已压缩空白的文本。

    Returns:
        断行单元列表。
    """
    tokens: list[str] = []
    buffer = ""
    for char in text:
        if _is_cjk(char) or char == " ":
            if buffer:
                tokens.append(buffer)
                buffer = ""
            tokens.append(char)
        else:
            buffer += char
    if buffer:
        tokens.append(buffer)
    return tokens


def wrap_text_lines(
    text: str,
    font: QFont,
    max_width: int,
    max_lines: int,
) -> list[str]:
    """把文本按像素宽度折成若干行，最后一行超长时加省略号。

    中文逐字断行，拉丁单词整体换行（避免 "Hello wor/ld" 这种断词）。

    Args:
        text: 原文（会先压缩连续空白）。
        font: 量宽用字体。
        max_width: 单行最大像素宽。
        max_lines: 最多行数。

    Returns:
        行文本列表（可能为空）。
    """
    if max_width <= 0 or max_lines <= 0:
        return []
    metrics = QFontMetrics(font)
    flat = " ".join(str(text).split())
    if not flat:
        return []
    tokens = _tokenize(flat)
    lines: list[str] = []
    current = ""
    consumed = 0
    truncated = False

    index = 0
    while index < len(tokens):
        token = tokens[index]
        candidate = current + token
        if metrics.horizontalAdvance(candidate) <= max_width:
            current = candidate
            index += 1
            consumed = index
            continue
        if token == " ":
            # 空格放不下就直接换行，不留行尾空格
            if current.strip():
                lines.append(current.rstrip())
            current = ""
            index += 1
            consumed = index
            if len(lines) >= max_lines:
                truncated = index < len(tokens)
                break
            continue
        if current.strip():
            lines.append(current.rstrip())
            current = ""
            if len(lines) >= max_lines:
                truncated = True
                break
            continue
        # 单 token 比整行还宽：退化为逐字硬切
        piece = ""
        for char in token:
            if metrics.horizontalAdvance(piece + char) <= max_width:
                piece += char
                continue
            break
        if not piece:
            piece = token[0]
        current = piece
        tokens[index] = token[len(piece):]
        consumed = index
        if not tokens[index]:
            index += 1
            consumed = index
        if len(lines) >= max_lines:
            truncated = True
            break

    if current.strip() and len(lines) < max_lines:
        lines.append(current.rstrip())
    elif current.strip():
        truncated = True

    if truncated and lines:
        tail = flat[sum(len(line) for line in lines):].strip()
        lines[-1] = metrics.elidedText(lines[-1] + tail, Qt.ElideRight, max_width)
    return lines[:max_lines]


# ---------------------------------------------------------------------------
# 渐变页面基类（R-18：全项目唯一的渐变绘制处）
# ---------------------------------------------------------------------------


class GradientPage(QWidget):
    """带梦幻渐变背景的页面基类。

    子类**不得**重写 `paintEvent`（否则 R-18 的"只在一处绘制"立即失效）；
    需要额外绘制的场景请使用子控件自绘。
    """

    #: 三个装饰光晕：(相对 x, 相对 y, 相对半径, 颜色, alpha)
    _BLOBS: tuple[tuple[float, float, float, str, int], ...] = (
        (0.16, 0.10, 0.62, "#C4B5FD", 92),
        (0.84, 0.18, 0.58, "#FBCFE8", 82),
        (0.58, 0.90, 0.60, "#BFDBFE", 72),
    )

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        """构造。"""
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, False)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """绘制底色渐变 + 三处柔光。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = self.rect()

        base = QLinearGradient(0, 0, rect.width(), rect.height())
        base.setColorAt(0.0, theme.qcolor(theme.BG_TOP))
        base.setColorAt(0.55, theme.qcolor("#F5F3FB"))
        base.setColorAt(1.0, theme.qcolor(theme.BG_BOTTOM))
        painter.fillRect(rect, base)

        painter.setPen(Qt.NoPen)
        span = max(rect.width(), rect.height())
        for rel_x, rel_y, rel_r, color, alpha in self._BLOBS:
            radius = span * rel_r * 0.55
            center = QPointF(rect.width() * rel_x, rect.height() * rel_y)
            blob = QRadialGradient(center, radius)
            blob.setColorAt(0.0, theme.qcolor(color, alpha))
            blob.setColorAt(1.0, theme.qcolor(color, 0))
            painter.setBrush(QBrush(blob))
            painter.drawEllipse(center, radius, radius)
        painter.end()


# ---------------------------------------------------------------------------
# 卡片
# ---------------------------------------------------------------------------


class Card(QWidget):
    """自绘圆角卡片（白底 + 1px 描边 + 可选阴影）。

    为什么不用 QSS：`border-radius` 对 `QWidget` 子类无效（视觉规格 §6-2）。
    """

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        radius: int = 24,
        background: str = theme.CARD_BG,
        border: str = theme.CARD_BORDER,
        shadow: bool = True,
    ) -> None:
        """构造。

        Args:
            parent: 父控件。
            radius: 圆角半径。
            background: 底色。
            border: 描边色。
            shadow: 是否加阴影。
        """
        super().__init__(parent)
        self._radius = int(radius)
        self._background = background
        self._border = border
        if shadow:
            apply_shadow(self, blur=38, color=theme.qcolor("#6D28D9", 40), dy=10)

    def set_radius(self, radius: int) -> None:
        """改圆角并重绘。"""
        self._radius = int(radius)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """绘制圆角卡片背景。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, self._radius, self._radius)
        painter.fillPath(path, theme.qcolor(self._background))
        painter.setPen(QPen(theme.qcolor(self._border), 1))
        painter.drawPath(path)
        painter.end()


# ---------------------------------------------------------------------------
# 情感筛选胶囊
# ---------------------------------------------------------------------------


class Chip(QWidget):
    """情感筛选胶囊（选中紫底白字，未选中白底描边）。"""

    clicked = pyqtSignal(str)

    def __init__(
        self,
        value: str,
        label: str,
        *,
        parent: Optional[QWidget] = None,
        icon: str = "",
    ) -> None:
        """构造。

        Args:
            value: 携带的值（`全部` 或情感标签）。
            label: 展示文本。
            parent: 父控件。
            icon: 前缀 emoji。
        """
        super().__init__(parent)
        self._value = value
        self._label = label
        self._icon = icon
        self._selected = False
        self._hover = False
        self.setCursor(Qt.PointingHandCursor)
        self.setFont(theme.font(10))
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setAttribute(Qt.WA_Hover, True)

    # -- 属性 -----------------------------------------------------------
    @property
    def value(self) -> str:
        """胶囊携带的值。"""
        return self._value

    def is_selected(self) -> bool:
        """是否选中态。"""
        return self._selected

    def set_selected(self, selected: bool) -> None:
        """设置选中态。"""
        if self._selected == bool(selected):
            return
        self._selected = bool(selected)
        self.update()

    # -- 尺寸 -----------------------------------------------------------
    def _display_text(self) -> str:
        """带图标的展示文本。"""
        return f"{self._icon} {self._label}".strip() if self._icon else self._label

    def _size(self) -> QSize:
        """按文本宽度计算尺寸。"""
        metrics = QFontMetrics(self.font())
        width = max(58, metrics.horizontalAdvance(self._display_text()) + 30)
        return QSize(width, 30)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt 命名
        """首选尺寸。"""
        return self._size()

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt 命名
        """最小尺寸。"""
        return self._size()

    # -- 交互 -----------------------------------------------------------
    def enterEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """悬停高亮。"""
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """取消高亮。"""
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """点击派发。"""
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self._value)
        super().mousePressEvent(event)

    # -- 绘制 -----------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """绘制胶囊。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = rect.height() / 2.0
        if self._selected:
            gradient = QLinearGradient(rect.topLeft(), rect.topRight())
            gradient.setColorAt(0.0, theme.qcolor(theme.PRIMARY_HOVER))
            gradient.setColorAt(1.0, theme.qcolor(theme.PRIMARY))
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(gradient))
        else:
            painter.setBrush(theme.qcolor("#FFFFFF", 235 if self._hover else 210))
            painter.setPen(QPen(theme.qcolor(theme.PRIMARY_BORDER if self._hover else "#E6E1F5"), 1))
        painter.drawRoundedRect(rect, radius, radius)

        painter.setFont(self.font())
        painter.setPen(
            QPen(theme.qcolor("#FFFFFF") if self._selected else theme.qcolor(theme.INK_SOFT))
        )
        painter.drawText(rect, Qt.AlignCenter, self._display_text())
        painter.end()


# ---------------------------------------------------------------------------
# 空态
# ---------------------------------------------------------------------------


class EmptyState(QWidget):
    """居中空态：大 emoji + 标题 + 提示。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        """构造。"""
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.setAlignment(Qt.AlignCenter)

        self._icon = QLabel("💫")
        self._icon.setFont(theme.emoji_font(42))
        self._icon.setAlignment(Qt.AlignCenter)
        self._icon.setStyleSheet("QLabel { background: transparent; }")

        self._title = make_label("", size=14, color=theme.INK_SOFT, bold=True)
        self._title.setAlignment(Qt.AlignCenter)

        self._hint = make_label("", size=11, color=theme.INK_FAINT)
        self._hint.setAlignment(Qt.AlignCenter)

        layout.addWidget(self._icon)
        layout.addWidget(self._title)
        layout.addWidget(self._hint)

    def set_text(self, icon: str, title: str, hint: str = "") -> None:
        """更新空态文案。"""
        self._icon.setText(icon)
        self._title.setText(title)
        self._hint.setText(hint)
        self._hint.setVisible(bool(hint))


# ---------------------------------------------------------------------------
# 头像
# ---------------------------------------------------------------------------


class Avatar(QWidget):
    """昵称首字圆形头像（颜色由昵称稳定派生）。"""

    _PALETTE: tuple[tuple[str, str], ...] = (
        ("#A78BFA", "#7C3AED"),
        ("#7DD3FC", "#0284C7"),
        ("#FCA5A5", "#EF4444"),
        ("#86EFAC", "#16A34A"),
        ("#FDBA74", "#EA580C"),
        ("#F9A8D4", "#DB2777"),
    )

    def __init__(self, nickname: str = "", *, size: int = 26, parent: Optional[QWidget] = None) -> None:
        """构造。

        Args:
            nickname: 昵称（取首字）。
            size: 直径。
            parent: 父控件。
        """
        super().__init__(parent)
        self._nickname = nickname or "?"
        self._size = int(size)
        self.setFixedSize(self._size, self._size)

    def set_nickname(self, nickname: str) -> None:
        """更新昵称并重绘。"""
        self._nickname = nickname or "?"
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """绘制圆形头像。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect())
        light, deep = self._PALETTE[sum(ord(ch) for ch in self._nickname) % len(self._PALETTE)]
        gradient = QRadialGradient(rect.center(), rect.width() / 2.0)
        gradient.setColorAt(0.0, theme.qcolor(light))
        gradient.setColorAt(1.0, theme.qcolor(deep))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(gradient))
        painter.drawEllipse(rect)
        painter.setFont(theme.font(max(8, int(self._size * 0.42)), bold=True))
        painter.setPen(QPen(theme.qcolor("#FFFFFF")))
        painter.drawText(rect, Qt.AlignCenter, self._nickname[0])
        painter.end()


# ---------------------------------------------------------------------------
# 按钮
# ---------------------------------------------------------------------------


class PrimaryButton(QPushButton):
    """主按钮（紫色胶囊）。"""

    def __init__(self, text: str, parent: Optional[QWidget] = None, *, height: int = 44) -> None:
        """构造。"""
        super().__init__(text, parent)
        self.setObjectName("primaryBtn")
        self.setStyleSheet(theme.QSS_PRIMARY_BUTTON)
        self.setFont(theme.font(11, bold=True))
        self.setFixedHeight(height)
        self.setCursor(Qt.PointingHandCursor)


class GhostButton(QPushButton):
    """次按钮（白底描边）。"""

    def __init__(self, text: str, parent: Optional[QWidget] = None, *, height: int = 36) -> None:
        """构造。"""
        super().__init__(text, parent)
        self.setObjectName("ghostBtn")
        self.setStyleSheet(theme.QSS_GHOST_BUTTON)
        self.setFont(theme.font(10))
        self.setFixedHeight(height)
        self.setCursor(Qt.PointingHandCursor)


class NavButton(QPushButton):
    """顶栏导航按钮（可选中）。"""

    def __init__(self, text: str, parent: Optional[QWidget] = None) -> None:
        """构造。"""
        super().__init__(text, parent)
        self.setObjectName("navBtn")
        self.setStyleSheet(theme.QSS_NAV_BUTTON)
        self.setFont(theme.font(10))
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(28)


class FloatingButton(QPushButton):
    """右下角紫色圆形悬浮发布按钮（60px，带阴影）。"""

    def __init__(self, parent: Optional[QWidget] = None, *, diameter: int = 60) -> None:
        """构造。"""
        super().__init__("＋", parent)
        self._diameter = int(diameter)
        self.setFixedSize(self._diameter, self._diameter)
        self.setCursor(Qt.PointingHandCursor)
        self.setFont(theme.font(20, bold=True))
        self.setStyleSheet(
            "QPushButton { background: transparent; color: white; border: none; }"
        )
        self.setToolTip("发布一个新泡泡（Ctrl+N）")
        apply_shadow(self, blur=30, color=theme.qcolor(theme.PRIMARY, 120), dy=8)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """绘制圆形按钮。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
        gradient.setColorAt(0.0, theme.qcolor(theme.PRIMARY_HOVER))
        gradient.setColorAt(1.0, theme.qcolor(theme.PRIMARY_DEEP))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(gradient))
        painter.drawEllipse(rect)
        painter.setFont(self.font())
        painter.setPen(QPen(theme.qcolor("#FFFFFF")))
        painter.drawText(rect, Qt.AlignCenter, "＋")
        painter.end()


class RoundIconButton(QPushButton):
    """圆形图标按钮（抽屉关闭等）。"""

    def __init__(self, text: str, parent: Optional[QWidget] = None, *, diameter: int = 30) -> None:
        """构造。"""
        super().__init__(text, parent)
        self.setFixedSize(diameter, diameter)
        self.setCursor(Qt.PointingHandCursor)
        self.setFont(theme.font(11))
        self.setStyleSheet(
            "QPushButton { background: #F4F1FB; color: #6B6880; border: none;"
            f" border-radius: {diameter // 2}px; }}"
            "QPushButton:hover { background: #EDE9FE; color: #7C3AED; }"
        )


def horizontal_rule(parent: Optional[QWidget] = None, color: str = "#EFECF8") -> QWidget:
    """一条 1px 分隔线（用 QWidget 自绘，避免 QSS 选择器连带）。"""
    line = QWidget(parent)
    line.setFixedHeight(1)
    line.setStyleSheet(f"QWidget {{ background: {color}; }}")
    return line


def row_widget(*children: QWidget, spacing: int = 8, margins: tuple[int, int, int, int] = (0, 0, 0, 0)) -> QWidget:
    """把若干控件横向排列成一个容器（避免各处重复写 QHBoxLayout）。"""
    container = QWidget()
    layout = QHBoxLayout(container)
    layout.setContentsMargins(*margins)
    layout.setSpacing(spacing)
    for child in children:
        layout.addWidget(child)
    layout.addStretch(1)
    return container


def center_rect(outer: QRect, width: int, height: int) -> QRect:
    """在 `outer` 内居中返回一个 `width x height` 的矩形（离屏截图/测试用）。"""
    return QRect(
        outer.x() + (outer.width() - width) // 2,
        outer.y() + (outer.height() - height) // 2,
        width,
        height,
    )


__all__ = [
    "apply_shadow",
    "make_label",
    "wrap_text_lines",
    "GradientPage",
    "Card",
    "Chip",
    "EmptyState",
    "Avatar",
    "PrimaryButton",
    "GhostButton",
    "NavButton",
    "FloatingButton",
    "RoundIconButton",
    "horizontal_rule",
    "row_widget",
    "center_rect",
]
