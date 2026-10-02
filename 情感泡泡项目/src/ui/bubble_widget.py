"""
bubble_widget.py - 精美泡泡卡片组件
每个泡泡显示为一个圆润的气泡卡片，带渐变和光泽效果
"""
from PyQt5.QtWidgets import QFrame, QLabel, QVBoxLayout, QHBoxLayout
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QPainter, QRadialGradient, QColor, QFont, QBrush, QPen, QLinearGradient

from .theme import EMOTION_COLORS, EMOTION_ICONS


class BubbleCard(QFrame):
    """泡泡卡片 - 显示在泡泡墙上的单个泡泡"""
    clicked = pyqtSignal(str)

    def __init__(self, bubble_data):
        """
        bubble_data: (id, author, node_id, content, emotion, reply_mode, status, created_at, resolved_at)
        """
        super().__init__()
        self.bubble_id = bubble_data[0]
        self.author = bubble_data[1]
        self.content = bubble_data[3]
        self.emotion = bubble_data[4]
        self.reply_mode = bubble_data[5]
        self.created_at = bubble_data[6]

        self.setFixedSize(280, 210)
        self.setCursor(Qt.PointingHandCursor)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 15)
        layout.setSpacing(8)

        # 顶部：图标 + 昵称 + 标签
        top = QHBoxLayout()
        icon = QLabel(EMOTION_ICONS.get(self.emotion, "💭"))
        icon.setFont(QFont("Segoe UI Emoji", 24))
        icon.setFixedSize(36, 36)
        top.addWidget(icon)

        name = QLabel(self.author)
        name.setFont(QFont("Microsoft YaHei", 11, QFont.Bold))
        name.setStyleSheet("color: #555;")
        top.addWidget(name)
        top.addStretch()

        tag = QLabel(f"#{self.emotion}")
        tag.setFont(QFont("Microsoft YaHei", 9))
        colors = EMOTION_COLORS.get(self.emotion, ("#ccc", "#999", "#fff"))
        tag.setStyleSheet(f"color: {colors[1]}; padding: 2px 8px; border-radius: 8px;")
        top.addWidget(tag)
        layout.addLayout(top)

        # 内容预览
        preview = self.content[:50] + "..." if len(self.content) > 50 else self.content
        content_label = QLabel(preview)
        content_label.setFont(QFont("Microsoft YaHei", 10))
        content_label.setStyleSheet("color: #444; line-height: 1.5;")
        content_label.setWordWrap(True)
        content_label.setFixedHeight(60)
        layout.addWidget(content_label)

        layout.addStretch()

        # 底部：回复模式 + 时间
        bottom = QHBoxLayout()
        mode_icon = "📝" if self.reply_mode == "single" else "💬"
        mode_text = "单次回复" if self.reply_mode == "single" else "对话框"
        mode_label = QLabel(f"{mode_icon} {mode_text}")
        mode_label.setFont(QFont("Microsoft YaHei", 8))
        mode_label.setStyleSheet("color: #aaa;")
        bottom.addWidget(mode_label)

        bottom.addStretch()

        time_str = self.created_at[5:16].replace("T", " ")
        time_label = QLabel(time_str)
        time_label.setFont(QFont("Microsoft YaHei", 8))
        time_label.setStyleSheet("color: #bbb;")
        bottom.addWidget(time_label)
        layout.addLayout(bottom)

    def paintEvent(self, event):
        """绘制精美泡泡卡片背景"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        colors = EMOTION_COLORS.get(self.emotion, ("#E8E8E8", "#C0C0C0", "#F8F8F8"))

        # 主背景径向渐变（泡泡感）
        grad = QRadialGradient(self.width() // 2, self.height() // 2, self.width() // 2 + 10)
        grad.setColorAt(0, QColor(colors[2]))
        grad.setColorAt(0.6, QColor(colors[0]))
        grad.setColorAt(1, QColor(colors[1]).lighter(120))
        painter.setBrush(QBrush(grad))
        painter.setPen(QPen(QColor(colors[1]).lighter(150), 1.5))
        painter.drawRoundedRect(2, 2, self.width() - 4, self.height() - 4, 22, 22)

        # 高光（左上角光泽）
        highlight = QRadialGradient(60, 40, 100)
        highlight.setColorAt(0, QColor(255, 255, 255, 140))
        highlight.setColorAt(0.4, QColor(255, 255, 255, 40))
        highlight.setColorAt(1, QColor(255, 255, 255, 0))
        painter.setBrush(QBrush(highlight))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(2, 2, self.width() - 4, self.height() - 4, 22, 22)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.bubble_id)
        super().mousePressEvent(event)
