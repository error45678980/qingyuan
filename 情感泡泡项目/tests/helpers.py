"""测试公共设施：临时库、夹具、假客户端、硬编码快照。

**本模块不得 import PyQt5。**

提供：
    PROJECT_ROOT / FIXTURES_DIR      路径常量
    temp_workspace()                 临时目录上下文管理器
    copy_fixture(name, target)       复制迁移夹具（每次测试都用全新副本）
    connect(path)                    返回 row_factory=Row 的连接
    table_columns(conn, table)       列名列表
    fetch_dicts(conn, table)         按列名取全部行
    LEGACY_CLIENT_BUBBLES 等         迁移前硬编码快照（A3/A4 断言用）
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
from contextlib import contextmanager
from typing import Any, Iterator, Mapping, Optional
from urllib.parse import parse_qs, urlsplit

PROJECT_ROOT: str = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS_DIR: str = os.path.join(PROJECT_ROOT, "tests")
FIXTURES_DIR: str = os.path.join(TESTS_DIR, "fixtures")

LEGACY_CLIENT_FIXTURE: str = os.path.join(FIXTURES_DIR, "legacy_client.db")
LEGACY_SERVER_FIXTURE: str = os.path.join(FIXTURES_DIR, "legacy_server.db")

# ---------------------------------------------------------------------------
# 迁移前快照（硬编码，防止"用迁移后的库证明迁移正确"的循环论证）
# ---------------------------------------------------------------------------

#: 客户端 6 条泡泡：id → (author_nickname, content, emotion_tag, reply_mode, status, created_at)
LEGACY_CLIENT_BUBBLES: dict[str, tuple[str, str, str, str, str, str]] = {
    "8d8ff2ff-4e27-42a8-840e-f73ca3c95f22": (
        "Tester",
        "Hello world",
        "开心",
        "single",
        "active",
        "2026-07-21T16:22:40.905472",
    ),
    "cb803a31-52f3-4a02-9f91-05d6706d92cf": (
        "A",
        "1111",
        "孤独",
        "single",
        "active",
        "2026-07-21T17:49:56.642867",
    ),
    "d3c8dd69-f1e8-4ebb-b44b-c106a6e3193b": (
        "A",
        "1111",
        "孤独",
        "single",
        "active",
        "2026-07-21T17:49:53.537151",
    ),
    "test-pub-003": ("Tester", "隐私测试", "孤独", "dialog", "active", "2026-07-21T17:20:00"),
    "test-pub-002": ("Tester", "测试2", "难过", "dialog", "active", "2026-07-21T17:10:00"),
    "test-pub-001": ("Tester", "测试泡泡", "开心", "single", "active", "2026-07-21T16:00:00"),
}

#: 旧客户端 `bubbles` 列序（author_node_id 在末尾，这是 P0-1 的根源）
LEGACY_CLIENT_BUBBLE_COLUMNS: tuple[str, ...] = (
    "id",
    "author_nickname",
    "content",
    "emotion_tag",
    "reply_mode",
    "status",
    "created_at",
    "resolved_at",
    "author_node_id",
)

#: 新客户端 `bubbles` 列序（A1 断言）
CLIENT_BUBBLE_COLUMNS_V2: tuple[str, ...] = (
    "id",
    "author_nickname",
    "author_client_id",
    "content",
    "emotion_tag",
    "reply_mode",
    "status",
    "version",
    "created_at",
    "resolved_at",
    "saved_at",
    "sync_state",
)

#: 服务端 5 条泡泡：id → (author_nickname, content, emotion_tag, reply_mode, status, created_at)
LEGACY_SERVER_BUBBLES: dict[str, tuple[str, str, str, str, str, str]] = {
    "test-pub-001": ("Tester", "测试泡泡", "开心", "single", "active", "2026-07-21T16:00:00"),
    "test-pub-002": ("Tester", "测试2", "难过", "dialog", "active", "2026-07-21T17:10:00"),
    "test-pub-003": ("Tester", "隐私测试", "孤独", "dialog", "active", "2026-07-21T17:20:00"),
    "d3c8dd69-f1e8-4ebb-b44b-c106a6e3193b": (
        "A",
        "1111",
        "孤独",
        "single",
        "active",
        "2026-07-21T17:49:53.537151",
    ),
    "cb803a31-52f3-4a02-9f91-05d6706d92cf": (
        "A",
        "1111",
        "孤独",
        "single",
        "active",
        "2026-07-21T17:49:56.642867",
    ),
}

#: 服务端 1 条回复：(id, bubble_id, replier_nickname, content, created_at)
LEGACY_SERVER_REPLIES: tuple[tuple[int, str, str, str, str], ...] = (
    (1, "test-pub-002", "Tester", "你好", "2026-07-21T16:39:35.447448"),
)

#: 服务端 2 条对话：(id, bubble_id, sender_nickname, receiver_nickname, content, created_at)
LEGACY_SERVER_CHATS: tuple[tuple[int, str, str, str, str, str], ...] = (
    (1, "test-pub-002", "Tester", "Tester", "继续聊天", "2026-07-21T17:11:00"),
    (2, "test-pub-003", "Tester", "Friend", "私聊内容", "2026-07-21T17:21:00"),
)


# ---------------------------------------------------------------------------
# 文件与连接
# ---------------------------------------------------------------------------


@contextmanager
def temp_workspace() -> Iterator[str]:
    """创建临时工作目录，退出时递归删除。

    Yields:
        目录绝对路径。
    """
    directory = tempfile.mkdtemp(prefix="eb_test_")
    try:
        yield directory
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def copy_fixture(source: str, target: str) -> str:
    """复制夹具到目标路径（保证每次测试都用未被迁移污染的副本）。"""
    os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
    shutil.copy2(source, target)
    return target


def copy_legacy_client(target: str) -> str:
    """复制旧客户端库夹具。"""
    return copy_fixture(LEGACY_CLIENT_FIXTURE, target)


def copy_legacy_server(target: str) -> str:
    """复制旧服务端库夹具。"""
    return copy_fixture(LEGACY_SERVER_FIXTURE, target)


def connect(path: str) -> sqlite3.Connection:
    """打开连接并设置 `row_factory = sqlite3.Row`。"""
    conn = sqlite3.connect(path, isolation_level=None, timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    """读取表的列名（顺序与 DDL 一致）。"""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [row["name"] for row in rows]


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    """判断表是否存在。"""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def count_rows(conn: sqlite3.Connection, table: str) -> int:
    """统计行数（表不存在时返回 0）。"""
    if not table_exists(conn, table):
        return 0
    row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
    return int(row["n"])


def fetch_dicts(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    """执行查询并把每行转成普通 dict。"""
    rows = conn.execute(sql, params).fetchall()
    return [{key: row[key] for key in row.keys()} for row in rows]


def close_quietly(conn: Optional[sqlite3.Connection]) -> None:
    """关闭连接，忽略关闭异常（测试清理用）。"""
    if conn is None:
        return
    try:
        conn.close()
    except sqlite3.Error:
        pass


def column_values(
    rows: list[Mapping[str, Any]], column: str
) -> list[Any]:
    """从 dict 列表里取某一列的所有值。"""
    return [row[column] for row in rows]


# ---------------------------------------------------------------------------
# HTTP 传输替身（让真实 ApiClient 走 Flask test_client，无需开端口）
# ---------------------------------------------------------------------------


class _FakeResponse:
    """最小化的 `requests.Response` 替身。"""

    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = "" if payload is None else str(payload)

    def json(self) -> Any:
        """返回 JSON 体；无体时抛 `ValueError`（与 requests 行为一致）。"""
        if self._payload is None:
            raise ValueError("响应不是 JSON")
        return self._payload


class FlaskTransport:
    """把 `requests` 风格调用转给 Flask `test_client`。

    这样 `src/core/api.py` 的**真实代码路径**（含状态码归一化、幂等判断）都被覆盖，
    同时不需要真的监听端口。
    """

    def __init__(self, flask_app: Any) -> None:
        """构造。

        Args:
            flask_app: Flask app 实例。
        """
        self._client = flask_app.test_client()
        self.offline = False

    def set_offline(self, flag: bool) -> None:
        """模拟断网。"""
        self.offline = bool(flag)

    def request(
        self,
        method: str,
        url: str,
        params: Optional[Mapping[str, Any]] = None,
        json: Optional[Mapping[str, Any]] = None,
        headers: Optional[Mapping[str, str]] = None,
        timeout: Optional[float] = None,
    ) -> _FakeResponse:
        """转发请求。"""
        import requests  # 仅测试用；src 中只有 core/api.py 允许

        if self.offline:
            raise requests.ConnectionError("模拟断网")
        parsed = urlsplit(url)
        path = parsed.path or "/"
        query: dict[str, Any] = {}
        if parsed.query:
            for key, values in parse_qs(parsed.query).items():
                query[key] = values[0]
        if params:
            for key, value in params.items():
                if value is not None:
                    query[key] = value
        response = self._client.open(
            path,
            method=method,
            json=dict(json) if json is not None else None,
            headers=dict(headers or {}),
            query_string=query,
        )
        return _FakeResponse(response.status_code, response.get_json(silent=True))


class FakeApi:
    """`ApiClient` 的脚本化替身（不联网、可编排返回值）。"""

    def __init__(self, client_id: str = "cid-fake", default: Any = None) -> None:
        """构造。

        Args:
            client_id: 设备 ID。
            default: 默认成功结果（缺省 `ok` 空 data）。
        """
        from src.core.api import ApiResult

        self.client_id = client_id
        self.token = ""
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.plan: dict[str, list[Any]] = {}
        self.default = default or ApiResult(True, 200, "ok", "", {})
        self.network_down = False

    def set_token(self, token: str) -> None:
        """记录 token。"""
        self.token = token

    def plan_for(self, name: str, results: list[Any]) -> None:
        """预置某方法的依次返回值。"""
        self.plan[name] = list(results)

    def _next(self, name: str, kwargs: dict[str, Any]) -> Any:
        """取下一个结果并记录调用。"""
        from src.core.api import ApiResult

        self.calls.append((name, kwargs))
        if self.network_down:
            return ApiResult(False, 0, "network_error", "网络好像断了，先记在本地啦")
        queue_ = self.plan.get(name)
        if queue_:
            return queue_.pop(0)
        return self.default

    def call_names(self) -> list[str]:
        """已发生调用的方法名序列。"""
        return [name for name, _ in self.calls]

    # -- 与 ApiClient 同名的方法 ------------------------------------------
    def health(self):
        """健康检查。"""
        return self._next("health", {})

    def session(self, nickname: str):
        """会话登记。"""
        return self._next("session", {"nickname": nickname})

    def list_bubbles(self, **kwargs):
        """列表。"""
        return self._next("list_bubbles", kwargs)

    def create_bubble(self, **kwargs):
        """发布。"""
        return self._next("create_bubble", kwargs)

    def get_bubble(self, bubble_id: str):
        """详情。"""
        return self._next("get_bubble", {"bubble_id": bubble_id})

    def reply(self, bubble_id: str, *, op_id: str, content: str):
        """回复。"""
        return self._next("reply", {"bubble_id": bubble_id, "op_id": op_id, "content": content})

    def get_chat(self, bubble_id: str, *, peer: Optional[str] = None):
        """读对话。"""
        return self._next("get_chat", {"bubble_id": bubble_id, "peer": peer})

    def post_chat(self, bubble_id: str, *, op_id: str, receiver: str, content: str):
        """发对话。"""
        return self._next(
            "post_chat",
            {"bubble_id": bubble_id, "op_id": op_id, "receiver": receiver, "content": content},
        )

    def resolve(self, bubble_id: str, *, op_id: str, base_version: int = 0):
        """已解决。"""
        return self._next(
            "resolve", {"bubble_id": bubble_id, "op_id": op_id, "base_version": base_version}
        )

    def save(self, bubble_id: str, *, op_id: str, is_public: bool, base_version: int = 0):
        """保留。"""
        return self._next(
            "save",
            {
                "bubble_id": bubble_id,
                "op_id": op_id,
                "is_public": is_public,
                "base_version": base_version,
            },
        )

    def mine(self, *, group: str = "published", limit: Any = None, cursor: Any = None):
        """我的。"""
        return self._next("mine", {"group": group, "limit": limit, "cursor": cursor})


__all__ = [
    "PROJECT_ROOT",
    "TESTS_DIR",
    "FIXTURES_DIR",
    "LEGACY_CLIENT_FIXTURE",
    "LEGACY_SERVER_FIXTURE",
    "LEGACY_CLIENT_BUBBLES",
    "LEGACY_CLIENT_BUBBLE_COLUMNS",
    "CLIENT_BUBBLE_COLUMNS_V2",
    "LEGACY_SERVER_BUBBLES",
    "LEGACY_SERVER_REPLIES",
    "LEGACY_SERVER_CHATS",
    "temp_workspace",
    "copy_fixture",
    "copy_legacy_client",
    "copy_legacy_server",
    "connect",
    "table_columns",
    "table_exists",
    "count_rows",
    "fetch_dicts",
    "close_quietly",
    "column_values",
    "FlaskTransport",
    "FakeApi",
]
