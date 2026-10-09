"""Office COM 自动化（Tier 0）—— 应用级 API，让文档类桌面任务「立竿见影」。

## 为什么需要这一级（比 UIA 还高一层）

WPS / Word / Excel / PPT 都暴露了 **COM 自动化接口**，可以像调用函数一样
「命令应用」新建文档、写内容、另存为 —— 完全不用模拟鼠标键盘：

- 摆脱截图 / 视觉模型 / 控件树（实测：WPS 首页是自绘的 `KPromeMainWindow`，
  UIA 只能看到 3 个匿名 GroupControl，控件树这条路对 WPS 首页同样是瞎的）
- 毫秒~秒级完成，稳定、可重复、不受 DPI / 分辨率 / 窗口是否最小化影响
- `visible=True` 时用户**照样能看着界面在动**（可视化与高效并不矛盾）

## 四级降级（本模块是第 0 级）

    ⓪ 应用级 API（本模块：Office COM）        —— 有原生接口就用它，最快最稳
        ↓ 应用没有 API
    ① UI Automation（core/uia_tools.py）      —— 控件树精确定位
        ↓ 自绘界面 / 无控件
    ② 浏览器（puppeteer MCP）                  —— 网页走 DOM
        ↓ 都不是
    ③ 视觉 + 坐标（core/gui_tools.py）          —— 万能兜底

ProgID（WPS 与 MS Office 都注册，优先 WPS）：
    KWPS.Application / Word.Application          → 文字（Writer）
    KET.Application  / Excel.Application         → 表格
    KWPP.Application / PowerPoint.Application    → 演示
"""

from __future__ import annotations

import contextlib
import os
import sys
import time

from core.gui_abort import is_aborted
from core.logger import setup_logger
from core.tools import register_tool

logger = setup_logger("huanzhen.office_com")

_IS_WINDOWS = sys.platform == "win32"

# 应用类型 → 候选 ProgID（WPS 在前；WPS 会接管 Word.Application 这个名字）
_PROGIDS = {
    "wps": ["KWPS.Application", "Word.Application"],
    "word": ["Word.Application", "KWPS.Application"],
    "et": ["KET.Application", "Excel.Application"],
    "excel": ["Excel.Application", "KET.Application"],
    "wpp": ["KWPP.Application", "PowerPoint.Application"],
    "ppt": ["PowerPoint.Application", "KWPP.Application"],
}

# Word 对象模型常量
_WD_ALIGN_CENTER = 1
_WD_FORMAT_DOCUMENT_DEFAULT = 16  # .docx


@contextlib.contextmanager
def _com_session():
    """在当前线程初始化 COM（工具执行在 asyncio 工作线程里）。"""
    try:
        import pythoncom
    except ImportError:
        raise RuntimeError("错误：未安装 pywin32，无法使用 Office COM。请执行 pip install pywin32")
    pythoncom.CoInitialize()
    try:
        yield
    finally:
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass


def _dispatch(app_key: str):
    """按候选 ProgID 依次尝试获取 COM 应用对象，返回 (app_obj, progid)。"""
    import win32com.client

    last_err = None
    for progid in _PROGIDS.get(app_key, _PROGIDS["wps"]):
        try:
            app_obj = win32com.client.Dispatch(progid)
            return app_obj, progid
        except Exception as e:  # noqa: BLE001 - 需要尝试下一个 ProgID
            last_err = e
            continue
    raise RuntimeError(f"无法启动 Office/WPS 应用（ProgID 均失败）：{last_err}")


def _find_window_hwnd(pid: int) -> int:
    """按进程 PID 找其主窗口句柄（用于把 hwnd 一并返回给后续 UIA 操作）。"""
    if not pid:
        return 0
    try:
        import win32gui
        import win32process
    except ImportError:
        return 0

    found = []

    def _cb(hwnd, _lparam):
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            _, wpid = win32process.GetWindowThreadProcessId(hwnd)
            if int(wpid) == int(pid) and (win32gui.GetWindowText(hwnd) or "").strip():
                found.append(int(hwnd))
        except Exception:
            pass
        return True

    try:
        win32gui.EnumWindows(_cb, None)
    except Exception:
        return 0
    return found[0] if found else 0


