"""
server_app.py - 情感泡泡 网页版服务器
提供页面渲染 + 表单处理 + 局部刷新片段。
安全原则：用户身份一律从 session 获取，绝不信任前端提交的 author/sender 等字段。
"""
import hashlib
import os
import time
import uuid
from datetime import datetime, timezone

from flask import (Flask, abort, jsonify, redirect, render_template, request,
                   session, url_for)
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

from .auth import (clear_login_failures, get_current_user, get_csrf_token,
                   is_login_blocked, login_required, record_login_failure,
                   validate_csrf)
from .db import close_db, get_db, init_database

# ==================== 常量 ====================
EMOTION_ICONS = {
    "开心": "😊", "难过": "😢", "孤独": "🥺",
    "焦虑": "😰", "寻求建议": "💡", "找人倾诉": "🗣️",
}
# (浅色, 主色, 背景色)——主色与背景色为 light-dark(亮, 暗)，随元素 color-scheme 自动取支
def _ld(light, dark):
    """亮/暗双值颜色：跟随元素 color-scheme，支持系统偏好与 data-theme 手动覆盖。"""
    return f"light-dark({light},{dark})"


EMOTION_COLORS = {
    "开心": ("#FFE066", _ld("#FFB800", "#F5A524"), _ld("#FFF8DC", "rgba(245,165,36,.16)")),
    "难过": ("#74B9FF", _ld("#0984E3", "#6FA8FF"), _ld("#E8F4FD", "rgba(59,111,224,.18)")),
    "孤独": ("#C59BFF", _ld("#8854D0", "#A98BFF"), _ld("#F3EAFF", "rgba(123,90,214,.20)")),
    "焦虑": ("#FF8A8A", _ld("#FF4757", "#FF7A93"), _ld("#FFE8E8", "rgba(224,74,110,.18)")),
    "寻求建议": ("#7BED9F", _ld("#2ED573", "#3FD9A0"), _ld("#E8F8F0", "rgba(24,168,124,.18)")),
    "找人倾诉": ("#FFB78C", _ld("#FF6348", "#FF9A5C"), _ld("#FFF0E8", "rgba(232,118,60,.18)")),
}
ALL_EMOTIONS = ["开心", "难过", "孤独", "焦虑", "寻求建议", "找人倾诉"]

# ==================== 泡泡样式（确定性随机）====================
# 泡泡膜色板：每情绪 5 色，亮/暗两套。
# 暗色套取中明度、高饱和——深底上高明度浅色会变成荧光斑，膜层靠饱和度而非明度出彩。
# 亮色套：原来整组都是 ~90% 明度的粉彩，盖在浅背景上几乎看不见。
# 往下压一档饱和度——仍然柔和，但薄膜色要"看得见"才谈得上虹彩。
_DREAMY_LIGHT = {
    "开心": ["#ffd166", "#ffb877", "#ffe9a3", "#ffb3c9", "#ffdca0"],
    "难过": ["#8fb8ff", "#6fa3ff", "#b3daff", "#c9c4ff", "#7fb0ff"],
    "孤独": ["#b8a0ff", "#9d84ff", "#d4c4ff", "#c2b0ff", "#a88fff"],
    "焦虑": ["#ff9eb8", "#ff85a8", "#ffc4d4", "#ffb3c8", "#ff95b0"],
    "寻求建议": ["#8fe8c8", "#6fdcb4", "#b8f0dc", "#9ee8cc", "#7fe0bc"],
    "找人倾诉": ["#ffbe8f", "#ffa570", "#ffd8b8", "#ffc4a0", "#ffb080"],
}
_DREAMY_DARK = {
    "开心": ["#F5A524", "#E07B39", "#FFD98A", "#FF9E7D", "#E8963C"],
    "难过": ["#3B6FE0", "#2A4FC4", "#6FA8FF", "#89B8FF", "#4A7FE8"],
    "孤独": ["#7B5AD6", "#5B3FB8", "#A98BFF", "#C0A6FF", "#8B6AE0"],
    "焦虑": ["#E04A6E", "#C22A52", "#FF7A93", "#FFA3AE", "#E85A7E"],
    "寻求建议": ["#18A87C", "#0D8A66", "#3FD9A0", "#6FE8C4", "#28B88C"],
    "找人倾诉": ["#E8763C", "#C4551C", "#FF9A5C", "#FFB98A", "#E8864C"],
}
_DREAMY_GLOW_LIGHT = {
    "开心": "rgba(255,190,110,.5)",
    "难过": "rgba(120,160,255,.5)",
    "孤独": "rgba(175,140,255,.5)",
    "焦虑": "rgba(255,150,180,.5)",
    "寻求建议": "rgba(120,230,180,.5)",
    "找人倾诉": "rgba(255,170,120,.5)",
}
_DREAMY_GLOW_DARK = {
    "开心": "rgba(255,170,60,.30)",
    "难过": "rgba(70,130,255,.28)",
    "孤独": "rgba(140,105,255,.28)",
    "焦虑": "rgba(255,80,120,.28)",
    "寻求建议": "rgba(40,220,160,.26)",
    "找人倾诉": "rgba(255,130,60,.28)",
}
# 深色模式的中心内核：深底上的肥皂泡是"边缘亮、中心暗"（薄膜边缘效应），
# 文字所在的中心必须压暗，否则近白文字对中明度膜色只有 ~2:1。
# 深色模式的中心内核：目标是"内部发光"，所以用饱和的深色而不是近黑色——
# 近黑会让泡泡中心变成一个洞（像发光环而不是泡泡）。
# 文字可读性不靠压黑内核，而靠 --soap-text-scrim 这层文字暗衬（见 style.css）。
_DREAMY_CORE_DARK = {
    "开心": "rgba(122, 74, 12, .52)",
    "难过": "rgba(30, 60, 142, .55)",
    "孤独": "rgba(74, 46, 152, .55)",
    "焦虑": "rgba(122, 28, 60, .55)",
    "寻求建议": "rgba(12, 94, 68, .52)",
    "找人倾诉": "rgba(122, 52, 16, .52)",
}


