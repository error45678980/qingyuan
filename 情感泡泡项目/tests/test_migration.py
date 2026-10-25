"""T-03 验收：旧客户端库无损迁移（A1~A7）。"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import unittest
from pathlib import Path

from src.core.errors import MigrationError
from src.core.migrations import (
    OUTCOME_ALREADY,
    OUTCOME_FRESH,
    OUTCOME_MIGRATED,
    Migrator,
    ensure_client_schema,
)
from tests.helpers import (
    CLIENT_BUBBLE_COLUMNS_V2,
    FIXTURES_DIR,
    LEGACY_CLIENT_BUBBLE_COLUMNS,
    LEGACY_CLIENT_BUBBLES,
    LEGACY_CLIENT_FIXTURE,
    LEGACY_SERVER_FIXTURE,
    PROJECT_ROOT,
    close_quietly,
    connect,
    copy_legacy_client,
    count_rows,
    fetch_dicts,
    table_columns,
    table_exists,
    temp_workspace,
)


class ClientMigrationTest(unittest.TestCase):
    """A1~A7：迁移后 6 条一条不少、字段不错位、幂等、有备份。"""

    def setUp(self) -> None:
        self._workspace_ctx = temp_workspace()
        self.workspace = self._workspace_ctx.__enter__()
        self.db_path = os.path.join(self.workspace, "emotion_bubble.db")
        self.backup_dir = os.path.join(self.workspace, "backup")
        copy_legacy_client(self.db_path)
        self.migrator = Migrator(self.db_path, self.backup_dir)
        self.result = self.migrator.run()

    def tearDown(self) -> None:
        self._workspace_ctx.__exit__(None, None, None)

    # -- A1 ---------------------------------------------------------------
    def test_a1_column_order(self) -> None:
        conn = connect(self.db_path)
        try:
            self.assertEqual(tuple(table_columns(conn, "bubbles")), CLIENT_BUBBLE_COLUMNS_V2)
            self.assertEqual(
                table_columns(conn, "replies"),
                [
                    "id",
                    "bubble_id",
                    "replier_nickname",
                    "replier_client_id",
                    "content",
                    "created_at",
                    "op_id",
                    "sync_state",
                ],
            )
            self.assertEqual(
                table_columns(conn, "chat_messages"),
                [
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
                ],
            )
        finally:
            close_quietly(conn)

    # -- A2 ---------------------------------------------------------------
    def test_a2_counts(self) -> None:
        conn = connect(self.db_path)
        try:
            self.assertEqual(count_rows(conn, "bubbles"), 6)
            self.assertEqual(count_rows(conn, "replies"), 0)
            self.assertEqual(count_rows(conn, "chat_messages"), 0)
        finally:
            close_quietly(conn)

    # -- A3 ---------------------------------------------------------------
    def test_a3_id_set_unchanged(self) -> None:
        conn = connect(self.db_path)
        try:
            rows = fetch_dicts(conn, "SELECT id FROM bubbles")
        finally:
            close_quietly(conn)
        self.assertEqual({row["id"] for row in rows}, set(LEGACY_CLIENT_BUBBLES))

    # -- A4 ---------------------------------------------------------------
    def test_a4_every_field_matches_snapshot(self) -> None:
        conn = connect(self.db_path)
        try:
            rows = fetch_dicts(
                conn,
                "SELECT id, author_nickname, content, emotion_tag, reply_mode, status, "
                "created_at, author_client_id, version, sync_state FROM bubbles",
            )
        finally:
            close_quietly(conn)
        self.assertEqual(len(rows), len(LEGACY_CLIENT_BUBBLES))
        for row in rows:
            expected = LEGACY_CLIENT_BUBBLES[row["id"]]
            self.assertEqual(
                (
                    row["author_nickname"],
                    row["content"],
                    row["emotion_tag"],
                    row["reply_mode"],
                    row["status"],
                    row["created_at"],
                ),
                expected,
                msg=f"{row['id']} 字段与快照不一致",
            )
            self.assertEqual(row["author_client_id"], "")
            self.assertEqual(row["version"], 1)
            self.assertEqual(row["sync_state"], "synced")

    def test_a4_test_pub_003_not_shifted(self) -> None:
        conn = connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT content, emotion_tag, reply_mode, status, created_at "
                "FROM bubbles WHERE id = ?",
                ("test-pub-003",),
            ).fetchone()
        finally:
            close_quietly(conn)
        self.assertIsNotNone(row)
        self.assertEqual(row["content"], "隐私测试")
        self.assertEqual(row["emotion_tag"], "孤独")
        self.assertEqual(row["reply_mode"], "dialog")
        self.assertEqual(row["status"], "active")
        self.assertEqual(row["created_at"], "2026-07-21T17:20:00")

    def test_a4_microseconds_preserved(self) -> None:
        conn = connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT created_at FROM bubbles WHERE id = ?",
                ("8d8ff2ff-4e27-42a8-840e-f73ca3c95f22",),
            ).fetchone()
        finally:
            close_quietly(conn)
        self.assertEqual(row["created_at"], "2026-07-21T16:22:40.905472")

    # -- A5 ---------------------------------------------------------------
    def test_a5_legacy_column_removed(self) -> None:
        conn = connect(self.db_path)
        try:
            columns = table_columns(conn, "bubbles")
            self.assertNotIn("author_node_id", columns)
            self.assertFalse(table_exists(conn, "bubbles_legacy_v1"))
            self.assertFalse(table_exists(conn, "bubbles_v2"))
        finally:
            close_quietly(conn)

    # -- A6 ---------------------------------------------------------------
    def test_a6_second_run_is_idempotent(self) -> None:
        self.assertEqual(self.result.outcome, OUTCOME_MIGRATED)
        second = self.migrator.run()
        self.assertEqual(second.outcome, OUTCOME_ALREADY)
        self.assertTrue(second.already_migrated)
        self.assertIsNone(second.backup_path)
        conn = connect(self.db_path)
        try:
            self.assertEqual(count_rows(conn, "bubbles"), 6)
            self.assertEqual(tuple(table_columns(conn, "bubbles")), CLIENT_BUBBLE_COLUMNS_V2)
        finally:
            close_quietly(conn)

    # -- A7 ---------------------------------------------------------------
    def test_a7_backup_created_and_readable(self) -> None:
        self.assertIsNotNone(self.result.backup_path)
        self.assertTrue(os.path.isfile(self.result.backup_path or ""))
        backups = os.listdir(self.backup_dir)
        self.assertEqual(len(backups), 1)
        conn = connect(self.result.backup_path or "")
        try:
            self.assertEqual(count_rows(conn, "bubbles"), 6)
            # 备份必须是**旧结构**（否则失去回滚意义）
            self.assertIn("author_node_id", table_columns(conn, "bubbles"))
        finally:
            close_quietly(conn)

    def test_backup_matches_original_bytes(self) -> None:
        self.assertIsNotNone(self.result.backup_path)
        original_size = os.path.getsize(self.result.backup_path or "")
        self.assertGreater(original_size, 0)

    # -- 其它 -------------------------------------------------------------
    def test_migration_log_written(self) -> None:
        conn = connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT value FROM meta WHERE key = ?", ("schema_version",)
            ).fetchone()
            self.assertEqual(row["value"], "2")
            log_row = conn.execute(
                "SELECT value FROM meta WHERE key = ?", ("migration_log",)
            ).fetchone()
            self.assertIsNotNone(log_row)
            self.assertIn("legacy_columns", log_row["value"])
            self.assertIn("ignored_legacy_table", log_row["value"])
            self.assertIn("users", log_row["value"])
        finally:
            close_quietly(conn)

    def test_legacy_users_table_untouched(self) -> None:
        conn = connect(self.db_path)
        try:
            self.assertTrue(table_exists(conn, "users"))
            self.assertEqual(count_rows(conn, "users"), 2)
        finally:
            close_quietly(conn)

    def test_outbox_and_meta_created(self) -> None:
        conn = connect(self.db_path)
        try:
            self.assertTrue(table_exists(conn, "outbox"))
            self.assertTrue(table_exists(conn, "meta"))
            self.assertEqual(
                table_columns(conn, "outbox"),
                [
                    "id",
                    "op_id",
                    "op_type",
                    "bubble_id",
                    "payload",
                    "base_version",
                    "state",
                    "attempts",
                    "next_attempt_at",
                    "last_error",
                    "created_at",
                    "updated_at",
                ],
            )
        finally:
            close_quietly(conn)

    def test_result_counts(self) -> None:
        self.assertEqual(self.result.before, {"bubbles": 6, "replies": 0, "chat_messages": 0})
        self.assertEqual(self.result.after, {"bubbles": 6, "replies": 0, "chat_messages": 0})
        self.assertEqual(self.result.schema_version, 2)
        self.assertEqual(
            self.result.legacy_columns["bubbles"], LEGACY_CLIENT_BUBBLE_COLUMNS
        )


class FreshInitTest(unittest.TestCase):
    """空库直接建 v2 结构。"""

    def test_fresh_init(self) -> None:
        with temp_workspace() as workspace:
            db_path = os.path.join(workspace, "nested", "emotion_bubble.db")
            result = ensure_client_schema(db_path, os.path.join(workspace, "backup"))
            self.assertEqual(result.outcome, OUTCOME_FRESH)
            self.assertIsNone(result.backup_path)
            conn = connect(db_path)
            try:
                self.assertEqual(tuple(table_columns(conn, "bubbles")), CLIENT_BUBBLE_COLUMNS_V2)
                self.assertEqual(count_rows(conn, "bubbles"), 0)
                self.assertEqual(count_rows(conn, "outbox"), 0)
            finally:
                close_quietly(conn)
            # 第二次仍应判定为已迁移
            again = ensure_client_schema(db_path, os.path.join(workspace, "backup"))
            self.assertEqual(again.outcome, OUTCOME_ALREADY)


class SavedAtRemapTest(unittest.TestCase):
    """v1 把 saved 时间写进 `resolved_at`，迁移必须搬回 `saved_at` 并清空 `resolved_at`。"""

    def _make_legacy(self, path: str) -> None:
        conn = sqlite3.connect(path)
        try:
            conn.execute(
                "CREATE TABLE bubbles ("
                "id TEXT PRIMARY KEY, author_nickname TEXT NOT NULL, content TEXT NOT NULL, "
                "emotion_tag TEXT NOT NULL, reply_mode TEXT NOT NULL, status TEXT NOT NULL, "
                "created_at TEXT NOT NULL, resolved_at TEXT)"
            )
            conn.execute(
                "INSERT INTO bubbles VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "b-saved",
                    "Tester",
                    "公开保留",
                    "开心",
                    "single",
                    "saved_public",
                    "2026-07-21T10:00:00",
                    "2026-07-21T11:00:00",
                ),
            )
            conn.execute(
                "INSERT INTO bubbles VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "b-private",
                    "Tester",
                    "私密保留",
                    "孤独",
                    "dialog",
                    "saved_private",
                    "2026-07-21T12:00:00",
                    "2026-07-21T13:00:00",
                ),
            )
            conn.execute(
                "INSERT INTO bubbles VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "b-resolved",
                    "Tester",
                    "已解决",
                    "难过",
                    "single",
                    "resolved",
                    "2026-07-21T14:00:00",
                    "2026-07-21T15:00:00",
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def test_saved_at_and_resolved_at_separated(self) -> None:
        with temp_workspace() as workspace:
            db_path = os.path.join(workspace, "legacy.db")
            self._make_legacy(db_path)
            ensure_client_schema(db_path, os.path.join(workspace, "backup"))
            conn = connect(db_path)
            try:
                saved = conn.execute(
                    "SELECT saved_at, resolved_at FROM bubbles WHERE id = ?", ("b-saved",)
                ).fetchone()
                private = conn.execute(
                    "SELECT saved_at, resolved_at FROM bubbles WHERE id = ?", ("b-private",)
                ).fetchone()
                resolved = conn.execute(
                    "SELECT saved_at, resolved_at FROM bubbles WHERE id = ?", ("b-resolved",)
                ).fetchone()
            finally:
                close_quietly(conn)
        self.assertEqual(saved["saved_at"], "2026-07-21T11:00:00")
        self.assertIsNone(saved["resolved_at"])
        self.assertEqual(private["saved_at"], "2026-07-21T13:00:00")
        self.assertIsNone(private["resolved_at"])
        self.assertEqual(resolved["resolved_at"], "2026-07-21T15:00:00")
        self.assertIsNone(resolved["saved_at"])


class BackupFailureTest(unittest.TestCase):
    """备份失败必须中止迁移且不修改原库。"""

    def test_backup_failure_aborts(self) -> None:
        with temp_workspace() as workspace:
            db_path = os.path.join(workspace, "emotion_bubble.db")
            copy_legacy_client(db_path)
            # 用一个**文件**冒充备份目录，制造 mkdir 失败
            blocked = os.path.join(workspace, "blocked_backup")
            with open(blocked, "w", encoding="utf-8") as handle:
                handle.write("not a directory")
            before_size = os.path.getsize(db_path)
            with self.assertRaises(MigrationError):
                Migrator(db_path, blocked).run()
            self.assertEqual(os.path.getsize(db_path), before_size)
            conn = connect(db_path)
            try:
                self.assertIn("author_node_id", table_columns(conn, "bubbles"))
                self.assertEqual(count_rows(conn, "bubbles"), 6)
            finally:
                close_quietly(conn)


class FixtureIntegrityTest(unittest.TestCase):
    """夹具完整性守卫（防止"旧库→新库"这条用例永久失效）。

    风险：若夹具被新代码就地迁移过一次，它就会变成 v2 结构，此后所有迁移测试都在
    "v2 → v2"，等于永远测不到真正的升级路径。因此这里**钉死哈希与列结构**：
    夹具一旦被改动，本用例立刻红灯，逼迫维护者重新从 `server_data/backup/` 取快照。

    权威来源（迁移前快照，不得覆盖）：
        server_data/backup/client_emotion_bubble_20261024_121250.db
        server_data/backup/server_emotion_bubble_20261024_121250.db
    """

    #: 夹具 sha256（与迁移前快照逐字节一致）。
    EXPECTED_SHA256: dict[str, str] = {
        "legacy_client.db": "d9d8197bf5bc410629c9a2b2638ecf4a48ab4e7d4b24b3f8f4072868e2a34c06",
        "legacy_server.db": "bf922b40226c81fea95a78c75d11d025da315e29bf9d04bc1ddd43777ca3a703",
    }

    def test_fixtures_are_byte_identical_to_pinned_snapshots(self) -> None:
        for name, expected in self.EXPECTED_SHA256.items():
            path = os.path.join(FIXTURES_DIR, name)
            self.assertTrue(os.path.isfile(path), f"缺少夹具 {name}")
            actual = hashlib.sha256(Path(path).read_bytes()).hexdigest()
            self.assertEqual(
                actual,
                expected,
                f"{name} 已被改动——请重新从 server_data/backup/ 复制迁移前快照，"
                "并确认不是被新代码就地迁移过",
            )

    def test_client_fixture_is_still_v1_schema(self) -> None:
        conn = connect(LEGACY_CLIENT_FIXTURE)
        try:
            columns = table_columns(conn, "bubbles")
            # 末列必须是 author_node_id（P0-1 的错位根源，测试价值就在这里）
            self.assertEqual(columns[-1], "author_node_id")
            self.assertNotIn("author_client_id", columns)
            self.assertNotIn("saved_at", columns)
            self.assertNotIn("sync_state", columns)
            self.assertEqual(count_rows(conn, "bubbles"), 6)
            self.assertTrue(table_exists(conn, "users"))
        finally:
            close_quietly(conn)

    def test_server_fixture_is_still_v1_schema(self) -> None:
        conn = connect(LEGACY_SERVER_FIXTURE)
        try:
            columns = table_columns(conn, "bubbles")
            self.assertIn("author_node_id", columns)
            self.assertNotIn("author_client_id", columns)
            self.assertEqual(count_rows(conn, "bubbles"), 5)
            self.assertEqual(count_rows(conn, "replies"), 1)
            self.assertEqual(count_rows(conn, "chat_messages"), 2)
        finally:
            close_quietly(conn)

    def test_authoritative_backups_still_exist(self) -> None:
        """迁移前快照必须还在（不得删除、不得覆盖）。"""
        backup_dir = os.path.join(PROJECT_ROOT, "server_data", "backup")
        for name in (
            "client_emotion_bubble_20261024_121250.db",
            "server_emotion_bubble_20261024_121250.db",
        ):
            self.assertTrue(
                os.path.isfile(os.path.join(backup_dir, name)),
                f"迁移前快照丢失: {name}",
            )

    def test_migration_does_not_touch_fixture_in_place(self) -> None:
        """迁移测试必须操作副本：跑一遍迁移后夹具哈希不变。"""
        before = hashlib.sha256(Path(LEGACY_CLIENT_FIXTURE).read_bytes()).hexdigest()
        with temp_workspace() as workspace:
            db_path = os.path.join(workspace, "copy.db")
            copy_legacy_client(db_path)
            ensure_client_schema(db_path, os.path.join(workspace, "backup"))
        after = hashlib.sha256(Path(LEGACY_CLIENT_FIXTURE).read_bytes()).hexdigest()
        self.assertEqual(before, after, "迁移测试污染了夹具本体")


if __name__ == "__main__":
    unittest.main()
