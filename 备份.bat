@echo off
chcp 65001 >nul
setlocal
title 幻帧 - 数据备份
cd /d "%~dp0"

echo.
echo   幻帧 AI 智能秘书 - 数据备份
echo   打包数据库 / 配置 / 安全文件到 data\backups\，默认保留最近 7 份
echo.

set "PY=%~dp0venv\Scripts\python.exe"
if not exist "%PY%" (
    echo [错误] 未找到虚拟环境：%PY%
    echo        请先双击运行「一键部署.bat」完成安装。
    echo.
    pause
    exit /b 1
)

"%PY%" "%~dp0scripts\backup.py" %*

echo.
echo 备份目录：data\backups\
echo 查看已有备份：备份.bat --list
echo 提示：备份不含 .env（含 API 密钥），请另行单独保管。
echo.
pause
endlocal
exit /b 0
