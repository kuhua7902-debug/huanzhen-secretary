import { useCallback, useEffect, useRef, useState } from 'react'
import {
  listSkills, listTools, mcpServers, mcpStatus, modelsBalance,
  saveSetupEnv, setupStatus, systemBackupNow, systemDiagnostics,
  systemRetentionPreview, systemRetentionRun,
} from './api.js'
import { FONT_SCALES, PRESETS, applyAppearance, readImageFile, saveAppearance } from './theme.js'

const LEGACY = '/static/index.html'

/* ─────────────────────── 通用弹窗 ─────────────────────── */
export function Modal({ title, onClose, children, className = '' }) {
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  return (
    <div className="mask center" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className={'dlg ' + className}>
        <div className="dlg-head">
          <span className="t">{title}</span>
          <button className="dlg-x" onClick={onClose}>✕</button>
        </div>
        {children}
      </div>
    </div>
  )
}

/* ─────────────────────── 设置 ─────────────────────── */
const SETTINGS_SECTIONS = [
  { id: 'setup', name: '配置向导', icon: '✧', adminOnly: true },
  { id: 'appearance', name: '外观与背景', icon: '◐' },
  { id: 'models', name: '模型', icon: '✦' },
  { id: 'tools', name: '工具能力', icon: '⚒' },
  { id: 'skills', name: '技能', icon: '⌘' },
  { id: 'mcp', name: 'MCP 服务', icon: '⚉' },
  { id: 'system', name: '系统状态', icon: '◈', adminOnly: true },
  { sep: '系统模块（经典控制台）' },
  { id: 'knowledge', name: '知识库', icon: '◇', embed: 'knowledge' },
  { id: 'files', name: '团队文件', icon: '◇', embed: 'files' },
  { id: 'database', name: '数据库', icon: '◇', embed: 'database' },
  { id: 'stats', name: '统计与用量', icon: '◇', embed: 'stats' },
  { id: 'admin', name: '用户与审计', icon: '◇', embed: 'admin', adminOnly: true },
]

export function SettingsDialog({ onClose, user, modelList, appearance, setAppearance, initialTab }) {
  const [tab, setTab] = useState(initialTab || 'appearance')

  return (
    <Modal title="设置" onClose={onClose} className="settings">
      <div className="dlg-rail">
        {SETTINGS_SECTIONS.map((s, i) => {
          if (s.sep) return <div key={i} className="rail-sep">{s.sep}</div>
          if (s.adminOnly && user?.role !== 'admin') return null
          return (
            <button key={s.id} className={'rail-item' + (tab === s.id ? ' on' : '')} onClick={() => setTab(s.id)}>
              <span>{s.icon}</span>{s.name}
            </button>
          )
        })}
      </div>
      <div className="dlg-body">
        {(() => {
          const sec = SETTINGS_SECTIONS.find((x) => x.id === tab)
          if (sec && sec.embed) {
            return (
              <div className="dlg-content flush">
                <iframe title={sec.name} src={LEGACY + '?page=' + sec.embed + '&embed=1'} />
              </div>
            )
          }
          return (
            <div className="dlg-content">
              {tab === 'setup' && <SetupPanel />}
              {tab === 'appearance' && <AppearancePanel appearance={appearance} setAppearance={setAppearance} />}
              {tab === 'models' && <ModelsPanel modelList={modelList} />}
              {tab === 'tools' && <ToolsPanel />}
              {tab === 'skills' && <SkillsPanel />}
              {tab === 'mcp' && <McpPanel />}
              {tab === 'system' && <SystemPanel />}
            </div>
          )
        })()}
      </div>
    </Modal>
  )
}

