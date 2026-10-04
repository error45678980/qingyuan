# -*- coding: utf-8 -*-
"""把真实的网页版渲染成截图，供设计评审。

不依赖 Playwright：用 Flask test_client 渲染（已登录）→ 落地成静态 HTML
→ 交给系统 Chrome headless 截图。

工程细节：
- Chrome 是异步写截图的，必须轮询等文件落盘。
- 全程走 ASCII 临时路径，避免中文路径踩坑；最后把 PNG 拷回 tools/shots。
- 强制临时 SQLite（DATABASE_URL 置空），绝不连生产库。

用法：python tools/shoot.py
输出：tools/shots/*.png
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SHOTS = ROOT / "tools" / "shots"
WORK = Path(tempfile.gettempdir()) / "eb_shots"
HTML_DIR = WORK / "html"
PNG_DIR = WORK / "png"
CSS_DIR = WORK / "css"
DEMO_DB = WORK / "demo.db"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"


def prepare():
    if WORK.exists():
        shutil.rmtree(WORK, ignore_errors=True)
    for d in (SHOTS, HTML_DIR, PNG_DIR, CSS_DIR):
        d.mkdir(parents=True, exist_ok=True)
    # CSS 复制到 ASCII 路径，让 Chrome 只碰纯英文路径
    shutil.copy(ROOT / "src" / "static" / "css" / "style.css", CSS_DIR / "style.css")

    os.environ["EMOTION_BUBBLE_DB"] = str(DEMO_DB)
    os.environ["DATABASE_URL"] = ""          # 强制 SQLite，绝不碰生产 Neon
    os.environ["SECRET_KEY"] = "shots-only-secret"
    os.environ["INVITE_CODE"] = "SHOTS-ONLY"


def seed():
    from werkzeug.security import generate_password_hash
    from src.database import Database

    db = Database(db_path=str(DEMO_DB))
    uid1 = db.create_user("小明", generate_password_hash("test1234"))
    uid2 = db.create_user("小红", generate_password_hash("test1234"))

    now = datetime.now()
    data = [
        (uid1, "小明", "在吗\n晚上一起吃饭", "找人倾诉"),
        (uid2, "小红", "今天下班路上看到一只橘猫，突然就没那么难过了", "开心"),
        (uid1, "小明", "最近总是睡不好，脑子里事情太多", "焦虑"),
        (uid2, "小红", "有人也喜欢下雨天吗", "孤独"),
        (uid1, "小明", "刚面试完，感觉还行", "开心"),
        (uid2, "小红", "到底要不要换工作，好纠结", "寻求建议"),
    ]
    ids = []
    for i, (uid, name, content, emo) in enumerate(data):
        bid = str(uuid.uuid4())
        ids.append(bid)
        db.save_bubble(bid, name, content, emo, "dialog",
                       created_at=(now - timedelta(minutes=i * 7)).isoformat(),
                       author_id=uid)

    conv = db.get_or_create_conversation(ids[0], "小明", "小红")
    for sender, receiver, text in (
        ("小红", "小明", "在的呀，几点？"),
        ("小明", "小红", "七点，老地方～"),
        ("小红", "小明", "好，我可能晚十分钟到"),
    ):
        db.save_chat_message(ids[0], sender, receiver, text, conversation_id=conv["id"])
    db.close()
    return ids, conv["id"]


def to_static(html: str) -> str:
    """把 CSS 引用改成本地 ASCII file:// 路径。"""
    return html.replace("/static/css/style.css", (CSS_DIR / "style.css").as_uri())


def open_sidebar(html: str, panel_html: str) -> str:
    html = html.replace('<div class="sidebar-body" id="sidebar-body"></div>',
                        f'<div class="sidebar-body" id="sidebar-body">{panel_html}</div>')
    html = html.replace('class="sidebar" id="sidebar"', 'class="sidebar open" id="sidebar"')
    html = html.replace('class="sidebar-overlay" id="sidebar-overlay"',
                        'class="sidebar-overlay open" id="sidebar-overlay"')
    return html


def open_modal(html: str) -> str:
    return html.replace('class="modal-overlay" id="publish-modal"',
                        'class="modal-overlay open" id="publish-modal"')


def chrome_shot(html_path: Path, out: Path, width: int, height: int, scale: int = 2) -> bool:
    """Chrome 异步写文件，必须轮询等待。"""
    if out.exists():
        out.unlink()
    profile = WORK / f"profile-{uuid.uuid4().hex[:8]}"
    cmd = [
        CHROME, "--headless", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
        f"--user-data-dir={profile}",
        f"--force-device-scale-factor={scale}",
        f"--window-size={width},{height}",
        f"--screenshot={out}", html_path.as_uri(),
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ok = False
    for _ in range(120):                     # 最多等 30 秒
        if out.exists() and out.stat().st_size > 1000:
            time.sleep(0.4)                  # 让写盘落定
            ok = out.stat().st_size > 1000
            break
        time.sleep(0.25)
    proc.terminate()
    shutil.rmtree(profile, ignore_errors=True)
    return ok


def main():
    prepare()
    bubble_ids, conv_id = seed()

    from src.server_app import app
    client = app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = 1            # 以「小明」身份渲染

    anon = app.test_client()
    wall = client.get("/").get_data(as_text=True)

    pages = {
        "01_login": (to_static(anon.get("/login").get_data(as_text=True)), 1440, 900),
        "02_wall": (to_static(wall), 1440, 1500),
        "03_publish_modal": (open_modal(to_static(wall)), 1440, 1000),
        "04_sidebar_author": (open_sidebar(to_static(wall),
                                           client.get(f"/partial/panel/bubble/{bubble_ids[0]}").get_data(as_text=True)), 1440, 1000),
        "05_sidebar_conversations": (open_sidebar(to_static(wall),
                                                  client.get("/partial/panel/conversations").get_data(as_text=True)), 1440, 1000),
        "06_sidebar_chat": (open_sidebar(to_static(wall),
                                         client.get(f"/partial/panel/conversation/{conv_id}").get_data(as_text=True)), 1440, 1000),
        "07_wall_mobile": (to_static(wall), 390, 1400),
    }

    print(f"渲染 {len(pages)} 个页面 -> {SHOTS}")
    ok = 0
    for name, (html, w, h) in pages.items():
        html_path = HTML_DIR / f"{name}.html"
        html_path.write_text(html, encoding="utf-8")
        png = PNG_DIR / f"{name}.png"
        scale = 2 if w > 500 else 3
        if chrome_shot(html_path, png, w, h, scale):
            shutil.copy(png, SHOTS / f"{name}.png")
            ok += 1
            print(f"  [OK]   {name}.png  {w}x{h}  {png.stat().st_size // 1024}KB")
        else:
            print(f"  [FAIL] {name}")
    print(f"\n完成 {ok}/{len(pages)} 张 -> {SHOTS}")


if __name__ == "__main__":
    main()
