"""桌面控制工具集 —— 命令执行、程序启动、文件下载

阶段一基础桌面控制能力：
1. run_command   — 执行 shell 命令（带安全防护）
2. open_application — 启动应用程序 / 打开文件 / 打开网页
3. download_file — 下载网络文件到本地
"""

import json
import os
import re
import sys
import shutil
import subprocess
import time
from pathlib import Path

import requests

from core.tools import register_tool
from core.logger import setup_logger

logger = setup_logger("huanzhen.desktop")

_IS_WINDOWS = sys.platform == "win32"

# ═══════════════════════════════════════════════════════════════
# 安全防护：危险命令黑名单（正则，不区分大小写匹配）
# ═══════════════════════════════════════════════════════════════
_DANGER_PATTERNS = [
    r"\brm\s+-[rf]{1,2}\b",            # rm -r, rm -rf, rm -fr
    r"\bdel\s+/[fq]\b",                # del /f, del /q
    r"\brmdir\s+/s\b",                 # rmdir /s
    r"\berase\s+/[fq]\b",              # erase /f, erase /q
    r"(?:^|[;&|]\s*)format\b",         # format C:
    r"\b(mkfs|diskpart)\b",            # 磁盘操作
    r"\bdd\s+if=",                     # dd
    r">\s*/dev/sd",                    # 写入磁盘设备
    r"\b(shutdown|reboot|poweroff)\b", # 关机/重启
    r":\(\)\s*\{.*\};\s*:",            # fork bomb
    r"\breg\s+delete\b",              # 删除注册表项
    r"\bcd\b.*\b&&\b.*\b(rm|del|format)\b",  # cd && rm 组合
    r"\btaskkill\s+/f\b",             # 强制结束进程（允许 /im 但拦 /f 全杀）
    r"\bnet\s+(user|stop|start)\b",   # 用户/服务管理
    r"\bsc\s+(stop|delete)\b",        # 服务管理
    r"\bcopy\b.*\bcon\b",             # copy con 写设备
    r"\bdebug\b",                      # debug 模式
]

_MAX_COMMAND_TIMEOUT = 300  # 最大 5 分钟
_MAX_OUTPUT = 8000          # 输出截断


def _check_command_safety(command: str) -> str | None:
    """检查命令安全性，返回 None 表示安全，返回字符串表示错误原因。"""
    if not command or not command.strip():
        return "错误：命令不能为空"
    lower = command.lower().strip()
    for pattern in _DANGER_PATTERNS:
        if re.search(pattern, lower):
            return f"错误：命令被安全防护拦截（匹配危险模式）。如确需执行，请通过 run_code 工具手动操作。"
    return None


def _truncate_output(text: str, max_len: int = _MAX_OUTPUT) -> str:
    if len(text) <= max_len:
        return text
    half = max_len // 2
    return (
        text[:half]
        + f"\n\n... (输出过长，已截断，共 {len(text)} 字符) ...\n\n"
        + text[-half:]
    )


# ═══════════════════════════════════════════════════════════════
# 工具1：run_command — 执行 shell 命令
# ═══════════════════════════════════════════════════════════════

