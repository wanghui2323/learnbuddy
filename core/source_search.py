"""外部来源搜索（Bocha Web Search）。

这一层只把在线搜索结果整理成 LearnBuddy 可消费的来源包：
- 不打印、不返回 API key。
- 搜索失败不阻断出课，调用方根据 status 降级。
- 只保留标题、URL、站点、摘要、发布时间等可展示字段。
"""

from __future__ import annotations

import html
import os
import re
from typing import Any, Optional

import httpx
from dotenv import load_dotenv

load_dotenv()

DEFAULT_BOCHA_WEB_SEARCH_URL = "https://api.bocha.cn/v1/web-search"

_AI_SOURCE_MARKERS = (
    "agent",
    "agents",
    "autogen",
    "chatgpt",
    "function calling",
    "harness",
    "langchain",
    "langgraph",
    "llm",
    "mcp",
    "model context protocol",
    "openai",
    "rag",
    "tool calling",
    "工具调用",
    "函数调用",
    "智能体",
    "大模型",
    "模型",
    "编排",
    "评测",
    "评估",
)

_CURATED_AI_AGENT_SOURCES = [
    {
        "title": "Model Context Protocol Docs: Introduction",
        "url": "https://modelcontextprotocol.io/introduction",
        "site": "Model Context Protocol",
        "summary": "MCP 官方介绍，说明该协议如何为 AI 应用连接外部工具、资源、提示词和上下文。",
        "date_published": "",
        "date_crawled": "",
        "kind": "official",
    },
    {
        "title": "Model Context Protocol GitHub Organization",
        "url": "https://github.com/modelcontextprotocol",
        "site": "GitHub",
        "summary": "MCP 官方 GitHub 组织，包含协议 SDK、服务器示例和生态实现。",
        "date_published": "",
        "date_crawled": "",
        "kind": "github",
    },
    {
        "title": "OpenAI Docs: Function calling",
        "url": "https://platform.openai.com/docs/guides/function-calling",
        "site": "OpenAI Platform",
        "summary": "OpenAI 官方关于函数调用 / 工具调用模式、参数结构化输出和工具编排的文档。",
        "date_published": "",
        "date_crawled": "",
        "kind": "official",
    },
    {
        "title": "LangChain Docs: Agents",
        "url": "https://docs.langchain.com/oss/python/langchain/agents",
        "site": "LangChain Docs",
        "summary": "LangChain 官方关于 Agent 运行时、模型调用、工具调用和上下文工程的文档。",
        "date_published": "",
        "date_crawled": "",
        "kind": "official",
    },
    {
        "title": "Microsoft AutoGen Documentation",
        "url": "https://microsoft.github.io/autogen/stable/",
        "site": "Microsoft AutoGen",
        "summary": "AutoGen 官方文档，覆盖多智能体应用、工具调用、对话式工作流与运行时设计。",
        "date_published": "",
        "date_crawled": "",
        "kind": "official",
    },
    {
        "title": "EleutherAI lm-evaluation-harness",
        "url": "https://github.com/EleutherAI/lm-evaluation-harness",
        "site": "GitHub",
        "summary": "开源模型评测框架，用于理解 evaluation harness、任务适配和评测流水线的工程化做法。",
        "date_published": "",
        "date_crawled": "",
        "kind": "github",
    },
    {
        "title": "LangSmith Docs: Evaluation",
        "url": "https://docs.smith.langchain.com/evaluation",
        "site": "LangSmith Docs",
        "summary": "LangSmith 官方评估文档，覆盖数据集、评测器、实验对比和应用质量回归。",
        "date_published": "",
        "date_crawled": "",
        "kind": "official",
    },
]


class SourceSearchError(Exception):
    """外部搜索失败。"""


def _clean_text(value: Any, limit: int = 500) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _looks_like_url(value: str) -> bool:
    return value.startswith("http://") or value.startswith("https://")


def _ai_source_relevant(query: str) -> bool:
    q = query.lower()
    if re.search(r"(?<![a-z])ai(?![a-z])", q):
        return True
    return any(marker in q for marker in _AI_SOURCE_MARKERS)


