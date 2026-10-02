"""
bubble_detail.py - 泡泡详情页面
查看泡泡完整内容、回复、操作（解决/保留/回复）
"""
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                             QTextEdit, QPushButton, QFrame, QScrollArea,
                             QMessageBox, QDialog, QCheckBox, QDialogButtonBox,
                             QGraphicsDropShadowEffect)
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QFont, QColor, QPainter, QLinearGradient

from .theme import EMOTION_COLORS, EMOTION_ICONS, SCROLLBAR_STYLE
from datetime import datetime


class BubbleDetailPage(QWidget):
    bubble_resolved = pyqtSignal(str)
    bubble_saved = pyqtSignal(str, bool)
    replied = pyqtSignal(str, str, str)
    start_chat = pyqtSignal(str, str)
    go_back = pyqtSignal()

    def __init__(self, database, nickname, bubble_id, network=None):
        super().__init__()
        self.db = database
        self.nickname = nickname
        self.bubble_id = bubble_id
        self.network = network
        self.setMinimumSize(1000, 700)
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
        layout.setContentsMargins(30, 20, 30, 20)

        # 顶部
        top_layout = QHBoxLayout()
        back_btn = QPushButton("←  返回泡泡墙")
        back_btn.setFont(QFont("Microsoft YaHei", 11))
        back_btn.setCursor(Qt.PointingHandCursor)
        back_btn.setStyleSheet("""
            QPushButton {
                background: transparent; border: none;
                color: #7C3AED; padding: 8px 16px;
            }
            QPushButton:hover {
                background: rgba(167,139,250,0.1);
                border-radius: 20px;
            }
        """)
        back_btn.clicked.connect(self.go_back.emit)
        top_layout.addWidget(back_btn)
        top_layout.addStretch()
        layout.addLayout(top_layout)

        # 主内容区域
        content = QWidget()
        content.setStyleSheet("background: transparent;")
        content_layout = QVBoxLayout(content)
        content_layout.setSpacing(15)

        # ===== 泡泡卡片 =====
        self.bubble_card = QFrame()
        self.bubble_card.setStyleSheet("""
            QFrame {
                background: rgba(255,255,255,235);
                border-radius: 25px;
                border: 1px solid rgba(200,180,255,80);
            }
        """)
        card_layout = QVBoxLayout(self.bubble_card)
        card_layout.setContentsMargins(30, 25, 30, 25)
        card_layout.setSpacing(12)

        # 头部
        header = QHBoxLayout()
        self.icon_label = QLabel()
        self.icon_label.setFont(QFont("Segoe UI Emoji", 36))
        self.icon_label.setFixedSize(55, 55)
        header.addWidget(self.icon_label)

        info_col = QVBoxLayout()
        info_col.setSpacing(3)
        self.name_label = QLabel()
        self.name_label.setFont(QFont("Microsoft YaHei", 16, QFont.Bold))
        self.name_label.setStyleSheet("color: #444;")
        info_col.addWidget(self.name_label)

        self.time_label = QLabel()
        self.time_label.setFont(QFont("Microsoft YaHei", 9))
        self.time_label.setStyleSheet("color: #bbb;")
        info_col.addWidget(self.time_label)
        header.addLayout(info_col)

        header.addStretch()

        self.tag_label = QLabel()
        self.tag_label.setFont(QFont("Microsoft YaHei", 10))
        header.addWidget(self.tag_label)

        card_layout.addLayout(header)

        # 分隔线
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet("background: rgba(200,180,255,50);")
        card_layout.addWidget(line)

        # 内容
        self.content_label = QLabel()
        self.content_label.setFont(QFont("Microsoft YaHei", 12))
        self.content_label.setStyleSheet("color: #333; line-height: 1.6;")
        self.content_label.setWordWrap(True)
        card_layout.addWidget(self.content_label)

        # 模式标签
        self.mode_label = QLabel()
        self.mode_label.setFont(QFont("Microsoft YaHei", 9))
        self.mode_label.setAlignment(Qt.AlignRight)
        self.mode_label.setStyleSheet("color: #aaa;")
        card_layout.addWidget(self.mode_label)

        content_layout.addWidget(self.bubble_card)

        # ===== 操作按钮（发布者可见）=====
        self.owner_actions = QFrame()
        self.owner_actions.setStyleSheet("background: transparent;")
        owner_layout = QHBoxLayout(self.owner_actions)
        owner_layout.setSpacing(15)

        self.resolve_btn = QPushButton("🎈 已解决 (删除泡泡)")
        self.resolve_btn.setFont(QFont("Microsoft YaHei", 10))
        self.resolve_btn.setFixedHeight(42)
        self.resolve_btn.setCursor(Qt.PointingHandCursor)
        self.resolve_btn.setStyleSheet("""
            QPushButton {
                background: #FEE2E2; color: #EF4444;
                border: 1px solid #FECACA;
                border-radius: 21px; padding: 0 25px;
            }
            QPushButton:hover { background: #FECACA; }
        """)
        self.resolve_btn.clicked.connect(self._on_resolve)
        owner_layout.addWidget(self.resolve_btn)

        self.save_btn = QPushButton("💾  申请保留")
        self.save_btn.setFont(QFont("Microsoft YaHei", 10))
        self.save_btn.setFixedHeight(42)
        self.save_btn.setCursor(Qt.PointingHandCursor)
        self.save_btn.setStyleSheet("""
            QPushButton {
                background: #DCFCE7; color: #22C55E;
                border: 1px solid #BBF7D0;
                border-radius: 21px; padding: 0 25px;
            }
            QPushButton:hover { background: #BBF7D0; }
        """)
        self.save_btn.clicked.connect(self._on_save)
        owner_layout.addWidget(self.save_btn)

        owner_layout.addStretch()
        content_layout.addWidget(self.owner_actions)

        # ===== 回复区域 =====
        self.reply_section = QFrame()
        self.reply_section.setStyleSheet("background: transparent;")
        reply_layout = QVBoxLayout(self.reply_section)
        reply_layout.setSpacing(10)

        # 回复列表标题
        replies_header = QHBoxLayout()
        replies_icon = QLabel("💬")
        replies_icon.setFont(QFont("Segoe UI Emoji", 14))
        replies_header.addWidget(replies_icon)
        self.replies_title = QLabel("回复")
        self.replies_title.setFont(QFont("Microsoft YaHei", 13, QFont.Bold))
        self.replies_title.setStyleSheet("color: #555;")
        replies_header.addWidget(self.replies_title)
        replies_header.addStretch()
        reply_layout.addLayout(replies_header)

        # 回复列表
        self.replies_scroll = QScrollArea()
        self.replies_scroll.setWidgetResizable(True)
        self.replies_scroll.setFixedHeight(180)
        self.replies_scroll.setStyleSheet(f"""
            QScrollArea {{
                border: 1px solid #EDE9FE;
                border-radius: 16px;
                background: rgba(255,255,255,150);
            }}
            {SCROLLBAR_STYLE}
        """)
        self.replies_container = QWidget()
        self.replies_container.setStyleSheet("background: transparent;")
        self.replies_list_layout = QVBoxLayout(self.replies_container)
        self.replies_list_layout.setSpacing(6)
        self.replies_list_layout.setContentsMargins(10, 10, 10, 10)
        self.replies_list_layout.addStretch()
        self.replies_scroll.setWidget(self.replies_container)
        reply_layout.addWidget(self.replies_scroll)

        # 输入区域
        input_cont = QFrame()
        input_cont.setStyleSheet("""
            background: rgba(255,255,255,200);
            border: 1px solid #EDE9FE;
            border-radius: 16px;
        """)
        input_layout = QHBoxLayout(input_cont)
        input_layout.setContentsMargins(10, 8, 10, 8)

        self.reply_input = QTextEdit()
        self.reply_input.setPlaceholderText("写下你的回复...")
        self.reply_input.setFont(QFont("Microsoft YaHei", 10))
        self.reply_input.setFixedHeight(55)
        self.reply_input.setStyleSheet("""
            QTextEdit {
                border: 2px solid #EDE9FE;
                border-radius: 14px;
                padding: 8px 15px;
                background: white;
                color: #333;
            }
            QTextEdit:focus { border-color: #A78BFA; }
        """)
        input_layout.addWidget(self.reply_input)

        self.send_btn = QPushButton("发送 ✨")
        self.send_btn.setFont(QFont("Microsoft YaHei", 11, QFont.Bold))
        self.send_btn.setFixedSize(90, 55)
        self.send_btn.setCursor(Qt.PointingHandCursor)
        self.send_btn.setStyleSheet("""
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
        self.send_btn.clicked.connect(self._on_send_reply)
        input_layout.addWidget(self.send_btn)
        reply_layout.addWidget(input_cont)

        content_layout.addWidget(self.reply_section)
        content_layout.addStretch()

        layout.addWidget(content)

    def _load_data(self):
        if self.network and self.network.is_online():
            try:
                details = self.network.fetch_bubble_details(self.bubble_id, viewer=self.nickname)
                if details and details.get("id"):
                    self.db.save_bubble(
                        details["id"],
                        details["author"],
                        details["content"],
                        details["emotion_tag"],
                        details["reply_mode"],
                        status=details.get("status", "active"),
                        created_at=details.get("created_at")
                    )
                    for reply in details.get("replies", []):
                        self.db.save_reply(
                            self.bubble_id,
                            reply["replier"],
                            reply["content"],
                            created_at=reply.get("created_at")
                        )
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

        _, author, _, content, emotion, reply_mode, status, created_at, _ = bubble

        self.icon_label.setText(EMOTION_ICONS.get(emotion, "💭"))
        self.name_label.setText(author)
        self.content_label.setText(content)
        time_str = created_at[:16].replace("T", " ")
        self.time_label.setText(f"发布于 {time_str}")

        colors = EMOTION_COLORS.get(emotion, ("#ccc", "#999", "#fff"))
        self.tag_label.setText(f"#{emotion}")
        self.tag_label.setStyleSheet(f"color: {colors[1]}; font-weight: bold;")

        mode = "📝 单次回复" if reply_mode == "single" else "💬 短时对话框"
        self.mode_label.setText(mode)

        is_owner = (author == self.nickname)
        self.owner_actions.setVisible(is_owner)
        self.reply_section.setVisible(not is_owner)

        self._load_replies()

    def _load_replies(self):
        while self.replies_list_layout.count():
            item = self.replies_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        replies = self.db.get_replies(self.bubble_id)
        messages = self.db.get_chat_messages(self.bubble_id)

        if not replies and not messages:
            empty = QLabel("还没有回复... 来写下第一句温暖的话吧 💕")
            empty.setFont(QFont("Microsoft YaHei", 10))
            empty.setStyleSheet("color: #bbb; padding: 20px;")
            empty.setAlignment(Qt.AlignCenter)
            self.replies_list_layout.addWidget(empty)
            self.replies_list_layout.addStretch()
            return

        for r in replies:
            _, _, replier, content, created_at = r
            self._add_reply_item(replier, content, created_at[5:16].replace("T", " "))

        for m in messages:
            _, _, sender, receiver, content, created_at = m
            if sender == self.nickname or receiver == self.nickname:
                self._add_reply_item(sender, f"→ {receiver}: {content}", created_at[5:16].replace("T", " "))

        self.replies_list_layout.addStretch()

    def _add_reply_item(self, name, content, time_str):
        item = QFrame()
        item.setStyleSheet("""
            background: rgba(248,245,255,120);
            border-radius: 12px;
            border: 1px solid rgba(237,233,254,100);
        """)
        item_layout = QHBoxLayout(item)
        item_layout.setContentsMargins(12, 6, 12, 6)

        name_lbl = QLabel(f"👤 {name}")
        name_lbl.setFont(QFont("Microsoft YaHei", 9, QFont.Bold))
        name_lbl.setStyleSheet("color: #7C3AED;")
        name_lbl.setFixedWidth(100)
        item_layout.addWidget(name_lbl)

        content_lbl = QLabel(content)
        content_lbl.setFont(QFont("Microsoft YaHei", 9))
        content_lbl.setStyleSheet("color: #555;")
        content_lbl.setWordWrap(True)
        item_layout.addWidget(content_lbl, 1)

        time_lbl = QLabel(time_str)
        time_lbl.setFont(QFont("Microsoft YaHei", 8))
        time_lbl.setStyleSheet("color: #ccc;")
        time_lbl.setFixedWidth(80)
        time_lbl.setAlignment(Qt.AlignRight)
        item_layout.addWidget(time_lbl)

        self.replies_list_layout.insertWidget(self.replies_list_layout.count(), item)

    def _on_send_reply(self):
        content = self.reply_input.toPlainText().strip()
        if not content:
            return

        bubble = self.db.get_bubble(self.bubble_id)
        if not bubble:
            return

        _, author, node_id, _, _, reply_mode, _, _, _ = bubble

        if reply_mode == "single":
            if self.db.has_replied(self.bubble_id, self.nickname):
                QMessageBox.information(self, "提示", "这个泡泡只能回复一次哦～")
                return
            created_at = datetime.now().isoformat()
            self.db.save_reply(self.bubble_id, self.nickname, content, created_at=created_at)
            self.replied.emit(self.bubble_id, self.nickname, content)

            if self.network and self.network.is_online():
                try:
                    self.network.reply_to_bubble(self.bubble_id, content)
                except Exception:
                    pass
        else:
            created_at = datetime.now().isoformat()
            self.db.save_chat_message(self.bubble_id, self.nickname, author, content, created_at=created_at)
            if self.network and self.network.is_online():
                try:
                    self.network.send_chat_message(self.bubble_id, author, content, created_at)
                except Exception:
                    pass
            self.start_chat.emit(self.bubble_id, self.nickname)

        self.reply_input.clear()
        self._load_replies()

    def _on_resolve(self):
        reply = QMessageBox.question(self, "确认",
            "确定这个情感需求已经解决了吗？\n泡泡将会炸掉消失 💥",
            QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.db.resolve_bubble(self.bubble_id)
            if self.network and self.network.is_online():
                try:
                    self.network.resolve_bubble(self.bubble_id)
                except Exception:
                    pass
            self.bubble_resolved.emit(self.bubble_id)

    def _on_save(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("保留泡泡")
        dialog.setFixedSize(380, 250)
        dialog.setStyleSheet("""
            QDialog {
                background: white;
                border-radius: 25px;
            }
        """)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(30, 25, 30, 25)
        layout.setSpacing(15)

        label = QLabel("这段对话对你来说很有价值吗？\n选择保留方式 💾")
        label.setFont(QFont("Microsoft YaHei", 11))
        label.setStyleSheet("color: #333;")
        layout.addWidget(label)

        public_cb = QCheckBox("🔓 公开保留（其他人也能看到）")
        public_cb.setFont(QFont("Microsoft YaHei", 10))
        public_cb.setStyleSheet("color: #555;")
        layout.addWidget(public_cb)

        private_cb = QCheckBox("🔒 私密保存（仅自己可见）")
        private_cb.setFont(QFont("Microsoft YaHei", 10))
        private_cb.setStyleSheet("color: #555;")
        layout.addWidget(private_cb)

        layout.addStretch()
        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.accepted.connect(dialog.accept)
        btn_box.rejected.connect(dialog.reject)
        layout.addWidget(btn_box)

        if dialog.exec_() == QDialog.Accepted:
            is_public = public_cb.isChecked()
            self.db.save_bubble_as(self.bubble_id, is_public)
            if self.network and self.network.is_online():
                try:
                    self.network.save_bubble(self.bubble_id, is_public)
                except Exception:
                    pass
            self.bubble_saved.emit(self.bubble_id, is_public)
