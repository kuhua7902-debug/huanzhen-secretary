"""登录失败限流（防爆破）契约测试。

钉住的行为：
- 阈值内不锁定；达到阈值后锁定并返回 Retry-After
- IP 与用户名**双维度**计数（任一维度超限都拦截）
- 锁定时间阶梯上升且有上限
- 登录成功清零
- 过期条目被回收（长期运行内存有界）
"""

from __future__ import annotations

import time

from core.security.ratelimit import LoginRateLimiter, keys_for


def _limiter(**kw) -> LoginRateLimiter:
    kw.setdefault("window", 60.0)
    kw.setdefault("max_fails", 3)
    kw.setdefault("max_lock", 120.0)
    kw.setdefault("base_lock", 10.0)
    return LoginRateLimiter(**kw)


# ─────────────────────────── 基本行为 ───────────────────────────


def test_no_lock_before_threshold():
    lim = _limiter()
    keys = keys_for("10.0.0.1", "admin")
    assert lim.retry_after(keys) == 0
    assert lim.record_failure(keys) == 0      # 第 1 次
    assert lim.record_failure(keys) == 0      # 第 2 次（阈值 3，还未到）
    assert lim.retry_after(keys) == 0


def test_locks_at_threshold_and_reports_retry_after():
    lim = _limiter()
    keys = keys_for("10.0.0.1", "admin")
    for _ in range(2):
        lim.record_failure(keys)
    lock = lim.record_failure(keys)            # 第 3 次 → 触发锁定
    assert lock > 0
    assert lim.retry_after(keys) > 0


def test_lock_backoff_grows_and_is_capped():
    lim = _limiter(base_lock=10.0, max_lock=25.0, max_fails=1)
    keys = keys_for("10.0.0.1", "admin")
    seen = [lim.record_failure(keys) for _ in range(6)]
    assert seen[0] == 10                       # 第 1 次失败即锁 10s
    assert seen[1] == 20                       # 翻倍
    assert seen[2] == 25                       # 触顶（10*4=40 → cap 25）
    assert max(seen) <= 25
    assert seen == sorted(seen)                # 单调不减


def test_success_resets_counter():
    lim = _limiter()
    keys = keys_for("10.0.0.1", "admin")
    for _ in range(2):
        lim.record_failure(keys)
    lim.record_success(keys)
    # 清零后需要重新累计到阈值才会锁定
    assert lim.record_failure(keys) == 0
    assert lim.retry_after(keys) == 0


# ─────────────────────────── 双维度 ───────────────────────────


def test_ip_dimension_blocks_across_usernames():
    """风险：只按用户名限流时，单 IP 撞库（换账号名）可以绕过。"""
    lim = _limiter()
    for name in ("a", "b", "c"):
        lim.record_failure(keys_for("10.0.0.1", name))
    # 换一个新用户名，但同一 IP 已超阈值 → 仍应被拦截
    assert lim.retry_after(keys_for("10.0.0.1", "brand_new")) > 0


def test_username_dimension_blocks_across_ips():
    """风险：只按 IP 限流时，换 IP 打同一账号可以绕过。"""
    lim = _limiter()
    for ip in ("10.0.0.1", "10.0.0.2", "10.0.0.3"):
        lim.record_failure(keys_for(ip, "admin"))
    assert lim.retry_after(keys_for("10.0.0.99", "admin")) > 0


def test_keys_for_lowercases_username_and_skips_blank():
    assert keys_for("1.2.3.4", "Admin") == ["ip:1.2.3.4", "user:admin"]
    assert keys_for("", "admin") == ["user:admin"]
    assert keys_for("1.2.3.4", "   ") == ["ip:1.2.3.4"]
    assert keys_for("", "") == []


# ─────────────────────────── 内存有界 ───────────────────────────


def test_expired_entries_are_pruned():
    # 注意：window 有 1.0 秒的安全下限（避免配置成 0 导致统计失效），
    # 因此这里用 1.0 秒窗口 + 略长于它的等待来验证回收。
    lim = _limiter(window=1.0, max_fails=2)
    keys = keys_for("10.0.0.1", "admin")
    lim.record_failure(keys)
    assert lim.stats()["tracked_keys"] == 2      # IP + 用户名 两个维度
    time.sleep(1.2)
    lim.retry_after(keys)                        # 任意一次访问都会触发回收
    assert lim.stats()["tracked_keys"] == 0


def test_stats_reports_locked_keys():
    lim = _limiter(max_fails=1)
    lim.record_failure(keys_for("10.0.0.1", "admin"))
    stats = lim.stats()
    assert stats["tracked_keys"] >= 1
    assert stats["locked_keys"] >= 1


# ─────────────────────────── 接线检查 ───────────────────────────


def test_login_endpoint_uses_rate_limiter():
    """风险：限流模块写好了但登录接口没接上——等于没做。"""
    import inspect

    from core import routes_auth

    source = inspect.getsource(routes_auth.login)
    assert "retry_after" in source, "登录接口未检查锁定期"
    assert "record_failure" in source, "登录接口未记录失败次数"
    assert "record_success" in source, "登录成功未清零计数"


def test_login_returns_429_when_locked():
    """集成：连续失败后应返回 429 且带 Retry-After 头。"""
    from fastapi.testclient import TestClient

    lim = _limiter(max_fails=2)
    import core.security.ratelimit as rl

    original = rl.login_limiter
    rl.login_limiter = lim
    try:
        import main

        # 覆盖模块内引用的单例绑定
        routes_auth = __import__("core.routes_auth", fromlist=["login"])
        routes_auth.login_limiter = lim

        client = TestClient(main.app)
        payload = {"username": "definitely_not_exists", "password": "wrong-password"}
        first = client.post("/api/auth/login", json=payload)
        assert first.status_code == 401
        second = client.post("/api/auth/login", json=payload)
        assert second.status_code == 429
        assert "Retry-After" in second.headers
    finally:
        rl.login_limiter = original
