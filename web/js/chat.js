// 智能滚动：用户不在底部时不强制下拉
function _autoScroll(el, threshold) {
  if (!el) return;
  threshold = threshold || 120;
  if (el.scrollTop + el.clientHeight >= el.scrollHeight - threshold) {
    el.scrollTop = el.scrollHeight;
  }
}

// ═══════════════ 斜杠命令处理 ═══════════════

async function compactChatHistory() {
  var sid = sessionId || currentConvId || conversationId;
  if (!sid) {
    toast('请先开始一段对话', 'error');
    return;
  }
  return handleSlashInput('/compact');
}

async function execCommand(cmd, args) {
  switch (cmd) {
    case '/new':
      showConfirm('新建对话', '确定新建对话？当前对话将保留在历史中。', function() { newChat(); });
      return true;
    case '/clear':
      showDangerConfirm('删除对话', '确定删除当前对话？此操作不可恢复！', function() {
        if (currentConvId) {
          huanzhenFetch('/api/conversations/' + currentConvId, { method: 'DELETE' }).catch(function(){});
        }
        conversationId = '';
        currentConvId = '';
        sessionId = '';
        _persistConvState();  // 修复：删除会话后同步清空持久化状态，避免 F5 恢复到已删除的会话
        document.getElementById('chatMessages').innerHTML =
          '<div class="empty-state"><div class="big-icon">' + _dualIcon('👋', 'fa-hand-wave') + '</div><h3>你好！我是幻帧</h3><p>对话已删除，可以开始新话题。</p></div>';
        document.getElementById('convTitle').textContent = '新对话';
      });
      return true;
    case '/history':
      openHistory();
      return true;
    case '/stop':
      stopStreaming();
      return true;
    default:
      return false; // 不认识的命令，交给后端或 AI
  }
}

async function fetchCommand(cmd, args) {
  var name = cmd.replace('/', '');
  var url = '/api/command/' + name;
  try {
    var res = await huanzhenFetch(url);
    if (!res.ok) return null;
    var data = await res.json();
    return data.text || '(无输出)';
  } catch(e) {
    return null;
  }
}

/** 在对话区显示命令 + 结果 */
async function showCommandResult(cmdText, resultText) {
  var msgs = document.getElementById('chatMessages');
  var empty = msgs.querySelector('.empty-state');
  if (empty) empty.remove();
  addMessage('user', escHtml(cmdText));
  addMessage('assistant', renderMarkdown(escHtml(resultText || '')));
  _autoScroll(msgs);
}

async function handleSlashInput(msg) {
  var parts = msg.split(' ');
  var cmd = parts[0].toLowerCase();
  var args = parts.slice(1);

  // 纯前端命令（不显示在对话，直接执行）
  if (cmd === '/new' || cmd === '/clear' || cmd === '/history' || cmd === '/stop') {
    execCommand(cmd, args);
    return { handled: true };
  }

  // /compact 特殊处理：POST + 切换 session
  if (cmd === '/compact') {
    var sid = sessionId || currentConvId || conversationId;
    if (!sid) { await showCommandResult(msg, '没有可压缩的对话。请先开始一段对话。'); return { handled: true }; }
    var msgs = document.getElementById('chatMessages');
    addMessage('user', escHtml(msg));
    var statusEl = document.createElement('div');
    statusEl.className = 'phase-badge phase-thinking';
    statusEl.textContent = '📦 压缩对话中...（AI 生成摘要）';
    msgs.appendChild(statusEl);
    _autoScroll(msgs);
    try {
      var res = await huanzhenFetch('/api/compact', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sid })
      });
      if (res.ok) {
        var data = await res.json();
        if (statusEl) statusEl.remove();
        addMessage('assistant', renderMarkdown(escHtml(data.text || '')));
        if (data.new_session_id) {
          sessionId = data.new_session_id;
          currentConvId = data.new_session_id;
          conversationId = data.new_session_id;
          _persistConvState();  // 修复：压缩后会话 id 已切换，同步持久化
        }
      } else {
        if (statusEl) statusEl.remove();
        addMessage('assistant', '压缩失败：服务器返回 ' + res.status);
      }
    } catch(e) {
      if (statusEl) statusEl.remove();
      addMessage('assistant', '压缩失败：' + e.message);
    }
    return { handled: true };
  }

  // 技能命令
  if (cmd === '/skills') {
    try {
      var res = await huanzhenFetch('/api/skills');
      if (!res.ok) { await showCommandResult(msg, '获取技能列表失败'); return { handled: true }; }
      var data = await res.json();
      var skills = data.skills || [];
      if (!skills.length) {
        await showCommandResult(msg, '暂无可用技能。在 skills/ 目录下放置 SKILL.md 即可添加。');
      } else {
        var lines = ['**可用技能：**'];
        skills.forEach(function(s){
          lines.push('  `/use ' + s.name + '`  — ' + s.description);
        });
        lines.push('');
        lines.push('使用 `/use 技能名` 激活技能，`/unload` 卸载');
        await showCommandResult(msg, lines.join('\n'));
      }
    } catch(e) {
      await showCommandResult(msg, '获取技能列表失败: ' + e.message);
    }
    return { handled: true };
  }

  if (cmd === '/use') {
    var skillName = args.join(' ');
    if (!skillName) { await showCommandResult(msg, '请指定技能名，如：`/use excel-analyzer`'); return { handled: true }; }
    var sid_use = sessionId || currentConvId || conversationId;
    if (!sid_use) { await showCommandResult(msg, '请先开始一段对话'); return { handled: true }; }
    try {
      var res = await huanzhenFetch('/api/skills/activate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sid_use, skill_name: skillName })
      });
      var data = await res.json();
      await showCommandResult(msg, data.message || '操作完成');
    } catch(e) {
      await showCommandResult(msg, '激活失败: ' + e.message);
    }
    return { handled: true };
  }

  if (cmd === '/unload') {
    var sid_unload = sessionId || currentConvId || conversationId;
    if (!sid_unload) { await showCommandResult(msg, '没有激活的技能'); return { handled: true }; }
    try {
      var res = await huanzhenFetch('/api/skills/deactivate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sid_unload })
      });
      var data = await res.json();
      await showCommandResult(msg, data.message || '已卸载所有技能');
    } catch(e) {
      await showCommandResult(msg, '卸载失败: ' + e.message);
    }
    return { handled: true };
  }

  // 后端命令
  var result = await fetchCommand(cmd, args);
  if (result) {
    await showCommandResult(msg, result);
    return { handled: true };
  }

  // 不认识 → 放行给 AI
  return { handled: false };
}

// ═══════════════ 快捷命令栏 ═══════════════

var QUICK_COMMANDS = [
  { cmd: '/new',       desc: '新建对话' },
  { cmd: '/clear',     desc: '删除当前对话' },
  { cmd: '/compact',   desc: '压缩对话历史' },
  { cmd: '/skills',    desc: '技能列表' },
  { cmd: '/use',       desc: '激活技能' },
  { cmd: '/unload',    desc: '卸载技能' },
  { cmd: '/status',    desc: '系统状态' },
  { cmd: '/selfcheck', desc: '运行自检' },
  { cmd: '/tools',     desc: '工具列表' },
  { cmd: '/knowledge', desc: '知识库统计' },
  { cmd: '/cost',      desc: '会话统计' },
  { cmd: '/history',   desc: '历史对话' },
];

