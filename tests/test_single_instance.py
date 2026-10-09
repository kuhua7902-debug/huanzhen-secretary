"""单实例保护（PID 锁）契约测试。

风险场景：用户"双击启动没反应 → 再双击一次"，而第一次其实还在启动中
（服务需 10~40 秒才监听端口），于是起了两个实例抢同一个 SQLite。
"""

from __future__ import annotations

import json
import os

import pytest

from core import single_instance as si


@pytest.fixture()
def lock(tmp_path, monkeypatch):
    path = tmp_path / "data" / ".huanzhen.lock"
    monkeypatch.setattr(si, "lock_path", lambda: str(path))
    monkeypatch.setattr(si, "_acquired", False)
    return path


def _write_lock(path, pid, port=8000):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pid": pid, "port": port, "started_at": 1700000000}), encoding="utf-8")


# ─────────────────────────── 基本行为 ───────────────────────────


def test_acquire_creates_lock(lock):
    ok, holder = si.acquire(port=8000)
    assert ok is True and holder is None
    assert lock.is_file()
    data = json.loads(lock.read_text(encoding="utf-8"))
    assert data["pid"] == os.getpid()
    assert data["port"] == 8000
    assert data["started_at"] > 0


def test_acquire_takes_over_stale_lock(lock, monkeypatch):
    """进程被强杀会留下僵尸锁：PID 已不存在时必须能接管，而不是把用户卡死。"""
    monkeypatch.setattr(si, "_pid_alive", lambda pid: False)
    _write_lock(lock, pid=999999)

    ok, holder = si.acquire(port=8000)
    assert ok is True and holder is None
    assert json.loads(lock.read_text(encoding="utf-8"))["pid"] == os.getpid()


def test_acquire_refuses_when_another_instance_alive(lock, monkeypatch):
    monkeypatch.setattr(si, "_pid_alive", lambda pid: True)
    _write_lock(lock, pid=424242, port=8000)

    ok, holder = si.acquire(port=8000)
    assert ok is False
    assert holder is not None and holder.pid == 424242
    # 不能覆盖别人的锁
    assert json.loads(lock.read_text(encoding="utf-8"))["pid"] == 424242


def test_force_start_env_bypasses_lock(lock, monkeypatch):
    monkeypatch.setattr(si, "_pid_alive", lambda pid: True)
    _write_lock(lock, pid=424242)
    monkeypatch.setenv("HUANZHEN_FORCE_START", "1")

    assert si.force_start() is True
    ok, holder = si.acquire(port=8000)
    assert ok is True and holder is None


def test_release_removes_own_lock(lock):
    si.acquire(port=8000)
    assert lock.is_file()
    si.release()
    assert not lock.is_file()


def test_release_does_not_remove_foreign_lock(lock, monkeypatch):
    """退出时只能删自己的锁，不能把后来者的锁删掉。"""
    monkeypatch.setattr(si, "_pid_alive", lambda pid: True)
    si.acquire(port=8000)
    # 模拟"锁已被另一个进程接管"
    _write_lock(lock, pid=424242)
    si.release()
    assert lock.is_file(), "不能删除不属于本进程的锁"


def test_same_process_reacquire_is_allowed(lock):
    ok1, _ = si.acquire(port=8000)
    ok2, holder = si.acquire(port=8000)
    assert ok1 and ok2 and holder is None


def test_read_holder_handles_corrupt_file(lock):
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("这不是 JSON", encoding="utf-8")
    assert si.read_holder() is None


def test_acquire_survives_unwritable_lock_path(tmp_path, monkeypatch):
    """锁写不进去（权限等）不应阻止服务启动——保护机制不能比它保护的东西更脆弱。"""
    monkeypatch.setattr(si, "lock_path", lambda: str(tmp_path / "no_such_dir" / "x" / ".lock"))
    monkeypatch.setattr(si, "_acquired", False)
    ok, holder = si.acquire(port=8000)
    assert ok is True and holder is None


# ─────────────────────────── 提示信息 ───────────────────────────


def test_conflict_message_offers_escape_hatch(lock, monkeypatch):
    monkeypatch.setattr(si, "_pid_alive", lambda pid: True)
    _write_lock(lock, pid=424242, port=8000)
    holder = si.check_existing()
    assert holder is not None

    text = si.conflict_message(holder)
    assert "424242" in text
    assert "停止服务.bat" in text
    assert "HUANZHEN_FORCE_START=1" in text, "必须给出绕过办法，避免误判后无法启动"
