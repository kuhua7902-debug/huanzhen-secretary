import { useEffect, useMemo, useRef, useState } from 'react'
import {
  activateSkill, activeSkills, cancelChat, deactivateSkill, getConversation, listSkills, streamChat,
} from './api.js'
import { fmtDuration, renderMarkdown, uid } from './lib.js'
import Composer from './Composer.jsx'
import WorkspacePicker from './WorkspacePicker.jsx'

const SUGGESTIONS = [
  { t: '分析数据并出图', d: '上传 Excel，找出异常订单并生成图表报告', q: '分析这份 Excel 的销售数据，找出异常订单，并生成一份带图表的分析报告。' },
  { t: '文档总结', d: '把 PDF/Word 要点整理成结构化报告', q: '请阅读我上传的文档，提取关键结论并整理成一份结构化报告。' },
  { t: '文件自动整理', d: '按月份/类型归类共享目录文件', q: '帮我把共享文件夹里的文件按月份自动归类整理。' },
  { t: '知识库问答', d: '基于已索引资料回答问题', q: '根据知识库里的资料，总结一下相关制度的关键要点。' },
  { t: '数据库问数', d: '自然语言生成 SQL 并解释结果', q: '连接我的数据库，统计最近一个月的订单量并解释结果。' },
  { t: '桌面自动化', d: '截图定位并操作桌面应用', q: '帮我打开记事本，写一段会议纪要并保存到桌面。' },
]

const newConvId = () => 'c' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6)

