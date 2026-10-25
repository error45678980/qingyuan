"""T-02 验收：具名行转换、缺列抛错、列序错位不得串字段、游标往返。"""

from __future__ import annotations

import unittest

from src.core.dto import (
    bubble_from_dto,
    bubble_to_dto,
    chat_from_dto,
    chat_to_dto,
    reply_from_dto,
    reply_to_dto,
)
from src.core.errors import InvalidCursorError, SchemaMismatchError
from src.core.models import (
    Bubble,
    ChatMessage,
    OutboxItem,
    Reply,
    decode_cursor,
    encode_cursor,
    normalize_limit,
    row_to_bubble,
    row_to_chat,
    row_to_outbox,
    row_to_reply,
    rows_to_bubbles,
)
from tests.helpers import connect, copy_legacy_client, temp_workspace


class RowToBubbleTest(unittest.TestCase):
    """`row_to_bubble` 的正确性与严格性。"""

    def _full_row(self) -> dict:
        return {
            "id": "test-pub-003",
            "author_nickname": "Tester",
            "author_client_id": "cid-1",
            "content": "隐私测试",
            "emotion_tag": "孤独",
            "reply_mode": "dialog",
            "status": "active",
            "version": 3,
            "created_at": "2026-07-21T17:20:00",
            "resolved_at": None,
            "saved_at": None,
            "sync_state": "pending",
            "reply_count": 2,
            "chat_count": 5,
        }

    def test_full_row_maps_every_field(self) -> None:
        bubble = row_to_bubble(self._full_row())
        self.assertEqual(bubble.id, "test-pub-003")
        self.assertEqual(bubble.author_nickname, "Tester")
        self.assertEqual(bubble.author_client_id, "cid-1")
        self.assertEqual(bubble.content, "隐私测试")
        self.assertEqual(bubble.emotion_tag, "孤独")
        self.assertEqual(bubble.reply_mode, "dialog")
        self.assertEqual(bubble.status, "active")
        self.assertEqual(bubble.version, 3)
        self.assertEqual(bubble.created_at, "2026-07-21T17:20:00")
        self.assertIsNone(bubble.resolved_at)
        self.assertIsNone(bubble.saved_at)
        self.assertEqual(bubble.sync_state, "pending")
        self.assertEqual(bubble.reply_count, 2)
        self.assertEqual(bubble.chat_count, 5)

    def test_optional_columns_default(self) -> None:
        row = {
            "id": "x",
            "author_nickname": "n",
            "author_client_id": "",
            "content": "c",
            "emotion_tag": "开心",
            "reply_mode": "single",
            "status": "active",
            "created_at": "2026-07-21T16:00:00",
        }
        bubble = row_to_bubble(row)
        self.assertEqual(bubble.version, 1)
        self.assertEqual(bubble.reply_count, 0)
        self.assertEqual(bubble.chat_count, 0)
        self.assertEqual(bubble.sync_state, "synced")
        self.assertIsNone(bubble.resolved_at)
        self.assertIsNone(bubble.saved_at)

    def test_missing_author_client_id_raises(self) -> None:
        row = self._full_row()
        del row["author_client_id"]
        with self.assertRaises(SchemaMismatchError):
            row_to_bubble(row)

    def test_missing_content_raises(self) -> None:
        row = self._full_row()
        del row["content"]
        with self.assertRaises(SchemaMismatchError):
            row_to_bubble(row)

    def test_missing_every_required_column_raises(self) -> None:
        required = (
            "id",
            "author_nickname",
            "author_client_id",
            "content",
            "emotion_tag",
            "reply_mode",
            "status",
            "created_at",
        )
        for name in required:
            row = self._full_row()
            del row[name]
            with self.assertRaises(SchemaMismatchError, msg=f"缺 {name} 时未抛错"):
                row_to_bubble(row)

    def test_no_silent_fallback_for_author_client_id(self) -> None:
        """`author_client_id` 缺列时不得用 `or ""` 兜底。"""
        row = self._full_row()
        row.pop("author_client_id")
        with self.assertRaises(SchemaMismatchError):
            row_to_bubble(row)

    def test_tuple_input_raises(self) -> None:
        with self.assertRaises(SchemaMismatchError):
            row_to_bubble(("id", "author"))  # type: ignore[arg-type]


