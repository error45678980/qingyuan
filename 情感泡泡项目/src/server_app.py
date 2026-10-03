"""
server_app.py - 情感泡泡 网页版服务器
提供页面渲染 + 表单处理 + 局部刷新片段。
安全原则：用户身份一律从 session 获取，绝不信任前端提交的 author/sender 等字段。
"""
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


def _get_server_db_path():
    env_path = os.environ.get("EMOTION_BUBBLE_DB")
    if env_path:
        return env_path
    base_dir = os.path.dirname(os.path.abspath(__file__))
    server_dir = os.path.join(base_dir, "..", "server_data")
    os.makedirs(server_dir, exist_ok=True)
    return os.path.join(server_dir, "emotion_bubble_server.db")


def _load_secret(name, default=""):
    val = os.environ.get(name)
    if val:
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
app.config["SECRET_KEY"] = _load_secret("SECRET_KEY", "dev-secret-change-me")
app.config["INVITE_CODE"] = _load_secret("INVITE_CODE", "")
app.config["DB_PATH"] = _get_server_db_path()
# 跑在 Cloudflare Tunnel 等反向代理后面：让 Flask 正确识别 https 与真实客户端 IP。
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

init_database(app.config["DB_PATH"])
app.teardown_appcontext(close_db)


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


@app.route("/publish")
@login_required
def publish_page():
    return render_template("publish.html")


@app.route("/bubble/<bubble_id>")
@login_required
def bubble_detail(bubble_id):
    db = get_db()
    bubble = db.get_bubble(bubble_id)
    if not bubble:
        return render_template("404.html"), 404
    user = get_current_user()
    is_author = (bubble["author_id"] == user["id"])
    replies = db.get_replies(bubble_id)
    conversation = None
    conversations = []
    if bubble["reply_mode"] == "dialog":
        if is_author:
            conversations = db.list_conversations_for_bubble(bubble_id)
        else:
            conversation = db.get_conversation_by_participants(bubble_id, user["username"])
    has_replied = db.has_replied(bubble_id, user["username"])
    return render_template("bubble_detail.html", bubble=bubble, is_author=is_author,
                           replies=replies, conversation=conversation,
                           conversations=conversations, has_replied=has_replied)


@app.route("/chat/<conversation_id>")
@login_required
def chat_page(conversation_id):
    db = get_db()
    conversation = db.get_conversation(conversation_id)
    if not conversation:
        return render_template("404.html"), 404
    user = get_current_user()
    if conversation["author_nickname"] != user["username"] and conversation["helper_nickname"] != user["username"]:
        return render_template("403.html"), 403
    bubble = db.get_bubble(conversation["bubble_id"])
    messages = db.get_messages_for_conversation(conversation_id)
    if conversation["helper_nickname"] == user["username"]:
        other = conversation["author_nickname"]
    else:
        other = conversation["helper_nickname"]
    return render_template("chat.html", conversation=conversation, bubble=bubble,
                           messages=messages, other=other)


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


@app.route("/chats")
@login_required
def chats_page():
    db = get_db()
    user = get_current_user()
    conversations = db.list_conversations_for_user(user["username"])
    return render_template("chats.html", conversations=conversations)


# ==================== 表单处理 ====================
@app.route("/publish", methods=["POST"])
@login_required
def publish():
    if not validate_csrf():
        flash("请求无效，请重试", "error")
        return redirect(url_for("publish_page"))
    content = (request.form.get("content") or "").strip()
    emotion_tag = request.form.get("emotion_tag") or ""
    reply_mode = request.form.get("reply_mode") or "single"

    if not content:
        flash("写点什么再发布吧", "error")
        return redirect(url_for("publish_page"))
    if emotion_tag not in ALL_EMOTIONS:
        flash("请选择一个情感标签", "error")
        return redirect(url_for("publish_page"))
    if reply_mode not in ("single", "dialog"):
        reply_mode = "single"

    user = get_current_user()
    db = get_db()
    bubble_id = str(uuid.uuid4())
    db.save_bubble(bubble_id, user["username"], content, emotion_tag, reply_mode,
                   author_id=user["id"])
    flash("泡泡发布成功 🌟", "success")
    return redirect(url_for("bubble_detail", bubble_id=bubble_id))


