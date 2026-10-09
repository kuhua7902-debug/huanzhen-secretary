"""启动期配置校验的契约测试。

风险：配置文件写错时服务"能启动但不能用"，报错又晚又晦涩。
这里钉住：常见写错方式都能被**在启动阶段**识别出来，并给出修复建议。
"""

from __future__ import annotations

from pathlib import Path

from core.config_check import Issue, blocking, format_banner, validate_config


def _write(root: Path, text: str) -> Path:
    path = root / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def _levels(issues) -> set[str]:
    return {i.level for i in issues}


def _titles(issues) -> str:
    return " | ".join(i.title for i in issues)


# ─────────────────────────── 文件层 ───────────────────────────


def test_missing_config_reports_error(tmp_path):
    issues = validate_config(str(tmp_path))
    assert blocking(issues), "缺少 config.yaml 应为阻断级问题"
    assert "config.yaml" in _titles(issues)


def test_invalid_yaml_reports_error(tmp_path):
    _write(tmp_path, "models:\n  default: deepseek\n   bad-indent: [\n")
    issues = validate_config(str(tmp_path))
    assert blocking(issues)
    assert "语法" in _titles(issues)


def test_non_mapping_root_reports_error(tmp_path):
    _write(tmp_path, "- just\n- a\n- list\n")
    issues = validate_config(str(tmp_path))
    assert blocking(issues)


# ─────────────────────────── 模型（最常见的坑）───────────────────────────


def _base_config(default: str, api_key: str, base_url: str = "https://api.example.com") -> str:
    return (
        "models:\n"
        f"  default: {default}\n"
        f"  {default}:\n"
        f"    base_url: {base_url}\n"
        f"    api_key: \"{api_key}\"\n"
        "    model: some-model\n"
    )


def test_missing_models_default_reports_error(tmp_path):
    _write(tmp_path, "models:\n  deepseek:\n    api_key: x\n    model: m\n")
    issues = validate_config(str(tmp_path))
    assert any("models.default" in i.title for i in issues if i.level == "error")


def test_default_pointing_to_unknown_model_reports_error(tmp_path):
    _write(tmp_path, "models:\n  default: ghost\n  deepseek:\n    api_key: x\n    model: m\n")
    issues = validate_config(str(tmp_path))
    assert any("ghost" in i.title for i in issues if i.level == "error")


def test_unresolved_remote_api_key_reports_error(tmp_path):
    """风险：默认模型密钥为空时服务照常启动，用户一发消息才报错。"""
    _write(tmp_path, _base_config("deepseek", "${TEST_ONLY_KEY_NOT_SET_XYZ}"))
    issues = validate_config(str(tmp_path))
    errs = [i for i in issues if i.level == "error"]
    assert any("api_key" in i.title for i in errs), _titles(issues)


def test_local_model_needs_no_api_key(tmp_path):
    """本地模型（Ollama）本来就不需要密钥，不应误报为错误。"""
    _write(tmp_path, _base_config("ollama", "", base_url="http://localhost:11434"))
    issues = validate_config(str(tmp_path))
    assert not any("api_key" in i.title for i in issues if i.level == "error"), _titles(issues)


def test_valid_config_has_no_blocking_issues(tmp_path):
    _write(tmp_path, _base_config("deepseek", "sk-real-looking-key"))
    issues = validate_config(str(tmp_path))
    assert not blocking(issues), _titles(issues)


# ─────────────────────────── agent 数值 ───────────────────────────


def test_invalid_max_tool_rounds_reports_error(tmp_path):
    _write(tmp_path, _base_config("deepseek", "sk-x") + "agent:\n  max_tool_rounds: 0\n")
    issues = validate_config(str(tmp_path))
    assert any("max_tool_rounds" in i.title for i in issues if i.level == "error")


def test_invalid_temperature_reports_error(tmp_path):
    _write(tmp_path, _base_config("deepseek", "sk-x") + "agent:\n  temperature: 9.9\n")
    issues = validate_config(str(tmp_path))
    assert any("temperature" in i.title for i in issues if i.level == "error")


# ─────────────────────────── MCP 段 ───────────────────────────


def test_mcp_servers_not_mapping_reports_error(tmp_path):
    _write(tmp_path, _base_config("deepseek", "sk-x") + "mcp_servers:\n  - a\n  - b\n")
    issues = validate_config(str(tmp_path))
    assert any("mcp_servers" in i.title for i in issues if i.level == "error")


def test_mcp_server_without_command_reports_warning(tmp_path):
    _write(
        tmp_path,
        _base_config("deepseek", "sk-x") + "mcp_servers:\n  broken:\n    args: ['-y']\n",
    )
    issues = validate_config(str(tmp_path))
    assert any("command" in i.title for i in issues if i.level == "warn"), _titles(issues)


# ─────────────────────────── 汇聚输出 ───────────────────────────


def test_blocking_filters_only_errors():
    issues = [Issue("info", "a"), Issue("warn", "b"), Issue("error", "c")]
    assert [i.title for i in blocking(issues)] == ["c"]


def test_format_banner_includes_fix_and_hint():
    issues = [Issue("error", "某问题", "细节", "修复命令")]
    text = format_banner(issues)
    assert "某问题" in text and "细节" in text and "修复命令" in text
    assert "诊断.bat" in text, "应引导用户去看完整诊断"


def test_format_banner_empty_when_no_issues():
    assert format_banner([]) == ""
