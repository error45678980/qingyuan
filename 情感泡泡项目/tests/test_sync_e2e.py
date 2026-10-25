"""T-07 验收：端到端同步（真 HTTP 路径 + 真实 ApiClient）与静态铁律检查。"""

from __future__ import annotations

import ast
import os
import unittest
import uuid
from pathlib import Path

from src.core.api import ApiClient
from src.core.config import AppConfig
from src.core.migrations import ensure_client_schema
from src.core.outbox import Outbox
from src.core.repo import Repo
from src.core.sync import SyncEngine
from src.core.timeutil import now_iso, shift_seconds
from src.server_app import create_app
from src.server_store import ServerStore, ensure_server_schema
from tests.helpers import FlaskTransport, temp_workspace


def _make_repo(workspace: str, name: str = "client.db") -> tuple[Repo, str]:
    """建一个 v2 客户端库并返回 `(repo, db_path)`。"""
    db_path = os.path.join(workspace, name)
    ensure_client_schema(db_path, os.path.join(workspace, "backup"))
    return Repo(db_path), db_path


class EndToEndSyncTest(unittest.TestCase):
    """真 HTTP 路径（Flask test_client）+ 真实 ApiClient：幂等与冲突。"""

    def setUp(self) -> None:
        self._ctx = temp_workspace()
        self.workspace = self._ctx.__enter__()
        self.server_db = os.path.join(self.workspace, "server.db")
        ensure_server_schema(self.server_db, os.path.join(self.workspace, "backup"))
        self.store = ServerStore(self.server_db)
        self.app = create_app(
            db_path=self.server_db,
            store=self.store,
            log_path="",
            rate_limit=100000,
        )
        self.transport = FlaskTransport(self.app)
        self.repo, _ = _make_repo(self.workspace, "client_a.db")
        self.outbox = Outbox(self.repo)
        self.config = AppConfig(client_id="cid-a", nickname="A", server_url="http://test")
        self.api = ApiClient("http://testserver", client_id="cid-a", http=self.transport)
        self.events: list[tuple[str, object]] = []
        self.engine = SyncEngine(
            self.repo,
            self.api,
            self.config,
            listener=lambda event, payload: self.events.append((event, payload)),
            outbox=self.outbox,
        )

    def tearDown(self) -> None:
        self.repo.close()
        self.store.close()
        self._ctx.__exit__(None, None, None)

    def _other_engine(self, client_id: str, nickname: str) -> tuple[Repo, SyncEngine]:
        repo, _ = _make_repo(self.workspace, f"client_{client_id}.db")
        api = ApiClient("http://testserver", client_id=client_id, http=self.transport)
        engine = SyncEngine(repo, api, AppConfig(client_id=client_id, nickname=nickname))
        engine.bootstrap()
        return repo, engine

    def test_offline_publish_then_replay_creates_exactly_one(self) -> None:
        self.transport.set_offline(True)
        bubble_id = self.engine.publish(content="断网写的", emotion_tag="孤独", reply_mode="single")
        self.assertEqual(self.store.stats()["bubbles"], 0)
        self.assertEqual(self.outbox.pending_count(), 1)

        self.transport.set_offline(False)
        done = self.engine.flush(now=shift_seconds(now_iso(), 60))
        self.assertEqual(done, 1)
        self.assertEqual(self.store.stats()["bubbles"], 1)
        self.assertEqual(self.store.get_bubble(bubble_id).content, "断网写的")
        self.assertEqual(self.repo.get_bubble(bubble_id).sync_state, "synced")

    def test_duplicate_replay_is_idempotent(self) -> None:
        bubble_id = self.engine.publish(content="只应一条", emotion_tag="开心", reply_mode="single")
        self.assertEqual(self.store.stats()["bubbles"], 1)
        # 人为把已完成的条目改回 pending，模拟"超时后重发"
        self.repo.execute(
            "UPDATE outbox SET state = 'pending', next_attempt_at = ? WHERE bubble_id = ?",
            (now_iso(), bubble_id),
        )
        self.engine.flush(now=shift_seconds(now_iso(), 60))
        self.assertEqual(self.store.stats()["bubbles"], 1, "重发不得产生第二条")
        self.assertEqual(self.repo.get_bubble(bubble_id).sync_state, "synced")

    def test_single_mode_second_reply_conflicts(self) -> None:
        bubble_id = self.engine.publish(content="只能回一次", emotion_tag="开心", reply_mode="single")
        repo_b, engine_b = self._other_engine("cid-b", "B")
        try:
            engine_b.reply(bubble_id=bubble_id, content="第一次")
            self.assertEqual(self.store.count_replies(bubble_id), 1)
            engine_b.reply(bubble_id=bubble_id, content="第二次")
            self.assertEqual(self.store.count_replies(bubble_id), 1, "服务端不得出现第二条")
            self.assertEqual(len(repo_b.list_replies(bubble_id)), 1, "本地乐观回复必须被删除")
        finally:
            repo_b.close()

    def test_reply_and_chat_roundtrip(self) -> None:
        bubble_id = self.engine.publish(content="聊聊", emotion_tag="寻求建议", reply_mode="dialog")
        repo_b, engine_b = self._other_engine("cid-b", "B")
        try:
            engine_b.reply(bubble_id=bubble_id, content="我在")
            engine_b.chat(bubble_id=bubble_id, receiver="A", content="你好")
            self.engine.detail(bubble_id)
            self.assertEqual(len(self.repo.list_replies(bubble_id)), 1)
            self.assertEqual(len(self.repo.list_chats(bubble_id)), 1)
            self.assertEqual(self.repo.list_chats(bubble_id)[0].receiver_nickname, "A")
        finally:
            repo_b.close()

    def test_resolve_syncs_status(self) -> None:
        bubble_id = self.engine.publish(content="结束吧", emotion_tag="难过", reply_mode="single")
        self.engine.resolve(bubble_id=bubble_id)
        server_bubble = self.store.get_bubble(bubble_id)
        self.assertEqual(server_bubble.status, "resolved")
        self.assertEqual(self.repo.get_bubble(bubble_id).status, "resolved")
        self.assertEqual(self.outbox.pending_count(), 0)

    def test_save_private_marks_server_private(self) -> None:
        bubble_id = self.engine.publish(content="保留", emotion_tag="开心", reply_mode="single")
        self.engine.save(bubble_id=bubble_id, is_public=False)
        self.assertEqual(self.store.get_bubble(bubble_id).status, "saved_private")


