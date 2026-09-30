@echo off
chcp 65001 >nul
:: 中文别名入口 —— 见 launch_huanzhen.bat
call "%~dp0launch_huanzhen.bat" %*
