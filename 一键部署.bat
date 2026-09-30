@echo off
chcp 65001 >nul
:: 中文别名入口 —— 见 setup_deploy.bat
call "%~dp0setup_deploy.bat" %*
