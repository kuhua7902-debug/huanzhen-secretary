import { useCallback, useEffect, useRef, useState } from 'react'
import {
  deleteConversation, fetchHealth, fetchMe, fileRoots, getSettings, listConversations,
  publicOverview, securityStatus, setToken, setupStatus,
} from './api.js'
import Landing from './Landing.jsx'
import { applyAppearance, loadAppearance, saveAppearance } from './theme.js'
import Sidebar from './Sidebar.jsx'
import ChatView from './ChatView.jsx'
import Login from './Login.jsx'
import Palette from './Palette.jsx'
import { PluginsDialog, SettingsDialog, TasksDialog } from './Dialogs.jsx'

const LEGACY = '/static/index.html'

function modelLabel(key, cfg) {
  const pretty = { deepseek: 'DeepSeek', openai: 'OpenAI', ollama: 'Ollama（本地）', zhipu: '智谱 GLM', qwen: '通义千问', qwen_vl: '通义千问 VL' }
  return pretty[key] || key
}

export default function App() {
  const [phase, setPhase] = useState('boot')          // boot | landing | login | app
  const [overview, setOverview] = useState({})
  // 看过一次介绍页后，之后直接进登录，避免每次都要滚一遍
  const seenLanding = typeof localStorage !== 'undefined'
    && localStorage.getItem('huanzhen_seen_landing') === '1'
  const [notice, setNotice] = useState('')
  const [user, setUser] = useState(null)
  const [online, setOnline] = useState(null)
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [dialog, setDialog] = useState('')            // '' | settings | plugins | tasks
  const [settingsTab, setSettingsTab] = useState('')  // 打开设置时定位到的分区
  const [collapsed, setCollapsed] = useState(false)
  const [setup, setSetup] = useState(null)            // 首次配置状态

  const [conversations, setConversations] = useState([])
  const [activeConv, setActiveConv] = useState(null)  // {id,title} | null
  const [nonce, setNonce] = useState(0)               // 强制清空会话

  const [modelList, setModelList] = useState([])
  const [modelKey, setModelKey] = useState(() => localStorage.getItem('huanzhen_model') || '')
  const [mode, setMode] = useState(() => localStorage.getItem('huanzhen_mode') || 'auto')
  const [appearance, setAppearanceState] = useState(() => loadAppearance())
  // 工作空间：会话级，选中后随每次请求发给后端
  const [workspace, setWorkspace] = useState('')
  const [workspaceRoots, setWorkspaceRoots] = useState([])

  /** 改动外观并立即持久化（顶栏切换白天/夜间、设置面板都用它） */
  const setAppearance = useCallback((patch) => {
    setAppearanceState((prev) => {
      const next = typeof patch === 'function' ? patch(prev) : { ...prev, ...patch }
      saveAppearance(next)
      return next
    })
  }, [])

  const newChatRef = useRef(null)
  const convIdRef = useRef('')

  /* ---------- 外观 ---------- */
  useEffect(() => { applyAppearance(appearance) }, [appearance])
  useEffect(() => { localStorage.setItem('huanzhen_model', modelKey || '') }, [modelKey])
  useEffect(() => { localStorage.setItem('huanzhen_mode', mode || 'auto') }, [mode])

  /* ---------- 启动 ---------- */
  useEffect(() => {
    let alive = true
    const entry = localStorage.getItem('huanzhen_seen_landing') === '1' ? 'login' : 'landing'
    publicOverview().then((d) => { if (alive) setOverview(d || {}) }).catch(() => {})
    ;(async () => {
      try {
        const st = await securityStatus()
        if (!st.auth_enabled || st.authenticated) {
          let u = st.user || null
          if (!u) { try { u = (await fetchMe()).user } catch { /* ignore */ } }
          if (!alive) return
          setUser(u)
          setPhase('app')
        } else {
          setPhase(entry)
        }
      } catch {
        if (alive) { setNotice('无法连接后端服务'); setPhase(entry) }
      }
    })()
    return () => { alive = false }
  }, [])

  const goLogin = useCallback(() => {
    localStorage.setItem('huanzhen_seen_landing', '1')
    setPhase('login')
  }, [])
  const goLanding = useCallback(() => setPhase('landing'), [])

  /* ---------- 健康检查 ---------- */
  useEffect(() => {
    if (phase !== 'app') return
    let alive = true
    const tick = async () => {
      try { const h = await fetchHealth(); if (alive) setOnline(h && h.status === 'healthy') }
      catch { if (alive) setOnline(false) }
    }
    tick()
    const t = setInterval(tick, 15000)
    return () => { alive = false; clearInterval(t) }
  }, [phase])

  /* ---------- 会话列表 ---------- */
  const refreshConversations = useCallback(async () => {
    try {
      const d = await listConversations()
      const list = (d.conversations || []).map((c) => ({
        id: String(c.id),
        title: c.title || '未命名对话',
        updated_at: c.updated_at,
        message_count: c.message_count,
      }))
      setConversations(list)
    } catch { /* ignore */ }
  }, [])

  useEffect(() => { if (phase === 'app') refreshConversations() }, [phase, refreshConversations])

  /* ---------- 首次配置状态 ---------- */
  useEffect(() => {
    if (phase !== 'app') return
    let alive = true
    setupStatus().then((d) => { if (alive) setSetup(d) }).catch(() => {})
    return () => { alive = false }
  }, [phase])

  /* ---------- 工作空间入口 ---------- */
  useEffect(() => {
    if (phase !== 'app') return
    let alive = true
    fileRoots()
      .then((d) => { if (alive) setWorkspaceRoots((d && d.roots) || []) })
      .catch(() => {})
    return () => { alive = false }
  }, [phase])

  /* ---------- 模型列表 ---------- */
  useEffect(() => {
    if (phase !== 'app') return
    getSettings().then((d) => {
      const models = (d && d.config && d.config.models) || {}
      const def = models.default || ''
      const list = Object.keys(models)
        .filter((k) => k !== 'default' && models[k] && typeof models[k] === 'object')
        .map((k) => ({
          key: k,
          name: modelLabel(k, models[k]),
          modelId: models[k].model || '',
          sub: [models[k].model, models[k].base_url].filter(Boolean).join(' · '),
          isDefault: k === def,
        }))
        .sort((a, b) => (b.isDefault ? 1 : 0) - (a.isDefault ? 1 : 0))
      setModelList(list)
      if (!modelKey && def) setModelKey(def)
    }).catch(() => {
      setModelList([{ key: '', name: '默认模型', sub: '未读取到 models 配置' }])
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase])

  /* ---------- ⌘K ---------- */
  useEffect(() => {
    const onKey = (e) => {
      if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')) {
        e.preventDefault(); setPaletteOpen((v) => !v)
      }
      if ((e.metaKey || e.ctrlKey) && e.key === ',') { e.preventDefault(); setDialog('settings') }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const logout = useCallback(() => {
    setToken(''); setUser(null); setPhase('login')
    setConversations([]); setActiveConv(null)
  }, [])

  const startNewChat = useCallback(() => {
    setActiveConv(null)
    convIdRef.current = ''
    setNonce((n) => n + 1)
    if (newChatRef.current) newChatRef.current()
  }, [])

  const openConv = useCallback((c) => {
    convIdRef.current = c.id
    setActiveConv({ id: c.id, title: c.title })
  }, [])

  const removeConv = useCallback(async (c) => {
    if (!window.confirm('删除该对话？此操作不可恢复。')) return
    try { await deleteConversation(c.id) } catch { /* ignore */ }
    setConversations((prev) => prev.filter((x) => x.id !== c.id))
    if (activeConv && activeConv.id === c.id) startNewChat()
  }, [activeConv, startNewChat])

  const onConversationCreated = useCallback((cid, title) => {
    convIdRef.current = cid
    setActiveConv({ id: cid, title: title || '新对话' })
    refreshConversations()
  }, [refreshConversations])

  const commands = [
    { id: 'new', icon: '✦', title: '新聊天', key: 'N', run: startNewChat },
    { id: 'settings', icon: '⚙', title: '打开设置', key: ',', run: () => setDialog('settings') },
    { id: 'plugins', icon: '⚉', title: '插件（MCP / 技能 / 工具）', run: () => setDialog('plugins') },
    { id: 'tasks', icon: '◷', title: '定时任务', run: () => setDialog('tasks') },
    { id: 'console', icon: '↗', title: '打开经典控制台', run: () => window.open(LEGACY, '_blank') },
    { id: 'theme', icon: '◐', title: '更换对话背景 / 字体大小', run: () => { setDialog('settings') } },
    {
      id: 'mode', icon: '◑', title: '切换白天 / 夜间模式',
      run: () => setAppearance({ mode: appearance.mode === 'light' ? 'dark' : 'light' }),
    },
    ...modelList.map((m) => ({
      id: 'model-' + m.key, icon: '✦', title: '切换模型 · ' + m.name,
      run: () => setModelKey(m.key),
    })),
    { id: 'logout', icon: '⏻', title: '退出登录', run: logout },
  ]

  if (phase === 'boot') {
    return <div className="login-wrap"><div style={{ color: 'var(--faint)', fontFamily: 'var(--mono)', fontSize: 13 }}>正在连接幻帧引擎…</div></div>
  }
  if (phase === 'landing') {
    return (
      <Landing
        onLogin={goLogin}
        overview={overview}
        mode={appearance.mode}
        onToggleMode={() => setAppearance({ mode: appearance.mode === 'light' ? 'dark' : 'light' })}
        seenBefore={seenLanding}
      />
    )
  }
  if (phase === 'login') {
    return (
      <Login
        notice={notice}
        overview={overview}
        onSuccess={(u) => { setUser(u); setPhase('app') }}
        onBack={goLanding}
      />
    )
  }

  return (
    <div className="app">
      <Sidebar
        user={user}
        conversations={conversations}
        activeConvId={activeConv?.id || ''}
        collapsed={collapsed}
        onNewChat={startNewChat}
        onSelectConv={openConv}
        onDeleteConv={removeConv}
        onOpenTasks={() => setDialog('tasks')}
        onOpenPlugins={() => setDialog('plugins')}
        onOpenSettings={() => setDialog('settings')}
        onOpenPalette={() => setPaletteOpen(true)}
        onLogout={logout}
        mode={appearance.mode}
        onToggleMode={() => setAppearance({ mode: appearance.mode === 'light' ? 'dark' : 'light' })}
        onOpenLanding={goLanding}
        workspaceRoots={workspaceRoots}
        workspace={workspace}
        onWorkspacePick={setWorkspace}
      />

      <div className="main">
        <div className="main-head">
          <button className="head-btn" onClick={() => setCollapsed((v) => !v)} title="切换侧边栏">☰</button>
          <div className="sub">
            {modelList.find((m) => m.key === modelKey)?.name || '默认模型'} · {mode === 'readonly' ? '只读模式' : '完全访问'}
          </div>
          <div className="spacer" />
          <span className="status-pill">
            <span className={'dot ' + (online === true ? 'on' : online === false ? 'off' : '')} />
            {online === true ? '引擎在线' : online === false ? '离线' : '连接中'}
          </span>
          <button
            className="head-btn"
            onClick={() => setAppearance({ mode: appearance.mode === 'light' ? 'dark' : 'light' })}
            title="切换白天 / 夜间模式"
          >{appearance.mode === 'light' ? '🌙' : '☀'}</button>
          <button className="head-btn" onClick={() => setDialog('plugins')}>插件</button>
          <button className="head-btn" onClick={() => setDialog('settings')}>设置</button>
          <button className="head-btn primary" onClick={startNewChat}>＋ 新聊天</button>
        </div>

        {setup && !setup.model_ready && (
          <div className="setup-notice">
            <span className="sn-ic">⚠</span>
            <div className="sn-body">
              <b>还没配置模型密钥</b>
              <span> —— {setup.reason || '对话将无法使用'}。请到「设置 → 配置向导」填写，保存后重启服务生效。</span>
            </div>
            <button
              className="head-btn primary"
              onClick={() => { setSettingsTab('setup'); setDialog('settings') }}
            >去配置</button>
          </div>
        )}

        <ChatView
          key={nonce + ':' + (activeConv?.id || 'new')}
          activeConv={activeConv}
          user={user}
          onNewChatRef={newChatRef}
          onConversationCreated={onConversationCreated}
          onOpenPlugins={() => setDialog('plugins')}
          models={modelList.length ? modelList : [{ key: '', name: '默认模型', sub: '' }]}
          modelKey={modelKey}
          setModelKey={setModelKey}
          mode={mode}
          setMode={setMode}
          workspace={workspace}
          setWorkspace={setWorkspace}
        />
      </div>

      {dialog === 'settings' && (
        <SettingsDialog
          onClose={() => { setDialog(''); setSettingsTab('') }}
          user={user}
          modelList={modelList}
          appearance={appearance}
          setAppearance={setAppearance}
          initialTab={settingsTab}
        />
      )}
      {dialog === 'plugins' && <PluginsDialog onClose={() => setDialog('')} />}
      {dialog === 'tasks' && (
        <TasksDialog onClose={() => setDialog('')} onOpenConsole={() => window.open(LEGACY, '_blank')} />
      )}

      <Palette open={paletteOpen} onClose={() => setPaletteOpen(false)} commands={commands} />
    </div>
  )
}