function toggleQuickCmds() {
  var panel = document.getElementById('quickCmdBar');
  var btn = document.getElementById('qcToggleBtn');
  if (!panel || !btn) return;
  var isOpen = panel.style.display !== 'none' && panel.classList.contains('open');
  if (isOpen) {
    panel.style.display = 'none';
    panel.classList.remove('open');
    btn.classList.remove('open');
  } else {
    panel.style.display = 'flex';
    panel.classList.add('open');
    btn.classList.add('open');
  }
}

async function onQuickCmdClick(cmd) {
  // 前端命令直接执行
  if (cmd === '/new' || cmd === '/clear' || cmd === '/history' || cmd === '/stop') {
    execCommand(cmd, []);
    return;
  }
  // 命令：显示在对话再执行
  var msgs = document.getElementById('chatMessages');
  var empty = msgs.querySelector('.empty-state');
  if (empty) empty.remove();
  addMessage('user', escHtml(cmd));

  // 技能命令
  if (cmd === '/skills') { onQuickCmdSkills(); return; }
  if (cmd === '/use') { onQuickCmdUse(); return; }
  if (cmd === '/unload') { onQuickCmdUnload(); return; }

  // /compact 特殊处理：POST + 切换 session
  if (cmd === '/compact') {
    var sid = sessionId || currentConvId || conversationId;
    if (!sid) { addMessage('assistant', '没有可压缩的对话。请先开始一段对话。'); _autoScroll(msgs); return; }
    var statusEl = document.createElement('div');
    statusEl.className = 'phase-badge phase-thinking';
    statusEl.textContent = '📦 压缩对话中...（AI 生成摘要）';
    msgs.appendChild(statusEl);
    _autoScroll(msgs);
    try {
      var res = await huanzhenFetch('/api/compact', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sid })
      });
      if (res.ok) {
        var data = await res.json();
        if (statusEl) statusEl.remove();
        addMessage('assistant', renderMarkdown(escHtml(data.text || '')));
        if (data.new_session_id) {
          sessionId = data.new_session_id;
          currentConvId = data.new_session_id;
          conversationId = data.new_session_id;
          _persistConvState();  // 修复：压缩后会话 id 已切换，同步持久化
        }
      } else {
        if (statusEl) statusEl.remove();
        addMessage('assistant', '压缩失败：服务器返回 ' + res.status);
      }
    } catch(e) {
      if (statusEl) statusEl.remove();
      addMessage('assistant', '压缩失败：' + e.message);
    }
    _autoScroll(msgs);
    return;
  }

  // 普通后端命令
  var result = await fetchCommand(cmd, []);
  addMessage('assistant', renderMarkdown(escHtml(result || '')));
  _autoScroll(msgs);
}

async function onQuickCmdSkills() {
  var msgs = document.getElementById('chatMessages');
  var empty = msgs.querySelector('.empty-state');
  if (empty) empty.remove();
  addMessage('user', escHtml('/skills'));
  try {
    var res = await huanzhenFetch('/api/skills');
    if (!res.ok) { addMessage('assistant', '获取技能列表失败'); _autoScroll(msgs); return; }
    var data = await res.json();
    var skills = data.skills || [];
    if (!skills.length) {
      addMessage('assistant', '暂无可用技能');
    } else {
      var lines = ['**可用技能：**'];
      skills.forEach(function(s){ lines.push('  `/use ' + s.name + '`  — ' + s.description); });
      lines.push('');
      lines.push('使用 `/use 技能名` 激活技能，`/unload` 卸载');
      addMessage('assistant', renderMarkdown(escHtml(lines.join('\n'))));
    }
  } catch(e) {
    addMessage('assistant', '获取技能列表失败: ' + e.message);
  }
  _autoScroll(msgs);
}

async function onQuickCmdUse() {
  var msgs = document.getElementById('chatMessages');
  var empty = msgs.querySelector('.empty-state');
  if (empty) empty.remove();
  addMessage('user', escHtml('/use'));
  // 先列出可用技能，让用户选
  try {
    var res = await huanzhenFetch('/api/skills');
    if (!res.ok) { addMessage('assistant', '获取技能列表失败'); _autoScroll(msgs); return; }
    var data = await res.json();
    var sks = data.skills || [];
    if (!sks.length) { addMessage('assistant', '暂无可用技能'); _autoScroll(msgs); return; }
    var lines = ['请使用命令输入完整技能名：', ''];
    sks.forEach(function(s){ lines.push('  `/use ' + s.name + '`  — ' + s.description); });
    addMessage('assistant', renderMarkdown(escHtml(lines.join('\n'))));
  } catch(e) {
    addMessage('assistant', '获取技能列表失败: ' + e.message);
  }
  _autoScroll(msgs);
}

async function onQuickCmdUnload() {
  var msgs = document.getElementById('chatMessages');
  var empty = msgs.querySelector('.empty-state');
  if (empty) empty.remove();
  addMessage('user', escHtml('/unload'));
  var sid = sessionId || currentConvId || conversationId;
  if (!sid) { addMessage('assistant', '没有激活的技能'); _autoScroll(msgs); return; }
  try {
    var res = await huanzhenFetch('/api/skills/deactivate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sid })
    });
    var data = await res.json();
    addMessage('assistant', data.message || '已卸载所有技能');
  } catch(e) {
    addMessage('assistant', '卸载失败: ' + e.message);
  }
  _autoScroll(msgs);
}

function renderQuickCommands() {
  var bar = document.getElementById('quickCmdBar');
  if (!bar) return;
  bar.innerHTML = '';
  QUICK_COMMANDS.forEach(function(qc) {
    var btn = document.createElement('button');
    btn.className = 'qc-btn';
    btn.innerHTML = '<span class="qc-cmd">' + qc.cmd + '</span><span class="qc-desc">' + qc.desc + '</span>';
    btn.onclick = function() { onQuickCmdClick(qc.cmd); };
    bar.appendChild(btn);
  });
}
document.addEventListener('DOMContentLoaded', renderQuickCommands);

// ===== 页面切换 =====
function switchPage(page) {
  currentPage = page;
  document.querySelectorAll('.page').forEach(function(p){p.classList.remove('active');p.style.display='none';});
  var t = document.getElementById('page-' + page);
  if (t) { t.classList.add('active'); t.style.display='flex'; }
  document.querySelectorAll('.nav-item').forEach(function(n){n.classList.remove('active');});
  var ni = document.querySelector('.nav-item[data-page="' + page + '"]');
  if (ni) ni.classList.add('active');

  if (page === 'knowledge') { loadKnowledgeBase(); }
  if (page === 'files') { loadDrives(); }
  if (page === 'database') { loadDbConfigs(); loadDbConfigSelect(); }
  if (page === 'stats') { setTimeout(loadStats, 50); }
  if (page === 'tools') { setTimeout(loadToolPage, 50); }
  if (page === 'settings') { setTimeout(function() {
    if (typeof loadAuditLogs === 'function') loadAuditLogs();
  }, 50); }
  if (page === 'admin' && typeof loadAdminPage === 'function') {
    setTimeout(loadAdminPage, 50);
  }
}

