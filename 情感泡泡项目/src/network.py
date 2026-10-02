"""
network.py - 中央服务器通信模块
从中央服务器获取全球泡泡信息，并将本地操作同步到服务器。
"""

import os
import requests


class CentralServerNetwork:
    """中央服务器 API 客户端"""

    def __init__(self, nickname, server_url=None, timeout=5):
        self.nickname = nickname
        self.server_url = server_url or os.getenv("EMOTION_BUBBLE_SERVER_URL", "http://127.0.0.1:5000")
        self.timeout = timeout

    def _url(self, path):
        return self.server_url.rstrip("/") + "/" + path.lstrip("/")

    def _request(self, method, path, **kwargs):
        try:
            url = self._url(path)
            response = requests.request(method, url, timeout=self.timeout, **kwargs)
            response.raise_for_status()
            return response.json()
        except Exception:
            return None

    def is_online(self):
        return self._request("GET", "/api/ping") is not None

    def fetch_bubbles(self, emotion=None):
        params = {}
        if emotion and emotion != "全部":
            params["emotion"] = emotion
        result = self._request("GET", "/api/bubbles", params=params)
        return result or []

    def fetch_bubble_details(self, bubble_id, viewer=None):
        params = {}
        if viewer:
            params["viewer"] = viewer
        return self._request("GET", f"/api/bubbles/{bubble_id}", params=params) or {}

    def publish_bubble(self, bubble_id, content, emotion, reply_mode, created_at):
        payload = {
            "id": bubble_id,
            "author": self.nickname,
            "content": content,
            "emotion_tag": emotion,
            "reply_mode": reply_mode,
            "created_at": created_at,
        }
        return self._request("POST", "/api/bubbles", json=payload)

    def reply_to_bubble(self, bubble_id, content):
        payload = {
            "replier": self.nickname,
            "content": content,
        }
        return self._request("POST", f"/api/bubbles/{bubble_id}/reply", json=payload)

    def send_chat_message(self, bubble_id, receiver, content, created_at):
        payload = {
            "sender": self.nickname,
            "receiver": receiver,
            "content": content,
            "created_at": created_at,
        }
        return self._request("POST", f"/api/bubbles/{bubble_id}/chat", json=payload)

    def resolve_bubble(self, bubble_id):
        return self._request("POST", f"/api/bubbles/{bubble_id}/resolve", json={})

    def save_bubble(self, bubble_id, is_public):
        return self._request("POST", f"/api/bubbles/{bubble_id}/save", json={"is_public": is_public})
