@echo off
chcp 65001 >nul
setlocal
title 幻帧 - 停止服务
cd /d "%~dp0"

echo.
echo   幻帧 AI 智能秘书 - 停止服务
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\stop_server.ps1"
set "RC=%errorlevel%"

echo.
if "%RC%"=="0" (
    echo 服务已停止，可以安全地关闭本窗口。
) else if "%RC%"=="2" (
    echo 服务当前没有在运行。
) else (
    echo 停止失败。可打开任务管理器结束 python.exe / pythonw.exe，
    echo 或在 PowerShell 中执行：
    echo   Get-NetTCPConnection -LocalPort 8000 ^| ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
)
echo.
pause
endlocal
exit /b 0