@register_tool(
    name="office_create_document",
    description=(
        "⭐第0级（最快）：用 Office/WPS 的 COM 自动化接口**直接命令应用**新建文档、写入内容并另存为，"
        "不走截图/视觉/鼠标模拟，秒级完成且稳定。visible=true 时用户能看着 WPS/Word 界面自动生成内容。"
        "适合：写文章/报告/通知并保存到指定目录。"
        "示例：office_create_document('正文内容', title='我站在未来等你', save_path='E:\\\\docx文档\\\\我站在未来等你.docx')"
    ),
    parameters={
        "content": {"type": "string", "description": "文档正文内容（必填）"},
        "title": {"type": "string", "description": "文档标题（可选，会作为首行居中加粗显示）"},
        "save_path": {"type": "string", "description": "另存为的完整路径（.docx），如 E:\\\\docx文档\\\\我站在未来等你.docx；留空则只新建不保存"},
        "app": {"type": "string", "description": "应用：wps(默认)/word；也支持 et/excel、wpp/ppt（表格/演示暂只建空文档）"},
        "visible": {"type": "boolean", "description": "是否显示应用窗口（默认 true，方便用户观看）"},
    },
    category="office_com",
    timeout=120,
)
def office_create_document(
    content: str,
    title: str = "",
    save_path: str = "",
    app: str = "wps",
    visible: bool = True,
) -> str:
    if not _IS_WINDOWS:
        return "错误：Office COM 仅支持 Windows。"
    if is_aborted():
        return "操作已中止：用户已点击停止"
    if content is None:
        return "错误：请提供文档内容"

    app_key = (app or "wps").strip().lower()
    save_path = (save_path or "").strip()
    title = (title or "").strip()

    # 目标目录不存在则创建（COM 的 SaveAs 不会自动建目录）
    if save_path:
        parent = os.path.dirname(os.path.abspath(save_path))
        if parent and not os.path.isdir(parent):
            try:
                os.makedirs(parent, exist_ok=True)
            except Exception as e:
                return f"错误：无法创建目录「{parent}」：{e}"

    started = time.time()
    try:
        with _com_session():
            app_obj, progid = _dispatch(app_key)
            try:
                app_obj.Visible = bool(visible)
            except Exception:
                pass

            docs = app_obj.Documents
            doc = docs.Add()

            full_text = f"{title}\n\n{content}" if title else content
            try:
                doc.Content.Text = full_text
            except Exception:
                # 兜底：逐段写入
                doc.Content.InsertAfter(full_text)

            # 标题格式化（居中、加粗、加大）
            if title:
                try:
                    p1 = doc.Paragraphs(1).Range
                    p1.Font.Bold = True
                    p1.Font.Size = 18
                    p1.ParagraphFormat.Alignment = _WD_ALIGN_CENTER
                except Exception as e:
                    logger.debug("标题格式化跳过: %s", e)

            saved_path = ""
            if save_path:
                try:
                    doc.SaveAs2(save_path, FileFormat=_WD_FORMAT_DOCUMENT_DEFAULT)
                except Exception:
                    doc.SaveAs(save_path)
                saved_path = save_path

            # 取窗口句柄，便于后续用 uia_* / 视觉继续操作
            hwnd = _find_window_hwnd(int(getattr(app_obj, "ProcessID", 0) or 0))

            if not visible:
                try:
                    app_obj.Quit()
                except Exception:
                    pass

        elapsed = round(time.time() - started, 2)
        lines = [
            f"✅ 已用「{progid}」创建文档（耗时 {elapsed}s）",
            f"- 标题：{title or '（无）'}",
            f"- 正文字数：{len(content)}",
        ]
        if saved_path:
            lines.append(f"- 已保存到：{saved_path}")
        else:
            lines.append("- 未指定 save_path，仅在应用中新建（未落盘）")
        if hwnd:
            lines.append(f"- 窗口 hwnd={hwnd}，如还需在界面上继续操作可配合 uia_* / click_element")
        if visible:
            lines.append("（已显示应用窗口，可直接在屏幕上看到结果）")
        return "\n".join(lines)

    except RuntimeError as e:
        return str(e)
    except Exception as e:
        logger.exception("office_create_document 失败")
        return f"创建文档失败：{type(e).__name__}: {e}"