function AppearancePanel({ appearance, setAppearance }) {
  const fileRef = useRef(null)
  const [err, setErr] = useState('')

  function update(patch) {
    const next = { ...appearance, ...patch }
    setAppearance(next)
    saveAppearance(next)
    applyAppearance(next)
  }

  async function pick(e) {
    const f = (e.target.files || [])[0]
    e.target.value = ''
    if (!f) return
    try {
      const url = await readImageFile(f)
      // 风景照通常细节多，自动给一个"压暗"默认值保证正文可读（用户可再调）
      update({ image: url, dim: appearance.dim < 0.35 ? 0.55 : appearance.dim })
      setErr('')
    } catch (e2) { setErr(e2.message) }
  }

  return (
    <>
      <div className="sec-h">显示模式</div>
      <div className="chips" style={{ marginBottom: 18 }}>
        <button className={'chip' + (appearance.mode !== 'light' ? ' on' : '')} onClick={() => update({ mode: 'dark' })}>🌙 夜间模式</button>
        <button className={'chip' + (appearance.mode === 'light' ? ' on' : '')} onClick={() => update({ mode: 'light' })}>☀ 白天模式</button>
      </div>

      <div className="sec-h">字体大小</div>
      <p className="sec-p">整体界面与对话正文字号同步缩放，觉得字小就调大一档。</p>
      <div className="chips" style={{ marginBottom: 18 }}>
        {FONT_SCALES.map((f) => (
          <button
            key={f.id}
            className={'chip' + (appearance.fontScale === f.id ? ' on' : '')}
            onClick={() => update({ fontScale: f.id })}
          >{f.name}</button>
        ))}
      </div>

      <div className="sec-h">对话背景</div>
      <p className="sec-p">
        更换对话区背景图，让你的作品集更有辨识度。选择内置主题，或上传自己的图片（保存在本地浏览器，不上传服务器）。
      </p>
      <div className="chips" style={{ marginBottom: 16 }}>
        {PRESETS.map((p) => (
          <button
            key={p.id}
            className={'chip preview' + (!appearance.image && appearance.preset === p.id ? ' on' : '')}
            style={{ background: p.id === 'none' ? '#0B0F17' : p.value }}
            title={p.name}
            onClick={() => update({ preset: p.id, image: '' })}
          />
        ))}
        {appearance.image && <span className="chip on">自定义图片</span>}
      </div>

      <div className="field-row">
        <label>自定义背景图</label>
        <button className="pill" onClick={() => fileRef.current.click()}>选择图片…</button>
        <input ref={fileRef} type="file" accept="image/*" style={{ display: 'none' }} onChange={pick} />
        {appearance.image && (
          <button className="pill" style={{ marginLeft: 8 }} onClick={() => update({ image: '' })}>清除</button>
        )}
        {err && <div style={{ color: 'var(--danger)', fontSize: 12, marginTop: 6 }}>{err}</div>}
      </div>

      <div className="field-row">
        <label>模糊程度 {appearance.blur}px</label>
        <input type="range" min="0" max="24" value={appearance.blur}
               onChange={(e) => update({ blur: Number(e.target.value) })} />
      </div>
      <div className="field-row">
        <label>压暗程度 {Math.round(appearance.dim * 100)}%</label>
        <input type="range" min="0" max="0.9" step="0.05" value={appearance.dim}
               onChange={(e) => update({ dim: Number(e.target.value) })} />
      </div>
      <p className="sec-p">提示：背景存在时建议把「压暗」调到 40%–60%，保证正文可读性。</p>
    </>
  )
}

