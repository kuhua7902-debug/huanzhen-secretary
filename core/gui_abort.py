"""GUI 操作全局紧急停止标志。

用户点"停止"时，除了设置 cancel_event，还要设置这个标志。
所有 GUI 工具在执行前/执行中都会检查它，实现真正的即时停止。

设计动机：
- runner.py 主循环只在每轮迭代开头检查 cancel_event，但当前正在跑的
  GUI 工具（pyautogui.click / typewrite 等）不会中途响应 cancel。
- 更糟的是工具失败重试循环（runner.py 第857行）也不检查 cancel，会一直重试。
- 本模块提供一个进程内的全局 Event，让 GUI 工具在每个操作前能立即查询，
  配合 runner.py 的 cancel_event 形成多层刹车。

线程安全：threading.Event 本身线程安全，可被任意线程 set/is_set。
"""
from __future__ import annotations

import threading
import time

# 全局停止标志（进程内）
_abort_flag = threading.Event()

# 停止时间戳（用于日志、恢复、过期判断）
_abort_time: float = 0.0

# 停止原因（可选，用于日志和返回给 agent）
_abort_reason: str = ""


def trigger_abort(reason: str = "用户点击停止") -> None:
    """触发全局 GUI 紧急停止。

    用户点"停止"按钮、或检测到危险情况需要立即中止所有 GUI 操作时调用。
    设置后，所有 GUI 工具（click_position / type_text / drag_mouse 等）
    在下一个操作前都会检查到并立即返回。

    参数：
        reason: 停止原因，会记录到日志并返回给 agent
    """
    global _abort_time, _abort_reason
    _abort_time = time.time()
    _abort_reason = reason
    _abort_flag.set()


def clear_abort() -> None:
    """清除停止标志。

    在开始新一轮 agent 对话前调用，确保上一轮的停止状态不会影响新对话。
    """
    _abort_flag.clear()


def reset_if_stale(max_age_seconds: float = 300.0) -> bool:
    """【陈旧停止标志自愈】只清除「已经过期」的停止标志，未过期则原样保留。

    背景（为什么需要这个函数）：
        本模块的停止标志是进程级单例（全局 Event），目前只有"开始新一轮
        流式对话"时会调用 clear_abort()。如果某次停止之后没有新对话来清理
        （例如用户点了停止就直接关掉对话、语音说了"停止"后不再说话、
        或任务线程异常退出），标志会永远保持 True，于是之后**所有** GUI 工具
        都只会返回"操作已中止"，表现为「GUI 自动化功能永久失效」。
        调用方可以在合适的时机（如每个新任务开始前、或工具执行前）调用
        本函数做一次"过期即清除"的自愈，而不会误清掉用户刚刚发出的停止指令。

    行为：
        - 未触发停止标志          → 返回 False（无操作）
        - 已触发且距今 <= max_age → 返回 False（保留标志，尊重用户刚刚的停止指令）
        - 已触发且距今 >  max_age → 清除标志，返回 True
        触发时间取 trigger_abort() 记录的 _abort_time；若标志已设置但时间戳
        异常（为 0），视为陈旧并清除。

    注意：与 clear_abort() 保持一致，本函数**只清标志**，
    _abort_time / _abort_reason 会保留（供 get_abort_info() 查日志），
    下次 trigger_abort() 会覆盖它们。

    参数：
        max_age_seconds: 停止标志的最长有效时长（秒），默认 300 秒（5 分钟）。
                         传 0 表示清除一切非本瞬间设置的标志（激进模式）。

    返回：True 表示本次调用清除了过期标志，False 表示未清除。

    兼容性：本函数为新增 API，不改变 trigger_abort / clear_abort /
    is_aborted / get_abort_info / wait_for_abort_or_timeout 的任何既有行为。
    """
    if not _abort_flag.is_set():
        return False

    try:
        max_age = float(max_age_seconds)
    except (TypeError, ValueError):
        max_age = 300.0
    if max_age < 0:
        max_age = 0.0

    # 时间戳为 0（异常情况）时视为无限陈旧
    age = (time.time() - _abort_time) if _abort_time else float("inf")
    if age <= max_age:
        return False

    _abort_flag.clear()
    return True


def is_aborted() -> bool:
    """检查是否已触发紧急停止。

    所有 GUI 工具在每个操作前调用此函数。返回 True 时，工具应立即返回
    "操作已中止" 消息，不执行任何实际 GUI 操作。
    """
    return _abort_flag.is_set()


def get_abort_info() -> dict:
    """获取停止详情（用于日志和调试）。

    返回：
        {"aborted": bool, "time": float, "reason": str}
    """
    return {
        "aborted": _abort_flag.is_set(),
        "time": _abort_time,
        "reason": _abort_reason,
    }


def wait_for_abort_or_timeout(timeout: float) -> bool:
    """等待停止信号或超时。

    用于长任务（如 run_code）想在执行过程中周期性检查停止信号的场景。
    返回 True 表示被停止，False 表示超时退出。

    参数：
        timeout: 最长等待秒数
    """
    return _abort_flag.wait(timeout=timeout)
