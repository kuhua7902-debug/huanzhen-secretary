# 安全策略

## ⚠️ 部署前必读：轮换历史密钥

**本项目的历史提交中曾包含明文密钥。** `启动幻帧语音助手.bat` 曾在第 27–30 行硬编码了
4 个真实 API Key（DeepSeek / 智谱 / Groq / 阿里云百炼），该文件是被 git 跟踪的，
因此这些密钥已经进入 git 历史。

该文件已在后续提交中改为从 `.env` 读取，但**删除文件不能收回已经流出的密钥**。因此：

> **如果你克隆的是包含该文件的版本，请立即到对应平台吊销并重新签发这 4 个 Key。**
> 已确认轮换完毕后，再删除历史提交（单提交仓库可用 `git filter-repo` 重建）。

新密钥只写进 `.env`（已 gitignore）：

```env
DEEPSEEK_API_KEY=...
ZHIPU_API_KEY=...
GROQ_API_KEY=...
DASHSCOPE_API_KEY=...
```

`.env` 与 `config.yaml` 都在 `.gitignore` 中。**永远不要把填好密钥的文件提交或打包分发。**

---

## 一、认证与授权

### 1.1 认证

- 用户密码用 **bcrypt** 加盐哈希存储（`core/security/users.py`）
- 登录后签发 **JWT（HS256）**，默认有效期 72 小时，密钥来自
  `HUANZHEN_JWT_SECRET` / `security.jwt_secret`（历史别名 `KEJI_JWT_SECRET` 仍兼容）；
    都未配置时会在 `data/security/jwt_secret` 生成一个随机密钥
    （注意：该文件丢失会导致所有已签发 token 立即失效）
- 可选服务级 **API Key**（`HUANZHEN_API_KEY`，历史别名 `KEJI_API_KEY` 仍兼容），用于脚本/服务调用
- 认证模式 `security.auth_mode`：`both`（默认）/ `user_only` / `api_key_only`
- `security.allow_localhost_without_auth` 默认 `false`。**若设为 `true`，本机请求将
  以 admin 身份免认证通过** —— 仅在完全可信的单机环境使用

### 1.2 角色与工具授权（fail-closed）

三种角色：`admin` / `member` / `readonly`。

工具授权采用**白名单 + 默认拒绝**：只读账号只能使用 `READ_ONLY_TOOLS` 中明确列出的工具，
**不在名单里的（包括将来新增的）一律拒绝**。

```python
# core/security/permissions.py
def tool_allowed_for_user(tool_name, user) -> bool:
    if not user or user.is_admin or user.role != "readonly":
        return True
    return tool_name in READ_ONLY_TOOLS     # fail-closed
```

早期版本是黑名单（列出写类工具、其余放行），结果是新增工具一旦忘记登记，只读账号就能直接用；
实际漏掉了 `run_command`、`open_application`、整套 GUI 操作、`rename_files`、
`deduplicate_files` 等 20 余个写类甚至可执行任意命令的工具。

`tests/test_permissions.py` 会断言这些工具对 `readonly` 必须被拒绝，防止回退。

### 1.3 文件沙箱

所有涉及路径的工具都必须先过 `core/path_policy.py::assert_path_allowed()`，
它会按角色把可访问范围收窄到：

```
data/workspace/
  shared/            全员可读；readonly 不可写
  users/<用户ID>/    仅本人 + 管理员
```

写入操作还需要额外传 `write=True` 才会校验写权限。

---

## 二、审计

工具调用与文件访问会写入两处：

- `logs/audit.jsonl`（追加式，含 actor / session / 客户端 IP / 工具名 / 脱敏参数）
- SQLite `audit_events` 表（供管理页 `/api/security/audit/logs` 查询）

参数中的 `api_key` / `password` / `secret` / `token` 等字段会被 `mask_secrets()` 替换为 `***`。

