"""可插拔 Embedding 客户端（RAG dense 向量 · 方案 §3.2）。

设计原则（用户要求"技术方案真做"）：
- 真实调用 OpenAI 兼容的 embedding 接口（如云雾代理），不是 mock。
- **可插拔**：未配置 API 时返回 None，调用方自动降级为 BM25 纯词法检索——
  这是"真实的降级路径"，不是假数据。用户后续把 key 填进 .env，dense 向量即生效。

环境变量（与主对话 key 解耦，方便指向不同代理）：
    ITUTOR_EMBED_API_KEY     embedding 服务 key（未填 = 关闭 dense，走 BM25）
    ITUTOR_EMBED_BASE_URL    OpenAI 兼容 base_url（默认 https://api.openai.com/v1）
    ITUTOR_EMBED_MODEL       模型名（默认 text-embedding-3-small）
"""

from __future__ import annotations

import os
from typing import Optional

from openai import OpenAI

DEFAULT_EMBED_BASE_URL = "https://api.openai.com/v1"
DEFAULT_EMBED_MODEL = "text-embedding-3-small"


def is_configured() -> bool:
    return bool(os.getenv("ITUTOR_EMBED_API_KEY"))


class Embedder:
    """OpenAI 兼容的 embedding 封装。失败抛 RuntimeError，由调用方降级。"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = 30.0,
    ):
        api_key = api_key or os.getenv("ITUTOR_EMBED_API_KEY")
        if not api_key:
            raise RuntimeError("未配置 ITUTOR_EMBED_API_KEY")
        self.api_key = api_key
        self.base_url = base_url or os.getenv("ITUTOR_EMBED_BASE_URL", DEFAULT_EMBED_BASE_URL)
        self.model = model or os.getenv("ITUTOR_EMBED_MODEL", DEFAULT_EMBED_MODEL)
        self._client = OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=timeout)

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """批量向量化。任意异常向上抛，让调用方降级到 BM25。"""
        if not texts:
            return []
        resp = self._client.embeddings.create(model=self.model, input=texts)
        # 保持与输入顺序一致
        return [d.embedding for d in sorted(resp.data, key=lambda d: d.index)]


def get_embedder() -> Optional[Embedder]:
    """返回 Embedder；未配置或初始化失败则返回 None（调用方走 BM25）。"""
    if not is_configured():
        return None
    try:
        return Embedder()
    except Exception:  # noqa: BLE001
        return None


__all__ = ["Embedder", "get_embedder", "is_configured", "DEFAULT_EMBED_MODEL"]
