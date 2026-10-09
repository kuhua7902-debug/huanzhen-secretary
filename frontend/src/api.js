const TOKEN_KEY = 'huanzhen_token'

export const getToken = () => localStorage.getItem(TOKEN_KEY) || ''
export const setToken = (t) => {
  if (t) localStorage.setItem(TOKEN_KEY, t)
  else localStorage.removeItem(TOKEN_KEY)
}

export async function api(path, { method = 'GET', body, headers } = {}) {
  const h = { ...(headers || {}) }
  const token = getToken()
  if (token) h['Authorization'] = 'Bearer ' + token
  if (body !== undefined) h['Content-Type'] = 'application/json'
  const res = await fetch(path, {
    method,
    headers: h,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  const text = await res.text()
  let data = null
  try { data = text ? JSON.parse(text) : null } catch { data = text }
  if (!res.ok) {
    const d = data && (data.detail || data.message)
    const msg = Array.isArray(d)
      ? d.map((x) => (typeof x === 'string' ? x : (x.loc ? x.loc.join('.') + ': ' : '') + (x.msg || ''))).join('\n')
      : (typeof d === 'string' ? d : res.statusText)
    throw Object.assign(new Error(msg || '请求失败'), { status: res.status })
  }
  return data
}

/* ---------------- 鉴权 ---------------- */
export const securityStatus = () => api('/api/security/status')
export const login = (username, password) => api('/api/auth/login', { method: 'POST', body: { username, password } })
export const fetchMe = () => api('/api/auth/me')
export const fetchHealth = () => api('/health')

/* ---------------- 会话 ---------------- */
export const listConversations = () => api('/api/conversations')
export const getConversation = (id) => api('/api/conversations/' + encodeURIComponent(id))
export const deleteConversation = (id) => api('/api/conversations/' + encodeURIComponent(id), { method: 'DELETE' })

/* ---------------- 设置 / 能力 ---------------- */
export const getSettings = () => api('/api/settings')
export const listSkills = () => api('/api/skills')
export const activateSkill = (sessionId, skillName) =>
  api('/api/skills/activate', { method: 'POST', body: { session_id: sessionId, skill_name: skillName } })
export const deactivateSkill = (sessionId, skillName) =>
  api('/api/skills/deactivate', { method: 'POST', body: { session_id: sessionId, skill_name: skillName || '' } })
export const activeSkills = (sessionId) =>
  api('/api/skills/active', { method: 'POST', body: { session_id: sessionId } })

/* 工作空间（团队文件工作区） */
export const fileRoots = () => api('/api/files/roots')
export const fileList = (path) => api('/api/files/list?path=' + encodeURIComponent(path || ''))
export const mcpStatus = () => api('/api/mcp/status')
export const mcpServers = () => api('/api/mcp/servers')
export const displayTools = () => api('/api/tools/display')
/** 权威工具注册表（含 MCP，共 200+ 项） */
export const listTools = () => api('/tools')
/** 各模型余额 + 本地累计用量（refresh=1 强制跳过缓存） */
export const modelsBalance = (refresh) => api('/api/models/balance' + (refresh ? '?refresh=1' : ''))
export const systemStatus = () => api('/api/status')

/* 系统状态与备份（管理页「系统状态」） */
export const systemDiagnostics = (deep) =>
  api('/api/system/diagnostics' + (deep ? '?deep=1' : ''))
export const systemBackups = () => api('/api/system/backups')
export const systemBackupNow = () => api('/api/system/backup', { method: 'POST' })
/** 登录前首页用的公开概览（仅计数） */
export const publicOverview = () => api('/api/public/overview')

/* ---------------- 对话流 ---------------- */
export async function streamChat({ query, sessionId, conversationId, files, model, mode, workspace, onEvent, signal }) {
  const headers = { 'Content-Type': 'application/json' }
  const token = getToken()
  if (token) headers['Authorization'] = 'Bearer ' + token

  const res = await fetch('/chat/stream', {
    method: 'POST',
    headers,
    signal,
    body: JSON.stringify({
      query,
      session_id: sessionId || '',
      conversation_id: conversationId || '',
      files: files || [],
      model: model || '',
      mode: mode || 'auto',
      workspace: workspace || '',
    }),
  })
  const sid = res.headers.get('X-Session-Id') || ''
  const cid = res.headers.get('X-Conversation-Id') || ''
  if (!res.ok || !res.body) throw new Error('对话请求失败：HTTP ' + res.status)

  const reader = res.body.getReader()
  const dec = new TextDecoder()
  let buf = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buf += dec.decode(value, { stream: true })
    const lines = buf.split('\n')
    buf = lines.pop() || ''
    for (const line of lines) {
      if (!line.startsWith('data: ')) continue
      try { onEvent(JSON.parse(line.slice(6))) } catch { /* 忽略坏行 */ }
    }
  }
  return { sid, cid }
}

export async function cancelChat(sessionId, conversationId) {
  const qs =
    '?session_id=' + encodeURIComponent(sessionId || '') +
    '&conversation_id=' + encodeURIComponent(conversationId || '')
  try { await api('/chat/stop' + qs, { method: 'POST' }) } catch { /* 忽略 */ }
}
