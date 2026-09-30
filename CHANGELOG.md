# 改动记录

本文件记录幻帧 Agent 的重要改动。格式参考 [Keep a Changelog](https://keepachangelog.com/)。

---

## [未发布]

### 安全（重要）

- **轮换历史泄露的密钥。** `启动幻帧语音助手.bat` 曾硬编码 4 个真实 API Key
  （DeepSeek / 智谱 / Groq / 阿里云百炼）且该文件被 git 跟踪，密钥已进入 git 历史。
  该文件已改为从 `.env` 读取，并用 `%~dp0` 替换写死的旧项目路径。
  **删除文件不能收回已流出的密钥，请到各平台吊销并重新签发。** 详见 `SECURITY.md`。
- **只读角色的权限从 fail-open 改为 fail-closed。** 原先只列写类工具黑名单、其余一律放行，
  导致只读账号可以直接调用 `run_command`（任意 shell 命令）、`open_application`、
  整套 GUI 鼠标键盘操作，以及 `rename_files` / `deduplicate_files`（无确认直接删文件）/
  `etl_pipeline` / `create_archive` / `extract_archive` 等 20 余个写类工具。
  现在只读账号只能使用 `READ_ONLY_TOOLS` 白名单内的工具，**未知工具默认拒绝**
  ——这样"新增工具忘了登记权限"的后果从"越权可用"变成"少一个工具"，是安全的失败方向。
- **`open_application` 命令注入。** 移除 `cmd /c start` 拼接，改为
  `subprocess.Popen([exe, *argv], shell=False)`。此前 `args` 由模型可控、
  经 cmd.exe 二次解析，可用 `&` / `|` / `^` 注入第二条命令，且完全绕过
  `run_command` 的危险命令黑名单。
- **tar 解压路径穿越。** `extractall()` 补 `filter="data"`，并对旧版本回退到自实现的
  安全解压（拒绝绝对/盘符路径、拒绝解析后逃出目标目录的成员、跳过链接与设备文件）。
  此前恶意 tar 可用 `../` 成员越界写盘，绕过整条路径沙箱。
- **恢复审计覆盖。** 审计埋点原在 `ToolRegistry.execute()`，而 runner 执行工具时直调
  `tool.execute()` 绕过了它 —— 文件读写、命令执行、9 个高频业务工具全部不留审计记录。
  现在在 `Runner._execute_tools()` 按「LLM 实际请求的工具名」统一补报，覆盖全部路径。
- `.gitignore` 补上 `screenshots/`（实测已堆积 160MB+）、工作流 `.trash` 备份等运行时产物。

### 修复

- **`/chat/stop` 从未真正停止对话。** 前端把 `session_id` 放在 JSON body，而后端按查询参数
  读取，导致 `sid` 恒为空、取消事件永远命中不了。后果是用户点"停止"后 **agent 仍在后台
  跑完整个 ReAct 循环并继续消耗 token**。现在后端同时接受查询参数与 JSON body，
  并会由 `conv_id` 还原出 `user:{uid}:{conv}` 会话键；前端也改为传查询参数。
  实测：`stop_reason=cancelled`、模型调用次数 0；服务端日志确认
  `Cancel event triggered for session: user:...`。
- **停止关键词会静默吞掉正常提问。** `chat.js` 对消息做子串匹配
  （含「取消」「stop」「不要了」等），命中就清空输入并直接返回 —— 因此
  「如何取消订阅」这类问题会凭空消失且没有任何反馈，且即使当时没有流式输出也会触发。
  改为仅在 `isStreaming` 时、且整句精确匹配才视为停止命令。
- **6 个工具的 schema 参数名与函数签名不一致**，调用必然
  `TypeError: unexpected keyword argument`，在 Web 路径上完全不可用：
  `clean_data` / `convert_data`（`data_source`→`source`）、
  `etl_pipeline`（`operations`→`steps`）、`ocr_pdf`（`pdf_path`→`path`）、
  `parse_email` / `extract_email_attachments`（`file_path`→`path`）。
- **5 个工具此前没有注册进 Web 路径**，AI 因此无法创建文件夹、无法浏览/搜索目录、
  无法管理工作流：补注册 `create_folder`、`browse_files`、`search_files`、
  `delete_workflow`、`edit_workflow`（`TOOL_DEFS` 60 → 65）。
- **两个微信桥接都是坏的。** `core/wechat/bridge.py` 与 `work_bridge.py` 都把参数写成
  `session_id=`，而真实签名是 `chat(query, sid, files)`，必然 `TypeError` 被 except 吞掉，
  对外固定回「抱歉，处理出错」。两处同时改用常驻事件循环承载异步调用
  （原来每次新建再关闭临时 loop，Adapter 因此从未连上 MCP 服务），
  企业微信回调改为全异步、不再阻塞服务器事件循环。
- **`verify_output` 只要用了 `check_sum` 就永远返回 FAIL。** 该函数把
  「合计[x]: 100.00」「验证通过」这类**正常信息**也 append 进 `issues`，
  而末尾以 `if issues: FAIL` 判定 —— 于是校验完全通过也报失败。
  现拆出 `notes` 与 `issues`。
- **强制验证可以被绕过。** `ComplianceHook.before_finalize` 原本只把
  `ctx.final_content` 置 `None` 就想让模型继续迭代，但 runner 从不读取它做控制流
  （docstring 里"hook 可以阻止 finalize"的承诺形同虚设），于是模型直接给答案就绕过了
  "必须先调 `verify_output`"的校验。现在引入显式的 `block_finalize` 契约，
  runner 会带着注入的提示真正回到循环，并加 `MAX_FINALIZE_BLOCKS=2` 上限
  防止模型死活不验证时把迭代耗尽、用户拿不到回答。
- **`db_execute_query` 给非 SELECT 语句也追加 `LIMIT`**，`INSERT/UPDATE/DELETE/DDL`
  会被拼成 `... LIMIT 100` 必然语法错误 —— 写操作永远失败。现在只对
  `SELECT/WITH/SHOW/DESCRIBE/EXPLAIN` 追加。
- **语音说"停止"不会刹住 GUI 操作。** 原先只调 `CoreAgent.cancel()`，没有触发进程级
  GUI abort，正在执行的 `pyautogui.click` / `typewrite` 不会中断
  （而 `core/gui_abort.py` 的 docstring 明确说它就是为了解决这个问题而写的）。
- **OpenWakeWord 的 ONNX 回退是"假成功"。** tflite 失败后只重建了模型、没开音频流，
  却 `return True`；之后 `listen()` 因 `_stream` 为空恒返回 `False`，
  **唤醒永久失效而上层以为初始化成功**。两条路径现在走同一套「建模型 + 开音频流」，
  任一缺失即清理并返回 False，并给出可操作的告警。
- **`voice_tray` 单实例检查形同虚设。** 先 `CloseHandle` 再用已关闭的句柄调
  `GetExitCodeProcess`，读到无效值恒为 0（≠ `STILL_ACTIVE`），因此永远判定"未在运行"。
  改为先读退出码再关闭（`finally` 保证只关一次），显式声明 ctypes argtypes/restype
  防止 64 位句柄截断，并改用 `PROCESS_QUERY_LIMITED_INFORMATION`。
- **`type_text` 会破坏用户剪贴板。** 中文输入走"写入剪贴板 + Ctrl+V"却不保存/恢复原内容。
  现在先保存、粘贴后在 `finally` 恢复；剪贴板不可读或是非文本则跳过恢复
  （避免用空串清掉用户的图片）。
- **`screenshot_tools.py` 仅为编译残留**（源码已在更名过程中删除），已确认无任何引用。

### 变更

- **环境变量命名兼容。** 产品由「科吉 / Keji」更名为「幻帧 / Huanzhen」，
  `config.example.yaml` 与 README 早已使用 `HUANZHEN_*`，但代码只认 `KEJI_*` ——
  按文档配置的用户实际拿不到管理员密码 / JWT 密钥。现在 `HUANZHEN_*` 优先、
  `KEJI_*` 兜底，两者都可用。
- **`database.path` 配置生效。** 此前 SQLite 路径硬编码为 `data/keji.db`，
  配置项被忽略。现在会读取 `database.path`，但**只认已存在的文件**，
  避免"配置写错 → 悄悄新建空库 → 历史对话全看不见"。默认路径不变。
- **启动日志降噪。** 飞书 / OpenAI / Tavily / Picovoice / GitHub 等可选环境变量未设置时
  不再刷 WARNING（降为 DEBUG）。README 常见问题里"启动后一堆环境变量未设置警告"
  即由此而来。
- `KejiTool` 新增 `read_only` / `exclusive`：权限判定统一复用
  `core.security.permissions`，避免两处各维护一份名单；
  GUI / 桌面类工具标记为 `exclusive`，开启并发执行时不会互相打断。

### 文档

- 重写 `docs/幻帧架构总结.md`：修正过期路径与结构，补充两个引擎的差异、
  未被使用的引擎能力、安全模型与已知边界。
- 新增 `SECURITY.md`：认证/授权/沙箱/审计机制、危险能力的真实边界、密钥轮换指引。
- 新增 `CHANGELOG.md`（本文件）。
- 新增 `tests/` pytest 测试套件与 `requirements-dev.txt`。

---

## 更早的改动

更早的迭代记录散落在 `docs/` 下的若干排查文档中，按主题保留了根因分析：

- `docs/技能面板Bug排查与修复记录.md` — 双 Adapter 实例导致技能不生效、空 `toast()` 覆盖
- `docs/数据库功能-前端空白问题排查记录.md` — HTML 嵌套错误导致页面空白
- `docs/环形图Hover放大动画Bug排查与修复记录.md`
- `docs/计时器添加踩坑记录.md`
- `docs/飞书对接指南.md`
- `docs/改造记录.md` — 从 openAgent 移植 NL2SQL 与数据库管理