@register_tool(
    name="run_command",
    description=(
        "在本机执行 shell 命令并返回输出。可用于：查看目录(dir/ls)、"
        "启动程序(start)、查看进程(tasklist)、查看系统信息(systeminfo)、"
        "文件操作(copy/move)等。已内置危险命令拦截。"
        "Windows 示例：dir C:\\Users、start notepad、tasklist、ipconfig、"
        "start chrome https://www.google.com"
    ),
    parameters={
        "command": {
            "type": "string",
            "description": "要执行的 shell 命令，如 'dir C:\\\\Users' 或 'start notepad'",
        },
        "timeout": {
            "type": "integer",
            "description": "超时秒数，默认60，最大300",
        },
    },
    category="desktop",
    timeout=300,
)
def run_command(command: str, timeout: int = 60) -> str:
    # 安全检查
    err = _check_command_safety(command)
    if err:
        return err

    effective_timeout = min(int(timeout or 60), _MAX_COMMAND_TIMEOUT)

    try:
        if _IS_WINDOWS:
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=effective_timeout,
                encoding="utf-8",
                errors="replace",
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )
        else:
            proc = subprocess.run(
                ["bash", "-c", command],
                capture_output=True,
                text=True,
                timeout=effective_timeout,
                encoding="utf-8",
                errors="replace",
            )
    except subprocess.TimeoutExpired:
        return f"错误：命令执行超时（{effective_timeout}秒）"
    except FileNotFoundError:
        return f"错误：找不到 shell 解释器"
    except Exception as e:
        return f"命令执行出错：{e}"

    parts = []
    if proc.stdout:
        parts.append(proc.stdout.strip())
    if proc.stderr:
        stderr_text = proc.stderr.strip()
        if stderr_text:
            parts.append(f"[STDERR]\n{stderr_text}")
    parts.append(f"[Exit code: {proc.returncode}]")

    result = "\n".join(parts) if parts else "(无输出)"
    result = _truncate_output(result)

    # 阶段三钩子：录制工作流时自动记录（零侵入，非录制时立即返回）
    try:
        from core.workflow_tools import _notify_workflow
        _notify_workflow("run_command", {"command": command, "timeout": timeout}, result=result)
    except Exception:
        pass

    return result


# ═══════════════════════════════════════════════════════════════
# 工具2：open_application — 启动应用程序 / 打开文件 / 打开网页
# ═══════════════════════════════════════════════════════════════

# 常见软件名称映射（Windows）
_APP_ALIASES = {
    "wps": "wps.exe",
    "wpsoffice": "wps.exe",
    "et": "et.exe",              # WPS 表格
    "wpp": "wpp.exe",            # WPS 演示
    "word": "winword.exe",
    "excel": "excel.exe",
    "powerpoint": "powerpnt.exe",
    "ppt": "powerpnt.exe",
    "notepad": "notepad.exe",
    "记事本": "notepad.exe",
    "calc": "calc.exe",
    "计算器": "calc.exe",
    "explorer": "explorer.exe",
    "资源管理器": "explorer.exe",
    "chrome": "chrome.exe",
    "edge": "msedge.exe",
    "firefox": "firefox.exe",
    "paint": "mspaint.exe",
    "画图": "mspaint.exe",
    "snipping": "snippingtool.exe",
    "cmd": "cmd.exe",
    "命令提示符": "cmd.exe",
    "powershell": "powershell.exe",
    "taskmgr": "taskmgr.exe",
    "任务管理器": "taskmgr.exe",
    "regedit": "regedit.exe",
    "control": "control.exe",
    "mstsc": "mstsc.exe",        # 远程桌面
}


def _resolve_app_path(app_name: str) -> str | None:
    """尝试将软件别名/名称解析为可执行文件路径。"""
    name = app_name.strip().strip('"').strip("'")
    lower = name.lower()

    # 1. 如果是完整路径且存在
    if os.path.isfile(name):
        return name

    # 2. 别名映射
    if lower in _APP_ALIASES:
        exe = _APP_ALIASES[lower]
        # 尝试通过 PATH 查找
        found = shutil.which(exe)
        if found:
            return found
        return exe  # 返回名称，后续由 App Paths 注册表 / os.startfile 解析（不再经过 cmd）

    # 3. 尝试直接用 which 查找
    found = shutil.which(name)
    if found:
        return found

    # 4. 返回原始名称，交给系统处理
    return name


# 浏览器别名/进程名（用于判断 args 里的网址应该交给谁打开）
_BROWSER_NAMES = {
    "chrome", "chrome.exe",
    "msedge", "msedge.exe", "edge", "edge.exe",
    "firefox", "firefox.exe",
    "iexplore", "iexplore.exe", "ie",
    "brave", "brave.exe", "opera", "opera.exe",
    "360se", "360se.exe", "qqbrowser", "qqbrowser.exe",
    "sogouexplorer", "sogouexplorer.exe",
}


