export function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]))
}

/** 轻量 Markdown 渲染（先转义再解析，避免 XSS）。
 *  支持：代码块 / 行内码 / 粗体 / 标题 / 分隔线 / 引用 /
 *       有序·无序列表 / 表格 / 段落。*/
export function renderMarkdown(src) {
  if (!src) return ''
  let s = escapeHtml(src).replace(/\r\n?/g, '\n')

  // 1) 先抽出代码块，避免内部内容被后续规则破坏
  const codes = []
  s = s.replace(/```[\w-]*\n?([\s\S]*?)```/g, (_m, code) => {
    codes.push(code.replace(/\n$/, ''))
    return `\u0000C${codes.length - 1}\u0000`
  })

  const lines = s.split('\n')
  const out = []
  let para = []
  let listType = ''      // 'ul' | 'ol' | ''
  let inQuote = false

  const flushPara = () => {
    if (para.length) {
      out.push('<p>' + para.join('<br/>') + '</p>')
      para = []
    }
  }
  const closeList = () => { if (listType) { out.push('</' + listType + '>'); listType = '' } }
  const closeQuote = () => { if (inQuote) { out.push('</blockquote>'); inQuote = false } }

  const inline = (t) => t
    .replace(/`([^`\n]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, '$1<em>$2</em>')

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]
    const t = line.trim()

    // 空行
    if (!t) { flushPara(); closeList(); closeQuote(); continue }

    // 代码块占位符：作为独立块，不要被包进 <p>
    if (/^\u0000C\d+\u0000$/.test(t)) {
      flushPara(); closeList(); closeQuote(); out.push(t); continue
    }

    // 表格：连续的 | a | b | 行
    if (/^\|.*\|$/.test(t) && /^\|[\s:|-]+\|$/.test((lines[i + 1] || '').trim())) {
      flushPara(); closeList(); closeQuote()
      const cells = (row) => row.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim())
      let html = '<table><thead><tr>' + cells(t).map((c) => '<th>' + inline(c) + '</th>').join('') + '</tr></thead><tbody>'
      i += 2
      for (; i < lines.length; i++) {
        const r = lines[i].trim()
        if (!/^\|.*\|$/.test(r)) { i--; break }
        html += '<tr>' + cells(r).map((c) => '<td>' + inline(c) + '</td>').join('') + '</tr>'
      }
      out.push(html + '</tbody></table>')
      continue
    }

    // 标题
    const h = t.match(/^(#{1,4})\s+(.*)$/)
    if (h) {
      flushPara(); closeList(); closeQuote()
      // # → h3；## / ### / #### → h4（模型最常用 ###，给到 h4 才够显眼）
      const lv = h[1].length === 1 ? 3 : 4
      out.push('<h' + lv + '>' + inline(h[2]) + '</h' + lv + '>')
      continue
    }

    // 分隔线
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(t)) {
      flushPara(); closeList(); closeQuote(); out.push('<hr/>'); continue
    }

    // 引用（注意：内容已被 escapeHtml 转义，'>' 现在是 '&gt;'）
    const q = t.match(/^&gt;\s?(.*)$/)
    if (q) {
      flushPara(); closeList()
      if (!inQuote) { out.push('<blockquote>'); inQuote = true }
      out.push('<div>' + inline(q[1]) + '</div>')
      continue
    }
    closeQuote()

    // 列表
    const ul = t.match(/^[-*+]\s+(.*)$/)
    const ol = t.match(/^\d+[.)]\s+(.*)$/)
    if (ul || ol) {
      flushPara()
      const want = ul ? 'ul' : 'ol'
      if (listType !== want) { closeList(); out.push('<' + want + '>'); listType = want }
      out.push('<li>' + inline((ul || ol)[1]) + '</li>')
      continue
    }
    closeList()

    para.push(inline(t))
  }
  flushPara(); closeList(); closeQuote()

  return out.join('').replace(/\u0000C(\d+)\u0000/g, (_m, i) => '<pre><code>' + codes[+i] + '</code></pre>')
}

export function fmtDuration(ms) {
  const s = Math.floor(ms / 1000)
  return String(Math.floor(s / 60)).padStart(2, '0') + ':' + String(s % 60).padStart(2, '0')
}

export function uid() {
  return Math.random().toString(36).slice(2, 10)
}
