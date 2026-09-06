"""OpenAI-compatible LLM 客户端封装。

默认继续使用 DeepSeek，也允许个人版通过统一的 LearnBuddy 环境变量接入
OpenAI 或其他兼容 Chat Completions 协议的模型服务。
同时提供同步和异步流式两套接口，覆盖 CLI 自测和 FastAPI SSE 两种场景。

使用方式：

    from core.llm_client import LLMClient, get_default_client
    from core.schemas import Message

    client = get_default_client()

    # 同步
    text = client.chat([Message(role="user", content="你好")])

    # 异步流式
    async for token in client.chat_stream(messages):
        print(token, end="", flush=True)

环境变量（来自 .env，由 python-dotenv 加载）：
    - LEARNBUDDY_LLM_API_KEY          (推荐，个人版由 owner 自备)
    - LEARNBUDDY_LLM_BASE_URL         (OpenAI-compatible base URL)
    - LEARNBUDDY_LLM_MODEL            (模型名)
    - LEARNBUDDY_LLM_REASONER_MODEL   (可选，重任务模型名)

旧的 DEEPSEEK_* 变量继续作为兼容回退，现有部署无需立即迁移。
"""

from __future__ import annotations

import os
from typing import AsyncIterator, Callable, Iterable, Optional

from dotenv import load_dotenv
from openai import AsyncOpenAI, OpenAI
from openai import APIConnectionError, APIError, APIStatusError, RateLimitError

from core.schemas import Message


# 加载 .env 一次（由调用方决定是否需要）
load_dotenv()


# ============================================================================
# usage 观察者：解耦 token 采集（避免 llm_client 反向依赖 usage/db）
# ============================================================================


_usage_observer: Optional[Callable[[str, dict], None]] = None


def set_usage_observer(fn: Optional[Callable[[str, dict], None]]) -> None:
    """注册一个回调，每次 LLM 调用结束后以 (model, usage_dict) 调用它。"""
    global _usage_observer
    _usage_observer = fn


def _usage_to_dict(usage) -> dict:
    if not usage:
        return {}
    return {
        "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
        "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
        "total_tokens": getattr(usage, "total_tokens", 0) or 0,
    }


def _emit_usage(model: str, usage) -> None:
    d = _usage_to_dict(usage)
    if _usage_observer is not None and d and d.get("total_tokens"):
        try:
            _usage_observer(model, d)
        except Exception:  # noqa: BLE001 — 采集失败绝不影响主流程
            pass


# ============================================================================
# 异常封装：让上层处理时不需要直接知道 OpenAI SDK 异常类型
# ============================================================================


class LLMError(Exception):
    """LLM 调用通用异常基类。"""


class LLMConfigError(LLMError):
    """配置错误（缺 API key / base_url 错误等）。"""


class LLMRateLimitError(LLMError):
    """超出速率限制。"""


class LLMNetworkError(LLMError):
    """网络异常 / 连接超时。"""


# ============================================================================
# 客户端
# ============================================================================


DEFAULT_MODEL = "deepseek-chat"
DEFAULT_REASONER_MODEL = "deepseek-reasoner"
DEFAULT_BASE_URL = "https://api.deepseek.com"


def _first_env(*names: str) -> str:
    """返回第一个非空环境变量，便于统一配置并兼容历史名称。"""
    for name in names:
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    return ""


def _provider_label(base_url: str) -> str:
    """生成只用于错误提示的 provider 名称，不暴露凭据。"""
    explicit = (os.getenv("LEARNBUDDY_LLM_PROVIDER") or "").strip()
    if explicit:
        return explicit[:40]
    lowered = (base_url or "").lower()
    if "deepseek" in lowered:
        return "DeepSeek"
    if "openai.com" in lowered:
        return "OpenAI"
    return "模型服务"


def llm_runtime_status() -> dict:
    """返回可安全展示的模型运行状态；永远不返回 API key。"""
    key = _first_env("LEARNBUDDY_LLM_API_KEY", "DEEPSEEK_API_KEY")
    base_url = _first_env("LEARNBUDDY_LLM_BASE_URL", "DEEPSEEK_BASE_URL") or DEFAULT_BASE_URL
    model = _first_env("LEARNBUDDY_LLM_MODEL", "DEEPSEEK_MODEL") or DEFAULT_MODEL
    return {
        "configured": bool(key),
        "provider": _provider_label(base_url),
        "model": model,
    }


def get_reasoner_model() -> Optional[str]:
    """返回做"重活"（课设/锐度评审）该用的 reasoner 模型名。

    - `ITUTOR_LESSON_REASONER=0/false/off` → 返回 None，调用方退回默认 chat 模型省钱。
    - 显式配置时优先用 `LEARNBUDDY_LLM_REASONER_MODEL`；
    - 旧部署继续兼容 `DEEPSEEK_REASONER_MODEL`；
    - 使用非 DeepSeek 的统一配置且未声明 reasoner 时返回 None，避免误调不存在的模型。
    """
    if os.getenv("ITUTOR_LESSON_REASONER", "1").strip().lower() in ("0", "false", "no", "off"):
        return None
    configured = _first_env("LEARNBUDDY_LLM_REASONER_MODEL", "DEEPSEEK_REASONER_MODEL")
    if configured:
        return configured
    generic_base = _first_env("LEARNBUDDY_LLM_BASE_URL")
    generic_model = _first_env("LEARNBUDDY_LLM_MODEL")
    if generic_base and "deepseek" not in generic_base.lower():
        return None
    if generic_model and not generic_model.lower().startswith("deepseek"):
        return None
    return DEFAULT_REASONER_MODEL


