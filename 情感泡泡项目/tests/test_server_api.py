"""T-06 验收：v2 端点 E01~E11、400/403/404/409 分支、分页与幂等。"""

from __future__ import annotations

import unittest

from tests.api_case import PUBLISHER, REPLIER, STRANGER, ApiTestCase, _op


class HealthAndSessionTest(ApiTestCase):
    """E01 / E02。"""

    def test_health(self) -> None:
        response = self.client.get("/api/v2/health")
        self.assertEqual(response.status_code, 200)
        body = self._json(response)
        self.assertEqual(body["code"], "ok")
        self.assertEqual(body["data"]["status"], "ok")
        self.assertEqual(body["data"]["schema_version"], 2)
        self.assertTrue(body["data"]["server_time"])

    def test_session_creates_and_reuses_token(self) -> None:
        first = self._register("cid-new", "新用户")
        self.assertTrue(first["is_new"])
        self.assertTrue(first["token"])
        second = self._register("cid-new", "新用户")
        self.assertFalse(second["is_new"])
        self.assertEqual(first["token"], second["token"])

    def test_session_missing_nickname(self) -> None:
        response = self.client.post("/api/v2/session", json={"client_id": "cid-x"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self._json(response)["code"], "bad_request")

    def test_session_nickname_too_long(self) -> None:
        response = self.client.post(
            "/api/v2/session", json={"client_id": "cid-x", "nickname": "字" * 13}
        )
        self.assertEqual(response.status_code, 400)

    def test_session_missing_client_id(self) -> None:
        response = self.client.post("/api/v2/session", json={"nickname": "x"})
        self.assertEqual(response.status_code, 400)

    def test_unknown_route_returns_envelope(self) -> None:
        response = self.client.get("/api/v2/not-a-route")
        self.assertEqual(response.status_code, 404)
        body = self._json(response)
        self.assertEqual(body["code"], "not_found")
        self.assertIsNone(body["data"])


class PublishTest(ApiTestCase):
    """E04。"""

    def test_create_returns_201_and_dto(self) -> None:
        data = self._publish(content="你好呀")
        self.assertEqual(data["content"], "你好呀")
        self.assertEqual(data["author"], "Publisher")
        self.assertEqual(data["status"], "active")
        self.assertEqual(data["version"], 1)
        self.assertTrue(data["is_owner"])
        self.assertFalse(data["can_reply"])
        self.assertTrue(data["can_chat"])

    def test_server_generates_uuid(self) -> None:
        data = self._publish()
        self.assertEqual(len(data["id"]), 36)

    def test_content_too_long(self) -> None:
        response = self._call(
            "POST",
            "/api/v2/bubbles",
            payload={
                "op_id": _op(),
                "content": "字" * 501,
                "emotion_tag": "开心",
                "reply_mode": "single",
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_content_empty(self) -> None:
        response = self._call(
            "POST",
            "/api/v2/bubbles",
            payload={
                "op_id": _op(),
                "content": "   ",
                "emotion_tag": "开心",
                "reply_mode": "single",
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_invalid_emotion(self) -> None:
        response = self._call(
            "POST",
            "/api/v2/bubbles",
            payload={
                "op_id": _op(),
                "content": "x",
                "emotion_tag": "暴躁",
                "reply_mode": "single",
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_invalid_reply_mode(self) -> None:
        response = self._call(
            "POST",
            "/api/v2/bubbles",
            payload={
                "op_id": _op(),
                "content": "x",
                "emotion_tag": "开心",
                "reply_mode": "many",
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_missing_op_id(self) -> None:
        response = self._call(
            "POST",
            "/api/v2/bubbles",
            payload={"content": "x", "emotion_tag": "开心", "reply_mode": "single"},
        )
        self.assertEqual(response.status_code, 400)

    def test_idempotent_replay_returns_first_result(self) -> None:
        op_id = _op()
        payload = {
            "op_id": op_id,
            "content": "只应存在一次",
            "emotion_tag": "开心",
            "reply_mode": "single",
        }
        first = self._call("POST", "/api/v2/bubbles", payload=payload)
        second = self._call("POST", "/api/v2/bubbles", payload=payload)
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(self._json(first)["data"]["id"], self._json(second)["data"]["id"])
        self.assertEqual(self.store.stats()["bubbles"], 1)

    def test_id_conflict_with_other_author(self) -> None:
        self._publish(bubble_id="fixed-id")
        response = self._call(
            "POST",
            "/api/v2/bubbles",
            client_id=STRANGER,
            payload={
                "op_id": _op(),
                "id": "fixed-id",
                "content": "抢 id",
                "emotion_tag": "开心",
                "reply_mode": "single",
            },
        )
        self.assertEqual(response.status_code, 409)


class ListTest(ApiTestCase):
    """E03。"""

    def setUp(self) -> None:
        super().setUp()
        for index in range(5):
            self._publish(
                content=f"内容{index}",
                emotion="孤独" if index % 2 else "开心",
                bubble_id=f"b{index}",
            )

    def test_list_shape_and_total(self) -> None:
        response = self._call("GET", "/api/v2/bubbles?limit=2")
        self.assertEqual(response.status_code, 200)
        data = self._json(response)["data"]
        self.assertEqual(len(data["items"]), 2)
        self.assertEqual(data["total"], 5)
        self.assertTrue(data["has_more"])
        self.assertTrue(data["next_cursor"])
        self.assertTrue(data["server_time"])

    def test_cursor_pagination_complete(self) -> None:
        seen: list[str] = []
        cursor = None
        for _ in range(10):
            path = "/api/v2/bubbles?limit=2" + (f"&cursor={cursor}" if cursor else "")
            data = self._json(self._call("GET", path))["data"]
            seen.extend(item["id"] for item in data["items"])
            if not data["has_more"]:
                break
            cursor = data["next_cursor"]
        self.assertEqual(sorted(seen), ["b0", "b1", "b2", "b3", "b4"])

    def test_emotion_filter(self) -> None:
        data = self._json(self._call("GET", "/api/v2/bubbles?emotion=孤独"))["data"]
        self.assertEqual({item["id"] for item in data["items"]}, {"b1", "b3"})

    def test_search_filter(self) -> None:
        data = self._json(self._call("GET", "/api/v2/bubbles?q=内容3"))["data"]
        self.assertEqual([item["id"] for item in data["items"]], ["b3"])

    def test_invalid_emotion(self) -> None:
        self.assertEqual(self._call("GET", "/api/v2/bubbles?emotion=暴躁").status_code, 400)

    def test_invalid_limit(self) -> None:
        self.assertEqual(self._call("GET", "/api/v2/bubbles?limit=abc").status_code, 400)
        self.assertEqual(self._call("GET", "/api/v2/bubbles?limit=0").status_code, 400)

    def test_limit_clamped(self) -> None:
        data = self._json(self._call("GET", "/api/v2/bubbles?limit=999"))["data"]
        self.assertEqual(len(data["items"]), 5)

    def test_invalid_cursor(self) -> None:
        self.assertEqual(
            self._call("GET", "/api/v2/bubbles?cursor=!!!!").status_code, 400
        )

    def test_query_too_long(self) -> None:
        self.assertEqual(
            self._call("GET", f"/api/v2/bubbles?q={'a' * 51}").status_code, 400
        )


class DetailTest(ApiTestCase):
    """E05。"""

    def test_detail_with_replies(self) -> None:
        bubble = self._publish()
        self._reply(bubble["id"], content="第一条")
        data = self._json(self._call("GET", f"/api/v2/bubbles/{bubble['id']}"))["data"]
        self.assertEqual(len(data["replies"]), 1)
        self.assertEqual(data["replies"][0]["replier"], "Replier")
        self.assertEqual(data["reply_count"], 1)
        self.assertEqual(data["my_reply"], None)

    def test_detail_404(self) -> None:
        self.assertEqual(self._call("GET", "/api/v2/bubbles/nope").status_code, 404)

    def test_detail_id_too_long(self) -> None:
        self.assertEqual(
            self._call("GET", f"/api/v2/bubbles/{'a' * 65}").status_code, 400
        )

    def test_private_bubble_forbidden_for_others(self) -> None:
        bubble = self._publish()
        response = self._call(
            "POST",
            f"/api/v2/bubbles/{bubble['id']}/save",
            payload={"op_id": _op(), "is_public": False, "base_version": 1},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self._call("GET", f"/api/v2/bubbles/{bubble['id']}", client_id=STRANGER).status_code,
            403,
        )
        self.assertEqual(
            self._call("GET", f"/api/v2/bubbles/{bubble['id']}", client_id=PUBLISHER).status_code,
            200,
        )

    def test_chats_filtered_for_replier(self) -> None:
        bubble = self._publish(mode="dialog")
        self._reply(bubble["id"])
        self._call(
            "POST",
            f"/api/v2/bubbles/{bubble['id']}/chat",
            client_id=REPLIER,
            payload={"op_id": _op(), "receiver": "Publisher", "content": "悄悄话"},
        )
        self._call(
            "POST",
            f"/api/v2/bubbles/{bubble['id']}/chat",
            client_id=PUBLISHER,
            payload={"op_id": _op(), "receiver": "Replier", "content": "收到"},
        )
        author_view = self._json(
            self._call("GET", f"/api/v2/bubbles/{bubble['id']}", client_id=PUBLISHER)
        )["data"]
        self.assertEqual(len(author_view["chats"]), 2)
        stranger_view = self._json(
            self._call("GET", f"/api/v2/bubbles/{bubble['id']}", client_id=STRANGER)
        )["data"]
        self.assertEqual(stranger_view["chats"], [])


if __name__ == "__main__":
    unittest.main()
