import { useEffect, useRef, useState } from 'react'

export default function Palette({ open, onClose, commands }) {
  const [q, setQ] = useState('')
  const [idx, setIdx] = useState(0)
  const inputRef = useRef(null)
  const boxRef = useRef(null)

  const filtered = commands.filter((c) => {
    const s = q.trim().toLowerCase()
    return !s || c.title.toLowerCase().includes(s) || c.id.includes(s)
  })

  useEffect(() => {
    if (open) {
      setQ('')
      setIdx(0)
      setTimeout(() => inputRef.current && inputRef.current.focus(), 20)
    }
  }, [open])

  useEffect(() => { setIdx(0) }, [q])

  // 选中项滚动进视野
  useEffect(() => {
    const box = boxRef.current
    if (!box) return
    const el = box.querySelector('.palette-item.active')
    if (el) el.scrollIntoView({ block: 'nearest' })
  }, [idx, q, open])

  if (!open) return null

  function run(i) {
    const c = filtered[i]
    if (!c) return
    onClose()
    setTimeout(() => { try { c.run() } catch (e) { console.warn(e) } }, 0)
  }

  function onKey(e) {
    if (e.key === 'ArrowDown') { e.preventDefault(); setIdx((v) => Math.min(v + 1, filtered.length - 1)) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setIdx((v) => Math.max(v - 1, 0)) }
    else if (e.key === 'Enter') { e.preventDefault(); run(idx) }
    else if (e.key === 'Escape') { e.preventDefault(); onClose() }
  }

  return (
    <div className="palette-mask" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className="palette">
        <input
          ref={inputRef}
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={onKey}
          placeholder="输入命令…（↑↓ 选择，Enter 执行，Esc 关闭）"
          autoComplete="off"
        />
        <div className="palette-list" ref={boxRef}>
          {filtered.length === 0 && (
            <div style={{ padding: 22, textAlign: 'center', color: 'var(--faint)', fontSize: 13 }}>没有匹配的命令</div>
          )}
          {filtered.map((c, i) => (
            <button
              key={c.id}
              className={'palette-item' + (i === idx ? ' active' : '')}
              onMouseEnter={() => setIdx(i)}
              onClick={() => run(i)}
            >
              <span className="ico">{c.icon}</span>
              <span>{c.title}</span>
              {c.key && <span className="kbd k">{c.key}</span>}
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}