@register_tool(
    name="office_new_document",
    description=(
        "⭐在已打开（或刚启动）的 WPS/Word 里新建一个空白文档，返回新文档窗口的 hwnd。"
        "专门解决「WPS 首页是自绘界面、uia 读不到『新建』按钮」的问题："
        "优先用键盘快捷键 Ctrl+N（自绘首页也能触发），失败才回退 COM 新建（可见）。"
        "拿到 hwnd 后即可用 uia_* 在编辑区定位控件、用 uia_set_text / type_text 输入正文。"
        "示例：office_new_document(app='wps')"
    ),
    parameters={
        "app": {"type": "string", "description": "应用：wps(默认)/word"},
        "timeout": {"type": "number", "description": "等待新窗口出现的秒数，默认10"},
    },
    category="office_com",
    timeout=60,
)
def office_new_document(app: str = "wps", timeout: float = 10.0) -> str:
    if not _IS_WINDOWS:
        return "错误：Office COM 仅支持 Windows。"
    if is_aborted():
        return "操作已中止：用户已点击停止"

    app_key = (app or "wps").strip().lower()
    keys = ("wps", "word") if app_key in ("wps", "word") else ("wps",)
    from core.desktop_tools import _list_top_level_windows

    before = _list_top_level_windows()

    # 1) 找到 WPS/Word 主窗口并置前，再按 Ctrl+N（自绘首页也能触发）
    target_hwnd = 0
    for hwnd, title in before.items():
        if any(k in title.lower() for k in keys):
            target_hwnd = hwnd
            break

    if target_hwnd:
        before_title = before.get(target_hwnd, "")
        try:
            from core.gui_tools import focus_window, press_key

            focus_window(hwnd=target_hwnd)
            time.sleep(0.6)
            press_key("ctrl+n")
        except Exception as e:
            logger.warning("Ctrl+N 新建失败：%s", e)

        # 2) 等新建结果出现。注意：WPS 会【复用同一个 hwnd】只改标题
        #    （实测：KPromeMainWindow 从「WPS Office」变成「文字文稿1 - WPS Office」），
        #    因此不能只找"新窗口"，还要检测同一窗口的标题状态变化。
        deadline = time.time() + max(1.0, float(timeout or 10))
        while time.time() < deadline:
            current = _list_top_level_windows()

            # (a) 出现了新的顶层窗口
            for hwnd, title in current.items():
                if hwnd in before:
                    continue
                low = title.lower()
                if any(k in low for k in keys) or ".doc" in low or "文档" in title or "文稿" in title:
                    return (
                        f"✅ 已用 Ctrl+N 新建文档，窗口「{title}」hwnd={hwnd}\n"
                        "⚠️ WPS 整个应用都是自绘界面，uia_* 读不到编辑区控件 —— "
                        "写入正文请改用 office_create_document(...)（可一次完成新建+写入+另存为）。"
                    )

            # (b) WPS 复用同一 hwnd，仅标题变化
            now_title = current.get(target_hwnd, "")
            if now_title and now_title != before_title:
                low = now_title.lower()
                if "文稿" in now_title or "文档" in now_title or ".doc" in low:
                    return (
                        f"✅ 已用 Ctrl+N 新建文档（WPS 复用同一窗口，hwnd={target_hwnd}），"
                        f"当前标题「{now_title}」\n"
                        "⚠️ WPS 整个应用都是自绘界面，uia_* 读不到编辑区控件。"
                        "写入正文请改用 office_create_document(...)（一次完成新建+写入+另存为），"
                        "不要指望 uia_set_text。"
                    )
            time.sleep(0.4)

    # 3) 回退：COM 新建（Visible=True，用户可见）
    try:
        with _com_session():
            app_obj, progid = _dispatch(app_key)
            try:
                app_obj.Visible = True
            except Exception:
                pass
            app_obj.Documents.Add()
            hwnd = _find_window_hwnd(int(getattr(app_obj, "ProcessID", 0) or 0))
        msg = f"✅ 已通过 COM（{progid}）新建文档"
        if hwnd:
            msg += f"，窗口 hwnd={hwnd}"
        return msg + "。后续可用 uia_* / type_text 输入正文。"
    except Exception as e:
        logger.exception("office_new_document 失败")
        return f"新建文档失败：{type(e).__name__}: {e}"


