"""服务端 API 测试的公共夹具（临时库 + Flask test_client + 身份登记）。"""

from __future__ import annotations

import os
import unittest
import uuid

from src.server_app import create_app
from src.server_store import ServerStore, ensure_server_schema
from tests.helpers import temp_workspace

PUBLISHER = "cid-publisher"
REPLIER = "cid-replier"
STRANGER = "cid-stranger"


def _op() -> str:
    """生成唯一 op_id。"""
    return str(uuid.uuid4())


class ApiTestCase(unittest.TestCase):
    """公共夹具：临时库 + Flask test_client。"""

    RATE_LIMIT = 100000

    def setUp(self) -> None:
        self._ctx = temp_workspace()
        self.workspace = self._ctx.__enter__()
        self.db_path = os.path.join(self.workspace, "server.db")
        self.backup_dir = os.path.join(self.workspace, "backup")
        ensure_server_schema(self.db_path, self.backup_dir)
        self.store = ServerStore(self.db_path)
        self.app = create_app(
            db_path=self.db_path,
            backup_dir=self.backup_dir,
            store=self.store,
            log_path="",
            rate_limit=self.RATE_LIMIT,
        )
        self.client = self.app.test_client()
        self._register(PUBLISHER, "Publisher")
        self._register(REPLIER, "Replier")
        self._register(STRANGER, "Stranger")
        # 注册请求也计入限流，测试开始前清空计数，保证断言只看被测请求
        self.app.extensions["eb_rate_limiter"].reset()

    def tearDown(self) -> None:
        self.store.close()
        self._ctx.__exit__(None, None, None)

    # -- 工具 -------------------------------------------------------------
    def _register(self, client_id: str, nickname: str) -> dict:
        response = self.client.post(
            "/api/v2/session", json={"client_id": client_id, "nickname": nickname}
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.get_json()["data"]

    def _call(
        self,
        method: str,
        path: str,
        *,
        client_id: str = PUBLISHER,
        payload: dict | None = None,
        headers: dict | None = None,
    ):
        merged = {"X-Client-Id": client_id} if client_id else {}
        merged.update(headers or {})
        return self.client.open(path, method=method, json=payload, headers=merged)

    def _json(self, response):
        return response.get_json()

    def _publish(
        self,
        *,
        client_id: str = PUBLISHER,
        content: str = "内容",
        emotion: str = "开心",
        mode: str = "single",
        bubble_id: str | None = None,
    ) -> dict:
        body = {
            "op_id": _op(),
            "content": content,
            "emotion_tag": emotion,
            "reply_mode": mode,
        }
        if bubble_id:
            body["id"] = bubble_id
        response = self._call("POST", "/api/v2/bubbles", client_id=client_id, payload=body)
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        return self._json(response)["data"]

    def _reply(self, bubble_id: str, *, client_id: str = REPLIER, content: str = "回复"):
        return self._call(
            "POST",
            f"/api/v2/bubbles/{bubble_id}/reply",
            client_id=client_id,
            payload={"op_id": _op(), "content": content},
        )


__all__ = [
    "PUBLISHER",
    "REPLIER",
    "STRANGER",
    "_op",
    "ApiTestCase",
]