def _dedupe_sources(sources: list[dict], limit: int) -> list[dict]:
    deduped: list[dict] = []
    seen_urls: set[str] = set()
    for src in sources:
        if not isinstance(src, dict):
            continue
        url = _clean_text(src.get("url"), 700)
        title = _clean_text(src.get("title"), 160)
        if not url or not title:
            continue
        norm_url = url.rstrip("/").lower()
        if norm_url in seen_urls:
            continue
        seen_urls.add(norm_url)
        normalized = dict(src)
        normalized["title"] = title
        normalized["url"] = url
        normalized["site"] = _clean_text(src.get("site"), 120)
        normalized["summary"] = _clean_text(src.get("summary"), 700)
        normalized["date_published"] = _clean_text(src.get("date_published"), 40)
        normalized["date_crawled"] = _clean_text(src.get("date_crawled"), 40)
        normalized["kind"] = _clean_text(src.get("kind") or "web", 40)
        _enrich_source(normalized)
        deduped.append(normalized)
        if len(deduped) >= limit:
            break
    return deduped


def _source_type(src: dict) -> str:
    kind = _clean_text(src.get("kind"), 40).lower()
    url = _clean_text(src.get("url"), 700).lower()
    site = _clean_text(src.get("site"), 120).lower()
    title = _clean_text(src.get("title"), 180).lower()
    # paper 类型只认 URL/站点/显式 kind，不认标题——否则 SEO 标题里塞个"论文"
    # 就会被错判成 paper，把垃圾来源当权威喂进去。"official" 是较强正向信号予以保留。
    domain_blob = " ".join([kind, url, site])
    soft_blob = domain_blob + " " + title
    if kind == "official" or "official" in soft_blob or any(x in domain_blob for x in ("docs.", "documentation", "platform.openai.com", "microsoft.github.io", "langchain.com")):
        return "official"
    if kind == "github" or "github.com" in domain_blob:
        return "github"
    if kind == "paper" or any(x in domain_blob for x in ("arxiv", "doi.org", "ieee", "acm.org")):
        return "paper"
    if any(x in soft_blob for x in ("bilibili", "youtube", "video", "视频")):
        return "video"
    if any(x in soft_blob for x in ("案例", "blog", "medium.com", "substack")):
        return "case"
    return "web"


def _authority_score(src: dict) -> float:
    stype = src.get("source_type") or _source_type(src)
    scores = {
        "official": 0.95,
        "paper": 0.88,
        "github": 0.82,
        "case": 0.68,
        "video": 0.6,
        "web": 0.55,
    }
    return scores.get(stype, 0.55)


def _freshness_score(src: dict) -> float:
    # 当前只做轻量启发；后续可解析具体日期并按领域加权。
    date = _clean_text(src.get("date_published") or src.get("date_crawled"), 40)
    return 0.7 if date else 0.45


def _relevance_score(src: dict) -> float:
    summary = _clean_text(src.get("summary"), 700)
    title = _clean_text(src.get("title"), 180)
    if summary and title:
        return 0.72
    if title:
        return 0.58
    return 0.4


def _claims_from_summary(src: dict) -> list[str]:
    text = _clean_text(src.get("summary"), 700)
    if not text:
        return []
    parts = [p.strip(" 。.；;") for p in re.split(r"[。.;；]\s*", text) if p.strip()]
    return [_clean_text(p, 180) for p in parts[:2]]


def _enrich_source(src: dict) -> dict:
    src["source_type"] = _clean_text(src.get("source_type") or _source_type(src), 40)
    src["authority_score"] = round(float(src.get("authority_score") or _authority_score(src)), 2)
    src["freshness_score"] = round(float(src.get("freshness_score") or _freshness_score(src)), 2)
    src["relevance_score"] = round(float(src.get("relevance_score") or _relevance_score(src)), 2)
    claims = src.get("claims")
    src["claims"] = claims if isinstance(claims, list) else _claims_from_summary(src)
    return src


def _query_plan(query: str) -> list[dict]:
    base = _clean_text(query, 180)
    if not base:
        return []
    plan = [
        {"purpose": "概念澄清", "query": base},
        {"purpose": "权威来源", "query": f"{base} official docs OR documentation"},
        {"purpose": "实践案例", "query": f"{base} tutorial case study best practices"},
    ]
    if _ai_source_relevant(query):
        plan.append({"purpose": "工程评估", "query": f"{base} evaluation framework agent workflow"})
    return plan[:4]


