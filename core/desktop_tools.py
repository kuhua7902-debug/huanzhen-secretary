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

logger = setup_logger("keji.desktop")

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
        return exe  # 返回名称，让 start 命令去查找

    # 3. 尝试直接用 which 查找
    found = shutil.which(name)
    if found:
        return found

    # 4. 返回原始名称，交给系统处理
    return name


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
                "启动后等待出现的窗口标题（模糊匹配），如 '计算器' / '记事本'。"
                "留空则不等待。建议为 GUI 程序传入此参数，确保窗口渲染完成后再返回。"
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

    try:
        if _IS_WINDOWS:
            # 如果 exe_path 不是有效文件路径，当做系统命令用 subprocess 启动
            if not os.path.isfile(exe_path) and not shutil.which(exe_path):
                # 不是文件，也不是 PATH 里的命令 → 用 start 命令直接启动
                logger.info(f"open_application: '{app_name}' 不是文件路径，用 start 命令尝试")
                cmd_list = ["cmd", "/c", "start", "", app_name]
                if args:
                    cmd_list.append(args)
                subprocess.Popen(
                    cmd_list,
                    shell=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                    | getattr(subprocess, "DETACHED_PROCESS", 0),
                )
            elif args:
                # 带参数启动：用 subprocess.Popen
                # 使用 start 命令在后台启动，不阻塞
                cmd_list = ["cmd", "/c", "start", "", exe_path]
                # 参数按空格分割后追加
                if args:
                    cmd_list.append(args)
                subprocess.Popen(
                    cmd_list,
                    shell=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                    | getattr(subprocess, "DETACHED_PROCESS", 0),
                )
            else:
                # 不带参数：用 os.startfile 最简单
                os.startfile(exe_path)
        else:
            cmd_list = [exe_path]
            if args:
                cmd_list.append(args)
            subprocess.Popen(
                cmd_list,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )

        desc = f"程序「{app_name}」"
        if args:
            desc += f"（参数：{args}）"
        desc += " 已启动"
        logger.info("open_application: %s -> %s", app_name, exe_path)

        # 等待窗口就绪（GUI 程序渲染需要时间）
        if wait_title:
            wait_ok = _wait_for_window(wait_title, timeout=12.0, poll_interval=0.3)
            if wait_ok:
                # 窗口已出现，再给 0.5s 完成渲染
                time.sleep(0.5)
                desc += f"，窗口「{wait_title}」已就绪"
            else:
                # 窗口未出现也算启动成功，只是提示一下
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
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) KejiAgent/1.0"
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
