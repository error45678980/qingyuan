"""离屏截图工具（视觉自检用）。

用 `WA_DontShowOnScreen`：不弹窗、但能拿到真实字体渲染（视觉规格 §6-3）。
`QT_QPA_PLATFORM=offscreen` 下没有字体，不要用它截文字。

用法：
    python tools/shoot.py            # 截全部场景
    python tools/shoot.py wall chat  # 只截指定场景

输出：`tools/shots/v2/{wall,my,publish,detail,chat}.png`
数据：临时目录里的一份演示库（不触碰 `~/.Qingyuan/qingyuan.db`）。
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtWidgets import QApplication, QWidget  # noqa: E402

from src.core.migrations import ensure_client_schema  # noqa: E402
from src.core.models import Bubble, ChatMessage, Reply  # noqa: E402
from src.core.repo import Repo  # noqa: E402
from src.core.timeutil import now_iso, shift_seconds  # noqa: E402

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shots", "v2")
WIDTH, HEIGHT = 1200, 800

#: 演示数据：`(正文, 情感, 模式, 作者)`。
DEMO: tuple[tuple[str, str, str, str], ...] = (
    ("在吗 晚上一起吃饭", "找人倾诉", "dialog", "小红"),
    ("今天下班路上看到一只橘猫，蹲在便利店门口，好像在等谁", "开心", "single", "小明"),
    ("最近总是睡不好，脑子一停下来就开始胡思乱想，白天又困得不行，有点撑不住了", "焦虑", "dialog", "阿茶"),
    ("有人也喜欢下雨天吗", "孤独", "single", "柚子"),
    ("刚面试完，感觉还行", "开心", "single", "Lily"),
    ("到底要不要换工作，现在这份稳定但没成长，新的机会又不确定", "寻求建议", "dialog", "老周"),
    ("想找个人说说话", "孤独", "dialog", "小北"),
    ("今天被夸了，开心到飞起", "开心", "single", "小满"),
    ("房租又要涨了，压力好大", "焦虑", "single", "阿茶"),
    ("有没有人跟我一样，明明很累却睡不着，第二天还要早起上班，恶性循环", "焦虑", "dialog", "柚子"),
    ("谢谢你昨天陪我聊到很晚，真的有用", "找人倾诉", "single", "Lily"),
    ("难过的时候你们都会做什么", "难过", "dialog", "小北"),
    ("一个人吃饭真的有点孤单", "孤独", "single", "老周"),
    ("考研失败了，不知道下一步该做什么，感觉所有人都在往前跑，只有我停在原地", "难过", "dialog", "小满"),
    ("下雨天适合听歌，也适合什么都不做", "开心", "single", "小明"),
    ("朋友说我太敏感了，可我真的控制不住", "难过", "dialog", "柚子"),
)


def _seed(repo: Repo) -> list[Bubble]:
    """写入演示数据并返回泡泡列表（按时间倒序）。"""
    base = now_iso()
    bubbles: list[Bubble] = []
    for index, (content, emotion, mode, author) in enumerate(DEMO):
        bubble = Bubble(
            id=f"demo-{index:02d}",
            author_nickname=author,
            author_client_id=f"cid-{author}",
            content=content,
            emotion_tag=emotion,
            reply_mode=mode,
            status="saved_public" if index in (10, 14) else "active",
            version=1,
            created_at=shift_seconds(base, -(index + 1) * 900),
            reply_count=2 if index == 2 else 0,
        )
        repo.upsert_bubble(bubble)
        bubbles.append(bubble)

    target = bubbles[2]
    for offset, (who, text) in enumerate(
        (("阿茶", "我也一样，最近总在凌晨三点醒"), ("小北", "要不要试试睡前不看手机"))
    ):
        repo.upsert_reply(
            Reply(
                id=0,
                bubble_id=target.id,
                replier_nickname=who,
                replier_client_id=f"cid-{who}",
                content=text,
                created_at=shift_seconds(target.created_at, 300 + offset * 120),
                op_id=f"demo-reply-{offset}",
            )
        )

    chat_bubble = bubbles[0]
    messages = (
        ("小红", "小明", "在的呀，几点？"),
        ("小明", "小红", "七点，老地方～"),
        ("小红", "小明", "好，我可能晚十分钟到"),
    )
    for offset, (sender, receiver, text) in enumerate(messages):
        repo.upsert_chat(
            ChatMessage(
                id=0,
                bubble_id=chat_bubble.id,
                sender_nickname=sender,
                sender_client_id=f"cid-{sender}",
                receiver_nickname=receiver,
                receiver_client_id=f"cid-{receiver}",
                content=text,
                created_at=shift_seconds(chat_bubble.created_at, 600 + offset * 90),
                op_id=f"demo-chat-{offset}",
            )
        )
    return bubbles


def _workspace() -> str:
    """建一个带演示数据的临时库。"""
    workspace = tempfile.mkdtemp(prefix="bubble_shots_")
    db_path = os.path.join(workspace, "demo.db")
    ensure_client_schema(db_path, os.path.join(workspace, "backup"))
    repo = Repo(db_path)
    _seed(repo)
    repo.close()
    return db_path


def _pump(app: QApplication, milliseconds: int = 500) -> None:
    """跑事件循环若干毫秒（让动画/布局稳定）。"""
    deadline = int(milliseconds)
    while deadline > 0:
        app.processEvents()
        app.thread().msleep(20)
        deadline -= 20


def _shot(app: QApplication, widget: QWidget, name: str) -> str:
    """渲染并保存一张图。"""
    widget.setAttribute(Qt.WA_DontShowOnScreen, True)
    widget.resize(widget.size())
    widget.show()
    _pump(app, 600)
    path = os.path.join(OUT_DIR, f"{name}.png")
    widget.grab().save(path)
    widget.hide()
    return path


def main(argv: list[str]) -> int:
    """入口。"""
    os.makedirs(OUT_DIR, exist_ok=True)
    wanted = set(argv[1:]) or {"wall", "my", "publish", "detail", "chat"}

    app = QApplication(sys.argv[:1])
    from src.ui import theme
    from src.ui.pages.chat_drawer import ChatDrawer
    from src.ui.pages.detail_drawer import DetailDrawer
    from src.ui.pages.my_page import MyPage
    from src.ui.pages.publish_page import PublishPage
    from src.ui.pages.wall_page import WallPage
    from src.ui.widgets.animation import BubbleAnimator
    from src.ui.widgets.drawer import Drawer

    app.setFont(theme.font(10))
    db_path = _workspace()
    repo = Repo(db_path)
    bubbles = repo.list_public(limit=100)
    animator = BubbleAnimator()
    produced: list[str] = []

    if "wall" in wanted:
        wall = WallPage(animator, nickname="小明")
        wall.resize(WIDTH, HEIGHT)
        wall.apply_bubbles(bubbles, total=len(bubbles), has_more=False)
        produced.append(_shot(app, wall, "wall"))

    if "my" in wanted:
        mine = MyPage(animator, nickname="Tester")
        mine.resize(WIDTH, HEIGHT)
        mine.apply_counts(
            {"published": 8, "saved_public": 2, "saved_private": 1, "resolved": 3}
        )
        mine.apply_group(
            "published",
            [
                bubble
                for bubble in bubbles
                if bubble.author_nickname in ("小明", "Tester", "阿茶", "小满")
            ],
        )
        produced.append(_shot(app, mine, "my"))

    if "publish" in wanted:
        publish = PublishPage()
        publish.resize(WIDTH, HEIGHT)
        publish.apply_draft(
            {
                "content": "今天下班路上看到一只橘猫，蹲在便利店门口，好像在等谁。我站着看了它很久，忽然觉得没那么累了。",
                "emotion_tag": "开心",
                "reply_mode": "single",
            }
        )
        produced.append(_shot(app, publish, "publish"))

    def _overlay() -> tuple[QWidget, Drawer]:
        """墙面 + 抽屉的组合画布（模拟参考图里的模糊背景）。"""
        root = QWidget()
        root.resize(WIDTH, HEIGHT)
        wall = WallPage(animator, nickname="小明")
        wall.setParent(root)
        wall.resize(WIDTH, HEIGHT)
        wall.apply_bubbles(bubbles, total=len(bubbles), has_more=False)
        drawer = Drawer(root)
        drawer.setGeometry(root.rect())
        return root, drawer

    if "detail" in wanted:
        root, drawer = _overlay()
        target = bubbles[2]
        detail = DetailDrawer("小北", "cid-小北")
        drawer.open(detail)
        detail.render(
            target,
            repo.list_replies(target.id),
            chats=[],
            my_reply_count=0,
            has_chatted=False,
        )
        produced.append(_shot(app, root, "detail"))

    if "chat" in wanted:
        root, drawer = _overlay()
        target = bubbles[0]
        chat = ChatDrawer("小明", target, "小红")
        drawer.open(chat)
        chat.render(target, "小红", repo.list_chats(target.id, peer="小红"))
        produced.append(_shot(app, root, "chat"))

    repo.close()
    shutil.rmtree(os.path.dirname(db_path), ignore_errors=True)
    for path in produced:
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
