"""C. 团队工作区路径隔离 —— ``core.workspace``。

风险：这是"多用户共用一台机器"的文件隔离层。任一处分隔失效，
成员 A 就能读/写成员 B 的私人文件，或只读账号可以写盘。

所有用例都通过 ``temp_workspace`` 夹具把根目录重定向到 ``tmp_path``，
真实 ``data/workspace`` 不会被碰。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.security.users import CurrentUser
from core.workspace import WorkspaceError

# 真实仓库路径：只用于"监控点"断言，任何用例都不会写入这里
REPO_ROOT = Path(__file__).resolve().parent.parent

MEMBER = CurrentUser(id="m1", username="m1", role="member", display_name="成员一")
OTHER_MEMBER = CurrentUser(id="m2", username="m2", role="member", display_name="成员二")
ADMIN = CurrentUser(id="adm", username="adm", role="admin", display_name="管理员")
READONLY = CurrentUser(id="r1", username="r1", role="readonly", display_name="只读")


# ────────────────────────── user_dir 目录名消毒 ──────────────────────────


@pytest.mark.parametrize(
    "raw",
    ["../../etc", "..\\..\\Windows", "./../etc", "a/../../b", "a b c",
     "user name!", "c:/windows/system32", "....//....//x"],
)
def test_user_dir_strips_traversal_and_unsafe_chars(temp_workspace, raw):
    """风险（核心回归）：用户 ID 若原样拼进路径，``../../etc`` 就能把目录建到工作区外。"""
    from core.workspace import _is_under, user_dir, users_root

    d = user_dir(raw)
    assert _is_under(d, users_root()), f"{raw!r} 逃出了 users 根目录: {d}"
    assert ".." not in d.parts and "/" not in d.name and "\\" not in d.name


@pytest.mark.parametrize("raw", ["", "   ", "..", "./..", "///", "!!!", "@@@"])
def test_user_dir_rejects_input_with_no_safe_characters(temp_workspace, raw):
    """风险：消毒后为空必须报错，不能退化成 users_root 本身（= 拿到所有人的目录）。"""
    from core.workspace import user_dir

    with pytest.raises(WorkspaceError):
        user_dir(raw)


def test_user_dir_truncates_overlong_id(temp_workspace):
    """风险：超长 ID 会撑爆 Windows 路径长度限制，导致整个工作区不可用。"""
    from core.workspace import user_dir

    assert len(user_dir("u" * 500).name) <= 64


def test_user_dir_is_under_users_root(temp_workspace):
    """风险：正常 ID 也必须落在 users/ 下（不能污染 shared/）。"""
    from core.workspace import user_dir, users_root

    d = user_dir("alice-01")
    assert d.parent == users_root() and d.name == "alice-01"


# ────────────────────────────── _is_under ──────────────────────────────


def test_is_under_equal_child_and_parent(temp_workspace):
    """风险：`relative_to` 语义写错 → 允许目录判定整体失效（沙箱形同虚设）。"""
    from core.workspace import _is_under

    root = temp_workspace.workspace_root()
    assert _is_under(root, root) is True
    assert _is_under(root / "shared" / "a.txt", root) is True
    assert _is_under(root.parent, root) is False
    assert _is_under(root.parent.parent, root) is False


def test_is_under_does_not_confuse_sibling_prefix(temp_workspace):
    """风险：字符串 startswith 写法会把 ``C:\\a`` 之外的 ``C:\\ab`` 也当成子路径。"""
    from core.workspace import _is_under
    from pathlib import Path

    assert _is_under(Path("C:/ab"), Path("C:/a")) is False
    assert _is_under(Path("C:/a/b"), Path("C:/a")) is True


# ───────────────────────────── assert_access ─────────────────────────────


def test_assert_access_permits_member_own_dir_and_shared(workspace_files):
    """风险：正常读写被误拒 → 功能不可用（回归会立刻被发现）。"""
    from core.workspace import assert_access

    assert assert_access(str(workspace_files["mine"]), MEMBER) == workspace_files["mine"].resolve()
    assert assert_access(str(workspace_files["shared"]), MEMBER) == workspace_files["shared"].resolve()
    assert assert_access(str(workspace_files["mine_dir"]), MEMBER, must_be_dir=True)


def test_assert_access_permits_admin_anywhere_in_workspace(workspace_files):
    """风险：管理员需要管理所有用户目录，被限制会导致运维不可用。"""
    from core.workspace import assert_access

    assert assert_access(str(workspace_files["other"]), ADMIN)
    assert assert_access(str(workspace_files["root"]), ADMIN)


def test_assert_access_rejects_path_outside_workspace(workspace_files):
    """风险（核心）：工作区外的路径若可通过，沙箱就完全失效了。"""
    from core.workspace import assert_access

    with pytest.raises(WorkspaceError, match="工作区"):
        assert_access(str(workspace_files["outside"]), MEMBER)


def test_assert_access_rejects_other_users_private_dir_for_member(workspace_files):
    """风险（核心）：成员 A 读/写成员 B 的私人目录 = 数据泄露。"""
    from core.workspace import assert_access

    with pytest.raises(WorkspaceError, match="无权访问"):
        assert_access(str(workspace_files["other"]), MEMBER)


def test_assert_access_rejects_readonly_write_to_own_dir(workspace_files):
    """风险（核心）：只读账号必须完全不能写盘，哪怕是在自己的目录里。"""
    from core.workspace import assert_access

    target = workspace_files["ws"].user_dir("r1")
    target.mkdir(parents=True, exist_ok=True)
    f = target / "x.txt"
    f.write_text("x", encoding="utf-8")

    assert assert_access(str(f), READONLY) == f.resolve()  # 读是允许的
    with pytest.raises(WorkspaceError, match="写入权限"):
        assert_access(str(f), READONLY, write=True)


def test_assert_access_rejects_readonly_write_to_shared(workspace_files):
    """风险：只读账号往共享目录写文件会污染所有人可见的数据。"""
    from core.workspace import assert_access

    with pytest.raises(WorkspaceError, match="写入权限"):
        assert_access(str(workspace_files["shared"]), READONLY, write=True)


def test_assert_access_member_can_write_shared_and_own(workspace_files):
    """风险：成员连共享目录都不能写 → 工作区功能名存实亡。"""
    from core.workspace import assert_access

    assert assert_access(str(workspace_files["shared"]), MEMBER, write=True)
    assert assert_access(str(workspace_files["mine"]), MEMBER, write=True)


@pytest.mark.parametrize("path", ["", "   ", None])
def test_assert_access_rejects_empty_path(workspace_files, path):
    """风险：空路径被 resolve 成 CWD（仓库目录）从而绕过工作区限制。"""
    from core.workspace import assert_access

    with pytest.raises(WorkspaceError):
        assert_access(path, MEMBER)


def test_assert_access_must_exist_and_type_checks(workspace_files):
    """风险：``must_exist`` 等护栏失效会让后续 open() 抛未捕获异常（500 而非友好提示）。"""
    from core.workspace import assert_access

    ghost = workspace_files["mine_dir"] / "ghost.txt"
    with pytest.raises(WorkspaceError, match="不存在"):
        assert_access(str(ghost), MEMBER, must_exist=True)
    with pytest.raises(WorkspaceError, match="不是文件夹"):
        assert_access(str(workspace_files["mine"]), MEMBER, must_be_dir=True)
    with pytest.raises(WorkspaceError, match="不是文件"):
        assert_access(str(workspace_files["shared_dir"]), MEMBER, must_be_file=True)


def test_assert_access_returns_resolved_absolute_path(workspace_files):
    """风险：返回未解析路径会让上层用相对路径访问到别的地方。"""
    from core.workspace import assert_access

    got = assert_access(str(workspace_files["mine"]), MEMBER)
    assert got.is_absolute() and got == workspace_files["mine"].resolve()


# ────────────────────────────── can_write ──────────────────────────────


@pytest.mark.parametrize("key", ["mine", "other", "shared", "outside", "root"])
def test_can_write_is_always_false_for_readonly(workspace_files, key):
    """风险（核心回归）：只读账号在任何路径上都不得有写权限。"""
    from core.workspace import can_write

    assert can_write(workspace_files[key], READONLY) is False


@pytest.mark.parametrize(
    ("key", "expected"),
    [("mine", True), ("shared", True), ("other", False), ("outside", False)],
)
def test_can_write_matrix_for_member(workspace_files, key, expected):
    """风险：成员写权限矩阵错位（写到别人目录 / 工作区外）。"""
    from core.workspace import can_write

    assert can_write(workspace_files[key], MEMBER) is expected


@pytest.mark.parametrize(
    ("key", "expected"),
    [("mine", True), ("other", True), ("shared", True), ("outside", False)],
)
def test_can_write_matrix_for_admin(workspace_files, key, expected):
    """风险：管理员必须能管所有用户目录，但不能越过工作区边界（沙箱仍生效）。"""
    from core.workspace import can_write

    assert can_write(workspace_files[key], ADMIN) is expected


# ─────────────────────── check_workspace_path / 其他 ───────────────────────


def test_check_workspace_path_returns_tool_friendly_tuple(workspace_files):
    """风险：工具层要靠 (path, err) 双元组决定返回文案；异常泄漏会让工具调用 500。"""
    from core.workspace import check_workspace_path

    ok, err = check_workspace_path(str(workspace_files["mine"]), MEMBER)
    assert err is None and ok == str(workspace_files["mine"].resolve())

    ok, err = check_workspace_path(str(workspace_files["outside"]), MEMBER)
    assert ok is None and err is not None and err.startswith("错误：")

    ok, err = check_workspace_path(str(workspace_files["shared"]), READONLY, write=True)
    assert ok is None and err is not None and "写入权限" in err


def test_use_workspace_for_user_only_for_real_users(temp_workspace):
    """风险：anonymous/localhost/service 被当成真人用户 → 未登录请求拿到某人的私有目录。"""
    from core.workspace import use_workspace_for_user

    cfg = {"workspace": {"enabled": True}}
    assert use_workspace_for_user(MEMBER, cfg) is True
    for uid in ("anonymous", "localhost", "service", "api_key", "", None):
        u = CurrentUser(id=uid, username=uid or "", role="member") if uid is not None else None
        assert use_workspace_for_user(u, cfg) is False


def test_use_workspace_for_user_respects_enabled_flag(temp_workspace):
    """风险：工作区被关闭后仍强制启用，会让"关闭隔离"设置失效。"""
    from core.workspace import use_workspace_for_user

    assert use_workspace_for_user(MEMBER, {"workspace": {"enabled": False}}) is False
    assert use_workspace_for_user(MEMBER, {"workspace": {}}) is True


def test_list_roots_exposes_admin_only_root_to_admin(workspace_files):
    """风险：普通成员看到"全部用户"根目录 = 目录浏览权限泄露给所有人。"""
    from core.workspace import list_roots

    member_ids = [r["id"] for r in list_roots(MEMBER)]
    admin_ids = [r["id"] for r in list_roots(ADMIN)]
    assert member_ids == ["shared", "mine"]
    assert "users" in admin_ids


def test_list_roots_marks_readonly_roots_as_not_writable(workspace_files):
    """风险：前端按 can_write 渲染"上传"按钮；标错会让只读账号以为能写。"""
    from core.workspace import list_roots

    for root in list_roots(READONLY):
        assert root["can_write"] is False
    for root in list_roots(MEMBER):
        assert root["can_write"] is True


def test_path_display_maps_shared_and_root(workspace_files):
    """风险：面包屑显示真实绝对路径会把服务器目录结构暴露给前端用户。"""
    from core.workspace import path_display

    assert path_display(str(workspace_files["shared"]), MEMBER) == "共享文件 / shared.txt"
    assert path_display(str(workspace_files["root"]), MEMBER) == "工作区"


def test_path_display_outside_workspace_returns_input(workspace_files):
    """风险：工作区外路径不能崩（异常会让整个文件浏览接口 500）。"""
    from core.workspace import path_display

    outside = str(workspace_files["outside"])
    assert path_display(outside, MEMBER) == outside


def test_path_display_replaces_user_id_with_display_name(workspace_files, monkeypatch):
    """风险：用户目录展示用内部 ID（泄露用户表主键）；此处同时避免触碰真实数据库。

    真实 ``data/keji.db`` 不参与：``get_db`` 在 ``path_display`` 内部按需导入，
    这里替换成桩对象。
    """

    class _StubDB:
        def get_user_by_id(self, uid):
            return {"id": uid, "username": uid, "display_name": "张三"}

    import core.database.db as dbmod
    from core.workspace import path_display

    monkeypatch.setattr(dbmod, "get_db", lambda: _StubDB())
    shown = path_display(str(workspace_files["mine"]), MEMBER)
    assert shown.startswith("用户文件 / 张三 / ")
    assert "m1" not in shown


def test_ensure_layout_creates_expected_skeleton(temp_workspace):
    """风险：骨架缺失会让所有文件工具在首次运行时直接失败。"""
    from core.workspace import shared_dir, users_root, workspace_root

    assert workspace_root().is_dir() and shared_dir().is_dir() and users_root().is_dir()
    assert (shared_dir() / "README.txt").is_file()


def test_ensure_user_dir_creates_and_is_idempotent(temp_workspace):
    """风险：重复调用应幂等（首次登录并发调用会同时创建目录）。"""
    from core.workspace import ensure_user_dir, user_dir

    first = ensure_user_dir("alice")
    second = ensure_user_dir("alice")
    assert first == second == user_dir("alice") and first.is_dir()


def test_workspace_error_is_a_permission_error():
    """风险：调用方普遍 ``except PermissionError``；若 WorkspaceError 不是它的子类，
    越权会变成未捕获异常（500）而不是被转换成友好提示或 403。"""
    assert issubclass(WorkspaceError, PermissionError)


def test_production_workspace_root_is_the_repo_data_dir():
    """监控点：确认 ``core.workspace`` 默认根目录仍指向仓库 ``data/workspace``。

    风险：这个断言失败说明工作区被移位（配置/代码改动）。同时它是一个"提醒"——
    正因为默认根目录是真实用户数据，本文件里每个用例都必须用 ``temp_workspace``
    把 ``_PROJECT_ROOT`` 重定向到 ``tmp_path``，绝不能直接调用被测函数。
    """
    from core.workspace import workspace_root

    assert workspace_root() == (REPO_ROOT / "data" / "workspace").resolve()
