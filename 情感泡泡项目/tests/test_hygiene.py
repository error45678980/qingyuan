"""T-12 验收：UI 层工程卫生（R-03 / R-16 / R-17，架构 §11.6）。

覆盖的硬规则：
    1. `src/ui/**` 不得 import requests（UI 线程零网络的结构性保障）；
    2. `QTimer` 只允许出现在 `animation.py` / `net_worker.py` / `toast.py`；
       `bubble_view.py` 里出现 QTimer 即为"每个泡泡一个定时器"的复现；
    3. `theme.EMOTION_COLORS` / `EMOTION_ICONS` 的 key 与 `constants.EMOTIONS` 完全一致；
    4. 无裸 `except:`、无 `except Exception: pass`；
    5. 无 `QFrame { ... }` 选择器（会让 QLabel 出现白方块，视觉规格 §6-1）；
    6. 无 BOM、无 `??????` 乱码；
    7. `ChatDrawer` 的 `peer` 必须是**必填参数**（R-07）；
    8. UI 层不写 SQL（禁止 UI 直接查库写库）。
"""

from __future__ import annotations

import ast
import inspect
import unittest
from pathlib import Path

from tests.qt_env import app  # noqa: F401 - 统一 offscreen 环境

from src.core.constants import EMOTIONS

PROJECT_ROOT = Path(__file__).resolve().parent.parent
UI_DIR = PROJECT_ROOT / "src" / "ui"

#: 允许出现 QTimer 的文件（全局动画 / 网络宿主 / 提示自动消失）。
QTimer_ALLOWED = {"animation.py", "net_worker.py", "toast.py"}


def _ui_files() -> list[Path]:
    """`src/ui/**` 下的全部 Python 文件。"""
    return sorted(
        path for path in UI_DIR.rglob("*.py") if "__pycache__" not in path.parts
    )


def _rel(path: Path) -> str:
    """相对项目根的路径（正斜杠）。"""
    return str(path.relative_to(PROJECT_ROOT)).replace("\\", "/")


def _imports(path: Path) -> set[str]:
    """顶层 import 的模块名。"""
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def _code_strings(path: Path) -> list[str]:
    """文件里**非 docstring** 的字符串字面量（注释/文档里解释坑位不算违规）。"""
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