function newChat() {
  conversationId = '';
  currentConvId = '';
  sessionId = '';
  _persistConvState();  // 修复：新建对话时同步清空 sessionStorage，否则 F5 会被恢复回旧会话
  document.getElementById('convTitle').textContent = '新对话';
  document.getElementById('chatMessages').innerHTML =
    '<div class="empty-state">' +
      '<div class="big-icon">' + _dualIcon('👋', 'fa-hand-wave') + '</div>' +
      '<h3>你好！我是幻帧</h3>' +
      '<p>你的企业级 AI 助手。我可以帮你整理文件、搜索知识库、分析文档、回答问题。在下方输入框开始对话。</p>' +
    '</div>';
}

// ===== 会话详情面板（右侧浮层，可隐藏） =====
// 必须用 function 声明（hoisted to global）以兼容行内 onclick
function toggleConvDetail(force) {
  try {
    console.log('[toggleConvDetail] 被调用, force=', force);
    var panel = document.getElementById('convDetailPanel');
    var btn = document.getElementById('detailToggleBtn');
    if (!panel) {
      console.error('[toggleConvDetail] 找不到 #convDetailPanel');
      try { toast('详情面板元素缺失，请刷新页面', 'error'); } catch (_) {}
      return;
    }
    var willOpen;
    if (typeof force === 'boolean') {
      willOpen = force;
      if (willOpen) panel.classList.add('open');
      else panel.classList.remove('open');
    } else {
      willOpen = !panel.classList.contains('open');
      panel.classList.toggle('open');
    }
    console.log('[toggleConvDetail] willOpen=', willOpen, 'panel.classList=', panel.className);
    // 同步头部"详情"按钮的高亮态
    if (btn) {
      if (willOpen) btn.classList.add('active');
      else btn.classList.remove('active');
    }
    if (willOpen) {
      // 用 setTimeout 0 让 panel 先完成 display 切换再异步渲染
      setTimeout(function() { renderConvDetail(); }, 0);
    }
  } catch (e) {
    console.error('[toggleConvDetail] 错误:', e);
    try { toast('详情面板打开失败：' + e.message, 'error'); } catch (_) {}
  }
}
// 同步到 window（有些场景如调试/JS 调用需要）
window.toggleConvDetail = toggleConvDetail;

async function renderConvDetail() {
  var body = document.getElementById('convDetailBody');
  if (!body) return;

  // 真实数据：当前会话 ID、创建时间
  var sid = sessionId || currentConvId || conversationId || '';
  var createdAt = '—';
  var messageCount = 0;
  var model = '—';
  var status = '进行中';
  var title = (document.getElementById('convTitle') || {}).textContent || '新对话';

  // 拉取后端设置中的默认模型
  try {
    var settings = await huanzhenFetch('/api/settings').then(function(r){ return r.ok ? r.json() : null; });
    if (settings) {
      if (settings.chat_model) model = settings.chat_model;
      else if (settings.openai_model) model = settings.openai_model;
      else if (settings.model) model = settings.model;
    }
  } catch (e) { /* 静默失败 */ }

  // 拉取当前会话的消息数和创建时间
  if (sid) {
    try {
      var resp = await huanzhenFetch('/api/conversations/' + sid);
      if (resp.ok) {
        var d = await resp.json();
        if (d && d.messages) messageCount = d.messages.length;
        if (d && d.created_at) createdAt = d.created_at;
        else if (d && d.create_time) createdAt = d.create_time;
        else if (d && d.created) createdAt = d.created;
        if (d && d.title) title = d.title;
      }
    } catch (e) { /* 静默失败 */ }
  } else {
    model = model || 'GLM-5.2';
  }

  // 格式化 createdAt
  if (createdAt && createdAt !== '—') {
    try {
      var dt = new Date(createdAt);
      if (!isNaN(dt.getTime())) {
        var pad = function(n){ return n < 10 ? '0' + n : '' + n; };
        createdAt = dt.getFullYear() + '-' + pad(dt.getMonth()+1) + '-' + pad(dt.getDate())
                  + ' ' + pad(dt.getHours()) + ':' + pad(dt.getMinutes());
      }
    } catch (e) {}
  }

  // 状态：当前有会话且在流式中 → 进行中；否则待开始
  if (typeof isStreaming !== 'undefined' && isStreaming) status = '进行中';
  else if (sid) status = '已暂停';
  else status = '待开始';

  var html = '';
  // 会话详情卡片
  html += '<div class="detail-card">';
  html += '<div class="detail-card-title">会话详情</div>';

  html += '<div class="detail-row">';
  html += '<span class="detail-label">会话 ID</span>';
  html += '<span class="detail-value">' + escHtml(sid || '尚未开始') + (sid ? '<span class="copy-icon" onclick="copySessionId()" title="复制">' + _dualIcon('📋', 'fa-copy') + '</span>' : '') + '</span>';
  html += '</div>';

  html += '<div class="detail-row">';
  html += '<span class="detail-label">创建时间</span>';
  html += '<span class="detail-value" style="font-family:var(--font)">' + escHtml(createdAt) + '</span>';
  html += '</div>';

  html += '<div class="detail-row">';
  html += '<span class="detail-label">模型</span>';
  html += '<span class="detail-value"><span class="detail-tag model">' + escHtml(model) + '</span></span>';
  html += '</div>';

  html += '<div class="detail-row">';
  html += '<span class="detail-label">消息数</span>';
  html += '<span class="detail-value" style="font-family:var(--font)">' + messageCount + '</span>';
  html += '</div>';

  html += '<div class="detail-row">';
  html += '<span class="detail-label">状态</span>';
  html += '<span class="detail-value"><span class="detail-tag ' + (status === '进行中' ? 'status-active' : 'status-done') + '">' + escHtml(status) + '</span></span>';
  html += '</div>';

  html += '<div class="detail-row">';
  html += '<span class="detail-label">标题</span>';
  html += '<span class="detail-value" style="font-family:var(--font);max-width:60%">' + escHtml(title) + '</span>';
  html += '</div>';

  html += '</div>';

  // 快捷操作卡片
  html += '<div class="detail-card">';
  html += '<div class="detail-card-title">快捷操作</div>';
  html += '<div class="quick-actions">';
  html += '<button class="quick-action-btn" onclick="quickClearSession()" title="清空当前会话消息"><span class="qa-icon">' + _dualIcon('🗑️', 'fa-trash-can') + '</span><span class="qa-label">清空会话</span></button>';
  html += '<button class="quick-action-btn" onclick="quickExportSession()" title="导出为 Markdown 文件"><span class="qa-icon">' + _dualIcon('📤', 'fa-file-export') + '</span><span class="qa-label">导出会话</span></button>';
  html += '<button class="quick-action-btn" onclick="quickShareSession()" title="复制会话链接到剪贴板"><span class="qa-icon">' + _dualIcon('🔗', 'fa-share-nodes') + '</span><span class="qa-label">分享会话</span></button>';
  var _pinned = isPinnedConv();
  html += '<button class="quick-action-btn' + (_pinned ? ' is-on' : '') + '" id="qaPinBtn" onclick="quickPinSession()" title="置顶 / 取消置顶当前会话"><span class="qa-icon">' + _dualIcon('📌', 'fa-thumbtack') + '</span><span class="qa-label">置顶会话</span><span class="qa-switch" aria-hidden="true"></span></button>';
  html += '</div>';
  html += '</div>';

  body.innerHTML = html;
}

