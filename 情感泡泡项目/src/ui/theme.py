"""
theme.py - 全局主题和样式常量
"""
from PyQt5.QtGui import QColor, QLinearGradient, QRadialGradient

# ==================== 情感分类配色 ====================
EMOTION_COLORS = {
    "开心": ("#FFE066", "#FFB800", "#FFF8DC"),    # 金色暖阳
    "难过": ("#74B9FF", "#0984E3", "#E8F4FD"),    # 清新蓝
    "孤独": ("#C59BFF", "#8854D0", "#F3EAFF"),    # 梦幻紫
    "焦虑": ("#FF8A8A", "#FF4757", "#FFE8E8"),    # 柔粉
    "寻求建议": ("#7BED9F", "#2ED573", "#E8F8F0"), # 治愈绿
    "找人倾诉": ("#FFB78C", "#FF6348", "#FFF0E8"), # 暖橙
}

EMOTION_ICONS = {
    "开心": "😊", "难过": "😢", "孤独": "🥺",
    "焦虑": "😰", "寻求建议": "💡", "找人倾诉": "🗣️",
}

# ==================== 全局样式 ====================
GRADIENT_BG = """
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #F0E6FF, stop:0.3 #FFE8F0, stop:0.6 #E6F0FF, stop:1 #F0FFE8);
"""

BTN_PRIMARY = """
    QPushButton {
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
            stop:0 #A78BFA, stop:1 #7C3AED);
        color: white;
        border: none;
        border-radius: 22px;
        font-size: 14px;
        font-weight: bold;
    }
    QPushButton:hover {
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
            stop:0 #BDA3FF, stop:1 #8B5CF6);
    }
    QPushButton:pressed {
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
            stop:0 #7C3AED, stop:1 #6D28D9);
    }
"""

SCROLLBAR_STYLE = """
    QScrollBar:vertical {
        width: 6px;
        background: rgba(200,200,200,30);
        border-radius: 3px;
    }
    QScrollBar::handle:vertical {
        background: rgba(160,140,220,80);
        border-radius: 3px;
        min-height: 30px;
    }
    QScrollBar::handle:vertical:hover {
        background: rgba(160,140,220,150);
    }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""