function ModelsPanel({ modelList }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')

  const load = useCallback(async (refresh) => {
    setLoading(true); setErr('')
    try { setData(await modelsBalance(refresh)) }
    catch (e) { setErr(e.message || '查询失败') }
    finally { setLoading(false) }
  }, [])

  useEffect(() => { load(false) }, [load])

  const norm = (s) => String(s || '').toLowerCase().replace(/[^a-z0-9]/g, '')
  const usageList = (data && data.usage) || []
  const usageBy = new Map(usageList.map((u) => [norm(u.model), u]))
  const balanceBy = new Map(((data && data.models) || []).map((b) => [b.provider, b]))
  const money = (n) => '¥' + Number(n || 0).toFixed(2)

  const usedKeys = new Set()
  const usageOf = (m) => {
    const u = usageBy.get(norm(m.modelId))
    if (u) usedKeys.add(norm(u.model))
    return u || null
  }
  const renderUsage = (u) => {
    if (!u) return <span className="v dim">暂无调用</span>
    if (u.cost > 0) {
      return (
        <span className="v ok">
          {money(u.cost)}
          <em> · {u.calls} 次调用</em>
        </span>
      )
    }
    return (
      <span className="v">
        {u.calls} 次
        <em> · 未记录 token 用量</em>
      </span>
    )
  }

  return (
    <>
      <div className="sec-head-row">
        <div>
          <div className="sec-h">模型与额度</div>
          <p className="sec-p" style={{ marginBottom: 0 }}>
            可在对话框右下的模型菜单按会话切换模型。余额来自厂商官方接口，
            用量来自本机实测统计（覆盖全部模型）。
          </p>
        </div>
        <button className="head-btn" onClick={() => load(true)} disabled={loading}>
          {loading ? '查询中…' : '刷新余额'}
        </button>
      </div>
      {err && <div className="err-inline">{err}</div>}

      <div className="tile-list" style={{ marginTop: 14 }}>
        {modelList.map((m) => {
          const b = balanceBy.get(m.key) || {}
          const u = usageOf(m)
          return (
            <div className="tile" key={m.key}>
              <div className="t">
                <span>{m.name}</span>
                {m.isDefault && <span className="badge-ok">默认</span>}
                <span style={{ flex: 1 }} />
                {b.billing_url && (
                  <a className="tile-link" href={b.billing_url} target="_blank" rel="noreferrer">
                    充值 ↗
                  </a>
                )}
              </div>
              <div className="d">{m.sub || '—'}</div>
              <div className="tile-metrics">
                <div className="m">
                  <span className="k">官方余额</span>
                  {b.status === 'ok'
                    ? <span className="v ok">{money(b.total)} <em>{b.currency}</em></span>
                    : <span className="v dim">{b.message || '—'}</span>}
                </div>
                <div className="m">
                  <span className="k">本机记录</span>
                  {renderUsage(u)}
                </div>
              </div>
            </div>
          )
        })}
        {modelList.length === 0 && <div className="side-empty">未从 /api/settings 读到模型配置</div>}
      </div>

      {(() => {
        const others = usageList.filter((u) => !usedKeys.has(norm(u.model)))
        if (!others.length) return null
        return (
          <>
            <div className="sec-h" style={{ marginTop: 22 }}>历史模型记录</div>
            <p className="sec-p">以下模型曾在本机被调用，但当前 <code>config.yaml</code> 里已不再配置。</p>
            <div className="tile-list">
              {others.map((u) => (
                <div className="tile" key={u.model}>
                  <div className="t">
                    <span style={{ fontFamily: 'var(--mono)', fontSize: 12.5 }}>{u.model}</span>
                  </div>
                  <div className="tile-metrics" style={{ borderTop: 'none', paddingTop: 0, marginTop: 6 }}>
                    <div className="m"><span className="k">调用</span>{renderUsage(u)}</div>
                  </div>
                </div>
              ))}
            </div>
          </>
        )
      })()}
    </>
  )
}

/** /api/tools/display 返回 {工具名: 中文名}；按来源拆成 内置 / 各 MCP 服务 */
function normalizeTools(raw) {
  let entries = []
  if (Array.isArray(raw)) {
    entries = raw.map((t) => [t.name, t.description || t.label || ''])
  } else if (raw && typeof raw === 'object') {
    entries = Object.entries(raw).map(([k, v]) => [k, String(v || '')])
  }
  return entries.map(([name, label]) => {
    if (name.startsWith('mcp_')) {
      const server = name.slice(4).split('_')[0]
      return { name, label, server, kind: 'mcp' }
    }
    return { name, label, server: '', kind: 'builtin' }
  })
}

