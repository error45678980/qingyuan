# -*- coding: utf-8 -*-
"""冒烟测试：验证认证、权限、核心流程（使用临时数据库，不污染真实数据）。"""
import os
import re
import tempfile

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["EMOTION_BUBBLE_DB"] = _tmp.name

from src.server_app import app  # noqa: E402


def get_csrf(resp):
    m = re.search(rb'name="_csrf_token" value="([^"]+)"', resp.data)
    assert m, "页面中未找到 CSRF token"
    return m.group(1).decode()


def main():
    client = app.test_client()
    INVITE = app.config["INVITE_CODE"]

    # 1. 未登录访问首页 → 302 到登录页
    r = client.get("/")
    assert r.status_code == 302, f"未登录应 302，得到 {r.status_code}"

    # 2. 登录页渲染
    r = client.get("/login")
    assert r.status_code == 200
    assert "情感泡泡".encode("utf-8") in r.data

    # 3. 错误邀请码 → 403
    r = client.get("/login")
    csrf = get_csrf(r)
    r = client.post("/register", data={
        "_csrf_token": csrf, "username": "小明", "password": "secret123",
        "invite_code": "WRONG"})
    assert r.status_code == 403, f"错邀请码应 403，得到 {r.status_code}"

    # 4. 正确注册
    r = client.get("/login")
    csrf = get_csrf(r)
    r = client.post("/register", data={
        "_csrf_token": csrf, "username": "小明", "password": "secret123",
        "invite_code": INVITE})
    assert r.status_code == 302, f"注册应 302，得到 {r.status_code}"

    # 5. 已登录访问首页
    r = client.get("/")
    assert r.status_code == 200

    # 6. 发布泡泡
    r = client.get("/publish")
    csrf = get_csrf(r)
    r = client.post("/publish", data={
        "_csrf_token": csrf, "content": "最近有点难过", "emotion_tag": "难过",
        "reply_mode": "dialog"})
    assert r.status_code == 302, f"发布应 302，得到 {r.status_code}"
    bubble_url = r.headers.get("Location")
    bubble_id = bubble_url.rstrip("/").split("/")[-1]
    assert bubble_url.startswith("/bubble/")

    # 7. 详情页
    r = client.get(bubble_url)
    assert r.status_code == 200

    # 8. 登出
    r = client.get("/")
    csrf = get_csrf(r)
    r = client.post("/logout", data={"_csrf_token": csrf})
    assert r.status_code == 302

    # 9. 注册第二个用户小红
    r = client.get("/login")
    csrf = get_csrf(r)
    r = client.post("/register", data={
        "_csrf_token": csrf, "username": "小红", "password": "secret123",
        "invite_code": INVITE})
    assert r.status_code == 302

    # 10. 小红尝试解决小明的泡泡 → 403（越权）
    r = client.get("/")
    csrf = get_csrf(r)
    r = client.post(f"/bubble/{bubble_id}/resolve", data={"_csrf_token": csrf})
    assert r.status_code == 403, f"越权解决应 403，得到 {r.status_code}"

    # 11. 小红开启对话
    r = client.get(bubble_url)
    csrf = get_csrf(r)
    r = client.post(f"/bubble/{bubble_id}/chat", data={
        "_csrf_token": csrf, "content": "别难过，我陪你聊聊"})
    assert r.status_code == 302, f"开对话应 302，得到 {r.status_code}"
    chat_url = r.headers.get("Location")
    conv_id = chat_url.rstrip("/").split("/")[-1]
    assert chat_url.startswith("/chat/")

    # 12. 聊天页渲染
    r = client.get(chat_url)
    assert r.status_code == 200

    # 13. 小明重新登录，回复
    r = client.get("/")
    csrf = get_csrf(r)
    client.post("/logout", data={"_csrf_token": csrf})
    r = client.get("/login")
    csrf = get_csrf(r)
    r = client.post("/login", data={
        "_csrf_token": csrf, "username": "小明", "password": "secret123"})
    assert r.status_code == 302

    r = client.get("/chats")
    assert r.status_code == 200

    r = client.get(chat_url)
    csrf = get_csrf(r)
    r = client.post(f"/chat/{conv_id}/send", data={
        "_csrf_token": csrf, "content": "谢谢你"})
    assert r.status_code == 302

    # 14. 未登录调用局部接口 → 401
    r = client.get("/")
    csrf = get_csrf(r)
    client.post("/logout", data={"_csrf_token": csrf})
    r = client.get("/partial/bubbles")
    assert r.status_code == 401, f"未登录局部接口应 401，得到 {r.status_code}"

    print("=== 全部冒烟测试通过 ===")


if __name__ == "__main__":
    try:
        main()
    finally:
        try:
            os.remove(_tmp.name)
        except OSError:
            pass
