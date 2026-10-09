# 改动记录

本文件记录幻帧 Agent 的重要改动。格式参考 [Keep a Changelog](https://keepachangelog.com/)。

---

## [未发布]

### 零门槛安装与长期运维（第五轮）

- **新增一键安装器：`安装.bat` + `scripts/install.ps1`。** 把「装环境」收敛成一次双击：
  ① 检查 Python（必须 3.12 / 64 位，缺失时给出 winget 安装命令与下载链接，并明确提醒勾选
  Add to PATH）→ ② 检查 Git（可选）→ ③ 调用 `deploy.ps1` 装依赖（优先离线包）
  → ④ 跑一次环境自检确认可用 → ⑤ 生成桌面快捷方式 → ⑥ 打印"下一步该填什么配置"。
  > **关于 Docker**：本项目是 **Windows 专属**（`pywin32` / `pyautogui` / `uiautomation` /
  > `pyaudio` 等依赖没有 Linux 构建），做成 Linux 镜像需要先把这批能力整体降级为可选、
  > 并拆分一份 Linux 依赖子集——那会改变产品形态。因此这一轮交付的是**平台适配的一键安装器**，
  > 而不是一个装不上依赖、无法验证的 Dockerfile。

- **新增单实例保护（PID 锁）：`core/single_instance.py`。** 现实中很常见：用户双击启动没反应，
  又双击一次——而第一次其实正在启动中（服务需 10~40 秒才监听端口），启动脚本的端口检测此时
  拦不住，于是两个实例抢同一个 SQLite。现在启动时在 `data/.huanzhen.lock` 写入 PID：
  已有存活实例则**拒绝启动并给出可操作提示**（怎么停、怎么强制启动）；进程被强杀留下的
  **僵尸锁会被自动接管**；退出时只删自己的锁。`HUANZHEN_FORCE_START=1` 可绕过。

- **新增数据保留策略：`scripts/retention.py`。** 解决常驻服务"跑久了目录臃肿"的问题——
  `logs/audit.jsonl` 每次工具调用都追加一行，`audit_events` 表同步增长，`sessions/` 持续累积。
  策略是**归档优先、删除兜底**：
  - 审计日志超过 90 天 → **按月份压缩归档**到 `logs/archive/audit-YYYY-MM.jsonl.gz`
  - 归档文件（365 天）、旧会话（180 天未更新）、数据库审计（180 天）到期才真正移除
  - **无法判定时间的记录一律保留**——宁可占空间，不可丢审计
  - 归档期间持有审计写入锁（`AuditLogger.exclusive()`），避免"读改写"覆盖掉并发写入的新记录
  - 服务启动时**每月自动执行一次**；网页「系统状态 → 数据保留」支持**预演**与立即执行

- **新增首次配置向导：`core/routes_setup.py` + 网页「设置 → 配置向导」。**
  新用户最容易卡住的一步是配置：能启动、但没填模型密钥，发消息一直失败却不知问题在哪。
  - `GET /api/setup/status`（**公开**，登录前也可用）：只返回"有没有配"，**绝不返回密钥值**
  - `POST /api/setup/env`（仅管理员）：把模型密钥 / 管理员密码写入 `.env`，免去手动找文件改文件；
    **只允许白名单键**，拒绝换行与超长值，写入时保留注释与其它行、只替换目标键
  - 网页在**未配置模型密钥时于聊天区顶部显示提示条**，一键直达配置向导；保存后提示需重启生效

### 产品能力（把命令行搬进网页）

- **新增「系统状态」页（管理页 → 设置 → 系统状态，仅管理员）。** 上一轮把「环境自检」
  `诊断.bat` 与「数据备份」`备份.bat` 做成了命令行脚本——但那要求管理员**能登录到那台机器、
  会开命令行**。现在同样能力在浏览器里即可完成：
  - **运行状态**：版本 / Python / 进程号 / 已运行时长 / 磁盘剩余（低于 5GB 高亮）
  - **数据规模**：用户、对话、消息、审计条数、库大小、内置工具与 MCP 工具数量
  - **环境自检**：一键运行（复用 `scripts/doctor.py`），按「通过 / 警告 / 阻断 / 提示」分级展示，
    并直接给出修复建议
  - **数据备份**：列出全部备份（名称/大小/时间），**一键立即备份**（复用 `scripts/backup.py`）

  对应新增 `core/routes_system.py`（`GET /api/system/diagnostics[?deep=1]`、
  `GET /api/system/backups`、`POST /api/system/backup`，全部要求 admin）。
  命令行的 `诊断.bat` / `备份.bat` **保留不变**——当服务本身起不来时，它是唯一还能用的排查入口，
  两者是互补关系而非替代。
- **新增 `core/script_loader.py`：服务端与脚本之间的统一桥梁。** `scripts/` 不是 Python 包，
  直接 import 会污染 `sys.path` 且有重名风险；现在统一用 importlib 按文件路径加载并缓存，
  让"命令行能做的，网页也能做"，同时避免了多处各写一份加载逻辑。
- **新增 `core/version.py`：版本号单一来源（当前 `1.1.0`）。** `/health` 同步返回 `version`，
  便于外部监控与排错时确认"跑的是哪个版本"。
- **`/health` 增加 `version` 字段。**

### 生产加固（面向「长期使用」）

- **登录失败限流（防暴力破解）。** `/api/auth/login` 此前**没有任何失败计数**——内网任何人
  都可以无限次尝试密码，管理员账号可被慢速爆破。现在按「来源 IP」与「用户名」**双维度**
  计数（只按 IP 会漏掉"换 IP 打同一账号"，只按用户名会漏掉"单 IP 撞库多账号"），
  窗口内失败达到阈值后**阶梯锁定**（30s 起翻倍，封顶 15 分钟），**登录成功立即清零**，
  过期条目自动回收（长期运行内存有界）。被限流时返回 **429 + `Retry-After`**。
  阈值可用 `HUANZHEN_LOGIN_WINDOW` / `HUANZHEN_LOGIN_MAX_FAILS` / `HUANZHEN_LOGIN_MAX_LOCK` 调整。
- **数据自动备份。** 此前**零备份机制**：`data/huanzhen.db` 里是全部用户、对话、文档元数据
  与审计记录，误删或磁盘故障即全部丢失。新增 `scripts/backup.py` + `备份.bat`：
  - 用 **SQLite 官方备份 API 生成一致快照**，而不是直接复制活动数据库文件
    （服务运行中直接 copy 可能得到损坏文件——这是本功能的关键实现点）
  - 打包为带时间戳的 zip，含数据库、`config.yaml`、`security/*` 与 `MANIFEST.txt`（恢复说明）
  - 默认保留最近 **7** 份，自动轮转
  - **服务启动时每天自动备份一次**（同一天不重复；失败静默，绝不影响启动）
  - **不含 `.env`**（其中含 API 密钥），避免"备份被随手分享导致密钥外泄"，已在输出与文档中明示
- **启动期配置校验（让"配置写错"在启动就暴露）。** 配置写错时服务此前仍会"成功启动"，
  问题要等用户真正发消息时才以晦涩错误爆出来。新增 `core/config_check.py`，启动时校验
  `config.yaml`（可解析性、`models.default` 是否存在、默认模型密钥能否解析、`agent` 数值范围、
  `security` 风险项、`mcp_servers` 结构、数据库路径等），一次性打印**可定位、可修复**的清单，
  并引导用户运行 `诊断.bat`。默认**只报告不阻断**（本地工具首要目标是"能进去改配置"，
  而不是配置一错就把用户锁在门外）；设 `HUANZHEN_STRICT_CONFIG=1` 可切严格模式。

**本轮自测中发现并修掉的 2 个真实缺陷：**
- `scripts/backup.py` 在**数据库与项目不在同一盘符**时（如 `database.path` 配到 `E:\`），
  `os.path.relpath` 会抛 `ValueError` → 备份整体失败。已改为安全降级为绝对路径。
- 备份文件名只精确到秒，**同一秒内的多次备份会互相覆盖**（实测连发 4 次只剩 1 个文件）。
  已改为毫秒级时间戳 + 冲突自增。

### 部署体验（面向「交给别人部署」）

- **新增环境自检：`诊断.bat` + `scripts/doctor.py`。** 把「能不能跑」拆成可判定、
  可解释、可修复的检查项（Python 版本/位数、是否在 venv、必需与可选依赖、
  `.env` 必填项、`config.yaml` 解析与默认模型密钥是否可解析、运行目录可写、
  端口占用、JWT 密钥、数据库可读、内置工具注册数、磁盘空间、Node/Ollama、
  MCP 启动命令可解析性），输出分级报告并对每项给出**具体修复命令**。
  退出码可用于脚本编排。设计上**不 import 任何 `core.*`**，保证「应用本身跑不起来时
  自检仍然可用」。
- **`--fix` 一键补齐缺失配置。** 没有 `.env` 时从模板生成并写入**随机 JWT 密钥**；
  没有 `config.yaml` 时从模板复制；缺失的运行目录自动创建。只创建缺失项，**绝不覆盖已有文件**。
- **一键启动脚本集成预检。** `launch_huanzhen.bat` 在启动前检查 `.env` / `config.yaml`：
  首次运行会引导生成并打开记事本提示填写两项必填配置，避免「启动了但登录不了 / 模型不可用」。
- **新增《故障排查手册》`docs/故障排查.md`。** 按症状组织（启动/登录/模型/知识库/
  桌面自动化/数据安全），给出可直接复制的 PowerShell 命令，并说明「求助时该提供哪些信息」。
- **`.gitattributes` 锁定 `.bat` / `.cmd` 为 CRLF。** 修复「双击启动脚本一闪而过」的
  根因之一：批处理被 git 转成 LF 后，cmd.exe 解析多行括号块会失败。
- **修复批处理块内未转义右括号。** `if (...)` 块里写 `echo 1) xxx` 会提前闭合代码块，
  把后续文本当命令执行；已改用 `1.` 形式，并新增契约测试防止复发
  （`tests/test_scripts_integrity.py` 校验 CRLF 与括号块、doctor 纯函数）。
- **新增一键停止：`停止服务.bat` + `scripts/stop_server.ps1`。** 服务以无窗口方式后台运行时，
  过去只能去任务管理器里找并手动结束 python 进程。现在按「谁在监听幻帧端口」精确定位，
  且**只结束 python 系进程**（避免误杀占用同端口的其它程序），并给出明确结果。
- **修复 PowerShell 脚本编码（含两处历史遗留）。** Windows PowerShell 5.1 默认按 ANSI(GBK)
  读取 `.ps1`，无 BOM 的 UTF-8 中文会乱码并撑破引号导致语法错误（实测 `stop_server.ps1`
  报 `Missing closing '}'`）。现所有含非 ASCII 的 `.ps1` 统一为 **UTF-8 with BOM**
  （含此前就已埋雷的 `install_shortcut.ps1`、`set_github_about.ps1`、`package_offline.ps1`），
  并加契约测试锁定该约定。
- **日志轮转。** `core/logger.py` 改用 `RotatingFileHandler`（单文件 10MB × 保留 5 份）。
  此前长期运行（常驻数月）会让 `agent.log` 无限增长，最终拖慢启动、占满磁盘。
- **`/health` 增加运维信息。** 追加 `python` / `pid` / `uptime_sec`，方便判断「服务是不是活着、
  是不是刚重启」；不触发任何初始化，成本极低，可安全用于监控与启动脚本轮询。
- **`run_server.bat` 异常退出时引导自检**：退出码非 0 时提示运行 `诊断.bat` 与查看 `logs\agent.log`。
- **新增《首次部署指南》`docs/首次部署指南.md`。** 覆盖「确认电脑条件 → 获取代码
  （git clone + LFS / 下载 ZIP 两种方式）→ 一键部署 → 填写配置 → 启动 → 三步验证 →
  局域网共享 → 日常操作速查 → 目录速查 → 卸载」的完整链路，**每一步都给出 ✅ 验收标准
  与 ⛔ 卡住了怎么办**，新手可照做并可自查。同时把 `docs/新机部署.md` 精简为「快速清单」
  并指向完整版，修掉其中一处历史乱码（`你的用户名` 被编码破坏）与过时的停止说明
  （`任务管理器结束 pythonw.exe` → `停止服务.bat`）。README 的部署入口同步更新。

### 新增

- **桌面自动化加入 UI Automation「第一级」精确定位。** 过去桌面操作只有一条路：
  截图 → 视觉模型 → 坐标点击，每步 1~5 秒且烧大量视觉 token，稳定性受 DPI /
  分辨率 / 窗口位置影响 —— 典型症状是「打开 WPS 点一个菜单」要绕很久。
  新增 `core/uia_tools.py`，基于 Windows 控件树（`uiautomation`，依赖本已随
  `requirements.txt` 安装但从未被使用），提供 6 个毫秒级工具：
  `uia_dump_tree`（导出控件树）、`uia_find_element`（精确查找）、
  `uia_click_element`（优先 InvokePattern 后台点击，不移动鼠标）、
  `uia_wait_element`（毫秒级等待）、`uia_set_text`（ValuePattern 写值）、
  `uia_get_text`（读取控件真实值）。已同时注册进 legacy 注册表与 Web 的
  `TOOL_DEFS`，并在 `core/security/permissions.py` 登记为写类（只读账号默认拒绝）。
- **`open_application` 改为「确定性」返回窗口句柄。** 启动时追踪本次拉起的
  进程 PID，并对比启动前后的顶层窗口，自动识别出被拉起的窗口，返回值里带上
  **`hwnd`（句柄）** 与后续使用 `uia_*` 的指引；不再要求模型猜窗口标题。
  实测：`open_application('notepad')` 直接返回 `hwnd`，`uia_dump_tree(hwnd=...)`
  一次读出 `DocumentControl('文本编辑器')`、菜单栏、标题栏按钮，
  `uia_click_element(name='关闭', ...)` 一步关闭，全程零截图、零视觉模型。
- **系统提示词写入「三级降级」策略**（`prompts/system.md`）：
  ① UI Automation 精确定位（首选）→ ② 浏览器走 puppeteer MCP →
  ③ 视觉+坐标兜底（仅当 uia 找不到控件时）。明确禁止「为点一个按钮先
  `screenshot_and_analyze` 绕一大圈」。
- **新增「第 0 级：应用级 API」——`core/office_com_tools.py`。**
  实测发现 WPS 主窗口是自绘的 `KPromeMainWindow`，UIA 只能看到 3 个匿名
  `GroupControl`（控件树对 WPS 首页同样无效）。但 WPS 注册了完整 COM 自动化
  （`KWPS.Application` / `Word.Application` / `KET.Application` / `KWPP.Application`），
  于是新增 `office_create_document`：通过 COM **直接命令应用**新建文档、写入内容、
  另存为，不经过截图/视觉/鼠标模拟。`visible=True` 时用户照样能看到界面自动生成。
  实测：生成《我站在未来等你》并保存到 `E:\docx文档\` **耗时 1.99 秒**，
  而同一任务走视觉路径 30 秒仍在截图找窗口。已注册进 legacy 注册表与
  `TOOL_DEFS`，并在权限模块登记为写类。
- **修复「工具报参数错后模型无限瞎猜参数名」。** 实测模型在
  `screenshot_and_analyze` 上依次猜 `prompt` / `task` / `instruction`（正确是
  `question`），每猜错一次浪费一轮，最终卡死在原地。现在 `HuanzhenTool.execute`
  单独捕获 `TypeError`，用 `_call_hint()` 把**真实函数签名**回给模型，
  并明确要求"按给出的参数名重调、不要继续猜名字"。这一类"猜参数名卡死"被整体消除。
- **`uia_dump_tree` 识别自绘界面并给出逃生路径。** 此前它对 WPS 这类自绘窗口只返回
  "5 个匿名 GroupControl"，却不告诉模型"这条路是死的"，模型于是在
  「dump → 截图 → 猜参数」之间打转。现在当节点里没有任何具名控件时，会明确提示
  改用：①快捷键 ②`office_create_document` 等应用级 API ③视觉兜底，并提示不要重复 dump。
- **新增 `office_new_document`：用 `Ctrl+N` 绕过 WPS 自绘首页。** WPS 首页
  (`KPromeMainWindow`) 不暴露控件，但键盘快捷键有效——先 `focus_window` 再
  `press_key('ctrl+n')` 即可新建，随后返回新文档窗口 hwnd（编辑区是标准 Word 式 UI，
  uia 可读）；快捷键失败时回退 COM `Documents.Add()`（可见）。
  实测补充：WPS 会**复用同一个 hwnd** 只改标题（`WPS Office` → `文字文稿1 - WPS Office`），
  因此检测逻辑同时覆盖"新窗口"与"同窗口标题变化"两种情况。
- **新增 `office_demo_typewrite`：录屏专用的「逐字打字」演示工具。**
  做演示视频时"快"和"好看"要同时满足：视觉路径每步 1~5 秒、画面全是干等；
  而 `office_create_document` 是瞬间出现全文、没有过程感。该工具用 COM 自身 API
  按固定节奏（`chars_per_second`，默认 45 字/秒）逐块写入，画面像真人在打字，
  全程确定性、可重复录制，且只需 1 次 LLM 回合。
  实测：116 字（标题《那一天，我捂了》+ 正文）**11.35 秒**完成打字并另存为。

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

- **命名统一为「幻帧 / Huanzhen」。** 早前的「科吉 / Keji」命名残留已全量清理：
  代码标识（`KejiAdapter`→`HuanzhenAdapter`、`KejiTool`→`HuanzhenTool`、
  `register_keji_tools`→`register_huanzhen_tools`）、日志 logger（`keji.*`→`huanzhen.*`）、
  前端全局函数与 `localStorage` 键（`kejiFetch`/`keji_token` 等 → `huanzhen*`）、
  数据库文件名（`data/keji.db`→`data/huanzhen.db`）、向量集合名
  （`keji_documents`→`huanzhen_documents`）、脚本与文档一并更新。
  为不丢既有数据，旧库文件与旧向量集合会在启动时**自动改名迁移**；
  历史 `KEJI_*` 环境变量与历史加密盐仍可识别（见下）。
- **环境变量命名兼容。** `config.example.yaml` 与 README 使用 `HUANZHEN_*`；
  大量既有部署的 `.env` 仍是历史 `KEJI_*`。现在 `HUANZHEN_*` 优先、
  `KEJI_*` 兜底，两者都可用（`core/security/secrets.py::ENV_ALIASES`）。
- **`database.path` 配置生效。** 此前 SQLite 路径硬编码，配置项被忽略。
  现在会读取 `database.path`，但**只认已存在的文件**，
  避免"配置写错 → 悄悄新建空库 → 历史对话全看不见"。默认路径为 `data/huanzhen.db`。
- **启动日志降噪。** 飞书 / OpenAI / Tavily / Picovoice / GitHub 等可选环境变量未设置时
  不再刷 WARNING（降为 DEBUG）。README 常见问题里"启动后一堆环境变量未设置警告"
  即由此而来。
- `HuanzhenTool` 新增 `read_only` / `exclusive`：权限判定统一复用
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
