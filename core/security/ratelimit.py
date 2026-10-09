"""登录失败限流（防暴力破解）。

## 为什么需要

`/api/auth/login` 此前**没有任何失败计数**：同一局域网内任何人都可以无限次
尝试密码。幻帧本身就是「内网多人共用」的部署形态，一旦有人扫到 8000 端口，
管理员账号就可以被离线慢速爆破。

## 策略：滑动窗口 + 双维度 + 阶梯锁定

- **双维度计数**：同时按「来源 IP」和「用户名」计数，任一维度超阈值即拦截。
  - 只按 IP 限制 → 漏掉「换 IP 打同一账号」
  - 只按用户名限制 → 漏掉「单 IP 撞库多账号」
- **阶梯锁定**：窗口内失败达到阈值后开始锁定，锁定时间随失败次数翻倍上升，
  封顶 `MAX_LOCK`。既能挡住自动化爆破，又不会让输错几次的正常用户被永久拒之门外。
- **成功即清零**：正常登录不受影响。
- **内存有界**：过期条目在每次访问时顺带回收，长期运行不会无限增长。

阈值可用环境变量调整（一般无需改）：

    HUANZHEN_LOGIN_WINDOW     统计窗口秒数，默认 300
    HUANZHEN_LOGIN_MAX_FAILS  触发锁定的失败次数，默认 5
    HUANZHEN_LOGIN_MAX_LOCK   单次锁定时长上限秒数，默认 900
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field

WINDOW = float(os.environ.get("HUANZHEN_LOGIN_WINDOW", "300"))
MAX_FAILS = int(os.environ.get("HUANZHEN_LOGIN_MAX_FAILS", "5"))
MAX_LOCK = float(os.environ.get("HUANZHEN_LOGIN_MAX_LOCK", "900"))
BASE_LOCK = 30.0


@dataclass
class _Entry:
    failures: list[float] = field(default_factory=list)
    locked_until: float = 0.0


class LoginRateLimiter:
    """线程安全的登录失败限流器（FastAPI 同步端点跑在线程池里，必须加锁）。"""

    def __init__(
        self,
        window: float = WINDOW,
        max_fails: int = MAX_FAILS,
        max_lock: float = MAX_LOCK,
        base_lock: float = BASE_LOCK,
    ) -> None:
        self.window = max(1.0, float(window))
        self.max_fails = max(1, int(max_fails))
        self.max_lock = max(1.0, float(max_lock))
        self.base_lock = max(1.0, float(base_lock))
        self._lock = threading.Lock()
        self._entries: dict[str, _Entry] = {}

    # ── 内部 ──

    def _prune(self, now: float) -> None:
        """回收过期条目与过期失败记录（须在持锁状态下调用）。"""
        stale: list[str] = []
        for key, entry in self._entries.items():
            entry.failures = [t for t in entry.failures if now - t < self.window]
            if not entry.failures and entry.locked_until <= now:
                stale.append(key)
        for key in stale:
            self._entries.pop(key, None)

    # ── 对外 ──

    def retry_after(self, keys: list[str]) -> int:
        """还需等待多少秒才能再次尝试（0 = 可以尝试）。"""
        now = time.time()
        with self._lock:
            self._prune(now)
            remaining = 0.0
            for key in keys:
                entry = self._entries.get(key)
                if entry and entry.locked_until > now:
                    remaining = max(remaining, entry.locked_until - now)
            return int(remaining + 0.999)

    def record_failure(self, keys: list[str]) -> int:
        """记录一次失败，返回本次生效的锁定秒数（0 = 尚未进入锁定）。"""
        now = time.time()
        with self._lock:
            self._prune(now)
            lock_for = 0.0
            for key in keys:
                entry = self._entries.setdefault(key, _Entry())
                entry.failures.append(now)
                count = len(entry.failures)
                if count >= self.max_fails:
                    # 第 max_fails 次锁 base，之后每多失败一次翻倍，封顶 max_lock
                    extra = count - self.max_fails
                    lock_for = max(lock_for, min(self.max_lock, self.base_lock * (2 ** extra)))
                    entry.locked_until = max(entry.locked_until, now + lock_for)
            return int(lock_for + 0.999)

    def record_success(self, keys: list[str]) -> None:
        """登录成功：清零相关计数。"""
        with self._lock:
            for key in keys:
                self._entries.pop(key, None)

    def reset(self) -> None:
        """清空全部状态（测试用）。"""
        with self._lock:
            self._entries.clear()

    def stats(self) -> dict:
        """简单观测信息。"""
        now = time.time()
        with self._lock:
            self._prune(now)
            locked = sum(1 for e in self._entries.values() if e.locked_until > now)
            return {"tracked_keys": len(self._entries), "locked_keys": locked}


def keys_for(ip: str, username: str) -> list[str]:
    """构造用于计数的键：IP 维度 + 用户名维度。"""
    keys: list[str] = []
    if ip:
        keys.append(f"ip:{ip}")
    name = (username or "").strip().lower()
    if name:
        keys.append(f"user:{name}")
    return keys


# 全局单例：登录接口直接使用
login_limiter = LoginRateLimiter()
