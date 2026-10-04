@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 正在启动情感泡泡（网页版）...
echo 启动完成后浏览器打开 http://127.0.0.1:5000
python run_web.py
pause