def _bubble_variant(bubble):
    """按泡泡 id 做确定性哈希，生成稳定的随机样式（刷新不闪烁）。"""
    key = (bubble["id"] + bubble["emotion_tag"]).encode("utf-8")
    seed = int(hashlib.md5(key).hexdigest(), 16)
    emotion = bubble["emotion_tag"] if bubble["emotion_tag"] in _DREAMY_LIGHT else "开心"
    pal_l, pal_d = _DREAMY_LIGHT[emotion], _DREAMY_DARK[emotion]
    n = len(pal_l)
    # 同一组色位索引同时作用于亮/暗两套色板：同一 id 永远得到同一组合，刷新不闪烁；
    # 具体取哪套由元素 color-scheme 决定（light-dark() 在内联样式里同样生效）。
    i1, i2, i3, i4 = seed % n, (seed >> 4) % n, (seed >> 8) % n, (seed >> 12) % n
    # 虹彩偏移：真正的肥皂膜是"光谱"，同一色系的深浅叠多少层都只是一片均匀的雾。
    # 所以 4 个色位里留两个给已有的虹彩 token（粉/玫/金/薄荷），另两个给情绪色——
    # 情绪仍是主导色相（内核、辉光、多数色位都是它），色彩编码不丢。
    irises = ("var(--iris-1)", "var(--iris-2)", "var(--iris-3)", "var(--iris-4)")
    accent_a, accent_b = irises[(seed >> 16) % 4], irises[(seed >> 20) % 4]
    content_len = len(bubble["content"])
    size = max(120, min(196, 122 + min(content_len, 24) * 3 + (seed % 16)))
    return {
        "size": size,
        "c1": _ld(pal_l[i1], pal_d[i1]),
        "c2": accent_a,
        "c3": _ld(pal_l[i3], pal_d[i3]),
        "c4": accent_b,
        "glow": _ld(_DREAMY_GLOW_LIGHT.get(emotion, "rgba(190,170,255,.5)"),
                    _DREAMY_GLOW_DARK.get(emotion, "rgba(150,120,255,.32)")),
        # 中心内核：亮色模式下与 glow 同色（亮核）；深色模式下压暗（暗核，保文字对比度）
        "core": _ld(_DREAMY_GLOW_LIGHT.get(emotion, "rgba(190,170,255,.5)"),
                    _DREAMY_CORE_DARK.get(emotion, "rgba(20,14,44,.55)")),
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
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
# Secure 由 _harden_session_cookie 按每次请求的协议动态决定：
# 走 https（Cloudflare 隧道）时打开，本地 http 调试时关闭，否则登录态根本发不出去。
app.config["SESSION_COOKIE_SECURE"] = False
# 跑在 Cloudflare Tunnel 等反向代理后面：让 Flask 正确识别 https 与真实客户端 IP。
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

init_database(app.config["DB_PATH"], app.config.get("DATABASE_URL", ""))
app.teardown_appcontext(close_db)

app.jinja_env.globals["bubble_style"] = _bubble_variant

# ==================== 安全响应头 ====================
# 只允许加载本站资源；内联 script/style 是现有模板的写法，暂时放行。
_CSP = (
    "default-src 'self'; img-src 'self' data:; font-src 'self'; "
    "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
    "connect-src 'self'; base-uri 'self'; frame-ancestors 'none'"
)


@app.after_request
def _security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("Content-Security-Policy", _CSP)
    return resp


@app.before_request
def _harden_session_cookie():
    app.config["SESSION_COOKIE_SECURE"] = request.is_secure


@app.context_processor
def inject_globals():
    return {
        "current_user": get_current_user(),
        "csrf_token": get_csrf_token(),
        "emotion_icons": EMOTION_ICONS,
        "emotion_colors": EMOTION_COLORS,
        "all_emotions": ALL_EMOTIONS,
    }


@app.errorhandler(400)
def bad_request(e):
    return render_template("400.html"), 400


@app.errorhandler(403)
def forbidden(e):
    return render_template("403.html"), 403


@app.errorhandler(404)
def not_found(e):
    return render_template("404.html"), 404


# ==================== 泡泡样板（预览）====================
@app.route("/preview")
def preview():
    """材质试验台：同一颗泡泡在亮/暗两种 color-scheme 下并排放大显示。

    颜色全部走 _bubble_variant()，和生产完全同一条路径——
    否则试验台上好看、真页面难看，等于白试。
    """
    samples = [
        ("孤独", "最近有点孤独"),
        ("开心", "今天超开心！"),
        ("难过", "好想找人说说话"),
    ]
    bubbles = []
    for i, (emotion, content) in enumerate(samples):
        fake = {"id": f"lab-{i}", "emotion_tag": emotion, "content": content}
        bubbles.append({"v": _bubble_variant(fake), "emotion": emotion, "content": content})
    return render_template("preview.html", bubbles=bubbles)


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

    if is_login_blocked():
        return render_template("login.html", active_tab="login",
                               error="尝试次数过多，请 1 分钟后再试"), 429

    db = get_db()
    user = db.get_user_by_username(username)
    if user is None or not check_password_hash(user["password_hash"], password):
        record_login_failure()
        return render_template("login.html", active_tab="login",
                               error="用户名或密码错误"), 401

    clear_login_failures()
    session.clear()
    session["user_id"] = user["id"]
    return redirect(url_for("index"))


@app.route("/logout", methods=["POST"])
def logout():
    # 登出也要校验 CSRF：否则第三方页面可以静默把你踢下线。
    if not validate_csrf():
        abort(400)
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


# ==================== 情绪洞察 ====================
def _epoch_ms(value):
    """把库里的时间字符串转成 epoch 毫秒。

    历史数据存在两种写法：带微秒的 naive UTC（2026-10-03T20:11:11.560720）
    和秒级（2026-07-21T16:00:00）。naive 一律按 UTC 解释——
    库里的约定就是 UTC（见 database.py 的 _now()），不按本地时区猜。
    解析不了返回 None，由调用方跳过，绝不让一条脏数据把整个面板打挂。
    """
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            try:
                dt = datetime.strptime(text[:19], "%Y-%m-%dT%H:%M:%S")
            except ValueError:
                return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


@app.route("/api/insights")
@login_required
def api_insights():
    """洞察面板数据：每条泡泡只给 (情绪, 时间戳)，按访问者本地时区分桶。"""
    data = get_db().get_insight_data()
    points = []
    for row in data["bubbles"]:
        ts = _epoch_ms(row["created_at"])
        if ts is None:
            continue
        points.append({"e": row["emotion_tag"], "t": ts})
    resp = jsonify({
        "generated_at": int(time.time() * 1000),
        "totals": {
            "active": len(points),
            "conversations": data["conversations"],
            "messages": data["messages"],
            "resolved": data["resolved"],
        },
        "points": points,
    })
    # 聚合结果随时间变化，且包含全站动态，不允许任何中间层缓存
    resp.headers["Cache-Control"] = "no-store"
    return resp


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




