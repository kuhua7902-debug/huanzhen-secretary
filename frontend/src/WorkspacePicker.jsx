import { useEffect, useState } from 'react'
import { fileList, fileRoots } from './api.js'
import { Modal } from './Dialogs.jsx'

/** 工作空间选择器：浏览团队文件工作区，为本次对话指定工作目录 */
export default function WorkspacePicker({ onClose, onPick, current }) {
  const [roots, setRoots] = useState([])
  const [path, setPath] = useState('')
  const [parent, setParent] = useState('')
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')

  async function go(p) {
    if (!p && p !== '') return
    setLoading(true); setErr('')
    try {
      const d = await fileList(p)
      setPath(d.path || p)
      setParent(d.parent || '')
      setItems(d.items || [])
    } catch (e) {
      setErr(e.message || '无法打开该目录')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fileRoots()
      .then((d) => {
        const r = (d && d.roots) || []
        setRoots(r)
        if (current) go(current)
        else if (r.length) go(r[0].path)
      })
      .catch((e) => setErr(e.message || '读取工作区失败'))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const dirs = items.filter((i) => i.is_dir)

  return (
    <Modal title="选择工作空间" onClose={onClose} className="simple">
      <div className="dlg-content">
        <p className="sec-p">
          选定后，本次对话中涉及文件的任务会**优先在该目录内完成**（读取、整理、产物都放这里）。
        </p>

        <div className="ws-roots">
          {roots.map((r) => (
            <button
              key={r.id || r.path}
              className={'chip' + (path === r.path ? ' on' : '')}
              onClick={() => go(r.path)}
              title={r.path}
            >
              {r.name || r.path}
            </button>
          ))}
        </div>

        <div className="ws-path">
          <button className="head-btn" onClick={() => go(parent)} disabled={!parent}>↑ 上级</button>
          <code title={path}>{path || '…'}</code>
        </div>

        {err && <div className="err-inline">{err}</div>}

        <div className="ws-list">
          {loading && <div className="side-empty">加载中…</div>}
          {!loading && dirs.length === 0 && <div className="side-empty">该目录下没有子文件夹</div>}
          {dirs.map((d) => (
            <button key={d.path} className="ws-dir" onClick={() => go(d.path)} title={d.path}>
              <span className="ic">▸</span>
              <span className="nm">{d.name}</span>
            </button>
          ))}
        </div>

        <div className="ws-actions">
          <button className="head-btn primary" onClick={() => { onPick(path); onClose() }} disabled={!path}>
            使用此目录
          </button>
          <button className="head-btn" onClick={() => { onPick(''); onClose() }}>不限定</button>
        </div>
      </div>
    </Modal>
  )
}