class StaticHygieneTest(unittest.TestCase):
    """铁律的静态检查（UI 部分的细则在 `test_hygiene.py`）。"""

    #: v1 遗留文件已全部删除（批次 B 收尾），因此白名单为空 —— 不再豁免任何文件。
    LEGACY_V1_FILES: frozenset[str] = frozenset()

    #: 允许 import PyQt5 的测试文件（批次 B 的 UI 测试，跑 offscreen）。
    UI_TEST_FILES: frozenset[str] = frozenset(
        {
            "tests/qt_env.py",
            "tests/test_flow_layout.py",
            "tests/test_wall_diff.py",
            "tests/test_hygiene.py",
        }
    )

    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parent.parent
        self.src = self.root / "src"

    def _python_files(self, base: Path) -> list[Path]:
        return sorted(
            path for path in base.rglob("*.py") if "__pycache__" not in path.parts
        )

    def _scoped(self, base: Path) -> list[Path]:
        return [
            path
            for path in self._python_files(base)
            if _rel(path, self.root) not in self.LEGACY_V1_FILES
        ]

    def test_requests_only_in_core_api(self) -> None:
        offenders: list[str] = []
        for path in self._scoped(self.src):
            if _rel(path, self.root) == "src/core/api.py":
                continue
            if "requests" in _imported_roots(path):
                offenders.append(_rel(path, self.root))
        self.assertEqual(offenders, [], "requests 只允许出现在 src/core/api.py")

    def test_core_has_no_pyqt_or_flask(self) -> None:
        offenders: list[str] = []
        for path in self._python_files(self.src / "core"):
            roots = _imported_roots(path)
            if "PyQt5" in roots or "flask" in roots:
                offenders.append(_rel(path, self.root))
        self.assertEqual(offenders, [])

    def test_ui_has_no_requests(self) -> None:
        offenders: list[str] = []
        for path in self._python_files(self.src / "ui"):
            if "requests" in _imported_roots(path):
                offenders.append(_rel(path, self.root))
        self.assertEqual(offenders, [])

    def test_server_modules_have_no_requests(self) -> None:
        offenders: list[str] = []
        for path in self._scoped(self.src):
            name = path.name
            if not name.startswith("server"):
                continue
            if "requests" in _imported_roots(path):
                offenders.append(_rel(path, self.root))
        self.assertEqual(offenders, [])

    def test_no_select_star(self) -> None:
        offenders: list[str] = []
        for path in self._scoped(self.src):
            for literal in _string_literals(path):
                if "SELECT *" in literal or "select *" in literal:
                    offenders.append(_rel(path, self.root))
                    break
        self.assertEqual(offenders, [])

    def test_no_bare_except(self) -> None:
        offenders: list[str] = []
        for path in self._scoped(self.src):
            for line in _bare_except_lines(path):
                offenders.append(f"{_rel(path, self.root)}:{line}")
        self.assertEqual(offenders, [])

    def test_tests_do_not_import_pyqt(self) -> None:
        offenders: list[str] = []
        for path in self._python_files(self.root / "tests"):
            if _rel(path, self.root) in self.UI_TEST_FILES:
                continue
            if "PyQt5" in _imported_roots(path):
                offenders.append(_rel(path, self.root))
        self.assertEqual(offenders, [])

    def test_emotion_constants_single_source(self) -> None:
        text = (self.src / "core" / "constants.py").read_text(encoding="utf-8")
        for emotion in ("开心", "难过", "孤独", "焦虑", "寻求建议", "找人倾诉"):
            self.assertIn(emotion, text)

    def test_sqlite_connections_are_autocommit_and_row_factory(self) -> None:
        """所有生产连接必须 `isolation_level=None` + `row_factory=sqlite3.Row`。

        理由（架构师实测踩坑）：
            * 默认 `isolation_level=""` 下 sqlite3 会隐式开事务，
              手写 `BEGIN IMMEDIATE` 直接抛 `cannot start a transaction within a transaction`；
            * 漏设 `row_factory` 的线程连接会退回位置下标，P0-1 字段错位原地复活。
        """
        offenders: list[str] = []
        for path in self._scoped(self.src):
            for label, missing in _connect_call_problems(path):
                offenders.append(f"{_rel(path, self.root)}:{label} 缺少 {missing}")
        self.assertEqual(offenders, [])

    def test_no_question_mark_garbled_text(self) -> None:
        offenders: list[str] = []
        for path in self._scoped(self.src):
            if "??????" in path.read_text(encoding="utf-8"):
                offenders.append(_rel(path, self.root))
        self.assertEqual(offenders, [])

    def test_all_source_files_are_utf8_without_bom(self) -> None:
        offenders: list[str] = []
        for path in self._scoped(self.src):
            raw = path.read_bytes()
            if raw.startswith(b"\xef\xbb\xbf"):
                offenders.append(f"{_rel(path, self.root)} (BOM)")
                continue
            try:
                raw.decode("utf-8")
            except UnicodeDecodeError:  # pragma: no cover - 失败即报告
                offenders.append(f"{_rel(path, self.root)} (非 UTF-8)")
        self.assertEqual(offenders, [])


