"""T-06 验收：写操作端点（E06 回复 / E07-E08 对话 / E09-E11 作者管理）。"""

from __future__ import annotations

import unittest

from tests.api_case import PUBLISHER, REPLIER, STRANGER, ApiTestCase, _op


class ReplyTest(ApiTestCase):
    """E06：409 与 403 的关键分支。"""

    def test_reply_ok(self) -> None:
        bubble = self._publish()
        response = self._reply(bubble["id"])
        self.assertEqual(response.status_code, 201)
        data = self._json(response)["data"]
        self.assertEqual(data["reply_count"], 1)
        self.assertEqual(data["reply"]["replier"], "Replier")

    def test_single_mode_second_reply_conflict(self) -> None:
        bubble = self._publish(mode="single")
        self.assertEqual(self._reply(bubble["id"]).status_code, 201)
        second = self._reply(bubble["id"])
        self.assertEqual(second.status_code, 409)
        body = self._json(second)
        self.assertEqual(body["code"], "conflict")
        self.assertIn("只能回复一次", body["message"])

    def test_dialog_mode_allows_repeat(self) -> None:
        bubble = self._publish(mode="dialog")
        self.assertEqual(self._reply(bubble["id"]).status_code, 201)
        self.assertEqual(self._reply(bubble["id"]).status_code, 201)

    def test_author_cannot_reply_own(self) -> None:
        bubble = self._publish()
        response = self._reply(bubble["id"], client_id=PUBLISHER)
        self.assertEqual(response.status_code, 403)

    def test_reply_after_resolved_forbidden(self) -> None:
        bubble = self._publish(mode="dialog")
        self._call(
            "POST",
            f"/api/v2/bubbles/{bubble['id']}/resolve",
            payload={"op_id": _op(), "base_version": 1},
        )
        self.assertEqual(self._reply(bubble["id"]).status_code, 403)

    def test_reply_content_too_long(self) -> None:
        bubble = self._publish(mode="dialog")
        self.assertEqual(
            self._reply(bubble["id"], content="字" * 201).status_code, 400
        )

    def test_reply_empty_content(self) -> None:
        bubble = self._publish(mode="dialog")
        self.assertEqual(self._reply(bubble["id"], content="  ").status_code, 400)

    def test_reply_404(self) -> None:
        self.assertEqual(self._reply("nope").status_code, 404)

    def test_reply_idempotent(self) -> None:
        bubble = self._publish(mode="dialog")
        op_id = _op()
        payload = {"op_id": op_id, "content": "只此一条"}
        first = self._call(
            "POST",
            f"/api/v2/bubbles/{bubble['id']}/reply",
            client_id=REPLIER,
            payload=payload,
        )
        second = self._call(
            "POST",
            f"/api/v2/bubbles/{bubble['id']}/reply",
            client_id=REPLIER,
            payload=payload,
        )
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(self.store.count_replies(bubble["id"]), 1)

    def test_reply_bumps_version(self) -> None:
        bubble = self._publish(mode="dialog")
        self._reply(bubble["id"])
        self.assertEqual(self.store.get_bubble(bubble["id"]).version, 2)


class ChatTest(ApiTestCase):
    """E07 / E08。"""

    def setUp(self) -> None:
        super().setUp()
        self.bubble = self._publish(mode="dialog")
        self._reply(self.bubble["id"])

    def _send(self, client_id: str, receiver: str, content: str = "hi"):
        return self._call(
            "POST",
            f"/api/v2/bubbles/{self.bubble['id']}/chat",
            client_id=client_id,
            payload={"op_id": _op(), "receiver": receiver, "content": content},
        )

    def test_stranger_cannot_read_chat(self) -> None:
        response = self._call(
            "GET", f"/api/v2/bubbles/{self.bubble['id']}/chat", client_id=STRANGER
        )
        self.assertEqual(response.status_code, 403)

    def test_replier_can_read_chat(self) -> None:
        response = self._call(
            "GET", f"/api/v2/bubbles/{self.bubble['id']}/chat", client_id=REPLIER
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._json(response)["data"]["messages"], [])

    def test_send_and_read(self) -> None:
        self.assertEqual(self._send(REPLIER, "Publisher", "你好").status_code, 201)
        self.assertEqual(self._send(PUBLISHER, "Replier", "收到").status_code, 201)
        data = self._json(
            self._call("GET", f"/api/v2/bubbles/{self.bubble['id']}/chat", client_id=PUBLISHER)
        )["data"]
        self.assertEqual(len(data["messages"]), 2)

    def test_stranger_cannot_send(self) -> None:
        self.assertEqual(self._send(STRANGER, "Publisher").status_code, 403)

    def test_receiver_empty(self) -> None:
        response = self._call(
            "POST",
            f"/api/v2/bubbles/{self.bubble['id']}/chat",
            client_id=REPLIER,
            payload={"op_id": _op(), "receiver": "", "content": "hi"},
        )
        self.assertEqual(response.status_code, 400)

    def test_content_too_long(self) -> None:
        self.assertEqual(
            self._send(REPLIER, "Publisher", "字" * 201).status_code, 400
        )

    def test_chat_after_resolved_forbidden(self) -> None:
        self._call(
            "POST",
            f"/api/v2/bubbles/{self.bubble['id']}/resolve",
            payload={"op_id": _op(), "base_version": 1},
        )
        self.assertEqual(self._send(REPLIER, "Publisher").status_code, 403)

    def test_chat_404(self) -> None:
        response = self._call(
            "POST",
            "/api/v2/bubbles/nope/chat",
            client_id=REPLIER,
            payload={"op_id": _op(), "receiver": "x", "content": "hi"},
        )
        self.assertEqual(response.status_code, 404)


