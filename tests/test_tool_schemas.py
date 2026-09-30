"""B. 工具 schema 完整性 —— ``nanobot/adapter_tools.py``。

这里的核心风险是"声明与实际签名不一致"：
``KejiTool.parameters`` 是给 LLM 看的 JSON Schema，``KejiTool.execute`` 会把
模型给的参数直接 ``**kwargs`` 打给真实函数。如果 schema 里声明了函数不接受的
参数名，参数校验会通过、调用却在运行期炸掉 —— 历史上就因此有 6 个工具
稳定报 ``TypeError: unexpected keyword argument``。

本文件的断言对 **TOOL_DEFS 里每一条** 生效，新增工具自动被覆盖。
"""

from __future__ import annotations

import inspect

import pytest


@pytest.fixture(scope="module")
def adapter():
    """惰性导入 adapter_tools（导入会注册全局工具，但不联网、不拉子进程）。"""
    return pytest.importorskip(
        "nanobot.adapter_tools", reason="nanobot 引擎不可用，跳过工具 schema 测试"
    )


@pytest.fixture(scope="module")
def tool_defs(adapter):
    defs = getattr(adapter, "TOOL_DEFS", None)
    assert isinstance(defs, list) and defs, "TOOL_DEFS 缺失或为空"
    return defs


def _accepted_param_names(fn) -> tuple[set[str], bool]:
    """返回 (可被关键字调用的参数名集合, 是否有 **kwargs)。"""
    sig = inspect.signature(fn)
    names = {
        p.name
        for p in sig.parameters.values()
        if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)
    }
    has_var_kw = any(p.kind is p.VAR_KEYWORD for p in sig.parameters.values())
    return names, has_var_kw


# ─────────────────────────── 结构完整性 ───────────────────────────


def test_tool_defs_entries_have_expected_shape(tool_defs):
    """风险：元组结构写错（少一列/描述写成 None）会让注册流程静默跳过或用坏 schema。"""
    for item in tool_defs:
        assert isinstance(item, tuple) and 3 <= len(item) <= 4, f"结构异常: {item!r}"
        name, desc, props = item[0], item[1], item[2]
        assert isinstance(name, str) and name.strip(), f"工具名非法: {name!r}"
        assert isinstance(desc, str) and desc.strip(), f"{name} 缺少描述"
        assert isinstance(props, dict), f"{name} 的 properties 不是 dict"


def test_tool_names_are_unique(tool_defs):
    """风险：重名工具在 ToolRegistry 里后者覆盖前者，模型看到的名字与实际执行的不一致。"""
    names = [d[0] for d in tool_defs]
    assert len(names) == len(set(names)), f"重名工具: {sorted({n for n in names if names.count(n) > 1})}"


def test_every_declared_property_is_an_accepted_parameter(adapter, tool_defs):
    """风险（核心回归）：schema 声明的参数名函数不接受 → 运行期 TypeError。"""
    problems = []
    for name, _desc, props, _required in tool_defs:
        fn = adapter._get_func(name)
        assert fn is not None, f"{name}: 没有可调用的实现函数"
        accepted, has_var_kw = _accepted_param_names(fn)
        if has_var_kw:
            continue
        extra = sorted(set(props) - accepted)
        if extra:
            problems.append(f"{name}: schema 声明但函数不接受 {extra}；签名={inspect.signature(fn)}")
    assert not problems, "schema 与函数签名不一致:\n" + "\n".join(problems)


def test_every_required_name_is_declared_in_properties(tool_defs):
    """风险：required 里出现 properties 没有的名字，模型无从提供 → 调用必然失败。"""
    problems = []
    for name, _desc, props, required in tool_defs:
        if not required:
            continue
        missing = [r for r in required if r not in props]
        if missing:
            problems.append(f"{name}: required 未在 properties 中声明 {missing}")
    assert not problems, "\n".join(problems)


def test_required_list_is_a_list_of_strings_when_present(tool_defs):
    """风险：required 写成字符串会被 JSON Schema 校验器当成字符数组，报错难查。"""
    for name, _desc, _props, required in tool_defs:
        if required is None:
            continue
        assert isinstance(required, list), f"{name}: required 不是 list"
        assert all(isinstance(r, str) and r for r in required), f"{name}: required 含非字符串"


# ─────────────────────────── 注册与权限一致性 ───────────────────────────


def test_every_tool_def_resolves_to_a_callable(adapter, tool_defs):
    """风险：``_get_func`` 解析不到函数时会回退到 CLI 子进程（慢且易失败），
    意味着该工具在引擎里实际上处于半坏状态。"""
    unresolved = [d[0] for d in tool_defs if adapter._get_func(d[0]) is None]
    assert not unresolved, f"以下工具找不到实现函数: {unresolved}"


