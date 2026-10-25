"""登录页（T-08）：输入昵称即进入（C-5，不做账号密码）。

视觉要点：
    * 背景由 `GradientPage` 统一绘制（R-18），本页不重写 `paintEvent`；
    * 卡片用自绘 `Card`（`border-radius` 对 QWidget 无效，视觉规格 §6-2）；
    * 输入框/按钮 QSS 一律 `#objectName` 选择器（避免白方块，视觉规格 §6-1）。
"""

from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget

from src.core.constants import APP_SUBTITLE, APP_TITLE, MAX_NICKNAME_LEN
from src.ui import theme
from src.ui.widgets.common import Card, GradientPage, PrimaryButton, make_label

#: 本页文案（架构 §11.1：文案集中在类顶部）。
TEXTS: dict[str, str] = {
    "title": f"💭 {APP_TITLE}",
    "subtitle": APP_SUBTITLE,
    "name_label": "给自己取个名字吧",
    "placeholder": "输入你的昵称…",
    "button": "🌟  进入泡泡世界",
    "empty": "先起个名字再进去吧～",
}


class LoginPage(GradientPage):
    """昵称登录页。"""

    login_success = pyqtSignal(str)

    def __init__(self, nickname: str = "", parent: Optional[QWidget] = None) -> None:
        """构造。

        Args:
            nickname: 上次使用的昵称（预填，减少重复输入）。
            parent: 父控件。
        """
        super().__init__(parent)
        self._build_ui(nickname)

    # ------------------------------------------------------------------
    # 构建
    # ------------------------------------------------------------------
    def _build_ui(self, nickname: str) -> None:
        """搭建界面。"""
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setAlignment(Qt.AlignCenter)

        card = Card(radius=30)
        card.setFixedSize(440, 420)
        outer.addWidget(card, alignment=Qt.AlignCenter)

        inner = QVBoxLayout(card)
        inner.setContentsMargins(48, 44, 48, 40)
        inner.setSpacing(12)

        title = make_label(TEXTS["title"], size=22, color=theme.PRIMARY, bold=True)
        title.setAlignment(Qt.AlignCenter)
        inner.addWidget(title)

        subtitle = make_label(TEXTS["subtitle"], size=10, color=theme.INK_FAINT, wrap=True)
        subtitle.setAlignment(Qt.AlignCenter)
        inner.addWidget(subtitle)

        inner.addSpacing(18)

        name_label = make_label(TEXTS["name_label"], size=10, color=theme.INK_SOFT)
        name_label.setAlignment(Qt.AlignCenter)
        inner.addWidget(name_label)

        self.nickname_input = QLineEdit()
        self.nickname_input.setObjectName("lineInput")
        self.nickname_input.setStyleSheet(theme.QSS_LINE_EDIT)
        self.nickname_input.setPlaceholderText(TEXTS["placeholder"])
        self.nickname_input.setMaxLength(MAX_NICKNAME_LEN)
        self.nickname_input.setFont(theme.font(12))
        self.nickname_input.setFixedHeight(44)
        self.nickname_input.setAlignment(Qt.AlignCenter)
        self.nickname_input.setText(nickname)
        self.nickname_input.returnPressed.connect(self._on_submit)
        self.nickname_input.textChanged.connect(self._clear_error)
        inner.addWidget(self.nickname_input)

        self._error = make_label("", size=9, color=theme.DANGER)
        self._error.setAlignment(Qt.AlignCenter)
        inner.addWidget(self._error)

        self.login_btn = PrimaryButton(TEXTS["button"], height=46)
        self.login_btn.clicked.connect(self._on_submit)
        inner.addWidget(self.login_btn)

        inner.addStretch(1)

        hint = make_label(
            "输入昵称即可开始，不需要密码 · 换昵称请点右上角「切换」",
            size=8,
            color=theme.INK_FAINT,
            wrap=True,
        )
        hint.setAlignment(Qt.AlignCenter)
        inner.addWidget(hint)

        self._nickname = nickname

    # ------------------------------------------------------------------
    # 交互
    # ------------------------------------------------------------------
    def focus_input(self) -> None:
        """聚焦昵称输入框。"""
        self.nickname_input.setFocus(Qt.OtherFocusReason)
        self.nickname_input.selectAll()

    def _clear_error(self) -> None:
        """输入变化时清掉错误提示。"""
        if self._error.text():
            self._error.setText("")
            self.nickname_input.setStyleSheet(theme.QSS_LINE_EDIT)

    def _on_submit(self) -> None:
        """提交昵称。"""
        nickname = self.nickname_input.text().strip()
        if not nickname:
            self._error.setText(TEXTS["empty"])
            self.nickname_input.setStyleSheet(
                theme.QSS_LINE_EDIT
                + "QLineEdit#lineInput { border: 1px solid #E5484D; }"
            )
            self.nickname_input.setFocus(Qt.OtherFocusReason)
            return
        self._nickname = nickname
        self.login_success.emit(nickname)

    def current_nickname(self) -> str:
        """当前输入/最近一次提交的昵称。"""
        return self._nickname


class LoginShell(QWidget):
    """给登录页套一层居中容器（供离屏截图直接使用）。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        """构造。"""
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(LoginPage())


__all__ = ["LoginPage", "LoginShell", "TEXTS"]