class LLMClient:
    """同步 + 异步流式两套接口的 LLM 客户端。

    - chat(...)         同步阻塞，一次性拿完整回复（用于 CLI 自测、单元测试）
    - chat_stream(...)  异步生成器，逐 token 流式返回（用于 FastAPI SSE）
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = 60.0,
    ):
        api_key = api_key or _first_env("LEARNBUDDY_LLM_API_KEY", "DEEPSEEK_API_KEY")
        if not api_key:
            raise LLMConfigError(
                "尚未配置模型 API Key。个人版请在 .env 中填写 "
                "LEARNBUDDY_LLM_API_KEY；现有 DeepSeek 部署也可继续使用 DEEPSEEK_API_KEY。"
            )

        self.api_key = api_key
        self.base_url = base_url or _first_env("LEARNBUDDY_LLM_BASE_URL", "DEEPSEEK_BASE_URL") or DEFAULT_BASE_URL
        self.model = model or _first_env("LEARNBUDDY_LLM_MODEL", "DEEPSEEK_MODEL") or DEFAULT_MODEL
        self.provider = _provider_label(self.base_url)
        self.timeout = timeout

        self._sync = OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=timeout)
        self._async = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url, timeout=timeout)

    @staticmethod
    def _to_openai_messages(messages: Iterable[Message]) -> list[dict]:
        return [{"role": m.role, "content": m.content} for m in messages]

    def chat(
        self,
        messages: Iterable[Message],
        *,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        model: Optional[str] = None,
    ) -> str:
        """同步一次性 chat，返回完整回复字符串。"""
        try:
            resp = self._sync.chat.completions.create(
                model=model or self.model,
                messages=self._to_openai_messages(messages),
                temperature=temperature,
                max_tokens=max_tokens,
                stream=False,
            )
        except RateLimitError as e:
            raise LLMRateLimitError(f"{self.provider}速率限制：{e}") from e
        except APIConnectionError as e:
            raise LLMNetworkError(f"{self.provider}网络异常：{e}") from e
        except APIStatusError as e:
            raise LLMError(f"{self.provider} API 状态异常 [{e.status_code}]：{e.message}") from e
        except APIError as e:
            raise LLMError(f"{self.provider} API 错误：{e}") from e

        _emit_usage(model or self.model, getattr(resp, "usage", None))
        choice = resp.choices[0]
        return choice.message.content or ""

    async def chat_stream(
        self,
        messages: Iterable[Message],
        *,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        model: Optional[str] = None,
    ) -> AsyncIterator[str]:
        """异步流式 chat，按 token 流逐块 yield 文本片段。

        FastAPI SSE 路由直接 `async for token in client.chat_stream(...)` 即可。
        """
        try:
            stream = await self._async.chat.completions.create(
                model=model or self.model,
                messages=self._to_openai_messages(messages),
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
                stream_options={"include_usage": True},
            )
        except RateLimitError as e:
            raise LLMRateLimitError(f"{self.provider}速率限制：{e}") from e
        except APIConnectionError as e:
            raise LLMNetworkError(f"{self.provider}网络异常：{e}") from e
        except APIStatusError as e:
            raise LLMError(f"{self.provider} API 状态异常 [{e.status_code}]：{e.message}") from e
        except APIError as e:
            raise LLMError(f"{self.provider} API 错误：{e}") from e

        final_usage = None
        try:
            async for chunk in stream:
                # 末尾带 usage 的 chunk 通常 choices 为空（include_usage 行为）
                if getattr(chunk, "usage", None):
                    final_usage = chunk.usage
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta and delta.content:
                    yield delta.content
        except APIConnectionError as e:
            raise LLMNetworkError(f"{self.provider}流式中断：{e}") from e
        finally:
            _emit_usage(model or self.model, final_usage)


# ============================================================================
# 单例（避免每次都新建 client；线程安全够用）
# ============================================================================


_default_client: Optional[LLMClient] = None


def get_default_client() -> LLMClient:
    """获取默认客户端（懒加载单例）。"""
    global _default_client
    if _default_client is None:
        _default_client = LLMClient()
    return _default_client


def reset_default_client() -> None:
    """主要给测试用：清掉单例，下次 get 会重建。"""
    global _default_client
    _default_client = None


__all__ = [
    "LLMClient",
    "LLMError",
    "LLMConfigError",
    "LLMRateLimitError",
    "LLMNetworkError",
    "get_default_client",
    "get_reasoner_model",
    "llm_runtime_status",
    "reset_default_client",
    "set_usage_observer",
    "DEFAULT_MODEL",
    "DEFAULT_REASONER_MODEL",
    "DEFAULT_BASE_URL",
]
