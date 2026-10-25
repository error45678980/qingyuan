"""迁移用的 DDL 与"旧列 → 新列"映射声明（架构文档 §3.1 / §3.3.1 / §3.3.2）。

从 `migrations.py` 拆出，目的：单文件 ≤ 500 行（铁律 5）。
本模块只声明**结构与映射**，不含任何执行逻辑；执行由 `migrations.SchemaMigrator` 负责。
服务端 `server_schema.py` 复用本模块的 `bubble_expressions / reply_expressions /
chat_expressions`，保证两端列名映射规则不会漂移。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from .constants import BUBBLE_STATUSES, EMOTION_FALLBACK, EMOTIONS, REPLY_MODES
from .errors import MigrationError

#: 安全字面量白名单（常量拼进 SQL 前的防御性校验）。
_SAFE_LITERAL = re.compile(r"^[0-9A-Za-z_\u4e00-\u9fff]+$")

# ---------------------------------------------------------------------------
# 迁移结果
# ---------------------------------------------------------------------------

OUTCOME_FRESH: str = "fresh"
OUTCOME_MIGRATED: str = "migrated"
OUTCOME_ALREADY: str = "already_migrated"

#: 安全字面量白名单（常量拼进 SQL 前的防御性校验）。
_SAFE_LITERAL = re.compile(r"^[0-9A-Za-z_\u4e00-\u9fff]+$")

#: 需要参与迁移与断言的业务表。
BUSINESS_TABLES: tuple[str, ...] = ("bubbles", "replies", "chat_messages")

#: 旧版遗留表（不读不写不删，仅登记）。
KNOWN_LEGACY_TABLES: tuple[str, ...] = ("users",)


@dataclass(frozen=True)
class MigrationResult:
    """迁移结果。"""

    outcome: str
    schema_version: int
    backup_path: Optional[str] = None
    before: Mapping[str, int] = field(default_factory=dict)
    after: Mapping[str, int] = field(default_factory=dict)
    legacy_columns: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def already_migrated(self) -> bool:
        """是否本来就是 v2 结构（幂等命中）。"""
        return self.outcome == OUTCOME_ALREADY


# ---------------------------------------------------------------------------
# 表迁移声明
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TableMigration:
    """一张表的迁移声明。

    Attributes:
        name: 正式表名。
        create_sql: 建表语句，含 `{table}`（本表名）与 `{ref}`（外键父表名）占位符。
        build_select: 由旧列名集合构造 `(select_list, params)`；`select_list` 会被用于
            `INSERT INTO {table}_v2 (目标列) SELECT ...`，也用于 M7 内容断言。
        fk_parent: 外键父表名（空表示无外键）。
    """

    name: str
    create_sql: str
    build_select: Callable[[frozenset[str]], tuple[str, tuple[Any, ...]]]
    fk_parent: str = ""


def sql_literals(values: Sequence[str]) -> str:
    """把常量序列渲染为 SQL 字面量列表（带白名单校验）。

    仅用于 `constants` 中的固定白名单（情感、状态、模式），**不用于任何用户输入**。

    Args:
        values: 常量序列。

    Returns:
        形如 `'开心','难过'` 的字符串。

    Raises:
        MigrationError: 出现不符合白名单的字符。
    """
    parts: list[str] = []
    for value in values:
        if not _SAFE_LITERAL.match(value):
            raise MigrationError(f"常量值含非法字符，拒绝拼入 SQL: {value!r}")
        parts.append("'" + value + "'")
    return ",".join(parts)


# ---------------------------------------------------------------------------
# 客户端表声明
# ---------------------------------------------------------------------------

BUBBLES_CREATE_SQL: str = """
CREATE TABLE {table} (
  id                TEXT    PRIMARY KEY,
  author_nickname   TEXT    NOT NULL,
  author_client_id  TEXT    NOT NULL DEFAULT '',
  content           TEXT    NOT NULL,
  emotion_tag       TEXT    NOT NULL
                    CHECK (emotion_tag IN ('开心','难过','孤独','焦虑','寻求建议','找人倾诉')),
  reply_mode        TEXT    NOT NULL CHECK (reply_mode IN ('single','dialog')),
  status            TEXT    NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active','resolved','saved_public','saved_private')),
  version           INTEGER NOT NULL DEFAULT 1,
  created_at        TEXT    NOT NULL,
  resolved_at       TEXT,
  saved_at          TEXT,
  sync_state        TEXT    NOT NULL DEFAULT 'synced'
                    CHECK (sync_state IN ('synced','pending','failed'))
)
"""

REPLIES_CREATE_SQL: str = """
CREATE TABLE {table} (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  bubble_id          TEXT    NOT NULL REFERENCES {ref}(id) ON DELETE CASCADE,
  replier_nickname   TEXT    NOT NULL,
  replier_client_id  TEXT    NOT NULL DEFAULT '',
  content            TEXT    NOT NULL,
  created_at         TEXT    NOT NULL,
  op_id              TEXT    NOT NULL,
  sync_state         TEXT    NOT NULL DEFAULT 'synced'
                     CHECK (sync_state IN ('synced','pending','failed'))
)
"""

CHAT_CREATE_SQL: str = """
CREATE TABLE {table} (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  bubble_id           TEXT    NOT NULL REFERENCES {ref}(id) ON DELETE CASCADE,
  sender_nickname     TEXT    NOT NULL,
  sender_client_id    TEXT    NOT NULL DEFAULT '',
  receiver_nickname   TEXT    NOT NULL,
  receiver_client_id  TEXT,
  content             TEXT    NOT NULL,
  created_at          TEXT    NOT NULL,
  op_id               TEXT    NOT NULL,
  sync_state          TEXT    NOT NULL DEFAULT 'synced'
                      CHECK (sync_state IN ('synced','pending','failed'))
)
"""

OUTBOX_CREATE_SQL: str = """
CREATE TABLE {table} (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  op_id            TEXT    NOT NULL UNIQUE,
  op_type          TEXT    NOT NULL
                   CHECK (op_type IN ('publish','reply','chat','resolve','save')),
  bubble_id        TEXT    NOT NULL,
  payload          TEXT    NOT NULL,
  base_version     INTEGER NOT NULL DEFAULT 0,
  state            TEXT    NOT NULL DEFAULT 'pending'
                   CHECK (state IN ('pending','syncing','done','failed')),
  attempts         INTEGER NOT NULL DEFAULT 0,
  next_attempt_at  TEXT    NOT NULL,
  last_error       TEXT,
  created_at       TEXT    NOT NULL,
  updated_at       TEXT    NOT NULL
)
"""

META_CREATE_SQL: str = """
CREATE TABLE {table} (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
)
"""

CLIENT_INDEX_STATEMENTS: tuple[str, ...] = (
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_replies_op ON replies(op_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_chat_op ON chat_messages(op_id)",
    "CREATE INDEX IF NOT EXISTS idx_bub_status_created ON bubbles(status, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_bub_emotion_created ON bubbles(emotion_tag, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_bub_author_status ON bubbles(author_client_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_rep_bubble_created ON replies(bubble_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_chat_bubble_created ON chat_messages(bubble_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_chat_participants "
    "ON chat_messages(bubble_id, sender_client_id, receiver_client_id)",
    "CREATE INDEX IF NOT EXISTS idx_outbox_state ON outbox(state, next_attempt_at, id)",
)

_EMOTION_LITERALS = sql_literals(EMOTIONS)
_MODE_LITERALS = sql_literals(REPLY_MODES)
_STATUS_LITERALS = sql_literals(BUBBLE_STATUSES)


def bubble_expressions(columns: frozenset[str]) -> list[str]:
    """构造 `bubbles` 的目标列值表达式（**不含** `sync_state`，共 11 项）。

    顺序与 `CLIENT_TARGET_COLUMNS["bubbles"]` 去掉 `sync_state` 后一致，
    服务端直接复用本函数，保证两端映射规则不会漂移。

    Args:
        columns: 旧表实际列名集合。

    Returns:
        表达式字符串列表。
    """
    nickname = "COALESCE(author_nickname,'')" if "author_nickname" in columns else "''"
    if "author_client_id" in columns:
        client_id = "COALESCE(author_client_id,'')"
    elif "author_node_id" in columns:
        client_id = "COALESCE(author_node_id,'')"
    else:
        client_id = "''"
    content = "COALESCE(content,'')" if "content" in columns else "''"
    emotion = (
        f"CASE WHEN emotion_tag IN ({_EMOTION_LITERALS}) THEN emotion_tag "
        f"ELSE '{EMOTION_FALLBACK}' END"
        if "emotion_tag" in columns
        else f"'{EMOTION_FALLBACK}'"
    )
    mode = (
        f"CASE WHEN reply_mode IN ({_MODE_LITERALS}) THEN reply_mode ELSE 'single' END"
        if "reply_mode" in columns
        else "'single'"
    )
    status = (
        f"CASE WHEN status IN ({_STATUS_LITERALS}) THEN status ELSE 'active' END"
        if "status" in columns
        else "'active'"
    )
    created = "COALESCE(created_at,'')" if "created_at" in columns else "''"
    resolved_source = "resolved_at" if "resolved_at" in columns else "NULL"
    # v1 把 saved 时间误写进 resolved_at，这里搬回正确列并清空 resolved_at
    resolved = f"CASE WHEN ({status}) = 'resolved' THEN {resolved_source} ELSE NULL END"
    saved = (
        f"CASE WHEN ({status}) IN ('saved_public','saved_private') "
        f"THEN {resolved_source} ELSE NULL END"
    )
    return [
        "id",
        nickname,
        client_id,
        content,
        emotion,
        mode,
        status,
        "1",
        created,
        resolved,
        saved,
    ]


def reply_expressions(columns: frozenset[str]) -> list[str]:
    """构造 `replies` 的目标列值表达式（**不含** `sync_state`，共 7 项）。"""
    if "replier_client_id" in columns:
        client_id = "COALESCE(replier_client_id,'')"
    elif "replier_node_id" in columns:
        client_id = "COALESCE(replier_node_id,'')"
    else:
        client_id = "''"
    nickname = "COALESCE(replier_nickname,'')" if "replier_nickname" in columns else "''"
    content = "COALESCE(content,'')" if "content" in columns else "''"
    created = "COALESCE(created_at,'')" if "created_at" in columns else "''"
    return ["id", "bubble_id", nickname, client_id, content, created, "'legacy-rep-' || id"]


def chat_expressions(columns: frozenset[str]) -> list[str]:
    """构造 `chat_messages` 的目标列值表达式（**不含** `sync_state`，共 9 项）。"""
    if "sender_client_id" in columns:
        sender_id = "COALESCE(sender_client_id,'')"
    elif "sender_node_id" in columns:
        sender_id = "COALESCE(sender_node_id,'')"
    else:
        sender_id = "''"
    sender = "COALESCE(sender_nickname,'')" if "sender_nickname" in columns else "''"
    receiver = "COALESCE(receiver_nickname,'')" if "receiver_nickname" in columns else "''"
    content = "COALESCE(content,'')" if "content" in columns else "''"
    created = "COALESCE(created_at,'')" if "created_at" in columns else "''"
    return [
        "id",
        "bubble_id",
        sender,
        sender_id,
        receiver,
        "NULL",
        content,
        created,
        "'legacy-chat-' || id",
    ]


def _bubbles_select(columns: frozenset[str]) -> tuple[str, tuple[Any, ...]]:
    """构造客户端 `bubbles` 的列名映射表达式（§3.3.1，末尾追加 `sync_state`）。"""
    return ", ".join(bubble_expressions(columns) + ["'synced'"]), ()


def _replies_select(columns: frozenset[str]) -> tuple[str, tuple[Any, ...]]:
    """构造客户端 `replies` 的列名映射表达式。"""
    return ", ".join(reply_expressions(columns) + ["'synced'"]), ()


def _chat_select(columns: frozenset[str]) -> tuple[str, tuple[Any, ...]]:
    """构造客户端 `chat_messages` 的列名映射表达式。"""
    return ", ".join(chat_expressions(columns) + ["'synced'"]), ()


#: 客户端迁移：表名 → 目标列顺序（与 `INSERT INTO ... (列) SELECT` 顺序一致）。
CLIENT_TARGET_COLUMNS: dict[str, tuple[str, ...]] = {
    "bubbles": (
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
    ),
    "replies": (
        "id",
        "bubble_id",
        "replier_nickname",
        "replier_client_id",
        "content",
        "created_at",
        "op_id",
        "sync_state",
    ),
    "chat_messages": (
        "id",
        "bubble_id",
        "sender_nickname",
        "sender_client_id",
        "receiver_nickname",
        "receiver_client_id",
        "content",
        "created_at",
        "op_id",
        "sync_state",
    ),
}

CLIENT_TABLE_MIGRATIONS: tuple[TableMigration, ...] = (
    TableMigration("bubbles", BUBBLES_CREATE_SQL, _bubbles_select),
    TableMigration("replies", REPLIES_CREATE_SQL, _replies_select, fk_parent="bubbles"),
    TableMigration("chat_messages", CHAT_CREATE_SQL, _chat_select, fk_parent="bubbles"),
)

#: 客户端额外表（不参与"旧→新"拷贝，直接建）。
CLIENT_EXTRA_TABLES: tuple[tuple[str, str], ...] = (
    ("outbox", OUTBOX_CREATE_SQL),
    ("meta", META_CREATE_SQL),
)



__all__ = [
    "OUTCOME_FRESH",
    "OUTCOME_MIGRATED",
    "OUTCOME_ALREADY",
    "BUSINESS_TABLES",
    "KNOWN_LEGACY_TABLES",
    "MigrationResult",
    "TableMigration",
    "sql_literals",
    "bubble_expressions",
    "reply_expressions",
    "chat_expressions",
    "BUBBLES_CREATE_SQL",
    "REPLIES_CREATE_SQL",
    "CHAT_CREATE_SQL",
    "OUTBOX_CREATE_SQL",
    "META_CREATE_SQL",
    "CLIENT_INDEX_STATEMENTS",
    "CLIENT_TABLE_MIGRATIONS",
    "CLIENT_TARGET_COLUMNS",
    "CLIENT_EXTRA_TABLES",
]
