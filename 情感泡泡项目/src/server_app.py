"""
server_app.py - 情感泡泡 网页版服务器
提供页面渲染 + 表单处理 + 局部刷新片段。
安全原则：用户身份一律从 session 获取，绝不信任前端提交的 author/sender 等字段。
"""
import hashlib
import os
import uuid

from flask import (Flask, flash, jsonify, redirect, render_template, request,
                   session, url_for)
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

from .auth import (get_current_user, get_csrf_token, is_login_blocked,
                   login_required, record_login_attempt, validate_csrf)
from .db import close_db, get_db, init_database

# ==================== 常量 ====================
EMOTION_ICONS = {
    "开心": "😊", "难过": "😢", "孤独": "🥺",
    "焦虑": "😰", "寻求建议": "💡", "找人倾诉": "🗣️",
}
# (浅色, 主色, 背景色)
EMOTION_COLORS = {
    "开心": ("#FFE066", "#FFB800", "#FFF8DC"),
    "难过": ("#74B9FF", "#0984E3", "#E8F4FD"),
    "孤独": ("#C59BFF", "#8854D0", "#F3EAFF"),
    "焦虑": ("#FF8A8A", "#FF4757", "#FFE8E8"),
    "寻求建议": ("#7BED9F", "#2ED573", "#E8F8F0"),
    "找人倾诉": ("#FFB78C", "#FF6348", "#FFF0E8"),
}
ALL_EMOTIONS = ["开心", "难过", "孤独", "焦虑", "寻求建议", "找人倾诉"]

# ==================== 泡泡样式（确定性随机）====================
_DREAMY = {
    "开心": ["#ffe29a", "#ffd0a8", "#fff3c9", "#ffc4d6", "#ffe8b8"],
    "难过": ["#a8c8ff", "#8fb0ff", "#c9e8ff", "#e6e6ff", "#9ec5ff"],
    "孤独": ["#c9b8ff", "#b3a0ff", "#e6dcff", "#d8c8ff", "#c5b3ff"],
    "焦虑": ["#ffb3c7", "#ff9ec0", "#ffd6e0", "#ffc9da", "#f5b8d0"],
    "寻求建议": ["#a8f0d8", "#8fe8c8", "#c9f5e6", "#b8f0e6", "#9ee8d0"],
    "找人倾诉": ["#ffd0a8", "#ffb88c", "#ffe0c8", "#ffc9a8", "#f5c0a0"],
}
_DREAMY_GLOW = {
    "开心": "rgba(255,190,110,.5)",
    "难过": "rgba(120,160,255,.5)",
    "孤独": "rgba(175,140,255,.5)",
    "焦虑": "rgba(255,150,180,.5)",
    "寻求建议": "rgba(120,230,180,.5)",
    "找人倾诉": "rgba(255,170,120,.5)",
}


def _bubble_variant(bubble):
    """按泡泡 id 做确定性哈希，生成稳定的随机样式（刷新不闪烁）。"""
    key = (bubble["id"] + bubble["emotion_tag"]).encode("utf-8")
    seed = int(hashlib.md5(key).hexdigest(), 16)
    emotion = bubble["emotion_tag"] if bubble["emotion_tag"] in _DREAMY else "开心"
    palette = _DREAMY[emotion]
    n = len(palette)
    c1 = palette[seed % n]
    c2 = palette[(seed >> 4) % n]
    c3 = palette[(seed >> 8) % n]
    c4 = palette[(seed >> 12) % n]
    content_len = len(bubble["content"])
    size = max(120, min(196, 122 + min(content_len, 24) * 3 + (seed % 16)))
    return {
        "size": size,
        "c1": c1, "c2": c2, "c3": c3, "c4": c4,
        "glow": _DREAMY_GLOW.get(emotion, "rgba(190,170,255,.5)"),
        "dur": 6 + (seed % 5),
        "delay": (seed % 40) / 10.0,
        # 泡泡海布局：横向只做轻微抖动（换行交给 flex-wrap），
        # 纵向错落有致，制造"漂浮"而不是"排队"的感觉。
        "x": (seed % 21) - 10,
        "y": ((seed >> 3) % 45) - 22,
        "drop": 12 + (seed % 9),
        "fall": 8 + (seed % 7),
    }


def _get_server_db_path():
    env_path = os.environ.get("EMOTION_BUBBLE_DB")
    if env_path:
        return env_path
    base_dir = os.path.dirname(os.path.abspath(__file__))
    server_dir = os.path.join(base_dir, "..", "server_data")
    os.makedirs(server_dir, exist_ok=True)
    return os.path.join(server_dir, "emotion_bubble_server.db")


