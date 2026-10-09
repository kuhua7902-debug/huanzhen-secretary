@echo off
chcp 65001 >nul
setlocal
title 幻帧 - 一键安装
cd /d "%~dp0"

if not exist "scripts\install.ps1" (
    echo [错误] 未找到 scripts\install.ps1
    echo        请确认本文件位于项目根目录。
    echo.
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install.ps1" %*
set "RC=%errorlevel%"

echo.
if not "%RC%"=="0" (
    echo 安装未完成（退出码 %RC%），请查看上方提示。
) else (
    echo 安装流程已结束。
)
echo.
pause
endlocal
exit /b %RC%
