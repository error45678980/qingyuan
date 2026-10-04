# -*- coding: utf-8 -*-
"""冒烟测试：认证、发布、侧栏聊天、权限（使用临时数据库）。"""
import os
import re
import sqlite3
import tempfile

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["EMOTION_BUBBLE_DB"] = _tmp.name
# 关键：显式清空 DATABASE_URL，防止回落到 secrets.local.py 里的生产 Neon 连接串。
# 同时显式提供测试专用的密钥/邀请码，测试完全不读 secrets 文件。
os.environ["DATABASE_URL"] = ""
os.environ["SECRET_KEY"] = "test-secret-not-for-production"
os.environ["INVITE_CODE"] = "TEST-INVITE-CODE"

from src.server_app import app  # noqa: E402


def assert_sqlite_backend():
    """测试必须跑在本地 SQLite 上，绝不能误连生产库。"""
    with app.app_context():
        from src.db import get_db
        backend = type(get_db()).__module__
        assert backend == "src.database", (
            f"冒烟测试必须使用 SQLite，实际后端是 {backend}（疑似连了生产库！）"
        )


def get_csrf(resp):
    m = re.search(rb'name="_csrf_token" value="([^"]+)"', resp.data)
    assert m, "页面中未找到 CSRF token"
    return m.group(1).decode()


def conv_id_for(bubble_id, helper):
    conn = sqlite3.connect(_tmp.name)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT id FROM conversations WHERE bubble_id=? AND helper_nickname=?",
        (bubble_id, helper)).fetchone()
    conn.close()
    return row["id"] if row else None


def main():
    assert_sqlite_backend()
    client = app.test_client()
    INVITE = app.config["INVITE_CODE"]

    # 1. 未登录 → 302
    assert client.get("/").status_code == 302
    # 2. 登录页
    assert client.get("/login").status_code == 200
    # 3. 错邀请码 → 403
    r = client.get("/login"); csrf = get_csrf(r)
    r = client.post("/register", data={"_csrf_token": csrf, "username": "小明",
                                       "password": "secret123", "invite_code": "WRONG"})
    assert r.status_code == 403
    # 4. 正确注册
    r = client.get("/login"); csrf = get_csrf(r)
    r = client.post("/register", data={"_csrf_token": csrf, "username": "小明",
                                       "password": "secret123", "invite_code": INVITE})
    assert r.status_code == 302
    # 5. 首页
    assert client.get("/").status_code == 200
    # 6. 发布（JSON）
    r = client.get("/"); csrf = get_csrf(r)
    r = client.post("/publish", data={"_csrf_token": csrf, "content": "最近有点难过",
                                      "emotion_tag": "难过"})
    assert r.status_code == 200
    bubble_id = r.get_json()["bubble_id"]
    # 7. 泡泡墙片段
    assert client.get("/partial/bubbles").status_code == 200
    # 8. 登出
    r = client.get("/"); csrf = get_csrf(r)
    client.post("/logout", data={"_csrf_token": csrf})
    # 9. 注册小红
    r = client.get("/login"); csrf = get_csrf(r)
    r = client.post("/register", data={"_csrf_token": csrf, "username": "小红",
                                       "password": "secret123", "invite_code": INVITE})
    assert r.status_code == 302
    # 10. 越权解决 → 403
    r = client.get("/"); csrf = get_csrf(r)
    r = client.post(f"/bubble/{bubble_id}/resolve", data={"_csrf_token": csrf})
    assert r.status_code == 403
    # 11. 小红点小明的泡泡 → 侧栏面板（自动建会话）
    r = client.get(f"/partial/panel/bubble/{bubble_id}")
    assert r.status_code == 200
    conv_id = conv_id_for(bubble_id, "小红")
    assert conv_id
    # 12. 小红发消息
    r = client.get("/"); csrf = get_csrf(r)
    r = client.post(f"/chat/{conv_id}/send", data={"_csrf_token": csrf, "content": "别难过"})
    assert r.status_code == 200
    # 13. 消息片段
    assert client.get(f"/partial/chat/{conv_id}").status_code == 200
    # 14. 小红会话列表
    assert client.get("/partial/panel/conversations").status_code == 200
    # 15. 小明重新登录，回复
    r = client.get("/"); csrf = get_csrf(r)
    client.post("/logout", data={"_csrf_token": csrf})
    r = client.get("/login"); csrf = get_csrf(r)
    r = client.post("/login", data={"_csrf_token": csrf, "username": "小明", "password": "secret123"})
    assert r.status_code == 302
    assert client.get("/partial/panel/conversations").status_code == 200
    r = client.get("/"); csrf = get_csrf(r)
    r = client.post(f"/chat/{conv_id}/send", data={"_csrf_token": csrf, "content": "谢谢你"})
    assert r.status_code == 200
    # 16. 未登录局部接口 → 401
    r = client.get("/"); csrf = get_csrf(r)
    client.post("/logout", data={"_csrf_token": csrf})
    assert client.get("/partial/bubbles").status_code == 401

    print("=== 全部冒烟测试通过 ===")


if __name__ == "__main__":
    try:
        main()
    finally:
        try:
            os.remove(_tmp.name)
        except OSError:
            pass
