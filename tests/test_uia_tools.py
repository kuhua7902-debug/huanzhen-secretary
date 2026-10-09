"""UI Automation 工具层（``core/uia_tools.py``）的契约测试。

重点钉住两类回归：
1. 工具确实注册进 legacy 注册表（语音 / CLI 路径可见），且被登记为写类；
2. 参数校验等纯逻辑分支不需要真实操作桌面即可验证（不在 CI/无桌面环境下也应通过）。
"""

from __future__ import annotations

import pytest


UIA_TOOLS = [
    "uia_dump_tree",
    "uia_find_element",
    "uia_click_element",
    "uia_wait_element",
    "uia_set_text",
    "uia_get_text",
]


def test_uia_tools_are_registered_in_legacy_registry():
    """风险：模块没被 core.tools 导入 → 装饰器不执行 → 语音/CLI 路径看不到这些工具。"""
    from core import tools

    registry = tools._tool_registry
    missing = [n for n in UIA_TOOLS if n not in registry]
    assert not missing, f"未注册的 UIA 工具: {missing}"


def test_uia_tools_are_write_tools():
    """风险：UIA 能点击/输入（有副作用），若被判为只读会让只读账号越权操作桌面。"""
    from core.security.permissions import is_write_tool

    for name in UIA_TOOLS:
        assert is_write_tool(name) is True, f"{name} 应被判为写类工具"


def test_missing_name_returns_error_without_touching_desktop():
    """参数校验分支：既没有 name 也没有 automation_id 时直接返回错误，不做任何桌面操作。"""
    from core.uia_tools import uia_click_element, uia_find_element, uia_get_text, uia_wait_element

    for fn in (uia_find_element, uia_click_element, uia_wait_element, uia_get_text):
        result = fn()
        assert isinstance(result, str) and result.startswith("错误"), f"{fn.__name__} 未拦截空查询: {result!r}"


def test_control_type_alias_resolution():
    """控件类型别名解析：中文/英文都要能映射到 UIA ControlType 常量。"""
    auto = pytest.importorskip("uiautomation", reason="未安装 uiautomation")
    from core.uia_tools import _control_type_id

    button = _control_type_id(auto, "按钮")
    assert button is not None and button == _control_type_id(auto, "Button")
    edit = _control_type_id(auto, "Edit")
    assert edit is not None
    # 未识别的类型返回 None（调用方会忽略该过滤条件），不应抛异常
    assert _control_type_id(auto, "不存在的类型xyz") is None


def test_set_text_requires_text():
    """text 缺失（None）时应明确报错，而不是静默输入空串。"""
    from core.uia_tools import uia_set_text

    result = uia_set_text(text=None, name="文件名")  # type: ignore[arg-type]
    assert isinstance(result, str) and result.startswith("错误")