def _lookup_windows_app_path(exe_name: str) -> str | None:
    """在 Windows 注册表 App Paths 中查找可执行文件完整路径，找不到返回 None。

    为什么需要：去掉 `cmd /c start` 之后，"chrome.exe" 这类「已安装但不在 PATH」
    的裸名字不再由 shell 帮忙解析。App Paths 正是 Windows 记录这类程序的官方
    注册表位置（ShellExecute / start 内部也用它），这里直接查注册表，
    既保留原有可用性，又完全不经过 cmd.exe。
    """
    if not _IS_WINDOWS:
        return None

    name = os.path.basename((exe_name or "").strip().strip('"').strip("'"))
    if not name:
        return None
    if not name.lower().endswith(".exe"):
        name += ".exe"

    try:
        import winreg
    except ImportError:
        return None

    subkey = "SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths\\" + name
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(root, subkey) as key:
                path, _ = winreg.QueryValueEx(key, None)
            if path and os.path.isfile(path):
                return path
        except OSError:
            continue
    return None


def _is_launchable_exe(path: str) -> bool:
    """判断路径能否被直接 CreateProcess 启动（真实 .exe/.com 文件或 PATH 中的命令）。

    返回 False 说明必须交给系统文件关联（os.startfile）打开，
    例如文档、URI（ms-settings:）、.lnk 快捷方式、.bat 脚本等。
    """
    if not path:
        return False
    if os.path.isfile(path):
        return path.lower().endswith((".exe", ".com"))
    return bool(shutil.which(path))


def _split_app_args(args: str) -> list[str]:
    """把 args 字符串拆成参数列表（只做分词，绝不解释 shell 元字符）。

    规则：空白分隔，双引号内的空白保留（便于传带空格的路径）。
    反斜杠不做转义处理（Windows 路径如 D:\\文档\\报告.docx 原样保留）。

    安全说明：本函数是纯分词器，& | ^ > % 等字符只会成为参数里的普通字符，
    随后以列表形式直接传给 CreateProcess（shell=False），
    不具备"注入第二条命令"的能力——这是旧实现 `cmd /c start` 的漏洞根源。
    """
    tokens: list[str] = []
    current: list[str] = []
    in_quotes = False
    for ch in args or "":
        if ch == '"':
            in_quotes = not in_quotes
            continue
        if ch.isspace() and not in_quotes:
            if current:
                tokens.append("".join(current))
                current = []
            continue
        current.append(ch)
    if current:
        tokens.append("".join(current))
    return tokens


def _is_url(value: str) -> bool:
    """判断字符串是否是可交给默认程序打开的网址。"""
    return (value or "").strip().lower().startswith(
        ("http://", "https://", "file://", "ftp://")
    )


def _is_browser_app(app_name: str, exe_path: str) -> bool:
    """判断目标程序是否为浏览器（用于决定网址交给谁打开）。"""
    names = {
        os.path.basename((app_name or "").strip()).lower(),
        os.path.basename((exe_path or "").strip()).lower(),
    }
    return bool(names & _BROWSER_NAMES)


def _is_console_exe(path: str) -> bool:
    """读取 PE 头判断是否为控制台程序（IMAGE_SUBSYSTEM_WINDOWS_CUI=3）。

    为什么需要：GUI 程序要用 CREATE_NO_WINDOW/DETACHED_PROCESS 静默启动，
    而 cmd.exe / powershell.exe 这类控制台程序必须分配到新控制台窗口，
    否则窗口不会出现，用户会以为"点了没反应"（比旧的 start 行为倒退）。
    """
    try:
        import struct
        with open(path, "rb") as f:
            if f.read(2) != b"MZ":
                return False
            f.seek(0x3C)
            e_lfanew = struct.unpack("<I", f.read(4))[0]
            f.seek(e_lfanew)
            if f.read(4) != b"PE\0\0":
                return False
            # IMAGE_FILE_HEADER(20) 之后是 OptionalHeader，Subsystem 位于其 0x44 处
            f.seek(e_lfanew + 24 + 0x44)
            subsystem = struct.unpack("<H", f.read(2))[0]
        return subsystem == 3
    except Exception:
        return False


