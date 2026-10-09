"""UI Automation 工具集 —— 桌面自动化的「第一级」精确定位。

## 为什么要做这一层

现有桌面自动化只有一条路：截图 → 视觉模型（Qwen-VL）→ 返回坐标 → 点鼠标。
这条路的每一步都要 1~5 秒，还会烧掉大量视觉 token；再叠加 DPI 缩放、分辨率、
窗口位置变化，稳定性也差。典型症状就是「让 agent 打开 WPS 点一个菜单」要绕很久。

而 Windows 的 UI Automation（UIA）把界面上每个控件都暴露成**带名字的结构化对象**：
按钮叫什么、在哪、能不能 Invoke、输入框里现在是什么文本，都能毫秒级直接读到，
不需要截图、不需要视觉模型，也不受 DPI / 分辨率影响。

## 三级降级策略（本模块是第一级）

    ① UI Automation（本模块 uia_*）   —— 控件树精确定位，最快最稳，优先走这条
            ↓ 找不到控件（自定义绘制界面 / 无 UIA 支持）
    ② 浏览器（puppeteer MCP）          —— 网页内操作走 DOM
            ↓ 都不是
    ③ 视觉 + 坐标点击（core/gui_tools.py）—— 万能兜底，慢但覆盖面最广

依赖：``uiautomation``（requirements.txt 已包含）、``pywin32``（已包含）。

注意：本模块所有函数都会被 ``nanobot/adapter_tools.py`` 动态 import 并注册为工具，
函数签名与返回值（str）必须符合 HuanzhenTool 的约定；schema 参数名要与函数签名一致，
否则 ``tests/test_tool_schemas.py`` 会失败。
"""

from __future__ import annotations

import contextlib
import json
import sys
import time

from core.gui_abort import is_aborted
from core.logger import setup_logger
from core.tools import register_tool

logger = setup_logger("huanzhen.uia")

_IS_WINDOWS = sys.platform == "win32"

_MISSING_UIA = (
    "错误：未安装 uiautomation，无法使用控件树定位。请执行："
    "venv\\Scripts\\python.exe -m pip install uiautomation"
)

# 控件类型：中文/英文别名 → uiautomation.ControlType 的成员名
_CONTROL_TYPE_ALIASES = {
    "button": "ButtonControl", "按钮": "ButtonControl",
    "edit": "EditControl", "输入框": "EditControl", "编辑框": "EditControl", "文本框": "EditControl",
    "text": "TextControl", "文本": "TextControl", "文字": "TextControl",
    "menuitem": "MenuItemControl", "menuitemcontrol": "MenuItemControl", "菜单项": "MenuItemControl", "菜单": "MenuItemControl",
    "menu": "MenuControl", "menubar": "MenuBarControl", "菜单栏": "MenuBarControl",
    "window": "WindowControl", "窗口": "WindowControl",
    "document": "DocumentControl", "文档": "DocumentControl",
    "listitem": "ListItemControl", "列表项": "ListItemControl",
    "list": "ListControl", "列表": "ListControl",
    "treeitem": "TreeItemControl", "树节点": "TreeItemControl",
    "tabitem": "TabItemControl", "选项卡": "TabItemControl", "标签页": "TabItemControl",
    "checkbox": "CheckBoxControl", "复选框": "CheckBoxControl", "勾选框": "CheckBoxControl",
    "radiobutton": "RadioButtonControl", "单选框": "RadioButtonControl",
    "combobox": "ComboBoxControl", "下拉框": "ComboBoxControl",
    "pane": "PaneControl", "面板": "PaneControl",
    "hyperlink": "HyperlinkControl", "链接": "HyperlinkControl",
    "toolbar": "ToolBarControl", "工具栏": "ToolBarControl",
    "image": "ImageControl", "图片": "ImageControl",
    "titlebar": "TitleBarControl", "标题栏": "TitleBarControl",
    "splitbutton": "SplitButtonControl", "下拉按钮": "SplitButtonControl",
    "datetimepicker": "DateTimePickerControl",
    "spinner": "SpinnerControl",
    "scrollbar": "ScrollBarControl",
    "statusbar": "StatusBarControl",
    "tooltip": "ToolTipControl",
}

# UIA 全局搜索超时：避免控件树巨大时单次搜索卡死
_SEARCH_TIMEOUT = 3.0


# ────────────────────────────────────────────────────────────
# uiautomation 会话（COM 初始化）
# ────────────────────────────────────────────────────────────

