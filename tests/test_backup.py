"""数据备份契约测试。

钉住的行为：
- 备份是**一致快照**（用 SQLite 备份 API，而非复制文件）
- 产物是单个 zip，含数据库、配置与 MANIFEST（带恢复说明）
- 自动轮转只保留最近 N 份
- 每日自动备份有节流（同一天不会重复备份）
- 没有数据库时明确失败而不是生成空备份
"""

from __future__ import annotations

import importlib.util
import os
import sqlite3
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load_backup():
    """按路径加载 scripts/backup.py（scripts 不是包）。"""
    path = ROOT / "scripts" / "backup.py"
    spec = importlib.util.spec_from_file_location("huanzhen_backup_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["huanzhen_backup_test"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """隔离环境：临时数据库 + 临时备份目录。"""
    mod = _load_backup()
    db = tmp_path / "data" / "huanzhen.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    tmp_backups = tmp_path / "backups"
    tmp_backups.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(mod, "db_path", lambda: str(db))
    monkeypatch.setattr(mod, "backup_dir", lambda: str(tmp_backups))
    return mod, db, tmp_backups


def _make_db(path: Path, rows: int = 3) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, name TEXT)")
        conn.executemany(
            "INSERT OR REPLACE INTO users (id, name) VALUES (?, ?)",
            [(f"u{i}", f"用户{i}") for i in range(rows)],
        )
        conn.commit()
    finally:
        conn.close()


# ─────────────────────────── 基本行为 ───────────────────────────


def test_backup_creates_zip_with_db_and_manifest(env):
    mod, db, backups = env
    _make_db(db, rows=3)

    zip_path = mod.create_backup(keep=7)
    assert Path(zip_path).is_file()
    assert Path(zip_path).parent == backups

    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        assert "huanzhen.db" in names
        assert "MANIFEST.txt" in names

        manifest = zf.read("MANIFEST.txt").decode("utf-8")
        assert "恢复方法" in manifest
        assert "不包含 .env" in manifest

        # 快照必须是可用的 SQLite 且数据完整
        extracted = backups / "extracted.db"
        extracted.write_bytes(zf.read("huanzhen.db"))
    conn = sqlite3.connect(extracted)
    try:
        count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    finally:
        conn.close()
    assert count == 3


def test_display_path_handles_cross_drive():
    """风险：database.path 配到别的盘符时 os.path.relpath 抛 ValueError，会让备份整体失败。"""
    mod = _load_backup()
    inside = os.path.join(mod.ROOT, "data", "huanzhen.db")
    assert mod._display_path(inside).replace("\\", "/").startswith("data")
    # 跨盘符必须安全降级为绝对路径，而不是抛异常
    assert mod._display_path(r"Z:\elsewhere\huanzhen.db") == r"Z:\elsewhere\huanzhen.db"


def test_backup_missing_database_raises(env):
    mod, db, _backups = env
    # 不创建数据库
    with pytest.raises(FileNotFoundError):
        mod.create_backup(keep=3)


def test_backup_includes_config_when_present(env, monkeypatch, tmp_path):
    mod, db, _backups = env
    _make_db(db)
    cfg = tmp_path / "config.yaml"
    cfg.write_text("models:\n  default: deepseek\n", encoding="utf-8")
    monkeypatch.setattr(mod, "ROOT", str(tmp_path))
    monkeypatch.setattr(mod, "_extra_files", lambda: [(str(cfg), "config.yaml")])

    zip_path = mod.create_backup(keep=3)
    with zipfile.ZipFile(zip_path) as zf:
        assert "config.yaml" in zf.namelist()


# ─────────────────────────── 轮转 ───────────────────────────


def test_prune_keeps_only_recent(env):
    mod, db, backups = env
    _make_db(db)
    for i in range(5):
        path = backups / f"huanzhen-backup-2026010{i}-000000.zip"
        path.write_bytes(b"x")

    removed = mod.prune_backups(keep=2)
    remaining = mod.list_backups()
    assert len(remaining) == 2
    assert len(removed) == 3
    # 保留的应是最新的两份
    assert {Path(p).name for p in remaining} == {
        "huanzhen-backup-20260104-000000.zip",
        "huanzhen-backup-20260103-000000.zip",
    }


def test_list_backups_newest_first(env):
    mod, db, backups = env
    for stamp in ("20260101-000000", "20260103-000000", "20260102-000000"):
        (backups / f"huanzhen-backup-{stamp}.zip").write_bytes(b"x")
    names = [Path(p).name for p in mod.list_backups()]
    assert names == [
        "huanzhen-backup-20260103-000000.zip",
        "huanzhen-backup-20260102-000000.zip",
        "huanzhen-backup-20260101-000000.zip",
    ]


def test_create_backup_rotates_automatically(env):
    mod, db, backups = env
    _make_db(db)
    for _ in range(4):
        mod.create_backup(keep=2)
    assert len(mod.list_backups()) == 2


# ─────────────────────────── 自动备份节流 ───────────────────────────


def test_maybe_auto_backup_runs_once_per_day(env):
    mod, db, _backups = env
    _make_db(db)

    first = mod.maybe_auto_backup(keep=3)
    second = mod.maybe_auto_backup(keep=3)

    assert first is not None, "首次应创建备份"
    assert second is None, "同一天不应重复备份"
    assert len(mod.list_backups()) == 1


def test_maybe_auto_backup_never_raises_without_db(env):
    """备份是可选能力：没有数据库 / 出错时必须静默返回 None，不能影响服务启动。"""
    mod, _db, _backups = env
    assert mod.maybe_auto_backup() is None
