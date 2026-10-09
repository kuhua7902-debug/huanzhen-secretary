import { useEffect, useState } from 'react'

const ROLE_TEXT = { admin: '管理员', member: '成员', readonly: '只读' }

export default function Sidebar({
  user, conversations, activeConvId, collapsed,
  onNewChat, onSelectConv, onDeleteConv,
  onOpenTasks, onOpenPlugins, onOpenSettings, onOpenPalette,
  onLogout, mode, onToggleMode, onOpenLanding,
  workspaceRoots = [], workspace, onWorkspacePick,
}) {
  const [recentOpen, setRecentOpen] = useState(true)
  const [userMenu, setUserMenu] = useState(false)

  // 账户菜单：点击外部 / Esc 关闭
  useEffect(() => {
    if (!userMenu) return
    const onDown = (e) => { if (!e.target.closest('.side-user-wrap')) setUserMenu(false) }
    const onKey = (e) => { if (e.key === 'Escape') setUserMenu(false) }
    document.addEventListener('mousedown', onDown)
    window.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      window.removeEventListener('keydown', onKey)
    }
  }, [userMenu])

  return (
    <aside className={'side' + (collapsed ? ' collapsed' : '')}>
      <div className="side-top">
        <button className="workspace" onClick={onOpenPalette} title="打开命令面板">
          <span className="mark">◆</span>
          <span>幻帧</span>
          <span className="chev">⌄</span>
        </button>
        <button className="side-icon-btn" onClick={onOpenPalette} title="搜索对话 / ⌘K" aria-label="搜索对话">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor"
               strokeWidth="2.1" strokeLinecap="round">
            <circle cx="10.5" cy="10.5" r="6.8" />
            <path d="M20 20l-4.1-4.1" />
          </svg>
        </button>
        <button className="side-icon-btn" onClick={onNewChat} title="新聊天" aria-label="新聊天">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor"
               strokeWidth="2.1" strokeLinecap="round" strokeLinejoin="round">
            <path d="M4 20h4L20 8a2.83 2.83 0 0 0-4-4L4 16v4z" />
            <path d="M14.5 6.5l3 3" />
          </svg>
        </button>
      </div>

      <div className="side-actions">
        <button className="side-action" onClick={onNewChat}>
          <span className="ic">✎</span>新聊天
        </button>
        <button className="side-action" onClick={onOpenTasks}>
          <span className="ic">◷</span>定时任务
        </button>
        <button className="side-action" onClick={onOpenPlugins}>
          <span className="ic">⚉</span>插件
        </button>
      </div>

      <div className="side-scroll">
        {workspaceRoots.length > 0 && (
          <div className="side-group">
            <div className="side-group-head"><span className="chev">▾</span>工作空间</div>
            {workspaceRoots.map((r) => (
              <button
                key={r.id || r.path}
                className={'side-item' + (workspace === r.path ? ' active' : '')}
                onClick={() => onWorkspacePick(workspace === r.path ? '' : r.path)}
                title={r.path}
              >
                <span className="ttl">⌂ {r.name || r.path}</span>
              </button>
            ))}
            {workspace && (
              <button className="side-item" onClick={() => onWorkspacePick('')}>
                <span className="ttl" style={{ color: 'var(--faint)' }}>✕ 不限定目录</span>
              </button>
            )}
          </div>
        )}

        <div className="side-group">
          <button className="side-group-head" onClick={() => setRecentOpen((v) => !v)}>
            <span className="chev">{recentOpen ? '▾' : '▸'}</span>最近
          </button>
          {recentOpen && (
            conversations.length === 0
              ? <div className="side-empty">还没有历史对话</div>
              : conversations.map((c) => (
                <button
                  key={c.id}
                  className={'side-item' + (c.id === activeConvId ? ' active' : '')}
                  onClick={() => onSelectConv(c)}
                  title={c.title || c.id}
                >
                  <span className="ttl">{c.title || '未命名对话'}</span>
                  <span
                    className="del"
                    onClick={(e) => { e.stopPropagation(); onDeleteConv(c) }}
                    title="删除对话"
                  >✕</span>
                </button>
              ))
          )}
        </div>
      </div>

      <div className="side-user-wrap">
        {userMenu && (
          <div className="menu side-menu">
            <button className="menu-item" onClick={() => { setUserMenu(false); onOpenSettings() }}>
              <span className="nm">⚙ 设置<span className="sub">外观 / 模型 / 工具 / 系统模块</span></span>
            </button>
            <button className="menu-item" onClick={() => { setUserMenu(false); onToggleMode && onToggleMode() }}>
              <span className="nm">{mode === 'light' ? '🌙 切换到夜间模式' : '☀ 切换到白天模式'}</span>
            </button>
            {onOpenLanding && (
              <button className="menu-item" onClick={() => { setUserMenu(false); onOpenLanding() }}>
                <span className="nm">◈ 查看介绍页</span>
              </button>
            )}
            <div className="menu-sep" />
            <button className="menu-item danger" onClick={() => { setUserMenu(false); onLogout() }}>
              <span className="nm">⏻ 退出登录</span>
            </button>
          </div>
        )}
        <div
          className="side-user"
          onClick={() => setUserMenu((v) => !v)}
          title="账户与设置"
        >
          <div className="ava">{(user?.display_name || user?.username || '?').slice(0, 1).toUpperCase()}</div>
          <div className="meta">
            <div className="nm">{user?.display_name || user?.username || '未登录'}</div>
            <div className="rl">{ROLE_TEXT[user?.role] || ''}</div>
          </div>
          <span className="gear">⚙</span>
        </div>
      </div>
    </aside>
  )
}
