"""临时冒烟脚本：真实启动客户端并走一遍主流程（跑完即可删除）。

覆盖：
    1. 旧库迁移（6 条记录）；
    2. 登录 → 泡泡墙（拉取服务端数据）；
    3. 打开详情耗时 < 300ms（R-03）；
    4. 发布 → 墙上出现新泡泡；
    5. 打开对话抽屉；
    6. 关闭窗口时写入窗口几何。
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

HOME = tempfile.mkdtemp(prefix="bubble_smoke_")
os.environ["EMOTION_BUBBLE_HOME"] = HOME
shutil.copy(
    os.path.join(ROOT, "tests", "fixtures", "legacy_client.db"),
    os.path.join(HOME, "emotion_bubble.db"),
)

from tests.qt_env import app  # noqa: E402

from src.core.api import ApiClient  # noqa: E402
from src.core.config import backup_dir, config_path, db_path, load_or_create  # noqa: E402
from src.core.logging_setup import setup_logging  # noqa: E402
from src.core.migrations import ensure_client_schema  # noqa: E402
from src.core.repo import Repo  # noqa: E402
from src.ui.app_window import AppWindow  # noqa: E402


def pump(ms: int) -> None:
    application = app()
    deadline = ms
    while deadline > 0:
        application.processEvents()
        application.thread().msleep(20)
        deadline -= 20


def main() -> int:
    setup_logging(None)
    config = load_or_create()
    result = ensure_client_schema(db_path(), backup_dir())
    print("迁移:", result.outcome, dict(result.before), "->", dict(result.after))
    print("备份:", result.backup_path)
    repo = Repo(db_path())
    print("repo ok")
    api = ApiClient(config.server_url, client_id=config.client_id)
    print("api ok")

    started = time.time()
    window = AppWindow(config, repo, api)
    print("window ok")
    window.show()
    print("shown")
    print("启动耗时: %.0f ms" % ((time.time() - started) * 1000))
    print("初始页面:", type(window.current_page()).__name__)

    window.login_page().nickname_input.setText("Tester")
    window._on_login("Tester")
    pump(1200)
    print("登录后页面:", type(window.current_page()).__name__)
    print("墙上泡泡数:", window.wall().bubble_count())

    # 等一次真实拉取
    deadline = time.time() + 8
    while time.time() < deadline and window.wall().bubble_count() < 6:
        pump(200)
    print("拉取后泡泡数:", window.wall().bubble_count())
    print("在线状态:", window.wall().top_bar.is_online())

    ids = window.wall().ordered_ids()
    if ids:
        target = ids[0]
        began = time.time()
        window._open_detail(target)
        pump(60)
        elapsed = (time.time() - began) * 1000
        print("打开详情耗时: %.0f ms" % elapsed)
        print("详情已渲染:", window.detail_widget() is not None)
        window.drawer().close_drawer()
        pump(300)

    # 发布
    window.publish_page().editor.setPlainText("冒烟测试：今天心情还不错")
    window.publish_page()._on_emotion("开心")
    window.publish_page()._on_submit()
    pump(1200)
    print("发布后页面:", type(window.current_page()).__name__)
    print("发布后墙上泡泡数:", window.wall().bubble_count())
    print("待同步:", repo.pending_count())

    # 对话
    dialog_ids = [bid for bid in window.wall().ordered_ids() if "test-pub-003" in bid]
    if dialog_ids:
        window._open_chat(dialog_ids[0], "Tester")
        pump(200)
        print("对话抽屉:", window.chat_widget() is not None)
        window.drawer().close_drawer()
        pump(300)

    window.close()
    pump(300)
    saved = open(config_path(), encoding="utf-8").read()
    print("配置含窗口几何:", '"window"' in saved)
    print("配置片段:", saved.replace("\n", " ")[:220])
    repo.close()
    print("冒烟完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
