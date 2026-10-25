"""最小复现脚本（排查用）。"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

HOME = tempfile.mkdtemp(prefix="bubble_min_")
os.environ["EMOTION_BUBBLE_HOME"] = HOME
shutil.copy(
    os.path.join(ROOT, "tests", "fixtures", "legacy_client.db"),
    os.path.join(HOME, "emotion_bubble.db"),
)

from tests.qt_env import app  # noqa: E402

from src.core.api import ApiClient  # noqa: E402
from src.core.config import backup_dir, db_path, load_or_create  # noqa: E402
from src.core.logging_setup import setup_logging  # noqa: E402
from src.core.migrations import ensure_client_schema  # noqa: E402
from src.core.repo import Repo  # noqa: E402
from src.ui.app_window import AppWindow  # noqa: E402

print("imports ok", flush=True)
setup_logging(None)
print("logging ok", flush=True)
config = load_or_create()
print("config ok", flush=True)
ensure_client_schema(db_path(), backup_dir())
print("migrate ok", flush=True)
repo = Repo(db_path())
api = ApiClient(config.server_url, client_id=config.client_id)
print("core ok", flush=True)
started = time.time()
window = AppWindow(config, repo, api)
print("window ok %.0fms" % ((time.time() - started) * 1000), flush=True)
window.show()
print("shown", flush=True)
