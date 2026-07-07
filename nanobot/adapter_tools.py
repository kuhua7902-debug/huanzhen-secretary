"""幻帧业务工具 — 直接 import 函数调用，不走 CLI 子进程"""

from __future__ import annotations

import asyncio
import importlib
from pathlib import Path
from typing import Any, Callable

from loguru import logger
from nanobot.agent.tools.base import Tool
from nanobot.agent.tools.registry import ToolRegistry


# ── 工具函数缓存（按需导入，避免启动时全部加载）──

_FUNC_CACHE: dict[str, Callable] = {}
_MODULES = ["core.new_tools", "core.archive_tools", "core.ocr_tools",
            "core.email_tools", "core.filetools_organize", "core.tools",
            "core.db_tools", "core.desktop_tools", "core.gui_tools",
            "core.workflow_tools"]


def _get_func(name: str) -> Callable | None:
    if name in _FUNC_CACHE:
        return _FUNC_CACHE[name]
    for mod_path in _MODULES:
        try:
            mod = importlib.import_module(mod_path)
            fn = getattr(mod, name, None)
            if fn is not None:
                _FUNC_CACHE[name] = fn
                return fn
        except Exception:
            continue
    return None


# ── 幻帧工具类 ──

class KejiTool(Tool):
    """通用幻帧工具包装：import 函数 -> asyncio.to_thread 执行"""

    def __init__(self, name: str, description: str, param_schema: dict, required: list[str] | None = None):
        self._name = name
        self._desc = description
        schema: dict = {"type": "object", "properties": param_schema}
        if required:
            schema["required"] = required
        self._schema = schema
        self._func: Callable | None = _get_func(name)

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._desc

    @property
    def parameters(self) -> dict[str, Any]:
        return self._schema

    async def execute(self, **kwargs: Any) -> str:
        logger.info("Tool: {} args={}", self._name, str(kwargs)[:200])
        try:
            if self._func is not None:
                result = await asyncio.to_thread(self._func, **kwargs)
                return str(result)[:4000]
            # 回退 CLI
            import subprocess, sys, json as _j, os as _os
            cli = str(Path(__file__).resolve().parent.parent / "cli.py")
            env = {**_os.environ, "PYTHONIOENCODING": "utf-8"}
            proc = subprocess.run(
                [sys.executable, cli, self._name, _j.dumps(kwargs, ensure_ascii=False)],
                capture_output=True, text=True, timeout=120,
                encoding="utf-8", errors="replace", env=env,
            )
            out = (proc.stdout or "").strip()
            for line in reversed(out.splitlines()):
                try:
                    obj = _j.loads(line)
                    if obj.get("ok"):
                        return str(obj["result"])[:4000]
                except _j.JSONDecodeError:
                    continue
            return (proc.stderr or "")[-300:] or "(无输出)"
        except asyncio.TimeoutError:
            return f"超时: {self._name}"
        except Exception as e:
            return f"错误: {type(e).__name__}: {str(e)[:200]}"


# ── 工具定义 ──

