@echo off
chcp 65001 >nul
title HuanZhen Voice Assistant - Listening

:: ============================================================
::  HuanZhen Voice Assistant - Quick Launch
::  OpenWakeWord mode + TTS voice reply
::
::  Usage: Double-click this .bat file to start
::  Wake word: "Alexa"
::
::  P0 Fix: 单实例保护 + 高阈值唤醒 (0.7) + 冷却期 3s
:: ============================================================

cd /d D:\about_python\Keji_Nanobot\Keji-agent-main

:: ── 检查是否已在运行 ──
tasklist /fi "imagename eq pythonw.exe" /fo csv 2>nul | findstr /i "voice_tray" >nul
if %errorlevel% equ 0 (
    echo [警告] 幻帧语音助手可能已在运行中
    echo        请检查系统托盘图标，避免重复启动
    timeout /t 5
    exit /b
)

:: ── Environment Variables ──
set DEEPSEEK_API_KEY=sk-5b52d8aa490c45cf92db05ebb2919755
set ZHIPU_API_KEY=7b523889e034473ba38db1682860e34d.pXycW3smbjBJ0wmy
set GROQ_API_KEY=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
set DASHSCOPE_API_KEY=sk-ws-H.RXPLEME.WmjJ.MEUCIQCLw8C5F8KxULJG9OpBQgV6tOOvd4DJ9jcofuojWu9-dQIgQPi8_k3GcIq5oEpduasPmenihlKa1S1Hr4pUmowdaNc

:: ── Launch Voice Assistant (direct pythonw, no start wrapper) ──
:: Backend: OpenWakeWord, Wake word: alexa, Keyboard fallback, TTS: edge
:: oww-threshold 0.7 = 高阈值减少误触发
venv\Scripts\pythonw.exe voice_tray.py --backend openwakeword --wake-word alexa --keyboard --tts edge --oww-threshold 0.7