def _ambiguity_for_query(query: str) -> dict:
    q = query.lower()
    candidates: list[dict] = []
    if "harness" in q:
        candidates = [
            {"label": "AI 应用运行/落地 harness", "hint": "模型、Agent、工具、数据流、评估和观测组成的工程系统。"},
            {"label": "AI/LLM 评测 harness", "hint": "围绕 benchmark、任务适配和回归评测的测试框架。"},
            {"label": "Harness.io DevOps 平台", "hint": "持续交付、Feature Flags、云成本等 DevOps 产品。"},
        ]
    elif "agent" in q or "智能体" in q:
        candidates = [
            {"label": "AI Agent 工作流", "hint": "模型根据目标选择工具、记忆和行动步骤。"},
            {"label": "客服/销售等业务代理", "hint": "面向具体业务角色的自动化助手。"},
        ]
    if not candidates:
        return {"term": "", "candidates": [], "confidence": 0.0}
    return {"term": candidates[0]["label"].split(" ")[0], "candidates": candidates, "confidence": 0.68}


def _curated_sources_for_query(query: str, limit: int) -> list[dict]:
    if not _ai_source_relevant(query):
        return []
    pool = list(_CURATED_AI_AGENT_SOURCES)
    q = query.lower()
    if "mcp" not in q and "model context protocol" not in q:
        pool = [
            src
            for src in pool
            if "modelcontextprotocol" not in str(src.get("url") or "").lower()
        ] + [
            src
            for src in pool
            if "modelcontextprotocol" in str(src.get("url") or "").lower()
        ]
    return _dedupe_sources(pool, max(1, min(limit, len(pool))))


def _endpoint_from_env() -> str:
    explicit = (
        os.getenv("BOCHA_WEB_SEARCH_URL", "").strip()
        or os.getenv("BOCHA_SEARCH_URL", "").strip()
    )
    if explicit and _looks_like_url(explicit):
        return explicit

    base = os.getenv("BOCHA_BASE_URL", "").strip()
    if base and _looks_like_url(base):
        base = base.rstrip("/")
        if base.endswith("/v1/web-search"):
            return base
        return f"{base}/v1/web-search"

    return DEFAULT_BOCHA_WEB_SEARCH_URL


def is_configured(api_key: Optional[str] = None) -> bool:
    """是否已配置 Bocha API key。"""
    return bool((api_key or os.getenv("BOCHA_API_KEY") or "").strip())


def _normalize_sources(items: list[dict], limit: int) -> list[dict]:
    sources: list[dict] = []
    seen_urls: set[str] = set()

    for item in items:
        if not isinstance(item, dict):
            continue
        url = _clean_text(item.get("url"), 700)
        title = _clean_text(item.get("name") or item.get("title"), 160)
        if not url or not title:
            continue
        norm_url = url.rstrip("/").lower()
        if norm_url in seen_urls:
            continue
        seen_urls.add(norm_url)

        sources.append(
            {
                "title": title,
                "url": url,
                "site": _clean_text(item.get("siteName") or item.get("displayUrl"), 120),
                "summary": _clean_text(item.get("summary") or item.get("snippet"), 700),
                "date_published": _clean_text(item.get("datePublished"), 40),
                "date_crawled": _clean_text(item.get("dateLastCrawled"), 40),
                "kind": "web",
            }
        )
        if len(sources) >= limit:
            break

    return _dedupe_sources(sources, limit)


