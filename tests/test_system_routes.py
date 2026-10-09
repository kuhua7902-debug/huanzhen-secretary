"""「系统状态」API 契约测试。

对应 `core/routes_system.py`——把命令行能力（诊断 / 备份）搬到网页。
钉住的行为：
- 诊断返回运行环境 / 数据规模 / 工具 / 磁盘 / 备份等分节
- deep=1 时附带环境自检结果
- 一键备份端点能创建备份，并在数据库缺失时返回可读的 400 而不是 500
- 全部端点要求管理员（非管理员拿不到）
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core import routes_system


# ─────────────────────────── 采集函数 ───────────────────────────


def test_runtime_shape():
    rt = routes_system._runtime()
    assert rt["app"] and rt["version"]
    assert rt["python"].count(".") == 2
    assert isinstance(rt["pid"], int) and rt["pid"] > 0


def test_disk_shape():
    disk = routes_system._disk()
    assert "free_gb" in disk and "total_gb" in disk
    assert disk["total_gb"] >= disk["free_gb"] >= 0


def test_tools_counts_builtin():
    tools = routes_system._tools()
    assert isinstance(tools["builtin"], int)
    assert tools["builtin"] >= 1, "内置工具应已注册"


# ─────────────────────────── HTTP 层 ───────────────────────────


async def _no_mcp() -> int | None:
    """避免测试中真正初始化 adapter（会拉起 MCP 子进程）。"""
    return None


@pytest.fixture()
def client(monkeypatch):
    """绕过鉴权：中间件关掉 + 管理员依赖打桩。

    注意：`APIKeyMiddleware` 在路由之前执行，只覆盖 `require_admin` 依赖是没用的
    （请求会先被中间件 401 掉），因此这里同时把鉴权设置改成 disabled。
    """
    from fastapi.testclient import TestClient

    import main
    from core.security import auth as auth_mod
    from core.security.deps import require_admin
    from core.security.users import CurrentUser

    monkeypatch.setattr(
        auth_mod,
        "get_security_settings",
        lambda reload=False: auth_mod.SecuritySettings(
            enabled=False,
            api_key="",
            allow_localhost_without_auth=False,
            public_prefixes=(),
            auth_mode="both",
        ),
    )
    monkeypatch.setattr(routes_system, "_mcp_tool_count", _no_mcp)
    main.app.dependency_overrides[require_admin] = lambda: CurrentUser(
        id="test-admin", username="admin", role="admin"
    )
    try:
        yield TestClient(main.app)
    finally:
        main.app.dependency_overrides.pop(require_admin, None)


def test_diagnostics_has_all_sections(client):
    resp = client.get("/api/system/diagnostics")
    assert resp.status_code == 200
    data = resp.json()
    for key in ("runtime", "database", "tools", "disk", "backups", "checks"):
        assert key in data, f"缺少分节：{key}"
    # 默认不跑完整自检，保持页面响应快
    assert data["checks"] == []


def test_diagnostics_deep_includes_checks(client):
    resp = client.get("/api/system/diagnostics?deep=1")
    assert resp.status_code == 200
    checks = resp.json()["checks"]
    assert isinstance(checks, list) and checks, "deep=1 应返回环境自检结果"
    sample = checks[0]
    for key in ("level", "title", "group"):
        assert key in sample


def test_backups_endpoint_lists(client):
    resp = client.get("/api/system/backups")
    assert resp.status_code == 200
    assert isinstance(resp.json()["backups"], list)


# ─────────────────────────── 备份端点 ───────────────────────────


class _FakeBackup:
    def __init__(self, tmp: Path, raise_missing: bool = False):
        self.tmp = tmp
        self.raise_missing = raise_missing

    def create_backup(self) -> str:
        if self.raise_missing:
            raise FileNotFoundError("未找到数据库")
        path = self.tmp / "huanzhen-backup-20260101-000000000.zip"
        path.write_bytes(b"fake-zip")
        return str(path)

    def list_backups(self):
        return sorted(str(p) for p in self.tmp.glob("huanzhen-backup-*.zip"))

    def backup_dir(self) -> str:
        return str(self.tmp)


def test_backup_endpoint_creates_backup(client, monkeypatch, tmp_path):
    fake = _FakeBackup(tmp_path)
    monkeypatch.setattr(routes_system, "load_script", lambda name: fake if name == "backup.py" else None)

    resp = client.post("/api/system/backup")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["name"].endswith(".zip")
    assert len(body["backups"]) == 1, "返回体应带上刷新后的备份列表"
    assert body["size_mb"] >= 0


def test_backup_endpoint_returns_readable_400_without_db(client, monkeypatch, tmp_path):
    """数据库还没建时，应是可读的 400，而不是 500 内部错误。"""
    fake = _FakeBackup(tmp_path, raise_missing=True)
    monkeypatch.setattr(routes_system, "load_script", lambda name: fake if name == "backup.py" else None)

    resp = client.post("/api/system/backup")
    assert resp.status_code == 400
    assert "备份" in resp.json()["detail"]


def test_backup_endpoint_reports_missing_script(client, monkeypatch):
    monkeypatch.setattr(routes_system, "load_script", lambda name: None)
    resp = client.post("/api/system/backup")
    assert resp.status_code == 500
    assert "backup.py" in resp.json()["detail"]
