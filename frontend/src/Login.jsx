import { useState } from 'react'
import { login, setToken } from './api.js'

/* 左侧「迷你执行流」取自真实会话的工具调用 */
const MINI_FLOW = [
  { name: 'open_application', arg: 'calc', ms: '0.06s' },
  { name: 'screenshot_and_find', arg: '数字 3', ms: '1.21s' },
  { name: 'click_element', arg: '3 · (1045, 1093)', ms: '0.31s' },
]
const POINTS = [
  '本地 / 内网部署，数据不外传',
  '65+ 内置工具 · 12 个 MCP 服务 · 25 个技能包',
  '权限 fail-closed · 文件沙箱 · 全链路审计',
]

export default function Login({ onSuccess, notice, onBack, overview }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [showPw, setShowPw] = useState(false)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [shake, setShake] = useState(false)

  async function submit(e) {
    e.preventDefault()
    if (busy) return
    setBusy(true)
    setErr('')
    try {
      const res = await login(username.trim(), password)
      setToken(res.token)
      onSuccess(res.user)
    } catch (e2) {
      setErr(e2.message || '登录失败')
      setShake(true)
      setTimeout(() => setShake(false), 420)
    } finally {
      setBusy(false)
    }
  }

  const data = overview || {}

  return (
    <div className="lg-split">
      <div className="lg-brand">
        <div className="lg-brand-bg" />
        <div className="lg-brand-glow" />
        <div className="lg-brand-dim" />
        <div className="lg-brand-inner">
          <div className="lg-logo">
            <span className="mark">◆</span> 幻帧
            <span className="tag">Huanzhen Agent</span>
          </div>
          <h1>把 AI 变成<br /><em>真正能干活的员工</em></h1>
          <ul className="lg-points">
            {POINTS.map((p) => <li key={p}>{p}</li>)}
          </ul>
          <div className="lg-mini">
            <div className="lg-mini-head">
              <span className="hd-dots"><i /><i /><i /></span>
              <span>实时执行</span>
            </div>
            {MINI_FLOW.map((t) => (
              <div className="lg-mini-row" key={t.name}>
                <span className="ic">▸</span>
                <span className="nm">{t.name}</span>
                <span className="ag">{t.arg}</span>
                <span className="ms">{t.ms}</span>
              </div>
            ))}
            <div className="lg-mini-tip">截图 → 视觉定位 → 鼠标点击，全程无取巧</div>
          </div>
          <div className="lg-brand-foot">
            v{data.version || '1.0.1-Beta'} · {data.engine || 'nanobot'} ReAct 引擎
          </div>
        </div>
      </div>

      <div className="lg-form-side">
        <form className={'lg-form' + (shake ? ' shake' : '')} onSubmit={submit}>
          <div className="lg-form-head">
            <h2><span className="mark">◆</span>登录幻帧</h2>
            <p className="sub">使用管理员分配的本地账号登录</p>
          </div>
          <div className="lg-field">
            <label>用户名</label>
            <input value={username} onChange={(e) => setUsername(e.target.value)}
              autoComplete="username" placeholder="admin" required />
          </div>
          <div className="lg-field">
            <label>密码</label>
            <div className={err ? 'lg-pw bad' : 'lg-pw'}>
              <input type={showPw ? 'text' : 'password'} value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password" placeholder="••••••" required />
              <button type="button" className="lg-eye" onClick={() => setShowPw((v) => !v)}
                title={showPw ? '隐藏密码' : '显示密码'}>{showPw ? '隐藏' : '显示'}</button>
            </div>
          </div>
          <button className="lg-submit" type="submit" disabled={busy}>{busy ? '登录中…' : '登 录'}</button>
          <div className="lg-err">{err || notice}</div>
          {onBack && (
            <div className="lg-back">
              <a href="#" onClick={(e) => { e.preventDefault(); onBack() }}>← 返回介绍页</a>
            </div>
          )}
        </form>
      </div>
    </div>
  )
}
