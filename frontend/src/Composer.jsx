import { useEffect, useMemo, useRef, useState } from 'react'
import { getToken } from './api.js'

export const MODES = [
  { id: 'auto', name: '完全访问', desc: '允许执行全部工具（写文件 / 命令 / 桌面操作）' },
  { id: 'readonly', name: '只读模式', desc: '仅放行只读工具，写类操作会被服务端拦截' },
]

const baseName = (p) => String(p || '').replace(/[\\/]+$/, '').split(/[\\/]/).pop() || p

export default function Composer({
  value, onChange, onSend, streaming, onStop,
  models, modelKey, setModelKey, mode, setMode,
  files, setFiles,
  workspace, onPickWorkspace,
  slashCommands = [],
}) {
  const taRef = useRef(null)
  const fileRef = useRef(null)
  const [menu, setMenu] = useState('')       // '' | 'model' | 'mode' | 'plus'
  const [uploading, setUploading] = useState(false)
  const [err, setErr] = useState('')
  const [slashIdx, setSlashIdx] = useState(0)

  /* 斜杠命令：输入以 / 开头且还没打空格时展开 */
  const slashQuery = value.startsWith('/') && !value.includes(' ') ? value.slice(1).toLowerCase() : null
  const slashList = useMemo(() => {
    if (slashQuery === null) return []
    return slashCommands.filter((c) => {
      const k = c.cmd.replace(/^\//, '').toLowerCase()
      return !slashQuery || k.includes(slashQuery) || (c.desc || '').includes(slashQuery)
    })
  }, [slashQuery, slashCommands])

  useEffect(() => { setSlashIdx(0) }, [slashQuery])

  useEffect(() => {
    const onDoc = (e) => { if (!e.target.closest('.composer')) setMenu('') }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [])

  const curModel = models.find((m) => m.key === modelKey) || models[0] || { name: '默认模型' }
  const curMode = MODES.find((m) => m.id === mode) || MODES[0]

  function runSlash(c) {
    onChange('')
    const el = taRef.current
    if (el) el.style.height = 'auto'
    setTimeout(() => { try { c.run() } catch (e) { console.warn(e) } }, 0)
  }

  function keydown(e) {
    if (slashList.length > 0) {
      if (e.key === 'ArrowDown') { e.preventDefault(); setSlashIdx((v) => Math.min(v + 1, slashList.length - 1)); return }
      if (e.key === 'ArrowUp') { e.preventDefault(); setSlashIdx((v) => Math.max(v - 1, 0)); return }
      if (e.key === 'Escape') { e.preventDefault(); onChange(''); return }
      if (e.key === 'Enter') { e.preventDefault(); runSlash(slashList[slashIdx]); return }
      if (e.key === 'Tab') { e.preventDefault(); runSlash(slashList[slashIdx]); return }
    }
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      onSend()
    }
  }

  function input(e) {
    onChange(e.target.value)
    const el = e.target
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 200) + 'px'
  }

  async function pickFiles(e) {
    const list = Array.from(e.target.files || [])
    e.target.value = ''
    if (!list.length) return
    setUploading(true); setErr('')
    try {
      const out = []
      for (const f of list) {
        const fd = new FormData()
        fd.append('file', f)
        const headers = {}
        const t = getToken()
        if (t) headers['Authorization'] = 'Bearer ' + t
        const res = await fetch('/api/upload', { method: 'POST', headers, body: fd })
        const data = await res.json()
        if (!res.ok) throw new Error(data.detail || '上传失败')
        out.push({ path: data.file_path, name: data.file_name, size: data.size_str })
      }
      setFiles([...files, ...out])
    } catch (e2) {
      setErr(e2.message || '上传失败')
    } finally {
      setUploading(false); setMenu('')
    }
  }

  return (
    <div className="composer-wrap">
      <div className="composer">
        {/* 斜杠命令面板 */}
        {slashList.length > 0 && (
          <div className="slash-menu">
            {slashList.map((c, i) => (
              <button
                key={c.cmd}
                className={'slash-item' + (i === slashIdx ? ' active' : '')}
                onMouseEnter={() => setSlashIdx(i)}
                onMouseDown={(e) => { e.preventDefault(); runSlash(c) }}
              >
                <span className="sc">{c.cmd}</span>
                <span className="sd">{c.desc}</span>
                {c.tag && <span className="badge-off">{c.tag}</span>}
              </button>
            ))}
          </div>
        )}

        {(files.length > 0 || workspace) && (
          <div className="chips" style={{ marginBottom: 8 }}>
            {workspace && (
              <span className="chip on" title={workspace}>
                ⌂ {baseName(workspace)}
                <span style={{ marginLeft: 6, cursor: 'pointer' }} onClick={() => onPickWorkspace('')}>✕</span>
              </span>
            )}
            {files.map((f, i) => (
              <span key={i} className="chip">
                {f.name} <span style={{ color: 'var(--faint)' }}>{f.size}</span>
                <span style={{ marginLeft: 6, cursor: 'pointer' }} onClick={() => setFiles(files.filter((_, j) => j !== i))}>✕</span>
              </span>
            ))}
          </div>
        )}

        <textarea
          ref={taRef}
          rows={1}
          value={value}
          onChange={input}
          onKeyDown={keydown}
          placeholder="给幻帧发送任务，输入 / 查看命令，Enter 发送，Shift+Enter 换行…"
        />

        <div className="composer-bar">
          <div style={{ position: 'relative' }}>
            <button className="pill round" title="添加文件 / 工作空间" onClick={() => setMenu(menu === 'plus' ? '' : 'plus')}>
              {uploading ? '⋯' : '＋'}
            </button>
            {menu === 'plus' && (
              <div className="menu">
                <button className="menu-item" onClick={() => fileRef.current.click()}>
                  <span className="nm">▤ 上传文件<span className="sub">文档 / 表格 / 图片，可多选</span></span>
                </button>
                <button className="menu-item" onClick={() => { setMenu(''); onPickWorkspace() }}>
                  <span className="nm">⌂ 选择工作空间<span className="sub">指定本次对话的工作目录</span></span>
                </button>
              </div>
            )}
          </div>
          <input ref={fileRef} type="file" multiple style={{ display: 'none' }} onChange={pickFiles} />

          {/* 工作空间 */}
          <button className={'pill' + (workspace ? ' on' : '')} onClick={() => onPickWorkspace()}
                  title={workspace || '选择工作空间'}>
            <span>⌂</span>{workspace ? baseName(workspace) : '工作空间'}<span className="chev">⌄</span>
          </button>

          {/* 执行模式 */}
          <div style={{ position: 'relative' }}>
            <button className={'pill' + (mode === 'readonly' ? ' warn' : '')}
                    onClick={() => setMenu(menu === 'mode' ? '' : 'mode')} title="对话执行模式">
              <span>◎</span>{curMode.name}<span className="chev">⌄</span>
            </button>
            {menu === 'mode' && (
              <div className="menu">
                <div className="menu-label">执行模式</div>
                {MODES.map((m) => (
                  <button key={m.id} className={'menu-item' + (m.id === mode ? ' on' : '')}
                          onClick={() => { setMode(m.id); setMenu('') }}>
                    <span className="nm">{m.name}<span className="sub">{m.desc}</span></span>
                    {m.id === mode && <span className="tick">✓</span>}
                  </button>
                ))}
              </div>
            )}
          </div>

          <div style={{ flex: 1 }} />

          <div style={{ position: 'relative' }}>
            <button className="pill" onClick={() => setMenu(menu === 'model' ? '' : 'model')} title="切换对话模型">
              <span>✦</span>{curModel.name}<span className="chev">⌄</span>
            </button>
            {menu === 'model' && (
              <div className="menu right">
                <div className="menu-label">对话模型</div>
                {models.map((m) => (
                  <button key={m.key} className={'menu-item' + (m.key === modelKey ? ' on' : '')}
                          onClick={() => { setModelKey(m.key); setMenu('') }}>
                    <span className="nm">{m.name}<span className="sub">{m.sub}</span></span>
                    {m.key === modelKey && <span className="tick">✓</span>}
                  </button>
                ))}
              </div>
            )}
          </div>

          {streaming
            ? <button className="send-circle stop" onClick={onStop} title="停止生成">■</button>
            : <button className="send-circle" onClick={() => onSend()} disabled={!value.trim()} title="发送">↑</button>}
        </div>

        {err && <div style={{ color: 'var(--danger)', fontSize: 12, marginTop: 6 }}>{err}</div>}
      </div>
      <div className="hint-line">输入 <b>/</b> 呼出命令 · ⌘K 命令面板 · ＋ 添加文件或工作空间 · 左下切模式，右下切模型</div>
    </div>
  )
}
