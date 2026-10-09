"""配置加载与 ${ENV_VAR} 密钥解析。"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

_ENV_PATTERN = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")

# ── 环境变量命名兼容 ──
# 大量既有部署的 .env 沿用历史前缀 KEJI_*。这里按「新名字优先、旧名字兜底」读取，
# 使 HUANZHEN_*（当前前缀）与历史 KEJI_* 都能生效，便于平滑迁移。
# 注意：KEJI_* 属于**兼容别名**而非命名遗留，删掉会让旧部署无法登录/鉴权。
ENV_ALIASES: dict[str, tuple[str, ...]] = {
    "ADMIN_PASSWORD": ("HUANZHEN_ADMIN_PASSWORD", "KEJI_ADMIN_PASSWORD"),
    "JWT_SECRET": ("HUANZHEN_JWT_SECRET", "KEJI_JWT_SECRET"),
    "API_KEY": ("HUANZHEN_API_KEY", "KEJI_API_KEY"),
}


def env_first(*names: str) -> str:
    """按顺序返回第一个非空环境变量值（全部为空则返回 ""）。"""
    for name in names:
        value = os.environ.get(name, "")
        if value and value.strip():
            return value.strip()
    return ""


def env_alias(kind: str) -> str:
    """按逻辑名读取环境变量，自动兼容新旧命名。"""
    return env_first(*ENV_ALIASES.get(kind, (kind,)))

# 日志中脱敏的键名（小写匹配）
_SECRET_KEYS = frozenset({
    "api_key", "app_secret", "secret", "password", "token",
    "verification_token", "encrypt_key", "work_secret",
})


# 可选环境变量：未设置时不该刷警告。
# README 的常见问题里就有「启动后一堆『环境变量未设置』警告」这一条 ——
# 大量是可选项（飞书 / OpenAI / 图搜索等），用 WARNING 级别喊出来只会淹没真正的问题。
_OPTIONAL_ENV_VARS = frozenset({
    "OPENAI_API_KEY",
    "TAVILY_API_KEY",
    "PICOVOICE_ACCESS_KEY",
    "FEISHU_APP_ID",
    "FEISHU_APP_SECRET",
    "GROQ_API_KEY",
    "GITHUB_PERSONAL_ACCESS_TOKEN",
    "OPENWEATHER_API_KEY",
    "ANTHROPIC_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "DINGTALK_APP_KEY",
    "DINGTALK_APP_SECRET",
})


def resolve_env_ref(value: str) -> str:
    """将 '${VAR}' 解析为环境变量；非引用格式原样返回。"""
    if not isinstance(value, str):
        return value
    m = _ENV_PATTERN.match(value.strip())
    if not m:
        return value
    var = m.group(1)
    resolved = os.environ.get(var, "")
    if not resolved:
        # 先按别名再试一次（HUANZHEN_* 与历史 KEJI_* 互相兼容）
        for names in ENV_ALIASES.values():
            if var in names:
                resolved = env_first(*names)
                break
    if not resolved:
        if var in _OPTIONAL_ENV_VARS:
            logger.debug("可选环境变量 {} 未设置（配置项引用了 ${{{}}}）", var, var)
        else:
            logger.warning("环境变量 {} 未设置（配置项引用了 ${{{}}}）", var, var)
    return resolved


def resolve_secrets(obj: Any) -> Any:
    """递归解析配置中的 ${ENV} 引用。"""
    if isinstance(obj, dict):
        return {k: resolve_secrets(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [resolve_secrets(v) for v in obj]
    if isinstance(obj, str):
        return resolve_env_ref(obj)
    return obj


def mask_secrets(obj: Any) -> Any:
    """用于审计/日志的参数脱敏。"""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if str(k).lower() in _SECRET_KEYS:
                out[k] = "***"
            else:
                out[k] = mask_secrets(v)
        return out
    if isinstance(obj, list):
        return [mask_secrets(v) for v in obj]
    return obj


def load_dotenv_file(project_root: Path | None = None) -> None:
    """从项目根目录 .env 加载环境变量（不覆盖已存在的变量）。"""
    root = project_root or Path(__file__).resolve().parent.parent.parent
    env_file = root / ".env"
    if not env_file.is_file():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_PROVIDER_ENV_VARS: dict[str, str] = {
    "deepseek": "DEEPSEEK_API_KEY",
    "openai": "OPENAI_API_KEY",
}


def provider_env_var(provider: str) -> str:
    return _PROVIDER_ENV_VARS.get(provider, f"{provider.upper()}_API_KEY")


def is_env_ref(value: str) -> bool:
    return bool(_ENV_PATTERN.match(str(value or "").strip()))


def upsert_dotenv_var(project_root: Path, key: str, value: str) -> None:
    """写入或更新项目根 .env 中的变量（不覆盖其他行）。"""
    env_file = project_root / ".env"
    lines: list[str] = []
    if env_file.is_file():
        lines = env_file.read_text(encoding="utf-8").splitlines()
    found = False
    out: list[str] = []
    prefix = f"{key}="
    for line in lines:
        if line.strip().startswith("#") or "=" not in line:
            out.append(line)
            continue
        k, _, _ = line.partition("=")
        if k.strip() == key:
            out.append(f"{key}={value}")
            found = True
        else:
            out.append(line)
    if not found:
        if out and out[-1].strip():
            out.append("")
        out.append(f"{key}={value}")
    env_file.write_text("\n".join(out) + "\n", encoding="utf-8")
    os.environ[key] = value
    logger.info("已更新 .env 中的 {}", key)


def persist_provider_api_key(config: dict, provider: str, api_key: str, project_root: Path | None = None) -> None:
    """将模型 API Key 写入 .env，并在 config 中改为 ${ENV} 引用。"""
    key = (api_key or "").strip()
    if not key or is_env_ref(key) or key in ("***", "••••"):
        return
    root = project_root or Path(__file__).resolve().parent.parent.parent
    var = provider_env_var(provider)
    upsert_dotenv_var(root, var, key)
    models = config.setdefault("models", {})
    prov = models.setdefault(provider, {})
    prov["api_key"] = f"${{{var}}}"


def mask_api_key_for_settings(raw: str) -> tuple[str, bool]:
    """返回 (展示值, 是否已配置)。不向浏览器回传明文密钥。"""
    if not raw:
        return "", False
    if is_env_ref(raw):
        return "", True
    return "", True


def load_app_config(config_path: Path | None = None) -> dict:
    """加载 config.yaml 并解析环境变量引用。"""
    root = Path(__file__).resolve().parent.parent.parent
    load_dotenv_file(root)
    if config_path is None:
        config_path = root / "config.yaml"
    if not config_path.is_file():
        logger.warning("配置文件不存在: {}", config_path)
        return {}
    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return resolve_secrets(raw)
