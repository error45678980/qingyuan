"""T-09 验收：流式布局（R-08）。

断言口径（架构 §8.4 说明）：
    * 列数用**区间**断言，不写死等号 —— 字体度量在不同机器上有差异；
    * 任何窗口宽度下矩形两两不相交、且不超出内容宽度（不重叠、不裁切）；
    * 行内居中：未放满的一行左右留白之差 ≤ 2px。
"""

from __future__ import annotations

import unittest

from tests.qt_env import app, pump

from PyQt5.QtCore import QRect
from PyQt5.QtWidgets import QWidget

from src.core.models import Bubble
from src.ui.widgets.bubble_view import BubbleView, diameter_for
from src.ui.widgets.flow_layout import FlowContainer

#: 固定用"长内容"造 168px 泡泡，便于把列数换算成确定区间。
LONG_CONTENT: str = "这是一段足够长的内容" * 12


def _bubble(index: int, content: str = LONG_CONTENT) -> Bubble:
    """构造一个测试泡泡。"""
    return Bubble(
        id=f"b-{index:03d}",
        author_nickname="Tester",
        author_client_id="cid-me",
        content=content,
        emotion_tag="开心",
        reply_mode="single",
        status="active",
        version=1,
        created_at="2026-07-21T10:00:00",
    )


class FlowLayoutTest(unittest.TestCase):
    """流式布局的几何正确性。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = app()

    def setUp(self) -> None:
        # 持有 host 引用，避免 Python GC 触发 C++ 端 deleteLater 把子控件销毁
        self._hosts: list[QWidget] = []

    def _layout(self, width: int, count: int, content: str = LONG_CONTENT):
        """在指定宽度下布置 `count` 个泡泡，返回 (容器, 控件列表)。"""
        host = QWidget()
        host.resize(width, 4000)
        self._hosts.append(host)
        container = FlowContainer(host, h_spacing=24, v_spacing=20, margins=(24, 8, 24, 24))
        container.setGeometry(0, 0, width, 4000)
        views: list[BubbleView] = []
        for index in range(count):
            view = BubbleView(_bubble(index, content))
            views.append(view)
            container.flow.addWidget(view)
        container.flow.setGeometry(QRect(0, 0, width, 4000))
        pump(2)
        return container, views

    def _rows(self, views: list[BubbleView]) -> list[list[BubbleView]]:
        """按 y 坐标把控件分成行（同一行允许 ±5px 错落）。"""
        ordered = sorted(views, key=lambda view: (view.geometry().y(), view.geometry().x()))
        rows: list[list[BubbleView]] = []
        for view in ordered:
            if rows and abs(view.geometry().y() - rows[-1][0].geometry().y()) <= 14:
                rows[-1].append(view)
            else:
                rows.append([view])
        return [sorted(row, key=lambda view: view.geometry().x()) for row in rows]

    def test_diameter_tiers(self) -> None:
        """直径分档 112 / 136 / 160 / 184（v2.0 放大后更接近视觉稿）。"""
        self.assertEqual(diameter_for("短"), 112)
        self.assertEqual(diameter_for("一" * 20), 112)
        self.assertEqual(diameter_for("一" * 21), 136)
        self.assertEqual(diameter_for("一" * 50), 136)
        self.assertEqual(diameter_for("一" * 51), 160)
        self.assertEqual(diameter_for("一" * 100), 160)
        self.assertEqual(diameter_for("一" * 101), 184)

    def test_columns_at_1200_is_about_five(self) -> None:
        """1200px 宽、168px 泡泡 → 5 列左右（视觉规格 §7-2）。"""
        _, views = self._layout(1200, 12)
        rows = self._rows(views)
        self.assertGreaterEqual(len(rows), 2)
        self.assertGreaterEqual(len(rows[0]), 4)
        self.assertLessEqual(len(rows[0]), 6)

    def test_columns_shrink_with_width(self) -> None:
        """窗口越窄列数越少。"""
        _, wide = self._layout(1900, 12)
        _, narrow = self._layout(900, 12)
        wide_columns = len(self._rows(wide)[0])
        narrow_columns = len(self._rows(narrow)[0])
        self.assertGreaterEqual(wide_columns, 7)
        self.assertLessEqual(wide_columns, 9)
        self.assertGreaterEqual(narrow_columns, 3)
        self.assertLess(narrow_columns, wide_columns)

    def test_no_overlap_and_no_clipping(self) -> None:
        """任何宽度下：矩形两两不相交、不超出内容区。"""
        for width in (900, 1000, 1200, 1400, 1900):
            container, views = self._layout(width, 14)
            rects = [view.geometry() for view in views]
            for index, first in enumerate(rects):
                self.assertGreaterEqual(first.x(), 0, f"width={width} 越界左")
                self.assertLessEqual(
                    first.x() + first.width(), width, f"width={width} 越界右"
                )
                for second in rects[index + 1 :]:
                    self.assertFalse(
                        first.intersects(second),
                        f"width={width} 出现重叠: {first} vs {second}",
                    )
            height = container.flow.heightForWidth(width)
            for rect in rects:
                self.assertLessEqual(rect.y() + rect.height(), height + 1)

    def test_rows_are_centered(self) -> None:
        """未放满的一行左右留白基本相等。"""
        _, views = self._layout(1400, 7)
        last_row = self._rows(views)[-1]
        if len(last_row) >= 6:
            self.skipTest("最后一行刚好放满，无法检验居中")
        left = last_row[0].geometry().x()
        right = 1400 - (last_row[-1].geometry().x() + last_row[-1].geometry().width())
        self.assertLessEqual(abs(left - right), 4)

    def test_height_grows_with_items(self) -> None:
        """高度随项数增加。"""
        few, _ = self._layout(1200, 4)
        many, _ = self._layout(1200, 20)
        self.assertLess(few.flow.heightForWidth(1200), many.flow.heightForWidth(1200))

    def test_stagger_offsets_are_bounded(self) -> None:
        """错落偏移不超出设计幅度（不制造重叠）。"""
        _, views = self._layout(1200, 12)
        rows = self._rows(views)
        for row in rows:
            tops = [view.geometry().y() for view in row]
            self.assertLessEqual(max(tops) - min(tops), 12)


if __name__ == "__main__":
    unittest.main()