@contextlib.contextmanager
def _uia_session():
    """在当前线程初始化 UI Automation 并返回 ``uiautomation`` 模块。

    uiautomation 基于 comtypes，跨线程调用必须先做 COM 初始化。工具实际执行在
    ``asyncio.to_thread`` 的工作线程里，因此每次调用都走一次会话，避免
    "第一次能用、第二次报 COM 未初始化" 这类偶发问题。
    """
    try:
        import uiautomation as auto
    except ImportError:
        raise RuntimeError(_MISSING_UIA)

    init_ctx = getattr(auto, "UIAutomationInitializerInThread", None)
    if init_ctx is not None:
        with init_ctx():
            _configure(auto)
            yield auto
        return

    # 老版本没有该上下文管理器：手动初始化 COM
    import comtypes
    comtypes.CoInitialize()
    try:
        _configure(auto)
        yield auto
    finally:
        try:
            comtypes.CoUninitialize()
        except Exception:
            pass


def _configure(auto) -> None:
    try:
        auto.SetGlobalSearchTimeout(_SEARCH_TIMEOUT)
    except Exception:
        pass


# ────────────────────────────────────────────────────────────
# 内部工具
# ────────────────────────────────────────────────────────────

def _control_type_id(auto, text: str):
    """把用户给的类型字符串（中/英）解析成 UIA ControlType 常量，失败返回 None。"""
    key = (text or "").strip().lower().replace(" ", "")
    member = _CONTROL_TYPE_ALIASES.get(key)
    if member is None:
        # 允许直接传 "ButtonControl" / "buttoncontrol"
        member = text.strip() if text.strip().endswith("Control") else None
    if not member:
        return None
    ctype = getattr(auto, "ControlType", None)
    if ctype is None:
        return None
    return getattr(ctype, member, None)


def _attr(ctrl, name: str, default=""):
    try:
        value = getattr(ctrl, name)
    except Exception:
        return default
    if value is None:
        return default
    return value


def _bbox(ctrl) -> dict:
    try:
        r = ctrl.BoundingRectangle
        return {
            "left": int(r.left), "top": int(r.top),
            "right": int(r.right), "bottom": int(r.bottom),
            "width": int(r.width()), "height": int(r.height()),
        }
    except Exception:
        return {}


def _describe(ctrl) -> dict:
    return {
        "name": str(_attr(ctrl, "Name")),
        "control_type": str(_attr(ctrl, "ControlTypeName")),
        "automation_id": str(_attr(ctrl, "AutomationId")),
        "class_name": str(_attr(ctrl, "ClassName")),
        "bbox": _bbox(ctrl),
    }


def _foreground_window(auto):
    """取当前前台窗口控件；失败则退回桌面根控件。"""
    try:
        import win32gui
        hwnd = win32gui.GetForegroundWindow()
        if hwnd:
            ctrl = auto.ControlFromHandle(hwnd)
            if ctrl is not None:
                return ctrl
    except Exception as e:
        logger.debug("获取前台窗口失败: %s", e)
    return auto.GetRootControl()


def _resolve_window(auto, window: str, hwnd: int):
    """按 hwnd / 标题 / 前台窗口 解析出目标窗口控件。返回 None 表示未找到。"""
    if hwnd:
        try:
            ctrl = auto.ControlFromHandle(int(hwnd))
            if ctrl is not None:
                return ctrl
        except Exception as e:
            logger.warning("按 hwnd=%s 取窗口失败: %s", hwnd, e)
            return None

    if window and window.strip():
        key = window.strip().lower()
        try:
            children = auto.GetRootControl().GetChildren()
        except Exception:
            children = []
        # 1) 精确标题
        for child in children:
            if str(_attr(child, "Name")) == window.strip():
                return child
        # 2) 包含匹配
        for child in children:
            if key in str(_attr(child, "Name")).lower():
                return child
        # 3) 交给 UIA 搜索兜底
        try:
            ctrl = auto.WindowControl(searchDepth=1, SubName=window.strip())
            if ctrl.Exists(1.0, 0.25):
                return ctrl
        except Exception:
            pass
        return None

    return _foreground_window(auto)


