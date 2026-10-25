"""顶栏（T-08）：品牌 + 导航 + 昵称 + 在线状态 + 切换。

被 `WallPage` / `MyPage` 复用；只负责展示与发信号，不含任何业务逻辑。
"""

from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import QRectF, Qt, pyqtSignal
from PyQt5.QtGui import QPainter, QPen
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from src.ui import theme
from src.ui.widgets.common import Avatar, GhostButton, NavButton, make_label


class TopBar(QWidget):
    """窗口顶栏。"""

    wall_clicked = pyqtSignal()
    mine_clicked = pyqtSignal()
    switch_user_clicked = pyqtSignal()

    HEIGHT: int = 58

    def __init__(self, nickname: str = "", parent: Optional[QWidget] = None) -> None:
        """构造。

        Args:
            nickname: 当前昵称。
            parent: 父控件。
        """
        super().__init__(parent)
        self.setFixedHeight(self.HEIGHT)
        self._online = True

        layout = QHBoxLayout(self)
        layout.setContentsMargins(24, 0, 24, 0)
        layout.setSpacing(10)

        brand = QLabel("💭 情缘")
        brand.setFont(theme.font(13, bold=True))
        brand.setStyleSheet(f"QLabel {{ color: {theme.PRIMARY}; background: transparent; }}")
        layout.addWidget(brand)
        layout.addStretch(1)

        self._wall_btn = NavButton("泡泡墙")
        self._wall_btn.setChecked(True)
        self._wall_btn.clicked.connect(lambda: self.wall_clicked.emit())
        layout.addWidget(self._wall_btn)

        self._mine_btn = NavButton("我的")
        self._mine_btn.clicked.connect(lambda: self.mine_clicked.emit())
        layout.addWidget(self._mine_btn)

        self._pending = make_label("", size=9, color=theme.WARN_INK)
        layout.addWidget(self._pending)
        self._pending.setVisible(False)

        self._avatar = Avatar(nickname, size=26)
        layout.addWidget(self._avatar)

        self._nickname = make_label(nickname or "未登录", size=10, color=theme.INK)
        layout.addWidget(self._nickname)

        self._status = make_label("🟢 在线", size=9, color=theme.INK_SOFT)
        layout.addWidget(self._status)

        self._switch = GhostButton("切换", height=28)
        self._switch.setFixedWidth(64)
        self._switch.clicked.connect(lambda: self.switch_user_clicked.emit())
        layout.addWidget(self._switch)

    # ------------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------------
    def set_nickname(self, nickname: str) -> None:
        """更新昵称。"""
        self._nickname.setText(nickname or "未登录")
        self._avatar.set_nickname(nickname)

    def set_online(self, online: bool) -> None:
        """更新在线状态（R-03 的顶栏提示）。"""
        self._online = bool(online)
        if self._online:
            self._status.setText("🟢 在线")
            self._status.setStyleSheet(
                f"QLabel {{ color: {theme.INK_SOFT}; background: transparent; }}"
            )
        else:
            self._status.setText("⚠️ 离线")
            self._status.setStyleSheet(
                f"QLabel {{ color: {theme.WARN_INK}; background: transparent; }}"
            )

    def set_pending(self, count: int) -> None:
        """更新待同步条数。"""
        if count > 0:
            self._pending.setText(f"⏳ {count} 条待同步")
            self._pending.setVisible(True)
        else:
            self._pending.setVisible(False)

    def set_active(self, page: str) -> None:
        """高亮当前页（`wall` / `mine`）。"""
        self._wall_btn.setChecked(page == "wall")
        self._mine_btn.setChecked(page == "mine")

    def is_online(self) -> bool:
        """当前在线状态。"""
        return self._online

    # ------------------------------------------------------------------
    # 绘制
    # ------------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """绘制半透明白底 + 底部 1px 分隔线。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillRect(self.rect(), theme.qcolor("#FFFFFF", 216))
        painter.setPen(QPen(theme.qcolor("#E9E5F5"), 1))
        painter.drawLine(0, self.height() - 1, self.width(), self.height() - 1)
        painter.end()


def nav_separator(parent: Optional[QWidget] = None) -> QWidget:
    """顶栏内的竖直细线（保留给后续扩展，避免样式散落）。"""
    line = QWidget(parent)
    line.setFixedSize(1, 18)
    line.setStyleSheet("QWidget { background: #E9E5F5; }")
    return line


__all__ = ["TopBar", "nav_separator"]
