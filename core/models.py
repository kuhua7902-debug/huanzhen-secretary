import json
import time
from abc import ABC, abstractmethod
from typing import Generator, Optional
import requests
from core.logger import setup_logger

logger = setup_logger("huanzhen.models")


class BaseModelAdapter(ABC):
    """模型适配器基类，定义统一接口"""

    def __init__(self, config: dict):
        self.config = config
        self.timeout = config.get("timeout", 60)

    @abstractmethod
    def chat(self, messages: list[dict], **kwargs) -> str:
        """非流式对话，返回完整回复"""
        ...

    @abstractmethod
    def chat_stream(self, messages: list[dict], **kwargs) -> Generator[str, None, None]:
        """流式对话，逐 token yield"""
        ...

    def _retry(self, func, max_retries: int = 2):
        """带重试的请求"""
        last_error = None
        for attempt in range(max_retries + 1):
            try:
                return func()
            except Exception as e:
                last_error = e
                if attempt < max_retries:
                    time.sleep(1.5 ** attempt)
        raise last_error


class OllamaAdapter(BaseModelAdapter):
    """Ollama 本地模型适配器"""

    def __init__(self, config: dict):
        super().__init__(config)
        self.base_url = config.get("base_url", "http://localhost:11434")
        self.model = config.get("model", "qwen2.5:7b")
        self.api_url = f"{self.base_url}/api/chat"

    def chat(self, messages: list[dict], **kwargs) -> str:
        def _call():
            resp = requests.post(
                self.api_url,
                json={
                    "model": self.model,
                    "messages": messages,
                    "stream": False,
                    "options": {"temperature": kwargs.get("temperature", 0.7)},
                },
                timeout=self.timeout,
            )
            resp.raise_for_status()
            return resp.json()["message"]["content"]

        logger.debug("Ollama chat: model=%s msg_count=%d", self.model, len(messages))
        result = self._retry(_call)
        logger.debug("Ollama response: %d chars", len(result))
        return result

    def chat_stream(self, messages: list[dict], **kwargs) -> Generator[str, None, None]:
        logger.debug("Ollama stream: model=%s msg_count=%d", self.model, len(messages))
        for attempt in range(3):
            try:
                resp = requests.post(
                    self.api_url,
                    json={
                        "model": self.model,
                        "messages": messages,
                        "stream": True,
                        "options": {"temperature": kwargs.get("temperature", 0.7)},
                    },
                    timeout=self.timeout,
                    stream=True,
                )
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line:
                        continue
                    chunk = json.loads(line)
                    if "message" in chunk and "content" in chunk["message"]:
                        yield chunk["message"]["content"]
                    if chunk.get("done"):
                        return
                return
            except Exception as e:
                if attempt < 2:
                    time.sleep(1.5 ** attempt)
                else:
                    raise e


class OpenAIAdapter(BaseModelAdapter):
    """OpenAI 兼容 API 适配器（支持 GPT / 国产大模型 / DeepSeek 等）"""

    def __init__(self, config: dict):
        super().__init__(config)
        self.base_url = config.get("base_url", "https://api.openai.com/v1")
        self.api_key = config.get("api_key", "")
        self.model = config.get("model", "gpt-4o-mini")
        self.api_url = f"{self.base_url}/chat/completions"

    def _build_headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

    def chat(self, messages: list[dict], **kwargs) -> str:
        def _call():
            resp = requests.post(
                self.api_url,
                headers=self._build_headers(),
                json={
                    "model": self.model,
                    "messages": messages,
                    "temperature": kwargs.get("temperature", 0.7),
                    "stream": False,
                },
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            # 健壮性：视觉模型可能返回 content=None（仅 reasoning），做兜底
            msg = data.get("choices", [{}])[0].get("message", {})
            content = msg.get("content")
            if content is None:
                # 兜底：尝试 reasoning_content（部分模型）或返回空
                content = msg.get("reasoning_content") or ""
            return content

        logger.debug("OpenAI chat: model=%s msg_count=%d", self.model, len(messages))
        result = self._retry(_call)
        logger.debug("OpenAI response: %d chars", len(result))
        return result

    def chat_stream(self, messages: list[dict], **kwargs) -> Generator[str, None, None]:
        logger.debug("OpenAI stream: model=%s msg_count=%d", self.model, len(messages))
        for attempt in range(3):
            try:
                resp = requests.post(
                    self.api_url,
                    headers=self._build_headers(),
                    json={
                        "model": self.model,
                        "messages": messages,
                        "temperature": kwargs.get("temperature", 0.7),
                        "stream": True,
                    },
                    timeout=self.timeout,
                    stream=True,
                )
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line or line == b"data: [DONE]":
                        continue
                    if line.startswith(b"data: "):
                        chunk = json.loads(line[6:])
                        delta = chunk["choices"][0].get("delta", {})
                        if "content" in delta and delta["content"] is not None:
                            yield delta["content"]
                return
            except Exception as e:
                if attempt < 2:
                    time.sleep(1.5 ** attempt)
                else:
                    raise e


class ModelRouter:
    """模型路由器：根据配置自动选择适配器

    约定：
    - 配置中每个模型项若含 adapter 字段则按其选择适配器类；
    - 未指定 adapter 时，ollama 用 OllamaAdapter，其余一律用 OpenAIAdapter
      （DeepSeek / Qwen-VL / OpenAI / 通义千问等均走 OpenAI 兼容协议）。
    - 含 vision: true 的模型视为视觉模型，可通过 get_vision() 获取。
    """

    def __init__(self, config: dict):
        models_config = config.get("models", {})
        self.default = models_config.get("default", "ollama")
        self.adapters: dict[str, BaseModelAdapter] = {}
        self._models_config = models_config  # 保留原始配置，供 get_vision 判断 vision 标记

        adapter_map = {
            "ollama": OllamaAdapter,
            "openai": OpenAIAdapter,
            "deepseek": OpenAIAdapter,
            "qwen_vl": OpenAIAdapter,
        }

        for name, cfg in models_config.items():
            if name == "default":
                continue
            if not isinstance(cfg, dict):
                continue
            adapter_name = cfg.get("adapter")
            adapter_cls = adapter_map.get(adapter_name) if adapter_name else adapter_map.get(name, OpenAIAdapter)
            self.adapters[name] = adapter_cls(cfg)

    def get(self, name: Optional[str] = None) -> BaseModelAdapter:
        """获取指定模型适配器，不指定则用默认"""
        target = name or self.default
        if target not in self.adapters:
            raise ValueError(f"未知模型: {target}，可用: {list(self.adapters)}")
        return self.adapters[target]

    def get_vision(self) -> Optional[BaseModelAdapter]:
        """获取视觉模型适配器（供截图分析等场景按需调用）。
        优先返回配置中标记了 vision: true 的模型；找不到则返回 None。
        """
        for name, adapter in self.adapters.items():
            if self._models_config.get(name, {}).get("vision", False):
                return adapter
        return None

    def has_vision(self) -> bool:
        """是否配置了视觉模型"""
        return self.get_vision() is not None

    @classmethod
    def register_adapter(cls, name: str, adapter_cls):
        """注册自定义适配器（向后兼容旧调用）"""
        if not hasattr(cls, "_extra_adapters"):
            cls._extra_adapters = {}
        cls._extra_adapters[name] = adapter_cls
