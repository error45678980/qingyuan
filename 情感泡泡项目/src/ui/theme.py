"""视觉令牌唯一来源（T-08）。

规则（架构文档 §11.5 + 视觉规格 §5 / §6）：
    * 6 组情感配色与图标的 key **必须**与 `core.constants.EMOTIONS` 完全一致；
    * 页面与组件不得就地写色值，一律从这里取；
    * 渐变背景只在 `widgets/common.GradientPage` 一处绘制（R-18）；
    * QSS 一律使用 `#objectName` 选择器 —— 视觉规格 §6-1 实测：`QFrame { ... }`
      会连带 QLabel（QLabel 继承 QFrame），导致每段文字背后出现白方块。
"""

from __future__ import annotations

from PyQt5.QtGui import QColor, QFont

from src.core.constants import EMOTION_FALLBACK, EMOTIONS

# ---------------------------------------------------------------------------
# 基础色板（视觉规格 §5）
# ---------------------------------------------------------------------------

PRIMARY: str = "#7C3AED"
PRIMARY_HOVER: str = "#8B5CF6"
PRIMARY_DEEP: str = "#6D28D9"
PRIMARY_SOFT: str = "#EDE9FE"
PRIMARY_BORDER: str = "#DDD6FE"

BG_TOP: str = "#FBF9FF"
BG_BOTTOM: str = "#EFEDF9"
CARD_BG: str = "#FFFFFF"
CARD_BORDER: str = "#EDEAF7"

INK: str = "#2F2B45"
INK_SOFT: str = "#6B6880"
INK_FAINT: str = "#9C98AE"

ONLINE: str = "#2ED573"
WARN_BG: str = "#FFF7E0"
WARN_BORDER: str = "#FFE08A"
WARN_INK: str = "#8A6D1F"
DANGER: str = "#E5484D"
DANGER_SOFT: str = "#FFE8E8"
SUCCESS: str = "#2ED573"

MASK_RGBA: tuple[int, int, int, int] = (24, 18, 43, 46)

# ---------------------------------------------------------------------------
# 情感配色（main, deep, light）与图标
# ---------------------------------------------------------------------------

#: `tag -> (主色, 深色, 浅色底)`；key 必须与 `constants.EMOTIONS` 完全一致。
EMOTION_COLORS: dict[str, tuple[str, str, str]] = {
    "开心": ("#FFE066", "#FFB800", "#FFF8DC"),
    "难过": ("#74B9FF", "#0984E3", "#E8F4FD"),
    "孤独": ("#C59BFF", "#8854D0", "#F3EAFF"),
    "焦虑": ("#FF8A8A", "#FF4757", "#FFE8E8"),
    "寻求建议": ("#7BED9F", "#2ED573", "#E8F8F0"),
    "找人倾诉": ("#FFB78C", "#FF6348", "#FFF0E8"),
}

#: `tag -> emoji`；与 `EMOTION_COLORS` 同 key 集合。
EMOTION_ICONS: dict[str, str] = {
    "开心": "😊",
    "难过": "😢",
    "孤独": "🥺",
    "焦虑": "😰",
    "寻求建议": "💡",
    "找人倾诉": "🗣️",
}

#: 回复模式徽标（视觉规格 §8.2：模式徽标 📝/💬）。
REPLY_MODE_ICONS: dict[str, str] = {"single": "📝", "dialog": "💬"}
REPLY_MODE_LABELS: dict[str, str] = {"single": "单次回复", "dialog": "短时对话"}

# ---------------------------------------------------------------------------
# 字体
# ---------------------------------------------------------------------------

FONT_FAMILY: str = "Microsoft YaHei"
EMOJI_FAMILY: str = "Segoe UI Emoji"


def font(size: int = 10, *, bold: bool = False) -> QFont:
    """构造正文字体（统一字体族，避免各处 `QFont("...")` 漂移）。

    Args:
        size: 磅值。
        bold: 是否加粗。

    Returns:
        `QFont`。
    """
    result = QFont(FONT_FAMILY, size)
    if bold:
        result.setBold(True)
    result.setStyleStrategy(QFont.PreferAntialias)
    return result


def emoji_font(size: int = 14) -> QFont:
    """构造 emoji 字体（视觉规格 §6-5：Win10 部分版本需 fallback 到雅黑）。"""
    result = QFont(EMOJI_FAMILY, size)
    families = [EMOJI_FAMILY, FONT_FAMILY, "Segoe UI Symbol"]
    setter = getattr(result, "setFamilies", None)
    if callable(setter):
        setter(families)
    else:  # pragma: no cover - 老版本 PyQt 无 setFamilies
        result.setFamily(EMOJI_FAMILY)
    return result


# ---------------------------------------------------------------------------
# 取色工具
# ---------------------------------------------------------------------------


def qcolor(value: str, alpha: int = 255) -> QColor:
    """把十六进制色值转 `QColor` 并可指定透明度。

    Args:
        value: `#RRGGBB`。
        alpha: 0~255。

    Returns:
        `QColor`。
    """
    color = QColor(value)
    if not color.isValid():  # 非法色值兜底为墨色，绝不产生透明黑洞
        color = QColor(INK)
    color.setAlpha(max(0, min(255, int(alpha))))
    return color


def emotion_colors(tag: str) -> tuple[str, str, str]:
    """取情感配色三元组；越界标签回退到 `EMOTION_FALLBACK`（**不用灰色**，R-01）。

    Args:
        tag: 情感标签。

    Returns:
        `(主色, 深色, 浅色底)`。
    """
    return EMOTION_COLORS.get(tag) or EMOTION_COLORS[EMOTION_FALLBACK]


