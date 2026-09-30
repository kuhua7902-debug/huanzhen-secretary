"""A. 权限模型 —— ``core.security.permissions``。

这是整个多用户体系的安全底线：只读账号一旦漏掉一个写类工具，
就等于把"执行任意命令 / 操作鼠标键盘"的能力交给了访客账号。

历史背景（见模块 docstring）：早期用黑名单 + ``return not is_write_tool(name)``，
漏掉了 ``run_command`` / 整套 GUI 操作 / ``rename_files`` / ``deduplicate_files``。
现在是 fail-closed 白名单，本文件就是防止它被改回 fail-open 的回归网。
"""

from __future__ import annotations

import pytest

from core.security.permissions import (
    READONLY_EXTRA_ALLOWED,
    READ_ONLY_TOOLS,
    WRITE_TOOL_NAMES,
    WRITE_TOOL_PREFIXES,
    is_write_tool,
    resolve_current_user,
    role_permission_hint,
    tool_allowed_for_user,
)
from core.security.users import CurrentUser

RO = CurrentUser(id="u-ro", username="ro", role="readonly", display_name="只读")
MEMBER = CurrentUser(id="u-mem", username="mem", role="member", display_name="成员")
ADMIN = CurrentUser(id="u-adm", username="adm", role="admin", display_name="管理员")

# 只读账号**必须**被拒绝的工具（安全回归清单，来自真实越权事故面）
READONLY_FORBIDDEN = [
    # 任意命令 / 代码执行
    "run_command",
    "run_code",
    # 桌面与键鼠控制
    "open_application",
    "click_position",
    "type_text",
    "press_key",
    "drag_mouse",
    "close_window",
    "download_file",
    # 文件写入 / 整理 / 压缩
    "write_file",
    "delete_file",
    "rename_files",
    "deduplicate_files",
    "etl_pipeline",
    "create_archive",
    "extract_archive",
    "extract_email_attachments",
    # 数据库写
    "db_execute_query",
    # 工作流录制/回放（等价于批量执行任意操作）
    "record_workflow",
    "replay_workflow",
]

# 只读账号**必须**仍然可用的工具（收紧权限时不能误伤正常查询能力）
READONLY_ALLOWED = [
    "read_file",
    "browse_files",
    "search_files",
    "read_document",
    "query_knowledge",
    "analyze_data",
    "db_connect",
    "db_list_tables",
    "web_search",
    "get_time",
    "calculator",
    "__tool__",
    "ocr_image",
]


# ────────────────────────── is_write_tool：fail-closed ──────────────────────────


def test_is_write_tool_is_fail_closed_for_unknown_tool():
    """风险：新增工具忘了登记权限时，必须是"只读账号少一个工具"而不是"越权可用"。"""
    assert is_write_tool("totally_new_tool_2027") is True
    assert is_write_tool("mcp_some_future_server_do_danger") is True


def test_is_write_tool_treats_empty_name_as_write():
    """风险：空/None 工具名（调用方拼错参数）不能落到"放行"分支。"""
    assert is_write_tool("") is True
    assert is_write_tool(None) is True  # type: ignore[arg-type]


@pytest.mark.parametrize("name", sorted(READ_ONLY_TOOLS))
def test_is_write_tool_false_for_every_whitelisted_tool(name):
    """风险：白名单里任何一个名字被判成写类，只读账号就会莫名其妙少功能。"""
    assert is_write_tool(name) is False


@pytest.mark.parametrize("name", sorted(WRITE_TOOL_NAMES))
def test_is_write_tool_true_for_every_known_write_tool(name):
    """风险：已知写类工具被误判为只读 = 直接越权。"""
    assert is_write_tool(name) is True


@pytest.mark.parametrize(
    "name",
    [
        "mcp_quack_export_csv",
        "mcp_quack_load_table",
        "mcp_engineer-your-data_create_table",
        "mcp_engineer-your-data_clean_data",
        "mcp_engineer-your-data_export_zip",
        "mcp_engineer-your-data_write_file",
    ],
)
def test_is_write_tool_by_prefix_for_mcp_side_effect_tools(name):
    """风险：MCP 动态工具无法逐个枚举，只能按前缀识别，前缀失效即越权。"""
    assert is_write_tool(name) is True


def test_readonly_whitelist_and_write_list_do_not_overlap():
    """风险：同一个名字同时出现在两个清单里，判定语义会随代码顺序漂移。

    ``is_write_tool`` 里白名单优先级更高，所以重叠会让一个写类工具被静默放行。
    """
    overlap = READ_ONLY_TOOLS & WRITE_TOOL_NAMES
    assert overlap == frozenset(), f"白名单与写类清单重叠: {sorted(overlap)}"


def test_readonly_extra_allowed_is_backward_compat_alias():
    """风险：旧命名被删除会打断仍在引用它的模块（会造成权限绕过或 ImportError）。"""
    assert READONLY_EXTRA_ALLOWED is READ_ONLY_TOOLS


def test_write_tool_prefixes_are_non_empty_strings():
    """风险：前缀列表里混入空串会让 ``startswith("")`` 把所有工具判成写类。"""
    assert WRITE_TOOL_PREFIXES
    for p in WRITE_TOOL_PREFIXES:
        assert isinstance(p, str) and p.strip() == p and p


# ───────────────────── tool_allowed_for_user：按角色放行 ─────────────────────