def _launch_executable(exe_path: str, argv: list[str]) -> "subprocess.Popen | None":
    """直接启动可执行文件本体，不经过任何 shell，返回 Popen（便于追踪 PID）。

    安全说明：参数以列表 + shell=False 传递，Windows 按 CreateProcess 的标准规则
    拼装命令行，argv 里的 & | ^ > %VAR% 等字符只是普通字符，不存在 cmd.exe
    二次解析造成的命令注入。
    """
    # 双保险：裸名字（不带路径）先解析成绝对路径
    # —— CreateProcess 的 lpApplicationName 只给部分名称时不保证搜索 PATH
    if not os.path.isfile(exe_path):
        resolved = shutil.which(exe_path)
        if resolved:
            exe_path = resolved

    if _is_console_exe(exe_path):
        # 控制台程序（cmd/powershell 等）分配新控制台窗口，保持旧的 start 体验
        creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    else:
        creationflags = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
        )

    return subprocess.Popen(
        [exe_path, *argv],
        shell=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
    )


# ── 启动后「确定性地」找到这次拉起的窗口 ──
# 这是删掉视觉绕路的根因：过去 open_application 返回后不告诉模型窗口句柄，
# 模型只能截图→视觉模型找窗口→再找按钮，每一步 1~5 秒。现在直接把
# hwnd + 标题 交给模型，后续可全部走 uia_* 精确定位，不再需要「看」。

def _list_top_level_windows() -> dict[int, str]:
    """返回所有「可见且带标题」的顶层窗口 {hwnd: title}（用于启动前后对比）。"""
    if not _IS_WINDOWS:
        return {}
    try:
        import win32gui
    except ImportError:
        return {}

    out: dict[int, str] = {}

    def _cb(hwnd, _lparam):
        try:
            if win32gui.IsWindowVisible(hwnd):
                title = (win32gui.GetWindowText(hwnd) or "").strip()
                if title:
                    out[int(hwnd)] = title
        except Exception:
            pass
        return True

    try:
        win32gui.EnumWindows(_cb, None)
    except Exception:
        return {}
    return out


def _window_pid(hwnd: int) -> int:
    try:
        import win32process
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        return int(pid or 0)
    except Exception:
        return 0


def _detect_launch_window(
    pid: int,
    before: dict[int, str],
    title_hint: str,
    timeout: float = 12.0,
) -> tuple[int, str]:
    """识别本次启动对应的主窗口，返回 (hwnd, title)；未识别到返回 (0, "")。

    优先级：
      1) 属于本次启动进程（pid）的窗口 —— 最准；
      2) 标题命中 title_hint（模糊）；
      3) 启动前后新出现的窗口 —— 兜底（launcher 进程转交给子进程时靠它）。
    """
    if not _IS_WINDOWS:
        return 0, ""

    deadline = time.time() + max(0.0, timeout)
    while time.time() < deadline:
        current = _list_top_level_windows()

        if pid:
            for hwnd in current:
                if _window_pid(hwnd) == pid:
                    return hwnd, current[hwnd]

        if title_hint:
            key = title_hint.lower()
            for hwnd, title in current.items():
                if key in title.lower():
                    return hwnd, title

        for hwnd, title in current.items():
            if hwnd not in before:
                return hwnd, title

        time.sleep(0.25)
    return 0, ""


def _wait_for_window(title: str, timeout: float = 12.0, poll_interval: float = 0.3) -> bool:
    """轮询等待窗口出现（标题模糊匹配）。

    参数：
        title: 窗口标题（或一部分）
        timeout: 最大等待秒数
        poll_interval: 轮询间隔秒数

    返回：True 表示窗口已出现，False 表示超时未找到。
    """
    if not title:
        return False

    deadline = time.time() + timeout
    last_err = None
    while time.time() < deadline:
        try:
            from core import gui_tools
            result = gui_tools.find_window(title)
            if isinstance(result, str) and result.startswith("["):
                # 返回的是 JSON 数组，说明找到了窗口
                try:
                    wins = json.loads(result)
                except Exception:
                    wins = []
                if wins:
                    return True
        except Exception as e:
            last_err = e
        time.sleep(poll_interval)

    if last_err:
        logger.debug("_wait_for_window 最后一次错误: %s", last_err)
    return False


