"""来源质量过滤 + 正文抓取单测（Phase 1 grounding 修复）。

确认：低权威 SEO 网页被过滤掉、官方/论文/GitHub 留下；正文抓取失败静默降级；
build_grounding_context 优先用抓到的正文、退回摘要。
"""

from core.source_search import (
    build_grounding_context,
    fetch_source_text,
    select_quality_sources,
)


def _src(title, url, kind, summary="一段足够长的摘要内容用于占位，确保相关性分达到阈值。"):
    return {"title": title, "url": url, "kind": kind, "summary": summary}


def test_select_quality_drops_seo_web():
    sources = [
        _src("如何用AI阅读论文写作", "http://spam.example.com/a", "web"),
        _src("OpenAI Docs: Function calling", "https://platform.openai.com/docs/guides/function-calling", "official"),
        _src("某SEO站", "https://www.guanxian.org/x", "web"),
        _src("Repo", "https://github.com/foo/bar", "github"),
    ]
    picked = select_quality_sources(sources, limit=3)
    urls = [s["url"] for s in picked]
    assert "https://platform.openai.com/docs/guides/function-calling" in urls
    assert "https://github.com/foo/bar" in urls
    assert all("spam.example.com" not in u and "guanxian.org" not in u for u in urls)


def test_select_quality_can_return_empty():
    sources = [
        _src("SEO 1", "http://a.example.com", "web"),
        _src("SEO 2", "http://b.example.com", "web"),
    ]
    # 全是低权威 web → 诚实返回空，调用方据此不喂噪声
    assert select_quality_sources(sources, limit=3) == []


def test_select_quality_respects_thresholds_override():
    sources = [_src("普通博客", "https://medium.com/p/x", "case")]  # authority 0.68
    assert select_quality_sources(sources, limit=3) == []          # 默认 0.75 卡掉
    picked = select_quality_sources(sources, min_authority=0.6, limit=3)
    assert len(picked) == 1


class _FakeResp:
    def __init__(self, text, content_type="text/html"):
        self.text = text
        self.headers = {"content-type": content_type}

    def raise_for_status(self):
        return None


class _FakeHttp:
    def __init__(self, resp_or_exc):
        self.resp_or_exc = resp_or_exc

    def get(self, url, timeout=None):
        if isinstance(self.resp_or_exc, Exception):
            raise self.resp_or_exc
        return self.resp_or_exc


def test_fetch_source_text_extracts_readable():
    html = "<html><head><title>x</title><style>.a{}</style></head><body><h1>标题</h1><p>这是正文第一段，包含关键判断。</p><script>var a=1;</script><p>第二段正文。</p></body></html>"
    text = fetch_source_text("https://example.com/a", http_client=_FakeHttp(_FakeResp(html)))
    assert "正文第一段" in text
    assert "标题" in text
    assert "var a=1" not in text          # script 去掉
    assert ".a{}" not in text             # style 去掉


def test_fetch_source_text_bad_url_returns_empty():
    assert fetch_source_text("not-a-url") == ""


def test_fetch_source_text_swallows_errors():
    import httpx

    assert fetch_source_text("https://x.example.com", http_client=_FakeHttp(httpx.HTTPError("boom"))) == ""


def test_build_grounding_context_prefers_fetched_body():
    sources = [_src("OpenAI Docs", "https://platform.openai.com/docs", "official", summary="只有摘要")]
    html = "<body><p>这是抓到的正文锚点，比摘要更可靠。</p></body>"
    ctx = build_grounding_context(sources, max_fetch=1, http_client=_FakeHttp(_FakeResp(html)))
    assert "正文摘录" in ctx
    assert "抓到的正文锚点" in ctx


def test_build_grounding_context_falls_back_to_summary():
    sources = [_src("OpenAI Docs", "https://platform.openai.com/docs", "official", summary="重要摘要内容")]
    # 抓取失败 → 退回摘要
    import httpx

    ctx = build_grounding_context(sources, max_fetch=1, http_client=_FakeHttp(httpx.HTTPError("boom")))
    assert "摘要：重要摘要内容" in ctx