class WrongColumnOrderTest(unittest.TestCase):
    """P0-1 回归：旧库列序（author_node_id 在末尾）不得串字段。"""

    def test_legacy_order_does_not_shift_fields(self) -> None:
        with temp_workspace() as workspace:
            db_path = f"{workspace}/legacy_client.db"
            copy_legacy_client(db_path)
            conn = connect(db_path)
            try:
                rows = conn.execute(
                    "SELECT id, author_nickname, content, emotion_tag, reply_mode, "
                    "status, created_at, resolved_at, author_node_id FROM bubbles "
                    "WHERE id = ?",
                    ("test-pub-003",),
                ).fetchall()
                self.assertEqual(len(rows), 1)
                raw = rows[0]
                bubble = row_to_bubble(
                    {
                        "id": raw["id"],
                        "author_nickname": raw["author_nickname"],
                        "author_client_id": raw["author_node_id"] or "",
                        "content": raw["content"],
                        "emotion_tag": raw["emotion_tag"],
                        "reply_mode": raw["reply_mode"],
                        "status": raw["status"],
                        "created_at": raw["created_at"],
                        "resolved_at": raw["resolved_at"],
                    }
                )
            finally:
                conn.close()
        # v1 错位时这里会拿到 reply_mode / status
        self.assertEqual(bubble.content, "隐私测试")
        self.assertEqual(bubble.emotion_tag, "孤独")
        self.assertEqual(bubble.reply_mode, "dialog")
        self.assertEqual(bubble.status, "active")
        self.assertEqual(bubble.created_at, "2026-07-21T17:20:00")


class BubblePropertyTest(unittest.TestCase):
    """派生属性。"""

    def _bubble(self, **kwargs) -> Bubble:
        base = dict(
            id="b1",
            author_nickname="n",
            author_client_id="c",
            content="x",
            emotion_tag="开心",
            reply_mode="single",
            status="active",
            created_at="2026-07-21T17:20:00",
        )
        base.update(kwargs)
        return Bubble(**base)

    def test_is_public_visible(self) -> None:
        self.assertTrue(self._bubble(status="active").is_public_visible)
        self.assertTrue(self._bubble(status="saved_public").is_public_visible)
        self.assertFalse(self._bubble(status="resolved").is_public_visible)
        self.assertFalse(self._bubble(status="saved_private").is_public_visible)

    def test_content_preview_truncates(self) -> None:
        short = self._bubble(content="短内容")
        self.assertEqual(short.content_preview, "短内容")
        long_text = "字" * 200
        preview = self._bubble(content=long_text).content_preview
        self.assertEqual(len(preview), 81)
        self.assertTrue(preview.endswith("…"))

    def test_content_preview_collapses_newlines(self) -> None:
        bubble = self._bubble(content="第一行\n第二行")
        self.assertEqual(bubble.content_preview, "第一行 第二行")

    def test_display_time_format(self) -> None:
        bubble = self._bubble(created_at="2026-07-21T17:20:03")
        self.assertEqual(bubble.display_time, "2026-07-21 17:20")
        self.assertEqual(bubble.short_time, "07-21 17:20")

    def test_display_time_handles_microseconds(self) -> None:
        bubble = self._bubble(created_at="2026-07-21T16:22:40.905472")
        self.assertEqual(bubble.display_time, "2026-07-21 16:22")

    def test_diff_key_changes_with_version(self) -> None:
        first = self._bubble(version=1)
        second = self._bubble(version=2)
        self.assertNotEqual(first.diff_key, second.diff_key)
        self.assertEqual(first.diff_key, self._bubble(version=1).diff_key)