// 置顶存储（localStorage，简单实现）
function _getPinnedConvs() {
  try { return JSON.parse(localStorage.getItem('huanzhen_pinned_convs') || '[]'); }
  catch (e) { return []; }
}
function isPinnedConv(id) {
  var sid = id || sessionId || currentConvId || conversationId || '';
  return _getPinnedConvs().indexOf(sid) >= 0;
}
function _setPinnedConvs(list) {
  try { localStorage.setItem('huanzhen_pinned_convs', JSON.stringify(list || [])); } catch (e) {}
}

async function quickClearSession() {
  var sid = sessionId || currentConvId || conversationId || '';
  if (!sid) {
    toast('当前没有可清空的会话', 'info');
    return;
  }
  if (!confirm('确定要清空当前会话吗？此操作会删除所有消息，且不可恢复。')) return;
  try {
    var resp = await huanzhenFetch('/api/conversations/' + sid, { method: 'DELETE' });
    if (resp.ok || resp.status === 204) {
      toast('会话已清空', 'success');
      // 重置前端会话状态并刷新界面
      try { newChat(); } catch (e) {}
      // 详情面板重渲染
      if (document.getElementById('convDetailPanel')?.classList.contains('open')) {
        setTimeout(renderConvDetail, 200);
      }
    } else {
      toast('清空失败：HTTP ' + resp.status, 'error');
    }
  } catch (e) {
    toast('清空失败：' + e.message, 'error');
  }
}

async function quickExportSession() {
  var sid = sessionId || currentConvId || conversationId || '';
  if (!sid) {
    toast('当前没有可导出的会话', 'info');
    return;
  }
  try {
    var resp = await huanzhenFetch('/api/conversations/' + sid);
    if (!resp.ok) { toast('获取会话失败：HTTP ' + resp.status, 'error'); return; }
    var d = await resp.json();
    var msgs = (d && d.messages) || [];
    var title = d.title || d.name || ('conversation-' + sid.substring(0, 8));
    var lines = [];
    lines.push('# ' + title);
    lines.push('');
    lines.push('**会话 ID：** ' + sid);
    lines.push('**导出时间：** ' + new Date().toLocaleString('zh-CN'));
    lines.push('**消息数：** ' + msgs.length);
    lines.push('');
    lines.push('---');
    lines.push('');
    for (var i = 0; i < msgs.length; i++) {
      var m = msgs[i];
      var role = (m.role || '').toLowerCase();
      var roleLabel = role === 'user' ? '👤 用户' : role === 'assistant' ? '🤖 助手' : (role || '消息');
      lines.push('## ' + roleLabel);
      lines.push('');
      var content = m.content || m.text || m.message || '';
      lines.push(typeof content === 'string' ? content : JSON.stringify(content, null, 2));
      lines.push('');
    }
    var md = lines.join('\n');
    var blob = new Blob([md], { type: 'text/markdown;charset=utf-8' });
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    var safeTitle = String(title).replace(/[\\/:*?"<>|]/g, '_').substring(0, 60);
    a.download = safeTitle + '_' + new Date().toISOString().slice(0,10) + '.md';
    document.body.appendChild(a);
    a.click();
    setTimeout(function(){ document.body.removeChild(a); URL.revokeObjectURL(url); }, 100);
    toast('会话已导出为 Markdown', 'success');
  } catch (e) {
    toast('导出失败：' + e.message, 'error');
  }
}

async function quickShareSession() {
  var sid = sessionId || currentConvId || conversationId || '';
  if (!sid) {
    toast('当前没有可分享的会话', 'info');
    return;
  }
  // 组装可分享链接（当前页 URL + hash 携带 convId）
  var base = location.origin + location.pathname;
  var shareUrl = base + '#conv=' + encodeURIComponent(sid);
  var shareText = '幻帧对话分享\n会话ID: ' + sid + '\n链接: ' + shareUrl;
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(shareUrl);
    } else {
      var ta = document.createElement('textarea');
      ta.value = shareText;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
    }
    toast('会话链接已复制到剪贴板', 'success');
  } catch (e) {
    toast('复制失败：' + e.message + '\n请手动复制：' + shareUrl, 'error');
  }
}

function quickPinSession() {
  var sid = sessionId || currentConvId || conversationId || '';
  if (!sid) {
    toast('当前没有可置顶的会话', 'info');
    return;
  }
  var list = _getPinnedConvs();
  var idx = list.indexOf(sid);
  if (idx >= 0) {
    list.splice(idx, 1);
    toast('已取消置顶', 'info');
  } else {
    list.push(sid);
    toast('已置顶会话', 'success');
  }
  _setPinnedConvs(list);
  // 重新渲染详情面板以更新按钮文字
  if (document.getElementById('convDetailPanel')?.classList.contains('open')) {
    setTimeout(renderConvDetail, 50);
  }
  // 触发自定义事件，让"历史"面板等可以重新排序
  try { window.dispatchEvent(new CustomEvent('huanzhen:pin-changed', { detail: { sid: sid, pinned: idx < 0 } })); } catch (e) {}
}

function copySessionId() {
  var sid = sessionId || currentConvId || conversationId || '';
  if (!sid) { toast('当前没有会话 ID', 'info'); return; }
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(sid).then(function() {
      toast('已复制会话 ID', 'success');
    }).catch(function(){
      _legacyCopy(sid);
    });
  } else {
    _legacyCopy(sid);
  }
}

function _legacyCopy(text) {
  try {
    var ta = document.createElement('textarea');
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    document.body.removeChild(ta);
    toast('已复制', 'success');
  } catch (e) {
    toast('复制失败：' + e.message, 'error');
  }
}

// ESC 关闭会话详情面板（仅在打开时）
document.addEventListener('keydown', function(e) {
  if (e.key === 'Escape') {
    var panel = document.getElementById('convDetailPanel');
    if (panel && panel.classList.contains('open')) {
      toggleConvDetail(false);
    }
  }
});

// 当切换会话或新建会话时，若详情面板打开则刷新
// 监听 newChat 已被 hook（chat.js 中 conversationId='' 等赋值后会触发）
// 用 setInterval 监听 sessionId 变化：会话切换时自动刷新详情面板
(function _watchConvDetail() {
  var lastSid = (typeof sessionId !== 'undefined') ? sessionId : '';
  setInterval(function() {
    var panel = document.getElementById('convDetailPanel');
    if (!panel || !panel.classList.contains('open')) return;
    var cur = (typeof sessionId !== 'undefined') ? sessionId : '';
    if (cur !== lastSid) {
      lastSid = cur;
      try { renderConvDetail(); } catch (e) { console.error(e); }
    }
  }, 800);
})();

// ===== 绑定"详情"按钮和面板关闭事件（用 addEventListener，不依赖行内 onclick） =====
(function _bindConvDetailEvents() {
  function bind() {
    var btn = document.getElementById('detailToggleBtn');
    if (btn && !btn._cdBound) {
      btn._cdBound = true;
      btn.addEventListener('click', function(e) {
        e.preventDefault();
        e.stopPropagation();
        toggleConvDetail();
      });
    }
    var bd = document.getElementById('convDetailBackdrop');
    if (bd && !bd._cdBound) {
      bd._cdBound = true;
      bd.addEventListener('click', function() { toggleConvDetail(); });
    }
    var closeBtn = document.getElementById('convDetailClose');
    if (closeBtn && !closeBtn._cdBound) {
      closeBtn._cdBound = true;
      closeBtn.addEventListener('click', function() { toggleConvDetail(); });
    }
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bind);
  } else {
    bind();
  }
  // 兜底：晚一点再试一次
  setTimeout(bind, 200);
  setTimeout(bind, 800);
})();

