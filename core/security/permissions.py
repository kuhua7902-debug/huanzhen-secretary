"""按账号角色限制 Agent 工具与文件路径（对话 / 工具调用）。

设计（2026-05 收敛）：
  早期版本用「黑名单」判定 —— 列出一批写类工具名，其余一律放行
  （``return not is_write_tool(name)``）。这种「fail-open」策略的问题是：
  每新增一个工具，如果忘了把它加进名单，只读账号就能直接用它。
  实际上确实漏了 ``run_command`` / ``open_application`` / 整套 GUI 操作 /
  ``rename_files`` / ``deduplicate_files`` 等一批写类甚至可执行任意命令的工具。

  现在改为 **fail-closed**：
    - ``READ_ONLY_TOOLS`` 明确列出只读账号**允许**使用的工具；
    - 不在这个名单里的工具，对只读账号一律拒绝（新工具默认安全）。
  这样"新增工具忘了改权限"的后果从「越权可用」变成「只读账号少一个工具」，
  是安全的失败方向。

判定入口只有一个：``is_write_tool()``。
``nanobot/adapter_tools.py`` 的 ``HuanzhenTool.read_only`` 也复用它，避免两处各写一份名单。
"""

from __future__ import annotations

from core.security.context import get_request_context
from core.security.users import CurrentUser

# ── 只读账号可以使用的工具（白名单，fail-closed 的唯一依据）──
READ_ONLY_TOOLS = frozenset({
    # 基础
    "get_time",
    "calculator",
    "selfcheck_run",
    "verify_output",
    # 文件读取
    "read_file",
    "list_dir",
    "glob",
    "grep",
    "read_document",
    "list_allowed_directories",
    "browse_files",
    "search_files",
    # 知识库检索
    "query_knowledge",
    "knowledge_stats",
    # 数据读取与纯计算（只返回文本，不落盘）
    "analyze_data",
    "format_data",
    "clean_data",
    # OCR / 邮件 / 压缩包读取（仅返回文本或写系统临时文件）
    "ocr_image",
    "ocr_pdf",
    "ocr_batch",
    "parse_email",
    "batch_parse_emails",
    "browse_archive",
    # 网页读取
    "web_search",
    "web_fetch",
    # 数据库查询（db_connect 会写连接配置文件，但只读账号必须靠它才能查询）
    "db_connect",
    "db_list_tables",
    "db_describe_table",
    "db_test_connection",
    # 窗口信息读取
    "list_windows",
    "find_window",
    "get_window_info",
    # 询问类，无副作用
    "confirm_dangerous_action",
    # 万能调度器：内部会对真实目标工具再做一次校验，见 _LazyTool.execute
    "__tool__",
    # MCP filesystem 只读子集
    "mcp_filesystem_list_allowed_directories",
    "mcp_filesystem_list_directory",
    "mcp_filesystem_list_directory_with_sizes",
    "mcp_filesystem_directory_tree",
    "mcp_filesystem_get_file_info",
    "mcp_filesystem_read_text_file",
    "mcp_filesystem_read_file",
    "mcp_filesystem_read_media_file",
    "mcp_filesystem_read_multiple_files",
    "mcp_filesystem_search_files",
})

