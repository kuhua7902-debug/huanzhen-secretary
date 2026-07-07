"""工作流编排工具集 —— 录制、回放、保存、列出、删除、编辑（阶段三）

让 agent 能把"操作步骤序列"变成可持久化、可复用、可参数化的一等公民。

核心思想：
- 录制：在现有桌面工具函数里埋轻量钩子 _notify_workflow()，零侵入
- 回放：直接调用工具函数（绕过 LLM 推理），优先用缓存坐标，失败回退视觉定位
- 持久化：保存为 data/workflows/{name}.workflow.json
- 编辑：支持追加/插入/删除/替换单个步骤，不必为了改一步就整段重录
- 删除：带二次确认 + .trash 备份，安全清理

6 个对外工具：
  1. record_workflow(name, description, action) — 开始/停止/取消录制
  2. replay_workflow(name, variables, dry_run)  — 回放工作流
  3. save_workflow(name, description, steps)     — 手动保存工作流
  4. list_workflows()                            — 列出已保存的工作流
  5. delete_workflow(name, confirm)              — 删除工作流（二次确认）
  6. edit_workflow(name, op, position, step)     — 编辑步骤（append/insert/delete/replace）

支持的回放 action：
  open_application / run_command / click_position / click_element /
  type_text / press_key / wait_for_element / wait
"""

import json
import os
import re
import time
import threading
from datetime import datetime
from pathlib import Path

from core.tools import register_tool
from core.logger import setup_logger

logger = setup_logger("keji.workflow")

# ═══════════════════════════════════════════════════════════════
# 持久化目录
# ═══════════════════════════════════════════════════════════════

_WORKFLOW_DIR = Path(__file__).resolve().parent.parent / "data" / "workflows"
_WORKFLOW_DIR.mkdir(parents=True, exist_ok=True)

_WORKFLOW_SUFFIX = ".workflow.json"

# 名称合法校验（只允许字母/数字/下划线/横线）
_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


def _workflow_path(name: str) -> Path:
    return _WORKFLOW_DIR / f"{name}{_WORKFLOW_SUFFIX}"


def _validate_name(name: str) -> str | None:
    """返回 None 表示合法，否则返回错误说明。"""
    if not name or not name.strip():
        return "工作流名称不能为空"
    name = name.strip()
    if not _NAME_RE.match(name):
        return "工作流名称只能包含字母、数字、下划线和横线"
    if len(name) > 64:
        return "工作流名称过长（最多64字符）"
    return None


# ═══════════════════════════════════════════════════════════════
# 录制上下文（threading.local，避免并发问题）
# ═══════════════════════════════════════════════════════════════

_recording_ctx = threading.local()


def _get_ctx():
    return getattr(_recording_ctx, "active", None)


def _set_ctx(ctx):
    _recording_ctx.active = ctx


class _RecordingContext:
    """单次录制的上下文。"""

    def __init__(self, name: str, description: str = ""):
        self.name = name
        self.description = description
        self.steps: list[dict] = []
        self.started_at = time.time()
        self.active = True

    def add_step(self, action: str, params: dict,
                 cached_coordinates: dict | None = None,
                 result: str = ""):
        if not self.active:
            return
        step = {
            "step_id": len(self.steps) + 1,
            "action": action,
            "params": _safe_params(params),
        }
        if cached_coordinates:
            step["cached_coordinates"] = cached_coordinates
        self.steps.append(step)
        logger.info(
            "record_workflow 录制步骤 #%d: %s params=%s",
            step["step_id"], action, step["params"],
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "version": "1.0",
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "variables": _extract_variables(self.steps),
            "steps": self.steps,
        }


def _safe_params(params: dict) -> dict:
    """过滤掉不能序列化或内部的参数。"""
    safe = {}
    for k, v in params.items():
        if k in ("require_confirm",):
            continue
        if isinstance(v, (str, int, float, bool)) or v is None:
            safe[k] = v
        else:
            try:
                json.dumps(v)
                safe[k] = v
            except Exception:
                safe[k] = str(v)[:200]
    return safe


