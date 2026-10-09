@echo off
chcp 65001 >nul
setlocal
title 幻帧 - 环境自检诊断
cd /d "%~dp0"

echo.
echo   幻帧 AI 智能秘书 - 环境自检
echo   （检查 Python/依赖/配置/端口/数据库/MCP 等，并给出修复建议）
echo.

set "PY=%~dp0venv\Scripts\python.exe"
if not exist "%PY%" (
    echo [错误] 未找到虚拟环境：%PY%
    echo        请先双击运行「一键部署.bat」完成安装。
    echo.
    pause
    exit /b 1
)

"%PY%" "%~dp0scripts\doctor.py" %*

set "RC=%errorlevel%"
echo.
if "%RC%"=="0" (
    echo 自检通过。若要启动服务，请双击「启动幻帧.bat」。
) else (
    echo 自检发现阻断问题，请按上方「修复建议」处理后重试。
    echo.
    echo 提示：加 --fix 参数可自动创建缺失的 .env / config.yaml 与运行目录：
    echo       诊断.bat --fix
)
echo.
pause
endlocal
exit /b %RC%
