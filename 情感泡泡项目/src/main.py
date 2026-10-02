"""
情感泡泡 - 主程序
双击启动即可进入梦幻情感世界
"""
import sys
import os
import traceback

# 确保能找到项目模块
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from PyQt5.QtWidgets import QApplication, QMainWindow, QDesktopWidget, QWidget, QMessageBox
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont

LOG_DIR = os.path.join(os.path.expanduser("~"), ".EmotionBubble")
LOG_PATH = os.path.join(LOG_DIR, "app.log")
os.makedirs(LOG_DIR, exist_ok=True)


def _write_log(message):
    with open(LOG_PATH, "a", encoding="utf-8") as fh:
        fh.write(message + "\n")


def _show_error_dialog(parent, title, message):
    try:
        QMessageBox.critical(parent, title, message)
    except Exception:
        print(message)


def _install_exception_hook():
    def _hook(exc_type, exc, tb):
        detail = ''.join(traceback.format_exception(exc_type, exc, tb))
        _write_log(detail)
        print(detail, file=sys.stderr)
        app = QApplication.instance()
        if app is not None:
            top_windows = [w for w in QApplication.topLevelWidgets() if hasattr(w, 'isVisible') and w.isVisible()]
            parent = top_windows[0] if top_windows else None
            _show_error_dialog(parent, "程序发生错误", f"抱歉，程序遇到了异常，已记录到：{LOG_PATH}\n\n{exc}")
    sys.excepthook = _hook

_install_exception_hook()

from src.database import Database
from src.network import CentralServerNetwork

# 全局网络实例
_network = None


class AppWindow(QMainWindow):
    """主窗口 - 直接切换中央部件来管理页面"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("情感泡泡 ??")
        self.setMinimumSize(1000, 700)
        self.resize(1100, 750)

        screen = QDesktopWidget().screenGeometry()
        self.move((screen.width() - self.width()) // 2,
                  (screen.height() - self.height()) // 2)

        self.db = Database()
        self.current_nickname = ""

        # 页面引用
        self.login_page = None
        self.main_page = None
        self.publish_page = None
        self.detail_page = None
        self.chat_page = None

        self._show_login()

    def _switch_to(self, new_widget):
        old = self.centralWidget()
        self.setCentralWidget(new_widget)
        if old and old is not new_widget:
            old.deleteLater()

    # === 登录 ===
    def _show_login(self):
        from src.ui.login_page import LoginPage
        self.login_page = LoginPage()
        self.login_page.login_success.connect(self._on_login)
        self._switch_to(self.login_page)

    def _on_login(self, nickname):
        self.current_nickname = nickname
        global _network
        try:
            _network = CentralServerNetwork(nickname)
        except Exception as e:
            _write_log(f"[网络] 初始化失败: {e}\n{traceback.format_exc()}")
            _network = None
        self.login_page = None
        try:
            self._show_main()
        except Exception as e:
            _write_log(f"[登录后进入主界面失败] {e}\n{traceback.format_exc()}")
            _show_error_dialog(self, "进入主界面失败", f"进入主界面失败：{e}\n\n请查看日志：{LOG_PATH}")
            self._show_login()

    def _on_remote_bubble(self, data):
        try:
            self.db.save_bubble(data["id"], data["author"], data["content"],
                                data["emotion"], data["reply_mode"])
            if self.main_page:
                self.main_page.refresh()
        except:
            pass

    def _on_remote_resolved(self, data):
        try:
            self.db.resolve_bubble(data["bubble_id"])
            if self.main_page:
                self.main_page.refresh()
        except:
            pass

    # === 主页面 ===
    def _show_main(self):
        from src.ui.main_window import MainWindow as BubbleWall
        self.main_page = BubbleWall(self.db, self.current_nickname, _network)
        self.main_page.show_publish.connect(self._show_publish)
        self.main_page.show_bubble_detail.connect(self._show_detail)
        self.main_page.switch_user.connect(self._switch_user)
        self._switch_to(self.main_page)

    # === 发布 ===
    def _show_publish(self):
        from src.ui.publish_page import PublishPage
        self.publish_page = PublishPage(self.current_nickname, self.db, _network)
        self.publish_page.published.connect(self._on_published)
        self.publish_page.cancelled.connect(self._back_main)
        self._switch_to(self.publish_page)

    def _on_published(self, *args):
        self.publish_page = None
        self._back_main()

    # === 详情 ===
    def _show_detail(self, bubble_id):
        from src.ui.bubble_detail import BubbleDetailPage
        self.detail_page = BubbleDetailPage(self.db, self.current_nickname, bubble_id, _network)
        self.detail_page.bubble_resolved.connect(self._on_detail_done)
        self.detail_page.bubble_saved.connect(self._on_detail_done)
        self.detail_page.replied.connect(self._back_main)
        self.detail_page.start_chat.connect(self._show_chat)
        self.detail_page.go_back.connect(self._back_main)
        self._switch_to(self.detail_page)

    def _on_detail_done(self, *args):
        self.detail_page = None
        self._back_main()

    def _show_chat(self, bubble_id, replier):
        from src.ui.chat_window import ChatWindow
        self.chat_page = ChatWindow(self.db, self.current_nickname, bubble_id, _network)
        self.chat_page.chat_ended.connect(self._back_main)
        self.chat_page.message_sent.connect(lambda *a: None)
        self._switch_to(self.chat_page)

    # === 辅助 ===
    def _back_main(self):
        self.publish_page = None
        self.detail_page = None
        self.chat_page = None
        if self.main_page:
            self._switch_to(self.main_page)
            self.main_page.refresh()
        else:
            self._show_main()

    def _switch_user(self):
        self.current_nickname = ""
        global _network
        _network = None
        self.main_page = None
        self._show_login()


def main():
    try:
        app = QApplication(sys.argv)
        app.setFont(QFont("Microsoft YaHei", 10))

        window = AppWindow()
        window.show()
        sys.exit(app.exec_())
    except Exception as e:
        _write_log(f"[main] 启动失败: {e}\n{traceback.format_exc()}")
        _show_error_dialog(None, "启动失败", f"程序启动失败：{e}\n\n请查看日志：{LOG_PATH}")
        raise


if __name__ == "__main__":
    main()
