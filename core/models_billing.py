"""模型账户余额查询与充值入口。

设计边界（重要）：

1. **只有少数厂商提供公开的余额查询接口** —— 目前确认可用的是 DeepSeek。
   其余厂商要么需要额外的管理密钥（如 OpenAI 的 Admin Key），要么根本没有
   公开接口（如阿里云百炼，余额在费用中心，需要 AccessKey + 签名）。
   这类情况如实返回 status='unsupported' / 'local'，**绝不编造数字**。
2. **API Key 只在服务端使用**，只把余额结果返回给前端。
3. 结果带 TTL 缓存，避免每次打开设置页都外呼厂商（限流 / 风控）。
"""

from __future__ import annotations

import json
import time
import urllib.request
from typing import Any, Optional

from loguru import logger

_CACHE: dict[str, tuple[float, dict]] = {}
_TTL_S = 300  # 5 分钟

# 各厂商「充值 / 用量」控制台地址。
# 可用 config.yaml 的 models.<key>.billing_url 覆盖（厂商改地址时无需改代码）。
DEFAULT_BILLING_URLS: dict[str, str] = {
    "deepseek": "https://platform.deepseek.com/top_up",
    "zhipu": "https://open.bigmodel.cn/",
    "qwen": "https://bailian.console.aliyun.com/",
    "qwen_vl": "https://bailian.console.aliyun.com/",
    "openai": "https://platform.openai.com/settings/organization/billing/overview",
}

# 本地部署，不存在余额概念
LOCAL_PROVIDERS = frozenset({"ollama"})


def billing_url(provider_key: str, cfg: dict) -> str:
    """充值入口地址：优先 config 覆盖，其次内置默认。"""
    url = str((cfg or {}).get("billing_url") or "").strip()
    if url:
        return url
    return DEFAULT_BILLING_URLS.get(provider_key, "")


def _fetch_deepseek_balance(api_key: str, timeout: float = 8.0) -> dict:
    """DeepSeek 官方余额接口：GET https://api.deepseek.com/user/balance"""
    req = urllib.request.Request(
        "https://api.deepseek.com/user/balance",
        headers={"Authorization": "Bearer " + api_key, "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        data = json.loads(resp.read().decode("utf-8"))
    infos = data.get("balance_infos") or []
    if not infos:
        raise ValueError("厂商未返回余额信息")
    info = infos[0]
    return {
        "currency": str(info.get("currency") or "CNY"),
        "total": float(info.get("total_balance") or 0),
        "granted": float(info.get("granted_balance") or 0),
        "topped_up": float(info.get("topped_up_balance") or 0),
        "available": bool(data.get("is_available", True)),
    }


def _query_one(provider_key: str, cfg: dict) -> dict:
    """查询单个厂商；任何异常都降级为状态值，绝不抛出。"""
    base: dict[str, Any] = {
        "provider": provider_key,
        "model_id": str((cfg or {}).get("model") or ""),
        "base_url": str((cfg or {}).get("base_url") or ""),
        "billing_url": billing_url(provider_key, cfg),
    }

    if provider_key in LOCAL_PROVIDERS:
        return {**base, "status": "local", "message": "本地部署，无 API 费用"}

    api_key = str((cfg or {}).get("api_key") or "").strip()
    if not api_key or api_key.startswith("${"):
        return {**base, "status": "no_key", "message": "未配置 API Key"}

    if provider_key == "deepseek":
        try:
            return {**base, "status": "ok", **_fetch_deepseek_balance(api_key)}
        except Exception as e:
            logger.warning("查询 DeepSeek 余额失败: {}", e)
            return {**base, "status": "error", "message": f"查询失败：{str(e)[:90]}"}

    return {
        **base,
        "status": "unsupported",
        "message": "该厂商未提供公开的余额查询接口",
    }


def get_balances(models_cfg: Optional[dict] = None, force: bool = False) -> list:
    """返回所有已配置模型的余额状态列表。"""
    if models_cfg is None:
        try:
            from core.security.secrets import load_app_config

            models_cfg = load_app_config().get("models") or {}
        except Exception:
            models_cfg = {}

    out: list = []
    now = time.time()
    for key, cfg in models_cfg.items():
        if key == "default" or not isinstance(cfg, dict):
            continue
        cached = _CACHE.get(key)
        if cached and not force and now - cached[0] < _TTL_S:
            out.append(cached[1])
            continue
        result = _query_one(key, cfg)
        if result.get("status") == "ok" or force:
            _CACHE[key] = (now, result)
        out.append(result)
    return out


def usage_by_model() -> list:
    """本地累计用量（我们自己统计的，覆盖全部模型）。"""
    try:
        from core.database.db import get_db

        return get_db().get_usage_by_model()
    except Exception as e:
        logger.debug("读取模型用量失败: {}", e)
        return []