@app.route("/bubble/<bubble_id>/reply", methods=["POST"])
@login_required
def post_reply(bubble_id):
    db = get_db()
    bubble = db.get_bubble(bubble_id)
    if not bubble:
        return render_template("404.html"), 404
    if not validate_csrf():
        flash("请求无效，请重试", "error")
        return redirect(url_for("bubble_detail", bubble_id=bubble_id))
    user = get_current_user()
    if bubble["author_id"] == user["id"]:
        flash("不能回复自己的泡泡哦", "error")
        return redirect(url_for("bubble_detail", bubble_id=bubble_id))
    if bubble["reply_mode"] != "single":
        return redirect(url_for("bubble_detail", bubble_id=bubble_id))
    if db.has_replied(bubble_id, user["username"]):
        flash("这个泡泡只能回复一次哦～", "error")
        return redirect(url_for("bubble_detail", bubble_id=bubble_id))
    content = (request.form.get("content") or "").strip()
    if not content:
        flash("回复不能为空", "error")
        return redirect(url_for("bubble_detail", bubble_id=bubble_id))
    db.save_reply(bubble_id, user["username"], content)
    flash("回复成功，谢谢你的关心 💕", "success")
    return redirect(url_for("bubble_detail", bubble_id=bubble_id))


@app.route("/bubble/<bubble_id>/chat", methods=["POST"])
@login_required
def start_chat(bubble_id):
    db = get_db()
    bubble = db.get_bubble(bubble_id)
    if not bubble:
        return render_template("404.html"), 404
    if not validate_csrf():
        flash("请求无效，请重试", "error")
        return redirect(url_for("bubble_detail", bubble_id=bubble_id))
    user = get_current_user()
    if bubble["author_id"] == user["id"]:
        flash("不能和自己聊天哦", "error")
        return redirect(url_for("bubble_detail", bubble_id=bubble_id))
    if bubble["reply_mode"] != "dialog":
        flash("这个泡泡没有开启对话通道", "error")
        return redirect(url_for("bubble_detail", bubble_id=bubble_id))
    content = (request.form.get("content") or "").strip()
    if not content:
        flash("说点什么吧", "error")
        return redirect(url_for("bubble_detail", bubble_id=bubble_id))

    conversation = db.get_or_create_conversation(bubble_id, bubble["author_nickname"], user["username"])
    db.save_chat_message(bubble_id, user["username"], bubble["author_nickname"],
                         content, conversation_id=conversation["id"])
    return redirect(url_for("chat_page", conversation_id=conversation["id"]))


@app.route("/chat/<conversation_id>/send", methods=["POST"])
@login_required
def send_message(conversation_id):
    db = get_db()
    conversation = db.get_conversation(conversation_id)
    if not conversation:
        return render_template("404.html"), 404
    if not validate_csrf():
        flash("请求无效，请重试", "error")
        return redirect(url_for("chat_page", conversation_id=conversation_id))
    user = get_current_user()
    if conversation["author_nickname"] != user["username"] and conversation["helper_nickname"] != user["username"]:
        return render_template("403.html"), 403
    content = (request.form.get("content") or "").strip()
    if not content:
        return redirect(url_for("chat_page", conversation_id=conversation_id))
    if user["username"] == conversation["helper_nickname"]:
        receiver = conversation["author_nickname"]
    else:
        receiver = conversation["helper_nickname"]
    db.save_chat_message(conversation["bubble_id"], user["username"], receiver,
                         content, conversation_id=conversation_id)
    return redirect(url_for("chat_page", conversation_id=conversation_id))


@app.route("/bubble/<bubble_id>/resolve", methods=["POST"])
@login_required
def resolve(bubble_id):
    db = get_db()
    bubble = db.get_bubble(bubble_id)
    if not bubble:
        return render_template("404.html"), 404
    if not validate_csrf():
        flash("请求无效，请重试", "error")
        return redirect(url_for("bubble_detail", bubble_id=bubble_id))
    user = get_current_user()
    if bubble["author_id"] != user["id"]:
        return render_template("403.html"), 403
    db.resolve_bubble(bubble_id)
    flash("泡泡已经炸掉消失啦 💥", "success")
    return redirect(url_for("index"))


@app.route("/bubble/<bubble_id>/save", methods=["POST"])
@login_required
def save(bubble_id):
    db = get_db()
    bubble = db.get_bubble(bubble_id)
    if not bubble:
        return render_template("404.html"), 404
    if not validate_csrf():
        flash("请求无效，请重试", "error")
        return redirect(url_for("bubble_detail", bubble_id=bubble_id))
    user = get_current_user()
    if bubble["author_id"] != user["id"]:
        return render_template("403.html"), 403
    is_public = request.form.get("is_public") == "true"
    db.save_bubble_as(bubble_id, is_public)
    flash("已保存 💾", "success")
    return redirect(url_for("bubble_detail", bubble_id=bubble_id))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)




