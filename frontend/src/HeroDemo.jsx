import { useEffect, useRef, useState } from 'react'
import { renderMarkdown } from './lib.js'
import {
  DEMO_ANSWER, DEMO_SELFCHECK, DEMO_THINKING, DEMO_TOOLS, DEMO_USER,
} from './landingDemo.js'

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

/**
 * 首页 Hero 的动态演示：脚本化回放一次真实会话。
 * 不联网、不调模型、循环播放；滚出视野或切到后台自动暂停。
 */
export default function HeroDemo() {
  const [items, setItems] = useState([])
  const boxRef = useRef(null)
  const stageRef = useRef(null)
  const alive = useRef(true)
  const paused = useRef(false)

  /* 视口外交互 / 标签页切走 → 暂停 */
  useEffect(() => {
    const el = stageRef.current
    let obs
    if (el && 'IntersectionObserver' in window) {
      obs = new IntersectionObserver(([e]) => { paused.current = !e.isIntersecting }, { threshold: 0.12 })
      obs.observe(el)
    }
    const onVis = () => { paused.current = document.hidden }
    document.addEventListener('visibilitychange', onVis)
    return () => { obs && obs.disconnect(); document.removeEventListener('visibilitychange', onVis) }
  }, [])

  useEffect(() => {
    alive.current = true

    const hold = async () => {
      let guard = 0
      while (paused.current && alive.current && guard < 900) { await sleep(120); guard += 1 }
    }

    const typeInto = async (id, full, speed) => {
      for (let i = 2; i <= full.length + 2; i += 2) {
        if (!alive.current) return
        await hold()
        const n = i
        setItems((prev) => prev.map((it) => (it.id === id ? { ...it, text: full.slice(0, n) } : it)))
        await sleep(speed)
      }
    }

    const run = async () => {
      let round = 0
      while (alive.current) {
        round += 1
        setItems([])
        await sleep(800)

        setItems([{ id: 'u', kind: 'user', text: DEMO_USER }])
        await sleep(1000)

        const tid = 'think-' + round
        setItems((p) => [...p, { id: tid, kind: 'thinking', text: '', startedAt: Date.now() }])
        await sleep(260)
        await typeInto(tid, DEMO_THINKING, 16)
        await sleep(620)

        for (let i = 0; i < DEMO_TOOLS.length; i += 1) {
          if (!alive.current) return
          await hold()
          const t = DEMO_TOOLS[i]
          setItems((p) => [...p, { id: `tool-${round}-${i}`, kind: 'tool', name: t.name, arg: t.arg, ms: t.ms }])
          await sleep(t.ms === '0.06s' ? 460 : 640)
        }

        setItems((p) => [...p, {
          id: 'sc-' + round, kind: 'badge', cls: 'result',
          text: '自检通过：' + DEMO_SELFCHECK.summary,
        }])
        await sleep(760)

        let elapsed = 0
        setItems((p) => p.map((it) => {
          if (it.kind !== 'thinking') return it
          elapsed = Date.now() - it.startedAt
          return { ...it, collapsed: true, elapsed }
        }))

        const aid = 'ans-' + round
        setItems((p) => [...p, { id: aid, kind: 'assistant', text: '' }])
        await typeInto(aid, DEMO_ANSWER, 7)
        await sleep(4600)
      }
    }

    run()
    return () => { alive.current = false }
  }, [])

  useEffect(() => {
    const el = boxRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [items])

  const mmss = (ms) => {
    const s = Math.floor(ms / 1000)
    return String(Math.floor(s / 60)).padStart(2, '0') + ':' + String(s % 60).padStart(2, '0')
  }

  return (
    <div className="hero-demo" ref={stageRef}>
      <div className="hero-demo-bar">
        <span className="hd-dots"><i /><i /><i /></span>
        <span className="hd-title">幻帧 · 实时执行</span>
        <span className="hd-live"><i />LIVE</span>
      </div>
      <div className="hero-demo-body" ref={boxRef}>
        {items.length === 0 && (
          <div className="hero-demo-idle">正在准备演示…</div>
        )}
        {items.map((it) => {
          if (it.kind === 'user') {
            return (
              <div key={it.id} className="row user">
                <div className="bubble">{it.text}</div>
              </div>
            )
          }
          if (it.kind === 'assistant') {
            return (
              <div key={it.id} className="row assistant">
                <div className="avatar">幻</div>
                <div className="bubble" dangerouslySetInnerHTML={{ __html: renderMarkdown(it.text) }} />
              </div>
            )
          }
          if (it.kind === 'thinking') {
            return (
              <div key={it.id} className="card-fold">
                <div className="hd">
                  <span>◈ 思考过程{it.collapsed && it.elapsed ? <span className="timer">{mmss(it.elapsed)}</span> : <span className="timer">进行中…</span>}</span>
                  <span style={{ color: 'var(--faint)', fontSize: 10 }}>{it.collapsed ? '▸' : '▾'}</span>
                </div>
                {!it.collapsed && <div className="bd">{it.text}</div>}
              </div>
            )
          }
          if (it.kind === 'tool') {
            return (
              <div key={it.id} className="demo-tool">
                <span className="dt-ic">▸</span>
                <span className="dt-name">{it.name}</span>
                <span className="dt-arg">{it.arg}</span>
                <span className="dt-ms">{it.ms}</span>
              </div>
            )
          }
          return (
            <div key={it.id} className={'badge ' + (it.cls || '')}>
              <span className="pulse" /><span>{it.text}</span>
            </div>
          )
        })}
      </div>
    </div>
  )
}
