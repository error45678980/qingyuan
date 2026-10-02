"""
chat_window.py - 短时对话窗口
私密聊天界面，类似微信的气泡样式
"""
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                             QTextEdit, QPushButton, QFrame, QScrollArea,
                             QGraphicsDropShadowEffect)
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QFont, QColor, QPainter, QLinearGradient

from .theme import SCROLLBAR_STYLE
from datetime import datetime


class ChatWindow(QWidget):
    chat_ended = pyqtSignal()
    message_sent = pyqtSignal(str, str, str, str)

    def __init__(self, database, nickname, bubble_id, network=None):
        super().__init__()
        self.db = database
        self.nickname = nickname
        self.bubble_id = bubble_id
        self.network = network
        self.setMinimumSize(800, 600)
        self._setup_ui()
        self._load_data()

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

        bg = QFrame()
        bg.setStyleSheet("background: rgba(255,255,255,235);")
        bg_layout = QVBoxLayout(bg)
        bg_layout.setContentsMargins(20, 15, 20, 15)
        bg_layout.setSpacing(10)

        # 顶部栏
        top = QHBoxLayout()
        back_btn = QPushButton("←  返回泡泡")
        back_btn.setFont(QFont("Microsoft YaHei", 10))
        back_btn.setCursor(Qt.PointingHandCursor)
        back_btn.setStyleSheet("""
            QPushButton {
                background: transparent; border: none;
                color: #7C3AED; padding: 5px 10px;
            }
            QPushButton:hover { color: #6D28D9; }
        """)
        back_btn.clicked.connect(self.chat_ended.emit)
        top.addWidget(back_btn)

        top.addStretch()

        self.title_label = QLabel("💬 私密对话")
        self.title_label.setFont(QFont("Microsoft YaHei", 16, QFont.Bold))
        self.title_label.setStyleSheet("color: #7C3AED;")
        top.addWidget(self.title_label)

        top.addStretch()
        bg_layout.addLayout(top)

        # 消息区域
        self.msg_scroll = QScrollArea()
        self.msg_scroll.setWidgetResizable(True)
        self.msg_scroll.setStyleSheet(f"""
            QScrollArea {{
                border: 1px solid #EDE9FE;
                border-radius: 18px;
                background: rgba(248,245,255,120);
            }}
            {SCROLLBAR_STYLE}
        """)

        self.msg_container = QWidget()
        self.msg_container.setStyleSheet("background: transparent;")
        self.msg_layout = QVBoxLayout(self.msg_container)
        self.msg_layout.setSpacing(8)
        self.msg_layout.setContentsMargins(15, 15, 15, 15)
        self.msg_layout.addStretch()

        self.msg_scroll.setWidget(self.msg_container)
        bg_layout.addWidget(self.msg_scroll)

        # 输入区
        input_frame = QFrame()
        input_frame.setStyleSheet("""
            background: white;
            border: 1px solid #EDE9FE;
            border-radius: 18px;
        """)
        input_layout = QHBoxLayout(input_frame)
        input_layout.setContentsMargins(12, 8, 12, 8)

        self.msg_input = QTextEdit()
        self.msg_input.setPlaceholderText("输入你的心里话...")
        self.msg_input.setFont(QFont("Microsoft YaHei", 10))
        self.msg_input.setFixedHeight(50)
        self.msg_input.setStyleSheet("""
            QTextEdit {
                border: 2px solid #EDE9FE;
                border-radius: 14px;
                padding: 8px 15px;
                background: rgba(255,255,255,200);
                color: #333;
            }
            QTextEdit:focus { border-color: #A78BFA; }
        """)
        input_layout.addWidget(self.msg_input)

        send_btn = QPushButton("发送 ✨")
        send_btn.setFont(QFont("Microsoft YaHei", 11, QFont.Bold))
        send_btn.setFixedSize(90, 50)
        send_btn.setCursor(Qt.PointingHandCursor)
        send_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #A78BFA, stop:1 #7C3AED);
                color: white;
                border: none;
                border-radius: 14px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #BDA3FF, stop:1 #8B5CF6);
            }
        """)
        send_btn.clicked.connect(self._on_send)
        input_layout.addWidget(send_btn)

        bg_layout.addWidget(input_frame)
        layout.addWidget(bg)

    def _load_data(self):
        if self.network and self.network.is_online():
            try:
                details = self.network.fetch_bubble_details(self.bubble_id, viewer=self.nickname)
                for chat in details.get("chats", []):
                    self.db.save_chat_message(
                        self.bubble_id,
                        chat["sender"],
                        chat["receiver"],
                        chat["content"],
                        created_at=chat.get("created_at")
                    )
            except Exception:
                pass

        bubble = self.db.get_bubble(self.bubble_id)
        if not bubble:
            return
        _, author, _, _, _, _, _, _, _ = bubble
        self.title_label.setText(f"💬 与 {author} 的对话")

        messages = self.db.get_chat_messages(self.bubble_id)
        for msg in messages:
            _, _, sender, receiver, content, created_at = msg
            self._add_message(sender, content, created_at[5:16].replace("T", " "))

    def _add_message(self, sender, content, time_str):
        is_me = (sender == self.nickname)

        msg_frame = QFrame()
        msg_frame.setStyleSheet("background: transparent;")
        msg_layout = QHBoxLayout(msg_frame)
        msg_layout.setContentsMargins(5, 3, 5, 3)

        # 头像
        avatar = QLabel("😊" if is_me else "👤")
        avatar.setFont(QFont("Segoe UI Emoji", 24))
        avatar.setFixedSize(40, 40)
        avatar.setAlignment(Qt.AlignCenter)

        # 消息气泡
        bubble = QFrame()
        if is_me:
            bubble.setStyleSheet("""
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #A78BFA, stop:1 #7C3AED);
                border-radius: 16px;
                border-bottom-right-radius: 4px;
                padding: 10px 16px;
            """)
            msg_layout.addStretch()
            msg_layout.addWidget(bubble)
            msg_layout.addWidget(avatar)
        else:
            bubble.setStyleSheet("""
                background: #F3F0FF;
                border-radius: 16px;
                border-bottom-left-radius: 4px;
                padding: 10px 16px;
            """)
            msg_layout.addWidget(avatar)
            msg_layout.addWidget(bubble)
            msg_layout.addStretch()

        bubble_layout = QVBoxLayout(bubble)
        bubble_layout.setContentsMargins(0, 0, 0, 0)
        bubble_layout.setSpacing(3)

        if not is_me:
            name_lbl = QLabel(sender)
            name_lbl.setFont(QFont("Microsoft YaHei", 8, QFont.Bold))
            name_lbl.setStyleSheet("color: #7C3AED;")
            bubble_layout.addWidget(name_lbl)

        msg_lbl = QLabel(content)
        msg_lbl.setFont(QFont("Microsoft YaHei", 10))
        msg_lbl.setStyleSheet(f"color: {'white' if is_me else '#333'};")
        msg_lbl.setWordWrap(True)
        msg_lbl.setMaximumWidth(400)
        bubble_layout.addWidget(msg_lbl)

        time_lbl = QLabel(time_str)
        time_lbl.setFont(QFont("Microsoft YaHei", 7))
        time_lbl.setStyleSheet(f"color: {'rgba(255,255,255,150)' if is_me else '#bbb'};")
        time_lbl.setAlignment(Qt.AlignRight)
        bubble_layout.addWidget(time_lbl)

        # 插入到stretch之前
        self.msg_layout.insertWidget(self.msg_layout.count() - 1, msg_frame)

    def _on_send(self):
        content = self.msg_input.toPlainText().strip()
        if not content:
            return

        bubble = self.db.get_bubble(self.bubble_id)
        if not bubble:
            return

        _, author, node_id, _, _, _, _, _, _ = bubble
        receiver = author if self.nickname != author else bubble[1]

        created_at = datetime.now().isoformat()
        self.db.save_chat_message(self.bubble_id, self.nickname, receiver, content, created_at=created_at)
        self.message_sent.emit(self.bubble_id, self.nickname, receiver, content)

        if self.network and self.network.is_online():
            try:
                self.network.send_chat_message(self.bubble_id, receiver, content, created_at)
            except Exception:
                pass

        now = created_at[5:16].replace("T", " ")
        self._add_message(self.nickname, content, now)
        self.msg_input.clear()

        # 滚动到底部
        sb = self.msg_scroll.verticalScrollBar()
        sb.setValue(sb.maximum())
