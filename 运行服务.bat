@echo off
chcp 65001 >nul
:: 中文别名入口 —— 见 run_server.bat
call "%~dp0run_server.bat" %*
