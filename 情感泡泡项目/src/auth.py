"""
auth.py - 认证与权限工具
包含：当前用户、登录守卫、CSRF 防护、登录限流。
"""
import functools
import secrets
import time

from flask import jsonify, redirect, request, session, url_for

from .db import get_db

# 进程内登录限流：按「客户端 IP」计数。
# 刻意不按用户名计数——否则攻击者只要拿别人的用户名反复失败，
# 就能把受害者锁在门外（账号级 DoS）。按 IP 计数只影响攻击者自己。
_LOGIN_FAILURES = {}
_LOGIN_WINDOW = 60          # 60 秒窗口
_LOGIN_MAX_ATTEMPTS = 5     # 窗口内最多 5 次失败
_PRUNE_THRESHOLD = 500      # 表过大时顺手回收过期项，避免内存无限增长


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


def _client_ip():
    """ProxyFix 已就位，remote_addr 是反代传来的真实客户端 IP。"""
    return request.remote_addr or "unknown"


def _prune(now):
    for ip in list(_LOGIN_FAILURES):
        kept = [t for t in _LOGIN_FAILURES[ip] if now - t < _LOGIN_WINDOW]
        if kept:
            _LOGIN_FAILURES[ip] = kept
        else:
            del _LOGIN_FAILURES[ip]


def is_login_blocked():
    ip = _client_ip()
    now = time.time()
    recent = [t for t in _LOGIN_FAILURES.get(ip, []) if now - t < _LOGIN_WINDOW]
    if recent:
        _LOGIN_FAILURES[ip] = recent
    else:
        _LOGIN_FAILURES.pop(ip, None)       # 顺手回收，别让空键堆积
    return len(recent) >= _LOGIN_MAX_ATTEMPTS


def record_login_failure():
    now = time.time()
    if len(_LOGIN_FAILURES) > _PRUNE_THRESHOLD:
        _prune(now)
    _LOGIN_FAILURES.setdefault(_client_ip(), []).append(now)


def clear_login_failures():
    """登录成功即清空该 IP 的失败记录：别让用户被自己之前的失误拖累。"""
    _LOGIN_FAILURES.pop(_client_ip(), None)
