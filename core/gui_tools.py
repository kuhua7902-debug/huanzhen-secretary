"""GUI 自动化工具集 —— 让 agent 拥有"眼睛"和"手"

阶段一交付物：
- screenshot_screen      截图（pyautogui）
- screenshot_and_analyze 截图 + 调用 Qwen3-VL-Plus 视觉模型分析（核心视觉工具）

后续阶段会追加鼠标/键盘/窗口管理工具。

依赖：
- pyautogui      截图
- Pillow         图片处理（pyautogui 依赖，会自动装）

注意：本模块的所有函数都会被 nanobot/adapter_tools.py 动态 import 并注册为工具，
函数签名和返回值（str）必须符合 HuanzhenTool 的约定。
"""

from __future__ import annotations

import base64
import os
import time
from typing import Optional

from core.logger import setup_logger
from core.gui_abort import is_aborted, trigger_abort
from core.tools import register_tool

logger = setup_logger("huanzhen.gui_tools")

# ── Windows DPI 感知：让截图返回物理像素，而非缩放后的逻辑像素 ──
import ctypes as _ctypes
try:
    _ctypes.windll.user32.SetProcessDPIAware()
    logger.info("DPI awareness set: process is now DPI-aware")
except Exception as _dpi_err:
    logger.warning("SetProcessDPIAware failed (non-Windows or permission issue): %s", _dpi_err)


# ────────────────────────────────────────────────────────────
# DPI 缩放处理（Windows 高分屏坐标体系统一）
# ────────────────────────────────────────────────────────────
#
# 问题背景（Windows + 系统缩放 125%/150%/175%）：
#   pyautogui.size()          → 逻辑像素（缩放后）  e.g. 1707x960
#   pyautogui.screenshot()    → 物理像素（真实）    e.g. 2560x1440
#   pyautogui.click(x, y)     → 逻辑像素
#
# 如果不统一，会让视觉模型在物理像素图上找元素，
# 但返回坐标 / 点击却按逻辑像素，导致系统性偏移。
#
# 统一策略：视觉模型始终用「图片实际像素」坐标系，
# 返回的物理像素坐标经 _physical_to_logical() 转换后再交给 click_position。

def _get_dpi_scale() -> tuple[float, float, int, int, int, int]:
    """获取当前屏幕的 DPI 缩放信息。

    返回 (scale_x, scale_y, logical_w, logical_h, physical_w, physical_h)：
    - scale_x/scale_y: 物理/逻辑 像素比（>1.0 说明启用了缩放）
    - logical_w/h:     pyautogui.size() 的逻辑像素尺寸
    - physical_w/h:    实际物理像素尺寸（截图的真实尺寸）

    出错时返回 1.0 缩放 + 1920x1080 兜底。
    """
    try:
        import pyautogui
        from PIL import ImageGrab as _ImageGrab
        logical_w, logical_h = pyautogui.size()
        # 截一张图获取真实物理像素尺寸（不保存，只读 size）
        # 用 PIL ImageGrab + all_screens=True 确保多显示器+高DPI也能截全
        probe = _ImageGrab.grab(all_screens=True)
        physical_w, physical_h = probe.size
        # 防止除零
        sx = physical_w / logical_w if logical_w else 1.0
        sy = physical_h / logical_h if logical_h else 1.0
        return sx, sy, logical_w, logical_h, physical_w, physical_h
    except Exception as e:
        logger.warning("_get_dpi_scale failed (%s), fallback to 1.0 / 1920x1080", e)
        return 1.0, 1.0, 1920, 1080, 1920, 1080


def _physical_to_logical(x: float, y: float, scale_x: float, scale_y: float) -> tuple[int, int]:
    """物理像素坐标 → 逻辑像素坐标（用于交给 pyautogui.click）。

    若 scale 接近 1.0（无缩放），原样返回。
    """
    if abs(scale_x - 1.0) < 0.01 and abs(scale_y - 1.0) < 0.01:
        return int(round(x)), int(round(y))
    return int(round(x / scale_x)), int(round(y / scale_y))


# ────────────────────────────────────────────────────────────
# 截图
# ────────────────────────────────────────────────────────────

@register_tool(
    name="screenshot_screen",
    description="截取当前屏幕并返回图片保存路径。如果只需要截图不需要视觉分析用这个。示例：screenshot_screen()、screenshot_screen('0,0,800,600')",
    parameters={
        "region": {"type": "string", "description": "截图区域 x,y,width,height，不传则全屏（可选）"},
        "save_path": {"type": "string", "description": "保存路径（可选，默认存 screenshots/）"},
    },
    category="gui",
    timeout=30,
)
def screenshot_screen(region: str = "", save_path: str = "") -> str:
    """截取当前屏幕，返回截图文件的绝对路径。

    参数：
        region:    可选，格式 "x,y,width,height"，不传则全屏截图
        save_path: 可选，保存路径；默认存到项目下 screenshots/ 目录

    返回：截图文件的绝对路径
    """
    # ── 刹车：用户点停止后不再截图 ──
    if is_aborted():
        return "操作已中止：用户已点击停止"

    try:
        import pyautogui
    except ImportError:
        return (
            "错误：未安装 pyautogui，无法截图。请执行："
            "py -3.12 -m pip install pyautogui pillow"
        )

    # 安全设置：鼠标移到屏幕左上角 (0,0) 触发 FailSafe 中止
    pyautogui.FAILSAFE = True

    if region:
        try:
            parts = [int(v.strip()) for v in region.split(",")]
            if len(parts) != 4:
                return "错误：region 格式应为 x,y,width,height"
            from PIL import ImageGrab
            x, y, w, h = parts
            img = ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True)
        except ValueError:
            return f"错误：region 参数解析失败: {region}"
    else:
        from PIL import ImageGrab
        img = ImageGrab.grab(all_screens=True)

    if not save_path:
        shots_dir = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "screenshots")
        )
        os.makedirs(shots_dir, exist_ok=True)
        save_path = os.path.join(shots_dir, f"shot_{int(time.time())}.png")

    save_path = os.path.abspath(save_path)
    img.save(save_path)
    logger.info("Screenshot saved: %s", save_path)
    return save_path


# ────────────────────────────────────────────────────────────
# 核心视觉工具：截图 + 视觉模型分析
# ────────────────────────────────────────────────────────────

@register_tool(
    name="screenshot_and_analyze",
    description="⭐核心视觉工具：截取当前屏幕并用视觉模型（Qwen3-VL-Plus）分析。当需要'看'屏幕上有什么内容、找按钮位置、判断当前界面状态时调用。返回视觉模型对屏幕的描述和可点击元素的大致坐标。",
    parameters={
        "question": {"type": "string", "description": "想让视觉模型分析的问题，如'保存按钮在哪'、'当前对话框是什么'"},
    },
    category="gui_vision",
    timeout=120,
)
def screenshot_and_analyze(question: str = "描述当前屏幕内容，列出可点击的按钮和它们的大致坐标") -> str:
    """⭐ 核心视觉工具：截取当前屏幕，并用 Qwen3-VL-Plus 视觉模型分析。

    这是 agent 的"眼睛"。当需要"看"屏幕上有什么内容、找按钮位置、
    判断当前界面状态时调用。返回视觉模型对屏幕的理解（文本），
    供 DeepSeek 主大脑决策下一步操作。

    参数：
        question: 想让视觉模型关注的问题。
                  例如："保存按钮在哪？"、"当前对话框是什么？"、
                  "屏幕上有什么可点击的按钮？分别给出坐标"

    返回：[截图路径] + [视觉模型分析结果] 的拼接字符串
    """
    # 1. 截图
    img_path = screenshot_screen()
    if img_path.startswith("错误"):
        return img_path  # 截图失败直接返回错误

    # 2. 读取图片转 base64
    try:
        with open(img_path, "rb") as f:
            img_bytes = f.read()
        # 大图压缩：超过 4MB 时用 Pillow 缩放
        if len(img_bytes) > 4 * 1024 * 1024:
            try:
                from PIL import Image
                import io
                pil_img = Image.open(img_path)
                # 等比缩放，最长边不超过 1920
                max_edge = 1920
                w, h = pil_img.size
                if max(w, h) > max_edge:
                    scale = max_edge / max(w, h)
                    pil_img = pil_img.resize((int(w * scale), int(h * scale)))
                buf = io.BytesIO()
                pil_img.save(buf, format="PNG")
                img_bytes = buf.getvalue()
                logger.info("Image compressed: %d -> %d bytes", len(img_bytes), len(img_bytes))
            except Exception as compress_err:
                logger.warning("Image compress failed: %s", compress_err)

        img_b64 = base64.b64encode(img_bytes).decode("ascii")
    except Exception as e:
        return f"错误：读取截图失败: {e}"

    # 3. 获取视觉模型
    vision_model, vision_name = _get_vision_model()
    if vision_model is None:
        return (
            f"[截图已保存: {img_path}]\n"
            f"[视觉分析失败]：未配置视觉模型。请在 config.yaml 的 models 下添加 "
            f"qwen_vl 配置项（含 base_url/api_key/model/vision:true），"
            f"并在 .env 中设置 DASHSCOPE_API_KEY。"
        )

    # 4. 构造多模态消息（OpenAI 兼容协议，DashScope 也支持）
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": question},
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{img_b64}"
                }
            }
        ]
    }]

    # 5. 调用视觉模型
    try:
        logger.info("Vision analysis calling %s, question=%s", vision_name, question[:80])
        result = vision_model.chat(messages, temperature=0.2)
        logger.info("Vision response: %s", str(result)[:200])
        return f"[截图已保存: {img_path}]\n[视觉分析({vision_name})]: {result}"
    except Exception as e:
        logger.error("Vision analysis failed: %s", e, exc_info=True)
        return (
            f"[截图已保存: {img_path}]\n"
            f"[视觉分析失败]: {type(e).__name__}: {str(e)[:300]}"
        )


