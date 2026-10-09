# 停止幻帧服务（供「停止服务.bat」双击调用，也可单独运行）。
#
# 为什么需要：服务以无窗口方式后台运行，用户过去只能去任务管理器里找
# python.exe / pythonw.exe 手动结束，既麻烦又容易杀错进程。这里按「端口占用者」
# 精确定位，只结束真正在监听幻帧端口的进程。
#
# 退出码：0 = 已停止；2 = 本来就没在运行；1 = 停止失败。

param(
    [int]$Port = 0
)

$ErrorActionPreference = 'Continue'

if ($Port -le 0) {
    $envPort = $env:HUANZHEN_PORT
    $Port = if ($envPort -and $envPort -match '^\d+$') { [int]$envPort } else { 8000 }
}

Write-Host "正在检查端口 $Port ..."

$conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if (-not $conns) {
    Write-Host "[提示] 端口 $Port 上没有正在运行的服务。"
    exit 2
}

$pids = $conns | Select-Object -ExpandProperty OwningProcess -Unique
$stopped = 0
$failed = 0

foreach ($targetPid in $pids) {
    try {
        $proc = Get-Process -Id $targetPid -ErrorAction Stop
        $name = $proc.ProcessName
        # 只结束 python 系进程，避免误杀占用同端口的其它程序
        if ($name -notmatch '^(python|pythonw)$') {
            Write-Host "[跳过] PID $targetPid 是 $name（非幻帧服务，不结束）"
            continue
        }
        Stop-Process -Id $targetPid -Force -ErrorAction Stop
        Write-Host "[已停止] PID $targetPid ($name)"
        $stopped++
    } catch {
        Write-Host "[失败] PID ${targetPid}: $($_.Exception.Message)"
        $failed++
    }
}

if ($stopped -gt 0) {
    Start-Sleep -Milliseconds 600
    if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
        Write-Host "[警告] 端口 $Port 仍被占用，可能有残留进程。"
        exit 1
    }
    Write-Host "服务已停止。"
    exit 0
}

if ($failed -gt 0) { exit 1 }
Write-Host "[提示] 没有可停止的幻帧服务进程。"
exit 2
