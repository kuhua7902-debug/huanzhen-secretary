// 修复：F5 刷新后恢复上次的会话（sessionId / currentConvId / conversationId），
//       这样用户刷新页面后继续的是同一段对话，而不是被当成新会话。
_restoreConvState();

initAuth().then(function() {
  if (!window.huanzhenAuthReady) return;
  checkStatus();
  loadThinkingSetting();
  loadIconTheme();
  loadModelSettings();
  loadWorkConfig();
  setInterval(checkStatus, 15000);
});
// 修复（死代码）：原 loadAgentMode() / updateModeUI() / #modeBtnReact / #modeBtnPlan 的图标设置已删除
//（Agent 模式按钮在 index.html 中已不存在，功能已并入自动流程；/chat/mode 现在只是恒返回 "react" 的遗留桩接口）

// 自动调整输入框高度
document.getElementById('chatInput').addEventListener('input', function() {
  this.style.height = 'auto';
  this.style.height = Math.min(this.scrollHeight, 150) + 'px';
});
document.getElementById('splitInput').addEventListener('input', function() {
  this.style.height = 'auto';
  this.style.height = Math.min(this.scrollHeight, 100) + 'px';
});

// 修复（死代码）：原「Plan-and-Execute 模式」区块（loadAgentMode / setAgentMode / updateModeUI）
// 已整体删除：它引用的 #modeBtnReact / #modeBtnPlan / .mode-btn 在 index.html 中都不存在，
// 这些函数没有任何调用方，属于永远不可达的代码。

// ── 深链与嵌入支持 ──
// 新版 Harness 的「设置」弹窗用 iframe 嵌入控制台的某个具体页面，
// 因此支持：?page=knowledge 直接切到指定页；?embed=1 隐藏顶部导航以便嵌入。
(function () {
  try {
    var params = new URLSearchParams(location.search);
    if (params.get('embed') === '1') document.body.classList.add('hz-embed');
    var page = params.get('page');
    if (!page) return;
    var tries = 0;
    var timer = setInterval(function () {
      tries += 1;
      var ready = window.huanzhenAuthReady === true;
      if (ready || tries > 60) {
        clearInterval(timer);
        if (typeof switchPage === 'function') switchPage(page);
      }
    }, 250);
  } catch (e) { /* 忽略 */ }
})();