class UiHygieneTest(unittest.TestCase):
    """UI 层静态检查。"""

    def test_ui_has_no_requests(self) -> None:
        """UI 线程零网络（R-03）。"""
        offenders = [_rel(path) for path in _ui_files() if "requests" in _imports(path)]
        self.assertEqual(offenders, [], "src/ui 不得 import requests")

    def test_ui_has_no_flask(self) -> None:
        """UI 不碰服务端框架。"""
        offenders = [_rel(path) for path in _ui_files() if "flask" in _imports(path)]
        self.assertEqual(offenders, [])

    def test_q_timer_only_in_allowed_files(self) -> None:
        """定时器只允许出现在白名单文件里（规则 A-1）。"""
        offenders: list[str] = []
        for path in _ui_files():
            if path.name in QTimer_ALLOWED:
                continue
            text = path.read_text(encoding="utf-8")
            if "QTimer(" in text or "QTimer.singleShot" in text:
                offenders.append(_rel(path))
        self.assertEqual(offenders, [], f"QTimer 只允许出现在 {sorted(QTimer_ALLOWED)}")

    def test_bubble_view_has_no_timer(self) -> None:
        """单个泡泡不得自建定时器（v1 卡顿的根因）。"""
        text = (UI_DIR / "widgets" / "bubble_view.py").read_text(encoding="utf-8")
        self.assertNotIn("QTimer(", text, "BubbleView 不得实例化 QTimer")
        self.assertNotIn("QTimer", _imports(UI_DIR / "widgets" / "bubble_view.py"))
        self.assertIn("QPropertyAnimation", text, "泡泡动画应使用属性动画，不用定时器")

    def test_emotion_tokens_cover_all_emotions(self) -> None:
        """情感配色/图标 key 与 `EMOTIONS` 完全一致（R-01 无灰色 fallback）。"""
        from src.ui import theme

        expected = set(EMOTIONS)
        self.assertEqual(set(theme.EMOTION_COLORS), expected)
        self.assertEqual(set(theme.EMOTION_ICONS), expected)

    def test_no_bare_except_in_ui(self) -> None:
        """禁止裸 `except:`（R-16）。"""
        offenders: list[str] = []
        for path in _ui_files():
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ExceptHandler) and node.type is None:
                    offenders.append(f"{_rel(path)}:{node.lineno}")
        self.assertEqual(offenders, [])

    def test_no_swallowed_exception(self) -> None:
        """禁止 `except Exception: pass`（R-16）。"""
        offenders: list[str] = []
        for path in _ui_files():
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ExceptHandler):
                    continue
                body = node.body
                if len(body) == 1 and isinstance(body[0], ast.Pass):
                    offenders.append(f"{_rel(path)}:{node.lineno}")
        self.assertEqual(offenders, [])

    def test_no_qframe_selector(self) -> None:
        """禁止 `QFrame { ... }` 选择器（QLabel 会跟着变白块，视觉规格 §6-1）。"""
        offenders: list[str] = []
        for path in _ui_files():
            for literal in _code_strings(path):
                if "QFrame {" in literal or "QFrame{" in literal:
                    offenders.append(_rel(path))
                    break
        self.assertEqual(offenders, [])

    def test_no_utf8_bom_and_no_garbled_text(self) -> None:
        """无 BOM、无乱码（C-4）。"""
        offenders: list[str] = []
        for path in _ui_files():
            raw = path.read_bytes()
            if raw.startswith(b"\xef\xbb\xbf"):
                offenders.append(f"{_rel(path)} (BOM)")
                continue
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                offenders.append(f"{_rel(path)} (非 UTF-8)")
                continue
            if "??????" in text:
                offenders.append(f"{_rel(path)} (乱码)")
        self.assertEqual(offenders, [])

    def test_ui_has_no_sql(self) -> None:
        """UI 层不写 SQL：读写一律经过 core 层。"""
        offenders: list[str] = []
        for path in _ui_files():
            text = path.read_text(encoding="utf-8")
            if "SELECT " in text.upper() or "INSERT INTO" in text.upper():
                offenders.append(_rel(path))
        self.assertEqual(offenders, [])

    def test_chat_drawer_requires_peer(self) -> None:
        """`ChatDrawer` 的 `peer` 必须是必填参数（R-07）。"""
        from src.ui.pages.chat_drawer import ChatDrawer

        signature = inspect.signature(ChatDrawer.__init__)
        self.assertIn("peer", signature.parameters)
        self.assertIs(
            signature.parameters["peer"].default,
            inspect.Parameter.empty,
            "peer 必须由调用方传入，不得有默认值",
        )

    def test_detail_drawer_prefers_server_permissions(self) -> None:
        """详情抽屉优先使用服务端下发的权限标志。"""
        text = (UI_DIR / "pages" / "detail_drawer.py").read_text(encoding="utf-8")
        self.assertIn("perms_known", text)
        self.assertIn("bubble.can_reply", text)

    def test_single_animator_module_owns_the_timer(self) -> None:
        """`animation.py` 只有一个 QTimer 实例（单定时器驱动全部泡泡）。"""
        text = (UI_DIR / "widgets" / "animation.py").read_text(encoding="utf-8")
        self.assertEqual(text.count("QTimer("), 1)

    def test_theme_has_no_gray_fallback(self) -> None:
        """情感配色回退不得是灰色（R-01：出现灰色 fallback 的次数必须为 0）。"""
        text = (UI_DIR / "theme.py").read_text(encoding="utf-8")
        self.assertNotIn("#999999", text)
        self.assertNotIn("#999", text)
        self.assertIn("EMOTION_COLORS[EMOTION_FALLBACK]", text)


if __name__ == "__main__":
    unittest.main()
