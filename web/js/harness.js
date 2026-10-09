/* ============================================================================
 * 幻帧 Harness 交互增强（Phase A）
 * ----------------------------------------------------------------------------
 * 新增能力，全部通过既有全局函数驱动，不改动任何业务逻辑：
 *   1. ⌘K / Ctrl+K 命令面板
 *   2. 空态示例 Prompt 卡片（一键发起对话）
 *   3. 右下角 ⌘K 提示胶囊
 * 本文件最后加载，所有依赖的全局函数此时均已就绪。
 * ========================================================================== */
(function () {
  'use strict';

  function has(fn) { return typeof window[fn] === 'function'; }

  /* ---------------------------------------------------------------- 命令表 */
  function buildCommands() {
    var cmds = [
      { id: 'new',     icon: '✦', title: '新建对话',      key: 'N', run: function () { has('newChat') && newChat(); } },
      { id: 'history', icon: '◷', title: '对话历史',      key: 'H', run: function () { has('openHistory') && openHistory(); } },
      { id: 'skills',  icon: '⌘', title: '技能面板',      key: 'S', run: function () { has('toggleSkillPanel') && toggleSkillPanel(); } },
      { id: 'compact', icon: '▤', title: '压缩对话上下文', key: '', run: function () { has('compactChatHistory') && compactChatHistory(); } },
      { id: 'stop',    icon: '■', title: '停止当前生成',   key: '', run: function () { has('stopStreaming') && stopStreaming(); } },
      { id: 'debug',   icon: '⚙', title: '调试抽屉',      key: '', run: function () { has('toggleDebug') && toggleDebug(); } },
      { id: 'p-chat',      icon: '◆', title: '前往 · 对话',   key: '', run: function () { has('switchPage') && switchPage('chat'); } },
      { id: 'p-knowledge', icon: '◇', title: '前往 · 知识库', key: '', run: function () { has('switchPage') && switchPage('knowledge'); } },
      { id: 'p-files',     icon: '◇', title: '前往 · 团队文件', key: '', run: function () { has('switchPage') && switchPage('files'); } },
      { id: 'p-database',  icon: '◇', title: '前往 · 数据库', key: '', run: function () { has('switchPage') && switchPage('database'); } },
      { id: 'p-stats',     icon: '◇', title: '前往 · 统计',   key: '', run: function () { has('switchPage') && switchPage('stats'); } },
      { id: 'p-tools',     icon: '◇', title: '前往 · 工具',   key: '', run: function () { has('switchPage') && switchPage('tools'); } },
      { id: 'p-settings',  icon: '◇', title: '前往 · 设置',   key: '', run: function () { has('switchPage') && switchPage('settings'); } },
      { id: 'theme',   icon: '◐', title: '切换图标主题（Emoji / FontAwesome）', key: '', run: toggleIconTheme },
      { id: 'logout',  icon: '⏻', title: '退出登录',      key: '', run: function () { has('logoutHuanzhen') && logoutHuanzhen(); } }
    ];
    // 管理页仅对管理员可见时才有意义，存在即加入
    var navAdmin = document.getElementById('navAdmin');
    if (navAdmin && navAdmin.style.display !== 'none') {
      cmds.splice(12, 0, { id: 'p-admin', icon: '◇', title: '前往 · 管理', key: '', run: function () { has('switchPage') && switchPage('admin'); } });
    }
    return cmds;
  }

  function toggleIconTheme() {
    var cur = localStorage.getItem('huanzhen_icon_theme') || 'emoji';
    var next = cur === 'fa' ? 'emoji' : 'fa';
    if (has('applyIconTheme')) applyIconTheme(next);
    var sel = document.getElementById('setIconTheme');
    if (sel) sel.value = next;
  }

  /* ------------------------------------------------------------ 命令面板 */
  var overlay, input, listEl, items = [], filtered = [], activeIdx = 0;

  function ensurePalette() {
    if (overlay) return;
    overlay = document.createElement('div');
    overlay.className = 'hz-palette';
    overlay.innerHTML =
      '<div class="hz-palette-box">' +
        '<input class="hz-palette-input" placeholder="输入命令…（↑↓ 选择，Enter 执行，Esc 关闭）" autocomplete="off">' +
        '<div class="hz-palette-list"></div>' +
      '</div>';
    document.body.appendChild(overlay);
    input = overlay.querySelector('.hz-palette-input');
    listEl = overlay.querySelector('.hz-palette-list');

    overlay.addEventListener('mousedown', function (e) { if (e.target === overlay) closePalette(); });
    input.addEventListener('input', function () { renderList(input.value); });
    input.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowDown') { e.preventDefault(); activeIdx = Math.min(activeIdx + 1, filtered.length - 1); paintActive(); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); activeIdx = Math.max(activeIdx - 1, 0); paintActive(); }
      else if (e.key === 'Enter') { e.preventDefault(); runActive(); }
      else if (e.key === 'Escape') { e.preventDefault(); closePalette(); }
    });
  }

  function renderList(q) {
    q = (q || '').trim().toLowerCase();
    filtered = items.filter(function (c) {
      return !q || c.title.toLowerCase().indexOf(q) >= 0 || c.id.indexOf(q) >= 0;
    });
    activeIdx = 0;
    if (!filtered.length) {
      listEl.innerHTML = '<div class="hz-palette-empty">没有匹配的命令</div>';
      return;
    }
    listEl.innerHTML = filtered.map(function (c, i) {
      return '<div class="hz-palette-item" data-i="' + i + '">' +
        '<span class="hz-palette-ico">' + c.icon + '</span>' +
        '<span>' + c.title + '</span>' +
        (c.key ? '<span class="hz-kbd hz-palette-key">' + c.key + '</span>' : '') +
        '</div>';
    }).join('');
    Array.prototype.forEach.call(listEl.querySelectorAll('.hz-palette-item'), function (el) {
      el.addEventListener('mouseenter', function () { activeIdx = parseInt(el.getAttribute('data-i'), 10); paintActive(); });
      el.addEventListener('click', function () { activeIdx = parseInt(el.getAttribute('data-i'), 10); runActive(); });
    });
    paintActive();
  }

  function paintActive() {
    Array.prototype.forEach.call(listEl.querySelectorAll('.hz-palette-item'), function (el, i) {
      el.classList.toggle('active', i === activeIdx);
    });
  }

  function runActive() {
    var c = filtered[activeIdx];
    if (!c) return;
    closePalette();
    setTimeout(function () { try { c.run(); } catch (e) { console.warn('[harness] command failed', e); } }, 0);
  }

  function openPalette() {
    ensurePalette();
    items = buildCommands();
    overlay.classList.add('open');
    input.value = '';
    renderList('');
    setTimeout(function () { input.focus(); }, 10);
  }

  function closePalette() { if (overlay) overlay.classList.remove('open'); }

  /* -------------------------------------------------- 空态示例 Prompt */
  var SUGGESTIONS = [
    { t: '分析数据并出图', d: '上传 Excel，找出异常订单并生成图表报告', q: '分析这份 Excel 的销售数据，找出异常订单，并生成一份带图表的分析报告。' },
    { t: '文档总结',       d: '把 PDF/Word 要点整理成结构化报告',      q: '请阅读我上传的文档，提取关键结论并整理成一份结构化报告。' },
    { t: '文件自动整理',   d: '按月份/类型归类共享目录文件',            q: '帮我把共享文件夹里的文件按月份自动归类整理。' },
    { t: '知识库问答',     d: '基于已索引资料回答问题并给出处',          q: '根据知识库里的资料，总结一下相关制度的关键要点。' }
  ];

  function injectSuggestions() {
    var msgs = document.getElementById('chatMessages');
    if (!msgs) return;
    var empty = msgs.querySelector('.empty-state');
    if (!empty || empty.querySelector('.hz-suggest')) return;

    var grid = document.createElement('div');
    grid.className = 'hz-suggest';
    grid.innerHTML = SUGGESTIONS.map(function (s, i) {
      return '<button type="button" class="hz-suggest-card" data-i="' + i + '">' +
        '<div class="t">' + s.t + '</div><div class="d">' + s.d + '</div></button>';
    }).join('');
    Array.prototype.forEach.call(grid.querySelectorAll('.hz-suggest-card'), function (btn) {
      btn.addEventListener('click', function () {
        var s = SUGGESTIONS[parseInt(btn.getAttribute('data-i'), 10)];
        var inp = document.getElementById('chatInput');
        if (!inp) return;
        inp.value = s.q;
        inp.dispatchEvent(new Event('input', { bubbles: true }));
        if (has('sendChat')) sendChat();
      });
    });
    empty.appendChild(grid);
  }

  function watchEmptyState() {
    var msgs = document.getElementById('chatMessages');
    if (!msgs) return;
    injectSuggestions();
    new MutationObserver(function () { injectSuggestions(); })
      .observe(msgs, { childList: true });
  }

  /* ------------------------------------------------------ 右下角提示胶囊 */
  function addHint() {
    if (document.querySelector('.hz-palette-hint')) return;
    var el = document.createElement('div');
    el.className = 'hz-palette-hint';
    el.innerHTML = '<span class="hz-kbd">⌘K</span><span>命令面板</span>';
    el.addEventListener('click', openPalette);
    document.body.appendChild(el);
  }

  /* ------------------------------------------------------------ 键盘绑定 */
  function bindKeys() {
    document.addEventListener('keydown', function (e) {
      var meta = e.metaKey || e.ctrlKey;
      if (meta && (e.key === 'k' || e.key === 'K')) {
        e.preventDefault();
        if (overlay && overlay.classList.contains('open')) closePalette(); else openPalette();
      }
    });
  }

  /* ---------------------------------------------------------------- 启动 */
  function boot() {
    ensurePalette();
    addHint();
    bindKeys();
    watchEmptyState();
    // 登录后才显示命令面板入口
    setTimeout(function () {
      var overlayLogin = document.getElementById('loginOverlay');
      if (overlayLogin && overlayLogin.classList.contains('open')) {
        var hint = document.querySelector('.hz-palette-hint');
        if (hint) hint.style.display = 'none';
      }
    }, 400);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();

  window.openHuanzhenPalette = openPalette; // 供其他脚本/调试调用
})();
