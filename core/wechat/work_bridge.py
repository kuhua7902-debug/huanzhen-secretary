"""企业微信桥接 —— 连接幻帧到企业微信自建应用

架构：
  企业微信服务器 → HTTP回调/Callback → 幻帧FastAPI → Agent 处理 → 回复发送
                                                                       ↓
                                                          企业微信HTTP API

前置条件：
  1. 企业微信后台创建自建应用，获取 CorpID / AgentID / Secret
  2. 配置回调URL指向幻帧服务器（需要公网可达，或使用frp/ngrok）
  3. 幻帧服务器上运行本桥接

设计说明（2026-05 修复）：
  原实现在同步函数里 `asyncio.new_event_loop()` + `run_until_complete` + `close()`，
  而它又是被 FastAPI 的异步路由直接同步调用的——既会阻塞服务器事件循环，
  又会因为调用参数写成 `session_id=`（真实签名是 `sid=`）而必然 TypeError。
  现改为全异步：回调路由 await 处理函数，直接复用服务器事件循环与 Adapter 单例。
"""

import logging
import threading
import uuid
import xml.etree.ElementTree as ET
from typing import Optional

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

from nanobot.adapter import KejiAdapter
from core.wechat.work import WorkClient, WorkConfig, WorkMessage

logger = logging.getLogger("keji.wechat.work_bridge")

# 全局实例
_bridge: Optional["WorkBridge"] = None


class WorkBridge:
    """企业微信桥接器"""

    def __init__(self):
        self.client = WorkClient()
        self.adapter: Optional[KejiAdapter] = None
        self._conv_map: dict[str, str] = {}
        self._conv_lock = threading.Lock()

    def configure(self, corp_id: str, agent_id: str, corp_secret: str):
        """配置企业微信参数"""
        self.client.config = WorkConfig(
            corp_id=corp_id,
            agent_id=agent_id,
            corp_secret=corp_secret,
        )
        logger.info("Work WeChat configured")

    def is_ready(self) -> bool:
        return self.client.is_configured()

    def _get_conv_id(self, user_id: str) -> str:
        with self._conv_lock:
            if user_id not in self._conv_map:
                self._conv_map[user_id] = f"ww_{uuid.uuid4().hex[:12]}"
            return self._conv_map[user_id]

    async def _ensure_adapter(self) -> KejiAdapter:
        """复用全局 Adapter 单例（在服务器事件循环里初始化，MCP 才能正常连接）。"""
        if self.adapter is None:
            from nanobot.adapter import get_adapter

            self.adapter = await get_adapter()
        return self.adapter

    async def handle_message(self, msg: WorkMessage) -> Optional[str]:
        """处理一条企业微信消息，返回回复内容。"""
        from core.security.context import set_request_context

        logger.info("企微 << %s: %.60s", msg.from_user, msg.content)

        conv_id = self._get_conv_id(msg.from_user)

        try:
            # 标注来源，便于审计里区分企业微信渠道（无登录用户，按服务身份处理）
            set_request_context(actor="wecom", user_id="service", role="")
            adapter = await self._ensure_adapter()
            # 注意：KejiAdapter.chat 的第二个参数名是 sid（不是 session_id）
            return await adapter.chat(msg.content, sid=conv_id)
        except Exception as e:
            logger.error("Agent error: %s", e, exc_info=True)
            return f"抱歉，处理出错：{str(e)[:100]}"

    def send_reply(self, to_user: str, text: str) -> bool:
        """发送回复到企业微信"""
        return self.client.send_text(to_user, text)

    def test(self) -> tuple[bool, str]:
        """测试连接"""
        return self.client.test_connection()


# ──── FastAPI 回调路由 ────

router = APIRouter(prefix="/api/work", tags=["企业微信"])


class WorkConfigForm(BaseModel):
    corp_id: str
    agent_id: str
    corp_secret: str


def _get_bridge() -> WorkBridge:
    global _bridge
    if not _bridge:
        _bridge = WorkBridge()
    return _bridge


@router.post("/configure")
def configure_work(req: WorkConfigForm):
    """配置企业微信参数"""
    _get_bridge().configure(req.corp_id, req.agent_id, req.corp_secret)
    return {"status": "ok", "message": "配置已保存"}


@router.get("/status")
def work_status():
    """获取企业微信连接状态"""
    bridge = _get_bridge()
    if not bridge.is_ready():
        return {"configured": False}
    ok, msg = bridge.test()
    return {"configured": True, "connected": ok, "message": msg}


@router.post("/callback")
async def work_callback(request: Request):
    """接收企业微信回调消息（XML格式）"""
    bridge = _get_bridge()
    if not bridge.is_ready():
        return Response(content="not configured", status_code=200)

    body = await request.body()
    xml_text = body.decode("utf-8")

    try:
        root = ET.fromstring(xml_text)
        msg_type = root.findtext("MsgType", "")
        content = root.findtext("Content", "")
        from_user = root.findtext("FromUserName", "")
        msg_id = root.findtext("MsgId", "")
    except Exception as e:
        logger.warning("Parse callback XML error: %s", e)
        return Response(content="ok")

    if msg_type != "text" or not content:
        return Response(content="ok")

    msg = WorkMessage(
        from_user=from_user,
        content=content,
        msg_type=msg_type,
        msg_id=msg_id,
    )

    # 直接 await，不再阻塞事件循环
    reply = await bridge.handle_message(msg)
    if reply:
        bridge.send_reply(from_user, reply)

    return Response(content="ok")


@router.get("/callback")
async def work_callback_verify(
    msg_signature: str = "",
    timestamp: str = "",
    nonce: str = "",
    echostr: str = "",
):
    """企业微信回调URL验证"""
    return Response(content=echostr)