def _load_secret(name, default=""):
    # 环境变量优先；显式设置（包括空串）都被尊重，允许用空串“强制关闭”某项配置，
    # 避免回落进 secrets.local.py（例如测试想关掉 DATABASE_URL 时）。
    val = os.environ.get(name)
    if val is not None:
        return val
    secrets_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "config", "secrets.local.py")
    if os.path.exists(secrets_path):
        import importlib.util
        spec = importlib.util.spec_from_file_location("secrets_local", secrets_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return getattr(mod, name, default)
    return default


app = Flask(__name__)
app.config["SECRET_KEY"] = _load_secret("SECRET_KEY", "")
app.config["INVITE_CODE"] = _load_secret("INVITE_CODE", "")
# 失效即拒绝启动（fail-closed）：绝不带不安全的默认值上线。
# SECRET_KEY 缺失会让会话可被伪造；INVITE_CODE 缺失会让注册对空邀请码放行。
if not app.config["SECRET_KEY"]:
    raise RuntimeError(
        "缺少 SECRET_KEY：请在 config/secrets.local.py 中设置，或用环境变量 SECRET_KEY 提供。"
        "（拒绝以不安全的默认值启动）"
    )
if not app.config["INVITE_CODE"]:
    raise RuntimeError(
        "缺少 INVITE_CODE：本应用为邀请制，请在 config/secrets.local.py 中设置邀请码，"
        "或用环境变量 INVITE_CODE 提供。"
    )
app.config["DB_PATH"] = _get_server_db_path()
app.config["DATABASE_URL"] = _load_secret("DATABASE_URL", "")
# 跑在 Cloudflare Tunnel 等反向代理后面：让 Flask 正确识别 https 与真实客户端 IP。
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

init_database(app.config["DB_PATH"], app.config.get("DATABASE_URL", ""))
app.teardown_appcontext(close_db)

app.jinja_env.globals["bubble_style"] = _bubble_variant


@app.context_processor
def inject_globals():
    return {
        "current_user": get_current_user(),
        "csrf_token": get_csrf_token(),
        "emotion_icons": EMOTION_ICONS,
        "emotion_colors": EMOTION_COLORS,
        "all_emotions": ALL_EMOTIONS,
    }


@app.errorhandler(403)
def forbidden(e):
    return render_template("403.html"), 403


@app.errorhandler(404)
def not_found(e):
    return render_template("404.html"), 404


# ==================== 泡泡样板（预览）====================
@app.route("/preview")
def preview():
    return render_template("preview.html")


# ==================== 认证 ====================
@app.route("/login")
def login_page():
    if session.get("user_id") is not None:
        return redirect(url_for("index"))
    return render_template("login.html", active_tab="login")


@app.route("/register", methods=["POST"])
def register():
    if not validate_csrf():
        return render_template("login.html", active_tab="register",
                               error="请求无效，请刷新页面重试"), 400
    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""
    invite_code = (request.form.get("invite_code") or "").strip()

    if not username or not password:
        return render_template("login.html", active_tab="register",
                               error="用户名和密码不能为空"), 400
    if len(username) > 20:
        return render_template("login.html", active_tab="register",
                               error="用户名最长 20 个字符"), 400
    if len(password) < 6:
        return render_template("login.html", active_tab="register",
                               error="密码至少 6 位"), 400
    if invite_code != app.config["INVITE_CODE"]:
        return render_template("login.html", active_tab="register",
                               error="邀请码不正确"), 403

    db = get_db()
    if db.get_user_by_username(username):
        return render_template("login.html", active_tab="register",
                               error="这个用户名已经被注册了"), 400

    db.create_user(username, generate_password_hash(password))
    user = db.get_user_by_username(username)
    session.clear()
    session["user_id"] = user["id"]
    return redirect(url_for("index"))


@app.route("/login", methods=["POST"])
def login():
    if not validate_csrf():
        return render_template("login.html", active_tab="login",
                               error="请求无效，请刷新页面重试"), 400
    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""

    if is_login_blocked(username):
        return render_template("login.html", active_tab="login",
                               error="尝试次数过多，请 1 分钟后再试"), 429

    db = get_db()
    user = db.get_user_by_username(username)
    if user is None or not check_password_hash(user["password_hash"], password):
        record_login_attempt(username)
        return render_template("login.html", active_tab="login",
                               error="用户名或密码错误"), 401

    session.clear()
    session["user_id"] = user["id"]
    return redirect(url_for("index"))


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login_page"))


# ==================== 页面 ====================
@app.route("/")
@login_required
def index():
    db = get_db()
    emotion = request.args.get("emotion")
    if emotion and emotion in ALL_EMOTIONS:
        bubbles = db.get_bubbles_by_emotion(emotion)
    else:
        bubbles = db.get_all_active_bubbles()
    return render_template("index.html", bubbles=bubbles, current_emotion=emotion)


@app.route("/partial/bubbles")
@login_required
def partial_bubbles():
    db = get_db()
    emotion = request.args.get("emotion")
    if emotion and emotion in ALL_EMOTIONS:
        bubbles = db.get_bubbles_by_emotion(emotion)
    else:
        bubbles = db.get_all_active_bubbles()
    return render_template("_bubbles.html", bubbles=bubbles)


@app.route("/partial/chat/<conversation_id>")
@login_required
def partial_chat(conversation_id):
    db = get_db()
    conversation = db.get_conversation(conversation_id)
    if not conversation:
        return jsonify({"error": "not found"}), 404
    user = get_current_user()
    if conversation["author_nickname"] != user["username"] and conversation["helper_nickname"] != user["username"]:
        return jsonify({"error": "forbidden"}), 403
    messages = db.get_messages_for_conversation(conversation_id)
    return render_template("_messages.html", messages=messages, current_user=user)


# ==================== 表单处理 ====================
@app.route("/publish", methods=["POST"])
@login_required
def publish():
    if not validate_csrf():
        return jsonify({"error": "invalid csrf"}), 400
    content = (request.form.get("content") or "").strip()
    emotion_tag = request.form.get("emotion_tag") or ""
    if not content:
        return jsonify({"error": "empty"}), 400
    if emotion_tag not in ALL_EMOTIONS:
        return jsonify({"error": "invalid emotion"}), 400
    user = get_current_user()
    db = get_db()
    bubble_id = str(uuid.uuid4())
    db.save_bubble(bubble_id, user["username"], content, emotion_tag, "dialog",
                   author_id=user["id"])
    return jsonify({"ok": True, "bubble_id": bubble_id})


@app.route("/chat/<conversation_id>/send", methods=["POST"])
@login_required
def send_message(conversation_id):
    db = get_db()
    conversation = db.get_conversation(conversation_id)
    if not conversation:
        return jsonify({"error": "not found"}), 404
    if not validate_csrf():
        return jsonify({"error": "invalid csrf"}), 400
    user = get_current_user()
    if conversation["author_nickname"] != user["username"] and conversation["helper_nickname"] != user["username"]:
        return jsonify({"error": "forbidden"}), 403
    content = (request.form.get("content") or "").strip()
    if not content:
        return jsonify({"error": "empty"}), 400
    if user["username"] == conversation["helper_nickname"]:
        receiver = conversation["author_nickname"]
    else:
        receiver = conversation["helper_nickname"]
    db.save_chat_message(conversation["bubble_id"], user["username"], receiver,
                         content, conversation_id=conversation_id)
    return jsonify({"ok": True})


@app.route("/bubble/<bubble_id>/resolve", methods=["POST"])
@login_required
def resolve(bubble_id):
    db = get_db()
    bubble = db.get_bubble(bubble_id)
    if not bubble:
        return jsonify({"error": "not found"}), 404
    if not validate_csrf():
        return jsonify({"error": "invalid csrf"}), 400
    user = get_current_user()
    if bubble["author_id"] != user["id"]:
        return jsonify({"error": "forbidden"}), 403
    db.resolve_bubble(bubble_id)
    return jsonify({"ok": True})


@app.route("/bubble/<bubble_id>/save", methods=["POST"])
@login_required
def save(bubble_id):
    db = get_db()
    bubble = db.get_bubble(bubble_id)
    if not bubble:
        return jsonify({"error": "not found"}), 404
    if not validate_csrf():
        return jsonify({"error": "invalid csrf"}), 400
    user = get_current_user()
    if bubble["author_id"] != user["id"]:
        return jsonify({"error": "forbidden"}), 403
    is_public = request.form.get("is_public") == "true"
    db.save_bubble_as(bubble_id, is_public)
    return jsonify({"ok": True})


# ==================== 侧栏面板 ====================
@app.route("/partial/panel/bubble/<bubble_id>")
@login_required
def panel_bubble(bubble_id):
    db = get_db()
    bubble = db.get_bubble(bubble_id)
    if not bubble:
        return render_template("404.html"), 404
    user = get_current_user()
    is_author = (bubble["author_id"] == user["id"])
    conversation = None
    conversations = []
    if is_author:
        conversations = db.list_conversations_for_bubble(bubble_id)
    else:
        conversation = db.get_or_create_conversation(bubble_id, bubble["author_nickname"], user["username"])
    messages = db.get_messages_for_conversation(conversation["id"]) if conversation else []
    other = None
    if conversation:
        other = conversation["author_nickname"] if conversation["helper_nickname"] == user["username"] else conversation["helper_nickname"]
    return render_template("_sidebar_panel_bubble.html", bubble=bubble, is_author=is_author,
                           conversation=conversation, conversations=conversations,
                           messages=messages, other=other)


@app.route("/partial/panel/conversation/<conversation_id>")
@login_required
def panel_conversation(conversation_id):
    db = get_db()
    conversation = db.get_conversation(conversation_id)
    if not conversation:
        return render_template("404.html"), 404
    user = get_current_user()
    if conversation["author_nickname"] != user["username"] and conversation["helper_nickname"] != user["username"]:
        return render_template("403.html"), 403
    bubble = db.get_bubble(conversation["bubble_id"])
    messages = db.get_messages_for_conversation(conversation_id)
    other = conversation["author_nickname"] if conversation["helper_nickname"] == user["username"] else conversation["helper_nickname"]
    return render_template("_chat_panel.html", conversation=conversation, bubble=bubble,
                           messages=messages, other=other)


@app.route("/partial/panel/conversations")
@login_required
def panel_conversations():
    db = get_db()
    user = get_current_user()
    conversations = db.list_conversations_for_user(user["username"])
    return render_template("_sidebar_panel_conversations.html", conversations=conversations)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)