# ═══════════════════════════════════════════════════════════════
# 录屏演示：确定性地「逐字打字」
# ═══════════════════════════════════════════════════════════════
# 为什么单独做一个工具：
#   做演示视频时，「快」和「好看」都要满足 —— 视觉路径（截图+视觉模型）每步
#   1~5 秒，画面全是干等；而 office_create_document 是"瞬间出现全文"，没有过程感。
#   这个工具用 COM 的应用自身 API 按固定节奏逐块写入，画面像真人在打字，
#   全程确定性、可重复录制，且只有 1 次 LLM 回合（不需要中间再"想"）。

def _find_app_window() -> int:
    """找 WPS/Word 的顶层窗口并最大化置前（录屏画面更好看）。"""
    try:
        from core.desktop_tools import _list_top_level_windows
    except Exception:
        return 0

    hwnd = 0
    for h, title in _list_top_level_windows().items():
        low = title.lower()
        if any(k in low for k in ("wps", "word", "文稿", "文档")):
            hwnd = h
            break

    if hwnd:
        try:
            import win32con
            import win32gui

            win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
            win32gui.SetForegroundWindow(hwnd)
        except Exception as e:
            logger.debug("最大化/置前失败: %s", e)
    return hwnd


def _emit_text(sel, doc, chunk: str) -> None:
    """优先 Selection.TypeText（有光标跟随的打字观感），失败退回 Content.InsertAfter。"""
    if sel is not None:
        try:
            sel.TypeText(chunk)
            return
        except Exception:
            pass
    try:
        doc.Content.InsertAfter(chunk)
    except Exception as e:
        logger.debug("写入片段失败: %s", e)


def _type_progressively(app_obj, doc, text: str, cps: float) -> int:
    """按 chars_per_second 的节奏逐块写入，返回写入字符数。"""
    sel = None
    try:
        sel = app_obj.Selection
    except Exception:
        sel = None

    interval = 1.0 / max(1.0, float(cps or 45.0))
    written = 0

    for line in (text or "").split("\n"):
        if is_aborted():
            break
        buf = ""
        for ch in line:
            buf += ch
            if len(buf) >= 4:  # 4 字一块：兼顾"打字感"与 API 调用次数
                _emit_text(sel, doc, buf)
                time.sleep(len(buf) * interval)
                written += len(buf)
                buf = ""
        if buf:
            _emit_text(sel, doc, buf)
            time.sleep(len(buf) * interval)
            written += len(buf)

        # 段落换行
        if sel is not None:
            try:
                sel.TypeParagraph()
                continue
            except Exception:
                pass
        try:
            doc.Content.InsertAfter("\r")
        except Exception:
            pass

    return written