def _find_control(auto, scope, name, control_type, automation_id, exact, search_depth, timeout):
    """在 ``scope`` 内查找控件；带超时轮询。返回控件对象或 None。"""
    kwargs = {"searchDepth": max(1, int(search_depth or 8))}
    if automation_id:
        kwargs["AutomationId"] = automation_id
    if name:
        kwargs["Name" if exact else "SubName"] = name
    if control_type:
        cid = _control_type_id(auto, control_type)
        if cid is not None:
            kwargs["ControlType"] = cid
        else:
            logger.debug("未识别的控件类型: %s（将忽略该过滤条件）", control_type)

    try:
        ctrl = scope.Control(**kwargs)
    except Exception as e:
        logger.warning("控件搜索构造失败: %s", e)
        return None

    wait = max(0.0, float(timeout or 0))
    try:
        if ctrl.Exists(wait, 0.25):
            return ctrl
    except Exception as e:
        logger.warning("控件存在性检查失败: %s", e)
    return None


def _top_hwnd(ctrl) -> int:
    try:
        top = ctrl.GetTopLevelControl()
        return int(getattr(top, "NativeWindowHandle", 0) or 0)
    except Exception:
        return 0


def _bring_to_front(hwnd: int) -> None:
    if not hwnd:
        return
    try:
        import win32con
        import win32gui
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
    except Exception as e:
        logger.debug("置前窗口 %s 失败: %s", hwnd, e)


def _do_click(ctrl, clicks: int, prefer_invoke: bool) -> str:
    """优先 InvokePattern（不移动鼠标、后台可用），否则退回鼠标模拟。"""
    top = _top_hwnd(ctrl)
    if top:
        _bring_to_front(top)

    if prefer_invoke and int(clicks) <= 1:
        try:
            pattern = ctrl.GetInvokePattern()
            if pattern is not None:
                pattern.Invoke()
                return "InvokePattern（后台调用，未移动鼠标）"
        except Exception as e:
            logger.debug("InvokePattern 不可用，退回鼠标点击: %s", e)

    try:
        ctrl.SetFocus()
    except Exception:
        pass

    try:
        if int(clicks) >= 2:
            ctrl.DoubleClick()
            return "鼠标双击模拟"
        ctrl.Click()
        return "鼠标单击模拟"
    except Exception as e:
        return f"点击失败: {type(e).__name__}: {e}"


# ────────────────────────────────────────────────────────────
# 工具：导出控件树
# ────────────────────────────────────────────────────────────