// ===== 技能面板 =====
function toggleSkillPanel() {
  var panel = document.getElementById('skillPanel');
  var isOpen = panel.classList.contains('open');
  panel.classList.toggle('open');
  if (!isOpen) loadSkillPanel();
}

// 技能快捷预设（多选组合，点一下加，再点一下减）
var SKILL_PRESETS = [
  { name: '🗄️ 数据处理', skills: ['iceberg','paimon','flink','fluss','lance','iggy','docker-compose'] },
  { name: '📄 办公文档', skills: ['docx','xlsx','pdf','pptx'] },
  { name: '🎨 内容创作', skills: ['doc-coauthoring','internal-comms','deck'] },
  { name: '💻 前端开发', skills: ['frontend-design','web-artifacts-builder','webapp-testing'] },
  { name: '🎭 设计', skills: ['canvas-design','brand-guidelines','theme-factory','algorithmic-art','slack-gif-creator'] },
];

function toggleSkillPreset(presetName) {
  var preset = null;
  for (var i = 0; i < SKILL_PRESETS.length; i++) {
    if (SKILL_PRESETS[i].name === presetName) { preset = SKILL_PRESETS[i]; break; }
  }
  if (!preset) return;
  var sid = sessionId || currentConvId || conversationId;
  if (!sid) { toast('请先开始一段对话', 'info'); return; }

  huanzhenFetch('/api/skills/active', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({session_id: sid})
  }).then(function(r){return r.json()}).then(function(d){
    var current = d.active_skills || [];
    var currentSet = {};
    current.forEach(function(n){ currentSet[n] = true; });

    var allActive = true;
    for (var i = 0; i < preset.skills.length; i++) {
      if (!currentSet[preset.skills[i]]) { allActive = false; break; }
    }

    var newSkills;
    if (allActive) {
      var removeSet = {};
      preset.skills.forEach(function(n){ removeSet[n] = true; });
      newSkills = current.filter(function(n){ return !removeSet[n]; });
    } else {
      newSkills = current.slice();
      preset.skills.forEach(function(n){
        if (newSkills.indexOf(n) < 0) newSkills.push(n);
      });
    }

    return huanzhenFetch('/api/skills/set', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({session_id: sid, skills: newSkills})
    });
  }).then(function(r){return r.json()}).then(function(d){
    if (d.status === 'ok') { loadSkillPanel(); }
    else { toast(d.message || '操作失败', 'error'); }
  }).catch(function(e){ toast('请求失败: ' + e.message, 'error'); });
}

function loadSkillPanel() {
  var body = document.getElementById('skillPanelBody');
  body.innerHTML = '<div style="text-align:center;padding:20px;color:var(--text-secondary)"><div class="spinner"></div>加载中...</div>';

  var sid = sessionId || currentConvId || conversationId;
  // 没有活跃会话时生成临时 ID，让新会话能直接看到默认技能
  if (!sid) {
    sid = 'tmp_' + Date.now().toString(36);
    sessionId = sid;
  }
  Promise.all([
    huanzhenFetch('/api/skills').then(function(r){return r.json()}).then(function(d){return d.skills || [];}),
    sid ? huanzhenFetch('/api/skills/active', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({session_id:sid})}).then(function(r){return r.json()}).then(function(d){return d.active_skills || []}).catch(function(){return [];}) : Promise.resolve([])
  ]).then(function(results){
    var skills = results[0];
    var activeSkills = results[1] || [];
    if (!skills.length) {
      body.innerHTML = '<div style="text-align:center;padding:30px;color:var(--text-secondary)">暂无可用技能</div>';
      return;
    }

    // 按分类分组
    var groups = {};
    skills.forEach(function(s){
      var cat = s.category || '其他';
      if (!groups[cat]) groups[cat] = {active: [], inactive: []};
      if (activeSkills.indexOf(s.name) >= 0) groups[cat].active.push(s);
      else groups[cat].inactive.push(s);
    });

    // 检测当前激活匹配哪个预设
    var activeSet = {};
    activeSkills.forEach(function(n){ activeSet[n] = true; });
    var matchedPreset = null;
    for (var pi = 0; pi < SKILL_PRESETS.length; pi++) {
      var p = SKILL_PRESETS[pi];
      if (p.name === '🧰 全部技能') continue;
      var match = true;
      if (p.skills.length !== Object.keys(activeSet).length) { match = false; continue; }
      for (var si = 0; si < p.skills.length; si++) {
        if (!activeSet[p.skills[si]]) { match = false; break; }
      }
      if (match) { matchedPreset = p; break; }
    }
    // 如果激活的是默认技能（docx+xlsx+pdf）也算"办公文档"预设
    if (!matchedPreset && Object.keys(activeSet).length === 3 &&
        activeSet['docx'] && activeSet['xlsx'] && activeSet['pdf'] &&
        !activeSet['pptx']) {
      matchedPreset = SKILL_PRESETS[1]; // 办公文档
    }

    // 预设栏（多选组合）
    var html = '<div class="skill-presets">';
    for (var pi = 0; pi < SKILL_PRESETS.length; pi++) {
      var p = SKILL_PRESETS[pi];
      var allIn = 0;
      for (var si = 0; si < p.skills.length; si++) {
        if (activeSet[p.skills[si]]) allIn++;
      }
      var cls = 'skill-preset-btn';
      if (allIn === p.skills.length) cls += ' active';
      else if (allIn > 0) cls += ' partial';
      html += '<button class="' + cls + '" onclick="toggleSkillPreset(\'' + p.name.replace(/'/g, "\\'") + '\')">' + p.name + '</button>';
    }
    html += '</div>';

    // 分类显示顺序
    var catOrder = ['数据处理','文档处理','内容创作','设计','开发工具','系统管理','其他'];
    catOrder.forEach(function(cat){
      var g = groups[cat];
      if (!g || (!g.active.length && !g.inactive.length)) return;
      html += '<div class="skill-section-title">' + escHtml(cat) + '</div>';
      g.active.forEach(function(s){ html += renderSkillCard(s, true); });
      g.inactive.forEach(function(s){ html += renderSkillCard(s, false); });
    });
    body.innerHTML = html;
  }).catch(function(){
    body.innerHTML = '<div style="text-align:center;padding:30px;color:var(--text-secondary)">加载失败</div>';
  });
}

function renderSkillCard(skill, isActive) {
  var name = skill.name;
  var desc = skill.description || '';
  var activeClass = isActive ? 'active' : '';
  var activeStyle = isActive ? ' style="background:#e8f5e9;border-color:#27ae60"' : '';
  var btnHtml = isActive
    ? '<button class="btn btn-sm btn-outline" style="color:var(--danger);border-color:var(--danger)" onclick="deactivateSkill(\'' + name + '\')"><span class="icon-emoji">✕</span><i class="icon-fa fa-solid fa-xmark"></i> 卸载</button>'
    : '<button class="btn btn-sm btn-primary" onclick="activateSkill(\'' + name + '\')"><span class="icon-emoji">✓</span><i class="icon-fa fa-solid fa-check"></i> 激活</button>';
  return '<div class="skill-card ' + activeClass + '"' + activeStyle + ' data-skill-name="' + escHtml(name) + '">'
    + '<div class="skill-card-name">' + escHtml(name) + '<span class="active-badge">已激活</span></div>'
    + '<div class="skill-card-toggle" onmouseenter="hoverSkillDesc(event, this, \'' + escHtml(desc) + '\')" onmouseleave="hideSkillDesc(event, this)"><span class="toggle-arrow">▸</span> 详细信息</div>'
    + '<div class="skill-card-actions">' + btnHtml + '</div>'
    + '</div>';
}

