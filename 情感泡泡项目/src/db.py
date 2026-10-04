"""
db.py - 数据库连接的 Flask 集成
优先用 PostgreSQL（配置了 DATABASE_URL 就走 Neon），否则回退本地 SQLite。
"""
from flask import current_app, g


def _make_db(init_schema, db_path=None, db_url=None):
    if db_url:
        from .database_pg import Database
        return Database(db_url=db_url, init_schema=init_schema)
    from .database import Database
    return Database(db_path=db_path, init_schema=init_schema)


def init_database(db_path, db_url=None):
    """应用启动时调用一次：建表。"""
    db = _make_db(init_schema=True, db_path=db_path, db_url=db_url)
    db.close()


def get_db():
    if "db" not in g:
        db_url = current_app.config.get("DATABASE_URL")
        db_path = current_app.config["DB_PATH"]
        g.db = _make_db(init_schema=False, db_path=db_path, db_url=db_url)
    return g.db


def close_db(e=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()
