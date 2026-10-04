# -*- coding: utf-8 -*-
"""验证 PostgreSQL 连接池确实在复用连接。

只做 SELECT 1（init_schema=False，不建表、不写任何数据）。
用法：python tools/probe_pool.py
"""
import importlib.util
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def load_url():
    p = ROOT / "config" / "secrets.local.py"
    if not p.exists():
        return ""
    spec = importlib.util.spec_from_file_location("secrets_local", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return getattr(mod, "DATABASE_URL", "")


def main():
    url = load_url()
    if not url:
        print("未配置 DATABASE_URL（走 SQLite），本项跳过。")
        return

    from src import database_pg

    stamps = []
    conn_ids = []
    for i in range(2):
        t0 = time.perf_counter()
        db = database_pg.Database(url, init_schema=False)
        db.cursor.execute("SELECT 1")
        db.cursor.fetchone()
        conn_ids.append(id(db.conn))
        db.close()
        stamps.append((time.perf_counter() - t0) * 1000)

    pool = database_pg._POOLS[url]
    print(f"池：min={pool.minconn} max={pool.maxconn} "
          f"闲置={len(pool._pool)} 使用中={len(pool._used)}")
    print(f"第 1 次连接+查询：{stamps[0]:.0f} ms")
    print(f"第 2 次连接+查询：{stamps[1]:.0f} ms")
    reused = conn_ids[0] == conn_ids[1]
    print(f"连接是否被复用：{'是' if reused else '否'}")
    if not reused:
        raise SystemExit("连接没有被复用，池没起作用")
    print("\n结论：第二次没有重新握手，池生效。")


if __name__ == "__main__":
    main()
