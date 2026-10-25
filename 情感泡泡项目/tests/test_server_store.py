"""T-04 验收：服务端库迁移（5/1/2 不丢）+ 分页 + op_log 幂等 + version。"""

from __future__ import annotations

import os
import unittest

from src.core.errors import InvalidCursorError
from src.core.migrations import OUTCOME_ALREADY, OUTCOME_FRESH, OUTCOME_MIGRATED
from src.server_store import Page, ServerMigrator, ServerStore, ensure_server_schema
from tests.helpers import (
    LEGACY_SERVER_BUBBLES,
    LEGACY_SERVER_CHATS,
    LEGACY_SERVER_REPLIES,
    close_quietly,
    connect,
    copy_legacy_server,
    count_rows,
    fetch_dicts,
    table_columns,
    table_exists,
    temp_workspace,
)

SERVER_BUBBLE_COLUMNS_V2 = (
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
)


class ServerMigrationTest(unittest.TestCase):
    """A8：服务端旧库 5 泡泡 / 1 回复 / 2 对话不丢，字段不错位。"""

    def setUp(self) -> None:
        self._ctx = temp_workspace()
        self.workspace = self._ctx.__enter__()
        self.db_path = os.path.join(self.workspace, "emotion_bubble_server.db")
        self.backup_dir = os.path.join(self.workspace, "backup")
        copy_legacy_server(self.db_path)
        self.migrator = ServerMigrator(self.db_path, self.backup_dir)
        self.result = self.migrator.run()

    def tearDown(self) -> None:
        self._ctx.__exit__(None, None, None)

    def test_counts_preserved(self) -> None:
        conn = connect(self.db_path)
        try:
            self.assertEqual(count_rows(conn, "bubbles"), 5)
            self.assertEqual(count_rows(conn, "replies"), 1)
            self.assertEqual(count_rows(conn, "chat_messages"), 2)
        finally:
            close_quietly(conn)

    def test_column_order_same_as_client(self) -> None:
        conn = connect(self.db_path)
        try:
            self.assertEqual(tuple(table_columns(conn, "bubbles")), SERVER_BUBBLE_COLUMNS_V2)
            self.assertNotIn("sync_state", table_columns(conn, "bubbles"))
            self.assertNotIn("author_node_id", table_columns(conn, "bubbles"))
            self.assertTrue(table_exists(conn, "clients"))
            self.assertTrue(table_exists(conn, "op_log"))
            self.assertFalse(table_exists(conn, "outbox"))
        finally:
            close_quietly(conn)

    def test_bubble_fields_match_snapshot(self) -> None:
        conn = connect(self.db_path)
        try:
            rows = fetch_dicts(
                conn,
                "SELECT id, author_nickname, content, emotion_tag, reply_mode, status, "
                "created_at, author_client_id FROM bubbles",
            )
        finally:
            close_quietly(conn)
        self.assertEqual({row["id"] for row in rows}, set(LEGACY_SERVER_BUBBLES))
        for row in rows:
            expected = LEGACY_SERVER_BUBBLES[row["id"]]
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

    def test_reply_and_chat_not_shifted(self) -> None:
        store = ServerStore(self.db_path)
        try:
            replies = store.list_replies("test-pub-002")
            self.assertEqual(len(replies), 1)
            self.assertEqual(replies[0].replier_nickname, "Tester")
            self.assertEqual(replies[0].content, "你好")
            self.assertEqual(replies[0].op_id, "legacy-rep-1")

            chats = store.list_chats("test-pub-003")
            self.assertEqual(len(chats), 1)
            self.assertEqual(chats[0].sender_nickname, "Tester")
            self.assertEqual(chats[0].receiver_nickname, "Friend")
            self.assertEqual(chats[0].content, "私聊内容")
            self.assertIsNone(chats[0].receiver_client_id)
            self.assertEqual(chats[0].op_id, "legacy-chat-2")
        finally:
            store.close()

    def test_idempotent(self) -> None:
        self.assertEqual(self.result.outcome, OUTCOME_MIGRATED)
        second = self.migrator.run()
        self.assertEqual(second.outcome, OUTCOME_ALREADY)
        conn = connect(self.db_path)
        try:
            self.assertEqual(count_rows(conn, "bubbles"), 5)
        finally:
            close_quietly(conn)

    def test_backup_created(self) -> None:
        self.assertIsNotNone(self.result.backup_path)
        self.assertEqual(len(os.listdir(self.backup_dir)), 1)

    def test_legacy_reply_count_by_nickname_fallback(self) -> None:
        store = ServerStore(self.db_path)
        try:
            # 旧回复的 replier_client_id 为空，需回退昵称
            self.assertEqual(store.count_replies_by_client("test-pub-002", "cid-x", "Tester"), 1)
            self.assertEqual(store.count_replies_by_client("test-pub-002", "cid-x", "Other"), 0)
        finally:
            store.close()


