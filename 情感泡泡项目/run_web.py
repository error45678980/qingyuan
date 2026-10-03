# -*- coding: utf-8 -*-
"""
生产环境启动脚本（网页版）
运行：python run_web.py
使用 waitress 多线程 WSGI 服务器，端口 5000。
（server.py 仍可用于开发调试：python server.py）
"""
from waitress import serve

from src.server_app import app

if __name__ == "__main__":
    print("情感泡泡 · 网页版已启动：http://127.0.0.1:5000")
    serve(app, host="0.0.0.0", port=5000, threads=8)