# (name, description, properties_dict, required_list_or_None)
TOOL_DEFS: list[tuple[str, str, dict, list[str] | None]] = [
    ("create_document", "创建Word文档，save_path必须含.docx扩展名",
     {"title": {"type": "string", "description": "文档标题"},
      "content": {"type": "string", "description": "正文内容"},
      "save_path": {"type": "string", "description": "保存路径，必须含.docx"},
      "count": {"type": "integer", "description": "份数，默认1"}},
     ["title", "save_path"]),
    ("create_table", "创建Excel表格",
     {"headers": {"type": "string", "description": "表头逗号分隔"},
      "rows": {"type": "string", "description": "行数据|分隔"},
      "save_path": {"type": "string", "description": "保存路径"}},
     ["headers", "save_path"]),
    ("create_presentation", "创建PPT",
     {"title": {"type": "string"}, "slides": {"type": "string", "description": "JSON数组"},
      "save_path": {"type": "string"}},
     ["title", "save_path"]),
    ("read_document", "读取PDF/Word/Excel/PPT文档内容",
     {"path": {"type": "string"}},
     ["path"]),
    ("delete_file", "删除文件（需确认）",
     {"path": {"type": "string"}, "confirm": {"type": "boolean", "description": "确认删除"}},
     ["path", "confirm"]),
    ("knowledge_stats", "知识库统计", {}, []),
    ("list_allowed_directories", "列出全局文件沙箱允许访问的目录", {}, []),

    # ── 基础工具 ──
    ("get_time", "获取当前日期时间", {}, []),
    ("calculator", "计算数学表达式", {"expr": {"type": "string", "description": "如 1+2*3"}}, ["expr"]),
    ("run_code", "执行Python代码完成任意任务", {"code": {"type": "string", "description": "Python代码"}}, ["code"]),

    # ── 数据处理 ──
    ("analyze_data", "分析CSV/Excel数据，计算统计指标", {"data_source": {"type": "string"}, "column": {"type": "string"}}, []),
    ("format_data", "格式化数据，支持排序/筛选/转置", {"data": {"type": "string"}, "operation": {"type": "string"}}, ["data"]),
    ("clean_data", "数据清洗", {"data_source": {"type": "string"}}, []),
    ("convert_data", "格式转换", {"data_source": {"type": "string"}, "target_format": {"type": "string"}}, []),
    ("etl_pipeline", "ETL数据处理", {"source": {"type": "string"}, "operations": {"type": "string"}}, []),

    # ── 知识库 ──
    ("query_knowledge", "知识库语义检索", {"query": {"type": "string"}}, ["query"]),
    ("index_knowledge", "索引文件到知识库", {"path": {"type": "string"}}, ["path"]),
    ("remove_from_knowledge", "从知识库删除文档", {"name": {"type": "string"}}, ["name"]),

    # ── OCR ──
    ("ocr_image", "图片文字识别", {"image_path": {"type": "string"}}, ["image_path"]),
    ("ocr_pdf", "PDF文字识别", {"pdf_path": {"type": "string"}}, ["pdf_path"]),
    ("ocr_batch", "批量OCR识别", {"directory": {"type": "string"}}, ["directory"]),

    # ── 压缩包 ──
    ("browse_archive", "浏览压缩包内容", {"path": {"type": "string", "description": "压缩包路径"}}, ["path"]),
    ("extract_archive", "解压压缩包", {"path": {"type": "string", "description": "压缩包路径"}, "output_dir": {"type": "string"}}, ["path"]),
    ("create_archive", "创建压缩包", {"sources": {"type": "string"}, "output_path": {"type": "string"}}, ["sources", "output_path"]),

    # ── 邮件 ──
    ("parse_email", "解析邮件文件", {"file_path": {"type": "string"}}, ["file_path"]),
    ("batch_parse_emails", "批量解析邮件", {"directory": {"type": "string"}}, ["directory"]),
    ("extract_email_attachments", "提取邮件附件", {"file_path": {"type": "string"}, "output_dir": {"type": "string"}}, ["file_path"]),

    # ── 文件整理 ──
    ("organize_files", "按类型自动分类整理文件", {"source_dir": {"type": "string"}, "mode": {"type": "string"}}, []),
    ("rename_files", "批量重命名文件。模式: prefix(加前缀)/suffix(加后缀)/replace(替换)/number(编号)", {"directory": {"type": "string"}, "pattern": {"type": "string", "description": "模式: prefix/suffix/replace/regex/number"}, "value": {"type": "string", "description": "模式参数"}}, ["directory"]),
    ("deduplicate_files", "文件去重", {"directory": {"type": "string"}}, ["directory"]),

    # ── 输出验证工具 ──
    ("verify_output", "验证输出文件数据的完整性：检查行数、空值、列名、合计一致性。支持指定 Sheet 和数值合计校验",
     {"path": {"type": "string", "description": "要验证的文件路径"},
      "expect_rows": {"type": "integer", "description": "期望的数据行数（0=不检查，排除表头行）"},
      "check_columns": {"type": "string", "description": "需要存在的列名，逗号分隔"},
      "sheet_name": {"type": "string", "description": "Excel Sheet 名（空=全部 sheet）"},
      "check_sum": {"type": "string", "description": "合计校验，格式 '列名=期望值' 或多个用分号隔开。如 '金额=1000' 或 '金额=-'（自动求和）"}},
     ["path"]),

    # ── 数据库工具 ──
    ("db_connect", "连接数据库（MySQL/PostgreSQL），返回连接ID",
     {"db_type": {"type": "string", "description": "mysql 或 postgresql"},
      "host": {"type": "string"}, "port": {"type": "integer"},
      "database": {"type": "string"}, "username": {"type": "string"},
      "password": {"type": "string"}},
     ["db_type", "host", "database", "username", "password"]),
    ("db_list_tables", "列出数据库中的所有表",
     {"connection_id": {"type": "string"}}, ["connection_id"]),
    ("db_describe_table", "查看表结构详情",
     {"connection_id": {"type": "string"}, "table_name": {"type": "string"}},
     ["connection_id", "table_name"]),
    ("db_execute_query", "执行 SQL 查询并返回结果",
     {"connection_id": {"type": "string"}, "sql": {"type": "string"},
      "limit": {"type": "integer", "description": "返回行数上限，默认100"}},
     ["connection_id", "sql"]),
    ("db_test_connection", "测试数据库连接",
     {"db_type": {"type": "string"}, "host": {"type": "string"},
      "port": {"type": "integer"}, "database": {"type": "string"},
      "username": {"type": "string"}, "password": {"type": "string"}},
     ["db_type", "host", "database", "username", "password"]),
    ("db_disconnect", "断开数据库连接",
     {"connection_id": {"type": "string"}}, ["connection_id"]),

    # ── 桌面控制工具 ──
    ("run_command", "在本机执行 shell 命令并返回输出。可用于查看目录、启动程序、查看进程等。已内置危险命令拦截。示例：run_command('start notepad')、run_command('dir C:\\\\Users')",
     {"command": {"type": "string", "description": "要执行的 shell 命令"},
      "timeout": {"type": "integer", "description": "超时秒数，默认60，最大300"}},
     ["command"]),
    ("open_application", "启动电脑上的应用程序，或用默认程序打开文件/网页。支持 wps/word/excel/powerpoint/notepad/chrome/edge 等。示例：open_application('wps')、open_application('chrome', 'https://www.google.com')、open_application('wps', 'D:\\\\文档\\\\报告.docx')",
     {"app_name": {"type": "string", "description": "程序名称或完整 exe 路径"},
      "args": {"type": "string", "description": "启动参数，如要打开的文件路径或网址（可选）"}},
     ["app_name"]),
    ("download_file", "从网络下载文件到本地指定路径。支持 HTTP/HTTPS。示例：download_file('https://example.com/setup.exe', 'D:\\\\Downloads')",
     {"url": {"type": "string", "description": "文件下载地址（http/https）"},
      "save_dir": {"type": "string", "description": "保存目录，默认下载到桌面"},
      "filename": {"type": "string", "description": "保存的文件名（可选）"}},
     ["url"]),

    # ── GUI 视觉工具（阶段一：让 agent 有"眼睛"）──
    ("screenshot_and_analyze",
     "⭐核心视觉工具：截取当前屏幕并用视觉模型（Qwen3-VL-Plus）分析。"
     "当需要\"看\"屏幕上有什么内容、找按钮位置、判断当前界面状态时调用。"
     "返回视觉模型对屏幕的描述和可点击元素的大致坐标。"
     "示例：screenshot_and_analyze('屏幕上有什么可点击的按钮？分别给出坐标')",
     {"question": {"type": "string", "description": "想让视觉模型分析的问题，如'保存按钮在哪'、'当前对话框是什么'"}},
     []),
    ("screenshot_screen",
     "截取当前屏幕并返回图片保存路径。如果只需要截图不需要视觉分析用这个。"
     "示例：screenshot_screen()、screenshot_screen('0,0,800,600')",
     {"region": {"type": "string", "description": "截图区域 x,y,width,height，不传则全屏（可选）"},
      "save_path": {"type": "string", "description": "保存路径（可选，默认存 screenshots/）"}},
     []),

    # ── 阶段二：鼠标键盘控制 ──
    ("click_position",
     "在屏幕指定坐标点击鼠标。左上角为(0,0)。"
     "示例：click_position(850, 120) 点击(850,120)处；click_position(500,300,'right') 右键点击",
     {"x": {"type": "integer", "description": "屏幕横坐标"},
      "y": {"type": "integer", "description": "屏幕纵坐标"},
      "button": {"type": "string", "description": "鼠标键：left(默认)/right/middle"},
      "clicks": {"type": "integer", "description": "点击次数，1=单击(默认) 2=双击"},
      "duration": {"type": "number", "description": "鼠标移动耗时(秒)，0=瞬移(默认)，>0=平滑移动"},
      "require_confirm": {"type": "boolean", "description": "是否需要用户确认弹窗，默认false"}},
     ["x", "y"]),
    ("type_text",
     "在当前焦点处输入文本。支持中文（通过剪贴板粘贴）。"
     "示例：type_text('Hello World')、type_text('你好世界')",
     {"text": {"type": "string", "description": "要输入的文本"},
      "interval": {"type": "number", "description": "每个字符间隔(秒)，0=立即输入(默认)"}},
     ["text"]),
    ("press_key",
     "按键或快捷键组合，多个键用+分隔表示同时按。"
     "示例：press_key('enter') 回车；press_key('ctrl+s') 保存；press_key('alt+tab') 切换窗口",
     {"key": {"type": "string", "description": "键名或组合，如 enter/ctrl+s/alt+tab"}},
     ["key"]),
    ("scroll_mouse",
     "鼠标滚轮滚动。示例：scroll_mouse('down', 5) 向下滚5格；scroll_mouse('up', 3) 向上滚3格",
     {"direction": {"type": "string", "description": "滚动方向：down(默认)/up"},
      "amount": {"type": "integer", "description": "滚动量(格数)，默认3"}},
     []),
    ("drag_mouse",
     "从起点坐标拖拽鼠标到终点坐标。用于拖文件到文件夹、调整窗口大小等。"
     "示例：drag_mouse(100,100,500,500) 从(100,100)拖到(500,500)",
     {"x1": {"type": "integer", "description": "起点横坐标"},
      "y1": {"type": "integer", "description": "起点纵坐标"},
      "x2": {"type": "integer", "description": "终点横坐标"},
      "y2": {"type": "integer", "description": "终点纵坐标"},
      "duration": {"type": "number", "description": "拖拽耗时(秒)，默认0.5"},
      "button": {"type": "string", "description": "鼠标键：left(默认)/right/middle"},
      "require_confirm": {"type": "boolean", "description": "是否需要用户确认，默认false"}},
     ["x1", "y1", "x2", "y2"]),

    # ── 阶段二：视觉→行动桥梁 ──
    ("screenshot_and_find",
     "⭐视觉→行动桥梁：截图并用 Qwen-VL 找到目标元素的屏幕坐标。"
     "返回JSON坐标，可直接喂给 click_position/drag_mouse 等操作工具。"
     "示例：screenshot_and_find('保存按钮') 返回 {\"found\":true,\"elements\":[{\"x\":850,\"y\":120}]}",
     {"target_description": {"type": "string", "description": "【必填，参数名必须叫 target_description，不要叫 target/element/description】要找的元素的文字描述，如'保存按钮'、'地址栏'、'数字7按钮'"},
      "return_all": {"type": "boolean", "description": "true=返回所有匹配，false=只返回最可能的一个(默认)"}},
     ["target_description"]),
    ("click_element",
     "⭐组合工具：用视觉找到元素并点击（screenshot_and_find + click_position 一步到位）。"
     "适合明确的按钮/图标。支持操作后验证与自动重试（可靠性增强）。"
     "示例：click_element('保存按钮')；click_element('6按钮', verify_change=true, max_retries=2)；"
     "click_element('保存', verify_description='保存成功提示出现', max_retries=1)",
     {"target_description": {"type": "string", "description": "【必填，参数名必须叫 target_description，不要叫 target/element/description】要点击的元素的文字描述，如'保存按钮'、'数字7按钮'、'关闭(X)按钮'"},
      "button": {"type": "string", "description": "鼠标键：left(默认)/right/middle"},
      "clicks": {"type": "integer", "description": "点击次数，1=单击(默认) 2=双击"},
      "require_confirm": {"type": "boolean", "description": "是否需要用户确认，默认false"},
      "verify_change": {"type": "boolean", "description": "点击后是否验证屏幕发生变化(像素差异对比，阈值0.1%)，默认false。开启后若屏幕无变化视为点击未生效。注意：对局部小变化(如数字刷新)可能误判，建议优先用verify_description"},
      "verify_description": {"type": "string", "description": "期望点击后在屏幕上出现的元素描述(如'输入框显示数字6')，用视觉模型验证。比verify_change更准但更慢，推荐优先使用"},
      "max_retries": {"type": "integer", "description": "验证失败时的最大重试次数，默认0不重试。重试会重新截图定位"},
      "wait_after": {"type": "number", "description": "点击后等待多少秒再验证，默认0.5，给应用反应时间"}},
     ["target_description"]),
    ("wait_for_element",
     "⭐智能等待：每隔一段时间截图查找目标元素，直到找到或超时。"
     "用于'等应用加载完成后再操作'，避免应用没加载完就急着找按钮导致失败。"
     "示例：wait_for_element('新建按钮', timeout=15) 等待WPS的新建按钮出现",
     {"target_description": {"type": "string", "description": "【必填，参数名必须叫 target_description，不要叫 target/element/description】要等待出现的元素描述，如'登录按钮'、'加载完成的页面'、'标准模式标签'"},
      "timeout": {"type": "number", "description": "最大等待秒数，默认10"},
      "interval": {"type": "number", "description": "每次重试间隔秒数，默认1"}},
     ["target_description"]),

    # ── 阶段二：窗口管理 ──
    ("list_windows",
     "列出当前所有可见窗口（含标题、句柄、进程ID、状态）。"
     "示例：list_windows() 列出所有窗口",
     {"include_minimized": {"type": "boolean", "description": "是否包含最小化的窗口，默认true"}},
     []),
    ("find_window",
     "按标题查找窗口（支持模糊匹配）。"
     "示例：find_window('记事本') 查找标题含'记事本'的窗口",
     {"title": {"type": "string", "description": "窗口标题或标题的一部分"},
      "exact": {"type": "boolean", "description": "true=精确匹配，false=包含匹配(默认)"}},
     ["title"]),
    ("focus_window",
     "激活指定窗口到前台。可通过title或hwnd指定。"
     "示例：focus_window('记事本') 激活记事本；focus_window('', 12345) 用句柄激活",
     {"title": {"type": "string", "description": "窗口标题（模糊匹配），与hwnd二选一"},
      "hwnd": {"type": "integer", "description": "窗口句柄(数字)，优先级高于title"}},
     []),
    ("close_window",
     "⚠关闭指定窗口（会弹窗让用户确认）。可通过title或hwnd指定。"
     "示例：close_window('记事本') 优雅关闭；close_window('记事本', force=true) 强制结束进程",
     {"title": {"type": "string", "description": "窗口标题（模糊匹配），与hwnd二选一"},
      "hwnd": {"type": "integer", "description": "窗口句柄(数字)，优先级高于title"},
      "force": {"type": "boolean", "description": "true=强制taskkill，false=发WM_CLOSE优雅关闭(默认)"}},
     []),
    ("get_window_info",
     "获取窗口详情（位置、大小、状态、进程名）。可通过title或hwnd指定。"
     "示例：get_window_info('记事本')",
     {"title": {"type": "string", "description": "窗口标题（模糊匹配），与hwnd二选一"},
      "hwnd": {"type": "integer", "description": "窗口句柄(数字)，优先级高于title"}},
     []),

    # ── 阶段二：安全确认 ──
    ("confirm_dangerous_action",
     "危险操作前弹窗让用户确认。返回'confirmed'或'cancelled'。"
     "agent 在执行关闭窗口、删除文件等操作前可主动调用此工具征求用户同意。"
     "示例：confirm_dangerous_action('关闭记事本窗口', '将丢失未保存内容')",
     {"action_description": {"type": "string", "description": "简短描述要做什么，如'关闭记事本窗口'"},
      "details": {"type": "string", "description": "详细说明，如'将丢失未保存内容'（可选）"}},
     ["action_description"]),

    # ── 阶段三：工作流编排 ──
    ("record_workflow",
     "⭐录制模式：开始/停止记录后续桌面操作为可复用的工作流。"
     "开启后，你执行的 open_application/click_element/type_text/press_key/click_position/wait_for_element "
     "等操作会自动记录。录制结束后用 record_workflow(action='stop') 停止并自动保存到 data/workflows/。"
     "示例：record_workflow('calc_test', '计算器测试', action='start') 开始录制；"
     "record_workflow('calc_test', action='stop') 停止并保存；"
     "record_workflow(action='cancel') 取消录制不保存。",
     {"name": {"type": "string", "description": "工作流名称，英文/数字/下划线（start/stop 必填；cancel 可省略）"},
      "description": {"type": "string", "description": "工作流描述（可选，start 时填写）"},
      "action": {"type": "string", "description": "start=开始录制(默认) / stop=停止录制并保存 / cancel=取消录制不保存"}},
     ["action"]),

    ("replay_workflow",
     "⭐回放模式：按保存的工作流步骤序列自动执行，支持变量替换。"
     "比逐步对话快很多（跳过LLM推理），且可复用。"
     "回放时优先用录制时缓存的坐标，验证失败后自动回退到视觉定位。"
     "示例：replay_workflow('calc_test') 直接回放；"
     "replay_workflow('save_doc', variables={'filename': '报告.docx', 'content': '你好'}) 带变量回放；"
     "replay_workflow('calc_test', dry_run=true) 仅预演不执行。",
     {"name": {"type": "string", "description": "要回放的工作流名称"},
      "variables": {"type": "object", "description": "变量字典，替换步骤中的 ${var} 占位符（可选）"},
      "dry_run": {"type": "boolean", "description": "true=只打印步骤不执行(预演)，false=实际执行(默认)"}},
     ["name"]),

    ("save_workflow",
     "手动保存工作流（不经过录制）。适合你已经想清楚流程，直接写成JSON保存。"
     "steps 是 JSON 数组字符串，每个 step 含 action/params/可选 verify/on_failure/max_retries。"
     "支持的 action: open_application, run_command, click_position, click_element, "
     "type_text, press_key, wait_for_element。"
     "示例：save_workflow('my_flow', '我的流程', "
     "'[{\"action\":\"open_application\",\"params\":{\"app_name\":\"calc\"}}]')",
     {"name": {"type": "string", "description": "工作流名称（英文/数字/下划线）"},
      "description": {"type": "string", "description": "工作流描述（可选）"},
      "steps": {"type": "string", "description": "步骤JSON数组字符串，每个step含action/params/verify等"}},
     ["name", "steps"]),

    ("list_workflows",
     "列出所有已保存的工作流。返回名称、描述、步骤数、创建时间、变量列表。"
     "示例：list_workflows()",
     {}, []),
]


def register_keji_tools(registry: ToolRegistry, project_root: Path):
    for item in TOOL_DEFS:
        name, desc, params, required = item[0], item[1], item[2], item[3] if len(item) > 3 else None
        try:
            registry.register(KejiTool(name, desc, params, required))
        except Exception as e:
            logger.warning("注册 {} 失败: {}", name, e)
    logger.info("幻帧工具注册完成: {} 个", len(TOOL_DEFS))