> 审计上报点在 `nanobot/agent/runner.py::_execute_tools()`。
> 早期版本只在 `ToolRegistry.execute()` 里埋点，而 runner 执行工具时直调 `tool.execute()`，
> 绕过了埋点 —— 结果是文件读写、命令执行、9 个高频业务工具完全不留痕。
> 现在按「LLM 实际请求的工具名」统一补报，覆盖全部执行路径。

---

## 三、危险能力与其边界

### 3.1 命令执行（`run_command`）

- 有基于正则的危险命令黑名单（`core/desktop_tools.py::_DANGER_PATTERNS`）
- **这是缓解措施，不是安全边界**。黑名单永远可以被绕过（编码、别名、间接调用）
- 因此 `run_command` 对 `readonly` 角色是**完全禁止**的

### 3.2 代码执行（`run_code`）

`run_code` 会往子进程注入一段「沙箱前导代码」，但它**只替换了内建 `open()`**：

```python
# core/path_policy.py::run_code_sandbox_preamble
_orig_open = open
def open(file, mode="r", *args, **kwargs):
    if 模式含 rwa+x: _huanzhen_guard_path(file)
    return _orig_open(file, mode, *args, **kwargs)
```

子进程里的 `os.remove` / `shutil.rmtree` / `subprocess` / `Path.write_text`
**都不受约束**。结论：`run_code` 等价于「以服务进程权限执行任意 Python」。
生产环境请配合容器、低权限专用账号或直接关闭该工具。

### 3.3 桌面自动化（GUI 工具）

- 依赖 pyautogui 控制真实鼠标键盘，可操作宿主机的任意窗口
- `close_window` 强制弹确认；`click_position` / `drag_mouse` 的 `require_confirm` 默认关闭
- 进程级急停标志 `core/gui_abort.py`，所有 GUI 工具在每个操作前检查
- 这些工具对 `readonly` 角色全部禁止

### 3.4 归档解压

tar 解压使用 `filter="data"`（Python 3.12+），并对旧版本回退到自实现的安全解压：
拒绝绝对路径/盘符路径成员、拒绝解析后逃出目标目录的成员、跳过符号链接/硬链接/设备文件。
此前 `extractall()` 无过滤，恶意 tar 可用 `../` 成员越界写盘、绕过整条路径沙箱。

### 3.5 外发网络

`web_fetch` / `download_file` / `puppeteer` 等可发起出站请求。
`runner.py` 内置了 SSRF 分类与「重复外发查询节流」的缓解逻辑，但不构成完整防护。
内网高敏感部署建议配合网络层 egress 策略。

---

## 四、已知边界与设计取舍

| 项 | 现状 | 建议 |
|---|---|---|
| 语音 / IM 桥接入口 | 无登录用户，角色限制不生效，只剩全局沙箱 | 新增入口时显式调用 `set_request_context()`；或为桥接配置专属账号 |
| GUI 急停标志 | 进程级全局，多会话共享 | 单机单人场景可接受；多人共用一台机器时不建议开放 GUI 工具 |
| `safety` 沙箱（exec） | Windows 上 OS 级 sandbox 不可用，仅命令守卫 | 依赖容器或专用低权限账号 |
| 数据库配置密码 | Fernet 加密，密钥由 `COMPUTERNAME` 派生 | 同机可解密；跨机迁移会失败（解密异常会退化为把密文当明文，需注意） |
| `data/db_connections/*.json` | 存明文连接密码（legacy CLI 路径） | 清理或改为加密 |
| JWT 密钥自动生成 | 落在 `data/security/jwt_secret` | 生产环境请显式配置 `HUANZHEN_JWT_SECRET` |

---

## 五、上报漏洞

本项目为自托管工具，请通过仓库 Issue 反馈，或在**私下渠道**联系维护者。
请勿在公开 Issue 中粘贴真实密钥、内网地址或用户数据。

报告中请包含：影响版本/提交、复现步骤、影响范围（是否可越权/是否可写盘）、
以及你建议的修复方向。
