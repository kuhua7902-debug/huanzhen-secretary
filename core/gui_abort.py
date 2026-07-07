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
