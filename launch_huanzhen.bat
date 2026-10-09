@echo off
chcp 65001 >nul
setlocal
title 幻帧 AI 智能秘书 - 一键启动
cd /d "%~dp0"

set "PORT=8000"
set "PY=%~dp0venv\Scripts\python.exe"

echo ============================================================
echo   幻帧 AI 智能秘书 - 一键启动
echo ============================================================
echo.

:: ── 1) 虚拟环境检查 ──
if not exist "%PY%" (
    echo [错误] 未找到虚拟环境：%PY%
    echo        请先双击运行「一键部署.bat」完成安装。
    echo.
    pause
    exit /b 1
)

:: ── 1.5) 首次运行：自动补齐 .env / config.yaml ──
if not exist ".env" (
    echo [提示] 未找到 .env，正在从模板自动生成…
    "%PY%" "%~dp0scripts\doctor.py" --fix >nul 2>nul
    echo.
    echo ============================================================
    echo  首次运行需要填写两项配置，然后才能启动：
    echo    1. HUANZHEN_ADMIN_PASSWORD = 你自己的管理员密码
    echo    2. DEEPSEEK_API_KEY        = 你的模型密钥（其它模型的 key 亦可）
    echo.
    echo  现在将打开记事本，填写完成后保存并关闭，
    echo  再重新双击本脚本即可启动。
    echo  更多诊断：双击 诊断.bat
    echo ============================================================
    echo.
    if exist ".env" start "" notepad ".env"
    pause
    exit /b 0
)
if not exist "config.yaml" (
    echo [提示] 未找到 config.yaml，正在从模板自动生成…
    copy /y "config.example.yaml" "config.yaml" >nul 2>nul
)

:: ── 2) 已在运行：直接打开页面 ──
powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }"
if not errorlevel 1 (
    echo [提示] 服务已在运行，正在打开页面…
    start "" "http://127.0.0.1:%PORT%/"
    exit /b 0
)

:: ── 3) 后台启动 ──
:: 用 Start-Process 创建独立进程：本窗口关闭后服务继续运行
:: （旧版用 `start /B pythonw`，窗口关闭会连带结束服务，是"起不来"的主因之一）
if not exist "logs" mkdir "logs" >nul 2>nul
echo [1/3] 正在后台启动服务…
powershell -NoProfile -Command "Start-Process -FilePath '%PY%' -ArgumentList 'main.py' -WorkingDirectory '%~dp0' -WindowStyle Hidden -RedirectStandardOutput '%~dp0logs\launcher.out.log' -RedirectStandardError '%~dp0logs\launcher.err.log'"

:: ── 4) 轮询等待 HTTP 就绪 ──
:: 首次启动要连接 MCP 服务，通常 10~40 秒；旧版只等 2 秒就开浏览器，必然白屏
echo [2/3] 正在等待服务就绪（首次启动约 10~40 秒，请稍候）…
powershell -NoProfile -Command "for ($i=1; $i -le 120; $i++) { try { $r = Invoke-WebRequest -Uri 'http://127.0.0.1:%PORT%/health' -UseBasicParsing -TimeoutSec 2; if ($r.StatusCode -eq 200) { exit 0 } } catch {}; Start-Sleep -Seconds 1 }; exit 1"
if errorlevel 1 (
    echo.
    echo [错误] 服务在 120 秒内未能就绪，启动失败。
    echo        请查看日志排查：logs\launcher.err.log
    echo.
    pause
    exit /b 1
)

:: ── 5) 打开浏览器 ──
echo [3/3] 服务已就绪  -^>  http://127.0.0.1:%PORT%/
start "" "http://127.0.0.1:%PORT%/"
exit /b 0