# ────────────────────────────────────────────────────────────
# 内部辅助
# ────────────────────────────────────────────────────────────

def _get_vision_model():
    """获取视觉模型适配器及其名称。返回 (adapter, name)，未配置则返回 (None, None)。

    注意：这里不 import core.agent，避免触发整个 agent 模块的 import 链
    （agent 会拉起 chromadb / rag / database 等重依赖）。直接读 config.yaml。
    """
    try:
        from core.models import ModelRouter
        config = _load_config_lite()
        models_config = config.get("models", {})
        router = ModelRouter(config)
        vision = router.get_vision()
        if vision is None:
            logger.warning(
                "视觉模型未找到！请确保 config.yaml 中 models 下有标记 vision:true 的模型配置"
                "（如 qwen_vl），并在 .env 中设置对应的 API_KEY 环境变量。"
                "当前已配置的模型: %s",
                [k for k in models_config if k != "default"]
            )
            return None, None
        for name in models_config:
            if name == "default":
                continue
            if models_config[name].get("vision"):
                logger.info("视觉模型已加载: %s (model=%s)", name, models_config[name].get("model", "unknown"))
                return vision, name
        return vision, "vision"
    except Exception as e:
        logger.warning("获取视觉模型失败: %s（请检查 API key 是否有效、网络是否可达）", e)
        return None, None