@register_tool(
    name="open_application",
    description=(
        "启动电脑上的应用程序，或用默认程序打开文件/网页。"
        "支持常见软件：wps/word/excel/powerpoint/notepad/chrome/edge/"
        "explorer/calc/paint/cmd/taskmgr 等，也可传完整 exe 路径。"
        "⭐会自动等待并识别被拉起窗口，返回其 hwnd（句柄）——"
        "拿到 hwnd 后请立即改用 UI Automation 工具精确定位控件"
        "（uia_dump_tree / uia_click_element / uia_set_text），不要再截图+视觉找按钮。"
        "示例：open_application('notepad')、open_application('chrome', 'https://www.google.com')、"
        "open_application('wps', 'D:\\\\文档\\\\报告.docx')"
    ),
    parameters={
        "app_name": {
            "type": "string",
            "description": "程序名称(如 notepad/wps/chrome)或完整 exe 路径",
        },
        "args": {
            "type": "string",
            "description": "启动参数，如要打开的文件路径或网址（可选）",
        },
        "wait_window": {
            "type": "string",
            "description": (
                "可选：额外强调要等待的窗口标题（模糊匹配），如 '计算器' / '记事本'。"
                "一般留空即可——工具会自动识别本次启动的窗口并返回 hwnd；"
                "只有当自动识别不准时，才需要用它显式指定。"
            ),
        },
    },
    category="desktop",
    timeout=30,
)
def open_application(app_name: str, args: str = "", wait_window: str = "") -> str:
    if not app_name or not app_name.strip():
        return "错误：请提供程序名称"

    app_name = app_name.strip()
    args = (args or "").strip()
    wait_title = (wait_window or "").strip()

    # 特殊处理：如果是网址，直接用默认浏览器打开
    if app_name.lower().startswith(("http://", "https://")):
        try:
            if _IS_WINDOWS:
                os.startfile(app_name)
            else:
                subprocess.Popen(["xdg-open", app_name])
            return f"已用默认浏览器打开网址：{app_name}"
        except Exception as e:
            return f"打开网址出错：{e}"

    # 解析程序路径
    exe_path = _resolve_app_path(app_name)
    if not exe_path:
        return f"错误：找不到程序「{app_name}」"

    # 补充解析：PATH 里找不到时，再查 Windows App Paths 注册表
    # （过去 `start` 命令就是靠它解析 chrome.exe 这类裸名字的）
    if _IS_WINDOWS and not os.path.isfile(exe_path):
        registered = _lookup_windows_app_path(exe_path)
        if registered:
            logger.info("open_application: %s 经 App Paths 解析为 %s", app_name, registered)
            exe_path = registered

    # 启动前快照现有顶层窗口，用于启动后识别「这次拉起的是哪个窗口」
    before_windows = _list_top_level_windows() if _IS_WINDOWS else {}
    launched_pid = 0
    is_gui_launch = False

    try:
        if _IS_WINDOWS:
            # ── 安全修复：彻底移除 `cmd /c start "" <app> <args>` ──
            # 旧实现把模型可控的 args 拼进 cmd.exe 命令行，而 cmd 会二次解析
            # 命令行，args 里的 & | ^ > %VAR% 等元字符能注入并执行第二条命令，
            # 同时绕过了 run_command 的 _DANGER_PATTERNS 黑名单（黑名单只在
            # run_command 内部生效）。
            # 现在两种情形都不经过 shell：
            #   A. 能解析成真实可执行文件 → Popen([exe, *argv], shell=False) 直接启动；
            #   B. 需要系统文件关联解析的（文档 / URI / 快捷方式 / 未解析的裸名字）
            #      → os.startfile(...)，它走 ShellExecute，同样不经过 cmd.exe。
            if _is_launchable_exe(exe_path):
                proc = _launch_executable(exe_path, _split_app_args(args))
                launched_pid = int(getattr(proc, "pid", 0) or 0)
                is_gui_launch = not _is_console_exe(exe_path)
            elif args and _is_url(args) and _is_browser_app(app_name, exe_path):
                # 浏览器没解析到具体路径、但参数是网址 → 交给默认浏览器打开
                # （与旧行为 `start "" <浏览器> <网址>` 的最终效果一致）
                os.startfile(args)
                is_gui_launch = True
                logger.info("open_application: 用默认程序打开网址 %s", args)
            else:
                # 文档 / 文件关联 / URI（如 ms-settings:）/ .lnk / .bat
                # os.startfile 不支持附加参数（它只有一个路径参数），
                # 因此若传了参数只能忽略并记警告，绝不退回 cmd 拼接
                if args:
                    logger.warning(
                        "open_application: 「%s」需由系统关联打开，无法安全透传参数，已忽略：%s",
                        app_name, args,
                    )
                os.startfile(exe_path)
                is_gui_launch = True
                logger.info("open_application: 用文件关联打开 %s", exe_path)
        else:
            cmd_list = [exe_path]
            if args:
                cmd_list.append(args)
            proc = subprocess.Popen(
                cmd_list,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            launched_pid = int(getattr(proc, "pid", 0) or 0)
            is_gui_launch = True

        desc = f"程序「{app_name}」"
        if args:
            desc += f"（参数：{args}）"
        desc += " 已启动"
        logger.info("open_application: %s -> %s", app_name, exe_path)

        # ── 窗口识别：GUI 程序主动等窗口，并把 hwnd / 标题一并返回 ──
        # 这是让桌面自动化「立竿见影」的关键：过去启动后不返回句柄，模型只能
        # 截图 → 视觉模型找窗口 → 再找按钮，每步 1~5 秒。现在直接把 hwnd 交给
        # 模型，后续可全部走 uia_*（毫秒级的控件树定位），不再需要「看」。
        if _IS_WINDOWS and (wait_title or is_gui_launch):
            timeout = 12.0 if (wait_title or launched_pid) else 4.0
            hwnd, title = _detect_launch_window(
                launched_pid, before_windows, wait_title, timeout=timeout
            )
            if hwnd:
                time.sleep(0.3)  # 给窗口一点渲染时间
                desc += f"，窗口「{title}」已就绪（hwnd={hwnd}）"
                desc += (
                    "\n后续对该窗口的操作请优先用 UI Automation 工具（毫秒级、无需截图）："
                    f"uia_dump_tree(hwnd={hwnd}) 查看控件、"
                    f"uia_click_element(..., hwnd={hwnd}) 精确点击、"
                    f"uia_set_text(..., hwnd={hwnd}) 精确输入；"
                    "只有在该窗口找不到对应控件（自绘界面）时，才退回 click_element 视觉兜底。"
                )
            elif wait_title:
                desc += f"（警告：等待窗口「{wait_title}」超时，可能未就绪）"
                logger.warning("open_application: 等待窗口「%s」超时", wait_title)
        elif wait_title:
            # 非 Windows（或未进入上面的分支）时保留原有的标题等待行为
            wait_ok = _wait_for_window(wait_title, timeout=12.0, poll_interval=0.3)
            if wait_ok:
                time.sleep(0.5)
                desc += f"，窗口「{wait_title}」已就绪"
            else:
                desc += f"（警告：等待窗口「{wait_title}」超时，可能未就绪）"
                logger.warning(
                    "open_application: 等待窗口「%s」超时（12s）", wait_title
                )

        # 阶段三钩子：录制工作流时自动记录（零侵入）
        try:
            from core.workflow_tools import _notify_workflow
            _notify_workflow(
                "open_application",
                {
                    "app_name": app_name,
                    "args": args,
                    "wait_window": wait_window,
                },
                result=desc,
            )
        except Exception:
            pass

        return desc

    except FileNotFoundError:
        return f"错误：找不到程序「{app_name}」，请检查名称或路径"
    except PermissionError:
        return f"错误：无权限启动程序「{app_name}」"
    except Exception as e:
        return f"启动程序出错：{e}"


# ═══════════════════════════════════════════════════════════════
# 工具3：download_file — 下载网络文件到本地
# ═══════════════════════════════════════════════════════════════

_MAX_DOWNLOAD_SIZE = 500 * 1024 * 1024  # 500MB


@register_tool(
    name="download_file",
    description=(
        "从网络下载文件到本地指定路径。支持 HTTP/HTTPS，"
        "可指定保存目录和文件名。适合下载 exe 安装包、文档、图片、压缩包等。"
        "示例：download_file('https://example.com/setup.exe', 'D:\\\\Downloads')、"
        "download_file('https://example.com/report.pdf', 'C:\\\\Users\\\\桌面', '季度报告.pdf')"
    ),
    parameters={
        "url": {
            "type": "string",
            "description": "文件下载地址（http/https）",
        },
        "save_dir": {
            "type": "string",
            "description": "保存目录，如 'D:\\\\Downloads' 或 '~/Desktop'。默认下载到桌面",
        },
        "filename": {
            "type": "string",
            "description": "保存的文件名（可选，不传则从URL自动提取）",
        },
    },
    category="desktop",
    timeout=300,
)
def download_file(url: str, save_dir: str = "", filename: str = "") -> str:
    if not url or not url.strip():
        return "错误：请提供下载地址"

    url = url.strip()

    # 校验 URL
    if not url.lower().startswith(("http://", "https://")):
        return "错误：URL 必须以 http:// 或 https:// 开头"

    # 确定保存目录
    if not save_dir or not save_dir.strip():
        save_dir = os.path.join(os.path.expanduser("~"), "Desktop")
    save_dir = os.path.expanduser(save_dir.strip())

    # 创建目录（如果不存在）
    try:
        os.makedirs(save_dir, exist_ok=True)
    except Exception as e:
        return f"错误：无法创建保存目录「{save_dir}」：{e}"

    # 确定文件名
    if not filename or not filename.strip():
        # 从 URL 提取文件名
        from urllib.parse import urlparse, unquote
        parsed = urlparse(url)
        filename = os.path.basename(unquote(parsed.path))
        if not filename:
            filename = "download_file"
        # 如果 URL 没有扩展名，尝试从 Content-Type 推断
        if "." not in filename:
            filename = filename + ".bin"
    filename = filename.strip()

    # 防止路径遍历
    filename = os.path.basename(filename)
    save_path = os.path.join(save_dir, filename)

    # 下载
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) HuanzhenAgent/1.0"
        }
        resp = requests.get(url, headers=headers, stream=True, timeout=30, allow_redirects=True)
        resp.raise_for_status()

        # 检查文件大小
        content_length = resp.headers.get("Content-Length")
        if content_length:
            total = int(content_length)
            if total > _MAX_DOWNLOAD_SIZE:
                return f"错误：文件过大（{total / 1024 / 1024:.1f}MB），超过最大限制 {_MAX_DOWNLOAD_SIZE / 1024 / 1024:.0f}MB"

        # 流式写入
        downloaded = 0
        with open(save_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=64 * 1024):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if downloaded > _MAX_DOWNLOAD_SIZE:
                        f.close()
                        os.remove(save_path)
                        return f"错误：下载超过最大限制 {_MAX_DOWNLOAD_SIZE / 1024 / 1024:.0f}MB，已取消"

        size_str = f"{downloaded / 1024:.1f} KB" if downloaded < 1024 * 1024 else f"{downloaded / 1024 / 1024:.1f} MB"
        logger.info("download_file: %s -> %s (%s)", url, save_path, size_str)
        return f"下载成功！\n文件已保存到：{save_path}\n文件大小：{size_str}"

    except requests.exceptions.HTTPError as e:
        return f"下载失败（HTTP错误）：{e}"
    except requests.exceptions.ConnectionError:
        return f"下载失败：无法连接到 {url}"
    except requests.exceptions.Timeout:
        return f"下载失败：连接超时"
    except requests.exceptions.RequestException as e:
        return f"下载失败：{e}"
    except OSError as e:
        return f"写入文件失败：{e}"
    except Exception as e:
        return f"下载出错：{e}"
