"""T-03 验收：客户端仓储（CRUD、分页、搜索、我的三组、私密隔离）。"""

from __future__ import annotations

import os
import unittest

from src.core.migrations import ensure_client_schema
from src.core.models import Bubble, ChatMessage, Reply
from src.core.repo import Repo
from tests.helpers import close_quietly, connect, copy_legacy_client, temp_workspace


def _bubble(bubble_id: str, **kwargs) -> Bubble:
    base = dict(
        id=bubble_id,
        author_nickname="Tester",
        author_client_id="cid-tester",
        content="内容",
        emotion_tag="开心",
        reply_mode="single",
        status="active",
        created_at="2026-07-21T17:00:00",
    )
    base.update(kwargs)
    return Bubble(**base)


class RepoOnMigratedLegacyTest(unittest.TestCase):
    """在真实迁移后的旧库上验证读路径。"""

    def setUp(self) -> None:
        self._ctx = temp_workspace()
        self.workspace = self._ctx.__enter__()
        self.db_path = os.path.join(self.workspace, "emotion_bubble.db")
        copy_legacy_client(self.db_path)
        ensure_client_schema(self.db_path, os.path.join(self.workspace, "backup"))
        self.repo = Repo(self.db_path)

    def tearDown(self) -> None:
        self.repo.close()
        self._ctx.__exit__(None, None, None)

    def test_list_public_returns_dataclasses_sorted_desc(self) -> None:
        bubbles = self.repo.list_public()
        self.assertEqual(len(bubbles), 6)
        self.assertTrue(all(isinstance(item, Bubble) for item in bubbles))
        timestamps = [item.created_at for item in bubbles]
        self.assertEqual(timestamps, sorted(timestamps, reverse=True))

    def test_list_public_emotion_filter(self) -> None:
        bubbles = self.repo.list_public(emotion="孤独")
        self.assertEqual(len(bubbles), 3)
        self.assertTrue(all(item.emotion_tag == "孤独" for item in bubbles))

    def test_list_public_search_by_content(self) -> None:
        bubbles = self.repo.list_public(q="隐私")
        self.assertEqual([item.id for item in bubbles], ["test-pub-003"])

    def test_list_public_search_by_author(self) -> None:
        bubbles = self.repo.list_public(q="A")
        self.assertEqual(len(bubbles), 2)
        self.assertTrue(all(item.author_nickname == "A" for item in bubbles))

    def test_list_public_search_escapes_wildcards(self) -> None:
        # `%` 必须被转义成普通字符，不能变成"匹配一切"
        self.assertEqual(self.repo.list_public(q="%"), [])

    def test_list_public_limit_offset(self) -> None:
        first = self.repo.list_public(limit=2, offset=0)
        second = self.repo.list_public(limit=2, offset=2)
        self.assertEqual(len(first), 2)
        self.assertEqual(len(second), 2)
        self.assertTrue({item.id for item in first}.isdisjoint({item.id for item in second}))

    def test_get_bubble_and_counts(self) -> None:
        bubble = self.repo.get_bubble("test-pub-003")
        self.assertIsNotNone(bubble)
        self.assertEqual(bubble.content, "隐私测试")
        self.assertEqual(bubble.reply_count, 0)
        self.assertEqual(bubble.chat_count, 0)

    def test_get_bubble_missing_returns_none(self) -> None:
        self.assertIsNone(self.repo.get_bubble("not-exist"))

    def test_list_mine_uses_nickname_fallback_for_legacy_rows(self) -> None:
        mine = self.repo.list_mine("published", client_id="cid-other", nickname="Tester")
        self.assertEqual({item.id for item in mine}, {"test-pub-001", "test-pub-002", "test-pub-003", "8d8ff2ff-4e27-42a8-840e-f73ca3c95f22"})

    def test_list_mine_rejects_unknown_group(self) -> None:
        with self.assertRaises(ValueError):
            self.repo.list_mine("bogus", client_id="c", nickname="n")

    def test_count_by_group(self) -> None:
        counts = self.repo.count_by_group(client_id="cid-other", nickname="Tester")
        self.assertEqual(counts["published"], 4)
        self.assertEqual(counts["saved_public"], 0)
        self.assertEqual(counts["saved_private"], 0)
        self.assertEqual(counts["resolved"], 0)


