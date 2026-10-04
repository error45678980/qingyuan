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


def check_security():
    """安全基线：响应头与会话 cookie 属性。少了这些，回归时没人会发现。

    必须用全新 client：会话 cookie 只在 session 真的被改动时才下发，
    复用已有会话的 client 拿不到 Set-Cookie。
    """
    r = app.test_client().get("/login")
    h = r.headers
    assert h.get("X-Content-Type-Options") == "nosniff", "缺少 X-Content-Type-Options"
    assert h.get("X-Frame-Options") == "DENY", "缺少 X-Frame-Options"
    csp = h.get("Content-Security-Policy", "")
    assert "default-src 'self'" in csp, f"CSP 不对：{csp!r}"
    assert "frame-ancestors 'none'" in csp, "CSP 缺少 frame-ancestors"

    cookies = "; ".join(r.headers.getlist("Set-Cookie"))
    assert "HttpOnly" in cookies, f"会话 cookie 缺 HttpOnly：{cookies!r}"
    assert "SameSite=Lax" in cookies, f"会话 cookie 缺 SameSite=Lax：{cookies!r}"


def check_logout_csrf(client):
    """无 CSRF 的登出必须被拒，且不能真把人踢下线。"""
    r = client.post("/logout")
    assert r.status_code == 400, f"无 CSRF 登出应 400，实际 {r.status_code}"
    assert client.get("/").status_code == 200, "被非法的登出请求踢下线了"


def check_login_rate_limit():
    """限流必须按 IP 计数（按用户名会把别人锁死），且成功登录后清零。"""
    from src import auth
    auth._LOGIN_FAILURES.clear()
    c = app.test_client()

    for _ in range(5):
        r = c.get("/login")
        c.post("/login", data={"_csrf_token": get_csrf(r), "username": "小明",
                               "password": "WRONG"})
    r = c.get("/login")
    r = c.post("/login", data={"_csrf_token": get_csrf(r), "username": "小明",
                               "password": "secret123"})
    assert r.status_code == 429, f"5 次失败后应被限流，实际 {r.status_code}"
    assert "小明" not in auth._LOGIN_FAILURES, "限流表不应以用户名为键（会把账号锁死）"

    auth._LOGIN_FAILURES.clear()
    r = c.get("/login")
    r = c.post("/login", data={"_csrf_token": get_csrf(r), "username": "小明",
                               "password": "secret123"})
    assert r.status_code == 302, "清零后应能正常登录"
    assert not auth._LOGIN_FAILURES, "登录成功后应清空该 IP 的失败记录"


def main():
    assert_sqlite_backend()
    client = app.test_client()
    INVITE = app.config["INVITE_CODE"]

    # 1. 未登录 → 302
    assert client.get("/").status_code == 302
    # 2. 登录页
    assert client.get("/login").status_code == 200
    # 2b. 安全响应头 / cookie 属性
    check_security()
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
    # 5b. 登出必须校验 CSRF
    check_logout_csrf(client)
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
    # 17. 登录限流：按 IP 计数 + 成功即清零（放最后，避免污染前面的断言）
    check_login_rate_limit()

    print("=== 全部冒烟测试通过 ===")


if __name__ == "__main__":
    try:
        main()
    finally:
        try:
            os.remove(_tmp.name)
        except OSError:
            pass