@register_tool(
    name="uia_dump_tree",
    description=(
        "⭐UI Automation 控件树：导出指定窗口内所有控件的层级结构"
        "（名称 / 类型 / AutomationId / 位置）。"
        "桌面自动化第一步——先看清窗口里有哪些控件，再用 uia_click_element / uia_set_text 精确操作。"
        "比截图+视觉模型快几十倍，且能读到控件真实名称。"
        "示例：uia_dump_tree('WPS Office')；uia_dump_tree(hwnd=123456, depth=4)"
    ),
    parameters={
        "window": {"type": "string", "description": "窗口标题（模糊匹配），与 hwnd 二选一；都为空则用当前前台窗口"},
        "hwnd": {"type": "integer", "description": "窗口句柄（open_application / list_windows 返回），优先级高于 window"},
        "depth": {"type": "integer", "description": "递归深度，默认3，最大6"},
        "max_nodes": {"type": "integer", "description": "最多导出多少节点，默认200"},
        "name_filter": {"type": "string", "description": "只显示名称包含该关键词的节点（可选）"},
    },
    category="gui_uia",
    timeout=60,
)
def uia_dump_tree(
    window: str = "",
    hwnd: int = 0,
    depth: int = 3,
    max_nodes: int = 200,
    name_filter: str = "",
) -> str:
    if not _IS_WINDOWS:
        return "错误：UI Automation 仅支持 Windows。"

    if is_aborted():
        return "操作已中止：用户已点击停止"

    max_depth = min(max(1, int(depth or 3)), 6)
    limit = min(max(1, int(max_nodes or 200)), 600)
    keyword = (name_filter or "").strip().lower()

    try:
        with _uia_session() as auto:
            win = _resolve_window(auto, window, hwnd)
            if win is None:
                return f"错误：未找到窗口「{window}」"
            win_info = _describe(win)

            lines: list[str] = []
            named = 0

            def walk(ctrl, level: int) -> None:
                nonlocal named
                if len(lines) >= limit:
                    return
                try:
                    children = ctrl.GetChildren()
                except Exception:
                    return
                for child in children:
                    if len(lines) >= limit:
                        return
                    info = _describe(child)
                    matched = (
                        not keyword
                        or keyword in info["name"].lower()
                        or keyword in info["automation_id"].lower()
                    )
                    if matched:
                        if info["name"] or info["automation_id"]:
                            named += 1
                        bb = info["bbox"]
                        node = (
                            f"{'  ' * level}- {info['control_type'] or '?'} "
                            f"name={info['name']!r} id={info['automation_id']!r}"
                        )
                        if bb:
                            node += f" bbox=({bb.get('left')},{bb.get('top')},{bb.get('width')}x{bb.get('height')})"
                        lines.append(node)
                    if level + 1 <= max_depth:
                        walk(child, level + 1)

            walk(win, 1)

        header = (
            f"窗口「{win_info['name']}」({win_info['class_name']}) 控件树，"
            f"共 {len(lines)} 个节点（深度≤{max_depth}）：\n"
        )

        # ── 自绘界面逃生提示 ──
        # 实测：WPS 首页是自绘的 KPromeMainWindow，控件树里全是匿名 GroupControl。
        # 若不给模型「这条路是死的」的明确信号，它会在"控件树→视觉→猜参数"之间打转。
        hint = ""
        if not lines or named == 0:
            hint = (
                "\n\n⚠️ 该窗口是【自绘界面】：控件树里没有(或几乎没有)任何具名控件，"
                "uia_* 在这类界面上定位不到元素（继续 dump 也没有用）。请改走确定性路径：\n"
                "  1) 应用级 API（WPS / Office 首选，秒级）："
                "office_create_document(...) 一次完成「新建+写内容+另存为」；"
                "office_new_document() 只新建空白文档。"
                "实测 WPS 整个应用（首页+文档编辑区）都是自绘的 KPromeMainWindow，uia 对 WPS 全程无效。\n"
                "  2) 想触发界面动作 → 键盘快捷键：press_key('ctrl+n') 新建、press_key('ctrl+s') 保存\n"
                "  3) 必须点击界面 → 视觉兜底：screenshot_and_analyze(question='...') 或 "
                "click_element(target_description='...')\n"
                "  4) 不要重复调用 uia_dump_tree（自绘界面再多看几层也没有具名控件）"
            )

        if not lines:
            return header + "（未发现子控件）" + hint
        return header + "\n".join(lines) + hint

    except RuntimeError as e:
        return str(e)
    except Exception as e:
        logger.exception("uia_dump_tree 失败")
        return f"导出控件树失败：{type(e).__name__}: {e}"


# ────────────────────────────────────────────────────────────
# 工具：查找控件
# ────────────────────────────────────────────────────────────

@register_tool(
    name="uia_find_element",
    description=(
        "⭐UI Automation 精确查找控件（不点击）：按名称/类型/AutomationId 定位，返回控件类型与坐标。"
        "比截图+视觉模型快几十倍。用于确认控件是否存在、拿到它的坐标后再做操作。"
        "示例：uia_find_element('保存', window='WPS Office')；uia_find_element(automation_id='btnOK')"
    ),
    parameters={
        "name": {"type": "string", "description": "控件名称（文本），如'保存'、'确定'。exact=false 时为包含匹配"},
        "window": {"type": "string", "description": "窗口标题（模糊匹配），与 hwnd 二选一；都为空则用前台窗口"},
        "hwnd": {"type": "integer", "description": "窗口句柄，优先级高于 window"},
        "control_type": {"type": "string", "description": "控件类型过滤，如'按钮'/'Button'/'Edit'/'MenuItem'（可选）"},
        "automation_id": {"type": "string", "description": "控件 AutomationId（最稳定的定位方式，可选）"},
        "exact": {"type": "boolean", "description": "名称是否精确匹配，默认 false（包含匹配）"},
        "search_depth": {"type": "integer", "description": "搜索深度，默认8"},
        "timeout": {"type": "number", "description": "等待控件出现的秒数，默认3"},
    },
    category="gui_uia",
    timeout=30,
)
def uia_find_element(
    name: str = "",
    window: str = "",
    hwnd: int = 0,
    control_type: str = "",
    automation_id: str = "",
    exact: bool = False,
    search_depth: int = 8,
    timeout: float = 3.0,
) -> str:
    if not _IS_WINDOWS:
        return "错误：UI Automation 仅支持 Windows。"
    if is_aborted():
        return "操作已中止：用户已点击停止"
    if not name and not automation_id:
        return "错误：请至少提供 name 或 automation_id 之一"

    try:
        with _uia_session() as auto:
            win = _resolve_window(auto, window, hwnd)
            if win is None:
                return f"错误：未找到窗口「{window}」"
            ctrl = _find_control(auto, win, name, control_type, automation_id, exact, search_depth, timeout)

        if ctrl is None:
            return json.dumps(
                {"found": False, "query": {"name": name, "control_type": control_type,
                                           "automation_id": automation_id}},
                ensure_ascii=False,
            )
        info = _describe(ctrl)
        return json.dumps({"found": True, **info}, ensure_ascii=False)

    except RuntimeError as e:
        return str(e)
    except Exception as e:
        logger.exception("uia_find_element 失败")
        return f"查找控件失败：{type(e).__name__}: {e}"


