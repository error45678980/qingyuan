"""
main_window.py - 泡泡墙主界面
显示所有活跃的泡泡，带情感筛选
"""
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                             QPushButton, QScrollArea, QFrame, QComboBox,
                             QGraphicsDropShadowEffect, QGridLayout)
from PyQt5.QtCore import Qt, pyqtSignal, QTimer
from PyQt5.QtGui import QFont, QColor, QPainter, QLinearGradient

from .theme import EMOTION_COLORS, EMOTION_ICONS, SCROLLBAR_STYLE
from .bubble_widget import BubbleCard


class MainWindow(QWidget):
    show_publish = pyqtSignal()
    show_bubble_detail = pyqtSignal(str)
    switch_user = pyqtSignal()

    def __init__(self, database, nickname, network=None):
        super().__init__()
        self.db = database
        self.nickname = nickname
        self.network = network
        self.current_filter = "全部"
        self.setMinimumSize(1000, 700)
        self._setup_ui()
        self._load_bubbles()

        self.timer = QTimer()
        self.timer.timeout.connect(self._load_bubbles)
        self.timer.start(3000)

    def paintEvent(self, event):
        painter = QPainter(self)
        grad = QLinearGradient(0, 0, self.width(), self.height())
        grad.setColorAt(0, QColor(240, 230, 255))
        grad.setColorAt(0.3, QColor(255, 232, 240))
        grad.setColorAt(0.6, QColor(230, 240, 255))
        grad.setColorAt(1, QColor(240, 255, 232))
        painter.fillRect(self.rect(), grad)

    def _setup_ui(self):
        main = QVBoxLayout(self)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)

        # ===== 顶栏 =====
        top = QFrame()
        top.setFixedHeight(65)
        top.setStyleSheet("background: rgba(255,255,255,200); border-bottom: 1px solid rgba(200,180,255,60);")
        tl = QHBoxLayout(top)
        tl.setContentsMargins(25, 10, 25, 10)

        ti = QLabel("💭 情感泡泡")
        ti.setFont(QFont("Microsoft YaHei", 20, QFont.Bold))
        ti.setStyleSheet("color: #7C3AED;")
        tl.addWidget(ti)

        tl.addStretch()

        self.status_label = QLabel("🟢 在线")
        self.status_label.setFont(QFont("Microsoft YaHei", 9))
        self.status_label.setStyleSheet("color: #22C55E; padding: 0 10px;")
        tl.addWidget(self.status_label)

        ui = QLabel(f"👤 {self.nickname}")
        ui.setFont(QFont("Microsoft YaHei", 10))
        ui.setStyleSheet("color: #666; padding: 0 10px;")
        tl.addWidget(ui)

        sw = QPushButton("切换")
        sw.setFont(QFont("Microsoft YaHei", 9))
        sw.setFixedSize(55, 28)
        sw.setCursor(Qt.PointingHandCursor)
        sw.setStyleSheet("""
            QPushButton {
                background: transparent; border: 1px solid #DDD6FE;
                border-radius: 14px; color: #7C3AED;
            }
            QPushButton:hover { background: rgba(167,139,250,0.1); }
        """)
        sw.clicked.connect(self.switch_user.emit)
        tl.addWidget(sw)
        main.addWidget(top)

        # ===== 筛选 =====
        fb = QFrame()
        fb.setFixedHeight(55)
        fb.setStyleSheet("background: rgba(255,255,255,140);")
        fl = QHBoxLayout(fb)
        fl.setContentsMargins(25, 5, 25, 5)

        fi = QLabel("🔍")
        fi.setFont(QFont("Segoe UI Emoji", 14))
        fl.addWidget(fi)

        self.combo = QComboBox()
        for e in ["全部", "开心", "难过", "孤独", "焦虑", "寻求建议", "找人倾诉"]:
            ic = EMOTION_ICONS.get(e, "")
            self.combo.addItem(f"{ic} {e}" if ic else e)
        self.combo.setFont(QFont("Microsoft YaHei", 10))
        self.combo.setStyleSheet("""
            QComboBox {
                border: 2px solid #DDD6FE; border-radius: 18px;
                padding: 4px 18px; min-width: 160px;
                background: white; color: #444;
            }
            QComboBox:hover { border-color: #A78BFA; }
            QComboBox::drop-down { border: none; width: 30px; }
            QComboBox QAbstractItemView {
                border: 1px solid #DDD6FE; border-radius: 12px;
                selection-background-color: #EDE9FE; color: #444; padding: 4px;
            }
        """)
        self.combo.currentIndexChanged.connect(self._on_filter)
        fl.addWidget(self.combo)
        fl.addStretch()

        self.cnt = QLabel("")
        self.cnt.setFont(QFont("Microsoft YaHei", 10))
        self.cnt.setStyleSheet("color: #999;")
        fl.addWidget(self.cnt)
        main.addWidget(fb)

        # ===== 泡泡网格 =====
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet(f"QScrollArea {{ border: none; background: transparent; }} {SCROLLBAR_STYLE}")

        self.grid_widget = QWidget()
        self.grid_widget.setStyleSheet("background: transparent;")
        self.grid = QGridLayout(self.grid_widget)
        self.grid.setSpacing(25)
        self.grid.setContentsMargins(30, 20, 30, 20)
        self.grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        scroll.setWidget(self.grid_widget)
        main.addWidget(scroll, 1)

        # ===== 底部发布按钮 =====
        bb = QFrame()
        bb.setFixedHeight(80)
        bb.setStyleSheet("background: transparent;")
        bl = QHBoxLayout(bb)
        bl.setContentsMargins(0, 0, 35, 15)
        bl.addStretch()

        pb = QPushButton("＋")
        pb.setFont(QFont("Arial", 32, QFont.Bold))
        pb.setFixedSize(60, 60)
        pb.setCursor(Qt.PointingHandCursor)
        pb.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #A78BFA, stop:1 #7C3AED);
                color: white; border: none; border-radius: 30px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #BDA3FF, stop:1 #8B5CF6);
            }
        """)
        sh = QGraphicsDropShadowEffect()
        sh.setBlurRadius(20)
        sh.setColor(QColor(124, 58, 237, 120))
        sh.setOffset(0, 4)
        pb.setGraphicsEffect(sh)
        pb.clicked.connect(self.show_publish.emit)
        bl.addWidget(pb)
        main.addWidget(bb)

    def _load_bubbles(self):
        """重新加载泡泡网格"""
        # 清除所有
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
 
        if self.network and self.network.is_online():
            try:
                fetched = self.network.fetch_bubbles(self.current_filter)
                for bubble in fetched:
                    self.db.save_bubble(
                        bubble["id"], bubble["author"], bubble["content"],
                        bubble["emotion_tag"], bubble["reply_mode"],
                        status=bubble.get("status", "active"),
                        created_at=bubble.get("created_at")
                    )
            except Exception:
                pass
 
        if self.current_filter == "全部":
            bubbles = self.db.get_all_active_bubbles()
        else:
            bubbles = self.db.get_bubbles_by_emotion(self.current_filter)

        status = "🟢 在线" if self.network and self.network.is_online() else "⚠️ 离线"
        if self.network:
            self.status_label.setText(status)
        self.cnt.setText(f"✨ 共 {len(bubbles)} 个泡泡")

        if not bubbles:
            empty = QLabel("💫 还没有泡泡哦~\n点击右下角的 ＋ 发布你的第一个心情吧！")
            empty.setFont(QFont("Microsoft YaHei", 14))
            empty.setStyleSheet("color: #bbb;")
            empty.setAlignment(Qt.AlignCenter)
            empty.setFixedHeight(300)
            self.grid.addWidget(empty, 0, 0, 1, 2, Qt.AlignCenter)
            return

        cols = 2
        for i, b in enumerate(bubbles):
            row, col = divmod(i, cols)
            card = BubbleCard(b)
            bid = b[0]  # bubble_id
            card.clicked.connect(lambda bid=bid: self.show_bubble_detail.emit(bid))
            self.grid.addWidget(card, row, col, Qt.AlignCenter)

    def _on_filter(self, idx):
        emotions = ["全部", "开心", "难过", "孤独", "焦虑", "寻求建议", "找人倾诉"]
        self.current_filter = emotions[idx]
        self._load_bubbles()

    def refresh(self):
        self._load_bubbles()
