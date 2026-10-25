"""T-07 验收：离线队列状态机、退避、幂等重放、Q-4 冲突、端到端同步。"""

from __future__ import annotations

import ast
import os
import unittest
import uuid
from pathlib import Path

from src.core.api import ApiResult
from src.core.config import AppConfig
from src.core.constants import OUTBOX_MAX_ATTEMPTS
from src.core.migrations import ensure_client_schema
from src.core.models import Bubble
from src.core.outbox import Outbox, backoff_seconds
from src.core.repo import Repo
from src.core.sync import SyncEngine
from src.core.timeutil import now_iso, shift_seconds
from tests.helpers import FakeApi, temp_workspace


def _make_repo(workspace: str, name: str = "client.db") -> tuple[Repo, str]:
    """建一个 v2 客户端库并返回 `(repo, db_path)`。"""
    db_path = os.path.join(workspace, name)
    ensure_client_schema(db_path, os.path.join(workspace, "backup"))
    return Repo(db_path), db_path


def _dto(bubble_id: str, **kwargs) -> dict:
    """构造一个合法的 BubbleDTO。"""
    base = {
        "id": bubble_id,
        "author": "A",
        "author_client_id": "cid-a",
        "content": "内容",
        "emotion_tag": "开心",
        "reply_mode": "single",
        "status": "active",
        "version": 2,
        "created_at": "2026-07-21T18:00:00",
        "resolved_at": None,
        "saved_at": None,
        "reply_count": 1,
        "chat_count": 0,
    }
    base.update(kwargs)
    return base


class OutboxStateMachineTest(unittest.TestCase):
    """§7.1 / §7.3：状态流转与退避。"""

    def setUp(self) -> None:
        self._ctx = temp_workspace()
        self.workspace = self._ctx.__enter__()
        self.repo, _ = _make_repo(self.workspace)
        self.outbox = Outbox(self.repo)

    def tearDown(self) -> None:
        self.repo.close()
        self._ctx.__exit__(None, None, None)

    def test_enqueue_initial_state(self) -> None:
        item = self.outbox.enqueue(
            op_type="publish", bubble_id="b1", payload={"content": "x"}
        )
        self.assertEqual(item.state, "pending")
        self.assertEqual(item.attempts, 0)
        self.assertEqual(item.base_version, 0)
        self.assertTrue(item.op_id)
        self.assertEqual(item.payload, '{"content": "x"}')

    def test_enqueue_rejects_unknown_type(self) -> None:
        with self.assertRaises(ValueError):
            self.outbox.enqueue(op_type="delete", bubble_id="b1", payload={})

    def test_enqueue_rejects_empty_bubble(self) -> None:
        with self.assertRaises(ValueError):
            self.outbox.enqueue(op_type="publish", bubble_id="", payload={})

    def test_next_batch_is_fifo(self) -> None:
        for index in range(3):
            self.outbox.enqueue(op_type="reply", bubble_id=f"b{index}", payload={})
        batch = self.outbox.next_batch()
        self.assertEqual([item.bubble_id for item in batch], ["b0", "b1", "b2"])

    def test_next_batch_respects_limit(self) -> None:
        for index in range(5):
            self.outbox.enqueue(op_type="reply", bubble_id=f"b{index}", payload={})
        self.assertEqual(len(self.outbox.next_batch(limit=2)), 2)

    def test_mark_syncing_increments_attempts(self) -> None:
        item = self.outbox.enqueue(op_type="reply", bubble_id="b1", payload={})
        self.outbox.mark_syncing(item.op_id)
        refreshed = self.outbox.get(item.op_id)
        self.assertEqual(refreshed.state, "syncing")
        self.assertEqual(refreshed.attempts, 1)

    def test_mark_retry_backoff_delays(self) -> None:
        item = self.outbox.enqueue(op_type="reply", bubble_id="b1", payload={})
        self.outbox.mark_syncing(item.op_id)
        base = now_iso()
        self.outbox.mark_retry(item.op_id, error="boom", now=base)
        refreshed = self.outbox.get(item.op_id)
        self.assertEqual(refreshed.state, "pending")
        self.assertEqual(refreshed.attempts, 1)
        self.assertEqual(refreshed.next_attempt_at, shift_seconds(base, 5))
        self.assertEqual(refreshed.last_error, "boom")

    def test_backoff_sequence(self) -> None:
        expected = [5, 10, 20, 40, 80, 160, 300, 300, 300]
        self.assertEqual([backoff_seconds(i) for i in range(1, 10)], expected)

    def test_next_batch_skips_future_items(self) -> None:
        item = self.outbox.enqueue(op_type="reply", bubble_id="b1", payload={})
        self.outbox.mark_syncing(item.op_id)
        base = now_iso()
        self.outbox.mark_retry(item.op_id, error="boom", now=base)
        self.assertEqual(self.outbox.next_batch(now=base), [])
        self.assertEqual(len(self.outbox.next_batch(now=shift_seconds(base, 10))), 1)

    def test_mark_failed_after_max_attempts(self) -> None:
        item = self.outbox.enqueue(op_type="reply", bubble_id="b1", payload={})
        for _ in range(OUTBOX_MAX_ATTEMPTS):
            self.outbox.mark_syncing(item.op_id)
            self.outbox.mark_retry(item.op_id, error="boom")
        self.assertEqual(self.outbox.get(item.op_id).state, "failed")

    def test_reset_stale_syncing(self) -> None:
        item = self.outbox.enqueue(op_type="reply", bubble_id="b1", payload={})
        self.outbox.mark_syncing(item.op_id)
        self.assertEqual(self.outbox.pending_count(), 1)
        self.assertEqual(self.outbox.reset_stale_syncing(), 1)
        refreshed = self.outbox.get(item.op_id)
        self.assertEqual(refreshed.state, "pending")
        self.assertEqual(self.outbox.reset_stale_syncing(), 0)

    def test_pending_count_excludes_done(self) -> None:
        item = self.outbox.enqueue(op_type="reply", bubble_id="b1", payload={})
        self.outbox.mark_syncing(item.op_id)
        self.outbox.mark_done(item.op_id)
        self.assertEqual(self.outbox.pending_count(), 0)

    def test_delete_pending_for_bubble(self) -> None:
        self.outbox.enqueue(op_type="reply", bubble_id="b1", payload={})
        self.outbox.enqueue(op_type="chat", bubble_id="b1", payload={})
        self.outbox.enqueue(op_type="reply", bubble_id="b2", payload={})
        removed = self.outbox.delete_pending_for_bubble("b1", ("reply", "chat"))
        self.assertEqual(removed, 2)
        self.assertEqual(len(self.outbox.list_for_bubble("b2")), 1)

    def test_purge(self) -> None:
        item = self.outbox.enqueue(op_type="reply", bubble_id="b1", payload={})
        self.outbox.mark_syncing(item.op_id)
        self.outbox.mark_done(item.op_id, now="2026-01-01T00:00:00")
        removed = self.outbox.purge(
            done_before="2026-06-01T00:00:00", failed_before="2026-06-01T00:00:00"
        )
        self.assertEqual(removed, 1)
        self.assertIsNone(self.outbox.get(item.op_id))