@register_tool(
    name="office_demo_typewrite",
    description=(
        "⭐⭐录屏专用：在 WPS/Word 里新建文档，像真人一样【逐字打字】标题与正文，最后另存为。"
        "全程确定性、节奏稳定，约 8~20 秒完成，画面连续无干等，适合录作品集/演示视频。"
        "不截图、不用视觉模型，一次调用完成整个演示。"
        "示例：office_demo_typewrite(content='...', title='那一天，我捂了', "
        "save_path='E:\\\\docx文档\\\\那一天，我捂了.docx', chars_per_second=45)"
    ),
    parameters={
        "content": {"type": "string", "description": "正文内容（必填），用 \\n 分段"},
        "title": {"type": "string", "description": "标题（可选，居中加粗大字）"},
        "save_path": {"type": "string", "description": "另存为完整路径(.docx)；留空则只打字不保存"},
        "app": {"type": "string", "description": "应用：wps(默认)/word"},
        "chars_per_second": {"type": "number", "description": "打字速度(字/秒)，默认45；录屏想更从容设25，想更快设80"},
        "visible": {"type": "boolean", "description": "是否显示应用窗口（录屏必须 true，默认 true）"},
    },
    category="office_com",
    timeout=300,
)
def office_demo_typewrite(
    content: str,
    title: str = "",
    save_path: str = "",
    app: str = "wps",
    chars_per_second: float = 45.0,
    visible: bool = True,
) -> str:
    if not _IS_WINDOWS:
        return "错误：Office COM 仅支持 Windows。"
    if is_aborted():
        return "操作已中止：用户已点击停止"
    if not content:
        return "错误：请提供正文 content"

    app_key = (app or "wps").strip().lower()
    cps = max(5.0, min(400.0, float(chars_per_second or 45.0)))
    save_path = (save_path or "").strip()
    title = (title or "").strip()

    if save_path:
        parent = os.path.dirname(os.path.abspath(save_path))
        if parent and not os.path.isdir(parent):
            try:
                os.makedirs(parent, exist_ok=True)
            except Exception as e:
                return f"错误：无法创建目录「{parent}」：{e}"

    started = time.time()
    try:
        with _com_session():
            app_obj, progid = _dispatch(app_key)
            try:
                app_obj.Visible = bool(visible)
            except Exception:
                pass

            hwnd = _find_app_window()
            time.sleep(0.4)  # 等窗口最大化动画结束再开打

            doc = app_obj.Documents.Add()
            time.sleep(0.3)

            # ① 标题
            if title:
                _type_progressively(app_obj, doc, title, cps)
                time.sleep(0.35)
                try:
                    p1 = doc.Paragraphs(1).Range
                    p1.Font.Bold = True
                    p1.Font.Size = 18
                    p1.ParagraphFormat.Alignment = _WD_ALIGN_CENTER
                except Exception as e:
                    logger.debug("标题格式化跳过: %s", e)

            # ② 正文
            written = _type_progressively(app_obj, doc, content, cps)
            time.sleep(0.3)

            saved = ""
            if save_path:
                try:
                    doc.SaveAs2(save_path, FileFormat=_WD_FORMAT_DOCUMENT_DEFAULT)
                except Exception:
                    doc.SaveAs(save_path)
                saved = save_path

        elapsed = round(time.time() - started, 2)
        total_chars = len(title) + len(content)
        lines = [
            f"✅ 演示打字完成：耗时 {elapsed}s，共 {total_chars} 字（约 {cps:.0f} 字/秒，{progid}）",
            f"- 标题：{title or '（无）'}",
            f"- 正文写入：{written} 字",
        ]
        if saved:
            lines.append(f"- 已保存到：{saved}")
        else:
            lines.append("- 未指定 save_path，仅打字未保存")
        if hwnd:
            lines.append(f"- 窗口 hwnd={hwnd}")
        return "\n".join(lines)

    except RuntimeError as e:
        return str(e)
    except Exception as e:
        logger.exception("office_demo_typewrite 失败")
        return f"演示打字失败：{type(e).__name__}: {e}"
