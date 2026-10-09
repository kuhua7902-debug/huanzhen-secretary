"""数据保留策略契约测试。

钉住的行为：
- 超过保留期的审计记录**按月归档为 gz**（不是直接删除）
- 无法判定时间的记录**一律保留**（宁可占空间，不可丢审计）
- dry-run 不改动任何文件
- 归档文件按保留期清理、旧会话压缩归档、数据库审计按时间删除
"""

from __future__ import annotations

import gzip
import importlib.util
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load():
    path = ROOT / "scripts" / "retention.py"
    spec = importlib.util.spec_from_file_location("huanzhen_retention_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["huanzhen_retention_test"] = module
    spec.loader.exec_module(module)
    return module


DAY = 86400


def _audit_line(ts: float, tool: str = "read_file") -> str:
    return json.dumps({"ts": ts, "event_type": "tool_call", "tool_name": tool}, ensure_ascii=False)


# ─────────────────────────── 时间解析 ───────────────────────────


def test_line_ts_accepts_epoch_and_iso():
    mod = _load()
    assert mod._line_ts(_audit_line(1700000000.0)) == 1700000000.0
    iso = json.dumps({"timestamp": "2026-01-02T03:04:05Z"})
    assert mod._line_ts(iso) is not None
    assert mod._line_ts(json.dumps({"tool": "x"})) is None      # 无时间字段
    assert mod._line_ts("不是 JSON") is None


# ─────────────────────────── 审计归档 ───────────────────────────


def test_archive_audit_splits_by_month_and_keeps_recent(tmp_path):
    mod = _load()
    now = time.time()
    log = tmp_path / "audit.jsonl"
    log.write_text(
        "\n".join([
            _audit_line(now - 200 * DAY),        # 老 → 归档
            _audit_line(now - 185 * DAY),        # 老 → 归档
            _audit_line(now - 1 * DAY),          # 新 → 保留
            _audit_line(now),                    # 新 → 保留
            json.dumps({"event_type": "x"}),     # 无时间 → 保留
        ]) + "\n",
        encoding="utf-8",
    )

    report = mod.archive_audit_log(str(log), audit_days=90)
    assert report["archived_lines"] == 2
    assert report["kept_lines"] == 2
    assert report["undated_lines"] == 1

    # 归档文件存在且内容完整（按月分文件）
    archive_dir = tmp_path / "archive"
    gz_files = sorted(archive_dir.glob("audit-*.jsonl.gz"))
    assert gz_files, "应生成按月归档的 gz 文件"
    total = 0
    for f in gz_files:
        with gzip.open(f, "rt", encoding="utf-8") as fh:
            total += len([ln for ln in fh if ln.strip()])
    assert total == 2, "归档行数应等于被移除的旧行数"

    # 原文件只剩新的 + 无时间的
    remaining = [ln for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(remaining) == 3


def test_archive_audit_dry_run_changes_nothing(tmp_path):
    mod = _load()
    log = tmp_path / "audit.jsonl"
    original = _audit_line(time.time() - 400 * DAY) + "\n"
    log.write_text(original, encoding="utf-8")

    report = mod.archive_audit_log(str(log), audit_days=90, dry_run=True)
    assert report["archived_lines"] == 1
    assert log.read_text(encoding="utf-8") == original, "预演不得改动文件"
    assert not (tmp_path / "archive").exists()


def test_archive_audit_missing_file_is_noop(tmp_path):
    mod = _load()
    report = mod.archive_audit_log(str(tmp_path / "nope.jsonl"), audit_days=90)
    assert report["archived_lines"] == 0


# ─────────────────────────── 归档清理 ───────────────────────────


def test_prune_archives_removes_only_expired(tmp_path):
    mod = _load()
    d = tmp_path / "archive"
    d.mkdir()
    old = d / "audit-2020-01.jsonl.gz"
    fresh = d / "audit-2026-01.jsonl.gz"
    for f in (old, fresh):
        f.write_bytes(b"x")
    os.utime(old, (time.time() - 400 * DAY, time.time() - 400 * DAY))

    report = mod.prune_archives(str(d), archive_days=365)
    assert "audit-2020-01.jsonl.gz" in report["removed"]
    assert not old.exists()
    assert fresh.exists(), "未过期的归档不能被删"


# ─────────────────────────── 旧会话归档 ───────────────────────────


def test_archive_sessions_moves_old_files(tmp_path, monkeypatch):
    mod = _load()
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    old = sessions / "old.jsonl"
    new = sessions / "new.jsonl"
    old.write_text('{"a":1}\n', encoding="utf-8")
    new.write_text('{"b":2}\n', encoding="utf-8")
    os.utime(old, (time.time() - 400 * DAY, time.time() - 400 * DAY))

    monkeypatch.setattr(mod, "sessions_dir", lambda: str(sessions))
    report = mod.archive_sessions(sessions_days=180)

    assert report["archived"] == 1
    assert not old.exists(), "旧会话应被移走"
    assert new.exists(), "新会话保持不动"
    archived = sessions / "archive" / "old.jsonl.gz"
    assert archived.is_file()
    with gzip.open(archived, "rt", encoding="utf-8") as fh:
        assert '{"a":1}' in fh.read()


# ─────────────────────────── 数据库审计清理 ───────────────────────────


def test_prune_db_audit_deletes_only_expired(tmp_path, monkeypatch):
    mod = _load()
    db = tmp_path / "huanzhen.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE audit_events (id INTEGER PRIMARY KEY, created_at REAL)")
    now = time.time()
    conn.executemany(
        "INSERT INTO audit_events (created_at) VALUES (?)",
        [(now - 400 * DAY,), (now - 200 * DAY,), (now,)],
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(mod, "db_path", lambda: str(db))
    report = mod.prune_db_audit(db_audit_days=180)
    assert report["deleted"] == 2

    conn = sqlite3.connect(db)
    left = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
    conn.close()
    assert left == 1


def test_prune_db_audit_handles_missing_db(tmp_path, monkeypatch):
    mod = _load()
    monkeypatch.setattr(mod, "db_path", lambda: str(tmp_path / "none.db"))
    report = mod.prune_db_audit(db_audit_days=180)
    assert report["deleted"] == 0 and report["skipped"]


# ─────────────────────────── 编排 ───────────────────────────


def test_apply_retention_reports_policy_and_sections(tmp_path, monkeypatch):
    mod = _load()
    log = tmp_path / "logs" / "audit.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text(_audit_line(time.time()) + "\n", encoding="utf-8")

    monkeypatch.setattr(mod, "audit_log_path", lambda: str(log))
    monkeypatch.setattr(mod, "db_path", lambda: str(tmp_path / "none.db"))
    monkeypatch.setattr(mod, "sessions_dir", lambda: str(tmp_path / "sessions"))

    report = mod.apply_retention()
    for key in ("policy", "audit", "archives", "sessions", "database"):
        assert key in report
    assert report["policy"]["audit_days"] == mod.DEFAULTS["audit_days"]


def test_maybe_auto_retention_runs_once_per_month(tmp_path, monkeypatch):
    mod = _load()
    log = tmp_path / "logs" / "audit.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text(_audit_line(time.time()) + "\n", encoding="utf-8")
    monkeypatch.setattr(mod, "ROOT", str(tmp_path))
    monkeypatch.setattr(mod, "audit_log_path", lambda: str(log))
    monkeypatch.setattr(mod, "db_path", lambda: str(tmp_path / "none.db"))
    monkeypatch.setattr(mod, "sessions_dir", lambda: str(tmp_path / "sessions"))

    assert mod.maybe_auto_retention() is not None
    assert mod.maybe_auto_retention() is None, "同一个月不应重复执行"
