/**
 * 首页演示脚本 —— 素材来自项目里一次**真实的**会话记录
 * （sessions/user_..._fd98cba9ae71.jsonl，视觉操控 Windows 计算器 3+7）。
 *
 * 这里做的是「脚本化回放」而不是视频：由播放器按节奏把事件喂给消息渲染器，
 * 因此画面与真实产品 100% 一致，且矢量、可换主题、可改文案、零体积。
 * thinking / tool 参数 / 坐标 / 表格数据都取自那次真实运行。
 */

export const DEMO_USER = '打开计算器，用视觉操作算一下 3 + 7 等于多少？'

export const DEMO_THINKING =
  '用户要的是真正的视觉能力测试，不是直接算出答案。' +
  '根据记忆，Windows 计算器用 `calc` 启动；' +
  '然后截图 → 视觉定位按钮坐标 → 鼠标点击，最后再截图确认显示结果。'

export const DEMO_TOOLS = [
  { name: 'open_application', arg: 'calc', ms: '0.06s' },
  { name: 'screenshot_and_find', arg: '数字 3', ms: '1.21s' },
  { name: 'click_element', arg: '3 · (1045, 1093) · High', ms: '0.31s' },
  { name: 'click_element', arg: '+ · (1247, 1093) · High', ms: '0.28s' },
  { name: 'click_element', arg: '7 · (663, 893) · High', ms: '0.30s' },
  { name: 'click_element', arg: '= · (1247, 1178) · High', ms: '0.27s' },
  { name: 'screenshot_and_find', arg: '显示结果 "10" (1299, 507)', ms: '1.05s' },
  { name: 'verify_output', arg: '截图比对通过', ms: '0.14s' },
]

export const DEMO_SELFCHECK = { passed: true, summary: '执行前系统自检通过 · 工具可达、坐标置信度满足阈值' }

export const DEMO_ANSWER = `## 🎯 视觉操控计算器 — 3 + 7 = **10** ✅

| 步骤 | 操作 | 视觉定位坐标 | 置信度 |
|:----:|------|:----------:|:-----:|
| 1 | 打开计算器 \`calc\`（从记忆调用） | — | — |
| 2 | 点击 **3** | (1045, 1093) | 🟢 High |
| 3 | 点击 **+** | (1247, 1093) | 🟢 High |
| 4 | 点击 **7** | (663, 893) | 🟢 High |
| 5 | 点击 **=** | (1247, 1178) | 🟢 High |
| 6 | 验证结果 **10** | (1299, 507) | 🟢 High |

全程纯视觉识别操控：**截图 → 定位 → 点击**，没有调用任何 \`calculator()\` 之类的取巧函数。`

/* ── 首页其它文案数据 ── */

export const CAPABILITIES = [
  { icon: '▤', title: '办公文档', desc: 'Word / Excel / PPT 生成与修改，合同、报告、周报模板化产出' },
  { icon: '◈', title: '知识库检索', desc: '文档解析 → 分块 → 向量索引 → 带出处的问答（RAG）' },
  { icon: '⌗', title: '智能问数', desc: '自然语言生成 SQL，连接 MySQL / PostgreSQL 查询并解释结果' },
  { icon: '◫', title: '数据分析', desc: 'CSV / Excel 清洗、聚合、异常检测、图表报告一体化' },
  { icon: '⬒', title: '桌面自动化', desc: '截图 + 视觉定位 + 鼠标键盘，操控任意桌面软件' },
  { icon: '◎', title: '语音交互', desc: '唤醒词 + STT + TTS，托盘常驻的语音助手' },
]

export const EXECUTION_STEPS = [
  { n: '01', title: '思考', desc: 'ReAct 循环先规划，思考过程实时折叠展示，可展开追溯每一步推理' },
  { n: '02', title: '调用工具', desc: '65+ 内置工具 + MCP 外部工具，每次调用显示工具名、参数与真实耗时' },
  { n: '03', title: '自检与验证', desc: '复杂任务执行前系统自检；产出交付物后强制 verify_output 校验' },
]

export const ENGINEERING = [
  { title: '双引擎架构', desc: 'nanobot ReAct 引擎（Web）与 CoreAgent（语音 / IM）并行，共享工具层' },
  { title: '权限 fail-closed', desc: '只读账号仅放行白名单工具，未知工具默认拒绝，而非默认放行' },
  { title: '全链路审计', desc: '工具调用与文件访问写入 audit.jsonl 与 SQLite，可按用户回溯' },
  { title: '文件沙箱', desc: '所有路径经过策略校验，按角色限制到共享 / 个人目录，防越界访问' },
]