def _rel(path: Path, root: Path) -> str:
    """相对路径（统一正斜杠）。"""
    return str(path.relative_to(root)).replace("\\", "/")


def _imported_roots(path: Path) -> set[str]:
    """解析文件的顶层 import 模块名（AST，避免被 docstring 里的词干扰）。"""
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                roots.add(node.module.split(".")[0])
    return roots


def _string_literals(path: Path) -> list[str]:
    """取文件中**非 docstring** 的字符串字面量。"""
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
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


def _bare_except_lines(path: Path) -> list[int]:
    """返回裸 `except:` 的行号。"""
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.ExceptHandler) and node.type is None
    ]


def _connect_call_problems(path: Path) -> list[tuple[int, str]]:
    """检查文件里每个 `sqlite3.connect(...)` 是否配齐隔离级别与 row_factory。

    Returns:
        `[(行号, 缺失项)]`；空列表表示全部合规。
    """
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    functions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    problems: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not _is_sqlite_connect(node):
            continue
        keywords = {kw.arg: kw.value for kw in node.keywords}
        isolation = keywords.get("isolation_level")
        if isolation is None or not (
            isinstance(isolation, ast.Constant) and isolation.value is None
        ):
            problems.append((node.lineno, "isolation_level=None"))
        owner = _enclosing_function(functions, node.lineno)
        if owner is None or not _sets_row_factory(owner, node):
            problems.append((node.lineno, "row_factory=sqlite3.Row"))
    return problems


def _is_sqlite_connect(node: ast.AST) -> bool:
    """判断节点是否为 `sqlite3.connect(...)` 调用。"""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "connect"
        and isinstance(func.value, ast.Name)
        and func.value.id == "sqlite3"
    )


def _enclosing_function(
    functions: list[ast.AST], lineno: int
) -> ast.AST | None:
    """找出包含指定行号的最内层函数。"""
    best: ast.AST | None = None
    best_span = 10**9
    for node in functions:
        start = node.lineno
        end = getattr(node, "end_lineno", start)
        if start <= lineno <= end:
            span = end - start
            if span < best_span:
                best, best_span = node, span
    return best


def _sets_row_factory(owner: ast.AST, call: ast.AST) -> bool:
    """在所属函数内确认 `conn.row_factory = sqlite3.Row`。"""
    conn_names: set[str] = set()
    for node in ast.walk(owner):
        if not isinstance(node, ast.Assign):
            continue
        if node.value is not call:
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                conn_names.add(target.id)
    if not conn_names:
        return False
    for node in ast.walk(owner):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if (
                isinstance(target, ast.Attribute)
                and target.attr == "row_factory"
                and isinstance(target.value, ast.Name)
                and target.value.id in conn_names
            ):
                return True
    return False


if __name__ == "__main__":
    unittest.main()
