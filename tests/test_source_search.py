"""Bocha 外部来源搜索单测。

不打真实网络；用 fake http client 锁住响应解析和降级契约。
"""

from core.source_search import (
    build_source_pack,
    format_sources_context,
    normalize_source_pack,
    web_search,
)


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload


class FakeHttpClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def post(self, url, *, headers, json, timeout):
        self.calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return FakeResponse(self.payload)


BOCHA_PAYLOAD = {
    "code": 200,
    "msg": "success",
    "data": {
        "webPages": {
            "value": [
                {
                    "name": "JLPT official N3 guide",
                    "url": "https://www.jlpt.jp/e/about/levelsummary.html",
                    "displayUrl": "jlpt.jp",
                    "siteName": "JLPT",
                    "snippet": "N3 is a bridge level for everyday Japanese.",
                    "summary": "The official JLPT site summarizes what N3 measures.",
                    "datePublished": "2026-01-01",
                },
                {
                    "name": "Duplicate",
                    "url": "https://www.jlpt.jp/e/about/levelsummary.html/",
                    "summary": "same url with trailing slash",
                },
                {
                    "name": "Grammar reference",
                    "url": "https://example.com/n3-grammar",
                    "summary": "N3 grammar list with examples.",
                },
            ]
        }
    },
}


def test_build_source_pack_disabled_without_key(monkeypatch):
    monkeypatch.delenv("BOCHA_API_KEY", raising=False)
    pack = build_source_pack("JLPT N3")
    assert pack["status"] == "disabled"
    assert pack["sources"] == []
    assert "BOCHA_API_KEY" in pack["note"]


def test_build_source_pack_uses_curated_ai_sources_without_key(monkeypatch):
    monkeypatch.delenv("BOCHA_API_KEY", raising=False)
    pack = build_source_pack("AI 领域 harness 工程 function calling agent", count=4)

    assert pack["status"] == "curated"
    assert len(pack["sources"]) == 4
    assert pack["query_plan"]
    assert pack["ambiguity"]["candidates"]
    urls = [src["url"] for src in pack["sources"]]
    assert "https://platform.openai.com/docs/guides/function-calling" in urls
    assert "https://microsoft.github.io/autogen/stable/" in urls
    assert all(src["title"] and src["url"] for src in pack["sources"])
    assert all(src["source_type"] in {"official", "github"} for src in pack["sources"])
    assert all(src["authority_score"] >= 0.8 for src in pack["sources"])


def test_build_source_pack_prioritizes_mcp_sources_without_key(monkeypatch):
    monkeypatch.delenv("BOCHA_API_KEY", raising=False)
    pack = build_source_pack("Model Context Protocol MCP 系统知识", count=3)

    assert pack["status"] == "curated"
    urls = [src["url"] for src in pack["sources"]]
    assert urls[0] == "https://modelcontextprotocol.io/introduction"
    assert "https://github.com/modelcontextprotocol" in urls
    assert pack["sources"][0]["source_type"] == "official"


def test_web_search_normalizes_and_dedupes(monkeypatch):
    monkeypatch.delenv("BOCHA_API_KEY", raising=False)
    fake = FakeHttpClient(BOCHA_PAYLOAD)
    sources = web_search("JLPT N3", api_key="test-key", count=5, http_client=fake)

    assert len(sources) == 2
    assert sources[0]["title"] == "JLPT official N3 guide"
    assert sources[0]["url"].startswith("https://www.jlpt.jp/")
    assert sources[0]["site"] == "JLPT"
    assert sources[0]["summary"].startswith("The official JLPT site")
    assert sources[0]["source_type"] == "official"
    assert sources[0]["claims"]
    assert fake.calls[0]["json"]["summary"] is True
    assert fake.calls[0]["headers"]["Authorization"].startswith("Bearer ")


def test_build_source_pack_ok_and_format_context(monkeypatch):
    monkeypatch.delenv("BOCHA_API_KEY", raising=False)
    fake = FakeHttpClient(BOCHA_PAYLOAD)
    pack = build_source_pack("JLPT N3", api_key="test-key", count=2, http_client=fake)
    assert pack["status"] == "ok"
    assert len(pack["sources"]) == 2
    assert pack["query_plan"][0]["purpose"] == "概念澄清"

    context = format_sources_context(pack["sources"], start=3)
    assert "[来源3]" in context
    assert "URL：https://www.jlpt.jp/e/about/levelsummary.html" in context
    assert "[来源4]" in context


def test_normalize_source_pack_backfills_legacy_metadata():
    pack = normalize_source_pack(
        {
            "status": "ok",
            "sources": [
                {
                    "title": "LangChain Agents",
                    "url": "https://docs.langchain.com/oss/python/langchain/agents",
                    "summary": "Agent runtime with tools and context.",
                    "kind": "web",
                }
            ],
        },
        fallback_query="AI agent harness 工程",
    )

    assert pack["query"] == "AI agent harness 工程"
    assert pack["query_plan"]
    assert pack["ambiguity"]["candidates"]
    assert pack["sources"][0]["source_type"] == "official"
    assert pack["sources"][0]["authority_score"] >= 0.8
    assert pack["sources"][0]["claims"]
