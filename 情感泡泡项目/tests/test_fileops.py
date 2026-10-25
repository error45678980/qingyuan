"""更名时的 SQLite 库搬移（含 WAL 边车文件）回归测试。

背景：SQLite 处于 WAL 模式时，已提交事务可能还在 `-wal` 里，主库文件不含最新数据。
只 rename 主文件会**静默丢数据**——品牌更名（EmotionBubble → Qingyuan）踩过这个坑，
这里把行为钉死。

"进程被强杀后留下 WAL" 的现场用「连接打开时复制三个文件」来模拟：
复制出来的副本里 WAL 未被 checkpoint，正是崩溃后的样子。
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
import unittest

from src.core.fileops import rename_sqlite_db


class RenameSqliteTest(unittest.TestCase):
    """`rename_sqlite_db` 的数据保全行为。"""

    def setUp(self) -> None:
        self.workspace = tempfile.mkdtemp(prefix="rename_test_")
        self.source = os.path.join(self.workspace, "old.db")
        self.new = os.path.join(self.workspace, "new.db")

    def tearDown(self) -> None:
        for name in os.listdir(self.workspace):
            path = os.path.join(self.workspace, name)
            try:
                os.remove(path)
            except OSError:
                pass
        os.rmdir(self.workspace)

    def _crash_like_wal_db(self) -> None:
        """造出"进程崩溃后"的库：主库 + 未 checkpoint 的 -wal/-shm。"""
        live = os.path.join(self.workspace, "live.db")
        conn = sqlite3.connect(live, isolation_level=None)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA wal_autocheckpoint=0")
            conn.execute("CREATE TABLE t (v TEXT)")
            conn.execute("INSERT INTO t VALUES ('还没落盘的这一行')")
            self.assertTrue(os.path.exists(live + "-wal"), "夹具应产生 -wal")
            # 连接仍打开时复制 → 副本的 WAL 未被合并，等价于被强杀的现场
            for suffix in ("", "-wal", "-shm"):
                if os.path.exists(live + suffix):
                    shutil.copy2(live + suffix, self.source + suffix)
        finally:
            conn.close()
        for suffix in ("", "-wal", "-shm"):
            if os.path.exists(live + suffix):
                os.remove(live + suffix)

    def test_sidecar_files_are_moved(self) -> None:
        """主库改名后 `-wal`/`-shm` 必须一起搬走。"""
        self._crash_like_wal_db()
        self.assertTrue(os.path.exists(self.source + "-wal"))
        self.assertTrue(rename_sqlite_db(self.source, self.new))
        self.assertFalse(os.path.exists(self.source))
        self.assertTrue(os.path.exists(self.new))
        self.assertFalse(os.path.exists(self.source + "-wal"), "旧名的 -wal 不应残留")

    def test_data_in_wal_survives_rename(self) -> None:
        """改名后数据必须完整（不能只剩主库里的旧页）。"""
        self._crash_like_wal_db()
        self.assertTrue(rename_sqlite_db(self.source, self.new))
        check = sqlite3.connect(self.new)
        try:
            rows = [row[0] for row in check.execute("SELECT v FROM t")]
        finally:
            check.close()
        self.assertEqual(rows, ["还没落盘的这一行"])

    def test_target_exists_is_noop(self) -> None:
        """目标已存在时不覆盖，返回 False。"""
        self._crash_like_wal_db()
        open(self.new, "wb").close()
        self.assertFalse(rename_sqlite_db(self.source, self.new))
        self.assertTrue(os.path.exists(self.source), "源库必须原样保留")

    def test_missing_source_returns_false(self) -> None:
        """源库不存在时返回 False。"""
        self.assertFalse(
            rename_sqlite_db(os.path.join(self.workspace, "nope.db"), self.new)
        )

    def test_locked_database_is_not_renamed(self) -> None:
        """库被占用时宁可放弃改名，也不能丢 WAL。"""
        self._crash_like_wal_db()
        holder = sqlite3.connect(self.source, isolation_level=None)
        try:
            holder.execute("BEGIN IMMEDIATE")
            holder.execute("INSERT INTO t VALUES ('占用中')")
            self.assertFalse(rename_sqlite_db(self.source, self.new))
            self.assertTrue(os.path.exists(self.source))
        finally:
            holder.execute("ROLLBACK")
            holder.close()


if __name__ == "__main__":
    unittest.main()