def emotion_icon(tag: str) -> str:
    """取情感 emoji；越界标签回退。"""
    return EMOTION_ICONS.get(tag) or EMOTION_ICONS[EMOTION_FALLBACK]


def mix(first: QColor, second: QColor, ratio: float) -> QColor:
    """线性混色（`ratio=0` 取 first，`ratio=1` 取 second）。"""
    ratio = max(0.0, min(1.0, float(ratio)))
    return QColor(
        int(first.red() + (second.red() - first.red()) * ratio),
        int(first.green() + (second.green() - first.green()) * ratio),
        int(first.blue() + (second.blue() - first.blue()) * ratio),
        int(first.alpha() + (second.alpha() - first.alpha()) * ratio),
    )


def emotion_alpha(tag: str, alpha: int) -> QColor:
    """情感主色 + 指定透明度。"""
    return qcolor(emotion_colors(tag)[0], alpha)


# ---------------------------------------------------------------------------
# QSS（全部使用 `#objectName` 选择器）
# ---------------------------------------------------------------------------

QSS_SCROLLBAR: str = """
QScrollBar:vertical {
    width: 8px; background: transparent; margin: 2px 0 2px 0;
}
QScrollBar::handle:vertical {
    background: rgba(124, 58, 237, 60); border-radius: 4px; min-height: 40px;
}
QScrollBar::handle:vertical:hover { background: rgba(124, 58, 237, 120); }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QScrollBar:horizontal { height: 0px; }
"""

QSS_SEARCH: str = """
QLineEdit#searchInput {
    background: #FFFFFF;
    border: 1px solid #E6E1F5;
    border-radius: 17px;
    padding: 5px 14px;
    color: #2F2B45;
    font-size: 13px;
}
QLineEdit#searchInput:focus { border: 1px solid #7C3AED; }
"""

QSS_PRIMARY_BUTTON: str = """
QPushButton#primaryBtn {
    background: #7C3AED; color: #FFFFFF; border: none; border-radius: 22px;
    font-size: 14px; font-weight: bold; padding: 0 22px;
}
QPushButton#primaryBtn:hover { background: #8B5CF6; }
QPushButton#primaryBtn:pressed { background: #6D28D9; }
QPushButton#primaryBtn:disabled { background: #D9D2F0; color: #FFFFFF; }
"""

QSS_GHOST_BUTTON: str = """
QPushButton#ghostBtn {
    background: #FFFFFF; color: #6B6880; border: 1px solid #E6E1F5;
    border-radius: 20px; font-size: 13px; padding: 0 16px;
}
QPushButton#ghostBtn:hover { border: 1px solid #C4B5FD; color: #7C3AED; }
QPushButton#ghostBtn:pressed { background: #F5F3FF; }
QPushButton#ghostBtn:disabled { color: #C9C5D8; border-color: #EFECF8; }
"""

QSS_NAV_BUTTON: str = """
QPushButton#navBtn {
    background: transparent; color: #6B6880; border: none;
    border-radius: 14px; font-size: 13px; padding: 4px 12px;
}
QPushButton#navBtn:hover { color: #7C3AED; background: #F3EEFF; }
QPushButton#navBtn:checked { color: #7C3AED; font-weight: bold; background: #EDE9FE; }
"""

QSS_TEXT_EDIT: str = """
QTextEdit#composerEdit {
    background: #FFFFFF; border: 1px solid #E6E1F5; border-radius: 16px;
    padding: 12px 14px; color: #2F2B45; font-size: 14px;
}
/* QTextEdit 的 viewport 是子 QWidget，不显式透明会形成"框中框" */
QTextEdit#composerEdit > QWidget { background: transparent; }
QTextEdit#composerEdit:focus { border: 1px solid #7C3AED; }
"""

QSS_LINE_EDIT: str = """
QLineEdit#lineInput {
    background: #FFFFFF; border: 1px solid #E6E1F5; border-radius: 16px;
    padding: 8px 14px; color: #2F2B45; font-size: 13px;
}
QLineEdit#lineInput:focus { border: 1px solid #7C3AED; }
"""

QSS_CHECKBOX_HINT: str = """
QLabel#hintLabel { color: #9C98AE; font-size: 11px; background: transparent; }
QLabel#errorLabel { color: #E5484D; font-size: 12px; background: transparent; }
"""


def emotion_key_set() -> set[str]:
    """返回情感 key 集合（供测试断言与运行时校验）。"""
    return set(EMOTIONS)


__all__ = [
    "PRIMARY",
    "PRIMARY_HOVER",
    "PRIMARY_DEEP",
    "PRIMARY_SOFT",
    "PRIMARY_BORDER",
    "BG_TOP",
    "BG_BOTTOM",
    "CARD_BG",
    "CARD_BORDER",
    "INK",
    "INK_SOFT",
    "INK_FAINT",
    "ONLINE",
    "WARN_BG",
    "WARN_BORDER",
    "WARN_INK",
    "DANGER",
    "DANGER_SOFT",
    "SUCCESS",
    "MASK_RGBA",
    "EMOTION_COLORS",
    "EMOTION_ICONS",
    "REPLY_MODE_ICONS",
    "REPLY_MODE_LABELS",
    "FONT_FAMILY",
    "EMOJI_FAMILY",
    "font",
    "emoji_font",
    "qcolor",
    "emotion_colors",
    "emotion_icon",
    "emotion_alpha",
    "mix",
    "emotion_key_set",
    "QSS_SCROLLBAR",
    "QSS_SEARCH",
    "QSS_PRIMARY_BUTTON",
    "QSS_GHOST_BUTTON",
    "QSS_NAV_BUTTON",
    "QSS_TEXT_EDIT",
    "QSS_LINE_EDIT",
    "QSS_CHECKBOX_HINT",
]