class ReplyChatRowTest(unittest.TestCase):
    """回复与对话的行转换。"""

    def test_row_to_reply(self) -> None:
        reply = row_to_reply(
            {
                "id": 7,
                "bubble_id": "b1",
                "replier_nickname": "Tester",
                "replier_client_id": "cid",
                "content": "你好",
                "created_at": "2026-07-21T16:39:35.447448",
                "op_id": "op-1",
                "sync_state": "synced",
            }
        )
        self.assertIsInstance(reply, Reply)
        self.assertEqual(reply.id, 7)
        self.assertEqual(reply.replier_client_id, "cid")
        self.assertEqual(reply.display_time, "2026-07-21 16:39")

    def test_row_to_reply_missing_column_raises(self) -> None:
        with self.assertRaises(SchemaMismatchError):
            row_to_reply(
                {
                    "id": 7,
                    "bubble_id": "b1",
                    "replier_nickname": "Tester",
                    "content": "你好",
                    "created_at": "2026-07-21T16:39:35",
                }
            )

    def test_row_to_chat(self) -> None:
        chat = row_to_chat(
            {
                "id": 2,
                "bubble_id": "test-pub-003",
                "sender_nickname": "Tester",
                "sender_client_id": "cid-a",
                "receiver_nickname": "Friend",
                "receiver_client_id": None,
                "content": "私聊内容",
                "created_at": "2026-07-21T17:21:00",
                "op_id": "op-2",
            }
        )
        self.assertIsInstance(chat, ChatMessage)
        self.assertEqual(chat.receiver_nickname, "Friend")
        self.assertIsNone(chat.receiver_client_id)

    def test_row_to_outbox(self) -> None:
        item = row_to_outbox(
            {
                "id": 3,
                "op_id": "op-3",
                "op_type": "publish",
                "bubble_id": "b1",
                "payload": "{}",
                "base_version": 2,
                "state": "pending",
                "attempts": 1,
                "next_attempt_at": "2026-07-21T17:20:00",
                "last_error": None,
                "created_at": "2026-07-21T17:19:00",
                "updated_at": "2026-07-21T17:19:30",
            }
        )
        self.assertIsInstance(item, OutboxItem)
        self.assertEqual(item.base_version, 2)
        self.assertEqual(item.attempts, 1)
        self.assertIsNone(item.last_error)

    def test_rows_to_bubbles(self) -> None:
        rows = [
            {
                "id": f"b{i}",
                "author_nickname": "n",
                "author_client_id": "",
                "content": "c",
                "emotion_tag": "开心",
                "reply_mode": "single",
                "status": "active",
                "created_at": "2026-07-21T16:00:00",
            }
            for i in range(3)
        ]
        bubbles = rows_to_bubbles(rows)
        self.assertEqual([b.id for b in bubbles], ["b0", "b1", "b2"])


