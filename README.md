# 幻帧 Agent (Huanzhen)

Windows 上的**本地 / 局域网 AI 助手**：浏览器打开即可对话，支持多账号、团队共享文件夹、管理员后台。适合办公室内网一台服务器、多人通过网页访问。

**Agent 引擎基于开源项目 [nanobot](https://github.com/HKUDS/nanobot)**（HKUDS，MIT 许可）。幻帧在其上增加了 Web 前端、多用户权限、团队文件工作区与企业部署能力。详见 [第三方声明与致谢](THIRD_PARTY_NOTICES.md)。

---

> ## ⚠️ 安全提示：如果你从公开仓库克隆，请先轮换密钥
>
> 本项目的历史提交中曾包含**明文 API Key**（`启动幻帧语音助手.bat` 曾硬编码 4 个真实密钥，
> 且该文件被 git 跟踪）。该文件已改为从 `.env` 读取，但**删除文件不能收回已经流出的密钥**。
>
> 请到 DeepSeek / 智谱 / Groq / 阿里云百炼 后台**吊销并重新签发**，新密钥只写入 `.env`。
> 详见 [SECURITY.md](SECURITY.md)。

---

## 界面预览

启动后浏览器访问 `http://127.0.0.1:8000/` 的效果如下：

![幻帧运行界面](image/huanzhen-screenshot.png)

---

## 你能用它做什么

- 网页里和 AI 对话（流式回复，会话按用户保存）
- **团队文件**：共享目录 + 每人独立目录，网页上传/浏览
- **三种角色**：管理员 / 成员（可写自己+共享）/ 只读（仅读、不可用写入类工具）
- 可选：Word/Excel、知识库检索、数据分析、MCP 工具（需 Node.js）

---

## 5 分钟上手（Windows）

### 第 0 步：准备软件

| 软件 | 是否必须 | 说明 |
|------|----------|------|
| [Git](https://git-scm.com/) | 必须 | 克隆代码；**请安装 [Git LFS](https://git-lfs.github.com/)**（离线依赖包在 LFS 里） |
| [Python 3.12](https://www.python.org/downloads/) 64 位 | 必须 | 与仓库内离线 wheel 一致；安装时勾选 **Add to PATH** |
| [Node.js 18+](https://nodejs.org/) | 可选 | 完整 MCP 能力；不装也能先聊天 |

### 第 1 步：下载代码

```powershell
git clone https://github.com/你的用户名/huanzhen-secretary.git
cd huanzhen-secretary
git lfs install
git lfs pull
```

> `git lfs pull` 会下载约 **400MB** 离线依赖包，只需执行一次。若跳过 LFS，部署时会改为联网下载（较慢）。

### 第 2 步：一键安装环境

双击 **`setup_deploy.bat`**（或 `一键部署.bat`）。

脚本会自动：创建 `venv`、从本地 wheel 安装依赖、生成 `config.yaml` / `.env`、创建数据目录。

### 第 3 步：填写密钥

用记事本打开项目根目录 **`.env`**，至少改这两项：

```env
DEEPSEEK_API_KEY=sk-你的DeepSeek密钥
HUANZHEN_ADMIN_PASSWORD=你想设置的管理员密码
```

保存后关闭。其他项可暂时留空；启动时若提示某环境变量未设置，多为可选项，补全 `.env` 后重启即可。

模型默认 DeepSeek，在 `config.yaml` 的 `models` 段可改。

### 第 4 步：启动

| 方式 | 操作 |
|------|------|
| **推荐** | 双击 **`launch_huanzhen.bat`** → 自动打开浏览器 |
| 看日志 | 双击 **`run_server.bat`** → 黑窗里运行，停服务按 `Ctrl+C` |

- 本机访问：http://127.0.0.1:8000/
- 局域网访问：http://**服务器IP**:8000/（同一 WiFi/内网的其他电脑）

### 第 5 步：登录

1. 浏览器打开上述地址  
2. 使用管理员账号登录（默认用户名见 `config.yaml` 里 `security.bootstrap_admin.username`，一般为 `admin`）  
3. 密码为你在 **`.env`** 里设置的 `HUANZHEN_ADMIN_PASSWORD`  

首次登录后可在管理页面创建其他用户（成员 / 只读）。

---

## 常用脚本说明

| 文件 | 用途 |
|------|------|
| `setup_deploy.bat` / `一键部署.bat` | 新机安装 Python 虚拟环境与依赖（调用 `scripts/deploy.ps1`） |
| `launch_huanzhen.bat` / `启动幻帧.bat` | 后台启动服务并打开网页（无控制台窗口） |
| `run_server.bat` / `运行服务.bat` | 带黑窗启动（排错时用，日志直接打印在窗口里） |
| `诊断.bat` | **环境自检**：检查 Python/依赖/配置/端口/数据库/MCP，并给出修复命令（`诊断.bat --fix` 可自动补齐缺失的 `.env` / `config.yaml`） |
| `启动幻帧语音助手.bat` | 启动语音助手（唤醒词 + 语音回复），密钥从 `.env` 读取 |
| `package_wheels.bat` | 在本机重新打包离线 wheel（换 Python 版本时用） |
| `build.bat` | PyInstaller 打包（产物在 `dist/`） |
| `install_voice_service.ps1` | 注册语音助手开机自启 |

---

## 开发与测试

```powershell
# 安装开发依赖
venv\Scripts\python.exe -m pip install -r requirements-dev.txt

# 运行测试
venv\Scripts\python.exe -m pytest tests -q
```

测试套件覆盖权限模型（防止只读角色越权回退）、工具 schema 与函数签名一致性
（防止再次出现「schema 参数名写错导致工具必然 TypeError」）、路径沙箱、
会话隔离、文档解析与 `verify_output` 验证逻辑。

**加新工具时请注意**：业务工具需要在两处登记 —— 源文件里的 `@register_tool` 装饰器
（legacy/语音/CLI 路径使用）与 `nanobot/adapter_tools.py` 的 `TOOL_DEFS`
（Web 路径使用）。`tests/test_tool_schema.py` 会校验二者与真实函数签名一致。
只读账号的可用范围由 `core/security/permissions.py::READ_ONLY_TOOLS` 白名单决定，
新增只读工具需显式加入白名单（**默认拒绝**是刻意的安全设计）。

---

## 配置说明

| 文件 | 作用 | 是否上传 Git |
|------|------|----------------|
| `.env` | API Key、管理员密码、JWT 密钥 | 否（本地私密） |
| `config.yaml` | 模型、MCP、安全策略 | 否（从 `config.example.yaml` 复制） |
| `config.example.yaml` | 配置模板 | 是 |
| `.env.example` | 环境变量模板 | 是 |

**不要**把填好密钥的 `.env` / `config.yaml` 提交到公开仓库。

---

## 角色与权限（简要）

| 角色 | 对话 | 文件 |
|------|------|------|
| **admin** | 全部工具 | 整个工作区 |
| **member** | 可写工具 | 共享目录 + 自己的 `users/<id>/` |
| **readonly** | 仅读/查询类工具 | 仅读共享 + 自己的目录 |

团队文件物理路径：`data/workspace/shared/`、`data/workspace/users/<用户ID>/`。

---

## 停止服务

- 用 `run_server.bat` 启动的：在黑窗按 **Ctrl+C**
- 用 `launch_huanzhen.bat` 启动的：任务管理器结束 **pythonw.exe**，或 PowerShell：

```powershell
Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue |
  ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
```

---

## 常见问题

**Q：克隆后没有 `offline_packages` 或部署仍要联网？**  
A：执行 `git lfs install` 后 `git lfs pull`。

**Q：必须用 Python 3.12 吗？**  
A：本仓库自带的离线 wheel 是 **3.12**。若你只有 3.10/3.11，可删掉 `offline_packages` 后运行 `setup_deploy.bat` 走联网安装。

**Q：启动后一堆「环境变量未设置」警告？**  
A：在 `.env` 补 `DEEPSEEK_API_KEY` 等；飞书、OpenAI 不用可忽略。

**Q：双击 `main.py` 闪退？**  
A：不要双击 py 文件，请用 `launch_huanzhen.bat` 或 `run_server.bat`。

**Q：pip 安装失败？**  
A：确认 Python 为 64 位；或在有网环境重新运行 `setup_deploy.bat`。

**Q：局域网别人访问不了？**  
A：检查 Windows 防火墙是否放行 **8000** 端口；用服务器内网 IP，不要用 `localhost`。

---

## 更多文档

- [**故障排查手册**（起不来 / 登录 / 模型 / 知识库 / 桌面自动化）](docs/故障排查.md)
- [架构说明（当前代码结构、两个引擎、安全模型）](docs/幻帧架构总结.md)
- [安全策略与密钥轮换](SECURITY.md)
- [改动记录](CHANGELOG.md)
- [新机部署详细步骤](docs/新机部署.md)
- [离线 / 内网部署](docs/离线部署.md)
- [使用指南（功能与页面）](docs/使用指南.md)
- [项目目录说明](docs/项目目录说明.md)
- [发展路线](docs/发展路线.md)

---

## 已知限制

以下能力**有意保留了边界**，部署前请知情（详见 [SECURITY.md](SECURITY.md)）：

- `run_code` 的沙箱只拦截文件读写的一部分（仅替换了内建 `open()`），
  等价于「以服务进程权限执行任意 Python」。生产环境请配合容器或低权限账号。
- 语音 / 微信 / 企业微信入口没有登录用户，角色权限不生效，只受全局文件沙箱约束。
- GUI 桌面自动化共享一个**进程级**急停标志，无法按会话隔离 ——
  多人共用一台机器时建议不开放 GUI 工具。
- `nanobot` 引擎自带的定时任务、心跳、长期记忆整理（Dream）等能力
  **代码存在但未接入**当前产品链路，见架构说明。
- 前端只引入了一个外部 CDN 资源（Font Awesome 6.5.1，用于「黑白图标」主题）。
  纯内网 / 离线环境下该请求会失败，**默认的 Emoji 图标主题不受影响**；
  如需完全离线请在设置页保持 Emoji 主题，或把 FA 资源下载到 `web/` 下改为本地引用。

---

## 技术栈

FastAPI · **[nanobot](https://github.com/HKUDS/nanobot)**（HKUDS）· SQLite 用户/会话 · JWT 鉴权 · 可选 MCP / Chroma 知识库

## 许可与第三方组件

- **幻帧自有代码**（`core/`、`web/`、`main.py` 及部署脚本等）：[MIT License](LICENSE)
- **nanobot 引擎**（`nanobot/`）：[MIT License](nanobot/LICENSE)，上游 [HKUDS/nanobot](https://github.com/HKUDS/nanobot)
- **其他 bundled 组件与 pip 依赖**：见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)

使用各 AI 模型 API（如 DeepSeek）及第三方依赖时，请遵守其各自的服务条款与许可。
