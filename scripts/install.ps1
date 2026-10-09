# 幻帧 AI 智能秘书 — 一键安装
#
# 目标：把「装环境」这件事变成一次双击。自动完成：
#   1. 检查 Python（3.12 / 64 位）与 Git
#   2. 调用 deploy.ps1 创建 venv 并安装依赖（优先离线包）
#   3. 跑一次环境自检（scripts/doctor.py）确认可用
#   4. 生成桌面快捷方式（可选）
#
# 用法：双击项目根目录的「安装.bat」，或手动执行：
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1
#
# 参数：
#   -SkipDeploy   跳过依赖安装（venv 已就绪时）
#   -NoShortcut   不创建桌面快捷方式

param(
    [switch]$SkipDeploy,
    [switch]$NoShortcut
)

$ErrorActionPreference = 'Continue'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Write-Step($n, $text) { Write-Host "`n[$n] $text" -ForegroundColor Cyan }
function Write-Ok($text)   { Write-Host "    ✓ $text" -ForegroundColor Green }
function Write-Warn($text) { Write-Host "    ! $text" -ForegroundColor Yellow }
function Write-Err($text)  { Write-Host "    ✗ $text" -ForegroundColor Red }

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  幻帧 AI 智能秘书 — 一键安装" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  项目目录：$Root"

if (-not (Test-Path (Join-Path $Root 'main.py'))) {
    Write-Err "当前目录不是幻帧项目根目录（未找到 main.py）。"
    Write-Host "      请把本脚本放在项目根目录下运行，或先解压/克隆完整项目。"
    exit 1
}

# ── 1) Python ──
Write-Step 1 '检查 Python'
$pythonCmd = $null
$pythonArgs = @()

if (Get-Command py -ErrorAction SilentlyContinue) {
    $out = & py -3.12 -c "import sys;print('%d.%d.%d' % sys.version_info[:3])" 2>$null
    if ($LASTEXITCODE -eq 0 -and "$out" -match '^3\.12\.') {
        $pythonCmd = 'py'; $pythonArgs = @('-3.12'); Write-Ok "找到 Python $out（py -3.12）"
    }
}
if (-not $pythonCmd -and (Get-Command python -ErrorAction SilentlyContinue)) {
    $out = & python -c "import sys;print('%d.%d.%d' % sys.version_info[:3]);print(64 if sys.maxsize > 2**32 else 32)" 2>$null
    if ($LASTEXITCODE -eq 0) {
        $pythonCmd = 'python'; $pythonArgs = @()
        $ver = "$out".Split("`n")[0].Trim()
        if ($ver -match '^3\.12\.') {
            Write-Ok "找到 Python $ver"
        } else {
            Write-Warn "Python 版本是 $ver（仓库自带离线包按 3.12 构建）"
            Write-Host "        继续安装将走联网下载依赖；或安装 3.12 后重新运行本脚本。"
        }
    }
}

if (-not $pythonCmd) {
    Write-Err "未检测到 Python。"
    Write-Host ""
    Write-Host "      请安装 64 位 Python 3.12：https://www.python.org/downloads/"
    Write-Host "      安装时务必勾选  Add Python to PATH ，装完重开窗口再运行本脚本。"
    Write-Host ""
    Write-Host "      已装 winget 的话也可以直接执行："
    Write-Host "        winget install --id Python.Python.3.12 -e"
    exit 1
}

$bits = & $pythonCmd @pythonArgs -c "import sys;print(64 if sys.maxsize > 2**32 else 32)" 2>$null
if ("$bits".Trim() -ne '64') {
    Write-Err "检测到的是 32 位 Python —— 本项目依赖的多个二进制包只有 64 位版本。"
    Write-Host "      请安装 64 位 Python 3.12 后重试。"
    exit 1
}
Write-Ok 'Python 为 64 位'

# ── 2) Git（可选） ──
Write-Step 2 '检查 Git（可选）'
$git = Get-Command git -ErrorAction SilentlyContinue
if ($git) {
    Write-Ok "已安装 Git（$((git --version))）"
} else {
    Write-Warn '未安装 Git —— 不影响运行，但无法用 git 拉取更新。'
    Write-Host "        安装：https://git-scm.com/ （建议一并安装 Git LFS）"
}

# ── 3) 依赖安装 ──
Write-Step 3 '安装运行环境（venv + 依赖）'
$venvPython = Join-Path $Root 'venv\Scripts\python.exe'
if ($SkipDeploy) {
    Write-Warn '已按参数跳过依赖安装'
} elseif (Test-Path $venvPython) {
    Write-Ok 'venv 已存在，跳过依赖安装（如需重装请先删除 venv 目录）'
} else {
    $deploy = Join-Path $PSScriptRoot 'deploy.ps1'
    if (-not (Test-Path $deploy)) {
        Write-Err "未找到 scripts\deploy.ps1，无法安装依赖。"
        exit 1
    }
    Write-Host '    开始安装，首次约 3~8 分钟（解包 + 安装大包），请不要关闭窗口…'
    & powershell -NoProfile -ExecutionPolicy Bypass -File $deploy
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $venvPython)) {
        Write-Err '依赖安装失败。'
        Write-Host "        可重试，或运行「诊断.bat」查看具体原因。"
        exit 1
    }
    Write-Ok '依赖安装完成'
}

# ── 4) 环境自检 ──
Write-Step 4 '环境自检'
$doctor = Join-Path $PSScriptRoot 'doctor.py'
if ((Test-Path $doctor) -and (Test-Path $venvPython)) {
    & $venvPython $doctor
    if ($LASTEXITCODE -ne 0) {
        Write-Warn '自检发现阻断问题（见上方「→ 修复建议」）。'
        Write-Host "        修复后重新运行本脚本，或直接双击「诊断.bat」。"
    } else {
        Write-Ok '自检通过'
    }
} else {
    Write-Warn '跳过自检（缺少 venv 或 scripts\doctor.py）'
}

# ── 5) 桌面快捷方式 ──
Write-Step 5 '创建快捷方式'
if ($NoShortcut) {
    Write-Warn '已按参数跳过'
} else {
    $launcher = Join-Path $Root '启动幻帧.bat'
    if (-not (Test-Path $launcher)) { $launcher = Join-Path $Root 'launch_huanzhen.bat' }
    try {
        $shell = New-Object -ComObject WScript.Shell
        $desktop = [Environment]::GetFolderPath('Desktop')
        $lnk = $shell.CreateShortcut((Join-Path $desktop '幻帧 AI 智能秘书.lnk'))
        $lnk.TargetPath = $launcher
        $lnk.WorkingDirectory = $Root
        $lnk.Description = '启动幻帧 AI 智能秘书'
        $lnk.Save()
        Write-Ok '已在桌面创建「幻帧 AI 智能秘书」快捷方式'
    } catch {
        Write-Warn "创建快捷方式失败：$($_.Exception.Message)"
    }
}

# ── 完成 ──
Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  安装完成" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  接下来还有一步：填写配置（首次必做）"
Write-Host "    1. 双击  启动幻帧.bat"
Write-Host "       —— 若还没有 .env，它会自动生成并打开记事本，"
Write-Host "          请填写 HUANZHEN_ADMIN_PASSWORD 与 DEEPSEEK_API_KEY"
Write-Host "    2. 保存记事本，再双击一次  启动幻帧.bat"
Write-Host ""
Write-Host "  也可以：先双击  诊断.bat --fix  自动生成配置，再手动补充密钥"
Write-Host ""
Write-Host "  常用入口：启动幻帧.bat / 停止服务.bat / 诊断.bat / 备份.bat"
Write-Host ""
