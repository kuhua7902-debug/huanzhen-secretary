// 修复：F5 刷新后恢复上次的会话（sessionId / currentConvId / conversationId），
//       这样用户刷新页面后继续的是同一段对话，而不是被当成新会话。
_restoreConvState();

initAuth().then(function() {
  if (!window.kejiAuthReady) return;
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