export default function ChatView({
  activeConv, user, onNewChatRef, onConversationCreated, onOpenPlugins,
  models, modelKey, setModelKey, mode, setMode,
  workspace, setWorkspace,
}) {
  const [items, setItems] = useState([])
  const [input, setInput] = useState('')
  const [files, setFiles] = useState([])
  const [streaming, setStreaming] = useState(false)
  const [status, setStatus] = useState('')

  const [wsOpen, setWsOpen] = useState(false)
  const [skills, setSkills] = useState([])
  const [active, setActive] = useState([])

  const convIdRef = useRef('')
  const ctrlRef = useRef(null)
  const scrollRef = useRef(null)
  const busyRef = useRef(false)

  const convId = activeConv?.id || ''

  /** 与后端 resolve_chat_ids 对齐的 session key，可提前算出（技能激活要用） */
  const sidOf = () => {
    const uid = user?.id || ''
    const conv = convIdRef.current || ''
    if (uid && !['anonymous', 'localhost', 'service'].includes(uid) && conv) return `user:${uid}:${conv}`
    return conv
  }

  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [items])

  /* 会话切换 / 新建 */
  useEffect(() => {
    convIdRef.current = convId || newConvId()
    setInput(''); setFiles([]); setStatus(''); setActive([])
    if (!convId) { setItems([]); return }
    let alive = true
    ;(async () => {
      try {
        const data = await getConversation(convId)
        if (!alive) return
        const msgs = (data.messages || [])
          .filter((m) => m.role === 'user' || m.role === 'assistant')
          .filter((m) => typeof m.content === 'string' && m.content.trim())
          .map((m) => ({ id: uid(), kind: m.role, text: m.content }))
        setItems(msgs)
      } catch {
        if (alive) setItems([])
      }
    })()
    return () => { alive = false }
  }, [convId])

  /* 技能列表 + 当前会话已激活技能 */
  useEffect(() => {
    listSkills().then((d) => setSkills((d && d.skills) || [])).catch(() => {})
  }, [])
  useEffect(() => {
    const sid = sidOf()
    if (!sid) return
    activeSkills(sid).then((d) => setActive((d && d.active_skills) || [])).catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [convId, user?.id])

  function newChat() {
    if (streaming) stop()
    setItems([]); setInput(''); setFiles([]); setStatus(''); setActive([])
  }
  useEffect(() => { if (onNewChatRef) onNewChatRef.current = newChat })

  function stop() {
    cancelChat(sidOf(), convIdRef.current)
    if (ctrlRef.current) ctrlRef.current.abort()
  }

  async function toggleSkill(name) {
    const sid = sidOf()
    if (!sid) return
    try {
      if (active.includes(name)) {
        await deactivateSkill(sid, name)
        setActive((a) => a.filter((x) => x !== name))
      } else {
        const d = await activateSkill(sid, name)
        setActive((d && d.active_skills) || [...active, name])
      }
    } catch { /* 忽略 */ }
  }

  const slashCommands = useMemo(() => {
    const base = [
      { cmd: '/new', desc: '新建对话' },
      { cmd: '/workspace', desc: '选择工作空间目录' },
      { cmd: '/tools', desc: '查看工具与插件' },
    ]
    const withRun = [
      { ...base[0], run: () => { if (onNewChatRef && onNewChatRef.current) onNewChatRef.current() } },
      { ...base[1], run: () => setWsOpen(true) },
      { ...base[2], run: () => onOpenPlugins && onOpenPlugins() },
    ]
    const skillCmds = skills.map((s) => ({
      cmd: '/use ' + s.name,
      desc: (s.description || '').slice(0, 70),
      tag: active.includes(s.name) ? '已激活' : '技能',
      run: () => toggleSkill(s.name),
    }))
    return [...withRun, ...skillCmds]
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [skills, active, onNewChatRef, onOpenPlugins])

  async function send(textArg) {
    const q = (textArg != null ? textArg : input).trim()
    if (!q || busyRef.current) return
    busyRef.current = true
    setInput('')
    const sentFiles = files.map((f) => f.path)
    const sentNames = files.map((f) => f.name)
    setFiles([])
    setItems((prev) => [...prev, { id: uid(), kind: 'user', text: q, files: sentNames }])
    setStreaming(true)
    setStatus('思考中…')

    const msgId = uid()
    let thinkId = null
    let acc = ''
    const ctrl = new AbortController()
    ctrlRef.current = ctrl

    try {
      const { cid } = await streamChat({
        query: q,
        sessionId: sidOf(),
        conversationId: convIdRef.current,
        files: sentFiles,
        model: modelKey,
        mode,
        workspace,
        signal: ctrl.signal,
        onEvent(evt) {
          switch (evt.phase) {
            case 'think_token': {
              const tok = evt.token || ''
              setStatus(/调用工具|🔧/.test(tok) ? '运行工具…' : '思考中…')
              if (!thinkId) {
                thinkId = uid()
                setItems((prev) => [...prev, { id: thinkId, kind: 'thinking', text: '', startedAt: Date.now(), collapsed: false }])
              }
              setItems((prev) => prev.map((it) => (it.id === thinkId ? { ...it, text: it.text + tok } : it)))
              break
            }
            case 'thinking':
              setStatus('思考中…' + (evt.round ? ' (' + evt.round + ')' : ''))
              break
            case 'selfcheck_start':
              setItems((prev) => [...prev, { id: uid(), kind: 'badge', cls: 'thinking', text: '执行前系统自检…' }])
              break
            case 'selfcheck_result':
              setItems((prev) => [...prev, {
                id: uid(), kind: 'badge', cls: evt.passed ? 'result' : 'error',
                text: (evt.passed ? '自检通过' : '自检未通过') + (evt.summary ? '：' + evt.summary : ''),
              }])
              break
            case 'system_notice':
              setItems((prev) => [...prev, { id: uid(), kind: 'badge', cls: 'result', text: evt.message || '系统提示' }])
              break
            case 'answer': {
              acc += evt.token || ''
              setStatus('回答中…')
              setItems((prev) => {
                const has = prev.some((it) => it.id === msgId)
                if (!has) return [...prev, { id: msgId, kind: 'assistant', text: acc }]
                return prev.map((it) => (it.id === msgId ? { ...it, text: acc } : it))
              })
              break
            }
            case 'error':
              setItems((prev) => [...prev, { id: uid(), kind: 'badge', cls: 'error', text: evt.message || '出错了' }])
              break
            default:
              break
          }
        },
      })
      if (cid && onConversationCreated) onConversationCreated(cid, q.slice(0, 24))
    } catch (e) {
      if (e.name !== 'AbortError') {
        setItems((prev) => [...prev, { id: uid(), kind: 'badge', cls: 'error', text: e.message || '请求失败' }])
      }
    } finally {
      busyRef.current = false
      setStreaming(false)
      setStatus('')
      ctrlRef.current = null
      setItems((prev) => prev.map((it) =>
        it.kind === 'thinking' && it.startedAt && !it.elapsed
          ? { ...it, elapsed: Date.now() - it.startedAt, collapsed: true }
          : it
      ))
    }
  }

  function toggleFold(id) {
    setItems((prev) => prev.map((it) => (it.id === id ? { ...it, collapsed: !it.collapsed } : it)))
  }
  const copy = (text) => { try { navigator.clipboard.writeText(text) } catch { /* ignore */ } }

  return (
    <div className="chat-area">
      <div className="chat-bg" />
      <div className="chat-bg-dim" />

      <div className="chat-scroll" ref={scrollRef}>
        <div className="chat-inner">
          {items.length === 0 && (
            <div className="empty">
              <div className="glyph">◆</div>
              <h3>你好，我是幻帧</h3>
              <p>企业级 AI 智能体 · ReAct 引擎 · 65+ 工具链 · MCP 扩展 · 知识库检索 · 多步任务编排。</p>
              <div className="suggest">
                {SUGGESTIONS.map((s) => (
                  <button key={s.t} className="suggest-card" onClick={() => send(s.q)}>
                    <div className="t">{s.t}</div>
                    <div className="d">{s.d}</div>
                  </button>
                ))}
              </div>
            </div>
          )}

          {items.map((it) => {
            if (it.kind === 'user' || it.kind === 'assistant') {
              return (
                <div key={it.id} className={'row ' + it.kind}>
                  {it.kind === 'assistant' && <div className="avatar">幻</div>}
                  <div>
                    <div className="bubble" dangerouslySetInnerHTML={{ __html: renderMarkdown(it.text) }} />
                    {it.files && it.files.length > 0 && (
                      <div className="chips" style={{ marginTop: 6, justifyContent: 'flex-end' }}>
                        {it.files.map((n, i) => <span key={i} className="chip">📎 {n}</span>)}
                      </div>
                    )}
                    <div className="msg-actions">
                      <button onClick={() => copy(it.text)}>复制</button>
                      {it.kind === 'assistant' && <button onClick={() => send(it.text.slice(0, 200))}>重试</button>}
                    </div>
                  </div>
                </div>
              )
            }
            if (it.kind === 'thinking') {
              return (
                <div key={it.id} className="card-fold">
                  <div className="hd" onClick={() => toggleFold(it.id)}>
                    <span>◈ 执行过程<span className="timer">{it.elapsed ? fmtDuration(it.elapsed) : '进行中…'}</span></span>
                    <span style={{ color: 'var(--faint)', fontSize: 10 }}>{it.collapsed ? '▸' : '▾'}</span>
                  </div>
                  {!it.collapsed && <div className="bd">{it.text}</div>}
                </div>
              )
            }
            return (
              <div key={it.id} className={'badge ' + (it.cls || '')}>
                <span className="pulse" /><span>{it.text}</span>
              </div>
            )
          })}

          {streaming && status && (
            <div className="badge thinking"><span className="pulse" /><span>{status}</span></div>
          )}
        </div>
      </div>

      <Composer
        value={input}
        onChange={setInput}
        onSend={send}
        streaming={streaming}
        onStop={stop}
        models={models}
        modelKey={modelKey}
        setModelKey={setModelKey}
        mode={mode}
        setMode={setMode}
        files={files}
        setFiles={setFiles}
        workspace={workspace}
        onPickWorkspace={(p) => { if (p === '') { setWorkspace(''); return } setWsOpen(true) }}
        slashCommands={slashCommands}
      />

      {wsOpen && (
        <WorkspacePicker
          current={workspace}
          onClose={() => setWsOpen(false)}
          onPick={(p) => setWorkspace(p || '')}
        />
      )}
    </div>
  )
}
