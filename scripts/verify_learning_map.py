#!/usr/bin/env python3
"""校验学习地图、导读、截图引用与源码链接；不访问用户数据或初始化数据库。"""
from pathlib import Path
from html.parser import HTMLParser
from urllib.parse import unquote, urlsplit
import re

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

def verify():
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
    if errors:
        raise SystemExit("\n".join(errors))
    print(f"PASS: {checked} local references, 6 sections, 4 source token mappings, version boundaries")
if __name__ == "__main__":
    verify()

