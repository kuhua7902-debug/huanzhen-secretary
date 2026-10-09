"""单实例保护（PID 锁）。

## 为什么需要

幻帧是常驻后台服务，下面这两种情况很常见：

1. 双击启动脚本没反应，于是又双击一次——**而第一次其实正在启动中**（服务要 10~40 秒
   才监听端口）。启动脚本的端口检测此时还是"未监听"，于是第二个实例也被拉起来。
2. 用户改了端口配置，端口检测查的是旧端口，同样拦不住。

两个实例抢同一个 SQLite 与端口：一个静默失败，另一个状态异常，排查成本很高。

## 做法

在 `data/.huanzhen.lock` 写入 `{"pid":..., "port":..., "started_at":...}`：

- 启动时若锁存在且**该 PID 仍存活** → 判定已有实例，拒绝启动并给出可操作提示
- 若 PID 已不存在（进程被强杀留下的僵尸锁）→ 视为陈旧锁，直接接管
- 退出时删除锁（`atexit` + 显式 `release()` 双保险）
- `HUANZHEN_FORCE_START=1` 可强制忽略（给"确实需要并存"的例外留出口）

> PID 复用理论上会造成误判，因此提示里明确给出"确认没有实例后可强制启动"的出路，
> 而不是把用户卡死——这类保护机制最忌讳的就是"误判且无法绕过"。
"""

from __future__ import annotations

import atexit
import json
import os
import sys
import time
from dataclasses import dataclass

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCK_NAME = ".huanzhen.lock"

_acquired = False


@dataclass
class Holder:
    pid: int
    port: int = 0
    started_at: float = 0.0

    def describe(self) -> str:
        when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.started_at)) if self.started_at else "未知时间"
        port = f"端口 {self.port}" if self.port else "端口未知"
        return f"PID {self.pid}（{port}，启动于 {when}）"


def lock_path() -> str:
    return os.path.join(_PROJECT_ROOT, "data", LOCK_NAME)


def force_start() -> bool:
    """是否允许忽略锁强制启动。"""
    return str(os.environ.get("HUANZHEN_FORCE_START", "")).strip().lower() in ("1", "true", "yes")


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        import psutil  # noqa: PLC0415

        return bool(psutil.pid_exists(pid))
    except Exception:
        pass
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True          # 进程存在但无权限发信号
    except OSError:
        return False
    except Exception:
        return False


def read_holder() -> Holder | None:
    """读取锁文件内容；文件不存在或损坏返回 None。"""
    path = lock_path()
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
        return Holder(
            pid=int(data.get("pid", 0)),
            port=int(data.get("port", 0)),
            started_at=float(data.get("started_at", 0.0)),
        )
    except Exception:
        return None


def check_existing() -> Holder | None:
    """返回仍在运行的实例；没有则返回 None（含"僵尸锁"情况）。"""
    holder = read_holder()
    if holder is None:
        return None
    if holder.pid == os.getpid():
        return None                       # 锁是自己写的（同进程重复 acquire）
    if _pid_alive(holder.pid):
        return holder
    return None


def acquire(port: int = 0) -> tuple[bool, Holder | None]:
    """尝试获取单实例锁。

    返回 `(是否成功, 已存在的持有者)`：
    - 成功 → `(True, None)`
    - 已有实例在运行 → `(False, holder)`
    """
    global _acquired

    existing = check_existing()
    if existing is not None and not force_start():
        return False, existing

    path = lock_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                {"pid": os.getpid(), "port": int(port or 0), "started_at": time.time()},
                f,
                ensure_ascii=False,
            )
        _acquired = True
        atexit.register(release)
        return True, None
    except Exception:
        # 锁文件写不了（权限等）不应阻止服务启动——保护机制不能比它保护的东西更脆弱
        return True, None


def release() -> None:
    """释放锁；只删属于本进程的锁，避免误删后来者的锁。"""
    global _acquired
    if not _acquired:
        return
    path = lock_path()
    try:
        holder = read_holder()
        if holder is not None and holder.pid == os.getpid() and os.path.isfile(path):
            os.remove(path)
    except Exception:
        pass
    _acquired = False


def conflict_message(holder: Holder | None) -> str:
    """生成给用户看的冲突提示。"""
    who = holder.describe() if holder else "一个已存在的实例"
    lines = [
        "幻帧似乎已经在运行了",
        f"  已运行的实例：{who}",
        "",
        "如果你确认它确实在运行：",
        "  - 直接打开 http://127.0.0.1:8000/ 使用即可；",
        "  - 想重启：先双击「停止服务.bat」，再启动本程序。",
        "",
        "如果你确认没有实例在运行（例如上次被强制结束过）：",
        "  - 删除 data\\.huanzhen.lock 后重试；",
        "  - 或设置环境变量 HUANZHEN_FORCE_START=1 强制启动。",
    ]
    return "\n".join(lines)