# ────────────────────────────────────────────────────────────
# 工具：点击控件
# ────────────────────────────────────────────────────────────

@register_tool(
    name="uia_click_element",
    description=(
        "⭐⭐UI Automation 精确点击控件（桌面自动化首选）：按名称/类型/AutomationId 定位控件并点击，"
        "优先用 InvokePattern（后台调用，不移动鼠标、不受窗口遮挡影响），失败才退回鼠标点击。"
        "毫秒级完成，比 click_element（截图+视觉）快几十倍。"
        "示例：uia_click_element('保存', window='WPS Office')；uia_click_element('确定', control_type='按钮')"
    ),
    parameters={
        "name": {"type": "string", "description": "控件名称（文本），如'保存'、'确定'"},
        "window": {"type": "string", "description": "窗口标题（模糊匹配），与 hwnd 二选一；都为空则用前台窗口"},
        "hwnd": {"type": "integer", "description": "窗口句柄，优先级高于 window"},
        "control_type": {"type": "string", "description": "控件类型过滤，如'按钮'/'Button'/'MenuItem'（可选）"},
        "automation_id": {"type": "string", "description": "控件 AutomationId（可选，最稳定）"},
        "exact": {"type": "boolean", "description": "名称是否精确匹配，默认 false（包含匹配）"},
        "clicks": {"type": "integer", "description": "点击次数，1=单击(默认) 2=双击"},
        "prefer_invoke": {"type": "boolean", "description": "是否优先用 InvokePattern 后台调用，默认 true"},
        "search_depth": {"type": "integer", "description": "搜索深度，默认8"},
        "timeout": {"type": "number", "description": "等待控件出现的秒数，默认5"},
    },
    category="gui_uia",
    timeout=30,
)
def uia_click_element(
    name: str = "",
    window: str = "",
    hwnd: int = 0,
    control_type: str = "",
    automation_id: str = "",
    exact: bool = False,
    clicks: int = 1,
    prefer_invoke: bool = True,
    search_depth: int = 8,
    timeout: float = 5.0,
) -> str:
    if not _IS_WINDOWS:
        return "错误：UI Automation 仅支持 Windows。"
    if is_aborted():
        return "操作已中止：用户已点击停止"
    if not name and not automation_id:
        return "错误：请至少提供 name 或 automation_id 之一"

    try:
        with _uia_session() as auto:
            win = _resolve_window(auto, window, hwnd)
            if win is None:
                return f"错误：未找到窗口「{window}」"
            ctrl = _find_control(auto, win, name, control_type, automation_id, exact, search_depth, timeout)
            if ctrl is None:
                return (
                    f"未找到控件：name={name!r} type={control_type!r} id={automation_id!r}。"
                    "建议先用 uia_dump_tree 查看该窗口实际控件名称；"
                    "若为自绘界面，请改用 click_element（视觉兜底）。"
                )
            info = _describe(ctrl)
            click_msg = _do_click(ctrl, clicks, prefer_invoke)
        return json.dumps({"found": True, "clicked": click_msg, **info}, ensure_ascii=False)

    except RuntimeError as e:
        return str(e)
    except Exception as e:
        logger.exception("uia_click_element 失败")
        return f"点击控件失败：{type(e).__name__}: {e}"


# ────────────────────────────────────────────────────────────
# 工具：等待控件
# ────────────────────────────────────────────────────────────