var _skillPopupTimer = null;
var _skillPopupEl = null;

function hoverSkillDesc(event, el, desc) {
  if (_skillPopupTimer) clearTimeout(_skillPopupTimer);
  _skillPopupTimer = setTimeout(function() {
    // 移除旧的浮层
    var old = document.getElementById('skillPopup');
    if (old) old.remove();

    var popup = document.createElement('div');
    popup.id = 'skillPopup';
    popup.className = 'skill-popup';
    popup.textContent = desc;

    var rect = el.getBoundingClientRect();
    // 先挂载到 body 测量实际高度
    popup.style.left = '-9999px';
    popup.style.top = '-9999px';
    document.body.appendChild(popup);
    var actualH = popup.offsetHeight;
    var spaceBelow = window.innerHeight - rect.bottom - 6;
    if (spaceBelow < actualH && rect.top > actualH + 6) {
      popup.style.top = (rect.top - actualH - 4) + 'px';
    } else {
      popup.style.top = (rect.bottom + 4) + 'px';
    }
    popup.style.left = Math.min(Math.max(rect.left, 4), window.innerWidth - 360) + 'px';

    document.body.appendChild(popup);
    _skillPopupEl = popup;

    // 浮层本身也监听 hover，避免移上去看的时候消失
    popup.onmouseenter = function() {
      if (_skillPopupTimer) clearTimeout(_skillPopupTimer);
    };
    popup.onmouseleave = function() {
      popup.remove();
      _skillPopupEl = null;
    };
  }, 500);
}

function hideSkillDesc(event, el) {
  if (_skillPopupTimer) {
    clearTimeout(_skillPopupTimer);
    _skillPopupTimer = null;
  }
  // 延迟一点点再移除，防止移回到浮层时闪动
  setTimeout(function() {
    if (_skillPopupEl && !_skillPopupEl.matches(':hover')) {
      _skillPopupEl.remove();
      _skillPopupEl = null;
    }
  }, 100);
}

function activateSkill(name) {
  var sid = sessionId || currentConvId || conversationId;
  if (!sid) { toast('请先开始一段对话', 'error'); return; }
  huanzhenFetch('/api/skills/activate', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({session_id: sid, skill_name: name})
  }).then(function(r){return r.json()}).then(function(d){
    if (d.status === 'ok') { toast(d.message, 'success'); loadSkillPanel(); }
    else { toast(d.message || '激活失败', 'error'); }
  }).catch(function(e){ toast('激活失败: ' + e.message, 'error'); });
}

function deactivateSkill(name) {
  var sid = sessionId || currentConvId || conversationId;
  if (!sid) return;
  huanzhenFetch('/api/skills/deactivate', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({session_id: sid, skill_name: name})
  }).then(function(r){return r.json()}).then(function(d){
    if (d.status === 'ok') { toast(d.message, 'success'); loadSkillPanel(); }
    else { toast(d.message || '卸载失败', 'error'); }
  }).catch(function(e){ toast('卸载失败: ' + e.message, 'error'); });
}

// 修复（死代码）：toggleConvPanel / loadConvList 引用的 #convPanel、#convList 在 index.html 中已不存在，
// 功能被历史面板（history.js 的 openHistory）取代，整体删除。

function loadConversation(id) {
  huanzhenFetch('/api/conversations/' + id).then(r => r.json()).then(d => {
    currentConvId = id;
    conversationId = id;
    sessionId = id;  // ← 关键：让后续消息发到同一个会话
    _persistConvState();  // 修复：会话切换后落盘，F5 仍在该会话里
    document.getElementById('convTitle').textContent = d.conversation.title;
    // 修复：#convPanel 已随对话列表面板一起下线，原来这里必然抛 TypeError，
    //      把后面的消息渲染整段吃掉（loadConversation 仍被 pages.js 的对话表格 onclick 调用）
    var convPanel = document.getElementById('convPanel');
    if (convPanel) convPanel.classList.remove('open');

    const msgs = document.getElementById('chatMessages');
    msgs.innerHTML = '';
    if (d.messages && d.messages.length) {
      d.messages.forEach(function(m) {
        var html = m.role === 'assistant' ? renderMarkdown(escHtml(m.content)) : escHtml(m.content);
        var bubble = addMessage(m.role, html);
        // 助理消息有思考内容 → 在其上方插入思考面板
        if (m.role === 'assistant' && m.thinking && isShowThinking()) {
          var panel = document.createElement('div');
          panel.className = 'thinking-panel';
          panel.innerHTML =
            '<div class="tp-header" onclick="this.nextElementSibling.classList.toggle(\'collapsed\');var t=this.querySelector(\'.tp-toggle\');t.textContent=t.textContent===\'▼\'?\'▶\':\'▼\'">' +
            '<span>' + _dualIcon('🧠', 'fa-brain') + ' 思考过程</span><span class="tp-toggle">▼</span></div>' +
            '<div class="tp-body">' + _safeThink(m.thinking) + '</div>';
          var msgDiv = bubble ? bubble.closest('.message') : null;
          if (msgDiv) msgs.insertBefore(panel, msgDiv);
        }
      });
    }
  }).catch(() => toast('加载对话失败', 'error'));
}

function onChatKeydown(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendChat();
  }
}

/* ═══════════════ 文件上传 ═══════════════ */
let uploadedFiles = [];
var uploadIdCounter = 0;

function handleFileSelect(event) {
  var files = event.target.files;
  if (!files.length) return;
  for (var fi = 0; fi < files.length; fi++) uploadFileItem(files[fi]);
  event.target.value = '';
}

function uploadFileItem(file) {
  var preview = document.getElementById('filePreview');
  var itemId = 'fu_' + (++uploadIdCounter);

  // 显示上传中状态
  preview.insertAdjacentHTML('beforeend',
    '<div class="file-preview" id="' + itemId + '">' +
      '<span class="file-icon">' + getFileIcon('.' + (file.name.split('.').pop() || 'bin')) + '</span>' +
      '<span class="file-name">' + escHtml(file.name) + '</span>' +
      '<span class="upload-progress">⏳ 上传中...</span>' +
    '</div>'
  );

  var formData = new FormData();
  formData.append('file', file);

  huanzhenFetch('/api/upload', { method: 'POST', body: formData })
    .then(function(r) {
      if (!r.ok) throw new Error('服务器返回: ' + r.status);
      return r.json();
    })
    .then(function(data) {
      if (data.status !== 'ok') throw new Error(data.message || '上传失败');
      uploadedFiles.push({ name: data.file_name, path: data.file_path, size: data.size });
      var el = document.getElementById(itemId);
      if (!el) return;
      // 用 data-path 代替 onclick 内联，避免反斜杠转义问题
      el.setAttribute('data-path', data.file_path);
      el.innerHTML =
        '<span class="file-icon">' + getFileIcon(data.ext) + '</span>' +
        '<span class="file-name">' + escHtml(data.file_name) + '</span>' +
        '<span class="file-size">' + (data.size_str || '') + '</span>' +
        '<span class="file-remove" title="移除">×</span>';
    })
    .catch(function(err) {
      var el = document.getElementById(itemId);
      if (el) {
        el.innerHTML = '<span class="file-icon">❌</span><span class="file-name">' + escHtml(file.name) + ' 上传失败</span>';
        el.title = err.message || '上传出错';
      }
    });
}