# ── 已知的写/执行类工具（仅用于日志、提示文案与自检，不参与放行判断）──
# 保留这份清单是为了：1) 给用户/日志一个准确的原因说明；2) 单元测试可以断言
# 「所有写类工具都不在 READ_ONLY_TOOLS 里」，防止将来有人误加。
WRITE_TOOL_NAMES = frozenset({
    # 文件写入/删除
    "write_file",
    "edit_file",
    "create_folder",
    "delete_file",
    "create_archive",
    "extract_archive",
    # 办公产物
    "create_document",
    "create_table",
    "create_presentation",
    # 知识库写入
    "index_knowledge",
    "remove_from_knowledge",
    # 代码 / 命令执行
    "run_code",
    "run_command",
    "exec",
    # 桌面与 GUI 控制
    "open_application",
    "download_file",
    "click_position",
    "click_element",
    "type_text",
    "press_key",
    "scroll_mouse",
    "drag_mouse",
    "wait_for_element",
    "focus_window",
    "close_window",
    "screenshot_screen",
    "screenshot_and_analyze",
    "screenshot_and_find",
    # UI Automation 精确定位（桌面自动化的写类操作，只读账号默认拒绝）
    "uia_dump_tree",
    "uia_find_element",
    "uia_click_element",
    "uia_wait_element",
    "uia_set_text",
    "uia_get_text",
    # Office/WPS COM 自动化（会新建/写入/保存文档，写类）
    "office_create_document",
    "office_new_document",
    "office_demo_typewrite",
    # 数据库写操作
    "db_execute_query",
    "db_disconnect",
    # 文件整理与转换
    "organize_files",
    "rename_files",
    "deduplicate_files",
    "etl_pipeline",
    "convert_data",
    "extract_email_attachments",
    # 工作流
    "record_workflow",
    "replay_workflow",
    "save_workflow",
    "delete_workflow",
    "edit_workflow",
    # MCP filesystem 写类
    "mcp_filesystem_write_file",
    "mcp_filesystem_edit_file",
    "mcp_filesystem_move_file",
    "mcp_filesystem_create_directory",
})

# MCP 工具里按前缀识别为写类的（导出/清洗/建物）
WRITE_TOOL_PREFIXES = (
    "mcp_quack_export_",
    "mcp_quack_load_",          # 会写入 DuckDB 会话状态
    "mcp_engineer-your-data_create_",
    "mcp_engineer-your-data_clean_",
    "mcp_engineer-your-data_export_",
    "mcp_engineer-your-data_write_",
)

# 兼容旧命名（早期版本叫 READONLY_EXTRA_ALLOWED）
READONLY_EXTRA_ALLOWED = READ_ONLY_TOOLS


def resolve_current_user() -> CurrentUser | None:
    """从请求上下文解析当前登录用户。"""
    ctx = get_request_context()
    if not ctx.user_id or ctx.user_id in ("anonymous", "localhost", "service"):
        return None
    if ctx.role in ("admin", "member", "readonly"):
        return CurrentUser(
            id=ctx.user_id,
            username=ctx.actor or ctx.user_id,
            role=ctx.role,
            display_name=ctx.actor or ctx.user_id,
        )
    from core.database.db import get_db

    row = get_db().get_user_by_id(ctx.user_id)
    if not row or not row.get("is_active"):
        return None
    return CurrentUser(
        id=row["id"],
        username=row["username"],
        role=row["role"],
        display_name=row.get("display_name") or row["username"],
    )


def is_write_tool(tool_name: str) -> bool:
    """判断工具是否具有副作用（写盘 / 执行命令 / 操作桌面）。

    采用 fail-closed：**未知工具一律视为写类**。
    """
    if not tool_name:
        return True
    # 白名单优先：即使它同时也出现在 WRITE_TOOL_NAMES 里（不应该发生），
    # 也以白名单为准，保持"一个判定入口"的语义清晰。
    if tool_name in READ_ONLY_TOOLS:
        return False
    if tool_name in WRITE_TOOL_NAMES:
        return True
    if any(tool_name.startswith(p) for p in WRITE_TOOL_PREFIXES):
        return True
    # 未知工具：默认拒绝（fail-closed）
    return True


def tool_allowed_for_user(tool_name: str, user: CurrentUser | None) -> bool:
    """该角色能否使用该工具。"""
    if not user:
        return True
    if user.is_admin:
        return True
    if user.role != "readonly":
        return True
    # 只读账号：仅白名单放行
    return tool_name in READ_ONLY_TOOLS


def role_permission_hint(user: CurrentUser | None) -> str:
    if not user:
        return ""
    if user.is_admin:
        return "当前为管理员：可使用全部工具并访问工作区内所有路径。"
    if user.role == "readonly":
        return (
            "当前为只读账号：仅可使用查询/读取类工具（读取文件、检索知识库、查询数据库、"
            "数据统计、OCR、查看窗口信息等）。不可创建、修改、删除文件，"
            "不可执行命令、不可操作鼠标键盘；文件路径仅限「共享文件」与「我的文件」。"
        )
    return "当前为成员账号：可读写共享目录与个人目录，不可访问其他用户私人文件夹。"
