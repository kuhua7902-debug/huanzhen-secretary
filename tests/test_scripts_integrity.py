"""脚本完整性契约测试。

这里钉住两类「部署脚本家族的静默故障」：

1. **批处理必须使用 CRLF 行尾**。LF 会让 cmd.exe 解析多行括号块失败
   （历史事故：git 把所有 .bat 转成 LF，导致「双击启动脚本一闪而过 /
   报『系统找不到指定的路径』」）。
2. **批处理块内不能出现未转义的右括号**。`echo 1) xxx` 写在 `if (...)` 里
   会提前闭合代码块，把后面的文本当成命令执行。

另外校验 doctor（运维自检）的核心纯函数行为，确保它在「应用跑不起来」时也可用。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BAT_FILES = sorted(p for p in ROOT.glob("*.bat"))
PS1_FILES = sorted(ROOT.glob("*.ps1")) + sorted((ROOT / "scripts").glob("*.ps1"))


def _load_doctor():
    """按路径加载 scripts/doctor.py（scripts 不是包，不能直接 import）。"""
    path = ROOT / "scripts" / "doctor.py"
    spec = importlib.util.spec_from_file_location("huanzhen_doctor", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["huanzhen_doctor"] = module
    spec.loader.exec_module(module)
    return module


# ─────────────────────────── 启动脚本家族 ───────────────────────────


def test_launcher_scripts_exist():
    """风险：一键启动/停止/部署/诊断入口被误删，用户不知道该运行什么。"""
    names = {p.name for p in BAT_FILES}
    required = (
        "launch_huanzhen.bat",   # 一键启动（后台 + 预检）
        "run_server.bat",        # 控制台启动（排错）
        "setup_deploy.bat",      # 一键部署
        "诊断.bat",              # 环境自检
        "停止服务.bat",          # 一键停止
        "备份.bat",              # 数据备份
        "安装.bat",              # 一键安装
        "启动幻帧.bat",          # 中文别名
        "运行服务.bat",
    )
    missing = [name for name in required if name not in names]
    assert not missing, f"缺少脚本：{missing}"


def test_backing_scripts_exist():
    """风险：bat 只是入口，真正干活的 ps1/py 被删后双击就会报错。"""
    for rel in (
        "scripts/doctor.py",
        "scripts/stop_server.ps1",
        "scripts/backup.py",
        "scripts/retention.py",
        "scripts/install.ps1",
        "scripts/deploy.ps1",
    ):
        assert (ROOT / rel).is_file(), f"缺少脚本：{rel}"


@pytest.mark.parametrize("path", BAT_FILES, ids=lambda p: p.name)
def test_bat_uses_crlf(path: Path):
    """风险：LF 行尾的批处理在 cmd.exe 下解析异常（多行括号块直接失效）。"""
    data = path.read_bytes()
    lf = data.count(b"\n")
    crlf = data.count(b"\r\n")
    assert lf == crlf, f"{path.name} 含 {lf - crlf} 个裸 LF —— Windows 批处理必须使用 CRLF"


@pytest.mark.parametrize("path", BAT_FILES, ids=lambda p: p.name)
def test_bat_paren_blocks_have_no_bare_closing_paren(path: Path):
    """风险：`if (...)` 块内出现未转义的 `)` 会提前闭合块，导致莫名其妙的报错。

    采用保守启发式：在缩进的 echo 行里查找 `数字)` 这种写法（最容易踩的形式），
    块内出现即视为错误——应改用 `1.` 或转义为 `1^)`。
    """
    import re

    offenders = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        line = raw.strip()
        if not line.lower().startswith("echo"):
            continue
        if line.startswith("::"):            # 注释行
            continue
        # 形如 "echo 1) xxx" —— 行首文本里出现 `1)`/`a)` 且未转义
        if re.search(r"(?<!\^)\b\d\)", line):
            offenders.append(f"{path.name}:{lineno}: {line}")
    assert not offenders, "批处理块内的右括号需要转义或改用『1.』形式：\n" + "\n".join(offenders)


@pytest.mark.parametrize("path", PS1_FILES, ids=lambda p: p.name)
def test_ps1_with_non_ascii_has_utf8_bom(path: Path):
    """风险：Windows PowerShell 5.1 默认按 ANSI(GBK) 读取 .ps1。

    无 BOM 的 UTF-8 中文会被解码成乱码，既显示成乱码，还可能撑破引号导致
    语法错误（实测 stop_server.ps1 曾因此报 "Missing closing '}'"）。
    纯 ASCII 脚本无需 BOM，故这里只对有非 ASCII 内容的脚本做要求。
    """
    data = path.read_bytes()
    if all(b < 128 for b in data):
        return
    assert data.startswith(b"\xef\xbb\xbf"), (
        f"{path.name} 含非 ASCII 字符但缺少 UTF-8 BOM"
        "（PowerShell 5.1 下会乱码/语法报错，请用 UTF-8 with BOM 保存）"
    )


# ─────────────────────────── doctor 纯函数 ───────────────────────────


def test_doctor_importable_without_third_party():
    """风险：自检工具本身依赖重库，导致「环境坏掉时恰恰跑不了自检」。"""
    mod = _load_doctor()
    assert hasattr(mod, "run_all") and hasattr(mod, "Report")


def test_doctor_parses_env_file(tmp_path: Path):
    mod = _load_doctor()
    env = tmp_path / ".env"
    env.write_text(
        "# 注释行应被忽略\n"
        "DEEPSEEK_API_KEY=sk-abc\n"
        'QUOTED="有 空格 的值"\n'
        "EMPTY=\n"
        "NOT_A_PAIR\n",
        encoding="utf-8",
    )
    parsed = mod._parse_env_file(str(env))
    assert parsed["DEEPSEEK_API_KEY"] == "sk-abc"
    assert parsed["QUOTED"] == "有 空格 的值"
    assert parsed["EMPTY"] == ""
    assert "NOT_A_PAIR" not in parsed
    assert "# 注释行应被忽略" not in parsed


def test_doctor_resolves_env_refs():
    """风险：${VAR} 解析错误会让「密钥已配置」被误判为空。"""
    mod = _load_doctor()
    env = {"DEEPSEEK_API_KEY": "sk-live-123"}
    assert mod._resolve_env_refs("${DEEPSEEK_API_KEY}", env) == "sk-live-123"
    assert mod._resolve_env_refs("${MISSING_KEY}", env) == ""
    assert mod._resolve_env_refs("https://x/v1", env) == "https://x/v1"
    assert mod._resolve_env_refs(None, env) is None


def test_doctor_generates_distinct_secrets():
    mod = _load_doctor()
    a, b = mod._gen_secret(), mod._gen_secret()
    assert a != b and len(a) == 48


def test_doctor_report_counting():
    mod = _load_doctor()
    rep = mod.Report()
    rep.add("ok", "a")
    rep.add("error", "b")
    rep.add("error", "c")
    rep.add("warn", "d")
    assert rep.count("error") == 2
    assert rep.count("warn") == 1
    assert rep.count("ok") == 1