class RepoWriteTest(unittest.TestCase):
    """写路径与私密隔离。"""

    def setUp(self) -> None:
        self._ctx = temp_workspace()
        self.workspace = self._ctx.__enter__()
        self.db_path = os.path.join(self.workspace, "emotion_bubble.db")
        ensure_client_schema(self.db_path, os.path.join(self.workspace, "backup"))
        self.repo = Repo(self.db_path)

    def tearDown(self) -> None:
        self.repo.close()
        self._ctx.__exit__(None, None, None)

    def test_upsert_and_read(self) -> None:
        self.repo.upsert_bubble(_bubble("b1"))
        bubble = self.repo.get_bubble("b1")
        self.assertIsNotNone(bubble)
        self.assertEqual(bubble.author_client_id, "cid-tester")

    def test_private_bubble_not_in_public_list(self) -> None:
        self.repo.upsert_bubble(_bubble("b1"))
        self.repo.upsert_bubble(_bubble("b2"))
        self.repo.mark_bubble_status("b2", "saved_private", version=2, at="2026-07-21T18:00:00")
        public_ids = {item.id for item in self.repo.list_public()}
        self.assertEqual(public_ids, {"b1"})
        mine = self.repo.list_mine("saved_private", client_id="cid-tester", nickname="Tester")
        self.assertEqual([item.id for item in mine], ["b2"])

    def test_saved_public_stays_in_wall(self) -> None:
        self.repo.upsert_bubble(_bubble("b1"))
        self.repo.mark_bubble_status("b1", "saved_public", version=2, at="2026-07-21T18:00:00")
        self.assertEqual([item.id for item in self.repo.list_public()], ["b1"])
        bubble = self.repo.get_bubble("b1")
        self.assertEqual(bubble.saved_at, "2026-07-21T18:00:00")
        self.assertIsNone(bubble.resolved_at)

    def test_resolved_hidden_from_wall(self) -> None:
        self.repo.upsert_bubble(_bubble("b1"))
        self.repo.mark_bubble_status("b1", "resolved", version=2, at="2026-07-21T18:00:00")
        self.assertEqual(self.repo.list_public(), [])
        mine = self.repo.list_mine("resolved", client_id="cid-tester", nickname="Tester")
        self.assertEqual([item.id for item in mine], ["b1"])
        self.assertEqual(self.repo.get_bubble("b1").resolved_at, "2026-07-21T18:00:00")

    def test_upsert_preserves_pending_sync_state(self) -> None:
        self.repo.upsert_bubble(_bubble("b1", sync_state="pending"))
        self.repo.upsert_bubble(_bubble("b1", sync_state="synced", content="改过"))
        bubble = self.repo.get_bubble("b1")
        self.assertEqual(bubble.sync_state, "pending")
        self.assertEqual(bubble.content, "改过")

    def test_set_bubble_sync_state(self) -> None:
        self.repo.upsert_bubble(_bubble("b1", sync_state="pending"))
        self.repo.set_bubble_sync_state("b1", "synced")
        self.assertEqual(self.repo.get_bubble("b1").sync_state, "synced")

    def test_reply_requires_op_id(self) -> None:
        self.repo.upsert_bubble(_bubble("b1"))
        with self.assertRaises(ValueError):
            self.repo.upsert_reply(
                Reply(
                    id=0,
                    bubble_id="b1",
                    replier_nickname="A",
                    replier_client_id="cid-a",
                    content="hi",
                    created_at="2026-07-21T18:00:00",
                    op_id="",
                )
            )

    def test_upsert_reply_is_idempotent_by_op_id(self) -> None:
        self.repo.upsert_bubble(_bubble("b1"))
        reply = Reply(
            id=0,
            bubble_id="b1",
            replier_nickname="A",
            replier_client_id="cid-a",
            content="hi",
            created_at="2026-07-21T18:00:00",
            op_id="op-1",
        )
        self.repo.upsert_reply(reply)
        self.repo.upsert_reply(reply)
        self.assertEqual(len(self.repo.list_replies("b1")), 1)
        self.assertEqual(self.repo.get_bubble("b1").reply_count, 1)

    def test_my_reply_count_by_client_id_and_nickname(self) -> None:
        self.repo.upsert_bubble(_bubble("b1"))
        self.repo.upsert_reply(
            Reply(
                id=0,
                bubble_id="b1",
                replier_nickname="A",
                replier_client_id="cid-a",
                content="hi",
                created_at="2026-07-21T18:00:00",
                op_id="op-1",
            )
        )
        self.repo.upsert_reply(
            Reply(
                id=0,
                bubble_id="b1",
                replier_nickname="Old",
                replier_client_id="",
                content="旧数据",
                created_at="2026-07-21T18:01:00",
                op_id="legacy-rep-9",
            )
        )
        self.assertEqual(self.repo.my_reply_count("b1", "cid-a", "A"), 1)
        self.assertEqual(self.repo.my_reply_count("b1", "cid-x", "Old"), 1)
        self.assertEqual(self.repo.my_reply_count("b1", "cid-x", "Nobody"), 0)

    def test_delete_reply_by_op(self) -> None:
        self.repo.upsert_bubble(_bubble("b1"))
        self.repo.upsert_reply(
            Reply(
                id=0,
                bubble_id="b1",
                replier_nickname="A",
                replier_client_id="cid-a",
                content="hi",
                created_at="2026-07-21T18:00:00",
                op_id="op-1",
            )
        )
        self.assertEqual(self.repo.delete_reply_by_op("op-1"), 1)
        self.assertEqual(self.repo.list_replies("b1"), [])

    def test_chat_peer_filter(self) -> None:
        self.repo.upsert_bubble(_bubble("b1"))
        for index, (sender, receiver) in enumerate(
            [("Tester", "Friend"), ("Friend", "Tester"), ("Tester", "Other")]
        ):
            self.repo.upsert_chat(
                ChatMessage(
                    id=0,
                    bubble_id="b1",
                    sender_nickname=sender,
                    sender_client_id=f"cid-{sender}",
                    receiver_nickname=receiver,
                    content=f"m{index}",
                    created_at=f"2026-07-21T18:0{index}:00",
                    op_id=f"op-{index}",
                )
            )
        self.assertEqual(len(self.repo.list_chats("b1")), 3)
        self.assertEqual(len(self.repo.list_chats("b1", peer="Friend")), 2)

    def test_has_chatted(self) -> None:
        self.repo.upsert_bubble(_bubble("b1"))
        self.repo.upsert_chat(
            ChatMessage(
                id=0,
                bubble_id="b1",
                sender_nickname="A",
                sender_client_id="cid-a",
                receiver_nickname="Tester",
                content="hi",
                created_at="2026-07-21T18:00:00",
                op_id="op-1",
            )
        )
        self.assertTrue(self.repo.has_chatted("b1", "cid-a", "A"))
        self.assertFalse(self.repo.has_chatted("b1", "cid-b", "B"))

    def test_pending_count(self) -> None:
        self.assertEqual(self.repo.pending_count(), 0)
        self.repo.execute(
            "INSERT INTO outbox (op_id, op_type, bubble_id, payload, base_version, state, "
            "attempts, next_attempt_at, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("op-1", "publish", "b1", "{}", 0, "pending", 0, "2026-07-21T18:00:00", "2026-07-21T18:00:00", "2026-07-21T18:00:00"),
        )
        self.assertEqual(self.repo.pending_count(), 1)

    def test_meta_roundtrip(self) -> None:
        self.assertEqual(self.repo.get_meta("missing", "fallback"), "fallback")
        self.repo.set_meta("last_pull_cursor", "abc")
        self.assertEqual(self.repo.get_meta("last_pull_cursor"), "abc")
        self.repo.set_meta("last_pull_cursor", "def")
        self.assertEqual(self.repo.get_meta("last_pull_cursor"), "def")

    def test_transaction_rollback(self) -> None:
        with self.assertRaises(RuntimeError):
            with self.repo.transaction() as conn:
                conn.execute(
                    "INSERT INTO bubbles (id, author_nickname, author_client_id, content, "
                    "emotion_tag, reply_mode, status, version, created_at, sync_state) "
                    "VALUES ('b-rollback', 'n', '', 'c', '开心', 'single', 'active', 1, "
                    "'2026-07-21T18:00:00', 'synced')"
                )
                raise RuntimeError("boom")
        self.assertIsNone(self.repo.get_bubble("b-rollback"))

    def test_delete_bubble_cascades(self) -> None:
        self.repo.upsert_bubble(_bubble("b1"))
        self.repo.upsert_reply(
            Reply(
                id=0,
                bubble_id="b1",
                replier_nickname="A",
                replier_client_id="cid-a",
                content="hi",
                created_at="2026-07-21T18:00:00",
                op_id="op-1",
            )
        )
        self.assertEqual(self.repo.delete_bubble("b1"), 1)
        conn = connect(self.db_path)
        try:
            row = conn.execute("SELECT COUNT(*) AS n FROM replies").fetchone()
        finally:
            close_quietly(conn)
        self.assertEqual(row["n"], 0)

    def test_from_default_path_creates_schema(self) -> None:
        path = os.path.join(self.workspace, "second.db")
        repo = Repo.from_default_path(path, os.path.join(self.workspace, "backup2"))
        try:
            self.assertEqual(repo.list_public(), [])
        finally:
            repo.close()


if __name__ == "__main__":
    unittest.main()