// 事件委托：点击 × 移除文件
document.addEventListener('click', function(e) {
  var target = e.target;
  if (!target.classList.contains('file-remove')) return;
  var preview = target.closest('.file-preview');
  if (!preview) return;
  var path = preview.getAttribute('data-path') || '';
  preview.remove();
  if (path) {
    uploadedFiles = uploadedFiles.filter(function(f) { return f.path !== path; });
  }
});

function setupDragDrop() {
  var col = document.querySelector('.chat-col');
  if (!col) return;
  col.addEventListener('dragenter', function(e) { e.preventDefault(); col.classList.add('drag-over'); });
  col.addEventListener('dragover', function(e) { e.preventDefault(); col.classList.add('drag-over'); });
  col.addEventListener('dragleave', function(e) {
    if (!e.currentTarget.contains(e.relatedTarget)) col.classList.remove('drag-over');
  });
  col.addEventListener('drop', function(e) {
    e.preventDefault();
    col.classList.remove('drag-over');
    if (e.dataTransfer.files.length) {
      for (var fi = 0; fi < e.dataTransfer.files.length; fi++) uploadFileItem(e.dataTransfer.files[fi]);
    }
  });
}
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', setupDragDrop);
} else { setupDragDrop(); }

// 中断关键词（整串精确匹配，不再用子串 indexOf）
const STOP_KEYWORDS = new Set([
  "停了", "停止", "别做了", "取消", "别点了", "stop", "别干了", "别动",
  "别继续", "不准动", "不要动了", "不要了", "别搞了"
]);

async function sendChat() {
  const input = document.getElementById('chatInput');
  const btn = document.getElementById('sendBtn');
  const msg = input.value.trim();

  // ── ⭐ 停止关键词检测：只有当"正在流式输出"且"整条消息恰好就是某个停止词"时才算中断命令 ──
  // 修复（两处）：1) 原来用 indexOf 子串匹配，像"如何取消订阅"、任何含 "stop" 的英文提问都会被吞掉，
  //              输入框被清空且没有任何反馈，用户以为消息丢了；
  //              2) 原来这段代码写在 `if (isStreaming) return;` 之后，真正在流式输出时会在那一行直接返回，
  //              所以停止命令永远不可能在需要时生效。现在改为 Set 整串精确匹配 + 仅在 isStreaming 时判定。
  if (isStreaming && STOP_KEYWORDS.has(msg.toLowerCase())) {
    input.value = '';
    input.style.height = 'auto';
    stopStreaming();
    // 清空正在显示的思考/工具面板
    document.querySelectorAll('.phase-badge').forEach(function(e) { e.remove(); });
    document.querySelectorAll('.tool-card.tc-running').forEach(function(e) {
      e.classList.remove('tc-running');
      e.classList.add('tc-done');
      var s = e.querySelector('.tc-status');
      if (s) s.textContent = '⏹ 已取消';
    });
    document.getElementById('stopBtn').style.display = 'none';
    isStreaming = false;
    btn.disabled = false;
    // 修复：给出可见反馈（原来静默清空输入框，用户完全不知道发生了什么）
    addPhase('⏹ 已中断当前回答', 'result');
    toast('已中断当前回答', 'info');
    input.focus();
    return;
  }

  // 未在流式输出 → 不是停止命令，正常发送；正在输出时不允许并发发送
  if (isStreaming) return;

  isStreaming = true;
  btn.disabled = true;

  // 清除空状态
  const msgs = document.getElementById('chatMessages');
  const empty = msgs.querySelector('.empty-state');
  if (empty) empty.remove();

  // 收集已上传文件路径并清空预览
  const files = uploadedFiles.map(function(f) { return f.path; });
  uploadedFiles = [];
  document.getElementById('filePreview').innerHTML = '';

  // 如果只有文件没有文字，用文件名作为 query
  var queryText = msg;
  if (!queryText && files.length) {
    queryText = '请帮我处理这些文件：' + files.map(function(f) {
      return f.split('/').pop().split('\\').pop();
    }).join(', ');
  }
  if (!queryText) { isStreaming = false; btn.disabled = false; return; }

  // 斜杠命令拦截
  if (queryText.startsWith('/')) {
    var cmdResult = await handleSlashInput(queryText);
    if (cmdResult.handled) {
      isStreaming = false;
      btn.disabled = false;
      input.focus();
      return;
    }
    // 不认识的命令继续走 AI
  }

  addMessage('user', escHtml(queryText));
  input.value = '';
  input.style.height = 'auto';

  try {
    const res = await huanzhenFetch('/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        query: queryText,
        session_id: sessionId,
        conversation_id: currentConvId,
        files: files
      })
    });

    sessionId = res.headers.get('X-Session-Id') || sessionId;
    const newConvId = res.headers.get('X-Conversation-Id') || '';
    if (newConvId) { currentConvId = newConvId; conversationId = newConvId; }
    _persistConvState();  // 修复：新会话 id 立即落盘，F5 后还能接着这段对话

    currentReader = res.body.getReader();
    const reader = currentReader;
    const dec = new TextDecoder();
    let buf = '';
    let aiDiv = null;
    let phaseDiv = null;
    let fullReply = '';
    let thinkingPanel = null;
    let thinkingBody = null;
    let thinkingHtml = '';
    let thinkingTimer = null;
    let doneHandled = false;   // done 事件是否已到达（决定收尾时能否立刻删掉状态栏）
    let statusBar = document.createElement('div');
    statusBar.className = 'phase-badge phase-thinking status-bar';
    statusBar.textContent = '⏳ 处理中';
    msgs.appendChild(statusBar);

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop() || '';

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        try {
          const evt = JSON.parse(line.slice(6));
          debugEvents.push(Object.assign({_time: Date.now()}, evt));
          // 修复（死分支）：后端 /chat/stream 实际只会下发 thinking / think_token / answer / error / done，
          //   以及 system_notice（上下文自动压缩提示）、selfcheck_start / selfcheck_result（合规自检）；
          //   原来的 knowledge / self_check / tool_call / tool_result / answering 永远不会到达，已全部删除。
          switch (evt.phase) {
            case 'think_token':
              if (isShowThinking()) {
                if (!thinkingPanel) {
                  const tp = document.createElement('div');
                  tp.className = 'thinking-panel';
                  tp.innerHTML = '<div class="tp-header" onclick="this.nextElementSibling.classList.toggle(\'collapsed\');var t=this.querySelector(\'.tp-toggle\');t.textContent=t.textContent==\'▼\'?\'▶\':\'▼\'">' +
                    '<span>' + _dualIcon('🧠', 'fa-brain') + ' 思考过程 <span class="tp-timer">00:00</span></span><span class="tp-toggle">▼</span></div><div class="tp-body"></div>';
                  msgs.appendChild(tp);
                  thinkingPanel = tp;
                  thinkingBody = tp.querySelector('.tp-body');
                  var tpHdr = tp.querySelector('.tp-header');
                  var tpTimerEl = tpHdr ? tpHdr.querySelector('.tp-timer') : null;
                  var tpTimerStart = Date.now();
                  if (thinkingTimer) { clearInterval(thinkingTimer); thinkingTimer = null; }
                  thinkingTimer = setInterval(function() {
                    if (!tpTimerEl) return;
                    var sec = Math.floor((Date.now() - tpTimerStart) / 1000);
                    tpTimerEl.textContent = String(Math.floor(sec / 60)).padStart(2, '0') + ':' + String(sec % 60).padStart(2, '0');
                  }, 1000);
                  _autoScroll(msgs);
                }
                thinkingHtml += evt.token || '';
                // 修复（XSS）：思考面板累积的是模型原始输出，必须先用 _safeThink 转义再写入 innerHTML
                thinkingBody.innerHTML = _safeThink(thinkingHtml);
                _autoScroll(msgs);
              }
              if (statusBar) {
                var tok = evt.token || '';
                if (tok.indexOf('🔧') >= 0 || tok.indexOf('调用工具') >= 0) {
                  statusBar.textContent = '🔧 运行工具...';
                } else if (tok.indexOf('验证') >= 0 || tok.indexOf('检查') >= 0) {
                  statusBar.textContent = '🔍 验证中...';
                } else if (statusBar.textContent === '🔧 运行工具...' && !tok.indexOf('[') === 0) {
                  // keep tool status visible
                } else {
                  statusBar.textContent = '🤔 思考...';
                }
              }
              break;
            case 'thinking':
              if (phaseDiv) phaseDiv.remove();
              phaseDiv = addPhase('🤔 思考中... (' + (evt.round||'') + ')', 'thinking');
              document.getElementById('stopBtn').style.display = '';
              break;
            case 'selfcheck_start':
              // 修复：后端真实事件名是 selfcheck_start（原来监听的是永远不会下发的 self_check），沿用原有进度提示
              if (phaseDiv) phaseDiv.remove();
              phaseDiv = addPhase('🔄 幻帧二次确认中...', 'thinking');
              document.getElementById('stopBtn').style.display = '';
              break;
            case 'selfcheck_result':
              // 修复（新增）：之前完全没处理自检结果。passed=是否通过，summary=中文摘要；
              // 摘要是后端拼的文本，用 escHtml 转义后再交给 addPhase 渲染
              //（addPhase 只对 _dualIcon 之外的 emoji 开头文本走图标分支，所以这里用 emoji 前缀）
              if (phaseDiv) { phaseDiv.remove(); phaseDiv = null; }
              addPhase(
                (evt.passed ? '✅ 自检通过' : '❌ 自检未通过')
                  + (evt.summary ? '：' + escHtml(evt.summary) : ''),
                evt.passed ? 'result' : 'error');
              break;
            case 'system_notice':
              // 修复（新增）：后端自动压缩上下文等系统提示，原来完全没有处理，用户看不到任何说明
              if (statusBar) statusBar.textContent = '📦 ' + (evt.message || '系统提示');
              addPhase('📦 ' + escHtml(evt.message || '系统提示'), 'result');
              break;
            case 'answer':
              // 修复：'answering' 是后端永远不会下发的死事件，它的职责（建气泡 / 收起停止按钮 / 状态栏）
              //      合并到首个 answer token，行为与原 answering 分支一致
              if (!aiDiv) {
                if (phaseDiv) { phaseDiv.remove(); phaseDiv = null; }
                if (statusBar) { statusBar.textContent = '💬 回答中'; }
                aiDiv = addMessage('assistant', '');
                document.getElementById('stopBtn').style.display = 'none';
              }
              fullReply += evt.token || '';
              aiDiv.innerHTML = renderMarkdown(escHtml(fullReply));
              _autoScroll(msgs);
              break;
            case 'error':
              toast('错误: ' + escHtml(evt.message||''), 'error');
              break;
            case 'done':
              // 修复：.phase-badge 也包含状态栏自身，原来先 forEach 全删，
              //      后面的"✅ 完成"写进的是一个已脱离文档的节点，用户永远看不到完成状态。
              //      现在改为跳过状态栏，并标记 doneHandled 让循环后的收尾清理不要立刻删掉它。
              doneHandled = true;
              document.querySelectorAll('.phase-badge').forEach(function(e) {
                if (e !== statusBar) e.remove();
              });
              document.getElementById('stopBtn').style.display = 'none';
              if (thinkingTimer) { clearInterval(thinkingTimer); thinkingTimer = null; }
              if (statusBar) {
                statusBar.className = 'phase-badge phase-result status-bar';
                statusBar.style.animation = 'none';
                statusBar.textContent = '✅ 完成';
                setTimeout(function(){
                  if (statusBar) { statusBar.remove(); statusBar = null; }
                }, 2000);
              }
              break;
          }
        } catch(e) {}
      }
    }
    // 修复：done 分支已把状态栏改写为"✅ 完成"并安排 2 秒后自动移除，
    //      这里不能再无条件删除，否则完成提示根本来不及显示；只有没收到 done 时才立刻清掉
    document.querySelectorAll('.phase-badge').forEach(function(e) {
      if (e !== statusBar) e.remove();
    });
    if (thinkingTimer) { clearInterval(thinkingTimer); thinkingTimer = null; }
    if (statusBar && !doneHandled) { statusBar.remove(); statusBar = null; }
  } catch (e) {
    toast('网络错误: ' + e.message, 'error');
  }
  isStreaming = false;
  btn.disabled = false;
  document.getElementById('stopBtn').style.display = 'none';
  input.focus();
}

