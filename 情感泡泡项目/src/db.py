"""
db.py - 数据库连接的 Flask 集成
按 Flask 官方教程模式：每个请求一个独立连接，存在 g 里，请求结束自动关闭。
修复原来全局共用连接导致并发串数据的问题。
"""
from flask import current_app, g

from .database import Database


def init_database(db_path):
    """应用启动时调用一次：建表 + 迁移。"""
    db = Database(db_path)
    db.close()


def get_db():
    if "db" not in g:
        g.db = Database(db_path=current_app.config["DB_PATH"], init_schema=False)
    return g.db


def close_db(e=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()
