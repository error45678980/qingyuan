"""
auth.py - 认证与权限工具
包含：当前用户、登录守卫、CSRF 防护、登录限流。
"""
import functools
import secrets
import time

from flask import jsonify, redirect, request, session, url_for

from .db import get_db

# 简单的进程内登录限流：{用户名: [时间戳]}
_LOGIN_ATTEMPTS = {}


def get_current_user():
    """返回当前登录用户（sqlite3.Row），未登录返回 None。"""
    user_id = session.get("user_id")
    if user_id is None:
        return None
    return get_db().get_user_by_id(user_id)


def login_required(view):
    """页面未登录 → 跳转登录页；/api/、/partial/ 未登录 → 401 JSON。"""
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if session.get("user_id") is None:
            if request.path.startswith("/api/") or request.path.startswith("/partial/"):
                return jsonify({"error": "unauthorized"}), 401
            return redirect(url_for("login_page", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def get_csrf_token():
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_hex(16)
        session["csrf_token"] = token
    return token


def validate_csrf():
    expected = session.get("csrf_token")
    if not expected:
        return False
    sent = (request.form.get("_csrf_token")
            or request.headers.get("X-CSRF-Token")
            or (request.get_json(silent=True) or {}).get("_csrf_token"))
    if not sent:
        return False
    return secrets.compare_digest(expected, sent)


def is_login_blocked(username):
    now = time.time()
    window = 60          # 60 秒窗口
    max_attempts = 5     # 最多 5 次失败
    attempts = [t for t in _LOGIN_ATTEMPTS.get(username, []) if now - t < window]
    _LOGIN_ATTEMPTS[username] = attempts
    return len(attempts) >= max_attempts


def record_login_attempt(username):
    _LOGIN_ATTEMPTS.setdefault(username, []).append(time.time())
