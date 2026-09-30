"""D. 全局文件路径沙箱 —— ``core/path_policy``。

风险：这是所有文件类工具（read_file / write_file / browse_files / run_code 预注入）
共用的最后一道边界。它一旦算错，模型就能读写整台机器。

全部用例显式传 ``config`` 与 ``project_root=tmp_path``，不依赖真实 ``config.yaml``，
也不会去 ``ensure_layout()`` 真实工作区（config 里显式关掉 workspace）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.path_policy import (
    PathPolicyError,
    assert_path_allowed,
    check_path,
    get_allowed_roots,
    is_sandbox_enabled,
    is_under,
    resolve_user_path,
)


# ────────────────────────────── is_under ──────────────────────────────


def test_is_under_equal_path_is_true(tmp_path):
    """风险：目录自身必须算"在允许范围内"，否则浏览根目录会被拒。"""
    assert is_under(tmp_path, tmp_path) is True


def test_is_under_child_path_is_true(tmp_path):
    """风险：子路径误判为越界会让所有文件操作不可用。"""
    child = tmp_path / "a" / "b.txt"
    child.parent.mkdir(parents=True, exist_ok=True)
    child.write_text("x", encoding="utf-8")
    assert is_under(child, tmp_path) is True


def test_is_under_parent_or_sibling_is_false(tmp_path):
    """风险：把父目录/兄弟目录当成子目录 = 允许目录形同虚设。"""
    sub = tmp_path / "a"
    sub.mkdir()
    assert is_under(tmp_path, sub) is False
    assert is_under(tmp_path.parent, tmp_path) is False


def test_is_under_sibling_prefix_trap():
    """风险（经典 bug）：用 ``str.startswith`` 实现时 ``C:\\ab`` 会被当成 ``C:\\a`` 的子路径。"""
    assert is_under(Path("C:/ab"), Path("C:/a")) is False
    assert is_under(Path("C:/abc/d.txt"), Path("C:/ab")) is False
    assert is_under(Path("C:/a/b"), Path("C:/a")) is True


def test_is_under_normalises_relative_and_dotdot(tmp_path):
    """风险：``..`` 未被 resolve 就参与判定 → 用 ``<root>/../../`` 逃逸。"""
    evil = tmp_path / "sub" / ".." / ".."
    assert is_under(evil, tmp_path) is False
    assert is_under(tmp_path / "sub" / "..", tmp_path) is True


# ─────────────────────────── resolve_user_path ───────────────────────────


def test_resolve_user_path_relative_against_project_root(tmp_path):
    """风险：相对路径若按进程 CWD 解析，模型传 ``data/x`` 会落到别处。"""
    got = resolve_user_path("sub/x.txt", tmp_path)
    assert got == (tmp_path / "sub" / "x.txt").resolve()
    assert got.is_absolute()


def test_resolve_user_path_keeps_absolute(tmp_path):
    """风险：绝对路径被二次拼接会指向意外位置。"""
    target = (tmp_path / "abs.txt").resolve()
    assert resolve_user_path(str(target), tmp_path) == target


def test_resolve_user_path_expands_tilde(tmp_path):
    """风险：``~`` 不展开时会被当成项目根下的普通名字（用户拿不到自己的桌面文件）。"""
    assert resolve_user_path("~", tmp_path) == Path.home().resolve()


def test_resolve_user_path_strips_whitespace(tmp_path):
    """风险：模型常带首尾空格传路径（``"  a.txt  "``），不 strip 会直接报文件不存在。"""
    assert resolve_user_path("  a.txt  ", tmp_path) == (tmp_path / "a.txt").resolve()


@pytest.mark.parametrize("bad", ["", "   "])
def test_resolve_user_path_rejects_empty(bad, tmp_path):
    """风险：空路径 resolve 后变成项目根，等于把根目录当文件操作。"""
    with pytest.raises(PathPolicyError):
        resolve_user_path(bad, tmp_path)


# ────────────────────────── get_allowed_roots ──────────────────────────


def test_get_allowed_roots_from_explicit_config(tmp_path, sandbox_config):
    """风险：允许目录解析错 → 要么过宽（越权）要么过窄（功能全废）。"""
    roots = get_allowed_roots(sandbox_config, tmp_path)
    assert roots, "允许目录不应为空"
    for r in roots:
        assert r.is_absolute()
        assert is_under(r, tmp_path), f"{r} 越出了指定的 project_root"
    assert any(r.name == "knowledge" for r in roots)


def test_get_allowed_roots_respects_sandbox_off(tmp_path):
    """风险：关闭沙箱后仍返回允许目录会给用户"其实还有限制"的错觉；
    当前契约：关闭时返回空列表 = 不限制。"""
    cfg = {"security": {"filesystem_sandbox": False}, "workspace": {"enabled": False}}
    assert is_sandbox_enabled(cfg) is False
    assert get_allowed_roots(cfg, tmp_path) == []


def test_is_sandbox_enabled_defaults_to_true():
    """风险：默认 fail-open（未配置即不限制）会让新装机完全没有路径沙箱。"""
    assert is_sandbox_enabled({}) is True
    assert is_sandbox_enabled({"security": {}}) is True
    assert is_sandbox_enabled({"security": {"filesystem_sandbox": True}}) is True
    assert is_sandbox_enabled({"security": {"filesystem_sandbox": False}}) is False


def test_get_allowed_roots_deduplicates(tmp_path):
    """风险：重复目录会重复出现在给模型的提示里，也可能绕过某些去重计数逻辑。"""
    cfg = {
        "security": {"filesystem_sandbox": True},
        "workspace": {"enabled": False},
        "mcp": {"filesystem_allowed_dirs": ["knowledge", "knowledge"],
                "include_knowledge": True, "include_data": False},
    }
    roots = get_allowed_roots(cfg, tmp_path)
    keys = [str(p).lower() for p in roots]
    assert len(keys) == len(set(keys))


def test_get_allowed_roots_ignores_dangerous_system_roots(tmp_path):
    """风险：配置里写成 ``C:/`` 等于把整块盘交给模型。"""
    cfg = {
        "security": {"filesystem_sandbox": True},
        "workspace": {"enabled": False},
        "mcp": {"filesystem_allowed_dirs": ["C:/", "knowledge"],
                "include_knowledge": False, "include_data": False},
    }
    roots = get_allowed_roots(cfg, tmp_path)
    assert all(str(r).lower() not in ("c:\\", "c:/") for r in roots)
    assert roots and all(is_under(r, tmp_path) for r in roots)


# ────────────────────────────── check_path ──────────────────────────────


def test_check_path_success_returns_tuple_with_none_error(tmp_path, sandbox_config):
    """风险：工具层按 ``(path, err)`` 解包；返回形状变化会让所有文件工具崩。"""
    target = tmp_path / "knowledge"
    result = check_path(str(target), sandbox_config, tmp_path)
    assert isinstance(result, tuple) and len(result) == 2
    got, err = result
    assert err is None and got == str(target.resolve())


def test_check_path_rejects_empty_without_allow_empty(tmp_path, sandbox_config):
    """风险：空路径被放行会让工具对 CWD 操作（通常是仓库根）。"""
    got, err = check_path("", sandbox_config, tmp_path)
    assert got is None and err is not None and err.startswith("错误：")


def test_check_path_allow_empty_returns_first_allowed_root(tmp_path, sandbox_config):
    """风险：browse_files 不带路径时应落到允许根，而不是 CWD 或桌面。"""
    got, err = check_path("", sandbox_config, tmp_path, allow_empty=True)
    roots = get_allowed_roots(sandbox_config, tmp_path)
    assert err is None and got == str(roots[0])


def test_check_path_returns_error_text_for_outside_path(tmp_path, sandbox_config):
    """风险：越界必须变成友好错误而不是异常（工具调用不能 500）。"""
    outside = tmp_path.parent / "definitely-not-allowed"
    got, err = check_path(str(outside), sandbox_config, tmp_path)
    assert got is None
    assert err is not None and err.startswith("错误：") and "不在允许访问范围内" in err


def test_check_path_rejects_escape_via_dotdot(tmp_path, sandbox_config):
    """风险（核心）：``knowledge/../../..`` 这类相对逃逸必须被 resolve 后拒绝。"""
    escape = str(tmp_path / "knowledge" / ".." / ".." / ".." / "Windows")
    got, err = check_path(escape, sandbox_config, tmp_path)
    assert got is None and err is not None


def test_check_path_must_exist_error_is_returned_not_raised(tmp_path, sandbox_config):
    """风险：must_exist 违反必须以返回值表达（read_document 依赖它给提示）。"""
    got, err = check_path(str(tmp_path / "knowledge" / "ghost.txt"),
                          sandbox_config, tmp_path, must_exist=True)
    assert got is None and err is not None and "不存在" in err


def test_check_path_must_be_file_and_must_be_dir(tmp_path, sandbox_config):
    """风险：类型校验丢失会让 read_document 拿到目录后抛未捕获异常。"""
    f = tmp_path / "knowledge" / "a.txt"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("x", encoding="utf-8")

    assert check_path(str(f), sandbox_config, tmp_path, must_be_file=True)[1] is None
    assert check_path(str(f), sandbox_config, tmp_path, must_be_dir=True)[0] is None


# ────────────────────────── assert_path_allowed ──────────────────────────


def test_assert_path_allowed_must_exist_raises_path_policy_error(tmp_path, sandbox_config):
    """风险：不存在的路径若被静默放行，后续 open() 会抛 FileNotFoundError（500）。"""
    with pytest.raises(PathPolicyError):
        assert_path_allowed(str(tmp_path / "knowledge" / "nope.txt"),
                            sandbox_config, tmp_path, must_exist=True)


def test_assert_path_allowed_raises_for_outside_path(tmp_path, sandbox_config):
    """风险：assert 版本必须抛错（不能只返回 None），调用方依赖异常做拦截。"""
    with pytest.raises(PathPolicyError):
        assert_path_allowed(str(tmp_path.parent / "outside-dir"), sandbox_config, tmp_path)


def test_assert_path_allowed_returns_resolved_path(tmp_path, sandbox_config):
    """风险：返回未解析路径会让后续 open 相对 CWD 打开别的文件。"""
    f = tmp_path / "knowledge" / "ok.txt"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("ok", encoding="utf-8")
    got = assert_path_allowed(str(f), sandbox_config, tmp_path, must_exist=True, must_be_file=True)
    assert got == f.resolve() and got.is_absolute()


def test_path_policy_error_is_a_permission_error():
    """风险：上层普遍 ``except PermissionError`` 做 403 转换；继承关系断掉会变成 500。"""
    assert issubclass(PathPolicyError, PermissionError)