def _load_config_lite() -> dict:
    """轻量版配置加载：只读 config.yaml + .env 环境变量，不触碰数据库/agent。

    与 core.agent._load_config 的区别：不读数据库动态模型配置（视觉模型
    不需要从 DB 覆盖），从而避免 import 整个 agent → database → rag 链。
    """
    import os
    import yaml
    import re

    config_path = os.path.join(os.path.dirname(__file__), "..", "config.yaml")
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    # 展开 ${VAR} 环境变量引用（与 agent 行为一致）
    env_pattern = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")

    def _expand(obj):
        if isinstance(obj, str):
            def _replacer(m):
                return os.environ.get(m.group(1), m.group(0))
            return env_pattern.sub(_replacer, obj)
        if isinstance(obj, dict):
            return {k: _expand(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_expand(v) for v in obj]
        return obj

    return _expand(config)


# ────────────────────────────────────────────────────────────
# 安全确认（阶段二）
# ────────────────────────────────────────────────────────────

@register_tool(
    name="confirm_dangerous_action",
    description="危险操作前弹窗让用户确认。返回'confirmed'或'cancelled'。agent 在执行关闭窗口、删除文件等操作前可主动调用此工具征求用户同意。",
    parameters={
        "action_description": {"type": "string", "description": "简短描述要做什么，如'关闭记事本窗口'"},
        "details": {"type": "string", "description": "详细说明，如'将丢失未保存内容'（可选）"},
    },
    category="gui",
    timeout=30,
)
def confirm_dangerous_action(action_description: str, details: str = "") -> str:
    """危险操作前弹窗让用户确认。

    当 agent 要执行关闭窗口、删除文件、执行危险命令等操作前调用此工具，
    会弹出 tkinter 确认框，用户点"是"返回 confirmed，点"否"返回 cancelled。

    参数：
        action_description: 简短描述要做什么，如 "关闭记事本窗口"
        details:            详细说明，如 "这将丢失未保存的内容"

    返回："confirmed" 或 "cancelled"
    """
    try:
        import tkinter as tk
        from tkinter import messagebox
    except ImportError:
        # 无 GUI 环境（如远程 SSH），默认拒绝以保安全
        logger.warning("tkinter 不可用，危险操作默认拒绝")
        return "cancelled"

    try:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        msg = f"Huanzhen Agent 准备执行以下操作：\n\n【操作】{action_description}"
        if details:
            msg += f"\n\n【详情】{details}"
        msg += "\n\n是否允许？"
        result = messagebox.askyesno("Huanzhen Agent 操作确认", msg, parent=root)
        root.destroy()
        return "confirmed" if result else "cancelled"
    except Exception as e:
        logger.error("confirm_dangerous_action 失败: %s", e)
        return f"错误: {e}"


# ────────────────────────────────────────────────────────────
# 鼠标键盘控制（阶段二）
# ────────────────────────────────────────────────────────────

@register_tool(
    name="click_position",
    description="在屏幕指定坐标点击鼠标。左上角为(0,0)。示例：click_position(850, 120) 点击(850,120)处；click_position(500,300,'right') 右键点击",
    parameters={
        "x": {"type": "integer", "description": "屏幕横坐标"},
        "y": {"type": "integer", "description": "屏幕纵坐标"},
        "button": {"type": "string", "description": "鼠标键：left(默认)/right/middle"},
        "clicks": {"type": "integer", "description": "点击次数，1=单击(默认) 2=双击"},
        "duration": {"type": "number", "description": "鼠标移动耗时(秒)，0=瞬移(默认)，>0=平滑移动"},
        "require_confirm": {"type": "boolean", "description": "是否需要用户确认弹窗，默认false"},
    },
    category="gui_mouse",
    timeout=30,
)
def click_position(
    x: int,
    y: int,
    button: str = "left",
    clicks: int = 1,
    duration: float = 0.0,
    require_confirm: bool = False,
) -> str:
    """在屏幕指定坐标点击鼠标。

    参数：
        x, y:           屏幕坐标（左上角为 0,0）
        button:         "left"（左键，默认）/ "right"（右键）/ "middle"（中键）
        clicks:         点击次数，1=单击（默认），2=双击
        duration:       鼠标移动耗时（秒），0=瞬移（默认），>0=平滑移动模拟人类
        require_confirm: 是否需要用户确认（危险操作设为 True）

    返回：执行结果字符串
    """
    # ── 刹车：用户点停止后不再点击 ──
    if is_aborted():
        return "操作已中止：用户已点击停止"

    try:
        import pyautogui
    except ImportError:
        return "错误：未安装 pyautogui。请执行 py -3.12 -m pip install pyautogui"

    if require_confirm:
        decision = confirm_dangerous_action(
            f"在坐标 ({x},{y}) 点击鼠标 {button} 键 {clicks} 次",
        )
        if decision != "confirmed":
            return f"操作已取消（用户拒绝）"

    pyautogui.FAILSAFE = True
    try:
        # 坐标边界检查
        sw, sh = pyautogui.size()
        if not (0 <= x < sw and 0 <= y < sh):
            return f"错误：坐标 ({x},{y}) 超出屏幕范围 {sw}x{sh}"

        # ── 二次刹车：弹窗确认/坐标检查期间用户可能又点了停止 ──
        if is_aborted():
            return "操作已中止：用户已点击停止"

        btn = button if button in ("left", "right", "middle") else "left"
        pyautogui.click(x=x, y=y, button=btn, clicks=clicks, duration=duration)
        logger.info("click_position: (%d,%d) %s x%d", x, y, btn, clicks)

        # 阶段三钩子：录制工作流时自动记录（零侵入）
        try:
            from core.workflow_tools import _notify_workflow
            _notify_workflow(
                "click_position",
                {"x": x, "y": y, "button": btn, "clicks": clicks, "duration": duration},
                cached_coordinates={"x": x, "y": y},
                result=f"已在 ({x},{y}) {btn}键点击 {clicks} 次",
            )
        except Exception:
            pass

        return f"已在 ({x},{y}) {btn}键点击 {clicks} 次"
    except pyautogui.FailSafeException:
        return "错误：触发 FailSafe（鼠标移到左上角），操作已中止"
    except Exception as e:
        return f"错误：{type(e).__name__}: {e}"


@register_tool(
    name="type_text",
    description="在当前焦点处输入文本。支持中文（通过剪贴板粘贴）。示例：type_text('Hello World')、type_text('你好世界')",
    parameters={
        "text": {"type": "string", "description": "要输入的文本"},
        "interval": {"type": "number", "description": "每个字符间隔(秒)，0=立即输入(默认)"},
    },
    category="gui_keyboard",
    timeout=30,
)
def type_text(text: str, interval: float = 0.0) -> str:
    """在当前焦点输入文本。支持中文（用剪贴板粘贴实现，比 typewrite 快且兼容）。

    参数：
        text:     要输入的文本
        interval: 每个字符间隔（秒），0=立即输入

    返回：执行结果字符串
    """
    if not text:
        return "错误：text 不能为空"

    # ── 刹车：用户点停止后不再输入 ──
    if is_aborted():
        return "操作已中止：用户已点击停止"

    try:
        import pyautogui
    except ImportError:
        return "错误：未安装 pyautogui"

    pyautogui.FAILSAFE = True
    try:
        # 中文或非 ASCII 字符走剪贴板粘贴
        if any(ord(c) > 127 for c in text):
            try:
                import pyperclip
            except ImportError:
                return "错误：输入中文需要 pyperclip，请执行 py -3.12 -m pip install pyperclip"

            # ⭐ 剪贴板保护：旧实现直接 pyperclip.copy(text) 覆盖剪贴板，粘贴完就
            #    不管了，用户原先复制的内容被永久破坏（图片/文件等内容尤甚）。
            #    这里先保存原内容，粘贴后必须在 finally 里恢复；
            #    若剪贴板不可读或内容非文本（paste() 拿到空串），则放弃恢复
            #    （避免用空串把用户的图片等非文本内容清掉），只记 debug 日志，
            #    绝不影响本次输入结果。
            saved_clip = ""
            clip_saved = False
            try:
                saved_clip = pyperclip.paste()
                clip_saved = isinstance(saved_clip, str) and saved_clip != ""
            except Exception as e:
                logger.debug("读取剪贴板失败，跳过恢复: %s", e)

            try:
                pyperclip.copy(text)
                pyautogui.hotkey("ctrl", "v")
                # 粘贴是异步的：目标程序收到 Ctrl+V 后还要读取剪贴板，
                # 立刻恢复会让它粘到旧内容，故留一点时间再还原
                time.sleep(0.15)
                logger.info("type_text (paste): %d chars", len(text))
                _result_str = f"已通过剪贴板粘贴输入 {len(text)} 个字符"
            finally:
                if clip_saved:
                    try:
                        pyperclip.copy(saved_clip)
                    except Exception as e:
                        logger.debug("恢复剪贴板失败（已忽略）: %s", e)
                else:
                    logger.debug("剪贴板为空或非文本内容，跳过恢复")
        else:
            # 纯 ASCII 用 typewrite
            if interval > 0:
                pyautogui.typewrite(text, interval=interval)
            else:
                pyautogui.typewrite(text)
            logger.info("type_text: %d chars", len(text))
            _result_str = f"已输入 {len(text)} 个字符"

        # 阶段三钩子：录制工作流时自动记录（零侵入）
        try:
            from core.workflow_tools import _notify_workflow
            _notify_workflow("type_text", {"text": text, "interval": interval}, result=_result_str)
        except Exception:
            pass

        return _result_str
    except Exception as e:
        return f"错误：{type(e).__name__}: {e}"


@register_tool(
    name="press_key",
    description="按键或快捷键组合，多个键用+分隔表示同时按。示例：press_key('enter') 回车；press_key('ctrl+s') 保存；press_key('alt+tab') 切换窗口",
    parameters={
        "key": {"type": "string", "description": "键名或组合，如 enter/ctrl+s/alt+tab"},
    },
    category="gui_keyboard",
    timeout=30,
)
def press_key(key: str) -> str:
    """按键或快捷键组合。多个键用 + 分隔表示同时按。

    参数：
        key: 键名或组合。如 "enter" / "tab" / "esc" / "ctrl+c" / "alt+tab" / "ctrl+s"
             键名不区分大小写，常用：enter/tab/esc/space/backspace/delete/
             up/down/left/right/shift/ctrl/alt/win/f1-f12/a-z/0-9

    返回：执行结果字符串
    """
    if not key:
        return "错误：key 不能为空"

    # ── 刹车：用户点停止后不再按键 ──
    if is_aborted():
        return "操作已中止：用户已点击停止"

    try:
        import pyautogui
    except ImportError:
        return "错误：未安装 pyautogui"

    pyautogui.FAILSAFE = True
    try:
        parts = [k.strip().lower() for k in key.split("+") if k.strip()]
        if not parts:
            return f"错误：无法解析按键组合: {key}"

        if len(parts) == 1:
            pyautogui.press(parts[0])
        else:
            pyautogui.hotkey(*parts)
        logger.info("press_key: %s", key)

        # 阶段三钩子：录制工作流时自动记录（零侵入）
        try:
            from core.workflow_tools import _notify_workflow
            _notify_workflow("press_key", {"key": key}, result=f"已按下: {key}")
        except Exception:
            pass

        return f"已按下: {key}"
    except Exception as e:
        return f"错误：{type(e).__name__}: {e}"


@register_tool(
    name="scroll_mouse",
    description="鼠标滚轮滚动。示例：scroll_mouse('down', 5) 向下滚5格；scroll_mouse('up', 3) 向上滚3格",
    parameters={
        "direction": {"type": "string", "description": "滚动方向：down(默认)/up"},
        "amount": {"type": "integer", "description": "滚动量(格数)，默认3"},
    },
    category="gui_mouse",
    timeout=30,
)
def scroll_mouse(direction: str = "down", amount: int = 3) -> str:
    """鼠标滚轮滚动。

    参数：
        direction: "down"（向下滚，默认）或 "up"（向上滚）
        amount:    滚动量（点击次数），默认 3

    返回：执行结果字符串
    """
    # ── 刹车：用户点停止后不再滚动 ──
    if is_aborted():
        return "操作已中止：用户已点击停止"

    try:
        import pyautogui
    except ImportError:
        return "错误：未安装 pyautogui"

    pyautogui.FAILSAFE = True
    try:
        # pyautogui.scroll 正数=向上，负数=向下
        if direction.lower() == "up":
            clicks = abs(amount)
        elif direction.lower() == "down":
            clicks = -abs(amount)
        else:
            return f"错误：direction 只能是 up/down，收到 {direction}"

        pyautogui.scroll(clicks)
        logger.info("scroll_mouse: %s %d", direction, amount)
        return f"已向{direction}滚动 {abs(amount)} 格"
    except Exception as e:
        return f"错误：{type(e).__name__}: {e}"


@register_tool(
    name="drag_mouse",
    description="从起点坐标拖拽鼠标到终点坐标。用于拖文件到文件夹、调整窗口大小等。示例：drag_mouse(100,100,500,500) 从(100,100)拖到(500,500)",
    parameters={
        "x1": {"type": "integer", "description": "起点横坐标"},
        "y1": {"type": "integer", "description": "起点纵坐标"},
        "x2": {"type": "integer", "description": "终点横坐标"},
        "y2": {"type": "integer", "description": "终点纵坐标"},
        "duration": {"type": "number", "description": "拖拽耗时(秒)，默认0.5"},
        "button": {"type": "string", "description": "鼠标键：left(默认)/right/middle"},
        "require_confirm": {"type": "boolean", "description": "是否需要用户确认，默认false"},
    },
    category="gui_mouse",
    timeout=30,
)
def drag_mouse(
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    duration: float = 0.5,
    button: str = "left",
    require_confirm: bool = False,
) -> str:
    """从 (x1,y1) 拖拽鼠标到 (x2,y2)。用于拖文件到文件夹、调整窗口大小等。

    参数：
        x1, y1:         起点坐标
        x2, y2:         终点坐标
        duration:       拖拽耗时（秒），默认 0.5
        button:         "left"/"right"/"middle"，默认 left
        require_confirm: 是否需要用户确认

    返回：执行结果字符串
    """
    # ── 刹车：用户点停止后不再拖拽 ──
    if is_aborted():
        return "操作已中止：用户已点击停止"

    try:
        import pyautogui
    except ImportError:
        return "错误：未安装 pyautogui"

    if require_confirm:
        decision = confirm_dangerous_action(
            f"从 ({x1},{y1}) 拖拽到 ({x2},{y2})",
        )
        if decision != "confirmed":
            return "操作已取消（用户拒绝）"

    pyautogui.FAILSAFE = True
    try:
        sw, sh = pyautogui.size()
        for coord, label in [((x1, y1), "起点"), ((x2, y2), "终点")]:
            cx, cy = coord
            if not (0 <= cx < sw and 0 <= cy < sh):
                return f"错误：{label}坐标 ({cx},{cy}) 超出屏幕范围 {sw}x{sh}"

        # ── 二次刹车：弹窗确认期间用户可能又点了停止 ──
        if is_aborted():
            return "操作已中止：用户已点击停止"

        btn = button if button in ("left", "right", "middle") else "left"
        # moveTo 起点 → 按住 → moveTo 终点 → 释放
        pyautogui.moveTo(x1, y1, duration=min(duration / 2, 0.25))
        # ── 拖拽中途刹车：moveTo 完成后、按下按钮前再查一次 ──
        if is_aborted():
            return "操作已中止：用户已点击停止（拖拽未完成）"
        pyautogui.mouseDown(button=btn)
        pyautogui.moveTo(x2, y2, duration=duration)
        pyautogui.mouseUp(button=btn)
        logger.info("drag_mouse: (%d,%d) -> (%d,%d) %s", x1, y1, x2, y2, btn)
        return f"已从 ({x1},{y1}) 拖拽到 ({x2},{y2})"
    except pyautogui.FailSafeException:
        return "错误：触发 FailSafe，操作已中止"
    except Exception as e:
        return f"错误：{type(e).__name__}: {e}"


# ────────────────────────────────────────────────────────────
# 视觉→行动桥梁（阶段二）
# ────────────────────────────────────────────────────────────

@register_tool(
    name="screenshot_and_find",
    description="⭐视觉→行动桥梁：截图并用 Qwen-VL 找到目标元素的屏幕坐标。返回JSON坐标，可直接喂给 click_position/drag_mouse 等操作工具。示例：screenshot_and_find('保存按钮')",
    parameters={
        "target_description": {"type": "string", "description": "【必填】要找的元素的文字描述，如'保存按钮'、'地址栏'、'数字7按钮'"},
        "return_all": {"type": "boolean", "description": "true=返回所有匹配，false=只返回最可能的一个(默认)"},
    },
    category="gui_vision",
    timeout=120,
)
def screenshot_and_find(
    target_description: str,
    return_all: bool = False,
) -> str:
    """⭐视觉→行动桥梁：截图并让 Qwen-VL 找到目标元素的屏幕坐标。

    这是让 agent "看到→指向"的关键工具。它会：
    1. 截取当前屏幕
    2. 告诉 Qwen-VL 屏幕尺寸，要求它返回目标元素的 JSON 坐标
    3. 解析 JSON，返回坐标列表

    返回的坐标可以直接喂给 click_position / drag_mouse 等操作工具。

    参数：
        target_description: 想找的元素的文字描述，如 "保存按钮"、"地址栏输入框"、"数字7按钮"
        return_all:         True=返回所有匹配项，False=只返回最可能的一个

    返回：JSON 格式坐标，如：
        {"found": true, "elements": [{"name": "保存按钮", "x": 850, "y": 120, "confidence": "high"}]}
        或未找到时 {"found": false, "reason": "..."}
    """
    import json

    # 1. 截图
    img_path = screenshot_screen()
    if img_path.startswith("错误"):
        return json.dumps({"found": False, "reason": f"截图失败: {img_path}"}, ensure_ascii=False)

    # 2. 获取 DPI 缩放信息 + 图片真实物理像素尺寸
    #    关键：VL 模型看到的图是物理像素，坐标系必须用图片实际尺寸，
    #    而不是 pyautogui.size() 的逻辑像素，否则会系统性偏移。
    scale_x, scale_y, logical_w, logical_h, physical_w, physical_h = _get_dpi_scale()

    # 3. 读取图片并获取真实尺寸（防止 _get_dpi_scale 兜底值与实际图片不符）
    try:
        from PIL import Image as _PILImage
        with open(img_path, "rb") as f:
            img_bytes = f.read()
        # 用图片实际尺寸作为 VL 坐标系（这是模型真正看到的）
        pil_for_size = _PILImage.open(img_path)
        img_w, img_h = pil_for_size.size
        # 重新计算 scale（以图片为准，最准确）
        if logical_w and logical_h:
            scale_x = img_w / logical_w
            scale_y = img_h / logical_h
        else:
            scale_x = scale_y = 1.0
        del pil_for_size
    except Exception as e:
        return json.dumps({"found": False, "reason": f"读取截图失败: {e}"}, ensure_ascii=False)

    # 记录 DPI 状态，方便排查
    dpi_mismatch = abs(scale_x - 1.0) > 0.01 or abs(scale_y - 1.0) > 0.01
    logger.info(
        "screenshot_and_find DPI: logical=%dx%d physical=%dx%d scale=%.3fx%.3f %s",
        logical_w, logical_h, img_w, img_h, scale_x, scale_y,
        "⚠️DPI缩放" if dpi_mismatch else "无缩放",
    )

    # 4. 强制压缩图片给视觉模型
    #    为什么必须压缩：
    #    a) 2K/4K 全屏图（2560x1600）里，一个按钮可能只占 30-50 像素，视觉模型在大图上
    #       定位小目标误差极大（实测偏 1000+ 像素，等于找错位置）。
    #    b) 压缩到 1280 后，模型处理的像素数减半，注意力更集中，定位精度显著提升。
    #    c) 同时用 0-999 归一化坐标协议（见 prompt），压缩不影响坐标映射，比例换算即可。
    compress_ratio = 1.0  # 压缩后 / 压缩前
    compressed_w, compressed_h = img_w, img_h  # 模型实际看到的图尺寸
    try:
        from PIL import Image
        import io
        pil_img = Image.open(img_path)
        max_edge = 1280  # 强制压缩阈值（从 2560 降回 1280，让小目标在图里更清晰）
        w, h = pil_img.size
        if max(w, h) > max_edge:
            compress_ratio = max_edge / max(w, h)
            compressed_w, compressed_h = int(w * compress_ratio), int(h * compress_ratio)
            pil_img = pil_img.resize((compressed_w, compressed_h))
            buf = io.BytesIO()
            pil_img.save(buf, format="PNG")
            img_bytes = buf.getvalue()
            logger.info(
                "Image compressed for VL: %dx%d -> %dx%d (ratio=%.3f), bytes %d -> %d",
                w, h, compressed_w, compressed_h, compress_ratio,
                len(open(img_path, "rb").read()), len(img_bytes),
            )
        img_b64 = base64.b64encode(img_bytes).decode("ascii")
    except Exception as e:
        return json.dumps({"found": False, "reason": f"图片编码失败: {e}"}, ensure_ascii=False)

    # 5. 获取视觉模型
    vision_model, vision_name = _get_vision_model()
    if vision_model is None:
        return json.dumps({
            "found": False,
            "reason": "未配置视觉模型，无法识别。截图已保存: " + img_path,
        }, ensure_ascii=False)

    # 6. 构造 prompt
    #    ⭐关键改进：使用 0-999 归一化坐标协议
    #    为什么不用绝对像素：
    #    a) 很多 VL 模型（qwen-vl 系列等）训练时用归一化坐标（0-1000 或 0-999），
    #       prompt 要求"绝对像素"时，模型可能内部按归一化输出，但当成像素返回，
    #       导致坐标严重偏小（实测 738 应该是 ~1900，正好是 0-999 当像素的典型表现）。
    #    b) 归一化坐标对图片尺寸不敏感，压缩/缩放都不影响映射，鲁棒性最好。
    #    c) 代码侧统一映射：像素 = round(归一化值 / 999 * (图片尺寸 - 1))
    coord_w = img_w  # 原图尺寸，用于最终映射回像素
    coord_h = img_h
    if return_all:
        find_instruction = (
            f"请在这张屏幕截图中找出所有符合描述的元素：「{target_description}」。\n\n"
            f"坐标系约定（重要）：\n"
            f"- 使用归一化坐标，取值范围 0 到 999（含端点）\n"
            f"- (0, 0) 表示图片左上角，(999, 999) 表示图片右下角\n"
            f"- x 表示水平位置（0=最左，999=最右），y 表示垂直位置（0=最上，999=最下）\n\n"
            f"要求：\n"
            f"1. 仔细观察每个元素的可点击区域边界\n"
            f"2. 给出元素可点击区域的【正中心】坐标，不是左上角\n"
            f"3. 坐标必须是 0-999 之间的整数\n"
            f"4. 如果元素是按钮/图标，中心点要落在按钮的视觉中心，不是文字中心\n"
            f"5. x 和 y 是两个独立字段，必须分别给出，不要把 y 值漏掉或合并到 x 后面\n\n"
            f"请严格只返回 JSON，不要任何其他文字。格式（注意 x 和 y 各自独立成键）：\n"
            f'{{"found": true, "elements": [{{"name": "元素名", "x": 500, "y": 300, "confidence": "high|medium|low"}}]}}\n'
            f"如果没找到，返回 {{\"found\": false, \"reason\": \"...\"}}。"
        )
    else:
        find_instruction = (
            f"请在这张屏幕截图中找到这个元素：「{target_description}」。\n\n"
            f"坐标系约定（重要）：\n"
            f"- 使用归一化坐标，取值范围 0 到 999（含端点）\n"
            f"- (0, 0) 表示图片左上角，(999, 999) 表示图片右下角\n"
            f"- x 表示水平位置（0=最左，999=最右），y 表示垂直位置（0=最上，999=最下）\n\n"
            f"要求：\n"
            f"1. 仔细观察这个元素的可点击区域边界\n"
            f"2. 给出元素可点击区域的【正中心】坐标，不是左上角\n"
            f"3. 坐标必须是 0-999 之间的整数\n"
            f"4. 如果元素是按钮/图标，中心点要落在按钮的视觉中心，不是文字中心\n"
            f"5. x 和 y 是两个独立字段，必须分别给出，不要把 y 值漏掉或合并到 x 后面\n\n"
            f"请严格只返回 JSON，不要任何其他文字。格式（注意 x 和 y 各自独立成键）：\n"
            f'{{"found": true, "elements": [{{"name": "{target_description}", "x": 500, "y": 300, "confidence": "high|medium|low"}}]}}\n'
            f"如果没找到，返回 {{\"found\": false, \"reason\": \"...\"}}。"
        )

    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": find_instruction},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
        ],
    }]

    # 7. 调用视觉模型
    try:
        # ── 刹车：截图后、调视觉模型前再查一次，避免无谓的 API 调用 ──
        if is_aborted():
            return json.dumps({"found": False, "reason": "操作已中止：用户已点击停止", "screenshot": img_path}, ensure_ascii=False)
        logger.info("screenshot_and_find: target=%s, vision=%s, coord=%dx%d", target_description[:60], vision_name, coord_w, coord_h)
        result = vision_model.chat(messages, temperature=0.0)
        logger.info("screenshot_and_find response: %s", str(result)[:300])
    except Exception as e:
        return json.dumps({
            "found": False,
            "reason": f"视觉模型调用失败: {type(e).__name__}: {str(e)[:200]}",
            "screenshot": img_path,
        }, ensure_ascii=False)

    # 8. 解析 JSON（模型可能返回带 markdown 包裹的 JSON，要容错）
    parsed = _parse_vision_json(result)
    if parsed is None:
        return json.dumps({
            "found": False,
            "reason": f"视觉模型返回格式无法解析: {str(result)[:200]}",
            "screenshot": img_path,
            "raw_response": str(result)[:500],
        }, ensure_ascii=False)

    # 9. 坐标映射：归一化(0-999) → 原图像素坐标
    #    模型返回的是 0-999 归一化坐标，需要映射回原图物理像素坐标，
    #    供 click_element 使用。映射公式：
    #      像素 = round(归一化值 / 999 * (原图尺寸 - 1))
    #    这样 (0,0)→左上角像素，(999,999)→右下角像素，覆盖全图。
    elements = parsed.get("elements", [])
    valid_elements = []
    for el in elements:
        nx, ny = el.get("x"), el.get("y")
        if nx is None or ny is None:
            continue
        try:
            nx, ny = float(nx), float(ny)
        except (ValueError, TypeError):
            continue
        # 归一化坐标边界检查（允许少量越界，后续裁剪）
        if nx < 0 or nx > 999 or ny < 0 or ny > 999:
            logger.warning("归一化坐标越界已丢弃: (%.1f,%.1f) 应在 0-999 范围", nx, ny)
            continue
        # 映射到原图像素
        x = round(nx / 999.0 * (coord_w - 1))
        y = round(ny / 999.0 * (coord_h - 1))
        x, y = int(x), int(y)
        el["x"], el["y"] = x, y
        # 保留归一化坐标备查
        el["norm_x"], el["norm_y"] = int(nx), int(ny)
        valid_elements.append(el)
        logger.info(
            "坐标映射: 归一化(%d,%d) → 原图像素(%d,%d) [图尺寸 %dx%d]",
            int(nx), int(ny), x, y, coord_w, coord_h,
        )

    if valid_elements:
        parsed["elements"] = valid_elements
        parsed["screenshot"] = img_path
        # ⭐ 关键：附带坐标转换信息，让 click_element 知道怎么把图片像素转成逻辑像素
        parsed["coordinate_system"] = "image_pixels"
        parsed["scale_x"] = round(scale_x, 4)
        parsed["scale_y"] = round(scale_y, 4)
        parsed["logical_size"] = [logical_w, logical_h]
        parsed["image_size"] = [coord_w, coord_h]
        return json.dumps(parsed, ensure_ascii=False)
    elif parsed.get("found"):
        parsed["found"] = False
        parsed["reason"] = "找到元素但坐标全部越界，可能视觉模型估算不准"
        parsed["screenshot"] = img_path
        return json.dumps(parsed, ensure_ascii=False)
    else:
        parsed["screenshot"] = img_path
        return json.dumps(parsed, ensure_ascii=False)