# ${var} 占位符正则
_VAR_PATTERN = re.compile(r"\$\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def _extract_variables(steps: list[dict]) -> list[str]:
    """扫描所有步骤的 params，提取用到的 ${var} 变量名。"""
    found: set[str] = set()
    for step in steps:
        for v in step.get("params", {}).values():
            if isinstance(v, str):
                for m in _VAR_PATTERN.finditer(v):
                    found.add(m.group(1))
    return sorted(found)


def _substitute_variables(value, variables: dict):
    """递归替换 ${var} 占位符。"""
    if isinstance(value, str):
        def _repl(m):
            key = m.group(1)
            if key in variables:
                return str(variables[key])
            return m.group(0)  # 未提供则保留占位符
        return _VAR_PATTERN.sub(_repl, value)
    if isinstance(value, dict):
        return {k: _substitute_variables(v, variables) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute_variables(v, variables) for v in value]
    return value


def _builtin_wait(seconds: float = 1.0) -> str:
    """内置 wait 步骤实现：纯等待，不依赖任何外部工具。
    用于工作流中"等一下再继续"的场景，例如等弹窗动画、等界面渲染。
    """
    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        seconds = 1.0
    if seconds < 0:
        seconds = 0
    if seconds > 300:
        seconds = 300  # 上限保护，避免误填导致长时间卡住
    time.sleep(seconds)
    return f"已等待 {seconds:.2f}s"


# ═══════════════════════════════════════════════════════════════
# 钩子函数 —— 现有工具函数在执行后调用此函数（零侵入）
# ═══════════════════════════════════════════════════════════════

def _notify_workflow(action: str, params: dict,
                     cached_coordinates: dict | None = None,
                     result: str = ""):
    """轻量钩子：如果正在录制，则记录一步操作。

    设计要点：
    - try/except 包裹，钩子失败绝不影响主功能
    - 不在录制状态时立即 return，几乎零开销
    - 只在录制线程内有效
    """
    try:
        ctx = _get_ctx()
        if ctx is None or not ctx.active:
            return
        # 嵌套抑制：click_element 内部调用 click_position 时，只记录 click_element 一次
        if getattr(_recording_ctx, "suppress_nested", False):
            return
        ctx.add_step(action, params, cached_coordinates, result)
    except Exception as e:
        logger.warning("record_workflow 钩子异常(忽略): %s", e)


class _SuppressNested:
    """上下文管理器：在 with 块内，钩子被抑制（用于 click_element 调 click_position 等嵌套场景）。"""

    def __enter__(self):
        _recording_ctx.suppress_nested = True
        return self

    def __exit__(self, *exc):
        _recording_ctx.suppress_nested = False
        return False


# ═══════════════════════════════════════════════════════════════
# 工具1：record_workflow — 录制模式
# ═══════════════════════════════════════════════════════════════

@register_tool(
    name="record_workflow",
    description=(
        "⭐录制模式：开始/停止记录后续桌面操作为可复用的工作流。"
        "开启后，你执行的 open_application/click_element/type_text/press_key/"
        "click_position/wait_for_element 等操作会自动记录。"
        "录制结束后用 record_workflow(action='stop') 停止并自动保存到 data/workflows/。"
        "示例：record_workflow('calc_test', '计算器测试', action='start') 开始录制；"
        "record_workflow('calc_test', action='stop') 停止录制并返回步骤摘要；"
        "record_workflow(action='cancel') 取消录制不保存。"
    ),
    parameters={
        "name": {
            "type": "string",
            "description": "工作流名称，英文/数字/下划线（start/stop 必填；cancel 可省略）",
        },
        "description": {
            "type": "string",
            "description": "工作流描述（可选，start 时填写）",
        },
        "action": {
            "type": "string",
            "description": "start=开始录制(默认) / stop=停止录制并保存 / cancel=取消录制不保存",
        },
    },
    category="workflow",
    timeout=10,
)
def record_workflow(name: str = "", description: str = "", action: str = "start") -> str:
    action = (action or "start").strip().lower()

    # ── cancel ──
    if action == "cancel":
        ctx = _get_ctx()
        if ctx is None:
            return "当前没有正在进行的录制"
        ctx.active = False
        _set_ctx(None)
        return f"已取消录制「{ctx.name}」（{len(ctx.steps)} 步未保存）"

    # ── stop ──
    if action == "stop":
        ctx = _get_ctx()
        if ctx is None:
            return "错误：当前没有正在进行的录制"
        ctx.active = False
        _set_ctx(None)

        # 校验名称
        if not name:
            name = ctx.name
        err = _validate_name(name)
        if err:
            return f"错误：{err}（录制内容仍保留在内存，请用合法名称重新 stop）"

        # 保存
        wf = ctx.to_dict()
        wf["name"] = name
        path = _workflow_path(name)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(wf, f, ensure_ascii=False, indent=2)
        except Exception as e:
            return f"错误：保存工作流失败：{e}"

        logger.info("record_workflow 保存：%s (%d 步)", path, len(wf["steps"]))
        return (
            f"✅ 录制完成并已保存\n"
            f"名称：{name}\n"
            f"描述：{ctx.description or '(无)'}\n"
            f"步骤数：{len(wf['steps'])}\n"
            f"变量：{wf['variables'] or '(无)'}\n"
            f"耗时：{time.time() - ctx.started_at:.1f}s\n"
            f"保存位置：{path}\n\n"
            f"步骤预览：\n" + _preview_steps(wf["steps"])
        )

    # ── start ──
    if action == "start":
        err = _validate_name(name)
        if err:
            return f"错误：{err}"

        # 已有录制 → 先停止旧的
        old = _get_ctx()
        if old is not None and old.active:
            old.active = False
            logger.info("record_workflow 自动停止旧录制：%s", old.name)

        ctx = _RecordingContext(name=name.strip(), description=description or "")
        _set_ctx(ctx)
        logger.info("record_workflow 开始录制：%s", ctx.name)
        return (
            f"✅ 已开始录制工作流「{name}」\n"
            f"描述：{description or '(无)'}\n"
            f"接下来你执行的桌面操作会自动记录。\n"
            f"完成后调用 record_workflow('{name}', action='stop') 停止并保存。"
        )

    return f"错误：未知 action「{action}」，应为 start / stop / cancel"


def _preview_steps(steps: list[dict]) -> str:
    lines = []
    for s in steps:
        action = s.get("action", "?")
        params = s.get("params", {})
        # 取一个最关键的参数作为预览
        key_field = (
            params.get("target_description")
            or params.get("text")
            or params.get("key")
            or params.get("app_name")
            or params.get("command")
            or ""
        )
        coord = s.get("cached_coordinates")
        coord_str = f" @({coord.get('x')},{coord.get('y')})" if coord else ""
        lines.append(f"  #{s.get('step_id')}. {action}: {key_field}{coord_str}")
    return "\n".join(lines) if lines else "  (无步骤)"


# ═══════════════════════════════════════════════════════════════
# 工具2：replay_workflow — 回放模式
# ═══════════════════════════════════════════════════════════════

@register_tool(
    name="replay_workflow",
    description=(
        "⭐回放模式：按保存的工作流步骤序列自动执行，支持变量替换。"
        "比逐步对话快很多（跳过LLM推理），且可复用。"
        "回放时优先用录制时缓存的坐标，验证失败后自动回退到视觉定位（策略B）。"
        "示例：replay_workflow('calc_test') 直接回放；"
        "replay_workflow('save_doc', variables={'filename': '报告.docx', 'content': '你好'}) 带变量回放；"
        "replay_workflow('calc_test', dry_run=true) 仅预演不执行。"
    ),
    parameters={
        "name": {
            "type": "string",
            "description": "要回放的工作流名称",
        },
        "variables": {
            "type": "object",
            "description": "变量字典，替换步骤中的 ${var} 占位符（可选）",
        },
        "dry_run": {
            "type": "boolean",
            "description": "true=只打印步骤不执行(预演)，false=实际执行(默认)",
        },
    },
    category="workflow",
    timeout=300,
)
def replay_workflow(name: str, variables: dict | None = None, dry_run: bool = False) -> str:
    err = _validate_name(name)
    if err:
        return f"错误：{err}"

    path = _workflow_path(name)
    if not path.exists():
        return f"错误：工作流「{name}」不存在（路径 {path}）。可用 list_workflows() 查看。"

    try:
        with open(path, "r", encoding="utf-8") as f:
            wf = json.load(f)
    except Exception as e:
        return f"错误：读取工作流失败：{e}"

    steps = wf.get("steps", [])
    if not steps:
        return f"工作流「{name}」没有步骤可回放"

    variables = variables or {}

    # 检查变量是否齐全
    required = set(wf.get("variables", []))
    provided = set(variables.keys())
    missing = required - provided
    if missing:
        return (
            f"错误：缺少变量 {sorted(missing)}\n"
            f"工作流需要：{sorted(required)}\n"
            f"已提供：{sorted(provided)}"
        )

    # dry_run 预演
    if dry_run:
        lines = [f"🔍 预演工作流「{name}」（{len(steps)} 步，不实际执行）"]
        for s in steps:
            params = _substitute_variables(s.get("params", {}), variables)
            action = s.get("action")
            coord = s.get("cached_coordinates")
            coord_str = f" 缓存坐标=({coord.get('x')},{coord.get('y')})" if coord else ""
            lines.append(f"  #{s.get('step_id')}. {action} {params}{coord_str}")
        return "\n".join(lines)

    # 实际回放
    logger.info("replay_workflow 开始回放：%s (%d 步)", name, len(steps))
    start_time = time.time()
    report_steps = []

    # 延迟导入，避免循环依赖
    from core import gui_tools, desktop_tools

    # 工具函数映射
    action_map = {
        "open_application": desktop_tools.open_application,
        "run_command": desktop_tools.run_command,
        "click_position": gui_tools.click_position,
        "click_element": gui_tools.click_element,
        "type_text": gui_tools.type_text,
        "press_key": gui_tools.press_key,
        "wait_for_element": gui_tools.wait_for_element,
        # 内置纯等待步骤（不依赖外部模块）
        "wait": lambda seconds=1, **_: _builtin_wait(seconds),
    }

    for step in steps:
        step_id = step.get("step_id", 0)
        action = step.get("action", "")
        params = _substitute_variables(step.get("params", {}), variables)
        cached = step.get("cached_coordinates")
        on_failure = step.get("on_failure", "abort")
        max_retries = int(step.get("max_retries", 0))
        verify = step.get("verify")

        step_start = time.time()
        step_report = {
            "step_id": step_id,
            "action": action,
            "status": "pending",
            "time": 0.0,
            "retries": 0,
            "used_cache": False,
            "note": "",
        }

        func = action_map.get(action)
        if func is None:
            step_report["status"] = "skipped"
            step_report["note"] = f"未知 action: {action}"
            report_steps.append(step_report)
            if on_failure == "abort":
                return _build_report(name, len(steps), report_steps, start_time, aborted=True)
            continue

        # 策略B：对于 click_element 优先用缓存坐标
        attempts = 0
        max_attempts = 1 + max_retries
        # ⭐ 缓存坐标回退需要额外1次机会（视觉重新定位）
        # 否则 max_retries=0 时，缓存未命中 continue 后 attempts=2 已超过 max_attempts=1，
        # 视觉回退代码永远执行不到
        if action == "click_element" and cached:
            max_attempts = max(max_attempts, 2)
        step_success = False
        last_err = ""

        while attempts < max_attempts:
            attempts += 1
            try:
                # 策略B 核心：第一次尝试用缓存坐标（如果有）
                if action == "click_element" and cached and attempts == 1:
                    step_report["used_cache"] = True
                    step_report["note"] = f"使用缓存坐标({cached.get('x')},{cached.get('y')})"
                    click_params = {
                        "x": int(cached["x"]),
                        "y": int(cached["y"]),
                        "button": params.get("button", "left"),
                        "clicks": params.get("clicks", 1),
                    }
                    # 点击前截图：用稳定截图，避免抓到 UI 渲染中间帧
                    # （例如计算器启动时显示区空白→0 的过渡帧）
                    before_shot = None
                    try:
                        before_shot = _stable_screenshot(timeout=2.0, interval=0.3)
                    except Exception as _e:
                        logger.warning("点击前稳定截图失败(%s)，跳过差异验证", _e)
                    result = gui_tools.click_position(**click_params)
                    time.sleep(0.8)  # 等待UI响应（原0.3s对刚启动的应用太短）
                    # 截图差异验证（即使无 verify 字段也强制做，防止点到空白处）
                    diff_pct = None
                    if before_shot is not None:
                        try:
                            after_shot = gui_tools.screenshot_screen()
                            diff_pct = _calc_screenshot_diff(before_shot, after_shot)
                            # 若差异偏小，再等0.5s重试一次截图，给慢UI留时间
                            if diff_pct < 0.005:
                                time.sleep(0.5)
                                after_shot2 = gui_tools.screenshot_screen()
                                diff_pct2 = _calc_screenshot_diff(before_shot, after_shot2)
                                if diff_pct2 > diff_pct:
                                    diff_pct = diff_pct2
                        except Exception as _e:
                            logger.warning("点击后截图/差异计算失败(%s)", _e)
                    # 阈值从 0.5% 降到 0.1%，适配深色主题小变化（如 0→7）
                    if diff_pct is not None and diff_pct < 0.001:
                        # 像素差异 < 0.1% → 缓存坐标真没点中，回退视觉定位
                        last_err = f"缓存坐标点击未生效(像素差异 {diff_pct*100:.2f}%)"
                        step_report["note"] = f"缓存坐标未命中(差异{diff_pct*100:.2f}%)，回退视觉定位"
                        step_report["used_cache"] = False
                        continue
                    # 差异足够大（或无法计算差异）→ 缓存点击生效
                    if diff_pct is not None:
                        if diff_pct < 0.005:
                            step_report["note"] = f"缓存坐标疑似命中(像素差异 {diff_pct*100:.2f}%)，继续执行"
                        else:
                            step_report["note"] = f"缓存坐标命中(像素差异 {diff_pct*100:.2f}%)"
                    # 如果还有 verify，继续校验
                    if verify:
                        ok, msg = _verify_step(verify, variables)
                        if ok:
                            step_success = True
                            break
                        else:
                            last_err = f"缓存坐标验证失败: {msg}"
                            step_report["note"] = f"缓存坐标验证失败，回退视觉定位"
                            step_report["used_cache"] = False
                            continue
                    else:
                        step_success = True
                        break
                else:
                    # 常规执行（包括回退后的 click_element）
                    if action == "click_element" and cached and attempts > 1:
                        step_report["note"] = f"第{attempts}次尝试：视觉重新定位"
                    # 清理掉 cached_coordinates 不该传给函数的参数
                    call_params = {k: v for k, v in params.items()
                                   if k not in ("cached_coordinates",)}
                    result = func(**call_params)

                    # 判断是否成功
                    if _is_step_success(result, action):
                        # 如有 verify 再验一次
                        if verify:
                            ok, msg = _verify_step(verify, variables)
                            if ok:
                                step_success = True
                                break
                            else:
                                last_err = f"验证失败: {msg}"
                                if attempts < max_attempts:
                                    time.sleep(0.5)
                                    continue
                        else:
                            step_success = True
                            break
                    else:
                        last_err = result[:200]
                        if attempts < max_attempts:
                            time.sleep(0.5)
                            continue

            except Exception as e:
                last_err = f"{type(e).__name__}: {e}"
                logger.warning("replay_workflow step#%d 异常: %s", step_id, last_err)
                if attempts < max_attempts:
                    time.sleep(0.5)
                    continue

        step_report["time"] = round(time.time() - step_start, 2)
        step_report["retries"] = attempts - 1
        if step_success:
            step_report["status"] = "success"
            # 对启动程序类步骤强制 settle，给 GUI 渲染留时间
            # wait_window 只等"窗口出现"，不等"显示区渲染完成"
            # 因此无论是否传 wait_window，都补一个稳定等待，确保下一步
            # click_element 的 before_shot 抓到的是真正的初始状态
            if action == "open_application":
                time.sleep(0.8)  # 显示区渲染兜底等待
                extra = "已等待0.8s(显示区渲染)"
                if not bool(params.get("wait_window", "").strip()):
                    extra = "已等待0.8s(无wait_window兜底)"
                step_report["note"] = (step_report.get("note", "") + " | " + extra).strip(" |")
        else:
            step_report["status"] = "failed"
            step_report["note"] = (step_report["note"] + " | " + last_err).strip(" |")
            report_steps.append(step_report)
            # 失败处理
            if on_failure == "abort":
                return _build_report(name, len(steps), report_steps, start_time, aborted=True)
            elif on_failure == "skip":
                continue
            else:
                # retry / ask_user 都按 abort 处理（已经用完 max_retries）
                return _build_report(name, len(steps), report_steps, start_time, aborted=True)

        report_steps.append(step_report)

    return _build_report(name, len(steps), report_steps, start_time)


def _is_step_success(result: str, action: str) -> bool:
    """根据工具返回的字符串判断是否成功。"""
    if not isinstance(result, str):
        return True
    # 错误关键字
    if result.startswith("错误") or result.startswith("Error"):
        return False
    if "操作已中止" in result:
        return False
    if "未找到元素" in result and action == "click_element":
        return False
    if '"found": false' in result.lower() or '"found":false' in result.lower():
        return False
    return True


def _calc_screenshot_diff(before_path: str, after_path: str) -> float:
    """计算两张截图的像素差异比例（0~1）。
    返回值越大表示差异越大。如果任一图片无法读取，返回 1.0（保守认为有变化，避免误回退）。
    """
    try:
        from PIL import Image
        import numpy as np
        img1 = Image.open(before_path).convert("RGB")
        img2 = Image.open(after_path).convert("RGB")
        # 统一尺寸（防止分辨率变化）
        if img1.size != img2.size:
            img2 = img2.resize(img1.size)
        arr1 = np.asarray(img1, dtype=np.int16)
        arr2 = np.asarray(img2, dtype=np.int16)
        diff = np.abs(arr1 - arr2)
        # 任意通道差异超过 10 的像素占比
        changed_pixels = int(np.sum(np.any(diff > 10, axis=2)))
        total_pixels = arr1.shape[0] * arr1.shape[1]
        return changed_pixels / total_pixels if total_pixels > 0 else 1.0
    except Exception as e:
        logger.warning("截图差异计算失败(%s)，保守返回1.0", e)
        return 1.0


def _stable_screenshot(timeout: float = 2.0, interval: float = 0.3,
                       threshold: float = 0.0005) -> str | None:
    """获取一张稳定的截图。

    连续两次截图，若差异 < threshold（默认 0.05%）则认为 UI 已稳定，返回后一张；
    否则继续重试直到 timeout。超时仍未稳定则返回最后一次截图（避免无限阻塞）。

    用于根治"点击前抓到中间帧"导致的差异验证误判：
    例如计算器刚启动时显示区从空白→0 的过渡帧不应被当作"初始状态"。
    """
    from core import gui_tools
    deadline = time.time() + timeout
    try:
        prev = gui_tools.screenshot_screen()
    except Exception as e:
        logger.warning("_stable_screenshot 首次截图失败(%s)", e)
        return None
    while time.time() < deadline:
        time.sleep(interval)
        try:
            cur = gui_tools.screenshot_screen()
        except Exception as e:
            logger.warning("_stable_screenshot 重试截图失败(%s)", e)
            return prev
        try:
            d = _calc_screenshot_diff(prev, cur)
        except Exception:
            return cur
        if d < threshold:
            return cur  # UI 已稳定
        prev = cur
    logger.warning("_stable_screenshot 超时(%.1fs)未稳定，返回最后一次截图", timeout)
    return prev


def _verify_step(verify: dict, variables: dict) -> tuple[bool, str]:
    """执行步骤的 verify 字段。"""
    vtype = verify.get("type", "none")
    if vtype == "none":
        return True, "无验证"
    if vtype == "wait_for_element":
        from core import gui_tools
        target = _substitute_variables(verify.get("target", ""), variables)
        timeout = float(verify.get("timeout", 10))
        result = gui_tools.wait_for_element(target_description=target, timeout=timeout)
        try:
            parsed = json.loads(result)
            if parsed.get("found"):
                return True, f"已出现: {target}"
            return False, f"未出现: {target}"
        except json.JSONDecodeError:
            return False, f"验证返回非JSON: {result[:100]}"
    if vtype == "verify_change":
        # 像素差异验证已在缓存坐标路径单独处理，这里保守通过
        return True, "verify_change 通过（由截图差异验证兜底）"
    return True, "未知验证类型，跳过"


def _build_report(name: str, total: int, steps: list[dict],
                  start_time: float, aborted: bool = False) -> str:
    """生成回放报告。"""
    total_time = round(time.time() - start_time, 2)
    success_count = sum(1 for s in steps if s["status"] == "success")
    failed_count = sum(1 for s in steps if s["status"] == "failed")
    skipped_count = sum(1 for s in steps if s["status"] == "skipped")

    status = "❌ 已中止" if aborted else ("✅ 全部成功" if failed_count == 0 else "⚠️ 部分失败")

    lines = [
        f"{'═' * 50}",
        f"工作流回放报告：{name}",
        f"{'═' * 50}",
        f"状态：{status}",
        f"总步骤：{total}  成功：{success_count}  失败：{failed_count}  跳过：{skipped_count}",
        f"总耗时：{total_time}s",
        f"{'─' * 50}",
        "步骤明细：",
    ]
    for s in steps:
        mark = {"success": "✅", "failed": "❌", "skipped": "⏭️", "pending": "⏸️"}.get(s["status"], "?")
        cache_flag = " [缓存]" if s.get("used_cache") else ""
        retry_flag = f" (重试{s['retries']}次)" if s.get("retries", 0) > 0 else ""
        note = f" | {s['note']}" if s.get("note") else ""
        lines.append(
            f"  {mark} #{s['step_id']} {s['action']}{cache_flag}{retry_flag} "
            f"({s.get('time', 0)}s){note}"
        )
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# 工具3：save_workflow — 手动保存
# ═══════════════════════════════════════════════════════════════

@register_tool(
    name="save_workflow",
    description=(
        "手动保存工作流（不经过录制）。适合你已经想清楚流程，直接写成JSON保存。"
        "steps 是 JSON 数组字符串，每个 step 含 action/params/可选 verify/on_failure/max_retries。"
        "支持的 action: open_application, run_command, click_position, click_element, "
        "type_text, press_key, wait_for_element, wait。"
        "示例：save_workflow('my_flow', '我的流程', "
        "'[{\"action\":\"open_application\",\"params\":{\"app_name\":\"calc\"}}]')"
    ),
    parameters={
        "name": {
            "type": "string",
            "description": "工作流名称（英文/数字/下划线）",
        },
        "description": {
            "type": "string",
            "description": "工作流描述（可选）",
        },
        "steps": {
            "type": "string",
            "description": "步骤JSON数组字符串，每个step含action/params/verify等",
        },
    },
    category="workflow",
    timeout=10,
)
def save_workflow(name: str, description: str = "", steps: str = "") -> str:
    err = _validate_name(name)
    if err:
        return f"错误：{err}"

    if not steps or not steps.strip():
        return "错误：steps 不能为空"

    try:
        steps_list = json.loads(steps)
    except json.JSONDecodeError as e:
        return f"错误：steps 不是合法 JSON：{e}"

    if not isinstance(steps_list, list):
        return "错误：steps 必须是 JSON 数组"

    # 规范化每个 step
    normalized = []
    for i, s in enumerate(steps_list):
        if not isinstance(s, dict):
            return f"错误：第 {i+1} 步不是对象"
        action = s.get("action")
        if not action:
            return f"错误：第 {i+1} 步缺少 action"
        params = s.get("params", {})
        if not isinstance(params, dict):
            return f"错误：第 {i+1} 步的 params 不是对象"
        step = {
            "step_id": i + 1,
            "action": action,
            "params": _safe_params(params),
        }
        if "cached_coordinates" in s:
            step["cached_coordinates"] = s["cached_coordinates"]
        if "verify" in s:
            step["verify"] = s["verify"]
        if "on_failure" in s:
            step["on_failure"] = s["on_failure"]
        if "max_retries" in s:
            step["max_retries"] = s["max_retries"]
        normalized.append(step)

    wf = {
        "name": name.strip(),
        "description": description or "",
        "version": "1.0",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "variables": _extract_variables(normalized),
        "steps": normalized,
    }

    path = _workflow_path(name)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(wf, f, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"错误：保存失败：{e}"

    logger.info("save_workflow 保存：%s (%d 步)", path, len(normalized))
    return (
        f"✅ 工作流已保存\n"
        f"名称：{name}\n"
        f"描述：{description or '(无)'}\n"
        f"步骤数：{len(normalized)}\n"
        f"变量：{wf['variables'] or '(无)'}\n"
        f"保存位置：{path}\n\n"
        f"步骤预览：\n" + _preview_steps(normalized)
    )


# ═══════════════════════════════════════════════════════════════
# 工具4：list_workflows — 列出已保存的工作流
# ═══════════════════════════════════════════════════════════════

@register_tool(
    name="list_workflows",
    description=(
        "列出所有已保存的工作流。返回名称、描述、步骤数、创建时间、变量列表。"
        "示例：list_workflows()"
    ),
    parameters={},
    category="workflow",
    timeout=10,
)
def list_workflows() -> str:
    files = sorted(_WORKFLOW_DIR.glob(f"*{_WORKFLOW_SUFFIX}"))
    if not files:
        return (
            f"📂 工作流目录 {_WORKFLOW_DIR}\n"
            f"暂无已保存的工作流。\n"
            f"用 record_workflow(action='start') 录制，"
            f"或用 save_workflow 手动创建。"
        )

    lines = [f"📂 工作流目录：{_WORKFLOW_DIR}", f"共 {len(files)} 个工作流", "─" * 60]
    for f in files:
        try:
            with open(f, "r", encoding="utf-8") as fp:
                wf = json.load(fp)
            name = wf.get("name", f.stem)
            desc = wf.get("description", "") or "(无描述)"
            nsteps = len(wf.get("steps", []))
            created = wf.get("created_at", "?")
            variables = wf.get("variables", [])
            var_str = f" 变量={variables}" if variables else ""
            lines.append(f"  📄 {name}  ({nsteps} 步){var_str}")
            lines.append(f"     描述：{desc}")
            lines.append(f"     创建：{created}")
        except Exception as e:
            lines.append(f"  ❌ {f.name} 读取失败：{e}")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# 工具5：delete_workflow — 删除工作流
# ═══════════════════════════════════════════════════════════════

@register_tool(
    name="delete_workflow",
    description=(
        "删除已保存的工作流文件。为防止误删，必须传 confirm=true 才会真正删除。"
        "示例：delete_workflow('calc_test', confirm=true) 删除 calc_test 工作流"
    ),
    parameters={
        "name": {
            "type": "string",
            "description": "要删除的工作流名称",
        },
        "confirm": {
            "type": "boolean",
            "description": "二次确认，必须为 true 才执行删除",
        },
    },
    category="workflow",
    timeout=10,
)
def delete_workflow(name: str, confirm: bool = False) -> str:
    err = _validate_name(name)
    if err:
        return f"错误：{err}"

    if not confirm:
        # 不删除，先返回工作流摘要让用户确认
        path = _workflow_path(name)
        if not path.exists():
            return f"错误：工作流「{name}」不存在"
        try:
            with open(path, "r", encoding="utf-8") as f:
                wf = json.load(f)
            nsteps = len(wf.get("steps", []))
            desc = wf.get("description", "") or "(无描述)"
        except Exception:
            nsteps = "?"
            desc = "(读取失败)"
        return (
            f"⚠️ 即将删除工作流「{name}」\n"
            f"描述：{desc}\n"
            f"步骤数：{nsteps}\n"
            f"文件：{path}\n\n"
            f"此操作不可恢复。如确认删除，请再次调用：\n"
            f"  delete_workflow('{name}', confirm=true)"
        )

    path = _workflow_path(name)
    if not path.exists():
        return f"错误：工作流「{name}」不存在"

    # 备份到 .trash 子目录（保险起见，可手动恢复）
    try:
        trash_dir = _WORKFLOW_DIR / ".trash"
        trash_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"{name}{_WORKFLOW_SUFFIX}.{ts}.bak"
        backup_path = trash_dir / backup_name
        import shutil
        shutil.copy2(path, backup_path)
    except Exception as e:
        logger.warning("delete_workflow 备份失败(继续删除): %s", e)
        backup_path = None

    try:
        path.unlink()
    except Exception as e:
        return f"错误：删除失败：{e}"

    backup_note = f"\n备份已保存：{backup_path}" if backup_path else ""
    logger.info("delete_workflow 已删除：%s (备份=%s)", path, backup_path)
    return (
        f"✅ 工作流「{name}」已删除"
        f"{backup_note}"
    )


# ═══════════════════════════════════════════════════════════════
# 工具6：edit_workflow — 编辑/追加/删除步骤
# ═══════════════════════════════════════════════════════════════

@register_tool(
    name="edit_workflow",
    description=(
        "⭐编辑已保存的工作流，支持 4 种操作：\n"
        "  - append:  在末尾追加一步\n"
        "  - insert:  在指定位置插入一步（position 从1开始）\n"
        "  - delete:  删除指定位置的步骤\n"
        "  - replace: 替换指定位置的步骤\n"
        "编辑后会自动重新编号 step_id 并刷新变量列表。\n"
        "示例：\n"
        "  edit_workflow('calc_test', op='append', step={\"action\":\"press_key\",\"params\":{\"key\":\"enter\"}})\n"
        "  edit_workflow('calc_test', op='insert', position=2, step={\"action\":\"wait\",\"params\":{\"seconds\":1}})\n"
        "  edit_workflow('calc_test', op='delete', position=3)\n"
        "  edit_workflow('calc_test', op='replace', position=2, step={\"action\":\"click_element\",\"params\":{\"target_description\":\"按钮8\"}})"
    ),
    parameters={
        "name": {
            "type": "string",
            "description": "要编辑的工作流名称",
        },
        "op": {
            "type": "string",
            "description": "操作类型: append / insert / delete / replace",
        },
        "position": {
            "type": "integer",
            "description": "步骤位置(从1开始)。insert/replace/delete 必填；append 忽略",
        },
        "step": {
            "type": "object",
            "description": "新的步骤对象（含 action/params/可选 verify/on_failure/max_retries）。append/insert/replace 必填",
        },
    },
    category="workflow",
    timeout=10,
)
def edit_workflow(name: str, op: str = "", position: int = 0,
                  step: dict | None = None) -> str:
    err = _validate_name(name)
    if err:
        return f"错误：{err}"

    op = (op or "").strip().lower()
    if op not in ("append", "insert", "delete", "replace"):
        return "错误：op 必须是 append / insert / delete / replace 之一"

    path = _workflow_path(name)
    if not path.exists():
        return f"错误：工作流「{name}」不存在"

    try:
        with open(path, "r", encoding="utf-8") as f:
            wf = json.load(f)
    except Exception as e:
        return f"错误：读取工作流失败：{e}"

    steps: list[dict] = wf.get("steps", [])

    # ── append ──
    if op == "append":
        if not step or not isinstance(step, dict):
            return "错误：append 需要提供 step"
        new_step, err_msg = _normalize_step(step, len(steps) + 1)
        if err_msg:
            return f"错误：{err_msg}"
        steps.append(new_step)
        action_desc = f"在末尾追加步骤 #{len(steps)}"

    # ── insert ──
    elif op == "insert":
        if not step or not isinstance(step, dict):
            return "错误：insert 需要提供 step"
        if position < 1 or position > len(steps) + 1:
            return f"错误：position 越界（当前 {len(steps)} 步，应在 1~{len(steps)+1}）"
        new_step, err_msg = _normalize_step(step, position)
        if err_msg:
            return f"错误：{err_msg}"
        steps.insert(position - 1, new_step)
        action_desc = f"在位置 {position} 插入步骤"

    # ── delete ──
    elif op == "delete":
        if position < 1 or position > len(steps):
            return f"错误：position 越界（当前 {len(steps)} 步，应在 1~{len(steps)}）"
        removed = steps.pop(position - 1)
        action_desc = f"删除步骤 #{position} ({removed.get('action', '?')})"

    # ── replace ──
    elif op == "replace":
        if not step or not isinstance(step, dict):
            return "错误：replace 需要提供 step"
        if position < 1 or position > len(steps):
            return f"错误：position 越界（当前 {len(steps)} 步，应在 1~{len(steps)}）"
        new_step, err_msg = _normalize_step(step, position)
        if err_msg:
            return f"错误：{err_msg}"
        steps[position - 1] = new_step
        action_desc = f"替换步骤 #{position}"

    # 重新编号 + 刷新变量
    for i, s in enumerate(steps):
        s["step_id"] = i + 1
    wf["steps"] = steps
    wf["variables"] = _extract_variables(steps)
    wf["updated_at"] = datetime.now().isoformat(timespec="seconds")

    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(wf, f, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"错误：保存失败：{e}"

    logger.info("edit_workflow %s: %s (%s, 现 %d 步)", op, name, action_desc, len(steps))
    return (
        f"✅ 工作流「{name}」已更新\n"
        f"操作：{action_desc}\n"
        f"当前步骤数：{len(steps)}\n"
        f"变量：{wf['variables'] or '(无)'}\n\n"
        f"步骤预览：\n" + _preview_steps(steps)
    )


def _normalize_step(step: dict, step_id: int) -> tuple[dict | None, str | None]:
    """规范化单个 step，返回 (normalized, error)。error 非 None 表示失败。"""
    action = step.get("action")
    if not action:
        return None, "step 缺少 action 字段"
    params = step.get("params", {})
    if not isinstance(params, dict):
        return None, "step 的 params 不是对象"
    normalized = {
        "step_id": step_id,
        "action": action,
        "params": _safe_params(params),
    }
    if "cached_coordinates" in step:
        normalized["cached_coordinates"] = step["cached_coordinates"]
    if "verify" in step:
        normalized["verify"] = step["verify"]
    if "on_failure" in step:
        normalized["on_failure"] = step["on_failure"]
    if "max_retries" in step:
        normalized["max_retries"] = step["max_retries"]
    return normalized, None
