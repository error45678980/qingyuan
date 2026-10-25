"""用真实库启动客户端并截图（不弹窗），验证迁移后的真实数据渲染。

用法：python tools/shoot_real.py
输出：tools/shots/v2/real_*.png
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication

from src.core import config as config_mod
from src.core import migrations, repo as repo_mod
from src.core.api import ApiClient
from src.ui.app_window import AppWindow

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shots", "v2")


def pump(app: QApplication, ms: int = 800) -> None:
    """跑一段时间事件循环。"""
    remaining = ms
    while remaining > 0:
        app.processEvents()
        app.thread().msleep(20)
        remaining -= 20


def shot(app: QApplication, widget, name: str) -> None:
    """截图（不显示到屏幕）。"""
    widget.setAttribute(Qt.WA_DontShowOnScreen, True)
    widget.show()
    pump(app, 700)
    path = os.path.join(OUT, name)
    widget.grab().save(path)
    print(path)


def main() -> int:
    app = QApplication(sys.argv)
    cfg = config_mod.load_or_create()
    migrations.ensure_client_schema(config_mod.db_path(), config_mod.backup_dir())
    repo = repo_mod.Repo(config_mod.db_path())
    api = ApiClient(cfg.server_url, client_id=cfg.client_id, token=cfg.token)

    # 只改内存副本，不写回 config.json
    cfg.nickname = os.environ.get("BUBBLE_SHOT_NICK", "Tester")

    window = AppWindow(cfg, repo, api)
    window.resize(1200, 800)
    shot(app, window, "real_wall.png")

    window.wall().show_mine.emit()
    pump(app, 800)
    shot(app, window, "real_my.png")

    first = repo.list_public()
    if first:
        window._open_detail(first[0].id)  # noqa: SLF001 - 截图脚本直连内部入口
        pump(app, 600)
        shot(app, window, "real_detail.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
