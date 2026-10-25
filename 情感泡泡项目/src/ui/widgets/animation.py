"""全局动画驱动器（T-09，规则 A-1）。

**全应用唯一的动画定时器**在这里：33ms 一次，把 `elapsed_ms` 广播给所有注册的
`BubbleView`，由每个泡泡自己算 `dy` 并 `update()`。

为什么不用"每个泡泡一个 QTimer"：v1 的 P0-3 卡顿正是这么来的（N 个定时器 + N 次
事件循环唤醒）。单定时器 + 注册表（`WeakSet`，widget 销毁自动摘除）既省 CPU，
也不会泄漏（R-14 ②）。
"""

from __future__ import annotations

import weakref
from typing import TYPE_CHECKING, Optional

from PyQt5.QtCore import QObject, Qt, QTimer, pyqtSignal

from src.core.constants import ANIMATION_INTERVAL_MS

if TYPE_CHECKING:  # pragma: no cover - 仅类型检查
    from src.ui.widgets.bubble_view import BubbleView


class BubbleAnimator(QObject):
    """单定时器驱动的泡泡浮动动画。"""

    #: 每次 tick 携带累计毫秒数。
    tick = pyqtSignal(int)

    def __init__(self, interval_ms: int = ANIMATION_INTERVAL_MS, parent: Optional[QObject] = None) -> None:
        """构造。

        Args:
            interval_ms: tick 间隔，默认 33ms（30fps）。
            parent: Qt 父对象。
        """
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setInterval(max(16, int(interval_ms)))
        self._timer.setTimerType(Qt.CoarseTimer)
        self._timer.timeout.connect(self._emit_tick)
        self._views: "weakref.WeakSet[BubbleView]" = weakref.WeakSet()
        self._elapsed = 0
        self._paused = False

    # ------------------------------------------------------------------
    # 注册表
    # ------------------------------------------------------------------
    def register(self, view: "BubbleView") -> None:
        """注册一个泡泡（并确保定时器在跑）。"""
        self._views.add(view)
        if not self._paused and not self._timer.isActive():
            self._timer.start()

    def unregister(self, view: "BubbleView") -> None:
        """注销一个泡泡；注册表空了就停表（泡泡数为 0 时不烧 CPU）。"""
        self._views.discard(view)
        if not self._views:
            self._timer.stop()

    def active_count(self) -> int:
        """当前注册的泡泡数。"""
        return len(self._views)

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def start(self) -> None:
        """启动动画。"""
        self._paused = False
        if self._views and not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        """停止动画（保留注册表）。"""
        self._timer.stop()

    def set_paused(self, paused: bool) -> None:
        """暂停/恢复（窗口最小化时暂停）。"""
        self._paused = bool(paused)
        if self._paused:
            self._timer.stop()
        elif self._views:
            self._timer.start()

    def is_running(self) -> bool:
        """定时器是否在跑。"""
        return self._timer.isActive()

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _emit_tick(self) -> None:
        """累计时间并广播。"""
        if self._paused or not self._views:
            return
        self._elapsed += self._timer.interval()
        self.tick.emit(self._elapsed)


__all__ = ["BubbleAnimator"]