function ToolsPanel() {
  const [tools, setTools] = useState([])
  const [loading, setLoading] = useState(true)
  const [q, setQ] = useState('')

  useEffect(() => {
    listTools()
      .then((d) => setTools(normalizeTools(d && d.tools)))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  const builtin = tools.filter((t) => t.kind === 'builtin')
  const byServer = new Map()
  tools.filter((t) => t.kind === 'mcp').forEach((t) => {
    if (!byServer.has(t.server)) byServer.set(t.server, [])
    byServer.get(t.server).push(t)
  })

  const match = (t) => !q || t.name.toLowerCase().includes(q.toLowerCase()) || t.label.includes(q)
  const shown = q ? tools.filter(match) : null

  return (
    <>
      <div className="sec-head-row">
        <div>
          <div className="sec-h">工具能力</div>
          <p className="sec-p" style={{ marginBottom: 0 }}>
            模型可调用的原子能力，共 <b>{tools.length}</b> 个：内置 {builtin.length} 个，
            来自 {byServer.size} 个 MCP 服务 {tools.length - builtin.length} 个。
            执行受角色权限与只读白名单约束（fail-closed）。
          </p>
        </div>
        <input
          value={q} onChange={(e) => setQ(e.target.value)}
          placeholder="搜索工具…" style={{ width: 150, padding: '7px 10px', fontSize: 12.5 }}
        />
      </div>

      {loading && <div className="side-empty">加载中…</div>}

      {shown && (
        <div className="tile-list" style={{ marginTop: 14 }}>
          {shown.length === 0 && <div className="side-empty">没有匹配的工具</div>}
          {shown.map((t) => (
            <div className="tile" key={t.name}>
              <div className="t">
                <span style={{ fontFamily: 'var(--mono)', fontSize: 12.5 }}>{t.name}</span>
                <span className="badge-off">{t.kind === 'mcp' ? t.server : '内置'}</span>
              </div>
              {t.label && <div className="d">{t.label}</div>}
            </div>
          ))}
        </div>
      )}

      {!shown && (
        <>
          <div className="tool-group">
            <div className="tg-head">内置工具 · {builtin.length}</div>
            <div className="tg-body">
              {builtin.map((t) => (
                <span className="tg-chip" key={t.name} title={t.label}>{t.name}</span>
              ))}
            </div>
          </div>
          {Array.from(byServer.entries())
            .sort((a, b) => b[1].length - a[1].length)
            .map(([server, list]) => (
              <div className="tool-group" key={server}>
                <div className="tg-head">{server} · {list.length}</div>
                <div className="tg-body">
                  {list.map((t) => (
                    <span className="tg-chip" key={t.name} title={t.label}>{t.name.slice(4 + server.length + 1)}</span>
                  ))}
                </div>
              </div>
            ))}
        </>
      )}
    </>
  )
}

function SkillsPanel() {
  const [skills, setSkills] = useState([])
  useEffect(() => { listSkills().then((d) => setSkills(d?.skills || [])).catch(() => {}) }, [])
  return (
    <>
      <div className="sec-h">技能库</div>
      <p className="sec-p">共 {skills.length} 个技能，可在对话中通过技能面板按会话激活。</p>
      <div className="tile-list">
        {skills.map((s) => (
          <div className="tile" key={s.name}>
            <div className="t"><span>{s.name}</span>{s.category && <span className="badge-off">{s.category}</span>}</div>
            <div className="d">{s.description}</div>
          </div>
        ))}
      </div>
    </>
  )
}

/** MCP 服务用途说明（用于面板展示） */
const MCP_INFO = {
  filesystem: '文件读写与目录操作，受 allowed_dirs 沙箱限制',
  memdb: '结构化长期记忆：存取、检索、更新、关系维护',
  excel: 'Excel 读写、建表、格式化、区域截图',
  charts: 'ECharts 图表生成（柱/线/饼/雷达/桑基/热力等 18 种）',
  'doc-tools': 'Word 文档创建与编辑：段落、表格、查找替换、页边距',
  'image-gen': '图片与语音生成（文生图、文生音）',
  quack: 'DuckDB 驱动的 CSV/Excel 分析：查询、异常检测、导出',
  'engineer-your-data': '数据工程：质量报告、清洗、聚合、Join、透视、可视化',
  puppeteer: '浏览器自动化：页面导航、元素操作、截图',
  weather: '天气查询',
  github: 'GitHub 仓库与 Issue 操作',
  screenshot: '屏幕截图',
}

function McpPanel() {
  const [servers, setServers] = useState([])
  const [connected, setConnected] = useState([])
  const [tools, setTools] = useState([])

  useEffect(() => {
    mcpServers()
      .then((d) => {
        const s = d && d.servers
        // 后端返回的是数组 [{name, command, args}]；兼容对象写法
        setServers(Array.isArray(s) ? s : Object.entries(s || {}).map(([name, cfg]) => ({ name, ...cfg })))
      })
      .catch(() => {})
    mcpStatus().then((d) => setConnected((d && d.connected) || [])).catch(() => {})
    listTools().then((d) => setTools(normalizeTools(d && d.tools))).catch(() => {})
  }, [])

  const countOf = (name) => tools.filter((t) => t.kind === 'mcp' && t.server === name).length
  const okCount = servers.filter((s) => connected.includes(s.name)).length

  return (
    <>
      <div className="sec-h">MCP 服务</div>
      <p className="sec-p">
        外部工具通过 MCP 协议接入（npx / node 子进程），工具名形如 <code>mcp_&lt;server&gt;_&lt;tool&gt;</code>。
        当前 <b>已连接 {okCount}/{servers.length}</b>，合计提供 {tools.filter((t) => t.kind === 'mcp').length} 个工具。
      </p>
      <div className="tile-list">
        {servers.map((s) => {
          const ok = connected.includes(s.name)
          const n = countOf(s.name)
          return (
            <div className="tile" key={s.name}>
              <div className="t">
                <span>{s.name}</span>
                {ok ? <span className="badge-ok">已连接</span> : <span className="badge-off">未连接</span>}
                <span style={{ flex: 1 }} />
                <span className="badge-off">{n} 个工具</span>
              </div>
              <div className="d">{MCP_INFO[s.name] || '外部 MCP 服务'}</div>
              <div className="d" style={{ fontFamily: 'var(--mono)', fontSize: 11, opacity: 0.75 }}>
                {[s.command, ...(s.args || [])].filter(Boolean).join(' ')}
              </div>
            </div>
          )
        })}
        {servers.length === 0 && <div className="side-empty">未读取到 MCP 配置</div>}
      </div>
    </>
  )
}

/* ─────────────────────── 配置向导 ─────────────────────── */

const SETUP_FIELDS = [
  { key: 'DEEPSEEK_API_KEY', label: 'DeepSeek 密钥', hint: '默认模型用它（platform.deepseek.com 申请，国内可直连）' },
  { key: 'ZHIPU_API_KEY', label: '智谱 GLM 密钥', hint: '可选（open.bigmodel.cn）' },
  { key: 'DASHSCOPE_API_KEY', label: '通义千问 / VL 密钥', hint: '可选；视觉识别与语音识别用它（阿里云百炼）' },
  { key: 'OPENAI_API_KEY', label: 'OpenAI 密钥', hint: '可选；国内访问需要代理' },
  { key: 'TAVILY_API_KEY', label: 'Tavily 搜索密钥', hint: '可选；联网搜索用' },
  { key: 'HUANZHEN_ADMIN_PASSWORD', label: '管理员密码', hint: '仅首次启动创建 admin 账号时使用；已有账号请用 scripts/reset_password.py' },
]

function SetupPanel() {
  const [st, setSt] = useState(null)
  const [form, setForm] = useState({})
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')

  const load = useCallback(async () => {
    try { setSt(await setupStatus()) } catch (e) { setErr(e.message || '读取配置状态失败') }
  }, [])
  useEffect(() => { load() }, [load])

  async function save() {
    const values = {}
    Object.entries(form).forEach(([k, v]) => { if (v && v.trim()) values[k] = v.trim() })
    if (Object.keys(values).length === 0) {
      setErr('请至少填写一项（空值会被忽略，以免误清空已有密钥）')
      return
    }
    setBusy(true); setErr(''); setMsg('')
    try {
      const r = await saveSetupEnv(values)
      setMsg(r.message || '已保存，需重启服务生效')
      setForm({})
      if (r.setup) setSt(r.setup)
      else load()
    } catch (e) {
      setErr(e.message || '保存失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <div className="sec-h">配置向导</div>
      <p className="sec-p">
        这里填写的密钥会写入项目根目录的 <code>.env</code>，<b>保存后需要重启服务才生效</b>
        （双击 <code>停止服务.bat</code> 再双击 <code>启动幻帧.bat</code>）。
        也可以手工编辑该文件，或双击 <code>诊断.bat</code> 查看还缺什么。
      </p>

      {st && (
        <div className="tile-list" style={{ marginBottom: 18 }}>
          <div className="tile">
            <div className="t">
              <span>当前配置状态</span>
              {st.configured
                ? <span className="badge-ok">已配置</span>
                : <span className="badge-off">尚未配置完成</span>}
            </div>
            {st.reason && <div className="d" style={{ color: 'var(--cyan)' }}>→ {st.reason}</div>}
            <div className="tile-metrics">
              <div className="m"><span className="k">默认模型</span><span className="v">{st.default_model || '—'}</span></div>
              <div className="m">
                <span className="k">模型可用</span>
                <span className={'v' + (st.model_ready ? ' ok' : ' dim')}>{st.model_ready ? '是' : '否'}</span>
              </div>
              <div className="m">
                <span className="k">管理员密码</span>
                <span className={'v' + (st.admin_password_set ? ' ok' : ' dim')}>
                  {st.admin_password_set ? '已设置' : '未设置'}
                </span>
              </div>
              <div className="m"><span className="k">已有账号</span><span className="v">{st.users}</span></div>
            </div>
            <div className="d" style={{ fontFamily: 'var(--mono)', fontSize: 11 }}>
              .env：{st.env_exists ? '存在' : '不存在'} · config.yaml：{st.config_exists ? '存在' : '不存在'}
            </div>
          </div>
        </div>
      )}

      {err && <div className="err-inline" style={{ marginBottom: 10 }}>{err}</div>}
      {msg && <div className="badge-ok" style={{ marginBottom: 10, display: 'inline-flex' }}>{msg}</div>}

      {SETUP_FIELDS.map((f) => {
        const done = !!(st && st.model_keys && st.model_keys[f.key])
          || (f.key === 'HUANZHEN_ADMIN_PASSWORD' && !!(st && st.admin_password_set))
        return (
          <div key={f.key} style={{ marginBottom: 14 }}>
            <div className="field-row">
              <label>
                {f.label}
                {done && <span className="badge-ok" style={{ marginLeft: 8 }}>已配置</span>}
              </label>
              <input
                type="password"
                autoComplete="off"
                value={form[f.key] || ''}
                placeholder={done ? '已配置（留空则不改动）' : '未配置'}
                onChange={(e) => setForm({ ...form, [f.key]: e.target.value })}
              />
            </div>
            <div className="sec-p" style={{ marginTop: 4, fontSize: 11.5 }}>{f.hint}</div>
          </div>
        )
      })}

      <div style={{ marginTop: 16, display: 'flex', gap: 8 }}>
        <button className="head-btn primary" onClick={save} disabled={busy}>
          {busy ? '保存中…' : '保存到 .env'}
        </button>
        <button className="head-btn" onClick={load} disabled={busy}>刷新状态</button>
      </div>
    </>
  )
}

/* ─────────────────────── 系统状态 ─────────────────────── */

function fmtUptime(sec) {
  const s = Number(sec || 0)
  if (s < 60) return s + ' 秒'
  if (s < 3600) return Math.floor(s / 60) + ' 分 ' + (s % 60) + ' 秒'
  return Math.floor(s / 3600) + ' 小时 ' + Math.floor((s % 3600) / 60) + ' 分'
}

const CHECK_LABEL = { ok: '通过', warn: '警告', error: '阻断', info: '提示' }

function SystemPanel() {
  const [data, setData] = useState(null)
  const [checks, setChecks] = useState(null)
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [clean, setClean] = useState('')

  const load = useCallback(async () => {
    setLoading(true); setErr('')
    try { setData(await systemDiagnostics(false)) }
    catch (e) { setErr(e.message || '读取系统状态失败') }
    finally { setLoading(false) }
  }, [])

  useEffect(() => { load() }, [load])

  async function runChecks() {
    setBusy('checks'); setErr(''); setMsg('')
    try {
      const d = await systemDiagnostics(true)
      setData(d)
      setChecks(d.checks || [])
    } catch (e) { setErr(e.message || '环境自检失败') }
    finally { setBusy('') }
  }

  async function doBackup() {
    setBusy('backup'); setErr(''); setMsg('')
    try {
      const r = await systemBackupNow()
      setMsg(`备份完成：${r.name}（${r.size_mb} MB）`)
      setData((prev) => (prev ? { ...prev, backups: r.backups } : prev))
    } catch (e) { setErr(e.message || '备份失败') }
    finally { setBusy('') }
  }

  async function doPreview() {
    setBusy('preview'); setErr(''); setMsg(''); setClean('')
    try {
      const r = await systemRetentionPreview()
      const a = r.audit || {}
      setClean(
        `预演：将归档审计 ${a.archived_lines || 0} 行、清理过期归档 ${(r.archives && r.archives.removed ? r.archives.removed.length : 0)} 个、`
        + `归档旧会话 ${(r.sessions && r.sessions.archived) || 0} 个、删除数据库审计 ${(r.database && r.database.deleted) || 0} 行`
      )
    } catch (e) { setErr(e.message || '预演失败') }
    finally { setBusy('') }
  }

  async function doClean() {
    if (!window.confirm('确认执行清理？\n\n旧审计会按月份压缩归档（不直接删除）；只有超过保留期的归档、会话与数据库审计记录才会被移除。')) return
    setBusy('clean'); setErr(''); setMsg(''); setClean('')
    try {
      const r = await systemRetentionRun()
      setMsg(r.message || '清理完成')
      load()
    } catch (e) { setErr(e.message || '清理失败') }
    finally { setBusy('') }
  }

  if (!data) {
    return <div className="side-empty">{loading ? '加载中…' : (err || '暂无数据')}</div>
  }

  const rt = data.runtime || {}
  const db = data.database || {}
  const tools = data.tools || {}
  const disk = data.disk || {}
  const backups = data.backups || []
  const lowDisk = typeof disk.free_gb === 'number' && disk.free_gb < 5

  return (
    <>
      <div className="sec-head-row">
        <div>
          <div className="sec-h">系统状态</div>
          <p className="sec-p" style={{ marginBottom: 0 }}>
            运行环境、数据规模与备份管理。命令行入口 <code>诊断.bat</code> / <code>备份.bat</code> 仍然可用——
            当服务本身起不来时，它是唯一还能用的排查入口。
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="head-btn" onClick={runChecks} disabled={busy !== ''}>
            {busy === 'checks' ? '自检中…' : '运行环境自检'}
          </button>
          <button className="head-btn" onClick={load} disabled={loading}>
            {loading ? '刷新中…' : '刷新'}
          </button>
        </div>
      </div>

      {err && <div className="err-inline" style={{ marginTop: 10 }}>{err}</div>}
      {msg && <div className="badge-ok" style={{ marginTop: 10, display: 'inline-flex' }}>{msg}</div>}

      <div className="tile-list" style={{ marginTop: 14 }}>
        <div className="tile">
          <div className="t">
            <span>运行时</span>
            <span className="badge-ok">v{rt.version}</span>
            <span style={{ flex: 1 }} />
            <span className="badge-off">{rt.platform}</span>
          </div>
          <div className="d">{rt.app}</div>
          <div className="tile-metrics">
            <div className="m"><span className="k">Python</span><span className="v">{rt.python}</span></div>
            <div className="m"><span className="k">进程号</span><span className="v">{rt.pid}</span></div>
            <div className="m"><span className="k">已运行</span><span className="v">{fmtUptime(rt.uptime_sec)}</span></div>
            <div className="m">
              <span className="k">磁盘剩余</span>
              <span className={'v' + (lowDisk ? ' dim' : ' ok')}>{disk.free_gb} / {disk.total_gb} GB</span>
            </div>
          </div>
        </div>

        <div className="tile">
          <div className="t"><span>数据规模</span></div>
          <div className="d" style={{ fontFamily: 'var(--mono)', fontSize: 11.5, opacity: 0.8 }}>{db.path}</div>
          <div className="tile-metrics">
            <div className="m"><span className="k">用户</span><span className="v">{db.users ?? '—'}</span></div>
            <div className="m"><span className="k">对话</span><span className="v">{db.conversations ?? '—'}</span></div>
            <div className="m"><span className="k">消息</span><span className="v">{db.messages ?? '—'}</span></div>
            <div className="m"><span className="k">审计事件</span><span className="v">{db.audit_events ?? '—'}</span></div>
            <div className="m"><span className="k">库大小</span><span className="v">{db.size_mb} MB</span></div>
            <div className="m">
              <span className="k">工具</span>
              <span className="v">
                内置 {tools.builtin ?? '—'}
                {tools.mcp != null && <> · MCP {tools.mcp}</>}
              </span>
            </div>
          </div>
        </div>
      </div>

      <div className="sec-head-row" style={{ marginTop: 20 }}>
        <div>
          <div className="sec-h">数据备份</div>
          <p className="sec-p" style={{ marginBottom: 0 }}>
            打包数据库 / 配置 / 安全文件为 zip，服务启动时每天自动备份一次，默认保留最近 7 份。
            <b>不含 .env</b>（含 API 密钥），请另行单独保管。
          </p>
        </div>
        <button className="head-btn primary" onClick={doBackup} disabled={busy !== ''}>
          {busy === 'backup' ? '备份中…' : '立即备份'}
        </button>
      </div>

      <div className="tile-list" style={{ marginTop: 12 }}>
        {backups.length === 0 && <div className="side-empty">暂无备份</div>}
        {backups.map((b) => (
          <div className="tile" key={b.name}>
            <div className="t">
              <span style={{ fontFamily: 'var(--mono)', fontSize: 12 }}>{b.name}</span>
              <span style={{ flex: 1 }} />
              <span className="badge-off">{b.size_mb} MB</span>
            </div>
            <div className="d">{b.created_at}</div>
          </div>
        ))}
      </div>

      <div className="sec-head-row" style={{ marginTop: 20 }}>
        <div>
          <div className="sec-h">数据保留</div>
          <p className="sec-p" style={{ marginBottom: 0 }}>
            审计日志超过 90 天会按月压缩归档到 <code>logs/archive/</code>；只有超过保留期的
            归档（365 天）、旧会话文件（180 天未更新）、数据库审计记录（180 天）才会被移除。
            <b>无法判定时间的记录一律保留</b>。服务启动时每月自动执行一次。
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="head-btn" onClick={doPreview} disabled={busy !== ''}>
            {busy === 'preview' ? '预演中…' : '预演'}
          </button>
          <button className="head-btn" onClick={doClean} disabled={busy !== ''}>
            {busy === 'clean' ? '清理中…' : '立即清理'}
          </button>
        </div>
      </div>
      {clean && (
        <div className="badge-off" style={{ marginTop: 10, display: 'inline-flex' }}>{clean}</div>
      )}

      {checks && (
        <>
          <div className="sec-h" style={{ marginTop: 20 }}>环境自检结果</div>
          <div className="tile-list" style={{ marginTop: 10 }}>
            {checks.length === 0 && <div className="side-empty">没有需要报告的问题</div>}
            {checks.map((c, i) => (
              <div className="tile" key={i}>
                <div className="t">
                  <span className={c.level === 'ok' ? 'badge-ok' : 'badge-off'}>
                    {CHECK_LABEL[c.level] || c.level}
                  </span>
                  <span>{c.title}</span>
                </div>
                {c.detail && <div className="d" style={{ fontFamily: 'var(--mono)', fontSize: 11.5 }}>{c.detail}</div>}
                {c.fix && c.level !== 'ok' && (
                  <div className="d" style={{ color: 'var(--cyan)' }}>→ {c.fix}</div>
                )}
              </div>
            ))}
          </div>
        </>
      )}
    </>
  )
}

/* ─────────────────────── 插件 ─────────────────────── */
export function PluginsDialog({ onClose }) {
  const [tab, setTab] = useState('mcp')
  return (
    <Modal title="插件" onClose={onClose} className="settings">
      <div className="dlg-rail">
        <div className="rail-sep">插件类型</div>
        <button className={'rail-item' + (tab === 'mcp' ? ' on' : '')} onClick={() => setTab('mcp')}><span>⚉</span>MCP 服务</button>
        <button className={'rail-item' + (tab === 'skills' ? ' on' : '')} onClick={() => setTab('skills')}><span>⌘</span>技能</button>
        <button className={'rail-item' + (tab === 'tools' ? ' on' : '')} onClick={() => setTab('tools')}><span>⚒</span>内置工具</button>
      </div>
      <div className="dlg-body">
        <div className="dlg-content">
          {tab === 'mcp' && <McpPanel />}
          {tab === 'skills' && <SkillsPanel />}
          {tab === 'tools' && <ToolsPanel />}
        </div>
      </div>
    </Modal>
  )
}

/* ─────────────────────── 定时任务 ─────────────────────── */
export function TasksDialog({ onClose, onOpenConsole }) {
  return (
    <Modal title="定时任务" onClose={onClose} className="simple">
      <div className="dlg-content">
        <div className="notice">
          定时任务依赖 nanobot 的 cron / heartbeat 编排层，当前部署**未启用该模块**
          （引擎代码已内置，但未接入产品运行路径），因此这里暂无可用任务列表。
        </div>
        <div className="sec-h" style={{ marginTop: 18 }}>后续可落地的方向</div>
        <div className="tile-list" style={{ marginTop: 10 }}>
          {[
            ['每早自动汇总团队日报', '定时触发 → 读取共享目录 → 生成日报并写入工作区'],
            ['文件夹监听', '新文件到达即自动解析、索引知识库或走 ETL'],
            ['模型调用预算告警', '按日/周统计 token 与成本，超阈值提醒'],
          ].map(([t, d]) => (
            <div className="tile" key={t}>
              <div className="t"><span>{t}</span><span className="badge-off">规划中</span></div>
              <div className="d">{d}</div>
            </div>
          ))}
        </div>
        <div style={{ marginTop: 16, display: 'flex', gap: 8 }}>
          <button className="head-btn" onClick={onOpenConsole}>打开经典控制台 ↗</button>
          <button className="head-btn" onClick={onClose}>知道了</button>
        </div>
      </div>
    </Modal>
  )
}
