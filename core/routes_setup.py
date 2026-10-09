"""首次配置向导 API。

## 为什么需要

新用户最容易卡住的一步是**配置**：装好了、能启动了，但没填模型密钥，
发消息一直失败，却不知道问题在哪。命令行 `诊断.bat` 能告诉他，但前提是
"他愿意去开命令行"。

这里提供两个能力：

- `GET  /api/setup/status` —— **公开**（登录前也要能看）：只返回布尔量与缺失项名称，
  **绝不返回密钥值**。前端据此在页面上给出"还差什么"的引导。
- `POST /api/setup/env`    —— **仅管理员**：把模型密钥 / 管理员密码写入 `.env`，
  免去手动找文件、改文件的过程。写入后需**重启服务**生效。

## 安全考量

- 只允许写入白名单内的键（`WRITABLE_KEYS`），避免被用来改任意环境变量
- 值做长度与换行校验，防止把 `.env` 写坏或注入多行
- 写入采用"保留注释与其它行、只替换目标键"的策略，不破坏用户已有配置
- 状态接口只暴露"有没有配"，不暴露"配的是什么"
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from core.security.deps import require_admin
from core.security.users import CurrentUser

router = APIRouter(prefix="/api/setup", tags=["setup"])

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ENV_PATH = _PROJECT_ROOT / ".env"
_CONFIG_PATH = _PROJECT_ROOT / "config.yaml"

# 允许通过网页写入的键（白名单：宁可少，不可滥）
MODEL_KEYS = ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "ZHIPU_API_KEY", "DASHSCOPE_API_KEY")
WRITABLE_KEYS = frozenset(MODEL_KEYS) | {"TAVILY_API_KEY", "HUANZHEN_ADMIN_PASSWORD"}

MAX_VALUE_LEN = 512


# ────────────────────────────────────────────────────────────
# 读取
# ────────────────────────────────────────────────────────────

def _read_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                out[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def _merged_env() -> dict[str, str]:
    merged = dict(os.environ)
    merged.update(_read_env_file(_ENV_PATH))
    return merged


def _load_config() -> dict:
    try:
        import yaml  # noqa: PLC0415

        if _CONFIG_PATH.is_file():
            with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def _model_ready(cfg: dict, env: dict[str, str]) -> tuple[bool, str, str]:
    """默认模型能否拿到密钥。返回 (就绪, 默认模型名, 不可用原因)。"""
    models = cfg.get("models") or {}
    default = str(models.get("default") or "")
    if not default:
        return False, "", "未设置 models.default"
    block = models.get(default)
    if not isinstance(block, dict):
        return False, default, f"models.{default} 配置缺失"
    raw = str(block.get("api_key", ""))
    resolved = re.sub(
        r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}",
        lambda m: env.get(m.group(1), ""),
        raw,
    ).strip()
    if resolved:
        return True, default, ""
    base_url = str(block.get("base_url", ""))
    if "localhost" in base_url or "127.0.0.1" in base_url:
        return True, default, ""          # 本地模型不需要密钥
    return False, default, f"默认模型「{default}」的密钥未配置"


def build_status() -> dict:
    env = _merged_env()
    cfg = _load_config()
    ready, default_model, reason = _model_ready(cfg, env)

    admin_pwd = (env.get("HUANZHEN_ADMIN_PASSWORD") or "").strip()
    template_pwd = "请改成你自己的强密码"

    users = 0
    try:
        import sqlite3  # noqa: PLC0415

        rel = str((cfg.get("database") or {}).get("path") or "data/huanzhen.db")
        db = rel if os.path.isabs(rel) else str(_PROJECT_ROOT / rel)
        if os.path.isfile(db):
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=3)
            try:
                users = int(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0])
            finally:
                conn.close()
    except Exception:
        pass

    return {
        "configured": bool(ready and (users > 0 or (admin_pwd and admin_pwd != template_pwd))),
        "model_ready": ready,
        "default_model": default_model,
        "reason": reason,
        "model_keys": {k: bool((env.get(k) or "").strip()) for k in MODEL_KEYS},
        "admin_password_set": bool(admin_pwd) and admin_pwd != template_pwd,
        "users": users,
        "env_file": str(_ENV_PATH),
        "env_exists": _ENV_PATH.is_file(),
        "config_exists": _CONFIG_PATH.is_file(),
    }


# ────────────────────────────────────────────────────────────
# 写入
# ────────────────────────────────────────────────────────────

class SaveEnvRequest(BaseModel):
    values: dict[str, str] = Field(default_factory=dict)


def _validate(key: str, value: str) -> None:
    if key not in WRITABLE_KEYS:
        raise HTTPException(400, f"不允许通过网页修改该配置项：{key}")
    if "\n" in value or "\r" in value:
        raise HTTPException(400, f"{key} 的值不能包含换行")
    if len(value) > MAX_VALUE_LEN:
        raise HTTPException(400, f"{key} 的值过长（上限 {MAX_VALUE_LEN} 字符）")
    if key == "HUANZHEN_ADMIN_PASSWORD" and value and len(value) < 8:
        raise HTTPException(400, "管理员密码至少 8 位")


def _update_env_file(updates: dict[str, str]) -> None:
    """只替换目标键，保留注释与其它行；不存在则追加。"""
    lines: list[str] = []
    if _ENV_PATH.is_file():
        try:
            lines = _ENV_PATH.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []

    remaining = dict(updates)
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in remaining:
                out.append(f"{key}={remaining.pop(key)}")
                continue
        out.append(line)

    if remaining:
        if out and out[-1].strip():
            out.append("")
        out.append("# ── 由网页「配置向导」写入 ──")
        for key, value in remaining.items():
            out.append(f"{key}={value}")

    try:
        _ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = str(_ENV_PATH) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(out) + "\n")
        os.replace(tmp, _ENV_PATH)
    except OSError as e:
        raise HTTPException(500, f"写入 .env 失败：{e}")


# ────────────────────────────────────────────────────────────
# 端点
# ────────────────────────────────────────────────────────────

@router.get("/status")
def setup_status():
    """公开端点：只返回"是否已配置"，不含任何密钥值。"""
    return build_status()


@router.post("/env")
def save_env(req: SaveEnvRequest, _admin: CurrentUser = Depends(require_admin)):
    """把白名单内的配置写入 .env（需重启服务生效）。"""
    updates: dict[str, str] = {}
    for key, raw in (req.values or {}).items():
        key = str(key).strip().upper()
        value = str(raw or "").strip()
        _validate(key, value)
        if value:
            updates[key] = value          # 空值表示"不改动"，避免误清空已有密钥

    if not updates:
        raise HTTPException(400, "没有需要保存的内容（空值会被忽略，以免误清空已有密钥）")

    _update_env_file(updates)
    status = build_status()
    return {
        "status": "ok",
        "updated": sorted(updates),
        "need_restart": True,
        "message": "已写入 .env，需重启服务后生效（双击 停止服务.bat 再双击 启动幻帧.bat）",
        "setup": status,
    }
