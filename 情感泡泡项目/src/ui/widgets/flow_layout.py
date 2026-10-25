"""自适应流式布局（T-09，R-08）。

特性：
    * 按可用宽度自动换行（能放几个放几个，不写死列数）；
    * 每一行整体**水平居中**；
    * 每个控件带轻微**上下错落偏移**（视觉规格 §1：相邻泡泡有错落感），
      偏移被限制在行带内，保证任何窗口宽度下**不重叠、不裁切**；
    * 实现 `heightForWidth`，配合 `QScrollArea` 纵向滚动。

测试（`tests/test_flow_layout.py`）断言：不同宽度下列数落在区间内、矩形两两不相交、
矩形不超出内容宽度。
"""

from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import QMargins, QPoint, QRect, QSize, Qt
from PyQt5.QtWidgets import QLayout, QLayoutItem, QSizePolicy, QWidget


class FlowLayout(QLayout):
    """流式布局。"""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        h_spacing: int = 24,
        v_spacing: int = 20,
        margins: tuple[int, int, int, int] = (24, 8, 24, 24),
        stagger: int = 5,
    ) -> None:
        """构造。

        Args:
            parent: 宿主控件。
            h_spacing: 水平间距。
            v_spacing: 垂直间距。
            margins: `(左, 上, 右, 下)`。
            stagger: 单个控件最大上下偏移（像素）。
        """
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._h_spacing = max(0, int(h_spacing))
        self._v_spacing = max(0, int(v_spacing))
        self._stagger = max(0, int(stagger))
        self.setContentsMargins(*margins)
        self.setSizeConstraint(QLayout.SetMinAndMaxSize)

    # ------------------------------------------------------------------
    # QLayout 接口
    # ------------------------------------------------------------------
    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802 - Qt 命名
        """追加一项。"""
        self._items.append(item)

    def count(self) -> int:
        """项数。"""
        return len(self._items)

    def itemAt(self, index: int) -> Optional[QLayoutItem]:  # noqa: N802 - Qt 命名
        """取第 index 项。"""
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int) -> Optional[QLayoutItem]:  # noqa: N802 - Qt 命名
        """移除并返回第 index 项。"""
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self) -> Qt.Orientations:  # noqa: N802 - Qt 命名
        """不主动扩张。"""
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt 命名
        """高度依赖宽度。"""
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt 命名
        """给定宽度下的总高度。"""
        return self._do_layout(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802 - Qt 命名
        """执行布局。"""
        super().setGeometry(rect)
        self._do_layout(rect, apply=True)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt 命名
        """首选尺寸。"""
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802 - Qt 命名
        """最小尺寸 = 最大单项 + 边距。"""
        size = QSize(0, 0)
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins: QMargins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    # ------------------------------------------------------------------
    # 间距
    # ------------------------------------------------------------------
    def setSpacing(self, h_spacing: int, v_spacing: Optional[int] = None) -> None:  # noqa: N802 - Qt 命名
        """设置水平/垂直间距。"""
        self._h_spacing = max(0, int(h_spacing))
        self._v_spacing = max(0, int(v_spacing if v_spacing is not None else h_spacing))
        self.invalidate()

    def spacing(self) -> int:
        """水平间距（`QLayout.spacing()` 语义）。"""
        return self._h_spacing

    def verticalSpacing(self) -> int:  # noqa: N802 - Qt 命名
        """垂直间距。"""
        return self._v_spacing

    def setStagger(self, stagger: int) -> None:  # noqa: N802 - Qt 命名
        """设置最大上下错落偏移。"""
        self._stagger = max(0, int(stagger))
        self.invalidate()

    # ------------------------------------------------------------------
    # 布局算法
    # ------------------------------------------------------------------
    def _offset(self, row: int, column: int) -> int:
        """确定性的错落偏移（同一位置每次布局结果一致，避免抖动）。"""
        if self._stagger <= 0:
            return 0
        steps = (-1.0, -0.5, 0.0, 0.5, 1.0)
        seed = (row * 7 + column * 13 + 3) % len(steps)
        return int(round(steps[seed] * self._stagger))

    def _rows(self, available: int) -> list[list[tuple[QLayoutItem, int, int]]]:
        """把项切成行。

        Args:
            available: 可用宽度（已扣除边距）。

        Returns:
            `[[(item, w, h), ...], ...]`。
        """
        rows: list[list[tuple[QLayoutItem, int, int]]] = []
        current: list[tuple[QLayoutItem, int, int]] = []
        used = 0
        for item in self._items:
            hint = item.sizeHint()
            width = max(1, hint.width())
            height = max(1, hint.height())
            if current and used + self._h_spacing + width > available:
                rows.append(current)
                current = []
                used = 0
            if current:
                used += self._h_spacing
            current.append((item, width, height))
            used += width
        if current:
            rows.append(current)
        return rows

    def _do_layout(self, rect: QRect, *, apply: bool) -> int:
        """核心布局。

        Args:
            rect: 目标矩形。
            apply: 是否真的移动子控件（`False` 时仅算高度）。

        Returns:
            所需总高度。
        """
        margins: QMargins = self.contentsMargins()
        left = rect.x() + margins.left()
        top = rect.y() + margins.top()
        available = max(1, rect.width() - margins.left() - margins.right())

        rows = self._rows(available)
        y = top
        for row_index, row in enumerate(rows):
            row_height = max(height for _, _, height in row)
            band_height = row_height + self._stagger * 2
            width_sum = sum(width for _, width, _ in row) + self._h_spacing * (len(row) - 1)
            start_x = left + max(0, (available - width_sum) // 2)
            cursor_x = start_x
            for column, (item, width, height) in enumerate(row):
                offset = self._offset(row_index, column)
                item_rect = QRect(
                    cursor_x,
                    y + (band_height - height) // 2 + offset,
                    width,
                    height,
                )
                if apply:
                    item.setGeometry(item_rect)
                cursor_x += width + self._h_spacing
            y += band_height
            if row_index < len(rows) - 1:
                y += self._v_spacing
        return y - rect.y() + margins.bottom()


class FlowContainer(QWidget):
    """承载 `FlowLayout` 的滚动内容控件。

    `QScrollArea(widgetResizable=True)` 不会自动处理 `heightForWidth`，
    因此这里把布局高度同步为 `minimumHeight`，滚动条才能正确出现。
    """

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        h_spacing: int = 24,
        v_spacing: int = 20,
        margins: tuple[int, int, int, int] = (24, 8, 24, 24),
        stagger: int = 5,
    ) -> None:
        """构造。"""
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.flow = FlowLayout(
            self,
            h_spacing=h_spacing,
            v_spacing=v_spacing,
            margins=margins,
            stagger=stagger,
        )
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)

    # -- Qt 尺寸协商 ----------------------------------------------------
    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt 命名
        """高度依赖宽度。"""
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt 命名
        """给定宽度下的高度。"""
        return self.flow.heightForWidth(width)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """宽度变化时同步最小高度，让 `QScrollArea` 出现滚动条。"""
        super().resizeEvent(event)
        self.setMinimumHeight(self.flow.heightForWidth(self.width()))

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt 命名
        """首选尺寸（宽度取父级，高度按布局）。"""
        width = max(1, self.width())
        return QSize(width, self.flow.heightForWidth(width))

    def relayout(self) -> None:
        """外部数据变化后强制重排（几何改变后滚动范围随之更新）。"""
        self.setMinimumHeight(self.flow.heightForWidth(max(1, self.width())))
        self.flow.invalidate()
        self.updateGeometry()


__all__ = ["FlowLayout", "FlowContainer"]
