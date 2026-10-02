"""
login_page.py - 精美登录页面
梦幻渐变背景 + 发光卡片
"""
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QLabel, QLineEdit,
                             QPushButton, QFrame, QGraphicsDropShadowEffect)
from PyQt5.QtCore import Qt, pyqtSignal, QPropertyAnimation, QEasingCurve
from PyQt5.QtGui import QFont, QColor, QPainter, QLinearGradient, QBrush, QPen


class LoginPage(QWidget):
    login_success = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(1000, 700)
        self._setup_ui()

    def paintEvent(self, event):
        """绘制梦幻渐变背景"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        grad = QLinearGradient(0, 0, self.width(), self.height())
        grad.setColorAt(0, QColor(240, 230, 255))
        grad.setColorAt(0.3, QColor(255, 232, 240))
        grad.setColorAt(0.6, QColor(230, 240, 255))
        grad.setColorAt(1, QColor(240, 255, 232))
        painter.fillRect(self.rect(), grad)

        # 装饰性泡泡
        self._draw_deco_bubble(painter, 100, 80, 60, QColor(167, 139, 250, 40))
        self._draw_deco_bubble(painter, 850, 120, 80, QColor(255, 138, 138, 30))
        self._draw_deco_bubble(painter, 200, 500, 50, QColor(123, 237, 159, 35))
        self._draw_deco_bubble(painter, 750, 550, 70, QColor(255, 183, 140, 30))

    def _draw_deco_bubble(self, painter, x, y, r, color):
        painter.setBrush(color)
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(x - r, y - r, r * 2, r * 2)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)

        # 卡片
        card = QFrame()
        card.setFixedSize(420, 400)
        card.setStyleSheet("""
            QFrame {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 rgba(255,255,255,235), stop:1 rgba(248,245,255,235));
                border-radius: 30px;
                border: 1px solid rgba(200,180,255,80);
            }
        """)
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(40)
        shadow.setColor(QColor(167, 139, 250, 100))
        shadow.setOffset(0, 8)
        card.setGraphicsEffect(shadow)

        card_layout = QVBoxLayout(card)
        card_layout.setSpacing(15)
        card_layout.setContentsMargins(45, 50, 45, 45)

        # 标题
        title = QLabel("💭 情感泡泡")
        title.setFont(QFont("Microsoft YaHei", 30, QFont.Bold))
        title.setStyleSheet("color: #7C3AED;")
        title.setAlignment(Qt.AlignCenter)
        card_layout.addWidget(title)

        subtitle = QLabel("把你的心情装进泡泡里，\n总有人会看见 ✨")
        subtitle.setFont(QFont("Microsoft YaHei", 11))
        subtitle.setStyleSheet("color: #999;")
        subtitle.setAlignment(Qt.AlignCenter)
        card_layout.addWidget(subtitle)

        card_layout.addSpacing(20)

        # 昵称输入
        name_label = QLabel("给自己取个名字吧")
        name_label.setFont(QFont("Microsoft YaHei", 10))
        name_label.setStyleSheet("color: #666;")
        name_label.setAlignment(Qt.AlignCenter)
        card_layout.addWidget(name_label)

        self.nickname_input = QLineEdit()
        self.nickname_input.setPlaceholderText("输入你的昵称...")
        self.nickname_input.setFont(QFont("Microsoft YaHei", 12))
        self.nickname_input.setMaxLength(12)
        self.nickname_input.setFixedHeight(45)
        self.nickname_input.setAlignment(Qt.AlignCenter)
        self.nickname_input.setStyleSheet("""
            QLineEdit {
                border: 2px solid #DDD6FE;
                border-radius: 22px;
                padding: 5px 20px;
                background: rgba(255,255,255,200);
                color: #333;
                font-size: 15px;
            }
            QLineEdit:focus {
                border-color: #7C3AED;
                background: white;
            }
        """)
        self.nickname_input.returnPressed.connect(self._on_login)
        card_layout.addWidget(self.nickname_input)

        card_layout.addSpacing(10)

        # 进入按钮
        self.login_btn = QPushButton("🌟  进入泡泡世界")
        self.login_btn.setFont(QFont("Microsoft YaHei", 13, QFont.Bold))
        self.login_btn.setFixedHeight(48)
        self.login_btn.setCursor(Qt.PointingHandCursor)
        self.login_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #A78BFA, stop:1 #7C3AED);
                color: white;
                border: none;
                border-radius: 24px;
                font-size: 16px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #BDA3FF, stop:1 #8B5CF6);
            }
            QPushButton:pressed {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #7C3AED, stop:1 #6D28D9);
            }
        """)
        self.login_btn.clicked.connect(self._on_login)
        card_layout.addWidget(self.login_btn)

        card_layout.addStretch()
        layout.addWidget(card, alignment=Qt.AlignCenter)

    def _on_login(self):
        nickname = self.nickname_input.text().strip()
        if nickname:
            self.login_success.emit(nickname)
        else:
            self.nickname_input.setPlaceholderText("请输入昵称再进入哦～")
            self.nickname_input.setStyleSheet("""
                QLineEdit {
                    border: 2px solid #FF6B6B;
                    border-radius: 22px;
                    padding: 5px 20px;
                    background: rgba(255,255,255,200);
                    color: #333;
                }
            """)
