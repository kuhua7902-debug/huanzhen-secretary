"""启动期的配置校验（让"配置写错"在启动时就暴露）。

## 为什么需要

`config.yaml` 写错（默认模型不存在、密钥没配、数值越界、MCP 段结构不对）时，
服务**仍然会正常启动**，问题要等到用户真正发消息、调用某个工具时才以莫名其妙的
错误爆出来——此时既没有线索，也不知道该改哪里。

本模块在启动阶段把这些问题一次性找出来，输出**可读、可定位、可修复**的清单。

## 设计取舍

- **只报告，不阻断**：默认即使有 `error` 也让服务起来。本地工具的首要目标是
  "能进去改"，而不是"配置不对就直接不启动"（那会让用户连改配置的入口都没有）。
  需要严格模式（有错即退出）可设环境变量 `HUANZHEN_STRICT_CONFIG=1`。
- **只依赖标准库 + PyYAML**，任何一步失败都降级成一条 Issue，绝不抛出。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

MAX_ISSUES_SHOWN = 12


@dataclass
class Issue:
    level: str          # error | warn | info
    title: str
    detail: str = ""
    fix: str = ""


def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read_env(root: str) -> dict[str, str]:
    merged = dict(os.environ)
    path = os.path.join(root, ".env")
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for raw in f:
                    line = raw.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    key = key.strip()
                    if key:
                        merged[key] = value.strip().strip('"').strip("'")
        except OSError:
            pass
    return merged


def _resolve(value, env: dict[str, str]):
    if isinstance(value, str):
        return re.sub(
            r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}",
            lambda m: env.get(m.group(1), ""),
            value,
        )
    return value


def validate_config(root: str | None = None) -> list[Issue]:
    """校验配置，返回问题清单（可能为空）。永不抛出。"""
    root = root or _project_root()
    issues: list[Issue] = []

    # ── 1) 文件存在性与可解析性 ──
    cfg_path = os.path.join(root, "config.yaml")
    if not os.path.isfile(cfg_path):
        issues.append(Issue(
            "error", "缺少 config.yaml",
            "模型、安全、MCP 等全部配置都从这里读取。",
            "执行 `venv\\Scripts\\python.exe scripts\\doctor.py --fix` 自动生成，"
            "或复制 config.example.yaml。",
        ))
        return issues

    try:
        import yaml  # noqa: PLC0415
    except ImportError:
        issues.append(Issue("error", "缺少 PyYAML，无法解析 config.yaml", "",
                            "venv\\Scripts\\python.exe -m pip install PyYAML"))
        return issues

    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    except Exception as e:
        issues.append(Issue("error", "config.yaml 语法错误", str(e)[:200],
                            "YAML 对缩进敏感，请检查缩进与冒号，或重新复制一份模板。"))
        return issues

    if not isinstance(cfg, dict):
        issues.append(Issue("error", "config.yaml 顶层结构异常", "应为键值映射。",
                            "重新复制 config.example.yaml 为 config.yaml。"))
        return issues

    env = _read_env(root)

    # ── 2) 模型（最容易导致"能启动但不能聊天"）──
    models = cfg.get("models") or {}
    if not isinstance(models, dict) or not models:
        issues.append(Issue("error", "config.yaml 缺少 models 段", "没有任何模型可用。",
                            "参考 config.example.yaml 补上 models 配置。"))
    else:
        default = models.get("default")
        available = [k for k in models if k != "default" and isinstance(models[k], dict)]
        if not default:
            issues.append(Issue("error", "未设置 models.default", "不知道默认用哪个模型。",
                                f"设为以下之一：{', '.join(available) or '（无可用模型）'}"))
        elif default not in models:
            issues.append(Issue(
                "error", f"models.default 指向不存在的模型「{default}」",
                f"当前可用：{', '.join(available) or '（无）'}",
                "修正 models.default，或补上该模型的配置段。",
            ))
        else:
            block = models.get(default) or {}
            key = str(_resolve(block.get("api_key", ""), env)).strip()
            base_url = str(block.get("base_url", ""))
            is_local = "localhost" in base_url or "127.0.0.1" in base_url
            if not key and not is_local:
                issues.append(Issue(
                    "error", f"默认模型「{default}」没有可用的 api_key",
                    f"models.{default}.api_key = {block.get('api_key', '')!r} 解析后为空。",
                    "在 .env 填写对应密钥（如 DEEPSEEK_API_KEY），"
                    "或把 models.default 改成已配置的模型。",
                ))
            if not block.get("model"):
                issues.append(Issue("warn", f"模型「{default}」未指定 model 名称", "",
                                    "补上 model 字段（如 deepseek-v4-flash）。"))

    # ── 3) agent 段数值（越界会在运行中炸）──
    agent = cfg.get("agent") or {}
    if isinstance(agent, dict):
        rounds = agent.get("max_tool_rounds")
        if rounds is not None:
            try:
                if int(rounds) <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                issues.append(Issue("error", f"agent.max_tool_rounds 非法：{rounds!r}",
                                    "应为正整数。", "改成如 80。"))
        temp = agent.get("temperature")
        if temp is not None:
            try:
                t = float(temp)
                if not (0.0 <= t <= 2.0):
                    raise ValueError
            except (TypeError, ValueError):
                issues.append(Issue("error", f"agent.temperature 非法：{temp!r}",
                                    "应在 0~2 之间。", "改成如 0.7。"))

    # ── 4) 安全段 ──
    sec = cfg.get("security") or {}
    if isinstance(sec, dict):
        if sec.get("enabled", True) and sec.get("allow_localhost_without_auth"):
            issues.append(Issue(
                "warn", "已开启「本机免登录」（security.allow_localhost_without_auth）",
                "本机任何进程都可无凭据访问，局域网共享场景不建议。",
                "确认仅内网调试时开启；否则改为 false。",
            ))
        admin = sec.get("bootstrap_admin") or {}
        pwd = str(_resolve(admin.get("password", ""), env)).strip()
        template_pwd = "请改成你自己的强密码"
        if not pwd or pwd == template_pwd:
            issues.append(Issue(
                "warn", "bootstrap_admin.password 未设置或仍是模板占位符",
                "只在「数据库中没有用户」时使用；若已初始化过可忽略。",
                "在 .env 设置 HUANZHEN_ADMIN_PASSWORD。",
            ))

    # ── 5) 数据库路径 ──
    db_rel = str((cfg.get("database") or {}).get("path") or "").strip()
    if db_rel:
        db_abs = db_rel if os.path.isabs(db_rel) else os.path.join(root, db_rel)
        if not os.path.isfile(db_abs):
            issues.append(Issue(
                "info", f"数据库文件尚不存在：{db_rel}",
                "首次启动会自动创建；若你期望看到历史对话，请确认路径没写错。",
                "用 `诊断.bat` 查看当前实际使用的库。",
            ))

    # ── 6) MCP 服务器结构 ──
    servers = cfg.get("mcp_servers")
    if servers is not None:
        if not isinstance(servers, dict):
            issues.append(Issue("error", "mcp_servers 结构错误", "应为「名称: 配置」映射。",
                                "参考 config.example.yaml 修正。"))
        else:
            bad = [name for name, conf in servers.items()
                   if not isinstance(conf, dict) or not str(conf.get("command", "")).strip()]
            if bad:
                issues.append(Issue(
                    "warn", f"{len(bad)} 个 MCP 服务器缺少 command",
                    "、".join(bad),
                    "补上 command（如 npx / node / python），或删掉用不到的服务器。",
                ))

    return issues


def blocking(issues: list[Issue]) -> list[Issue]:
    return [i for i in issues if i.level == "error"]


def format_banner(issues: list[Issue]) -> str:
    """把问题清单格式化成一段便于在日志里一眼看到的多行文本。"""
    if not issues:
        return ""
    icons = {"error": "✗", "warn": "!", "info": "i"}
    lines = ["配置检查发现问题（服务仍会启动，但相关功能可能不可用）："]
    for issue in issues[:MAX_ISSUES_SHOWN]:
        lines.append(f"  {icons.get(issue.level, '·')} [{issue.level}] {issue.title}")
        if issue.detail:
            lines.append(f"      {issue.detail}")
        if issue.fix:
            lines.append(f"      → {issue.fix}")
    if len(issues) > MAX_ISSUES_SHOWN:
        lines.append(f"  … 其余 {len(issues) - MAX_ISSUES_SHOWN} 条略")
    lines.append("  → 完整诊断：双击 诊断.bat")
    return "\n".join(lines)


def strict_mode() -> bool:
    """严格模式：有 error 时拒绝启动（默认关闭，避免用户被锁在门外）。"""
    return str(os.environ.get("HUANZHEN_STRICT_CONFIG", "")).strip() in ("1", "true", "yes")
