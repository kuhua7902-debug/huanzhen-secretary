@echo off
chcp 65001 >nul
setlocal
title 幻帧 - 服务（控制台模式）

:: ============================================================
::  带控制台启动服务：日志直接打印在本窗口，便于排错
::  停止服务：在本窗口按 Ctrl+C
:: ============================================================

cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
    echo [错误] 未找到虚拟环境 venv\Scripts\python.exe
    echo        请先运行 setup_deploy.bat 完成部署。
    echo.
    pause
    exit /b 1
)

if not exist ".env" (
    echo [警告] 未找到 .env 文件。
    echo        请先复制 .env.example 为 .env 并填写密钥与管理员密码。
    echo.
)

echo ============================================================
echo  幻帧 AI 助手启动中...
echo  本机访问: http://127.0.0.1:8000/
echo  局域网访问: http://^<本机IP^>:8000/
echo  停止服务: 在本窗口按 Ctrl+C
echo ============================================================
echo.

venv\Scripts\python.exe main.py
set "RC=%errorlevel%"

echo.
if not "%RC%"=="0" (
    echo ============================================================
    echo  服务异常退出（退出码 %RC%）。
    echo.
    echo  建议先做一次环境自检定位问题：双击  诊断.bat
    echo  也可以直接查看日志：logs\agent.log
    echo ============================================================
) else (
    echo 服务已正常退出。
)
echo.
pause
endlocal
exit /b %RC%