@register_tool(
    name="uia_wait_element",
    description=(
        "⭐UI Automation 等待控件出现（毫秒级轮询，不截图）。"
        "用于'等应用加载完成再操作'，比 wait_for_element（截图+视觉）快得多。"
        "示例：uia_wait_element('新建', window='WPS Office', timeout=15)"
    ),
    parameters={
        "name": {"type": "string", "description": "控件名称（文本）"},
        "window": {"type": "string", "description": "窗口标题（模糊匹配），与 hwnd 二选一"},
        "hwnd": {"type": "integer", "description": "窗口句柄，优先级高于 window"},
        "control_type": {"type": "string", "description": "控件类型过滤，如'按钮'（可选）"},
        "automation_id": {"type": "string", "description": "控件 AutomationId（可选）"},
        "exact": {"type": "boolean", "description": "名称是否精确匹配，默认 false"},
        "search_depth": {"type": "integer", "description": "搜索深度，默认8"},
        "timeout": {"type": "number", "description": "最大等待秒数，默认10"},
    },
    category="gui_uia",
    timeout=45,
)
def uia_wait_element(
    name: str = "",
    window: str = "",
    hwnd: int = 0,
    control_type: str = "",
    automation_id: str = "",
    exact: bool = False,
    search_depth: int = 8,
    timeout: float = 10.0,
) -> str:
    if not _IS_WINDOWS:
        return "错误：UI Automation 仅支持 Windows。"
    if is_aborted():
        return "操作已中止：用户已点击停止"
    if not name and not automation_id:
        return "错误：请至少提供 name 或 automation_id 之一"

    started = time.time()
    try:
        with _uia_session() as auto:
            win = _resolve_window(auto, window, hwnd)
            if win is None:
                return f"错误：未找到窗口「{window}」"
            ctrl = _find_control(auto, win, name, control_type, automation_id, exact, search_depth, timeout)
            if ctrl is None:
                return f"等待超时（{timeout}s）：控件 name={name!r} 未出现"
            info = _describe(ctrl)
        info["waited"] = round(time.time() - started, 3)
        return json.dumps({"found": True, **info}, ensure_ascii=False)

    except RuntimeError as e:
        return str(e)
    except Exception as e:
        logger.exception("uia_wait_element 失败")
        return f"等待控件失败：{type(e).__name__}: {e}"


# ────────────────────────────────────────────────────────────
# 工具：写入文本 / 读取文本
# ────────────────────────────────────────────────────────────

@register_tool(
    name="uia_set_text",
    description=(
        "⭐UI Automation 向控件写入文本（桌面自动化首选）：优先用 ValuePattern.SetValue（直接写值、"
        "不依赖焦点与输入法），失败再退回聚焦+键盘输入。适合在输入框/编辑区填内容。"
        "注意：WPS/Word 的正文可能不支持 ValuePattern，会退回键盘输入。"
        "示例：uia_set_text('季度报告', name='文件名', window='另存为')"
    ),
    parameters={
        "text": {"type": "string", "description": "要写入的文本（必填）"},
        "name": {"type": "string", "description": "目标控件名称，如'文件名'、'搜索'"},
        "window": {"type": "string", "description": "窗口标题（模糊匹配），与 hwnd 二选一"},
        "hwnd": {"type": "integer", "description": "窗口句柄，优先级高于 window"},
        "control_type": {"type": "string", "description": "控件类型过滤，如'Edit'/'输入框'（可选）"},
        "automation_id": {"type": "string", "description": "控件 AutomationId（可选）"},
        "exact": {"type": "boolean", "description": "名称是否精确匹配，默认 false"},
        "clear_first": {"type": "boolean", "description": "写入前是否清空原内容，默认 true"},
        "search_depth": {"type": "integer", "description": "搜索深度，默认8"},
        "timeout": {"type": "number", "description": "等待控件出现的秒数，默认5"},
    },
    category="gui_uia",
    timeout=30,
)
def uia_set_text(
    text: str,
    name: str = "",
    window: str = "",
    hwnd: int = 0,
    control_type: str = "",
    automation_id: str = "",
    exact: bool = False,
    clear_first: bool = True,
    search_depth: int = 8,
    timeout: float = 5.0,
) -> str:
    if not _IS_WINDOWS:
        return "错误：UI Automation 仅支持 Windows。"

    if is_aborted():
        return "操作已中止：用户已点击停止"
    if text is None:
        return "错误：请提供要写入的文本"

    try:
        with _uia_session() as auto:
            win = _resolve_window(auto, window, hwnd)
            if win is None:
                return f"错误：未找到窗口「{window}」"
            ctrl = _find_control(auto, win, name, control_type, automation_id, exact, search_depth, timeout)
            if ctrl is None:
                return (
                    f"未找到控件：name={name!r} type={control_type!r} id={automation_id!r}。"
                    "建议先用 uia_dump_tree 查看真实控件名称。"
                )

            info = _describe(ctrl)

            # 1) ValuePattern：直接写值（最稳，不依赖焦点/输入法）
            try:
                pattern = ctrl.GetValuePattern()
                if pattern is not None:
                    pattern.SetValue(str(text))
                    return json.dumps(
                        {"set": "ValuePattern", "text": str(text), **info}, ensure_ascii=False
                    )
            except Exception as e:
                logger.debug("ValuePattern 写入失败，退回键盘输入: %s", e)

            # 2) 聚焦后用键盘输入（复用 gui_tools 的剪贴板安全中文输入）
            top = _top_hwnd(ctrl)
            if top:
                _bring_to_front(top)
            try:
                ctrl.SetFocus()
            except Exception:
                pass

            from core.gui_tools import press_key, type_text
            if clear_first:
                try:
                    press_key("ctrl+a")
                except Exception:
                    pass
                try:
                    press_key("delete")
                except Exception:
                    pass
            type_result = type_text(str(text))

        return json.dumps({"set": "键盘输入", "detail": type_result, "text": str(text), **info},
                          ensure_ascii=False)

    except RuntimeError as e:
        return str(e)
    except Exception as e:
        logger.exception("uia_set_text 失败")
        return f"写入文本失败：{type(e).__name__}: {e}"


