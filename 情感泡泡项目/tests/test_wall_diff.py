"""T-10 验收：刷新按 id diff 复用（R-14）+ 新泡泡不打断视图（R-13）。

断言口径：
    * 连续 10 次刷新同一批数据：widget 身份（`id()`）不变、数量不增长；
    * 滚动位置偏移 ≤ 10px；
    * 用户已滚动时出现新泡泡：不插入、只提示，点击提示后才插入并回到顶部；
    * 数据没变时 `BubbleView.update_from()` 返回 False（不触发重绘）。
"""

from __future__ import annotations

import unittest

from tests.qt_env import app, pump

from src.core.models import Bubble
from src.ui.widgets.animation import BubbleAnimator
from src.ui.widgets.bubble_view import BubbleView
from src.ui.pages.wall_page import WallPage

#: 制造足够多的泡泡，保证出现滚动条。
TOTAL: int = 42


def _bubble(index: int, *, reply_count: int = 0) -> Bubble:
    """构造测试泡泡。"""
    return Bubble(
        id=f"w-{index:03d}",
        author_nickname=f"用户{index % 7}",
        author_client_id=f"cid-{index % 7}",
        content=f"这是第 {index} 个泡泡，写点足够长的话让它的直径大一些" * (1 + index % 2),
        emotion_tag=("开心", "难过", "孤独", "焦虑", "寻求建议", "找人倾诉")[index % 6],
        reply_mode="dialog" if index % 3 == 0 else "single",
        status="active",
        version=1,
        created_at=f"2026-07-21T10:{index:02d}:00",
        reply_count=reply_count,
    )


def _dataset(count: int = TOTAL) -> list[Bubble]:
    """一批泡泡（时间倒序）。"""
    return [_bubble(index) for index in range(count)]


