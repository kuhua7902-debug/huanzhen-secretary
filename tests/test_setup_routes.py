"""首次配置向导 API 契约测试。

安全要求（本文件重点验证）：
- 状态接口**只暴露"有没有配"，不暴露"配的是什么"**
- 写接口只允许白名单键，拒绝换行 / 超长 / 过短的管理员密码
- 写 .env 时保留注释与其它行，只替换目标键
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from core import routes_setup


@pytest.fixture()
def envfile(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    cfg = tmp_path / "config.yaml"
    monkeypatch.setattr(routes_setup, "_ENV_PATH", env)
    monkeypatch.setattr(routes_setup, "_CONFIG_PATH", cfg)
    monkeypatch.setattr(routes_setup, "_PROJECT_ROOT", tmp_path)
    return env, cfg


CONFIG_WITH_MODEL = """models:
  default: deepseek
  deepseek:
    base_url: https://api.deepseek.com
    api_key: ${DEEPSEEK_API_KEY}
    model: deepseek-v4-flash
"""


# ─────────────────────────── 状态 ───────────────────────────


def test_status_reports_missing_key_without_leaking_values(envfile, monkeypatch):
    env, cfg = envfile
    cfg.write_text(CONFIG_WITH_MODEL, encoding="utf-8")
    env.write_text("DEEPSEEK_API_KEY=sk-super-secret-value\n", encoding="utf-8")

    # 清掉进程环境里可能存在的同名变量，保证断言稳定
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    status = routes_setup.build_status()

    assert status["model_ready"] is True
    assert status["model_keys"]["DEEPSEEK_API_KEY"] is True
    # 关键：返回体里绝不能出现密钥值
    dumped = str(status)
    assert "sk-super-secret-value" not in dumped, "状态接口泄漏了密钥值"


def test_status_marks_not_ready_when_no_key(envfile, monkeypatch):
    env, cfg = envfile
    cfg.write_text(CONFIG_WITH_MODEL, encoding="utf-8")
    env.write_text("", encoding="utf-8")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    status = routes_setup.build_status()
    assert status["model_ready"] is False
    assert "deepseek" in status["reason"]
    assert status["configured"] is False


def test_status_treats_local_model_as_ready(envfile, monkeypatch):
    env, cfg = envfile
    cfg.write_text(
        "models:\n  default: ollama\n  ollama:\n    base_url: http://localhost:11434\n"
        "    api_key: ''\n    model: qwen2.5:7b\n",
        encoding="utf-8",
    )
    status = routes_setup.build_status()
    assert status["model_ready"] is True, "本地模型不需要密钥，不应判为未配置"


# ─────────────────────────── 写入校验 ───────────────────────────


def test_validate_rejects_unlisted_key():
    with pytest.raises(HTTPException) as e:
        routes_setup._validate("PATH", "evil")
    assert e.value.status_code == 400


def test_validate_rejects_newline_injection():
    with pytest.raises(HTTPException):
        routes_setup._validate("DEEPSEEK_API_KEY", "abc\nPATH=evil")


def test_validate_rejects_overlong_value():
    with pytest.raises(HTTPException):
        routes_setup._validate("DEEPSEEK_API_KEY", "x" * (routes_setup.MAX_VALUE_LEN + 1))


def test_validate_rejects_short_admin_password():
    with pytest.raises(HTTPException):
        routes_setup._validate("HUANZHEN_ADMIN_PASSWORD", "123")
    routes_setup._validate("HUANZHEN_ADMIN_PASSWORD", "a-strong-password")


def test_validate_accepts_whitelisted_keys():
    routes_setup._validate("DEEPSEEK_API_KEY", "sk-abcdefghijklmnop")


# ─────────────────────────── .env 写入 ───────────────────────────


def test_update_env_preserves_comments_and_replaces_existing(envfile):
    env, _cfg = envfile
    env.write_text(
        "# 我是注释\n"
        "DEEPSEEK_API_KEY=old-value\n"
        "TAVILY_API_KEY=\n"
        "# 末尾注释\n",
        encoding="utf-8",
    )

    routes_setup._update_env_file({"DEEPSEEK_API_KEY": "new-value", "ZHIPU_API_KEY": "zhipu-key"})

    text = env.read_text(encoding="utf-8")
    assert "# 我是注释" in text and "# 末尾注释" in text, "注释必须保留"
    assert "DEEPSEEK_API_KEY=new-value" in text
    assert "old-value" not in text
    assert "TAVILY_API_KEY=" in text, "其它键不能被破坏"
    assert "ZHIPU_API_KEY=zhipu-key" in text, "不存在的键应被追加"


def test_update_env_creates_file_when_missing(envfile):
    env, _cfg = envfile
    assert not env.exists()
    routes_setup._update_env_file({"DEEPSEEK_API_KEY": "sk-abc"})
    assert env.read_text(encoding="utf-8").strip().endswith("DEEPSEEK_API_KEY=sk-abc")


# ─────────────────────────── 公开可访问性 ───────────────────────────


def test_setup_status_is_public_but_env_write_is_not():
    """状态接口要在登录页可用；写接口必须仅管理员。"""
    from core.security.auth import _DEFAULT_PUBLIC_PREFIXES, _is_public_path

    assert _is_public_path("/api/setup/status", _DEFAULT_PUBLIC_PREFIXES)
    assert not _is_public_path("/api/setup/env", _DEFAULT_PUBLIC_PREFIXES)
