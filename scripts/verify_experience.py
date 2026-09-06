"""产品体验打磨（V0.28）端到端验证。

覆盖：
- web/common.css / web/common.js 存在
- 3 个页面（space/instance/self-check）都引用 common.css + common.js
- common.css 含关键 CSS 类（toast / spinner / loading / @media / focus-visible）
- common.js 含关键函数（showToast / setButtonLoading / fetchJson）
- self-check.html submit 已接入新工具
- server 拉 common.css / common.js 200
- viewport meta 3 页齐全
"""
from __future__ import annotations
import os, sys, json, re, pathlib, subprocess

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
fails: list[str] = []


def check(name, ok, detail=""):
    print(f"  [{PASS if ok else FAIL}] {name}" + (f" — {detail}" if detail else ""))
    if not ok: fails.append(name)


def http_get(path):
    r = subprocess.run(
        ["curl", "-s", "-o", "/tmp/_exp_body", "-w", "%{http_code}", f"http://127.0.0.1:8000{path}", "--max-time", "10"],
        capture_output=True, text=True, timeout=15,
    )
    code = int(r.stdout.strip() or 0)
    try: body = open("/tmp/_exp_body", encoding="utf-8", errors="replace").read()
    except OSError: body = ""
    return code, body


# ---- 1. 文件存在 ----
check("web/common.css 存在", (ROOT / "web/common.css").is_file())
check("web/common.js 存在", (ROOT / "web/common.js").is_file())

css = (ROOT / "web/common.css").read_text(encoding="utf-8")
js = (ROOT / "web/common.js").read_text(encoding="utf-8")

# ---- 2. common.css 关键 CSS ----
css_checks = {
    "CSS .toast 类": ".toast " in css and ".toast.err" in css,
    "CSS #toastBox 容器": "#toastBox" in css,
    "CSS .spinner 动画": ".spinner" in css and "@keyframes spin" in css,
    "CSS button.loading 禁用态": "button.loading" in css or ".btn.loading" in css,
    "CSS @media 移动端": "@media (max-width: 720px)" in css,
    "CSS :focus-visible 键盘可达": ":focus-visible" in css,
    "CSS 1dvh 移动键盘修复": "100dvh" in css,
}
for name, ok in css_checks.items():
    check(name, ok)

# ---- 3. common.js 关键函数 ----
js_checks = {
    "JS showToast": "function showToast" in js,
    "JS setButtonLoading": "function setButtonLoading" in js,
    "JS fetchJson": "function fetchJson" in js,
    "JS toast role aria": "setAttribute('role'" in js and "setAttribute('aria-busy'" in js,
    "JS XSS 转义": "escapeHtml" in js,
    "JS 网络错误兜底": "网络异常" in js,
}
for name, ok in js_checks.items():
    check(name, ok)

# ---- 4. 3 个页面引用 common.css + common.js ----
for p in ("space.html", "instance.html", "self-check.html"):
    h = (ROOT / "web" / p).read_text(encoding="utf-8")
    check(f"{p} 引用 /common.css", '/common.css' in h)
    check(f"{p} 引用 /common.js", '/common.js' in h)
    check(f"{p} 含 viewport meta", 'name="viewport"' in h)

# ---- 5. self-check.html submit 已接入新工具 ----
sc = (ROOT / "web/self-check.html").read_text(encoding="utf-8")
check("self-check submit 用 fetchJson", "submit() {" in sc and "fetchJson" in sc and "POST" in sc)
check("self-check submit 用 setButtonLoading", "setButtonLoading" in sc)
check("self-check submit 成功 toast", 'showToast("本周自检已提交"' in sc)

# ---- 6. server 拉 common.css / common.js 200 ----
s, body = http_get("/common.css")
check("GET /common.css 200", s == 200, f"status={s} len={len(body)}")
s, body = http_get("/common.js")
check("GET /common.js 200", s == 200, f"status={s} len={len(body)}")

# ---- 7. 静态资源也 200（不破坏 3 个页）----
for p in ("/space", "/self-check/japanese-jlpt-n3", "/instance/japanese-jlpt-n3"):
    s, _ = http_get(p)
    check(f"GET {p} 200（不破坏 3 个页）", s in (200, 302), f"status={s}")

# ---- 8. CSS 选择器安全性（无 unclosed 括号）----
def css_valid(s):
    return s.count("{") == s.count("}")
check("common.css 括号匹配", css_valid(css))

# ---- 9. JS 语法简单校验（无未闭合大括号）----
def js_valid(s):
    return s.count("{") == s.count("}")
check("common.js 括号匹配", js_valid(js))

# ---- 10. 不引外部图表库（不引 d3/chart.js/echarts）----
check("common.css 不引外部库", "http" not in css.split("@media")[0])
check("common.js 不引外部库", "import " not in js and "fetch(" not in js.split("//")[0])

# ---- 11. toast 动画时序合理（toastIn < 1s）----
m = re.search(r"animation:\s*toastIn\s+([\d.]+)s", css)
check("toastIn 时长 < 1s", m and float(m.group(1)) < 1.0, f"got={m.group(1) if m else 'n/a'}s")

print("\n=== 总结 ===")
if fails:
    print(f"❌ {len(fails)} 项未通过：")
    for f in fails: print(f"   - {f}")
    sys.exit(1)
print(f"✅ 全部通过")