class WallDiffTest(unittest.TestCase):
    """泡泡墙 diff 刷新。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = app()

    def setUp(self) -> None:
        self.wall = WallPage(BubbleAnimator(), nickname="Tester")
        self.wall.resize(1200, 800)
        self.wall.show()
        pump(4)

    def tearDown(self) -> None:
        self.wall.hide()
        self.wall.deleteLater()
        pump(2)

    # ------------------------------------------------------------------
    def test_ten_refreshes_keep_widget_identity(self) -> None:
        """10 次刷新：身份不变、数量不增长（R-14 ②）。"""
        data = _dataset()
        self.wall.apply_bubbles(data, total=len(data), has_more=False)
        pump(2)
        baseline = {bubble_id: id(view) for bubble_id, view in self._views().items()}
        self.assertEqual(len(baseline), TOTAL)

        for _ in range(10):
            self.wall.apply_bubbles(data, total=len(data), has_more=False)
            pump(1)

        current = {bubble_id: id(view) for bubble_id, view in self._views().items()}
        self.assertEqual(set(current), set(baseline), "刷新不得增删 widget")
        self.assertEqual(current, baseline, "刷新不得重建 widget（身份必须不变）")
        self.assertEqual(self.wall.bubble_count(), TOTAL)
        self.assertEqual(self._flow_count(), TOTAL)

    def test_scroll_offset_is_preserved(self) -> None:
        """10 次刷新滚动偏移 ≤ 10px（R-14 ①）。"""
        data = _dataset()
        self.wall.apply_bubbles(data, total=len(data), has_more=False)
        pump(3)
        bar = self.wall._scroll.verticalScrollBar()
        self.assertGreater(bar.maximum(), 0, "测试数据不足以产生滚动条")
        bar.setValue(min(240, bar.maximum()))
        anchor = bar.value()
        for _ in range(10):
            self.wall.apply_bubbles(data, total=len(data), has_more=False)
            pump(1)
        self.assertLessEqual(abs(bar.value() - anchor), 10)

    def test_new_bubbles_do_not_interrupt_when_scrolled(self) -> None:
        """已滚动时新泡泡只提示不插入（R-13）。"""
        data = _dataset()
        self.wall.apply_bubbles(data, total=len(data), has_more=False)
        pump(3)
        bar = self.wall._scroll.verticalScrollBar()
        bar.setValue(min(200, bar.maximum()))

        fresh = [_bubble(1000)] + data
        self.wall.apply_bubbles(fresh, total=len(fresh), has_more=False)
        pump(2)

        self.assertEqual(self.wall.bubble_count(), TOTAL, "不得立即插入新泡泡")
        self.assertEqual(self.wall.new_count(), 1)
        self.assertTrue(self.wall.toast().isVisible(), "应浮出「有 N 个新泡泡」")

        self.wall._on_toast_action()
        pump(3)
        self.assertEqual(self.wall.bubble_count(), TOTAL + 1)
        self.assertEqual(bar.value(), 0, "点击后应回到顶部")
        self.assertEqual(self.wall.ordered_ids()[0], "w-1000")

    def test_removed_bubble_is_dropped(self) -> None:
        """不在新集合里的泡泡会被移除。"""
        data = _dataset()
        self.wall.apply_bubbles(data, total=len(data), has_more=False)
        pump(2)
        remaining = data[1:]
        self.wall.apply_bubbles(remaining, total=len(remaining), has_more=False)
        pump(2)
        self.assertEqual(self.wall.bubble_count(), TOTAL - 1)
        self.assertIsNone(self.wall.view_for(data[0].id))

    def test_removal_waits_for_burst_animation(self) -> None:
        """已解决：爆破动画期间 widget 仍在，播完才移除（R-08 ≥600ms）。"""
        data = _dataset(6)
        self.wall.apply_bubbles(data, total=len(data), has_more=False)
        pump(2)
        target = data[2]
        self.wall.remove_bubble_animated(target.id)
        view = self.wall.view_for(target.id)
        self.assertIsNotNone(view)
        self.assertTrue(view.is_bursting(), "应进入爆破状态")
        self.assertGreaterEqual(
            view._burst_anim.duration(), 600, "爆破动画不得短于 600ms"
        )
        # 动画结束后（这里直接触发回调，避免测试等待 700ms）才真正移除
        self.wall._on_burst_finished(target.id)
        pump(2)
        self.assertIsNone(self.wall.view_for(target.id))

    def test_update_from_returns_false_when_unchanged(self) -> None:
        """数据没变就不重绘（R-14 ③）。"""
        bubble = _bubble(1)
        view = BubbleView(bubble)
        self.assertFalse(view.update_from(bubble), "同一条数据不得触发重绘")
        self.assertTrue(view.update_from(_bubble(1, reply_count=3)), "回复数变化应刷新")
        self.assertFalse(
            view.update_from(_bubble(1, reply_count=3)), "再次相同则不再重绘"
        )

    def test_single_animator_drives_all_bubbles(self) -> None:
        """全部泡泡共用同一个动画定时器（规则 A-1）。"""
        data = _dataset(10)
        self.wall.apply_bubbles(data, total=len(data), has_more=False)
        pump(2)
        animator = self.wall._animator
        self.assertEqual(animator.active_count(), 10)
        self.assertTrue(animator.is_running())

    # ------------------------------------------------------------------
    def _views(self) -> dict[str, BubbleView]:
        """访问内部视图字典（测试需要校验身份）。"""
        return dict(self.wall._views)

    def _flow_count(self) -> int:
        """布局里的项数。"""
        return self.wall._flow.count()


class LocalOnlyMergeTest(unittest.TestCase):
    """服务端拉取结果必须与本机独有泡泡合并（否则自己发的泡泡会凭空消失）。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = app()

    def setUp(self) -> None:
        import os
        import tempfile

        from src.core import config as config_mod
        from src.core import migrations, repo as repo_mod
        from src.core.api import ApiClient
        from src.ui.app_window import AppWindow

        self._tmp = tempfile.mkdtemp(prefix="merge_test_")
        db_path = os.path.join(self._tmp, "c.db")
        migrations.ensure_client_schema(db_path, os.path.join(self._tmp, "bk"))
        self.repo = repo_mod.Repo(db_path)
        self.cfg = config_mod.AppConfig(
            client_id="cid-me", nickname="我", server_url="http://127.0.0.1:1"
        )
        api = ApiClient(self.cfg.server_url, client_id=self.cfg.client_id, timeout=0.2)
        # 昵称留空 → 停在登录页，不启动同步线程
        self.window = AppWindow(config_mod.AppConfig(client_id="cid-me"), self.repo, api)

    def tearDown(self) -> None:
        self.window.close()
        self.repo.close()

    def _seed(self, bubble_id: str, author_client_id: str, author: str) -> None:
        """写入一条本地泡泡。"""
        self.repo.upsert_bubble(
            Bubble(
                id=bubble_id,
                author_nickname=author,
                author_client_id=author_client_id,
                content=f"内容 {bubble_id}",
                emotion_tag="开心",
                reply_mode="single",
                status="active",
                created_at="2026-07-21T10:00:00",
            )
        )

    def test_local_only_bubble_is_kept(self) -> None:
        """我发布的、服务端没有的泡泡，合并后仍在墙上。"""
        self._seed("mine-1", "cid-me", "我")
        self._seed("other-1", "cid-other", "别人")
        remote = [self.repo.get_bubble("other-1")]
        merged = self.window._merge_local_only(remote)  # noqa: SLF001
        self.assertEqual({item.id for item in merged}, {"mine-1", "other-1"})

    def test_remote_items_are_not_duplicated(self) -> None:
        """服务端已有的泡泡不会被重复加入。"""
        self._seed("mine-1", "cid-me", "我")
        remote = [self.repo.get_bubble("mine-1")]
        merged = self.window._merge_local_only(remote)  # noqa: SLF001
        self.assertEqual([item.id for item in merged], ["mine-1"])

    def test_legacy_nickname_fallback(self) -> None:
        """旧数据 author_client_id 为空时，按昵称判定归属。"""
        self.window._config = self.cfg  # noqa: SLF001
        self._seed("legacy-1", "", "我")
        merged = self.window._merge_local_only([])  # noqa: SLF001
        self.assertEqual([item.id for item in merged], ["legacy-1"])


if __name__ == "__main__":
    unittest.main()
