"""
publish_page.py - 发布泡泡页面
精美表单：内容输入 + 情感选择 + 回复模式
"""
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                             QTextEdit, QPushButton, QFrame, QButtonGroup,
                             QRadioButton, QScrollArea, QGraphicsDropShadowEffect)
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QFont, QColor, QPainter, QLinearGradient

from .theme import EMOTION_COLORS, EMOTION_ICONS
import uuid
from datetime import datetime


class PublishPage(QWidget):
    published = pyqtSignal(str, str, str, str)
    cancelled = pyqtSignal()

    def __init__(self, nickname, database, network=None):
        super().__init__()
        self.nickname = nickname
        self.db = database
        self.network = network
        self.setMinimumSize(1000, 700)
        self._setup_ui()

    def paintEvent(self, event):
        painter = QPainter(self)
        grad = QLinearGradient(0, 0, self.width(), self.height())
        grad.setColorAt(0, QColor(240, 230, 255))
        grad.setColorAt(0.3, QColor(255, 232, 240))
        grad.setColorAt(0.6, QColor(230, 240, 255))
        grad.setColorAt(1, QColor(240, 255, 232))
        painter.fillRect(self.rect(), grad)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # 内容区域
        content_widget = QWidget()
        content_widget.setStyleSheet("background: transparent;")
        content_layout = QVBoxLayout(content_widget)
        content_layout.setAlignment(Qt.AlignCenter)

        # 表单卡片
        card = QFrame()
        card.setFixedSize(580, 560)
        card.setStyleSheet("""
            QFrame {
                background: rgba(255,255,255,235);
                border-radius: 30px;
                border: 1px solid rgba(200,180,255,80);
            }
        """)
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(40)
        shadow.setColor(QColor(167, 139, 250, 80))
        shadow.setOffset(0, 8)
        card.setGraphicsEffect(shadow)

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(35, 30, 35, 30)
        card_layout.setSpacing(15)

        # 顶部标题
        top = QHBoxLayout()
        
        back_btn = QPushButton("← 返回")
        back_btn.setFont(QFont("Microsoft YaHei", 10))
        back_btn.setCursor(Qt.PointingHandCursor)
        back_btn.setStyleSheet("""
            QPushButton {
                background: transparent; border: none;
                color: #7C3AED; padding: 5px;
            }
            QPushButton:hover { color: #6D28D9; }
        """)
        back_btn.clicked.connect(self.cancelled.emit)
        top.addWidget(back_btn)
        top.addStretch()

        title = QLabel("💭 发布心情")
        title.setFont(QFont("Microsoft YaHei", 18, QFont.Bold))
        title.setStyleSheet("color: #7C3AED;")
        top.addWidget(title)
        top.addStretch()
        card_layout.addLayout(top)

        card_layout.addSpacing(5)

        # 内容输入
        content_label = QLabel("你的情感诉说 ✍️")
        content_label.setFont(QFont("Microsoft YaHei", 11))
        content_label.setStyleSheet("color: #555;")
        card_layout.addWidget(content_label)

        self.content_edit = QTextEdit()
        self.content_edit.setPlaceholderText("写下你现在的心情...\n分享你的感受，会有人看见的 🌟")
        self.content_edit.setFont(QFont("Microsoft YaHei", 11))
        self.content_edit.setFixedHeight(130)
        self.content_edit.setStyleSheet("""
            QTextEdit {
                border: 2px solid #EDE9FE;
                border-radius: 16px;
                padding: 15px;
                background: rgba(255,255,255,200);
                color: #333;
                font-size: 13px;
            }
            QTextEdit:focus { border-color: #A78BFA; background: white; }
        """)
        card_layout.addWidget(self.content_edit)

        # 情感选择
        emotion_label = QLabel("选择情感标签 🏷️")
        emotion_label.setFont(QFont("Microsoft YaHei", 11))
        emotion_label.setStyleSheet("color: #555;")
        card_layout.addWidget(emotion_label)

        emotion_cont = QFrame()
        emotion_cont.setStyleSheet("background: transparent;")
        emotion_layout = QHBoxLayout(emotion_cont)
        emotion_layout.setSpacing(8)

        self.emotion_group = QButtonGroup()
        emotions = ["开心", "难过", "孤独", "焦虑", "寻求建议", "找人倾诉"]

        for i, emotion in enumerate(emotions):
            btn = QPushButton(f"{EMOTION_ICONS[emotion]} {emotion}")
            btn.setFont(QFont("Microsoft YaHei", 9))
            btn.setFixedHeight(38)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setCheckable(True)
            colors = EMOTION_COLORS[emotion]
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: {colors[2]};
                    border: 2px solid {colors[0]};
                    border-radius: 19px;
                    padding: 3px 14px;
                    color: {colors[1]};
                }}
                QPushButton:checked {{
                    background: {colors[0]};
                    border-color: {colors[1]};
                    color: white;
                }}
                QPushButton:hover {{
                    background: {colors[0]}80;
                    border-color: {colors[1]};
                }}
            """)
            self.emotion_group.addButton(btn, i)
            emotion_layout.addWidget(btn)

        emotion_layout.addStretch()
        card_layout.addWidget(emotion_cont)

        # 回复模式
        mode_label = QLabel("选择回复模式 🔄")
        mode_label.setFont(QFont("Microsoft YaHei", 11))
        mode_label.setStyleSheet("color: #555;")
        card_layout.addWidget(mode_label)

        mode_cont = QFrame()
        mode_cont.setStyleSheet("""
            background: rgba(248,245,255,150);
            border-radius: 16px;
            border: 1px solid #EDE9FE;
        """)
        mode_layout = QHBoxLayout(mode_cont)
        mode_layout.setContentsMargins(20, 15, 20, 15)
        mode_layout.setSpacing(30)

        self.mode_group = QButtonGroup()

        # 单次
        single_frame = QFrame()
        single_frame.setCursor(Qt.PointingHandCursor)
        single_lyt = QVBoxLayout(single_frame)
        single_lyt.setSpacing(3)
        self.single_radio = QRadioButton("📝 单次回复")
        self.single_radio.setFont(QFont("Microsoft YaHei", 11, QFont.Bold))
        self.single_radio.setStyleSheet("color: #555; spacing: 8px;")
        self.single_radio.setChecked(True)
        single_desc = QLabel("每个人只能回复一次\n简单直接，不留牵挂")
        single_desc.setFont(QFont("Microsoft YaHei", 9))
        single_desc.setStyleSheet("color: #999; padding-left: 28px;")
        single_lyt.addWidget(self.single_radio)
        single_lyt.addWidget(single_desc)
        self.mode_group.addButton(self.single_radio, 0)

        # 对话框
        dialog_frame = QFrame()
        dialog_frame.setCursor(Qt.PointingHandCursor)
        dialog_lyt = QVBoxLayout(dialog_frame)
        dialog_lyt.setSpacing(3)
        self.dialog_radio = QRadioButton("💬 短时对话框")
        self.dialog_radio.setFont(QFont("Microsoft YaHei", 11, QFont.Bold))
        self.dialog_radio.setStyleSheet("color: #555; spacing: 8px;")
        dialog_desc = QLabel("可以建立私密对话\n深入交流，彼此倾听")
        dialog_desc.setFont(QFont("Microsoft YaHei", 9))
        dialog_desc.setStyleSheet("color: #999; padding-left: 28px;")
        dialog_lyt.addWidget(self.dialog_radio)
        dialog_lyt.addWidget(dialog_desc)
        self.mode_group.addButton(self.dialog_radio, 1)

        mode_layout.addWidget(single_frame)
        mode_layout.addWidget(dialog_frame)
        mode_layout.addStretch()
        card_layout.addWidget(mode_cont)

        card_layout.addStretch()

        # 发布按钮
        publish_btn = QPushButton("🌟  发布泡泡")
        publish_btn.setFont(QFont("Microsoft YaHei", 14, QFont.Bold))
        publish_btn.setFixedHeight(50)
        publish_btn.setCursor(Qt.PointingHandCursor)
        publish_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #A78BFA, stop:1 #7C3AED);
                color: white;
                border: none;
                border-radius: 25px;
                font-size: 16px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #BDA3FF, stop:1 #8B5CF6);
            }
        """)
        publish_btn.clicked.connect(self._on_publish)
        card_layout.addWidget(publish_btn)

        content_layout.addWidget(card, alignment=Qt.AlignCenter)
        layout.addWidget(content_widget)

    def _on_publish(self):
        content = self.content_edit.toPlainText().strip()
        if not content:
            self.content_edit.setStyleSheet("""
                QTextEdit {
                    border: 2px solid #FF6B6B;
                    border-radius: 16px;
                    padding: 15px;
                    background: rgba(255,255,255,200);
                    color: #333;
                }
            """)
            return

        # 情感标签
        idx = self.emotion_group.checkedId()
        emotions = ["开心", "难过", "孤独", "焦虑", "寻求建议", "找人倾诉"]
        emotion = emotions[idx] if idx >= 0 else "开心"

        # 回复模式
        mode_idx = self.mode_group.checkedId()
        reply_mode = "dialog" if mode_idx == 1 else "single"

        bubble_id = str(uuid.uuid4())
        created_at = datetime.now().isoformat()

        self.db.save_bubble(bubble_id, self.nickname, content, emotion, reply_mode, created_at=created_at)

        if self.network and self.network.is_online():
            try:
                self.network.publish_bubble(bubble_id, content, emotion, reply_mode, created_at)
            except Exception:
                pass

        self.published.emit(bubble_id, content, emotion, reply_mode)