class EngineOfflineTest(unittest.TestCase):
    """离线优先：断网写入仍立即可见，恢复后重放。"""

    def setUp(self) -> None:
        self._ctx = temp_workspace()
        self.workspace = self._ctx.__enter__()
        self.repo, _ = _make_repo(self.workspace)
        self.outbox = Outbox(self.repo)
        self.api = FakeApi(client_id="cid-a")
        self.config = AppConfig(client_id="cid-a", nickname="A", server_url="http://test")
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
        self._ctx.__exit__(None, None, None)

    def _event_names(self) -> list[str]:
        return [name for name, _ in self.events]

    def test_publish_offline_is_visible_and_queued(self) -> None:
        self.api.network_down = True
        bubble_id = self.engine.publish(content="断网发布", emotion_tag="孤独", reply_mode="single")
        bubble = self.repo.get_bubble(bubble_id)
        self.assertIsNotNone(bubble)
        self.assertEqual(bubble.content, "断网发布")
        self.assertEqual(bubble.sync_state, "pending")
        self.assertEqual(self.outbox.pending_count(), 1)
        self.assertIn("bubble_updated", self._event_names())
        self.assertIn("outbox_changed", self._event_names())

    def test_publish_then_recover_syncs(self) -> None:
        self.api.network_down = True
        bubble_id = self.engine.publish(content="恢复后同步", emotion_tag="开心", reply_mode="single")
        self.api.network_down = False
        self.api.plan_for("create_bubble", [ApiResult(True, 201, "ok", "", _dto(bubble_id))])
        done = self.engine.flush(now=shift_seconds(now_iso(), 60))
        self.assertEqual(done, 1)
        self.assertEqual(self.repo.get_bubble(bubble_id).sync_state, "synced")
        self.assertEqual(self.outbox.pending_count(), 0)
        # 第一次断网已计 1 次，恢复后重放再计 1 次
        self.assertEqual(self.outbox.list_by_state("done")[0].attempts, 2)

    def test_reply_offline_then_recover(self) -> None:
        self.repo.upsert_bubble(
            Bubble(
                id="b1",
                author_nickname="A",
                author_client_id="cid-a",
                content="x",
                emotion_tag="开心",
                reply_mode="dialog",
                status="active",
                created_at="2026-07-21T18:00:00",
            )
        )
        self.api.network_down = True
        op_id = self.engine.reply(bubble_id="b1", content="离线回复")
        replies = self.repo.list_replies("b1")
        self.assertEqual(len(replies), 1)
        self.assertEqual(replies[0].sync_state, "pending")

        self.api.network_down = False
        self.api.plan_for(
            "reply",
            [
                ApiResult(
                    True,
                    201,
                    "ok",
                    "",
                    {
                        "reply": {
                            "id": 1,
                            "bubble_id": "b1",
                            "replier": "A",
                            "replier_client_id": "cid-a",
                            "content": "离线回复",
                            "created_at": "2026-07-21T18:01:00",
                            "op_id": op_id,
                        },
                        "reply_count": 1,
                    },
                )
            ],
        )
        self.engine.flush(now=shift_seconds(now_iso(), 60))
        replies = self.repo.list_replies("b1")
        self.assertEqual(len(replies), 1, "重放后不得出现重复回复")
        self.assertEqual(replies[0].sync_state, "synced")

    def test_retry_on_429_keeps_pending(self) -> None:
        self.api.plan_for(
            "create_bubble", [ApiResult(False, 429, "rate_limited", "慢一点", None)]
        )
        bubble_id = self.engine.publish(content="x", emotion_tag="开心", reply_mode="single")
        item = self.outbox.list_for_bubble(bubble_id)[0]
        self.assertEqual(item.state, "pending")
        self.assertEqual(item.attempts, 1)
        self.assertIn("429", item.last_error or "")

    def test_retry_on_500_keeps_pending(self) -> None:
        self.api.plan_for(
            "create_bubble", [ApiResult(False, 500, "server_error", "服务器打盹", None)]
        )
        bubble_id = self.engine.publish(content="x", emotion_tag="开心", reply_mode="single")
        self.assertEqual(self.outbox.list_for_bubble(bubble_id)[0].state, "pending")

    def test_reply_conflict_drops_optimistic_row(self) -> None:
        self.repo.upsert_bubble(
            Bubble(
                id="b1",
                author_nickname="A",
                author_client_id="cid-a",
                content="x",
                emotion_tag="开心",
                reply_mode="single",
                status="active",
                created_at="2026-07-21T18:00:00",
            )
        )
        self.api.plan_for(
            "reply",
            [
                ApiResult(True, 201, "ok", "", {"reply": {"id": 1, "bubble_id": "b1", "replier": "A", "replier_client_id": "cid-a", "content": "第一次", "created_at": "2026-07-21T18:01:00"}, "reply_count": 1}),
                ApiResult(False, 409, "conflict", "这个泡泡只能回复一次哦～", None),
            ],
        )
        self.api.plan_for("get_bubble", [ApiResult(True, 200, "ok", "", _dto("b1", reply_count=1))])
        self.engine.reply(bubble_id="b1", content="第一次")
        self.engine.reply(bubble_id="b1", content="第二次")
        self.assertEqual(len(self.repo.list_replies("b1")), 1, "409 后必须删除本地乐观回复")
        failed = self.outbox.list_by_state("failed")
        self.assertEqual(len(failed), 1)
        self.assertIn(("action_failed", ("reply", "这个泡泡只能回复一次哦～", 409)), self.events)

    def test_forbidden_reply_marks_failed(self) -> None:
        self.repo.upsert_bubble(
            Bubble(
                id="b1",
                author_nickname="A",
                author_client_id="cid-a",
                content="x",
                emotion_tag="开心",
                reply_mode="dialog",
                status="active",
                created_at="2026-07-21T18:00:00",
            )
        )
        self.api.plan_for(
            "reply", [ApiResult(False, 403, "forbidden", "这个泡泡已经结束啦", None)]
        )
        self.engine.reply(bubble_id="b1", content="太晚了")
        self.assertEqual(self.repo.list_replies("b1"), [])
        self.assertEqual(len(self.outbox.list_by_state("failed")), 1)
        self.assertIn(("action_failed", ("reply", "这个泡泡已经结束啦", 403)), self.events)

    def test_resolve_conflict_author_wins(self) -> None:
        self.repo.upsert_bubble(
            Bubble(
                id="b1",
                author_nickname="A",
                author_client_id="cid-a",
                content="x",
                emotion_tag="开心",
                reply_mode="single",
                status="active",
                created_at="2026-07-21T18:00:00",
            )
        )
        self.api.plan_for(
            "resolve", [ApiResult(False, 409, "conflict", "数据刚刚更新过", None)]
        )
        self.engine.resolve(bubble_id="b1")
        self.assertEqual(self.repo.get_bubble("b1").status, "resolved")
        self.assertEqual(len(self.outbox.list_by_state("done")), 1, "作者 409 必须视为成功")
        self.assertEqual(self.repo.get_bubble("b1").sync_state, "synced")

    def test_save_private_cancels_unsynced_publish(self) -> None:
        """Q-3：从未上传过的泡泡私密保存后不得上传。"""
        self.api.network_down = True
        bubble_id = self.engine.publish(content="不想上传", emotion_tag="焦虑", reply_mode="single")
        self.assertEqual(self.outbox.pending_count(), 1)
        op_id = self.engine.save(bubble_id=bubble_id, is_public=False)
        self.assertEqual(op_id, "")
        self.assertEqual(self.outbox.list_for_bubble(bubble_id), [])
        bubble = self.repo.get_bubble(bubble_id)
        self.assertEqual(bubble.status, "saved_private")
        self.assertEqual(bubble.sync_state, "synced")
        self.assertNotIn("save", self.api.call_names())

    def test_save_private_cancels_pending_chat_and_reply(self) -> None:
        self.repo.upsert_bubble(
            Bubble(
                id="b1",
                author_nickname="A",
                author_client_id="cid-a",
                content="x",
                emotion_tag="开心",
                reply_mode="dialog",
                status="active",
                version=1,
                created_at="2026-07-21T18:00:00",
            )
        )
        self.api.network_down = True
        self.engine.reply(bubble_id="b1", content="别上传")
        self.engine.chat(bubble_id="b1", receiver="B", content="也别上传")
        self.assertEqual(self.outbox.pending_count(), 2)
        self.api.network_down = False
        self.api.plan_for("save", [ApiResult(True, 200, "ok", "", _dto("b1", status="saved_private"))])
        self.engine.save(bubble_id="b1", is_public=False)
        remaining = self.outbox.list_for_bubble("b1")
        self.assertTrue(all(item.op_type == "save" for item in remaining))
        self.assertEqual(self.repo.list_replies("b1")[0].sync_state, "failed")
        self.assertEqual(self.repo.list_chats("b1")[0].sync_state, "failed")

    def test_chat_on_private_bubble_refused(self) -> None:
        self.repo.upsert_bubble(
            Bubble(
                id="b1",
                author_nickname="A",
                author_client_id="cid-a",
                content="x",
                emotion_tag="开心",
                reply_mode="dialog",
                status="saved_private",
                created_at="2026-07-21T18:00:00",
            )
        )
        op_id = self.engine.chat(bubble_id="b1", receiver="B", content="秘密")
        self.assertEqual(op_id, "")
        self.assertEqual(self.outbox.list_for_bubble("b1"), [])
        self.assertIn("action_failed", self._event_names())

    def test_flush_skips_chat_for_private_bubble(self) -> None:
        """R5：私密泡泡的 chat 项不得发出去。"""
        self.repo.upsert_bubble(
            Bubble(
                id="b1",
                author_nickname="A",
                author_client_id="cid-a",
                content="x",
                emotion_tag="开心",
                reply_mode="dialog",
                status="saved_private",
                created_at="2026-07-21T18:00:00",
            )
        )
        self.outbox.enqueue(op_type="chat", bubble_id="b1", payload={"receiver": "B", "content": "x"})
        self.engine.flush()
        self.assertNotIn("post_chat", self.api.call_names())
        self.assertEqual(len(self.outbox.list_by_state("failed")), 1)

    def test_online_flag_requires_two_failures(self) -> None:
        self.api.network_down = True
        self.engine.pull()
        self.assertTrue(self.engine.is_online, "一次失败不应立即判定离线")
        self.engine.pull()
        self.assertFalse(self.engine.is_online)
        self.assertIn(("online_changed", False), self.events)

    def test_pull_upserts_bubbles(self) -> None:
        self.api.plan_for(
            "list_bubbles",
            [
                ApiResult(
                    True,
                    200,
                    "ok",
                    "",
                    {"items": [_dto("remote-1")], "total": 1, "has_more": False, "next_cursor": None},
                )
            ],
        )
        self.assertTrue(self.engine.pull())
        self.assertIsNotNone(self.repo.get_bubble("remote-1"))
        self.assertTrue(self.engine.is_online)


if __name__ == "__main__":
    unittest.main()