@pytest.mark.parametrize(
    "tool_name",
    ["create_folder", "browse_files", "search_files", "delete_workflow", "edit_workflow"],
)
def test_specific_tools_are_registered(adapter, tool_name):
    """风险：这几个工具曾从 TOOL_DEFS 里丢失（模型完全看不到它们）。"""
    assert adapter._get_func(tool_name) is not None, f"{tool_name} 找不到实现"
    names = [d[0] for d in adapter.TOOL_DEFS]
    assert tool_name in names, f"{tool_name} 不在 TOOL_DEFS 中"


def test_read_only_flag_matches_permission_model(adapter, tool_defs):
    """风险：引擎的 ``read_only`` 与权限模块的判定分叉。

    ``read_only`` 同时决定只读账号能否调用（RolePermissionHook）与
    引擎能否并发执行。两处不一致 = 权限漏洞或并发踩踏。
    """
    from core.security.permissions import is_write_tool

    problems = []
    for name, desc, props, required in tool_defs:
        tool = adapter.KejiTool(name, desc, props, required)
        expected_read_only = not is_write_tool(name)
        if bool(tool.read_only) != expected_read_only:
            problems.append(f"{name}: read_only={tool.read_only} 与 is_write_tool 判定不符")
    assert not problems, "\n".join(problems)


def test_keji_tool_schema_uses_object_type_and_carries_required(adapter, tool_defs):
    """风险：schema 结构不符合 OpenAI function-calling 规范 → 模型拿不到参数定义。"""
    name, desc, props, required = tool_defs[0]
    tool = adapter.KejiTool(name, desc, props, required)
    schema = tool.parameters
    assert schema["type"] == "object"
    assert schema["properties"] is props
    assert tool.name == name and tool.description == desc
    if required:
        assert schema.get("required") == required
    else:
        assert "required" not in schema


def test_required_wiring_is_enforced_by_validation(adapter, tool_defs):
    """风险：required 声明了却没被校验，模型可以只传一半参数就触发执行。

    引擎里 ``prepare_call`` 先 cast 再 ``validate_params``；这里直接验证
    「空参数集会因为缺少必填项而被拒」这一契约。
    """
    problems = []
    for name, desc, props, required in tool_defs:
        tool = adapter.KejiTool(name, desc, props, required)
        errors = tool.validate_params({})
        if required:
            if not errors:
                problems.append(f"{name}: required={required} 但空参数集未被拒绝")
        elif errors:
            problems.append(f"{name}: 无必填项却报错 {errors}")
    assert not problems, "\n".join(problems)


def test_gui_tools_are_exclusive(adapter, tool_defs):
    """风险：鼠标键盘是整机共享资源，并发执行会互相打断（拖拽到一半被点击）。

    这里钉住"GUI/桌面类工具必须独占"的契约。
    """
    for name in ("click_position", "type_text", "press_key", "drag_mouse",
                 "close_window", "open_application", "run_command"):
        tool = adapter.KejiTool(name, "d", {}, None)
        assert tool.exclusive is True, f"{name} 未标记为独占执行"


def test_read_only_tools_are_not_exclusive(adapter):
    """风险：把只读工具标成独占会让引擎完全失去并发能力（性能回归）。"""
    for name in ("read_file", "browse_files", "get_time"):
        tool = adapter.KejiTool(name, "d", {}, None)
        assert tool.exclusive is False


@pytest.mark.slow
def test_register_keji_tools_populates_engine_registry(adapter, tmp_path):
    """风险（集成）：注册流程本身坏掉（异常被 except 吞掉）会导致工具全部消失。

    ``register_keji_tools`` 内部对每个工具 try/except + warning，所以这里断言
    "注册数量等于 TOOL_DEFS 数量"，否则异常被静默吞掉也看不出来。
    """
    from nanobot.agent.tools.registry import ToolRegistry

    registry = ToolRegistry()
    adapter.register_keji_tools(registry, tmp_path)

    assert len(registry) == len(adapter.TOOL_DEFS), (
        f"注册 {len(registry)} 个，期望 {len(adapter.TOOL_DEFS)} 个"
    )
    for name in ("create_folder", "browse_files", "search_files",
                 "delete_workflow", "edit_workflow"):
        assert registry.has(name), f"{name} 未注册进引擎"


@pytest.mark.slow
def test_registered_tool_definitions_are_valid_schemas(adapter, tmp_path):
    """风险：注册进引擎后 schema 不能被下游（prompt 构建 / 参数校验）解析。"""
    from nanobot.agent.tools.registry import ToolRegistry

    registry = ToolRegistry()
    adapter.register_keji_tools(registry, tmp_path)
    definitions = registry.get_definitions()
    assert len(definitions) == len(adapter.TOOL_DEFS)
    for d in definitions:
        fn = d.get("function") or d
        assert isinstance(fn.get("name"), str) and fn["name"]
        assert isinstance(fn.get("parameters"), dict)
        assert fn["parameters"].get("type") == "object"
