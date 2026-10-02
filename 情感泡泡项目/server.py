"""
中央服务器入口脚本
运行 `python server.py` 启动本地中央服务器。
"""

from src.server_app import app

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