@register_tool(
    name="click_element",
    description="⭐组合工具：用视觉找到元素并点击（screenshot_and_find + click_position 一步到位）。适合明确的按钮/图标。支持操作后验证与自动重试。示例：click_element('保存按钮')；click_element('6按钮', verify_change=true, max_retries=2)",
    parameters={
        "target_description": {"type": "string", "description": "【必填】要点击的元素的文字描述，如'保存按钮'、'数字7按钮'、'关闭(X)按钮'"},
        "button": {"type": "string", "description": "鼠标键：left(默认)/right/middle"},
        "clicks": {"type": "integer", "description": "点击次数，1=单击(默认) 2=双击"},
        "require_confirm": {"type": "boolean", "description": "是否需要用户确认，默认false"},
        "verify_change": {"type": "boolean", "description": "点击后是否验证屏幕发生变化(像素差异对比)，默认false"},
        "verify_description": {"type": "string", "description": "期望点击后在屏幕上出现的元素描述(如'输入框显示数字6')，用视觉模型验证"},
        "max_retries": {"type": "integer", "description": "验证失败时的最大重试次数，默认0不重试"},
        "wait_after": {"type": "number", "description": "点击后等待多少秒再验证，默认0.5"},
    },
    category="gui_vision",
    timeout=120,
)
def click_element(
    target_description: str,
    button: str = "left",
    clicks: int = 1,
    require_confirm: bool = False,
    verify_change: bool = False,
    verify_description: str = "",
    max_retries: int = 0,
    wait_after: float = 0.5,
) -> str:
    """⭐组合工具：用视觉找到元素并点击（screenshot_and_find + click_position）。

    一步到位的"看准就点"。适合明确的元素（按钮、图标）。
    支持操作后验证与自动重试（可靠性增强）。

    参数：
        target_description:  要点击的元素的文字描述，如 "保存按钮"、"关闭(X)按钮"
        button:              "left"/"right"/"middle"，默认 left
        clicks:              点击次数，1=单击 2=双击
        require_confirm:     是否需要用户确认
        verify_change:       点击后是否验证屏幕发生变化（默认 False）。
                             开启后会截图对比，若屏幕几乎无变化视为点击未生效。
        verify_description:  期望点击后在屏幕上出现的元素描述，如 "输入框显示数字6"。
                             若提供，则用视觉模型验证该元素是否出现（比单纯像素对比更准）。
        max_retries:         验证失败时的最大重试次数（默认 0 不重试）。
                             重试时会重新截图定位（防止上一次坐标偏了）。
        wait_after:          点击后等待多少秒再做验证（默认 0.5，给应用反应时间）。

    返回：执行结果，包含找到的坐标、点击结果、验证结果
    """
    import json

    # ── 刹车：用户点停止后不再执行组合操作 ──
    if is_aborted():
        return "操作已中止：用户已点击停止"

    attempt = 0
    max_attempts = 1 + max(0, int(max_retries))
    last_result = ""

    while attempt < max_attempts:
        if is_aborted():
            return "操作已中止：用户已点击停止"

        # 1) 视觉查找
        find_result = screenshot_and_find(target_description, return_all=False)
        try:
            parsed = json.loads(find_result)
        except json.JSONDecodeError:
            return f"视觉查找失败，返回非 JSON: {find_result[:300]}"

        if not parsed.get("found"):
            reason = parsed.get("reason", "未知原因")
            last_result = f"未找到元素「{target_description}」: {reason}"
            attempt += 1
            if attempt < max_attempts:
                logger.info("click_element 第%d次查找失败，重试中...", attempt)
                time.sleep(0.5)
                continue
            return last_result

        elements = parsed.get("elements", [])
        if not elements:
            last_result = f"未找到元素「{target_description}」: 元素列表为空"
            attempt += 1
            if attempt < max_attempts:
                time.sleep(0.5)
                continue
            return last_result

        el = elements[0]
        x, y = el.get("x"), el.get("y")
        confidence = el.get("confidence", "unknown")

        if x is None or y is None:
            last_result = f"找到元素但坐标无效: {el}"
            attempt += 1
            continue

        # 2) DPI 坐标转换
        coord_system = parsed.get("coordinate_system", "logical")
        scale_x = parsed.get("scale_x", 1.0)
        scale_y = parsed.get("scale_y", 1.0)

        if coord_system == "image_pixels" and (scale_x != 1.0 or scale_y != 1.0):
            click_x, click_y = _physical_to_logical(float(x), float(y), scale_x, scale_y)
            logger.info(
                "click_element DPI 转换: 图片像素(%.1f,%.1f) → 逻辑像素(%d,%d) scale=%.3fx%.3f",
                x, y, click_x, click_y, scale_x, scale_y,
            )
        else:
            click_x, click_y = int(x), int(y)

        # 3) 执行点击
        # 阶段三：用 _SuppressNested 抑制 click_position 的内部钩子，避免重复记录
        try:
            from core.workflow_tools import _SuppressNested
            with _SuppressNested():
                click_result = click_position(
                    x=click_x,
                    y=click_y,
                    button=button,
                    clicks=clicks,
                    require_confirm=require_confirm,
                )
        except ImportError:
            click_result = click_position(
                x=click_x,
                y=click_y,
                button=button,
                clicks=clicks,
                require_confirm=require_confirm,
            )

        # 4) 不需要验证 → 直接返回
        if not verify_change and not verify_description:
            _ret = (
                f"找到「{target_description}」于图片坐标 ({x},{y})"
                + (f" → 逻辑坐标 ({click_x},{click_y}) [DPI缩放 {scale_x:.2f}x{scale_y:.2f}]" if coord_system == "image_pixels" and scale_x != 1.0 else "")
                + f" [置信度:{confidence}]\n"
                f"点击结果: {click_result}"
            )
            # 阶段三钩子：记录 click_element（含缓存坐标）
            try:
                from core.workflow_tools import _notify_workflow
                _notify_workflow(
                    "click_element",
                    {"target_description": target_description, "button": button, "clicks": clicks},
                    cached_coordinates={"x": click_x, "y": click_y},
                    result=_ret,
                )
            except Exception:
                pass
            return _ret

        # 5) 需要验证 → 等待 + 截图验证
        time.sleep(max(0.1, wait_after))
        verify_ok, verify_msg = _verify_click_effect(
            verify_change=verify_change,
            verify_description=verify_description,
            pre_click_img=parsed.get("screenshot", ""),
        )

        attempt_num = attempt + 1
        header = (
            f"[第{attempt_num}/{max_attempts}次] 找到「{target_description}」于图片坐标 ({x},{y})"
            + (f" → 逻辑坐标 ({click_x},{click_y})" if coord_system == "image_pixels" and scale_x != 1.0 else "")
            + f" [置信度:{confidence}]\n"
            f"点击结果: {click_result}\n"
            f"验证结果: {verify_msg}"
        )

        if verify_ok:
            _ret2 = header + "\n✅ 点击已生效"
            # 阶段三钩子：记录 click_element（含缓存坐标）
            try:
                from core.workflow_tools import _notify_workflow
                _notify_workflow(
                    "click_element",
                    {"target_description": target_description, "button": button, "clicks": clicks,
                     "verify_change": verify_change, "verify_description": verify_description},
                    cached_coordinates={"x": click_x, "y": click_y},
                    result=_ret2,
                )
            except Exception:
                pass
            return _ret2

        # 验证失败
        last_result = header + "\n⚠️ 验证未通过"
        attempt += 1
        if attempt < max_attempts:
            logger.info("click_element 验证失败，准备第%d次重试...", attempt + 1)
            time.sleep(0.5)
            continue

    return last_result + "\n（已达到最大重试次数）"