class ServerFreshTest(unittest.TestCase):
    """空库初始化。"""

    def test_fresh(self) -> None:
        with temp_workspace() as workspace:
            db_path = os.path.join(workspace, "server.db")
            result = ensure_server_schema(db_path, os.path.join(workspace, "backup"))
            self.assertEqual(result.outcome, OUTCOME_FRESH)
            store = ServerStore(db_path)
            try:
                self.assertEqual(store.schema_version, 2)
                self.assertEqual(store.list_bubbles().items, [])
                self.assertEqual(store.stats()["bubbles"], 0)
            finally:
                store.close()


class ServerStoreTest(unittest.TestCase):
    """仓储行为。"""

    def setUp(self) -> None:
        self._ctx = temp_workspace()
        self.workspace = self._ctx.__enter__()
        self.db_path = os.path.join(self.workspace, "server.db")
        ensure_server_schema(self.db_path, os.path.join(self.workspace, "backup"))
        self.store = ServerStore(self.db_path)

    def tearDown(self) -> None:
        self.store.close()
        self._ctx.__exit__(None, None, None)

    def _seed(self, count: int, *, status: str = "active", emotion: str = "开心") -> list[str]:
        ids: list[str] = []
        for index in range(count):
            bubble_id = f"b{index}"
            self.store.create_bubble(
                bubble_id=bubble_id,
                author_nickname="Tester",
                author_client_id="cid-1",
                content=f"内容{index}",
                emotion_tag=emotion,
                reply_mode="single",
                created_at=f"2026-07-21T10:0{index}:00",
                status=status,
            )
            ids.append(bubble_id)
        return ids

    def test_create_and_get(self) -> None:
        self._seed(1)
        bubble = self.store.get_bubble("b0")
        self.assertIsNotNone(bubble)
        self.assertEqual(bubble.version, 1)
        self.assertEqual(bubble.content, "内容0")
        self.assertEqual(bubble.reply_count, 0)

    def test_get_missing_returns_none(self) -> None:
        self.assertIsNone(self.store.get_bubble("nope"))

    def test_pagination(self) -> None:
        self._seed(5)
        first = self.store.list_bubbles(limit=2)
        self.assertIsInstance(first, Page)
        self.assertEqual(first.total, 5)
        self.assertEqual(len(first.items), 2)
        self.assertTrue(first.has_more)
        self.assertIsNotNone(first.next_cursor)
        self.assertEqual([item.id for item in first.items], ["b4", "b3"])

        second = self.store.list_bubbles(limit=2, cursor=first.next_cursor)
        self.assertEqual([item.id for item in second.items], ["b2", "b1"])
        self.assertTrue(second.has_more)

        third = self.store.list_bubbles(limit=2, cursor=second.next_cursor)
        self.assertEqual([item.id for item in third.items], ["b0"])
        self.assertFalse(third.has_more)
        self.assertIsNone(third.next_cursor)

    def test_pagination_no_duplicates_across_pages(self) -> None:
        self._seed(7)
        seen: list[str] = []
        cursor = None
        for _ in range(10):
            page = self.store.list_bubbles(limit=2, cursor=cursor)
            seen.extend(item.id for item in page.items)
            if not page.has_more:
                break
            cursor = page.next_cursor
        self.assertEqual(len(seen), len(set(seen)))
        self.assertEqual(len(seen), 7)

    def test_invalid_cursor_raises(self) -> None:
        with self.assertRaises(InvalidCursorError):
            self.store.list_bubbles(cursor="!!!bad!!!")

    def test_hides_resolved_and_private(self) -> None:
        self._seed(1)
        self.store.create_bubble(
            bubble_id="b-resolved",
            author_nickname="Tester",
            author_client_id="cid-1",
            content="已解决",
            emotion_tag="开心",
            reply_mode="single",
            created_at="2026-07-21T11:00:00",
            status="resolved",
        )
        self.store.create_bubble(
            bubble_id="b-private",
            author_nickname="Tester",
            author_client_id="cid-1",
            content="私密",
            emotion_tag="开心",
            reply_mode="single",
            created_at="2026-07-21T12:00:00",
            status="saved_private",
        )
        ids = {item.id for item in self.store.list_bubbles(limit=100).items}
        self.assertEqual(ids, {"b0"})

    def test_emotion_and_search_filters(self) -> None:
        self._seed(2, emotion="开心")
        self.store.create_bubble(
            bubble_id="b-x",
            author_nickname="A",
            author_client_id="cid-2",
            content="孤独的夜",
            emotion_tag="孤独",
            reply_mode="dialog",
            created_at="2026-07-21T13:00:00",
        )
        self.assertEqual(len(self.store.list_bubbles(emotion="孤独").items), 1)
        self.assertEqual(len(self.store.list_bubbles(q="孤独").items), 1)
        self.assertEqual(len(self.store.list_bubbles(q="Tester").items), 2)
        self.assertEqual(len(self.store.list_bubbles(q="%").items), 0)

    def test_since_filter(self) -> None:
        self._seed(3)
        page = self.store.list_bubbles(since="2026-07-21T10:00:30")
        self.assertEqual({item.id for item in page.items}, {"b1", "b2"})

    def test_set_status_bumps_version(self) -> None:
        self._seed(1)
        bubble = self.store.set_bubble_status("b0", "resolved", at="2026-07-21T12:00:00")
        self.assertIsNotNone(bubble)
        self.assertEqual(bubble.status, "resolved")
        self.assertEqual(bubble.version, 2)
        self.assertEqual(bubble.resolved_at, "2026-07-21T12:00:00")

    def test_bump_version(self) -> None:
        self._seed(1)
        self.assertEqual(self.store.bump_version("b0"), 2)
        self.assertEqual(self.store.bump_version("b0"), 3)

    def test_add_reply_idempotent_by_op_id(self) -> None:
        self._seed(1)
        first = self.store.add_reply(
            bubble_id="b0",
            replier_nickname="A",
            replier_client_id="cid-a",
            content="你好",
            created_at="2026-07-21T12:00:00",
            op_id="op-1",
        )
        second = self.store.add_reply(
            bubble_id="b0",
            replier_nickname="A",
            replier_client_id="cid-a",
            content="你好",
            created_at="2026-07-21T12:00:00",
            op_id="op-1",
        )
        self.assertEqual(first.id, second.id)
        self.assertEqual(self.store.count_replies("b0"), 1)

    def test_add_chat_idempotent_by_op_id(self) -> None:
        self._seed(1)
        for _ in range(2):
            self.store.add_chat(
                bubble_id="b0",
                sender_nickname="Tester",
                sender_client_id="cid-1",
                receiver_nickname="A",
                receiver_client_id="cid-a",
                content="hi",
                created_at="2026-07-21T12:00:00",
                op_id="op-chat-1",
            )
        self.assertEqual(self.store.count_chats("b0"), 1)

    def test_chat_participant_filter(self) -> None:
        self._seed(1)
        self.store.add_chat(
            bubble_id="b0",
            sender_nickname="Tester",
            sender_client_id="cid-1",
            receiver_nickname="A",
            receiver_client_id="cid-a",
            content="给 A",
            created_at="2026-07-21T12:00:00",
            op_id="op-1",
        )
        self.store.add_chat(
            bubble_id="b0",
            sender_nickname="Tester",
            sender_client_id="cid-1",
            receiver_nickname="B",
            receiver_client_id="cid-b",
            content="给 B",
            created_at="2026-07-21T12:01:00",
            op_id="op-2",
        )
        self.assertEqual(len(self.store.list_chats("b0")), 2)
        mine = self.store.list_chats("b0", client_id="cid-a")
        self.assertEqual([item.content for item in mine], ["给 A"])
        self.assertTrue(self.store.has_chatted("b0", "cid-a", "A"))
        self.assertFalse(self.store.has_chatted("b0", "cid-c", "C"))

    def test_op_log_roundtrip(self) -> None:
        self.assertIsNone(self.store.get_op_log("op-x"))
        self.store.put_op_log(
            op_id="op-x",
            client_id="cid-1",
            op_type="publish",
            result={"id": "b1"},
            at="2026-07-21T12:00:00",
        )
        cached = self.store.get_op_log("op-x")
        self.assertIsNotNone(cached)
        self.assertEqual(cached["result"], {"id": "b1"})
        # 重复写入不覆盖首次结果
        self.store.put_op_log(
            op_id="op-x",
            client_id="cid-1",
            op_type="publish",
            result={"id": "b2"},
            at="2026-07-21T12:01:00",
        )
        self.assertEqual(self.store.get_op_log("op-x")["result"], {"id": "b1"})

    def test_client_upsert(self) -> None:
        record, created = self.store.upsert_client(
            client_id="cid-1", nickname="小明", token="tok-1", at="2026-07-21T12:00:00"
        )
        self.assertTrue(created)
        self.assertEqual(record.nickname, "小明")
        record2, created2 = self.store.upsert_client(
            client_id="cid-1", nickname="小明2", token="tok-2", at="2026-07-21T12:05:00"
        )
        self.assertFalse(created2)
        self.assertEqual(record2.nickname, "小明2")
        # token 不被覆盖
        self.assertEqual(record2.token, "tok-1")
        self.assertEqual(record2.last_seen_at, "2026-07-21T12:05:00")
        self.assertIsNotNone(self.store.get_client_by_token("tok-1"))

    def test_list_mine_groups(self) -> None:
        self._seed(1)
        self.store.create_bubble(
            bubble_id="b-pub",
            author_nickname="Tester",
            author_client_id="cid-1",
            content="公开保留",
            emotion_tag="开心",
            reply_mode="single",
            created_at="2026-07-21T11:00:00",
            status="saved_public",
        )
        self.store.create_bubble(
            bubble_id="b-priv",
            author_nickname="Tester",
            author_client_id="cid-1",
            content="私密",
            emotion_tag="开心",
            reply_mode="single",
            created_at="2026-07-21T12:00:00",
            status="saved_private",
        )
        counts = self.store.count_by_group(client_id="cid-1", nickname="Tester")
        self.assertEqual(counts["published"], 2)
        self.assertEqual(counts["saved_public"], 1)
        self.assertEqual(counts["saved_private"], 1)
        self.assertEqual(counts["resolved"], 0)
        published = self.store.list_mine(
            "published", client_id="cid-1", nickname="Tester"
        )
        self.assertEqual({item.id for item in published.items}, {"b0", "b-pub"})

    def test_list_mine_rejects_unknown_group(self) -> None:
        with self.assertRaises(ValueError):
            self.store.list_mine("bogus", client_id="c", nickname="n")

    def test_legacy_owner_nickname_fallback(self) -> None:
        self.store.create_bubble(
            bubble_id="b-legacy",
            author_nickname="Tester",
            author_client_id="",
            content="旧数据",
            emotion_tag="开心",
            reply_mode="single",
            created_at="2026-07-21T11:00:00",
        )
        page = self.store.list_mine("published", client_id="cid-new", nickname="Tester")
        self.assertEqual([item.id for item in page.items], ["b-legacy"])
        other = self.store.list_mine("published", client_id="cid-new", nickname="Other")
        self.assertEqual(other.items, [])

    def test_transaction_rollback(self) -> None:
        with self.assertRaises(RuntimeError):
            with self.store.transaction() as conn:
                conn.execute(
                    "INSERT INTO bubbles (id, author_nickname, author_client_id, content, "
                    "emotion_tag, reply_mode, status, version, created_at) "
                    "VALUES ('b-rb', 'n', '', 'c', '开心', 'single', 'active', 1, "
                    "'2026-07-21T12:00:00')"
                )
                raise RuntimeError("boom")
        self.assertIsNone(self.store.get_bubble("b-rb"))


if __name__ == "__main__":
    unittest.main()
