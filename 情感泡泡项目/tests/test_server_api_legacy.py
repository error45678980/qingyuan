"""T-06 验收：旧端点 `/api/*` 兼容（不能把老客户端打死）+ 限流 429。"""

from __future__ import annotations

import unittest

from tests.api_case import PUBLISHER, REPLIER, STRANGER, ApiTestCase, _op


class LegacyEndpointTest(ApiTestCase):
    """§4.3 旧端点兼容（不能把老客户端打死）。"""

    def test_ping(self) -> None:
        response = self.client.get("/api/ping")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._json(response), {"status": "ok"})

    def test_list_returns_array(self) -> None:
        self._publish(bubble_id="legacy-1")
        response = self.client.get("/api/bubbles")
        self.assertEqual(response.status_code, 200)
        body = self._json(response)
        self.assertIsInstance(body, list)
        self.assertEqual(body[0]["id"], "legacy-1")
        self.assertEqual(body[0]["author"], "Publisher")
        self.assertIn("author_node_id", body[0])
        self.assertNotIn("chats", body[0])

    def test_create_requires_id(self) -> None:
        response = self.client.post(
            "/api/bubbles",
            json={
                "author": "Publisher",
                "content": "x",
                "emotion_tag": "开心",
                "reply_mode": "single",
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_create_and_get(self) -> None:
        response = self.client.post(
            "/api/bubbles",
            json={
                "id": "legacy-2",
                "author": "Publisher",
                "content": "旧客户端发布",
                "emotion_tag": "难过",
                "reply_mode": "dialog",
                "created_at": "2026-07-21T18:00:00",
            },
        )
        self.assertEqual(response.status_code, 201)
        detail = self.client.get("/api/bubbles/legacy-2?viewer=Publisher")
        self.assertEqual(detail.status_code, 200)
        data = self._json(detail)
        self.assertEqual(data["content"], "旧客户端发布")
        self.assertEqual(data["emotion_tag"], "难过")
        self.assertEqual(data["replies"], [])
        self.assertEqual(data["chats"], [])

    def test_viewer_without_match_sees_no_chats(self) -> None:
        """P0-5 回归：无匹配 viewer 不得看到他人私聊。"""
        bubble = self._publish(mode="dialog")
        self._reply(bubble["id"])
        self.client.post(
            f"/api/bubbles/{bubble['id']}/chat",
            json={"sender": "Replier", "receiver": "Publisher", "content": "私聊内容"},
            headers={"X-Client-Id": REPLIER},
        )
        unknown = self._json(self.client.get(f"/api/bubbles/{bubble['id']}?viewer=Nobody"))
        self.assertEqual(unknown["chats"], [])
        replier_view = self._json(
            self.client.get(f"/api/bubbles/{bubble['id']}?viewer=Replier")
        )
        self.assertEqual(len(replier_view["chats"]), 1)
        self.assertEqual(replier_view["chats"][0]["receiver"], "Publisher")

    def test_legacy_reply_conflict(self) -> None:
        bubble = self._publish(mode="single")
        first = self.client.post(
            f"/api/bubbles/{bubble['id']}/reply",
            json={"replier": "Replier", "content": "第一次"},
            headers={"X-Client-Id": REPLIER},
        )
        self.assertEqual(first.status_code, 201)
        second = self.client.post(
            f"/api/bubbles/{bubble['id']}/reply",
            json={"replier": "Replier", "content": "第二次"},
            headers={"X-Client-Id": REPLIER},
        )
        self.assertEqual(second.status_code, 409)
        self.assertEqual(self._json(second)["code"], "conflict")

    def test_legacy_chat_only_returns_own(self) -> None:
        bubble = self._publish(mode="dialog")
        self._reply(bubble["id"])
        self.client.post(
            f"/api/bubbles/{bubble['id']}/chat",
            json={"sender": "Replier", "receiver": "Publisher", "content": "给作者"},
            headers={"X-Client-Id": REPLIER},
        )
        self.client.post(
            f"/api/bubbles/{bubble['id']}/chat",
            json={"sender": "Publisher", "receiver": "Replier", "content": "给回复者"},
            headers={"X-Client-Id": PUBLISHER},
        )
        replier_view = self._json(
            self.client.post(
                f"/api/bubbles/{bubble['id']}/chat",
                json={"sender": "Replier", "receiver": "Publisher", "content": "第三条"},
                headers={"X-Client-Id": REPLIER},
            )
        )
        self.assertEqual(len(replier_view["chats"]), 3)
        stranger = self.client.post(
            f"/api/bubbles/{bubble['id']}/chat",
            json={"sender": "Stranger", "receiver": "Publisher", "content": "插话"},
            headers={"X-Client-Id": STRANGER},
        )
        self.assertEqual(stranger.status_code, 403)

    def test_legacy_resolve_and_save(self) -> None:
        bubble = self._publish()
        resolved = self.client.post(
            f"/api/bubbles/{bubble['id']}/resolve",
            json={},
            headers={"X-Client-Id": PUBLISHER},
        )
        self.assertEqual(resolved.status_code, 200)
        self.assertEqual(self._json(resolved)["status"], "resolved")

        other = self._publish(bubble_id="legacy-save")
        saved = self.client.post(
            "/api/bubbles/legacy-save/save",
            json={"is_public": True},
            headers={"X-Client-Id": PUBLISHER},
        )
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(self._json(saved)["status"], "saved_public")

    def test_legacy_404(self) -> None:
        self.assertEqual(self.client.get("/api/bubbles/nope").status_code, 404)


class RateLimitTest(ApiTestCase):
    """429 + Retry-After。"""

    RATE_LIMIT = 3

    def test_rate_limited(self) -> None:
        statuses = [self._call("GET", "/api/v2/bubbles").status_code for _ in range(3)]
        self.assertEqual(statuses, [200, 200, 200])
        response = self._call("GET", "/api/v2/bubbles")
        self.assertEqual(response.status_code, 429)
        self.assertEqual(self._json(response)["code"], "rate_limited")
        self.assertIn("Retry-After", response.headers)

    def test_health_exempt(self) -> None:
        for _ in range(10):
            self.assertEqual(self.client.get("/api/v2/health").status_code, 200)


if __name__ == "__main__":
    unittest.main()
