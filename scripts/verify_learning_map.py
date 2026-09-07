#!/usr/bin/env python3
"""校验学习地图、导读、截图引用与源码链接；不访问用户数据或初始化数据库。"""
from pathlib import Path
from html.parser import HTMLParser
from urllib.parse import unquote, urlsplit
import re
import argparse
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]
class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.ids = set()
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id"):
            self.ids.add(attrs["id"])
        for key in ("href", "src"):
            if attrs.get(key):
                self.links.append(attrs[key])

def verify(article_source=None):
    page = ROOT / "docs/学习地图.html"
    content = page.read_text()
    parsed = Links()
    parsed.feed(content)
    errors = []
    checked = 0
    for link in parsed.links:
        uri = urlsplit(link)
        if uri.scheme or uri.netloc:
            continue
        target = (page.parent / unquote(uri.path)).resolve() if uri.path else page
        if not target.is_relative_to(ROOT) or not target.exists():
            errors.append(f"missing/unsafe: {link}")
            continue
        if uri.fragment and target.suffix == ".html":
            child = Links()
            child.feed(target.read_text())
            if unquote(uri.fragment) not in child.ids:
                errors.append(f"missing anchor: {link}")
        checked += 1
    for required in ("product", "design", "architecture", "agent", "workbench", "verify"):
        assert required in parsed.ids
    assert "人工维护" in content and "真实飞书" in content
    source = (ROOT / "web/index.html").read_text()
    for name, value in (("teal", "#2c6e6a"), ("amber", "#d98a3d"), ("paper", "#f7f4ee"), ("red", "#c2603f")):
        assert re.search(rf"--{name}:\s*{value}", source, re.I)
        assert value in content
    assert "id=\"open-learning\"" in (ROOT / "docs/LearnBuddy项目工作台.html").read_text()
    article = (ROOT / "docs/AIBuilder第一周.md").read_text()
    images = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", article)
    delivery = json.loads((ROOT / "docs/assets/learning-map/delivery-provenance.json").read_text())["current_article"]
    assert images == delivery["images"], "article image manifest drift"
    assert hashlib.sha256(article.encode()).hexdigest() == delivery["sha256"], "article revision manifest drift"
    for public_path in delivery["preserved_public_paths"]:
        target = (ROOT / unquote(public_path.split("#", 1)[0])).resolve()
        assert target.is_relative_to(ROOT) and target.exists(), public_path
    if article_source:
        expected = Path(article_source).read_text().replace("(assets/", "(./assets/learning-map/")
        assert expected.strip() == article.strip(), "repository article differs from approved source"
    for name in ("design_lesson", "write_lesson", "critic_lesson", "revise_lesson", "_run_lesson_quality_loop"):
        assert name in article
        assert f"def {name}(" in (ROOT / "core/content.py").read_text(), name
    for image in images:
        assert (ROOT / "docs" / image).resolve().is_file(), image
    for removed in ("03-page-preview.png", "01-open-process-aigc.png", "06-workbench.png"):
        assert removed not in article
    for relative in re.findall(r"https://github.com/wanghui2323/learnbuddy/(?:blob|tree)/main/([^\s)]+)", article):
        assert (ROOT / unquote(relative.split("#", 1)[0])).exists(), relative
    design = (ROOT / "docs/AI_DESIGN.md").read_text()
    for required in ("页面合同", "视觉语言", "组件与状态", "AI 交互", "Design QA", "未对业务页面"):
        assert required in design
    prd = (ROOT / "docs/01-需求方案.md").read_text()
    assert prd.index("## 当前版本 PRD") < prd.index("## 历史档案")
    assert "LB-BASE-01" in prd and "Given" in prd
    if errors:
        raise SystemExit("\n".join(errors))
    print(f"PASS: {checked} map references; {len(images)} article images; {len(delivery['preserved_public_paths'])} preserved public paths; article hash and optional source; PRD/AI Design contracts")
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--article-source", help="Optional author-approved Markdown to compare, without local path assumptions")
    verify(parser.parse_args().article_source)
