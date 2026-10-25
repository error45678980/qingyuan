"""UI 组件包（T-08 ~ T-12）。

模块划分：
    common.py     渐变页 / 卡片 / 胶囊 / 空态 / 头像 / 按钮 / 文本工具
    top_bar.py    顶栏（墙与「我的」共用）
    message.py    对话消息气泡
    flow_layout.py 自适应流式布局
    bubble_view.py 圆形泡泡（自绘）
    animation.py  全局单定时器动画
    toast.py      底部轻提示
    drawer.py     右侧抽屉容器
"""

from src.ui.widgets.animation import BubbleAnimator
from src.ui.widgets.bubble_view import BubbleView, diameter_for
from src.ui.widgets.common import (
    Avatar,
    Card,
    Chip,
    EmptyState,
    FloatingButton,
    GhostButton,
    GradientPage,
    NavButton,
    PrimaryButton,
    RoundIconButton,
    apply_shadow,
    make_label,
    wrap_text_lines,
)
from src.ui.widgets.drawer import Drawer
from src.ui.widgets.flow_layout import FlowContainer, FlowLayout
from src.ui.widgets.message import MessageBubble
from src.ui.widgets.toast import Toast
from src.ui.widgets.top_bar import TopBar

__all__ = [
    "BubbleAnimator",
    "BubbleView",
    "diameter_for",
    "Avatar",
    "Card",
    "Chip",
    "EmptyState",
    "FloatingButton",
    "GhostButton",
    "GradientPage",
    "NavButton",
    "PrimaryButton",
    "RoundIconButton",
    "apply_shadow",
    "make_label",
    "wrap_text_lines",
    "Drawer",
    "FlowContainer",
    "FlowLayout",
    "MessageBubble",
    "Toast",
    "TopBar",
]