@register_tool(
    name="uia_get_text",
    description=(
        "⭐UI Automation 读取控件文本（比截图+视觉更准）：返回控件的名称与当前值，"
        "适合读取输入框内容、状态栏文字、列表项文本等结构化数据。"
        "示例：uia_get_text(name='文件名', window='另存为')"
    ),
    parameters={
        "name": {"type": "string", "description": "控件名称（文本）"},
        "window": {"type": "string", "description": "窗口标题（模糊匹配），与 hwnd 二选一"},
        "hwnd": {"type": "integer", "description": "窗口句柄，优先级高于 window"},
        "control_type": {"type": "string", "description": "控件类型过滤（可选）"},
        "automation_id": {"type": "string", "description": "控件 AutomationId（可选）"},
        "exact": {"type": "boolean", "description": "名称是否精确匹配，默认 false"},
        "search_depth": {"type": "integer", "description": "搜索深度，默认8"},
        "timeout": {"type": "number", "description": "等待控件出现的秒数，默认3"},
    },
    category="gui_uia",
    timeout=30,
)
def uia_get_text(
    name: str = "",
    window: str = "",
    hwnd: int = 0,
    control_type: str = "",
    automation_id: str = "",
    exact: bool = False,
    search_depth: int = 8,
    timeout: float = 3.0,
) -> str:
    if not _IS_WINDOWS:
        return "错误：UI Automation 仅支持 Windows。"
    if is_aborted():
        return "操作已中止：用户已点击停止"
    if not name and not automation_id:
        return "错误：请至少提供 name 或 automation_id 之一"

    try:
        with _uia_session() as auto:
            win = _resolve_window(auto, window, hwnd)
            if win is None:
                return f"错误：未找到窗口「{window}」"
            ctrl = _find_control(auto, win, name, control_type, automation_id, exact, search_depth, timeout)
            if ctrl is None:
                return f"未找到控件：name={name!r} type={control_type!r} id={automation_id!r}"

            info = _describe(ctrl)
            value = ""
            # 依次尝试 ValuePattern / TextPattern / LegacyIAccessiblePattern
            for getter, attr in (
                ("GetValuePattern", "Value"),
                ("GetTextPattern", "DocumentRange.GetText"),
                ("GetLegacyIAccessiblePattern", "Value"),
            ):
                try:
                    pattern = getattr(ctrl, getter)()
                    if pattern is None:
                        continue
                    if attr == "DocumentRange.GetText":
                        got = pattern.DocumentRange.GetText(-1)
                    else:
                        got = getattr(pattern, attr)
                    if got:
                        value = str(got)
                        break
                except Exception:
                    continue

        info["value"] = value
        return json.dumps({"found": True, **info}, ensure_ascii=False)

    except RuntimeError as e:
        return str(e)
    except Exception as e:
        logger.exception("uia_get_text 失败")
        return f"读取控件文本失败：{type(e).__name__}: {e}"
