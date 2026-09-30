"""微信 ↔ 幻帧 Agent 桥接路由

从微信接收消息 → 调用幻帧 Agent 处理 → 回复发回微信

设计说明（2026-05 修复）：
  原来的实现每次调用都 `asyncio.new_event_loop()` 再 `close()`，有两个后果：
  1. `KejiAdapter.chat()` 的调用参数写成了 `session_id=`，而真实签名是
     `chat(query, sid="", files=None)`，导致每次调用必然 TypeError，
     被 except 吞掉后固定回「抱歉，处理出错」——桥接实际完全不可用；
  2. Adapter 在「临时循环」里初始化、`create_task` 连接 MCP，循环随即关闭，
     所以微信侧从来没有连上任何 MCP 工具。
  修复方式：用一个常驻后台事件循环承载所有异步调用，Adapter 只初始化一次。
"""

import asyncio
import logging
import threading
from typing import Optional

from nanobot.adapter import KejiAdapter
from core.wechat.ilink import ILinkClient, WeChatMessage

logger = logging.getLogger("keji.wechat.bridge")

# ── 桥接专用常驻事件循环 ──
# KejiAdapter 会在初始化时异步连接 MCP 服务，需要一个长期存活的循环。
# 这里用后台守护线程跑 run_forever，所有调用通过 run_coroutine_threadsafe 提交。
_LOOP: Optional[asyncio.AbstractEventLoop] = None
_LOOP_LOCK = threading.Lock()


def _get_loop() -> asyncio.AbstractEventLoop:
    """获取（或惰性创建）常驻事件循环。"""
    global _LOOP
    if _LOOP is not None and not _LOOP.is_closed():
        return _LOOP
    with _LOOP_LOCK:
        if _LOOP is None or _LOOP.is_closed():
            loop = asyncio.new_event_loop()
            threading.Thread(
                target=loop.run_forever, name="wechat-bridge-loop", daemon=True
            ).start()
            _LOOP = loop
            logger.info("微信桥接事件循环已启动")
    return _LOOP


def _submit(coro, timeout: float = 600):
    """把协程提交到常驻循环并同步等待结果。"""
    future = asyncio.run_coroutine_threadsafe(coro, _get_loop())
    return future.result(timeout=timeout)


class WeChatBridge:
    """微信桥接器：将微信消息路由到幻帧 Agent"""

    def __init__(self, session_path: str = "data/wechat_session.json"):
        self.client = ILinkClient(session_path=session_path)
        self.adapter: Optional[KejiAdapter] = None
        self._conv_map: dict[str, str] = {}  # wechat_user_id → huanzhen_conv_id

        # 注册回调
        self.client.set_on_message(self._handle_message)
        self.client.set_on_error(self._handle_error)

    # ──── 生命周期 ────

    def start(self):
        """启动微信桥接（登录 + 开始轮询消息）"""
        logger.info("Starting WeChat Bridge...")

        # 在常驻循环里初始化 Adapter（会顺带异步连接 MCP 服务）
        try:
            self.adapter = _submit(self._init_adapter())
            logger.info("KejiAdapter initialized")
        except Exception as e:
            logger.error("KejiAdapter 初始化失败: %s", e)
            return False

        # 登录
        if not self.client.login():
            logger.error("WeChat login failed")
            return False

        # 开始轮询消息
        self.client.start_polling()
        logger.info("WeChat Bridge started")
        return True

    @staticmethod
    async def _init_adapter() -> KejiAdapter:
        from nanobot.adapter import get_adapter

        return await get_adapter()

    def stop(self):
        """停止桥接"""
        self.client.stop_polling()
        logger.info("WeChat Bridge stopped")

    # ──── 消息处理 ────

    def _get_conv_id(self, wechat_user: str) -> str:
        """获取或创建幻帧对话 ID"""
        if wechat_user not in self._conv_map:
            import uuid
            conv_id = f"wx_{uuid.uuid4().hex[:12]}"
            self._conv_map[wechat_user] = conv_id
            logger.info("New conversation for %s: %s", wechat_user, conv_id)
        return self._conv_map[wechat_user]

    async def _chat_async(self, text: str, conv_id: str) -> str:
        """在常驻循环里调用 Agent。"""
        from nanobot.adapter import get_adapter
        from core.security.context import set_request_context

        # 标注来源，便于审计里区分微信渠道的消息（无登录用户，按服务身份处理）
        set_request_context(actor="wechat", user_id="service", role="")
        adapter = self.adapter or await get_adapter()
        # 注意：KejiAdapter.chat 的第二个参数名是 sid（不是 session_id）
        return await adapter.chat(text, sid=conv_id)

    def _handle_message(self, msg: WeChatMessage):
        """处理收到的微信消息"""
        if not self.adapter:
            logger.warning("Adapter 未就绪，丢弃消息: %s", msg.from_user)
            return

        # 只处理文本消息
        text = msg.get_text()
        if not text:
            logger.debug("Skipping non-text message from %s", msg.from_user)
            return

        logger.info("WeChat << %s: %s", msg.from_user, text[:80])

        # 发送"正在输入"状态
        self.client.send_typing(msg.from_user)

        # 设置会话 ID，让幻帧保持对话上下文
        conv_id = self._get_conv_id(msg.from_user)

        # 调用幻帧 Agent 处理（非流式，微信不支持流式推送）
        try:
            reply = _submit(self._chat_async(text, conv_id))
        except Exception as e:
            logger.error("Agent chat error: %s", e, exc_info=True)
            reply = f"抱歉，处理出错：{str(e)[:100]}"

        if not reply:
            reply = "抱歉，我没有理解你的意思。"

        # 发送回复到微信
        success = self.client.send_message(
            to_user=msg.from_user,
            text=reply,
            context_token=msg.context_token,
        )

        if success:
            logger.info("WeChat >> %s: %s", msg.from_user, reply[:80])
        else:
            logger.error("Failed to send reply to %s", msg.from_user)

    def _handle_error(self, error: str):
        """处理错误事件"""
        if error == "session_expired":
            logger.warning("Session expired, attempting re-login...")
            # 尝试自动重登
            if self.client.login():
                logger.info("Re-login success")
            else:
                logger.error("Re-login failed, please restart")