def web_search(
    query: str,
    *,
    count: int = 5,
    freshness: str = "noLimit",
    summary: bool = True,
    api_key: Optional[str] = None,
    endpoint: Optional[str] = None,
    timeout: float = 8.0,
    http_client: Any = None,
) -> list[dict]:
    """调用 Bocha Web Search，返回归一化来源列表。

    单测可传入 http_client；真实调用默认用 httpx.Client。
    """
    query = _clean_text(query, 240)
    if not query:
        return []

    key = (api_key or os.getenv("BOCHA_API_KEY") or "").strip()
    if not key:
        raise SourceSearchError("未配置 BOCHA_API_KEY")

    payload = {
        "query": query,
        "summary": bool(summary),
        "freshness": freshness or "noLimit",
        "count": max(1, min(int(count or 5), 10)),
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    url = endpoint or _endpoint_from_env()

    try:
        if http_client is not None:
            resp = http_client.post(url, headers=headers, json=payload, timeout=timeout)
        else:
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise SourceSearchError(f"Bocha Web Search 调用失败：{type(exc).__name__}") from exc

    if not isinstance(data, dict):
        raise SourceSearchError("Bocha Web Search 返回格式异常")
    if data.get("code") not in (None, 200, "200"):
        msg = _clean_text(data.get("msg") or data.get("message"), 160)
        raise SourceSearchError(f"Bocha Web Search 返回错误：{msg or data.get('code')}")

    values = (
        ((data.get("data") or {}).get("webPages") or {}).get("value")
        if isinstance(data.get("data"), dict)
        else None
    )
    if not isinstance(values, list):
        return []
    return _normalize_sources(values, payload["count"])


def build_source_pack(
    query: str,
    *,
    count: int = 5,
    freshness: str = "noLimit",
    api_key: Optional[str] = None,
    timeout: float = 8.0,
    http_client: Any = None,
) -> dict:
    """构建出课可用来源包。失败时返回状态，不中断主链路。"""
    query = _clean_text(query, 240)
    if not query:
        return {"status": "empty_query", "query": "", "sources": []}
    limit = max(1, min(int(count or 5), 10))
    curated_sources = _curated_sources_for_query(query, limit)
    base_pack = {
        "query": query,
        "query_plan": _query_plan(query),
        "ambiguity": _ambiguity_for_query(query),
    }
    if not is_configured(api_key):
        if curated_sources:
            return {
                **base_pack,
                "status": "curated",
                "sources": curated_sources,
                "note": "未配置 BOCHA_API_KEY，已使用内置权威来源兜底。",
            }
        return {
            **base_pack,
            "status": "disabled",
            "sources": [],
            "note": "未配置 BOCHA_API_KEY，本节未进行联网来源核验。",
        }

    try:
        sources = web_search(
            query,
            count=count,
            freshness=freshness,
            api_key=api_key,
            timeout=timeout,
            http_client=http_client,
        )
    except SourceSearchError as exc:
        if curated_sources:
            return {
                **base_pack,
                "status": "curated_after_error",
                "sources": curated_sources,
                "note": f"{exc}；已使用内置权威来源兜底。",
            }
        return {**base_pack, "status": "error", "sources": [], "note": str(exc)}

    if sources:
        merged_sources = _dedupe_sources([*sources, *curated_sources], limit)
        return {**base_pack, "status": "ok", "sources": merged_sources}
    if curated_sources:
        return {
            **base_pack,
            "status": "curated_after_empty",
            "sources": curated_sources,
            "note": "联网搜索未返回可用来源，已使用内置权威来源兜底。",
        }
    return {**base_pack, "status": "empty", "sources": []}


def normalize_source_pack(source_pack: Any, *, fallback_query: str = "", count: int = 5) -> dict:
    """把旧版/部分来源包补齐为新版结构。

    只做确定性归一化，不联网，不修改原对象。用于旧缓存运行时兼容。
    """
    query = _clean_text(
        (source_pack or {}).get("query") if isinstance(source_pack, dict) else "",
        240,
    ) or _clean_text(fallback_query, 240)
    if not isinstance(source_pack, dict):
        source_pack = {}
    limit = max(1, min(int(count or 5), 10))
    sources = _dedupe_sources(source_pack.get("sources") or [], limit)
    return {
        **source_pack,
        "query": query,
        "query_plan": source_pack.get("query_plan") or _query_plan(query),
        "ambiguity": source_pack.get("ambiguity") or _ambiguity_for_query(query),
        "status": source_pack.get("status") or ("ok" if sources else "empty"),
        "sources": sources,
    }


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "").strip() or default)
    except (TypeError, ValueError):
        return default


def select_quality_sources(
    sources: list[dict],
    *,
    min_authority: Optional[float] = None,
    min_relevance: Optional[float] = None,
    limit: int = 3,
) -> list[dict]:
    """只留下高权威 + 相关的来源，低质 SEO 网页直接丢。

    用 `_enrich_source` 已算好的 authority/relevance 分过滤。默认阈值偏严，
    宁可返回空（让上层诚实地按通用知识生成），也不要把垃圾来源当权威喂给模型。
    阈值可用 ITUTOR_SOURCE_MIN_AUTHORITY / ITUTOR_SOURCE_MIN_RELEVANCE 调。
    """
    if min_authority is None:
        min_authority = _env_float("ITUTOR_SOURCE_MIN_AUTHORITY", 0.75)
    if min_relevance is None:
        min_relevance = _env_float("ITUTOR_SOURCE_MIN_RELEVANCE", 0.6)
    picked: list[dict] = []
    for src in sources or []:
        if not isinstance(src, dict):
            continue
        enriched = _enrich_source(dict(src))
        if (
            float(enriched.get("authority_score") or 0) >= min_authority
            and float(enriched.get("relevance_score") or 0) >= min_relevance
        ):
            picked.append(enriched)
    picked.sort(
        key=lambda s: (float(s.get("authority_score") or 0) + float(s.get("relevance_score") or 0)),
        reverse=True,
    )
    if limit <= 0:
        return []
    return picked[:limit]


