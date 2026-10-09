"""幻帧环境自检与部署诊断（doctor）。

## 为什么需要它

项目要交给别人在自己的机器上部署，最大的门槛不是功能，而是**出问题不知道去哪看**：
Python 版本不对、依赖没装全、`.env` 缺密钥、`config.yaml` 没生成、端口被占用、
Node/Ollama 缺失导致 MCP 与知识库不可用……这些都会表现为"服务起不来"或
"功能不好使"，但错误信息往往藏在日志深处。

本脚本把「能不能跑」拆成一条条可判定、可解释、可修复的检查项，输出分级报告，
并对可自动处理的问题提供 `--fix`（生成 .env / config.yaml、建目录、生成 JWT 密钥）。

## 用法

    venv\\Scripts\\python.exe scripts\\doctor.py          # 只检查
    venv\\Scripts\\python.exe scripts\\doctor.py --fix    # 检查并自动修复可修复项

退出码：0 = 无阻断问题；1 = 存在 ERROR 级问题。

## 设计约束

- **必须在"应用本身跑不起来"时也能运行**：因此不 import 任何 core.* / nanobot.*，
  依赖全部惰性判断，任何异常都被捕获成一条检查结果而非崩溃。
- 只读检查为主；`--fix` 只创建缺失文件/目录，**绝不覆盖已有文件**。
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import platform
import random
import re
import shutil
import socket
import string
import sys
from dataclasses import dataclass, field

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# 离线 wheel 对应的 Python 版本；不一致时联网安装仍可用，故只警告不阻断
EXPECTED_PY = (3, 12)
PORT = int(os.environ.get("HUANZHEN_PORT", "8000"))

REQUIRED_MODULES = [
    ("fastapi", "Web 框架"),
    ("uvicorn", "ASGI 服务器"),
    ("pydantic", "数据校验"),
    ("yaml", "配置文件解析（PyYAML）"),
    ("loguru", "日志"),
    ("requests", "HTTP 客户端"),
]

OPTIONAL_MODULES = [
    ("chromadb", "知识库向量存储", "知识库检索不可用"),
    ("jieba", "中文分词（可选）", "分词退化为默认策略"),
    ("win32api", "Windows 桌面自动化（pywin32）", "桌面/GUI 工具不可用"),
    ("uiautomation", "UI Automation 控件定位", "uia_* 工具不可用"),
    ("pyautogui", "截图与鼠标键盘", "视觉自动化不可用"),
    ("PIL", "图片处理", "图像相关工具不可用"),
    ("docx", "Word 生成（python-docx）", "create_document 不可用"),
    ("openpyxl", "Excel 生成", "create_table 不可用"),
    ("pptx", "PPT 生成（python-pptx）", "create_presentation 不可用"),
]

# .env 中至少要有其一的模型密钥
MODEL_KEY_NAMES = [
    "DEEPSEEK_API_KEY",
    "OPENAI_API_KEY",
    "ZHIPU_API_KEY",
    "DASHSCOPE_API_KEY",
]


# ────────────────────────────────────────────────────────────
# 结果模型
# ────────────────────────────────────────────────────────────

@dataclass
class Result:
    level: str          # ok | warn | error | info
    title: str
    detail: str = ""
    fix: str = ""
    group: str = ""     # 所属检查分组（用于输出分节）


@dataclass
class Report:
    results: list[Result] = field(default_factory=list)

    def add(self, level: str, title: str, detail: str = "", fix: str = "") -> None:
        self.results.append(Result(level, title, detail, fix))

    def count(self, level: str) -> int:
        return sum(1 for r in self.results if r.level == level)


# ────────────────────────────────────────────────────────────
# 小工具
# ────────────────────────────────────────────────────────────

def _enable_ansi() -> bool:
    """Windows 控制台开启 ANSI 颜色；失败则退化为纯文本。"""
    if os.name != "nt":
        return True
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_ulong()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        # ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:
        return False


_ANSI = _enable_ansi()
_COLORS = {"ok": "\033[32m", "warn": "\033[33m", "error": "\033[31m", "info": "\033[36m"}
_MARKS = {"ok": "✓", "warn": "!", "error": "✗", "info": "i"}


def _c(text: str, level: str) -> str:
    if not _ANSI:
        return text
    return f"{_COLORS.get(level, '')}{text}\033[0m"


def _parse_env_file(path: str) -> dict[str, str]:
    """极简 .env 解析（不依赖任何第三方库，保证 doctor 在坏环境里也能跑）。"""
    out: dict[str, str] = {}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key:
                    out[key] = value
    except Exception:
        pass
    return out


def _merged_env() -> dict[str, str]:
    """进程环境变量 + .env（.env 覆盖），与运行时行为保持一致。"""
    merged = dict(os.environ)
    env_path = os.path.join(ROOT, ".env")
    if os.path.isfile(env_path):
        merged.update(_parse_env_file(env_path))
    return merged


def _load_config() -> tuple[dict | None, str]:
    """读取 config.yaml，返回 (配置, 错误说明)。"""
    path = os.path.join(ROOT, "config.yaml")
    if not os.path.isfile(path):
        return None, "missing"
    try:
        import yaml  # noqa: PLC0415
    except ImportError:
        return None, "no_pyyaml"
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        if not isinstance(data, dict):
            return None, "not_mapping"
        return data, ""
    except Exception as e:  # YAML 语法错误
        return None, f"parse_error: {e}"


def _resolve_env_refs(value, env: dict[str, str]):
    """把 ${VAR} 形式的引用替换为环境变量值（与运行时一致）。"""
    if isinstance(value, str):
        def repl(m):
            return env.get(m.group(1), "")
        return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", repl, value)
    return value


def _gen_secret(n: int = 48) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(random.SystemRandom().choice(alphabet) for _ in range(n))


def _port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.6)
        return s.connect_ex((host, port)) == 0


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


def _db_path() -> str:
    cfg, _ = _load_config()
    rel = "data/huanzhen.db"
    if cfg:
        rel = str((cfg.get("database") or {}).get("path") or rel)
    return rel if os.path.isabs(rel) else os.path.join(ROOT, rel)


def _db_user_count() -> int | None:
    """数据库中已有多少用户；无法判断返回 None。

    用途：bootstrap 管理员只在「库中没有任何用户」时执行，
    因此已有账号的部署缺少 HUANZHEN_ADMIN_PASSWORD 并不是错误。
    """
    path = _db_path()
    if not os.path.isfile(path):
        return None
    try:
        import sqlite3
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=3)
        try:
            return int(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0])
        finally:
            conn.close()
    except Exception:
        return None


# ────────────────────────────────────────────────────────────
# 各项检查
# ────────────────────────────────────────────────────────────

def check_python(rep: Report) -> None:
    v = sys.version_info
    ver = f"{v.major}.{v.minor}.{v.micro}"
    if (v.major, v.minor) == EXPECTED_PY:
        rep.add("ok", f"Python 版本 {ver}")
    elif (v.major, v.minor) > EXPECTED_PY:
        rep.add("warn", f"Python 版本 {ver}（高于 {EXPECTED_PY[0]}.{EXPECTED_PY[1]}）",
                "仓库自带的离线依赖包（offline_packages）是为 3.12 构建的。",
                "继续用当前版本：部署时请删除 offline_packages/ 走联网安装；"
                "或安装 Python 3.12 后重新部署。")
    else:
        rep.add("error", f"Python 版本过低：{ver}",
                f"幻帧需要 Python {EXPECTED_PY[0]}.{EXPECTED_PY[1]}+。",
                f"安装 Python {EXPECTED_PY[0]}.{EXPECTED_PY[1]}（64 位）后重新运行一键部署。")

    bits = platform.architecture()[0]
    if bits == "64bit":
        rep.add("ok", "Python 64 位")
    else:
        rep.add("error", f"Python 为 {bits}", "本项目依赖的若干二进制包只有 64 位版本。",
                "安装 64 位 Python 后重新部署。")


def check_venv(rep: Report) -> None:
    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    venv_dir = os.path.join(ROOT, "venv")
    if in_venv:
        rep.add("ok", "正在虚拟环境中运行", sys.prefix)
    elif os.path.isdir(venv_dir):
        # 常见于用 venv 里的 python 之外的解释器运行
        rep.add("warn", "当前解释器不是项目的 venv",
                f"当前：{sys.executable}",
                f"请改用：{os.path.join(venv_dir, 'Scripts', 'python.exe')} scripts\\doctor.py")
    else:
        rep.add("error", "未找到虚拟环境 venv/",
                "项目尚未部署，或 venv 被删除。",
                "双击 setup_deploy.bat（或 一键部署.bat）完成部署。")


def check_modules(rep: Report) -> None:
    missing = [f"{m}（{desc}）" for m, desc in REQUIRED_MODULES if not _module_available(m)]
    if missing:
        rep.add("error", f"缺少 {len(missing)} 个必需依赖", "、".join(missing),
                "运行：venv\\Scripts\\python.exe -m pip install -r requirements.txt")
    else:
        rep.add("ok", f"必需依赖完整（{len(REQUIRED_MODULES)} 个）")

    opt_missing = [(m, impact) for m, _d, impact in OPTIONAL_MODULES if not _module_available(m)]
    if opt_missing:
        rep.add("warn", f"缺少 {len(opt_missing)} 个可选依赖",
                "；".join(f"{m} → {impact}" for m, impact in opt_missing),
                "按需安装，例如：venv\\Scripts\\python.exe -m pip install chromadb python-docx openpyxl python-pptx")
    else:
        rep.add("ok", "可选依赖齐全")


def check_env_file(rep: Report, fix: bool) -> None:
    path = os.path.join(ROOT, ".env")
    example = os.path.join(ROOT, ".env.example")

    if not os.path.isfile(path):
        if fix and os.path.isfile(example):
            try:
                with open(example, "r", encoding="utf-8") as f:
                    content = f.read()
                content = re.sub(
                    r"^HUANZHEN_JWT_SECRET=.*$",
                    f"HUANZHEN_JWT_SECRET={_gen_secret()}",
                    content, flags=re.M,
                )
                with open(path, "w", encoding="utf-8") as f:
                    f.write(content)
                rep.add("warn", "已自动生成 .env", "已从模板创建，并写入随机 JWT 密钥。",
                        "⚠ 还需手动填写：HUANZHEN_ADMIN_PASSWORD 与至少一个模型密钥（如 DEEPSEEK_API_KEY）")
                return
            except Exception as e:
                rep.add("error", "生成 .env 失败", str(e),
                        "手动复制 .env.example 为 .env 并填写内容。")
                return
        rep.add("error", "缺少配置文件 .env",
                "API 密钥与管理员密码都从这里读取。",
                "执行 python scripts\\doctor.py --fix 自动生成，或手动复制 .env.example 为 .env。")
        return

    env = _parse_env_file(path)
    rep.add("ok", "找到 .env", f"{len(env)} 个配置项")

    admin_pwd = (env.get("HUANZHEN_ADMIN_PASSWORD") or "").strip()
    placeholder = "请改成你自己的强密码"
    users = _db_user_count()

    if not admin_pwd or admin_pwd == placeholder:
        # 已经初始化过的部署（库里有用户）不需要它：bootstrap 只在无用户时执行
        if users:
            rep.add("info", f"未设置 HUANZHEN_ADMIN_PASSWORD（已有 {users} 个账号，无需 bootstrap）",
                    "首次启动只在「数据库中没有用户」时才用它建管理员，当前已跳过。",
                    "如需重置密码：venv\\Scripts\\python.exe scripts\\reset_password.py")
        elif not admin_pwd:
            rep.add("error", "未设置 HUANZHEN_ADMIN_PASSWORD",
                    "没有它无法创建管理员账号，网页无法登录。",
                    "在 .env 中设置 HUANZHEN_ADMIN_PASSWORD=你的强密码")
        else:
            rep.add("error", "HUANZHEN_ADMIN_PASSWORD 仍是模板占位符",
                    "直接部署会使用占位符作为管理员密码，存在安全风险。",
                    "改成你自己的强密码（至少 8 位）。")
    elif len(admin_pwd) < 8:
        rep.add("warn", "管理员密码过短", f"当前长度 {len(admin_pwd)}",
                "建议 12 位以上、含大小写与符号。")
    else:
        rep.add("ok", "管理员密码已设置")

    keys = {n: (env.get(n) or "").strip() for n in MODEL_KEY_NAMES}
    if not any(keys.values()):
        rep.add("error", "未配置任何对话模型密钥",
                "至少需要一个：" + "、".join(MODEL_KEY_NAMES),
                "在 .env 填入例如 DEEPSEEK_API_KEY=sk-xxxx（DeepSeek 官方申请）")
    else:
        configured = [n for n, v in keys.items() if v]
        rep.add("ok", f"模型密钥已配置：{', '.join(configured)}")


def check_config_file(rep: Report, fix: bool) -> None:
    path = os.path.join(ROOT, "config.yaml")
    example = os.path.join(ROOT, "config.example.yaml")
    cfg, err = _load_config()

    if err == "missing":
        if fix and os.path.isfile(example):
            try:
                shutil.copyfile(example, path)
                rep.add("warn", "已自动生成 config.yaml", "从 config.example.yaml 复制。",
                        "如需调整模型 / MCP / 安全策略，请编辑 config.yaml。")
                cfg, err = _load_config()
            except Exception as e:
                rep.add("error", "生成 config.yaml 失败", str(e), "手动复制模板。")
                return
        else:
            rep.add("error", "缺少 config.yaml",
                    "模型、MCP、安全策略都从这里读取。",
                    "执行 python scripts\\doctor.py --fix 自动生成，或复制 config.example.yaml。")
            return

    if err == "no_pyyaml":
        rep.add("error", "缺少 PyYAML，无法解析 config.yaml",
                "config.yaml 是 YAML 格式。",
                "venv\\Scripts\\python.exe -m pip install PyYAML")
        return
    if err.startswith("parse_error"):
        rep.add("error", "config.yaml 语法错误", err.replace("parse_error: ", ""),
                "检查缩进与冒号（YAML 对缩进敏感），或重新复制一份模板。")
        return
    if err == "not_mapping":
        rep.add("error", "config.yaml 顶层结构异常", "应当是键值映射。",
                "重新复制 config.example.yaml 为 config.yaml。")
        return

    rep.add("ok", "config.yaml 解析正常")

    env = _merged_env()
    models = (cfg or {}).get("models") or {}
    default = models.get("default")
    if not default:
        rep.add("error", "config.yaml 未设置 models.default",
                "不知道默认用哪个模型。", "在 models 段设置 default: deepseek")
        return
    if default not in models:
        rep.add("error", f"models.default 指向不存在的模型「{default}」",
                f"可选项：{', '.join(k for k in models if k != 'default')}",
                "修正 models.default，或补上对应模型配置。")
        return

    api_key = _resolve_env_refs((models.get(default) or {}).get("api_key", ""), env)
    if api_key:
        rep.add("ok", f"默认模型「{default}」密钥可用")
    else:
        # ollama 是本地模型，无需密钥
        base_url = str((models.get(default) or {}).get("base_url", ""))
        if "localhost" in base_url or "127.0.0.1" in base_url:
            rep.add("info", f"默认模型「{default}」为本地模型", base_url,
                    "请确认本地模型服务（如 Ollama）已启动。")
        else:
            rep.add("error", f"默认模型「{default}」的 api_key 未解析出值",
                    "通常是 .env 里对应的环境变量为空。",
                    "在 .env 中填写该模型的密钥，或把 models.default 改成已配置的模型。")


def check_port(rep: Report) -> None:
    try:
        if _port_in_use(PORT):
            rep.add("info", f"端口 {PORT} 已被占用",
                    "通常说明幻帧服务已经在运行。",
                    f"打开 http://127.0.0.1:{PORT}/ ；若需重启，先结束占用进程："
                    f"Get-NetTCPConnection -LocalPort {PORT} | % {{ Stop-Process -Id $_.OwningProcess -Force }}")
        else:
            rep.add("ok", f"端口 {PORT} 空闲")
    except Exception as e:
        rep.add("warn", "端口检查失败", str(e))


def check_paths(rep: Report, fix: bool) -> None:
    targets = ["data", "logs", "sessions", "data/security"]
    bad, created = [], []
    for rel in targets:
        p = os.path.join(ROOT, rel)
        if not os.path.isdir(p):
            if fix:
                try:
                    os.makedirs(p, exist_ok=True)
                    created.append(rel)
                    continue
                except Exception:
                    pass
            bad.append(rel)
            continue
        # 可写性探测
        probe = os.path.join(p, ".write_test")
        try:
            with open(probe, "w", encoding="utf-8") as f:
                f.write("ok")
            os.remove(probe)
        except Exception:
            bad.append(rel + "（不可写）")

    if created:
        rep.add("info", f"已创建缺失目录：{', '.join(created)}")
    if bad:
        rep.add("error", "目录不可用：" + "、".join(bad),
                "数据、日志、会话需要可写权限。",
                "检查是否被其他程序占用，或把项目移到有写权限的位置（避免 C:\\Program Files）。")
    else:
        rep.add("ok", "运行目录就绪且可写")


def check_jwt(rep: Report) -> None:
    env = _merged_env()
    secret = (env.get("HUANZHEN_JWT_SECRET") or "").strip()
    if secret:
        rep.add("ok", "JWT 密钥已显式配置")
        return
    legacy = os.path.join(ROOT, "data", "security", "jwt_secret")
    if os.path.isfile(legacy):
        rep.add("info", "JWT 密钥将复用已有文件",
                legacy,
                "注意：该文件丢失会导致所有用户掉线，生产环境建议改用 .env 的 HUANZHEN_JWT_SECRET。")
    else:
        rep.add("info", "JWT 密钥未配置",
                "首次启动会自动生成到 data/security/jwt_secret。",
                "生产环境建议在 .env 显式设置 HUANZHEN_JWT_SECRET。")


def check_external(rep: Report) -> None:
    # Node.js / npx —— MCP 工具依赖
    node = shutil.which("node")
    npx = shutil.which("npx")
    if node and npx:
        rep.add("ok", "Node.js 可用（MCP 工具就绪）", node)
    else:
        rep.add("warn", "未检测到 Node.js / npx",
                "多数 MCP 外部工具（filesystem/excel/charts 等）依赖 npx 启动。",
                "安装 Node.js 18+（https://nodejs.org）后重启服务；不装也能用内置工具。")

    # Ollama —— 知识库嵌入
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=1.2) as r:
            if r.status == 200:
                rep.add("ok", "Ollama 可用（知识库嵌入就绪）")
                return
    except Exception:
        pass
    rep.add("warn", "未检测到本地 Ollama",
            "未安装时知识库索引会退化为零向量，检索不到结果。",
            "安装 Ollama 后执行：ollama pull nomic-embed-text；不需要知识库可忽略。")


def check_mcp_commands(rep: Report) -> None:
    cfg, err = _load_config()
    if cfg is None:
        return
    servers = cfg.get("mcp_servers") or {}
    if not isinstance(servers, dict) or not servers:
        rep.add("info", "未配置 MCP 服务器")
        return

    missing = []
    for name, conf in servers.items():
        if not isinstance(conf, dict):
            continue
        cmd = str(conf.get("command", "")).strip()
        if not cmd:
            continue
        if shutil.which(cmd) or os.path.isfile(os.path.join(ROOT, cmd)):
            continue
        missing.append(f"{name}({cmd})")

    if missing:
        rep.add("warn", f"{len(missing)} 个 MCP 服务器的启动命令不可解析",
                "、".join(missing),
                "确认 Node.js 已装且在 PATH；或用不到的服务器可在 config.yaml 的 mcp_servers 中删除。")
    else:
        rep.add("ok", f"MCP 启动命令可解析（{len(servers)} 个）")


def check_database(rep: Report) -> None:
    path = _db_path()
    rel = os.path.relpath(path, ROOT) if path.lower().startswith(ROOT.lower()) else path

    if not os.path.isfile(path):
        rep.add("info", "数据库尚未创建", f"{rel}（首次启动自动创建）")
        return
    try:
        import sqlite3
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=3)
        try:
            users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            convs = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
            rep.add("ok", "数据库可读", f"{rel} — 用户 {users} 个 / 对话 {convs} 个")
        except sqlite3.DatabaseError:
            rep.add("warn", "数据库可打开但表结构不符合预期", rel,
                    "可能是早期版本或损坏；先备份该文件再排查。")
        finally:
            conn.close()
    except Exception as e:
        rep.add("error", "数据库无法打开", f"{rel} — {e}",
                "确认文件未被其他进程独占；损坏时可从备份恢复或改名后让程序重建。")


def check_tool_registry(rep: Report) -> None:
    import contextlib
    import io

    # 导入 core.tools 会触发各模块的 setup_logger（StreamHandler 绑定当时的 sys.stdout），
    # 会刷一屏 JSON 日志污染自检输出。这里把标准流转到缓冲区，导入完即丢弃。
    sink = io.StringIO()
    try:
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            from core.tools import _tool_registry  # noqa: PLC0415
        total = len(_tool_registry)
    except Exception as e:
        rep.add("warn", "无法加载工具注册表", str(e)[:160],
                "应用代码可能不完整；先解决上面的依赖问题再复测。")
        return

    if total >= 60:
        rep.add("ok", f"内置工具已注册：{total} 个")
    else:
        rep.add("warn", f"内置工具数量异常：{total} 个", "预期 60 个以上。",
                "可能有模块导入失败，请查看启动日志 logs/。")


def check_disk(rep: Report) -> None:
    try:
        usage = shutil.disk_usage(ROOT)
        free_gb = usage.free / (1024 ** 3)
        if free_gb < 1:
            rep.add("error", f"磁盘剩余空间不足：{free_gb:.2f} GB",
                    "知识库与日志需要持续写入。", "清理磁盘后重试。")
        elif free_gb < 5:
            rep.add("warn", f"磁盘剩余空间偏少：{free_gb:.1f} GB",
                    "向量库与截图会持续增长。", "建议保留 5 GB 以上。")
        else:
            rep.add("ok", f"磁盘剩余 {free_gb:.1f} GB")
    except Exception:
        pass


# ────────────────────────────────────────────────────────────
# 主流程
# ────────────────────────────────────────────────────────────

CHECKS = [
    ("运行环境", [check_python, check_venv]),
    ("依赖与配置", [check_modules, check_env_file, check_config_file]),
    ("运行条件", [check_paths, check_port, check_jwt, check_disk]),
    ("数据与扩展", [check_database, check_tool_registry, check_external, check_mcp_commands]),
]


def run_all(fix: bool = False) -> Report:
    """执行全部检查。每个 Result 会带上所属分组，供输出分节。"""
    rep = Report()
    needs_fix = {"check_env_file", "check_config_file", "check_paths"}
    for group, fns in CHECKS:
        for fn in fns:
            start = len(rep.results)
            try:
                if fn.__name__ in needs_fix:
                    fn(rep, fix)
                else:
                    fn(rep)
            except Exception as e:  # 单项检查崩溃不应拖垮整体
                rep.add("warn", f"检查项 {fn.__name__} 执行异常", f"{type(e).__name__}: {e}")
            for r in rep.results[start:]:
                r.group = group
    return rep


def main() -> int:
    # 必须在 argparse 之前：否则 --help 的中文会在 GBK 控制台乱码
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="幻帧环境自检与部署诊断")
    parser.add_argument("--fix", action="store_true", help="自动创建 .env / config.yaml 与运行目录")
    args = parser.parse_args()

    print()
    print(_c("═" * 60, "info"))
    print(_c("  幻帧 AI 智能秘书 · 环境自检", "info"))
    print(f"  项目目录：{ROOT}")
    print(f"  解释器：  {sys.executable}")
    print(_c("═" * 60, "info"))

    rep = run_all(fix=args.fix)

    for group, _fns in CHECKS:
        chunk = [r for r in rep.results if r.group == group]
        if not chunk:
            continue
        print()
        print(_c(f"── {group} " + "─" * max(4, 28 - len(group)), "info"))
        for r in chunk:
            mark = _MARKS.get(r.level, "·")
            print(f"  {_c(mark, r.level)} {r.title}")
            if r.detail:
                print(f"      {r.detail}")
            if r.fix and r.level in ("error", "warn"):
                print("      " + (_c("→ " + r.fix, "info") if _ANSI else "→ " + r.fix))

    errors, warns = rep.count("error"), rep.count("warn")
    print()
    print(_c("═" * 60, "info"))
    if errors:
        print(f"  结果：{_c(str(errors) + ' 个阻断问题', 'error')}，{warns} 个警告")
        print("  请先按上面的「→ 修复建议」处理，然后重新运行本检查。")
    elif warns:
        print(f"  结果：{_c('无阻断问题', 'ok')}，{warns} 个警告（不影响启动）")
        print("  可以启动：双击 launch_huanzhen.bat")
    else:
        print(f"  结果：{_c('全部通过 ✓', 'ok')}")
        print("  可以启动：双击 launch_huanzhen.bat")
    print(_c("═" * 60, "info"))
    print()

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
