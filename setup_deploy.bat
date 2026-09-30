@echo off
chcp 65001 >nul
setlocal
title 幻帧 - 环境部署

:: ============================================================
::  一键部署：创建 venv 并安装依赖
::  本脚本只是 scripts\deploy.ps1 的 Windows 双击入口
:: ============================================================

cd /d "%~dp0"

if not exist "scripts\deploy.ps1" (
    echo [错误] 未找到 scripts\deploy.ps1
    echo        请在项目根目录运行本脚本。
    pause
    exit /b 1
)

echo 正在部署幻帧运行环境，请稍候...
echo （首次运行会创建 venv 并安装依赖，可能需要几分钟）
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\deploy.ps1"
set "RC=%errorlevel%"

echo.
if "%RC%"=="0" (
    echo ============================================================
    echo  部署完成。
    echo.
    echo  下一步：
    echo    1. 编辑项目根目录的 .env，至少填写：
    echo         HUANZHEN_ADMIN_PASSWORD=你的管理员密码
    echo         DEEPSEEK_API_KEY=你的密钥        ^(或其它模型的密钥^)
    echo    2. 双击 launch_huanzhen.bat 启动
    echo ============================================================
) else (
    echo 部署失败（退出码 %RC%），请查看上方错误信息。
)
echo.
pause
endlocal
exit /b %RC%