function addMessage(role, html) {
  const msgs = document.getElementById('chatMessages');
  const div = document.createElement('div');
  div.className = 'message ' + role;
  var avatarHtml = role === 'user' ? _dualIcon('👤', 'fa-user') : _dualIcon('🤖', 'fa-robot');
  div.innerHTML =
    '<div class="avatar">' + avatarHtml + '</div>' +
    '<div class="bubble">' + html + '</div>' +
    '<button class="copy-btn" onclick="copyMessage(this)" title="复制内容">' + _dualIcon('📋', 'fa-copy') + '</button>';
  msgs.appendChild(div);
  _autoScroll(msgs);
  return div.querySelector('.bubble');
}

function copyMessage(btn) {
  var bubble = btn.parentNode.querySelector('.bubble');
  if (!bubble) return;
  var text = bubble.textContent || '';
  var doneIcon = _dualIcon('✅', 'fa-circle-check');
  var copyIcon = _dualIcon('📋', 'fa-copy');
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(function() {
      btn.innerHTML = doneIcon;
      setTimeout(function() { btn.innerHTML = copyIcon; }, 1500);
    });
  } else {
    // fallback
    var ta = document.createElement('textarea');
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    document.body.removeChild(ta);
    btn.innerHTML = doneIcon;
    setTimeout(function() { btn.innerHTML = copyIcon; }, 1500);
  }
}

function addPhase(label, cls) {
  const msgs = document.getElementById('chatMessages');
  const div = document.createElement('div');
  div.className = 'phase-badge phase-' + cls;
  // 如果已通过 _dualIcon() 传入了 HTML，直接使用 innerHTML
  if (label.indexOf('<i class=') === 0) {
    div.innerHTML = label;
  } else {
    var chars = Array.from(label);
    var first = chars[0];
    if (first && _emojiFA[first]) {
      div.innerHTML = _dualIcon(first, _emojiFA[first]) + ' ' + chars.slice(1).join('').trim();
    } else {
      div.textContent = label;
    }
  }
  msgs.appendChild(div);
  _autoScroll(msgs);
  return div;
}

// ================================================================
