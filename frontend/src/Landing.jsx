import { useEffect } from 'react'
import HeroDemo from './HeroDemo.jsx'
import { CAPABILITIES, ENGINEERING, EXECUTION_STEPS } from './landingDemo.js'

/** 滚动淡入：进入视口加 .in */
function useReveal() {
  useEffect(() => {
    const els = Array.from(document.querySelectorAll('.reveal'))
    if (!('IntersectionObserver' in window)) {
      els.forEach((e) => e.classList.add('in'))
      return
    }
    const io = new IntersectionObserver((entries) => {
      entries.forEach((en) => { if (en.isIntersecting) { en.target.classList.add('in'); io.unobserve(en.target) } })
    }, { threshold: 0.12, rootMargin: '0px 0px -60px 0px' })
    els.forEach((e) => io.observe(e))
    return () => io.disconnect()
  }, [])
}

export default function Landing({ onLogin, overview, mode, onToggleMode, seenBefore }) {
  useReveal()

  const stats = [
    { n: (overview.tools || 65) + '+', t: '内置工具' },
    { n: overview.mcp || 8, t: 'MCP 服务' },
    { n: overview.skills || 20, t: '技能包' },
    { n: '389', t: '测试用例' },
  ]

  const scrollTo = (id) => {
    const el = document.getElementById(id)
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  return (
    <div className="landing">
      {/* ── 顶部导航（登录按钮常驻） ── */}
      <header className="ld-nav">
        <div className="ld-brand">
          <span className="mark">◆</span>
          <span className="name">幻帧</span>
          <span className="tag">Huanzhen Agent</span>
        </div>
        <nav className="ld-links">
          <button onClick={() => scrollTo('cap')}>能力</button>
          <button onClick={() => scrollTo('exec')}>执行过程</button>
          <button onClick={() => scrollTo('eng')}>工程</button>
        </nav>
        <div className="ld-nav-right">
          <button className="ghost-btn" onClick={onToggleMode} title="切换白天 / 夜间">
            {mode === 'light' ? '🌙' : '☀'}
          </button>
          <button className="ld-login" onClick={onLogin}>
            登录{seenBefore ? '' : '体验'} →
          </button>
        </div>
      </header>

      <main className="ld-main">
        {/* ── HERO ── */}
        <section className="ld-hero">
          <div className="ld-hero-glow" />
          <div className="ld-hero-text">
            <div className="ld-badge">Windows · 本地 / 局域网部署 · 数据不外传</div>
            <h1>把 AI 变成<br /><em>真正能干活的员工</em></h1>
            <p>
              幻帧是运行在本地或企业内网的 AI 智能体。<br />
              它不只是回答问题——它能读你的文件、查你的数据库、
              操作你的桌面软件，并把结果交付成文档。
            </p>
            <div className="ld-cta">
              <button className="ld-btn primary" onClick={onLogin}>登录并开始使用 →</button>
              <button className="ld-btn" onClick={() => scrollTo('exec')}>看看它怎么工作</button>
            </div>
            <div className="ld-hero-note">基于 ReAct 引擎 · 支持 DeepSeek / 通义 / 智谱 / Ollama 本地模型</div>
          </div>

          <div className="ld-hero-demo">
            <HeroDemo />
          </div>
        </section>

        {/* ── 数据 ── */}
        <section className="ld-stats reveal">
          {stats.map((s) => (
            <div className="ld-stat" key={s.t}>
              <div className="n">{s.n}</div>
              <div className="t">{s.t}</div>
            </div>
          ))}
        </section>

        {/* ── 能力 ── */}
        <section className="ld-sec" id="cap">
          <div className="reveal">
            <h2 className="ld-h2">它能做什么</h2>
            <p className="ld-sub">不是"支持 XX"，而是任务从头做到尾：读取 → 处理 → 交付。</p>
          </div>
          <div className="ld-grid">
            {CAPABILITIES.map((c) => (
              <div className="ld-card reveal" key={c.title}>
                <div className="ic">{c.icon}</div>
                <div className="tt">{c.title}</div>
                <div className="dd">{c.desc}</div>
              </div>
            ))}
          </div>
        </section>

        {/* ── 执行过程 ── */}
        <section className="ld-sec" id="exec">
          <div className="reveal">
            <h2 className="ld-h2">执行过程完全可见</h2>
            <p className="ld-sub">
              大多数"AI 助手"只给你一个答案。幻帧把中间的每一步摊开给你看——
              这就是它和套壳聊天机器人的区别。
            </p>
          </div>
          <div className="ld-steps">
            {EXECUTION_STEPS.map((s) => (
              <div className="ld-step reveal" key={s.n}>
                <div className="num">{s.n}</div>
                <div className="tt">{s.title}</div>
                <div className="dd">{s.desc}</div>
              </div>
            ))}
          </div>
        </section>

        {/* ── 工程 ── */}
        <section className="ld-sec" id="eng">
          <div className="reveal">
            <h2 className="ld-h2">工程实现</h2>
            <p className="ld-sub">企业场景真正在意的部分：权限、审计、边界。</p>
          </div>
          <div className="ld-grid two">
            {ENGINEERING.map((e) => (
              <div className="ld-card reveal" key={e.title}>
                <div className="tt">{e.title}</div>
                <div className="dd">{e.desc}</div>
              </div>
            ))}
          </div>
        </section>

        {/* ── 底部 CTA ── */}
        <section className="ld-final reveal">
          <h2>准备好了吗？</h2>
          <p>登录后即可上传文件、连接数据库，让幻帧开始工作。</p>
          <button className="ld-btn primary big" onClick={onLogin}>登录 →</button>
        </section>
      </main>

      <footer className="ld-foot">
        <span>◆ 幻帧 Huanzhen Agent</span>
        <span>ReAct 引擎 · FastAPI · 本地优先</span>
      </footer>
    </div>
  )
}