@pytest.mark.parametrize("tool", READONLY_FORBIDDEN)
def test_readonly_cannot_use_write_and_exec_tools(tool):
    """风险（核心回归）：只读账号能调用这些工具 = 任意命令执行 / 桌面劫持。"""
    assert tool_allowed_for_user(tool, RO) is False


@pytest.mark.parametrize("tool", READONLY_ALLOWED)
def test_readonly_can_still_use_read_only_tools(tool):
    """风险：收紧权限时误伤查询能力（只读账号变成"什么都干不了"）。"""
    assert tool_allowed_for_user(tool, RO) is True


def test_readonly_is_restricted_to_allowlist_entirely():
    """风险：只读账号的放行集合必须**恰好**等于白名单，不能多一个。"""
    for name in sorted(WRITE_TOOL_NAMES):
        assert tool_allowed_for_user(name, RO) is False, f"{name} 未对只读账号拒绝"
    assert tool_allowed_for_user("brand_new_unknown_tool", RO) is False


def test_member_is_unrestricted():
    """风险：成员账号被误限制会打断正常业务（成员本就该能读写自己的目录）。"""
    for name in ("run_command", "write_file", "delete_file", "whatever_new_tool"):
        assert tool_allowed_for_user(name, MEMBER) is True


def test_admin_is_unrestricted():
    """风险：管理员被误限制 = 管理功能不可用。"""
    for name in ("run_command", "db_execute_query", "brand_new_tool"):
        assert tool_allowed_for_user(name, ADMIN) is True


def test_no_user_context_means_unrestricted():
    """风险：未登录（本机/服务调用）场景的语义必须明确，不能意外变成"全部拒绝"。

    当前契约：``user is None`` → 放行（由路径沙箱与请求鉴权层接管）。
    本用例把这个契约钉死，改动它会立刻暴露出来。
    """
    assert tool_allowed_for_user("run_command", None) is True
    assert tool_allowed_for_user("write_file", None) is True


# ───────────────────────── role_permission_hint：提示文案 ─────────────────────────


@pytest.mark.parametrize(
    ("user", "expected_substring"),
    [(ADMIN, "管理员"), (RO, "只读"), (MEMBER, "成员")],
)
def test_role_permission_hint_is_role_specific_and_non_empty(user, expected_substring):
    """风险：提示文案混淆角色会让 LLM 以为自己有权限，从而反复尝试被拒的工具。"""
    hint = role_permission_hint(user)
    assert isinstance(hint, str) and hint.strip()
    assert expected_substring in hint


def test_role_permission_hint_is_empty_without_user():
    """风险：无用户时给出"管理员"文案会误导模型。"""
    assert role_permission_hint(None) == ""


def test_role_permission_hint_differs_between_roles():
    """风险：三个角色文案相同说明分支被写坏。"""
    hints = {role_permission_hint(u) for u in (ADMIN, RO, MEMBER)}
    assert len(hints) == 3


# ───────────────────── resolve_current_user：请求上下文 → 用户 ─────────────────────


def test_resolve_current_user_returns_none_when_context_empty():
    """风险：无上下文时凭空造出一个用户，会让权限判定基于错误身份。"""
    assert resolve_current_user() is None


@pytest.mark.parametrize("uid", ["anonymous", "localhost", "service"])
def test_resolve_current_user_ignores_pseudo_users(uid):
    """风险：把 anonymous/localhost 当真人用户，会让未登录请求获得某人的私有目录。"""
    from core.security.context import set_request_context

    set_request_context(user_id=uid, role="admin", actor=uid)
    assert resolve_current_user() is None


def test_resolve_current_user_builds_user_from_context():
    """风险：角色/ID 传递错误 → 权限判定用错角色（例如只读被当成管理员）。"""
    from core.security.context import set_request_context

    set_request_context(user_id="u1", role="readonly", actor="张三")
    user = resolve_current_user()
    assert user is not None
    assert (user.id, user.role, user.username) == ("u1", "readonly", "张三")
    assert user.is_admin is False


def test_resolve_current_user_falls_back_to_db_for_unknown_role(monkeypatch):
    """风险：上下文里 role 非标准（旧客户端）时必须查库校验，且要检查 is_active。

    这里用桩数据库，避免触碰真实 data/keji.db。
    """

    class _StubDB:
        def get_user_by_id(self, uid):
            return {"id": uid, "username": "dbuser", "role": "member",
                    "display_name": "库里的用户", "is_active": 1}

    import core.database.db as dbmod

    monkeypatch.setattr(dbmod, "get_db", lambda: _StubDB())

    from core.security.context import set_request_context

    set_request_context(user_id="u9", role="", actor="x")
    user = resolve_current_user()
    assert user is not None and user.role == "member" and user.display_name == "库里的用户"


def test_resolve_current_user_rejects_inactive_db_user(monkeypatch):
    """风险：被停用的账号仍能通过旧 token 使用工具（应当解析为未登录）。"""

    class _StubDB:
        def get_user_by_id(self, uid):
            return {"id": uid, "username": "gone", "role": "member", "is_active": 0}

    import core.database.db as dbmod

    monkeypatch.setattr(dbmod, "get_db", lambda: _StubDB())

    from core.security.context import set_request_context

    set_request_context(user_id="u10", role="", actor="x")
    assert resolve_current_user() is None
