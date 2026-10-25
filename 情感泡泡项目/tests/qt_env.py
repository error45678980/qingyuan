"""UI 测试的 Qt 环境（offscreen，不弹窗）。

**仅供批次 B 的 UI 测试使用**（`test_flow_layout` / `test_wall_diff` / `test_hygiene`）。
批次 A 的测试仍然不 import PyQt5（`test_sync_e2e.StaticHygieneTest` 只豁免这三个文件）。

离屏平台没有字体（视觉规格 §6-3），因此这里只断言**几何**，不断言文字渲染。
"""

from __future__ import annotations

import os

# 必须在导入 PyQt5 之前设置，否则会尝试连接真实窗口系统。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication  # noqa: E402

_APP: QApplication | None = None


def app() -> QApplication:
    """返回进程内唯一的 `QApplication`。"""
    global _APP
    instance = QApplication.instance()
    if instance is None:
        instance = QApplication([])
    _APP = instance
    return instance


def pump(times: int = 3) -> None:
    """跑几轮事件循环，让布局/几何生效。"""
    application = app()
    for _ in range(max(1, times)):
        application.processEvents()


__all__ = ["app", "pump"]
