﻿# 幻帧语音助手 - Windows 开机自启动安装脚本
# 用法:
#   1. 以管理员身份打开 PowerShell
#   2. 执行: Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
#   3. 执行: .\install_voice_service.ps1

$ErrorActionPreference = "Stop"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  幻帧语音助手 - 开机自启动安装" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

$ProjectDir = "D:\about_python\Keji_Nanobot\Keji-agent-main"
$BatPath = "$ProjectDir\启动幻帧语音助手.bat"
$PythonExe = "$ProjectDir\venv\Scripts\python.exe"
$TrayScript = "$ProjectDir\voice_tray.py"

if (-not (Test-Path $BatPath)) {
    Write-Host "X 找不到启动脚本: $BatPath" -ForegroundColor Red
    exit 1
}

if (-not (Test-Path $PythonExe)) {
    Write-Host "X 找不到 Python 虚拟环境: $PythonExe" -ForegroundColor Red
    Write-Host "   请先运行: python -m venv venv" -ForegroundColor Yellow
    exit 1
}

if (-not (Test-Path $TrayScript)) {
    Write-Host "X 找不到托盘程序: $TrayScript" -ForegroundColor Red
    exit 1
}

Write-Host "V 项目路径: $ProjectDir" -ForegroundColor Green
Write-Host "V 启动脚本: $BatPath" -ForegroundColor Green
Write-Host ""

$TaskName = "幻帧语音助手AutoStart"

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "! 任务已存在" -ForegroundColor Yellow
    $choice = Read-Host "是否覆盖? (y/n)"
    if ($choice -ne "y") {
        Write-Host "已取消" -ForegroundColor Gray
        exit 0
    }
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "已删除旧任务" -ForegroundColor Gray
}

$Action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c start `"`" /B `"$BatPath`"" -WorkingDirectory $ProjectDir
$Trigger = New-ScheduledTaskTrigger -AtLogOn
$Settings = New-ScheduledTaskSettingsSet -Hidden -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew

try {
    Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Description "幻帧语音助手 - 开机自启动" -RunLevel Highest -Force | Out-Null

    Write-Host ""
    Write-Host "V 任务创建成功！" -ForegroundColor Green
    Write-Host ""
    Write-Host "任务详情:" -ForegroundColor Cyan
    Write-Host "   - 触发器: 用户登录时自动启动" -ForegroundColor White
    Write-Host "   - 窗口模式: 隐藏（后台运行）" -ForegroundColor White
    Write-Host "   - 唤醒词: Alexa (OpenWakeWord)" -ForegroundColor White
    Write-Host ""
    Write-Host "管理命令:" -ForegroundColor Cyan
    Write-Host "   查看任务: Get-ScheduledTask -TaskName '$TaskName'" -ForegroundColor Gray
    Write-Host "   手动启动: Start-ScheduledTask -TaskName '$TaskName'" -ForegroundColor Gray
    Write-Host "   删除任务: Unregister-ScheduledTask -TaskName '$TaskName'" -ForegroundColor Gray
    Write-Host ""
    Write-Host "下次重启电脑后，幻帧语音助手将自动在后台启动！" -ForegroundColor Yellow

} catch {
    Write-Host "X 创建任务失败: $_" -ForegroundColor Red
    exit 1
}
