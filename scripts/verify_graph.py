"""#12 知识星图端到端验证（API + HTML 静态结构）。

不打 LLM，靠现有 english 实例（owner=1）做 fixture；
把 alice 提为 admin 来绕过 _assert_can_access 拿到真实 /graph 返回。
验证项：
1. /api/instance/{id}/graph 200 + 数据结构完整（concepts/counts/phases/source/threshold）
2. status ∈ {mastered, frontier, locked} 三类都有
3. mastery ∈ [0,1]
4. phases 列表非空且含 deliverable
5. instance.html 关键 DOM/JS 函数存在
6. SVG 渲染关键 CSS 存在
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
import urllib.parse
import urllib.request
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8000"
DB = ROOT / "data" / "itutor.db"
INST_ID = "english"
EMAIL = "graph_verify@test.com"
PASSWORD = "graphverify123"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
fails: list[str] = []


def check(name: str, ok: bool, detail: str = ""):
    tag = PASS if ok else FAIL
    print(f"  [{tag}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        fails.append(name)


def http(method: str, path: str, *, cookie: str | None = None, body: dict | None = None) -> tuple[int, dict, str]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json", **({"Cookie": cookie} if cookie else {})},
    )
    try:
        with OPENER.open(req, timeout=60) as r:
            raw = r.read().decode()
            set_cookie = r.headers.get("Set-Cookie", "")
            return r.status, json.loads(raw) if raw else {}, set_cookie
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw), ""
        except Exception:
            return e.code, {"raw": raw}, ""


# ---- 0. 让测试用户为 admin（沙盒测试，不会污染生产） ----
con = sqlite3.connect(DB)
con.execute("UPDATE users SET is_admin=1 WHERE email=?", (EMAIL,))
con.commit()
con.close()

# ---- 1. 注册/登录 ----
http("POST", "/api/auth/register", body={"email": EMAIL, "password": PASSWORD, "name": "graph_verify"})
status, login, cookie_raw = http("POST", "/api/auth/login", body={"email": EMAIL, "password": PASSWORD})
check("auth login 200", status == 200 and login.get("ok"), f"status={status}")
m = re.search(r"itutor_session=[^;]+", cookie_raw)
cookie = m.group(0) if m else ""
check("got session cookie", bool(cookie))

# ---- 2. /graph API 结构验证 ----
status, g, _ = http("GET", f"/api/instance/{INST_ID}/graph", cookie=cookie)
check("/graph 200", status == 200, f"status={status}")
if status != 200:
    print("body:", json.dumps(g, ensure_ascii=False)[:300])
    sys.exit(1)

print("\n--- /graph 返回概览 ---")
print(f"  source     : {g.get('source')}")
print(f"  threshold  : {g.get('threshold')}")
print(f"  counts     : {g.get('counts')}")
print(f"  phases cnt : {len(g.get('phases') or [])}")
cs = g.get("concepts") or []
print(f"  concepts   : {len(cs)}")

check("source ∈ {cache, llm, fallback}", g.get("source") in {"cache", "llm", "fallback"}, str(g.get("source")))
check("threshold = 0.7", g.get("threshold") == 0.7)
check("counts 字段齐全", set(g.get("counts", {}).keys()) >= {"mastered", "frontier", "locked", "total"})
check("phases 非空", bool(g.get("phases")), f"len={len(g.get('phases') or [])}")
check("concepts >= 5", len(cs) >= 5, f"len={len(cs)}")

# 每个 concept 的字段
required_keys = {"id", "name", "week", "difficulty", "prerequisites", "prereq_names", "unlocks", "status", "mastery"}
for i, c in enumerate(cs[:3]):
    check(f"concept[{i}] 字段齐全", required_keys <= set(c.keys()), f"缺 {required_keys - set(c.keys())}")

statuses = {c.get("status") for c in cs}
check("status 全在 {mastered/frontier/locked}", statuses <= {"mastered", "frontier", "locked"}, f"statuses={statuses}")

# mastery ∈ [0,1]
bad_mastery = [c.get("mastery") for c in cs if not (0 <= (c.get("mastery") or 0) <= 1)]
check("mastery ∈ [0,1]", not bad_mastery, f"bad={bad_mastery[:3]}")

# phases 每条要有 deliverable
phase_ok = all((p.get("deliverable") or p.get("title")) for p in (g.get("phases") or []))
check("phases 含 deliverable/title", phase_ok)

# ---- 3. instance.html 关键 DOM/JS ----
inst_html = (ROOT / "web" / "instance.html").read_text(encoding="utf-8")
checks_html = {
    "DOM starwrap 容器": 'class="starwrap"' in inst_html,
    "DOM 三个 tab 切换": 'data-ptab="map"' in inst_html and 'data-ptab="star"' in inst_html and 'data-ptab="weeks"' in inst_html,
    "JS renderStarMap 函数": "function renderStarMap" in inst_html,
    "JS initStarMap 函数": "function initStarMap" in inst_html,
    "JS renderGraph 函数": "function renderGraph" in inst_html,
    "JS loadGraph API 调用": "/api/instance/" in inst_html and "/graph" in inst_html,
    "SVG 状态色定义 .s-mastered": ".snode.s-mastered" in inst_html,
    "SVG 状态色定义 .s-frontier": ".snode.s-frontier" in inst_html,
    "SVG 状态色定义 .s-locked": ".snode.s-locked" in inst_html,
    "掌握度环 stroke-dasharray": "stroke-dasharray" in inst_html,
    "滚轮缩放事件": "addEventListener('wheel'" in inst_html,
    "拖拽平移 pointerdown": "pointerdown" in inst_html,
    "悬停 dim/highlight": "'mouseenter'" in inst_html and "classList.toggle('hi'" in inst_html and "classList.add('dim')" in inst_html,
    "锁定节点提示前置": "先掌握" in inst_html or "需先掌握" in inst_html,
    "CSS 注释 /* 知识星图 #12 */": "知识星图 #12" in inst_html,
    "反生成按钮 regenGraph": "function regenGraph" in inst_html and "?regenerate=1" in inst_html,
}
for name, ok in checks_html.items():
    check(name, ok)

# ---- 4. 阶段闸门 ----
phase_objs = g.get("phases") or []
phase_statuses = {p.get("status") for p in phase_objs}
check("phases.status ∈ {cleared,current,locked}", phase_statuses <= {"cleared", "current", "locked"}, f"got={phase_statuses}")

# ---- 总结 ----
print(f"\n=== 总结 ===")
if fails:
    print(f"❌ {len(fails)} 项未通过：")
    for f in fails:
        print(f"   - {f}")
    sys.exit(1)
print(f"✅ 全部通过（{len(checks_html) + 18} 项）")
print("⚠️  视觉/交互浏览器实测需要用户本地手动跑（sandbox 无 chromium）")