def _verify_click_effect(
    verify_change: bool,
    verify_description: str,
    pre_click_img: str = "",
) -> tuple[bool, str]:
    """验证点击是否生效。

    两种验证方式（可组合）：
    - verify_change: 截图前后像素差异对比，差异大于阈值视为屏幕有变化
    - verify_description: 用视觉模型检查期望元素是否出现在屏幕上

    返回 (是否通过, 说明文字)
    """
    # 截一张点击后的图
    post_img = screenshot_screen()
    if post_img.startswith("错误"):
        return False, f"验证截图失败: {post_img}"

    # 1) 像素差异对比（轻量，不调视觉模型）
    # 阈值 0.001 (0.1%)：很多应用点击后只有局部小变化（如计算器显示区刷新、
    # 输入框光标闪烁），全屏像素差异可能只有 0.1%~0.3%。
    # 0.5% 阈值过严，会把已生效的点击误判为失败（假阴性）。
    PIXEL_DIFF_THRESHOLD = 0.001
    if verify_change and pre_click_img and os.path.exists(pre_click_img):
        try:
            diff_ratio = _image_diff_ratio(pre_click_img, post_img)
            if diff_ratio < PIXEL_DIFF_THRESHOLD:
                logger.info("verify: 像素差异 %.4f < 阈值 %.4f，判定无变化", diff_ratio, PIXEL_DIFF_THRESHOLD)
                # 即便像素差异小，也可能只是局部文字变化，再用视觉模型验一下
                if not verify_description:
                    return False, f"屏幕几乎无变化（差异 {diff_ratio*100:.3f}% < {PIXEL_DIFF_THRESHOLD*100:.1f}%），点击可能未生效"
            else:
                logger.info("verify: 像素差异 %.4f ≥ 阈值 %.4f，屏幕有变化", diff_ratio, PIXEL_DIFF_THRESHOLD)
                if not verify_description:
                    return True, f"屏幕已变化（差异 {diff_ratio*100:.3f}%）"
        except Exception as e:
            logger.warning("像素差异对比失败: %s，降级到视觉验证", e)

    # 2) 视觉模型验证期望元素
    if verify_description:
        vision_model, vision_name = _get_vision_model()
        if vision_model is None:
            return True, "未配置视觉模型，跳过语义验证；截图已保存: " + post_img

        import base64 as _b64
        try:
            with open(post_img, "rb") as f:
                img_bytes = f.read()
            # 压缩
            try:
                from PIL import Image as _PIL
                import io as _io
                pil = _PIL.open(post_img)
                w, h = pil.size
                if max(w, h) > 1280:
                    s = 1280 / max(w, h)
                    pil = pil.resize((int(w * s), int(h * s)))
                buf = _io.BytesIO()
                pil.save(buf, format="PNG")
                img_bytes = buf.getvalue()
            except Exception:
                pass
            img_b64 = _b64.b64encode(img_bytes).decode("ascii")

            verify_prompt = (
                f"请判断这张屏幕截图中是否出现了以下内容：「{verify_description}」。\n"
                f"只需回答 JSON：{{\"present\": true/false, \"reason\": \"简要说明\"}}"
            )
            messages = [{
                "role": "user",
                "content": [
                    {"type": "text", "text": verify_prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
                ],
            }]
            resp = vision_model.chat(messages, temperature=0.0)
            logger.info("verify 视觉验证: %s", str(resp)[:200])
            parsed_v = _parse_vision_json(resp)
            if parsed_v and "present" in parsed_v:
                present = bool(parsed_v.get("present"))
                reason = parsed_v.get("reason", "")
                return present, f"视觉验证({vision_name}): {'已出现' if present else '未出现'} - {reason}"
            else:
                return False, f"视觉验证返回无法解析: {str(resp)[:150]}"
        except Exception as e:
            return False, f"视觉验证调用失败: {type(e).__name__}: {e}"

    return True, "验证通过（未指定具体验证项）"


def _image_diff_ratio(img_path_a: str, img_path_b: str) -> float:
    """计算两张图的像素差异比例（0.0~1.0）。

    用于快速判断点击前后屏幕是否变化。
    为加速，将图缩放到 320 长边再比较。
    """
    from PIL import Image
    import math

    def _load_small(p: str) -> Image.Image:
        im = Image.open(p).convert("RGB")
        w, h = im.size
        target = 320
        if max(w, h) > target:
            s = target / max(w, h)
            im = im.resize((int(w * s), int(h * s)))
        return im

    a = _load_small(img_path_a)
    b = _load_small(img_path_b)
    if a.size != b.size:
        # 尺寸不同说明屏幕分辨率变了，直接判为有变化
        return 1.0

    pa = a.load()
    pb = b.load()
    w, h = a.size
    diff_pixels = 0
    threshold = 30  # 单通道差异阈值
    for y in range(h):
        for x in range(w):
            ra, ga, ba = pa[x, y]
            rb, gb, bb = pb[x, y]
            if abs(ra - rb) > threshold or abs(ga - gb) > threshold or abs(ba - bb) > threshold:
                diff_pixels += 1
    return diff_pixels / (w * h)


@register_tool(
    name="wait_for_element",
    description="⭐智能等待：每隔一段时间截图查找目标元素，直到找到或超时。用于'等应用加载完成后再操作'，避免应用没加载完就急着找按钮导致失败。示例：wait_for_element('新建按钮', timeout=15)",
    parameters={
        "target_description": {"type": "string", "description": "【必填】要等待出现的元素描述，如'登录按钮'、'加载完成的页面'、'标准模式标签'"},
        "timeout": {"type": "number", "description": "最大等待秒数，默认10"},
        "interval": {"type": "number", "description": "每次重试间隔秒数，默认1"},
    },
    category="gui_vision",
    timeout=60,
)
def wait_for_element(
    target_description: str,
    timeout: float = 10.0,
    interval: float = 1.0,
) -> str:
    """⭐智能等待：每隔一段时间截图查找目标元素，直到找到或超时。

    用于"等应用加载完成后再操作"的场景。例如打开 WPS 后等待"新建"按钮出现，
    避免应用没加载完就急着截图找按钮导致失败。

    参数：
        target_description: 要等待出现的元素描述，如 "登录按钮"、"加载完成的页面"
        timeout:            最大等待秒数（默认 10）
        interval:           每次重试间隔秒数（默认 1）

    返回：找到则返回 JSON 坐标信息（同 screenshot_and_find）；
          超时则返回未找到的提示。
    """
    import json

    if is_aborted():
        return "操作已中止：用户已点击停止"

    deadline = time.time() + max(0.5, timeout)
    interval = max(0.3, interval)
    attempt = 0

    while time.time() < deadline:
        if is_aborted():
            return "操作已中止：用户已点击停止"

        attempt += 1
        remaining = max(0, deadline - time.time())
        logger.info(
            "wait_for_element 第%d次尝试 (剩余 %.1fs): %s",
            attempt, remaining, target_description[:60],
        )

        result = screenshot_and_find(target_description, return_all=False)
        try:
            parsed = json.loads(result)
            if parsed.get("found") and parsed.get("elements"):
                el = parsed["elements"][0]
                _wf_ret = json.dumps({
                    "found": True,
                    "waited_seconds": round(time.time() - (deadline - timeout), 2),
                    "attempts": attempt,
                    "element": el,
                    "screenshot": parsed.get("screenshot", ""),
                }, ensure_ascii=False)
                # 阶段三钩子：记录 wait_for_element
                try:
                    from core.workflow_tools import _notify_workflow
                    _notify_workflow(
                        "wait_for_element",
                        {"target_description": target_description, "timeout": timeout, "interval": interval},
                        result=_wf_ret,
                    )
                except Exception:
                    pass
                return _wf_ret
        except json.JSONDecodeError:
            pass

        if time.time() + interval < deadline:
            time.sleep(interval)

    return json.dumps({
        "found": False,
        "reason": f"等待 {timeout}s 超时，未出现「{target_description}」",
        "attempts": attempt,
    }, ensure_ascii=False)


# ────────────────────────────────────────────────────────────
# 辅助：解析视觉模型返回的 JSON（容错 markdown 包裹）
# ────────────────────────────────────────────────────────────

def _parse_vision_json(text: str) -> dict | None:
    """从视觉模型的返回文本中提取 JSON 对象。

    模型有时会把 JSON 包在 ```json ... ``` 里，或前后带解释文字，
    这里做容错提取。

    返回：解析后的 dict，失败返回 None
    """
    import json
    import re

    if not text:
        return None

    text = text.strip()

    # 0. 修复模型偶尔漏写 "y": 键名的破损 JSON
    #    例如 {"x": 302, 731} → {"x": 302, "y": 731}
    #    只匹配 "x": <数字>, <裸数字> 这种特定模式，不会误伤正常 JSON
    fix_y_pattern = re.compile(r'("x"\s*:\s*\d+)\s*,\s*(\d+)')
    text = fix_y_pattern.sub(r'\1, "y": \2', text)

    # 1. 直接尝试解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 2. 去除 markdown 代码块包裹
    code_block_pattern = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
    m = code_block_pattern.search(text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # 3. 提取第一个 {...} 块
    brace_pattern = re.compile(r"\{[^{}]*\}", re.DOTALL)
    # 先尝试非嵌套的花括号块
    for m in brace_pattern.finditer(text):
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            continue

    # 4. 尝试提取嵌套的花括号块（贪婪扩展）
    nested_pattern = re.compile(r"\{.*\}", re.DOTALL)
    m = nested_pattern.search(text)
    if m:
        candidate = m.group(0)
        # 可能含多个对象，逐个尝试
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            # 尝试找第一个完整对象
            depth = 0
            start = -1
            for i, ch in enumerate(candidate):
                if ch == "{":
                    if depth == 0:
                        start = i
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0 and start >= 0:
                        try:
                            return json.loads(candidate[start:i+1])
                        except json.JSONDecodeError:
                            start = -1
                            continue
        except Exception:
            pass

    return None


# ────────────────────────────────────────────────────────────
# 窗口管理（阶段二，使用 win32gui + uiautomation）
# ────────────────────────────────────────────────────────────

def _enum_windows(include_minimized: bool = True) -> list[dict]:
    """枚举所有顶级窗口，返回窗口信息列表（内部辅助）。

    返回每个窗口的 dict：{title, hwnd, pid, is_visible, is_minimized, rect}
    rect = (left, top, right, bottom)
    """
    try:
        import win32gui
        import win32process
        import win32con
    except ImportError:
        return []

    windows: list[dict] = []

    def _callback(hwnd, _):
        # 只收可见窗口或有标题的窗口
        title = win32gui.GetWindowText(hwnd)
        if not title:
            return
        if not include_minimized and not win32gui.IsWindowVisible(hwnd):
            return

        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
        except Exception:
            pid = 0

        is_visible = bool(win32gui.IsWindowVisible(hwnd))
        is_minimized = bool(win32gui.IsIconic(hwnd)) if is_visible else False

        try:
            rect = win32gui.GetWindowRect(hwnd)  # (l, t, r, b)
        except Exception:
            rect = (0, 0, 0, 0)

        windows.append({
            "title": title,
            "hwnd": hwnd,
            "pid": pid,
            "is_visible": is_visible,
            "is_minimized": is_minimized,
            "rect": list(rect),
        })

    try:
        win32gui.EnumWindows(_callback, None)
    except Exception as e:
        logger.warning("EnumWindows 失败: %s", e)

    return windows


@register_tool(
    name="list_windows",
    description="列出当前所有可见窗口（含标题、句柄、进程ID、状态）。示例：list_windows() 列出所有窗口",
    parameters={
        "include_minimized": {"type": "boolean", "description": "是否包含最小化的窗口，默认true"},
    },
    category="gui_window",
    timeout=30,
)
def list_windows(include_minimized: bool = True) -> str:
    """列出当前所有可见窗口。

    参数：
        include_minimized: 是否包含最小化的窗口，默认 True

    返回：窗口列表 JSON 字符串
        [{"title": "记事本", "hwnd": 12345, "pid": 678, "is_minimized": false, "rect": [100,100,800,600]}]
    """
    import json

    wins = _enum_windows(include_minimized=include_minimized)
    if not wins:
        return "错误：无法枚举窗口（可能未安装 pywin32，或当前非 Windows 桌面环境）"

    # 过滤掉空标题、只保留有意义的窗口
    meaningful = [w for w in wins if w.get("title") and w.get("is_visible")]
    if not meaningful:
        return "当前没有可见窗口"

    # 按 hwnd 序排序（基本对应 Z-order）
    meaningful.sort(key=lambda w: w.get("hwnd", 0), reverse=True)

    result = [
        {
            "title": w["title"],
            "hwnd": w["hwnd"],
            "pid": w["pid"],
            "is_minimized": w["is_minimized"],
            "rect": {"left": w["rect"][0], "top": w["rect"][1], "right": w["rect"][2], "bottom": w["rect"][3]},
        }
        for w in meaningful
    ]
    return json.dumps(result, ensure_ascii=False)


@register_tool(
    name="find_window",
    description="按标题查找窗口（支持模糊匹配）。示例：find_window('记事本') 查找标题含'记事本'的窗口",
    parameters={
        "title": {"type": "string", "description": "窗口标题或标题的一部分"},
        "exact": {"type": "boolean", "description": "true=精确匹配，false=包含匹配(默认)"},
    },
    category="gui_window",
    timeout=30,
)
def find_window(title: str, exact: bool = False) -> str:
    """按标题查找窗口。

    参数：
        title: 窗口标题（或标题的一部分）
        exact: True=精确匹配，False=包含匹配（默认）

    返回：匹配的窗口列表 JSON，或未找到提示
    """
    import json

    if not title:
        return "错误：title 不能为空"

    wins = _enum_windows(include_minimized=True)
    if not wins:
        return "错误：无法枚举窗口"

    matched = []
    for w in wins:
        if not w.get("title"):
            continue
        if exact:
            if w["title"] == title:
                matched.append(w)
        else:
            if title in w["title"]:
                matched.append(w)

    if not matched:
        return f'未找到标题含「{title}」的窗口'

    result = [
        {
            "title": w["title"],
            "hwnd": w["hwnd"],
            "pid": w["pid"],
            "is_minimized": w["is_minimized"],
        }
        for w in matched
    ]
    return json.dumps(result, ensure_ascii=False)


@register_tool(
    name="focus_window",
    description="激活指定窗口到前台。可通过title或hwnd指定。示例：focus_window('记事本') 激活记事本；focus_window('', 12345) 用句柄激活",
    parameters={
        "title": {"type": "string", "description": "窗口标题（模糊匹配），与hwnd二选一"},
        "hwnd": {"type": "integer", "description": "窗口句柄(数字)，优先级高于title"},
    },
    category="gui_window",
    timeout=30,
)
def focus_window(title: str = "", hwnd: int = 0) -> str:
    """激活指定窗口到前台。可通过 title 或 hwnd 指定。

    参数：
        title: 窗口标题（模糊匹配），与 hwnd 二选一
        hwnd:  窗口句柄（数字），优先级高于 title

    返回：执行结果字符串
    """
    try:
        import win32gui
        import win32con
    except ImportError:
        return "错误：未安装 pywin32，无法操作窗口"

    target_hwnd = None
    target_title = ""

    if hwnd:
        # 优先用 hwnd
        try:
            target_hwnd = int(hwnd)
            target_title = win32gui.GetWindowText(target_hwnd)
            if not target_title:
                return f"错误：hwnd={hwnd} 对应的窗口不存在"
        except Exception as e:
            return f"错误：无效的 hwnd={hwnd}: {e}"
    elif title:
        wins = _enum_windows(include_minimized=True)
        for w in wins:
            if w.get("title") and title in w["title"]:
                target_hwnd = w["hwnd"]
                target_title = w["title"]
                break
        if target_hwnd is None:
            return f'未找到标题含「{title}」的窗口'
    else:
        return "错误：必须提供 title 或 hwnd 参数"

    try:
        # 如果窗口最小化，先恢复
        if win32gui.IsIconic(target_hwnd):
            win32gui.ShowWindow(target_hwnd, win32con.SW_RESTORE)

        # 置顶并激活
        win32gui.SetForegroundWindow(target_hwnd)
        logger.info("focus_window: hwnd=%d title=%s", target_hwnd, target_title)
        return f"已激活窗口: 「{target_title}」(hwnd={target_hwnd})"
    except Exception as e:
        return f"错误：激活窗口失败: {type(e).__name__}: {e}"


@register_tool(
    name="close_window",
    description="⚠关闭指定窗口（会弹窗让用户确认）。可通过title或hwnd指定。示例：close_window('记事本') 优雅关闭；close_window('记事本', force=true) 强制结束进程",
    parameters={
        "title": {"type": "string", "description": "窗口标题（模糊匹配），与hwnd二选一"},
        "hwnd": {"type": "integer", "description": "窗口句柄(数字)，优先级高于title"},
        "force": {"type": "boolean", "description": "true=强制taskkill，false=发WM_CLOSE优雅关闭(默认)"},
    },
    category="gui_window",
    timeout=30,
)
def close_window(title: str = "", hwnd: int = 0, force: bool = False) -> str:
    """关闭指定窗口。⚠ 高安全等级，默认会弹窗让用户确认。

    参数：
        title: 窗口标题（模糊匹配），与 hwnd 二选一
        hwnd:  窗口句柄（数字），优先级高于 title
        force: True=强制结束进程（taskkill），False=发 WM_CLOSE 优雅关闭（默认）

    返回：执行结果字符串
    """
    import json

    # ── 刹车：用户点停止后不再关闭窗口 ──
    if is_aborted():
        return "操作已中止：用户已点击停止"

    # 先确认操作（高安全等级）
    decision = confirm_dangerous_action(
        f"关闭窗口「{title or hwnd}」" + ("（强制结束进程）" if force else ""),
        "force=True 会用 taskkill 直接结束进程，可能丢失未保存数据",
    )
    if decision != "confirmed":
        return "操作已取消（用户拒绝）"

    try:
        import win32gui
        import win32con
    except ImportError:
        return "错误：未安装 pywin32"

    target_hwnd = None
    target_title = ""
    target_pid = 0

    if hwnd:
        try:
            target_hwnd = int(hwnd)
            target_title = win32gui.GetWindowText(target_hwnd)
            if not target_title:
                return f"错误：hwnd={hwnd} 对应的窗口不存在"
        except Exception as e:
            return f"错误：无效的 hwnd={hwnd}: {e}"
    elif title:
        wins = _enum_windows(include_minimized=True)
        for w in wins:
            if w.get("title") and title in w["title"]:
                target_hwnd = w["hwnd"]
                target_title = w["title"]
                target_pid = w.get("pid", 0)
                break
        if target_hwnd is None:
            return f'未找到标题含「{title}」的窗口'
    else:
        return "错误：必须提供 title 或 hwnd 参数"

    try:
        if force:
            # 强制结束进程
            if target_pid == 0:
                try:
                    import win32process
                    _, target_pid = win32process.GetWindowThreadProcessId(target_hwnd)
                except Exception:
                    pass
            if target_pid == 0:
                return f"错误：无法获取窗口 {target_title} 的进程ID"

            import subprocess
            result = subprocess.run(
                ["taskkill", "/PID", str(target_pid), "/F"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                logger.info("close_window force: hwnd=%d pid=%d title=%s", target_hwnd, target_pid, target_title)
                return f"已强制结束窗口「{target_title}」(pid={target_pid})"
            else:
                return f"强制结束失败: {result.stderr.strip() or result.stdout.strip()}"
        else:
            # 优雅关闭：发送 WM_CLOSE
            win32gui.PostMessage(target_hwnd, win32con.WM_CLOSE, 0, 0)
            logger.info("close_window: hwnd=%d title=%s", target_hwnd, target_title)
            return f"已发送关闭指令给窗口「{target_title}」(hwnd={target_hwnd})"
    except Exception as e:
        return f"错误：关闭窗口失败: {type(e).__name__}: {e}"


@register_tool(
    name="get_window_info",
    description="获取窗口详情（位置、大小、状态、进程名）。可通过title或hwnd指定。示例：get_window_info('记事本')",
    parameters={
        "title": {"type": "string", "description": "窗口标题（模糊匹配），与hwnd二选一"},
        "hwnd": {"type": "integer", "description": "窗口句柄(数字)，优先级高于title"},
    },
    category="gui_window",
    timeout=30,
)
def get_window_info(title: str = "", hwnd: int = 0) -> str:
    """获取窗口详情（位置、大小、状态、所属进程）。

    参数：
        title: 窗口标题（模糊匹配），与 hwnd 二选一
        hwnd:  窗口句柄（数字），优先级高于 title

    返回：窗口详情 JSON 字符串
    """
    import json

    try:
        import win32gui
        import win32process
        import win32con
        import win32api
    except ImportError:
        return "错误：未安装 pywin32"

    target_hwnd = None

    if hwnd:
        try:
            target_hwnd = int(hwnd)
        except Exception as e:
            return f"错误：无效的 hwnd={hwnd}: {e}"
    elif title:
        wins = _enum_windows(include_minimized=True)
        for w in wins:
            if w.get("title") and title in w["title"]:
                target_hwnd = w["hwnd"]
                break
        if target_hwnd is None:
            return f'未找到标题含「{title}」的窗口'
    else:
        return "错误：必须提供 title 或 hwnd 参数"

    try:
        title_str = win32gui.GetWindowText(target_hwnd)
        if not title_str:
            return f"错误：hwnd={target_hwnd} 对应的窗口不存在或已关闭"

        rect = win32gui.GetWindowRect(target_hwnd)  # (l, t, r, b)
        _, pid = win32process.GetWindowThreadProcessId(target_hwnd)
        is_visible = bool(win32gui.IsWindowVisible(target_hwnd))
        is_minimized = bool(win32gui.IsIconic(target_hwnd))
        is_maximized = False
        try:
            placement = win32gui.GetWindowPlacement(target_hwnd)
            # placement: (flags, showCmd, minPos, maxPos, normalRect)
            # showCmd: 1=正常, 2=最小化, 3=最大化
            is_maximized = (placement[1] == 3)
        except Exception:
            pass

        # 尝试获取进程名
        process_name = ""
        try:
            import subprocess
            r = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0 and r.stdout.strip():
                # 输出格式: "进程名","PID","会话名","会话#","内存"
                process_name = r.stdout.strip().split(",")[0].strip('"')
        except Exception:
            pass

        info = {
            "title": title_str,
            "hwnd": target_hwnd,
            "pid": pid,
            "process_name": process_name,
            "is_visible": is_visible,
            "is_minimized": is_minimized,
            "is_maximized": is_maximized,
            "rect": {
                "left": rect[0],
                "top": rect[1],
                "right": rect[2],
                "bottom": rect[3],
                "width": rect[2] - rect[0],
                "height": rect[3] - rect[1],
            },
        }
        return json.dumps(info, ensure_ascii=False)
    except Exception as e:
        return f"错误：获取窗口信息失败: {type(e).__name__}: {e}"