def _extract_readable_text(html_text: str, max_chars: int = 2400) -> str:
    """从 HTML 里粗提正文：去脚本/样式/标签，反转义，合并空白后截断。

    不引新依赖，正则即可；目标是给模型一段可读正文锚点，不追求精确排版。
    """
    if not html_text:
        return ""
    text = re.sub(r"(?is)<(script|style|noscript|svg|head)[^>]*>.*?</\1>", " ", html_text)
    text = re.sub(r"(?is)<!--.*?-->", " ", text)
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)</(p|div|li|h[1-6]|tr|section|article)>", "\n", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = html.unescape(text)
    lines = [re.sub(r"[ \t\u00a0]+", " ", ln).strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if len(ln) >= 2]
    cleaned = "\n".join(lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 1].rstrip() + "…"


def fetch_source_text(
    url: str,
    *,
    timeout: float = 6.0,
    max_chars: int = 2400,
    http_client: Any = None,
) -> str:
    """抓取单个来源的正文文本，失败静默返回空串（不阻断出课）。"""
    url = _clean_text(url, 700)
    if not _looks_like_url(url):
        return ""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        )
    }
    try:
        if http_client is not None:
            resp = http_client.get(url, timeout=timeout)
        else:
            with httpx.Client(timeout=timeout, follow_redirects=True, headers=headers) as client:
                resp = client.get(url)
        resp.raise_for_status()
        content_type = str(resp.headers.get("content-type", "")).lower()
        if content_type and "html" not in content_type and "text" not in content_type:
            return ""
        return _extract_readable_text(resp.text, max_chars=max_chars)
    except (httpx.HTTPError, ValueError, TypeError, UnicodeDecodeError):
        return ""


def build_grounding_context(
    sources: list[dict],
    *,
    start: int = 1,
    max_fetch: int = 2,
    max_chars_each: int = 2400,
    fetch: bool = True,
    http_client: Any = None,
) -> str:
    """把已过滤的高质量来源拼成带正文锚点的 grounding 上下文。

    前 max_fetch 个来源尝试抓正文；抓不到就退回摘要。低质来源应在调用前用
    select_quality_sources 过滤掉，这里只负责组织已选中的来源。
    """
    blocks: list[str] = []
    for offset, src in enumerate(sources, start):
        title = _clean_text(src.get("title"), 160)
        url = _clean_text(src.get("url"), 700)
        site = _clean_text(src.get("site"), 120)
        summary = _clean_text(src.get("summary"), 700)
        parts = [f"[来源{offset}]《{title}》"]
        if site:
            parts.append(f"站点：{site}")
        if url:
            parts.append(f"URL：{url}")
        body = ""
        if fetch and (offset - start) < max_fetch:
            body = fetch_source_text(url, max_chars=max_chars_each, http_client=http_client)
        if body:
            parts.append(f"正文摘录：\n{body}")
        elif summary:
            parts.append(f"摘要：{summary}")
        blocks.append("\n".join(parts))
    return "\n\n".join(blocks)


def format_sources_context(sources: list[dict], *, start: int = 1) -> str:
    """把来源列表格式化为 prompt 上下文。"""
    lines = []
    for offset, src in enumerate(sources, start):
        title = _clean_text(src.get("title"), 160)
        url = _clean_text(src.get("url"), 700)
        site = _clean_text(src.get("site"), 120)
        summary = _clean_text(src.get("summary"), 700)
        date_published = _clean_text(src.get("date_published"), 40)
        parts = [f"[来源{offset}]《{title}》"]
        if site:
            parts.append(f"站点：{site}")
        if date_published:
            parts.append(f"发布时间：{date_published}")
        if url:
            parts.append(f"URL：{url}")
        if summary:
            parts.append(f"摘要：{summary}")
        lines.append("\n".join(parts))
    return "\n\n".join(lines)


__all__ = [
    "DEFAULT_BOCHA_WEB_SEARCH_URL",
    "SourceSearchError",
    "build_grounding_context",
    "build_source_pack",
    "fetch_source_text",
    "format_sources_context",
    "is_configured",
    "normalize_source_pack",
    "select_quality_sources",
    "web_search",
]
