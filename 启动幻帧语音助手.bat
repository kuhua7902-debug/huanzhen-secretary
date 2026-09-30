@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
title 幻帧语音助手 - 聆听中

:: ============================================================
::  幻帧语音助手 - 快速启动
::  模式：OpenWakeWord 唤醒 + Edge TTS 语音回复 + 键盘兜底
::
::  用法：双击本文件即可启动
::  唤醒词："Alexa"（可在下方 --wake-word 修改）
::
::  ⚠️ 密钥不写在本文件里：统一从项目根目录 .env 读取。
::     本文件曾经硬编码过 API Key，请确认已轮换旧密钥。
:: ============================================================

:: ── 切换到脚本所在目录（可移植，不再依赖绝对路径）──
cd /d "%~dp0"

:: ── 从 .env 加载环境变量 ──
set "ENV_FILE=%~dp0.env"
if not exist "%ENV_FILE%" (
    echo [错误] 未找到 .env 文件：%ENV_FILE%
    echo        请先复制 .env.example 为 .env 并填写密钥。
    echo.
    pause
    exit /b 1
)

for /f "usebackq tokens=1,* delims==" %%a in ("%ENV_FILE%") do (
    set "_k=%%a"
    set "_v=%%b"
    :: 跳过注释行与空行
    if not "!_k!"=="" if not "!_k:~0,1!"=="#" (
        set "_k=!_k: =!"
        if not "!_k!"=="" set "!_k!=!_v!"
    )
)

:: ── 检查关键密钥 ──
if "%DASHSCOPE_API_KEY%"=="" (
    echo [警告] .env 中未设置 DASHSCOPE_API_KEY —— 视觉模型将不可用
)
if "%GROQ_API_KEY%"=="" (
    echo [警告] .env 中未设置 GROQ_API_KEY —— 语音识别(STT)将不可用
)
if "%DEEPSEEK_API_KEY%"=="" if "%ZHIPU_API_KEY%"=="" (
    echo [警告] .env 中未设置任何对话模型密钥 —— 语音对话将不可用
)

:: ── 检查虚拟环境 ──
if not exist "venv\Scripts\pythonw.exe" (
    echo [错误] 未找到虚拟环境 venv\Scripts\pythonw.exe
    echo        请先运行 setup_deploy.bat 完成部署。
    echo.
    pause
    exit /b 1
)

:: ── 启动语音助手 ──
:: 单实例保护由 voice_tray.py 内部基于 PID 文件实现（见 _check_single_instance）
:: Backend: openwakeword | Wake word: alexa | 键盘兜底: Ctrl+Alt+V | TTS: edge
:: oww-threshold 0.7 = 高阈值减少误触发
start "" venv\Scripts\pythonw.exe voice_tray.py --backend openwakeword --wake-word alexa --keyboard --tts edge --oww-threshold 0.7

endlocal
exit /b 0
