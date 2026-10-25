@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

rem ---- 定位 Python：优先 py 启动器，其次 PATH 中的 python ----
set "PY="
where py >nul 2>nul
if not errorlevel 1 (
    for /f "delims=" %%P in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do set "PY=%%P"
)
if not defined PY (
    where python >nul 2>nul
    if not errorlevel 1 (
        for /f "delims=" %%P in ('python -c "import sys;print(sys.executable)" 2^>nul') do set "PY=%%P"
    )
)
if not defined PY (
    echo [错误] 没有找到 Python，请先安装 Python 3.10 及以上版本。
    pause
    exit /b 1
)

echo 使用解释器: "%PY%"
echo 启动 情缘 ...
"%PY%" "src\main.py"
set "EXITCODE=%ERRORLEVEL%"
if not "%EXITCODE%"=="0" (
    echo [提示] 程序退出码 %EXITCODE%
    echo [提示] 详细日志: %USERPROFILE%\.Qingyuan\app.log
    pause
)
endlocal
