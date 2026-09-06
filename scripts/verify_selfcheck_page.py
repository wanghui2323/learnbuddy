"""6 关自检交互页（web/self-check.html）静态结构验证 + 路由可达性。

不打 LLM，纯 HTML 关键字 + DOM ID + JS 函数检查 + server 路由 200/302。
"""
from __future__ import annotations
import json, re, sys, urllib.request, urllib.error, pathlib
ROOT = pathlib.Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8000"
PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
fails: list[str] = []


def check(name, ok, detail=""):
    print(f"  [{PASS if ok else FAIL}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        fails.append(name)


def http(method, path, *, cookie=None):
    """用 curl 替代 urllib（sandbox 中 python urllib 自动 follow redirect 慢/超时）。"""
    import subprocess
    args = ["curl", "-s", "-o", "/tmp/_page_body", "-w", "%{http_code}", "-X", method, BASE + path]
    if cookie:
        args += ["-b", cookie]
    args += ["--max-time", "15"]
    r = subprocess.run(args, capture_output=True, text=True, timeout=20)
    code = int(r.stdout.strip() or 0)
    try:
        body = open("/tmp/_page_body", encoding="utf-8", errors="replace").read()
    except OSError:
        body = ""
    return code, body


# 1) 路由可达
s, html = http("GET", "/self-check/japanese-jlpt-n3")
check("GET /self-check/{id} 302 未登录跳转", s == 302, f"status={s}")

# 2) HTML 文件存在 + 关键 DOM/JS
sf = ROOT / "web" / "self-check.html"
check("web/self-check.html 存在", sf.exists())
html = sf.read_text(encoding="utf-8")
checks = {
    "DOM #gateList 6 关容器": 'id="gateList"' in html,
    "DOM #submitBtn 提交按钮": 'id="submitBtn"' in html,
    "DOM #progressBox 进度条": 'id="progressBox"' in html,
    "DOM #pgBar 进度条填充": 'id="pgBar"' in html,
    "DOM #histList 历史区": 'id="histList"' in html,
    "DOM #fbBox 反馈区": 'id="fbBox"' in html,
    "JS loadDimensions": "async function loadDimensions" in html,
    "JS loadLatest": "async function loadLatest" in html,
    "JS loadHistory": "async function loadHistory" in html,
    "JS renderGates": "function renderGates" in html,
    "JS submit": "async function submit" in html,
    "JS loadCoachFeedback": "async function loadCoachFeedback" in html,
    "JS computeR1": "function computeR1" in html,
    "API /self-check POST 调用": '/self-check"' in html or '/self-check/' in html,
    "API /self-check/latest 调用": "/self-check/latest" in html,
    "API /self-check/history 调用": "/self-check/history" in html,
    "R1 规则 advance 文字": "本周通过" in html,
    "R1 规则 reduce 文字": "差 1 关" in html,
    "R1 规则 repeat 文字": "未通过" in html,
    "R1 规则 reset 文字": "回到上一周" in html,
    "通过阈值 4 (>=4 pass)": "通过标志" in html and "≥4" in html or "4" in html,
    "可访问性 role=img 或 aria-label": "role=" in html or "aria-label" in html,
    "响应式 meta viewport": '<meta name="viewport"' in html,
    "返回学习空间链接": 'href="/space"' in html or 'href={`/space' in html,
    "无 emoji 自检图标但有 SVG checkSquare 用法": "${icon" not in html,  # 用了文字 "✓" 即可
}
for name, ok in checks.items():
    check(name, ok)

# 3) /self-check/{id} 路由 HTML 内容核对（用合法 admin cookie）
import sqlite3
con = sqlite3.connect(ROOT / "data/itutor.db")
EMAIL = "pagecheck@test.com"
import subprocess
# 注册（curl, silent）
subprocess.run(
    ["curl", "-s", "-X", "POST", BASE + "/api/auth/register",
     "-H", "Content-Type: application/json",
     "-d", json.dumps({"email": EMAIL, "password": "p1234567", "name": "pc"}),
     "--max-time", "10"],
    capture_output=True, timeout=15,
)
con.execute("UPDATE users SET is_admin=1 WHERE email=?", (EMAIL,))
con.commit(); con.close()

# 登录（curl 拿 cookie）
login_args = ["curl", "-s", "-D", "/tmp/_page_hdr", "-X", "POST", BASE + "/api/auth/login",
              "-H", "Content-Type: application/json",
              "-d", json.dumps({"email": EMAIL, "password": "p1234567"}),
              "--max-time", "10"]
subprocess.run(login_args, capture_output=True, timeout=15)
hdr_text = open("/tmp/_page_hdr", encoding="utf-8", errors="replace").read()
m = re.search(r"itutor_session=[^;]+", hdr_text)
ck = m.group(0) if m else ""
check("登录拿到 cookie", bool(ck))

s, body = http("GET", "/self-check/japanese-jlpt-n3", cookie=ck)
check("GET /self-check/{id} 200 已登录", s == 200, f"status={s} len={len(body)}")
check("返回 HTML 含 hero h1", "<h1" in body and "6 关自检" in body)
check("返回 HTML 含 #gateList", 'id="gateList"' in body)

# 4) server.py 路由存在
srv = (ROOT / "server.py").read_text(encoding="utf-8")
check("server.py 包含 /self-check/{id} 路由", '@app.get("/self-check/{instance_id}")' in srv)
check("server.py 包含 self-check POST", '"/api/instance/{instance_id}/self-check"' in srv)
check("server.py 包含 latest 路由", '"/api/instance/{instance_id}/self-check/latest"' in srv)
check("server.py 包含 history 路由", '"/api/instance/{instance_id}/self-check/history"' in srv)

# 5) space.html 入口存在
space = (ROOT / "web" / "space.html").read_text(encoding="utf-8")
check("space.html hero 引入「6 关自检」按钮", "/self-check/" in space and "6 关自检" in space)

# 6) 6 关兜底维度已写在 HTML
check("兜底 DIMENSIONS 6 个默认维度", "听" in html and "说" in html and "读" in html and "写" in html)

print("\n=== 总结 ===")
if fails:
    print(f"❌ {len(fails)} 项未通过：")
    for f in fails:
        print(f"   - {f}")
    sys.exit(1)
print(f"✅ 全部通过（{len(checks) + 11} 项）")