class ManageTest(ApiTestCase):
    """E09 / E10 / E11。"""

    def test_resolve_by_author(self) -> None:
        bubble = self._publish()
        response = self._call(
            "POST",
            f"/api/v2/bubbles/{bubble['id']}/resolve",
            payload={"op_id": _op(), "base_version": 1},
        )
        self.assertEqual(response.status_code, 200)
        data = self._json(response)["data"]
        self.assertEqual(data["status"], "resolved")
        self.assertEqual(data["version"], 2)
        self.assertTrue(data["resolved_at"])

    def test_resolve_by_other_forbidden(self) -> None:
        bubble = self._publish()
        response = self._call(
            "POST",
            f"/api/v2/bubbles/{bubble['id']}/resolve",
            client_id=REPLIER,
            payload={"op_id": _op(), "base_version": 1},
        )
        self.assertEqual(response.status_code, 403)

    def test_resolve_idempotent(self) -> None:
        bubble = self._publish()
        for _ in range(2):
            response = self._call(
                "POST",
                f"/api/v2/bubbles/{bubble['id']}/resolve",
                payload={"op_id": _op(), "base_version": 1},
            )
            self.assertEqual(response.status_code, 200)
        self.assertEqual(self.store.get_bubble(bubble["id"]).version, 2)

    def test_save_public_and_private(self) -> None:
        bubble = self._publish()
        public = self._call(
            "POST",
            f"/api/v2/bubbles/{bubble['id']}/save",
            payload={"op_id": _op(), "is_public": True, "base_version": 1},
        )
        self.assertEqual(self._json(public)["data"]["status"], "saved_public")
        private = self._call(
            "POST",
            f"/api/v2/bubbles/{bubble['id']}/save",
            payload={"op_id": _op(), "is_public": False, "base_version": 2},
        )
        self.assertEqual(self._json(private)["data"]["status"], "saved_private")

    def test_save_non_bool_rejected(self) -> None:
        bubble = self._publish()
        response = self._call(
            "POST",
            f"/api/v2/bubbles/{bubble['id']}/save",
            payload={"op_id": _op(), "is_public": "yes", "base_version": 1},
        )
        self.assertEqual(response.status_code, 400)

    def test_save_by_other_forbidden(self) -> None:
        bubble = self._publish()
        response = self._call(
            "POST",
            f"/api/v2/bubbles/{bubble['id']}/save",
            client_id=REPLIER,
            payload={"op_id": _op(), "is_public": True, "base_version": 1},
        )
        self.assertEqual(response.status_code, 403)

    def test_mine_groups_and_counts(self) -> None:
        self._publish(bubble_id="m1")
        self._publish(bubble_id="m2")
        self._call(
            "POST",
            "/api/v2/bubbles/m1/save",
            payload={"op_id": _op(), "is_public": False, "base_version": 1},
        )
        response = self._call("GET", "/api/v2/mine?group=published")
        data = self._json(response)["data"]
        self.assertEqual({item["id"] for item in data["items"]}, {"m2"})
        self.assertEqual(data["counts"]["published"], 1)
        self.assertEqual(data["counts"]["saved_private"], 1)
        private = self._json(self._call("GET", "/api/v2/mine?group=saved_private"))["data"]
        self.assertEqual([item["id"] for item in private["items"]], ["m1"])

    def test_mine_isolated_between_clients(self) -> None:
        self._publish(bubble_id="p1")
        data = self._json(self._call("GET", "/api/v2/mine", client_id=STRANGER))["data"]
        self.assertEqual(data["items"], [])
        self.assertEqual(data["counts"]["published"], 0)

    def test_mine_invalid_group(self) -> None:
        self.assertEqual(self._call("GET", "/api/v2/mine?group=bogus").status_code, 400)


if __name__ == "__main__":
    unittest.main()