class DtoTest(unittest.TestCase):
    """传输层序列化往返。"""

    def _bubble(self) -> Bubble:
        return Bubble(
            id="test-pub-003",
            author_nickname="Tester",
            author_client_id="",
            content="隐私测试",
            emotion_tag="孤独",
            reply_mode="dialog",
            status="active",
            version=1,
            created_at="2026-07-21T17:20:00",
        )

    def test_bubble_to_dto_has_contract_keys(self) -> None:
        dto = bubble_to_dto(
            self._bubble(), is_owner=True, can_reply=False, can_chat=True, legacy_owner=True
        )
        for key in (
            "id",
            "author",
            "author_client_id",
            "content",
            "emotion_tag",
            "reply_mode",
            "status",
            "version",
            "created_at",
            "resolved_at",
            "saved_at",
            "reply_count",
            "chat_count",
            "is_owner",
            "can_reply",
            "can_chat",
            "legacy_owner",
        ):
            self.assertIn(key, dto)
        self.assertEqual(dto["author"], "Tester")
        self.assertNotIn("author_nickname", dto)
        self.assertTrue(dto["is_owner"])
        self.assertFalse(dto["can_reply"])
        self.assertTrue(dto["legacy_owner"])

    def test_bubble_dto_roundtrip(self) -> None:
        original = self._bubble()
        dto = bubble_to_dto(original, is_owner=False, can_reply=True, can_chat=True)
        restored = bubble_from_dto(dto)
        self.assertEqual(restored.id, original.id)
        self.assertEqual(restored.author_nickname, original.author_nickname)
        self.assertEqual(restored.content, original.content)
        self.assertEqual(restored.emotion_tag, original.emotion_tag)
        self.assertEqual(restored.reply_mode, original.reply_mode)
        self.assertEqual(restored.status, original.status)
        self.assertEqual(restored.created_at, original.created_at)

    def test_bubble_from_dto_accepts_legacy_author_key(self) -> None:
        restored = bubble_from_dto({"id": "b1", "author": "Tester", "content": "x"})
        self.assertEqual(restored.author_nickname, "Tester")
        self.assertEqual(restored.status, "active")

    def test_bubble_from_dto_missing_id_raises(self) -> None:
        with self.assertRaises(SchemaMismatchError):
            bubble_from_dto({"author": "Tester", "content": "x"})

    def test_reply_dto_roundtrip(self) -> None:
        reply = Reply(
            id=1,
            bubble_id="b1",
            replier_nickname="Tester",
            replier_client_id="cid",
            content="你好",
            created_at="2026-07-21T16:39:35",
            op_id="op-1",
        )
        dto = reply_to_dto(reply)
        self.assertEqual(dto["replier"], "Tester")
        restored = reply_from_dto(dto)
        self.assertEqual(restored.replier_nickname, "Tester")
        self.assertEqual(restored.op_id, "op-1")

    def test_chat_dto_roundtrip(self) -> None:
        message = ChatMessage(
            id=2,
            bubble_id="b1",
            sender_nickname="Tester",
            sender_client_id="cid-a",
            receiver_nickname="Friend",
            content="hi",
            created_at="2026-07-21T17:21:00",
            op_id="op-2",
        )
        dto = chat_to_dto(message)
        self.assertEqual(dto["sender"], "Tester")
        self.assertEqual(dto["receiver"], "Friend")
        restored = chat_from_dto(dto)
        self.assertEqual(restored.sender_nickname, "Tester")
        self.assertEqual(restored.receiver_nickname, "Friend")


class CursorTest(unittest.TestCase):
    """游标编解码。"""

    def test_roundtrip(self) -> None:
        cursor = encode_cursor("2026-07-21T17:20:00", "test-pub-003")
        self.assertNotIn("=", cursor)
        self.assertEqual(decode_cursor(cursor), ("2026-07-21T17:20:00", "test-pub-003"))

    def test_roundtrip_with_chinese_id(self) -> None:
        cursor = encode_cursor("2026-07-21T17:20:00", "泡泡-中文")
        self.assertEqual(decode_cursor(cursor), ("2026-07-21T17:20:00", "泡泡-中文"))

    def test_invalid_base64_raises(self) -> None:
        with self.assertRaises(InvalidCursorError):
            decode_cursor("!!!not-base64!!!")

    def test_missing_separator_raises(self) -> None:
        import base64

        raw = base64.urlsafe_b64encode(b"no-separator").decode().rstrip("=")
        with self.assertRaises(InvalidCursorError):
            decode_cursor(raw)

    def test_empty_raises(self) -> None:
        with self.assertRaises(InvalidCursorError):
            decode_cursor("")


class NormalizeLimitTest(unittest.TestCase):
    """limit 规整。"""

    def test_default(self) -> None:
        self.assertEqual(normalize_limit(None), 50)
        self.assertEqual(normalize_limit(""), 50)

    def test_clamped_to_max(self) -> None:
        self.assertEqual(normalize_limit(500), 100)

    def test_invalid_raises(self) -> None:
        with self.assertRaises(ValueError):
            normalize_limit("abc")
        with self.assertRaises(ValueError):
            normalize_limit(0)


if __name__ == "__main__":
    unittest.main()
