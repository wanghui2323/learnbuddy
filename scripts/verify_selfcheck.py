"""6 关自检端到端验证：API + 数据完整性。

不打 LLM；用 alice (uid 10) + japanese-jlpt-n3 演示实例。
"""
from __future__ import annotations
import json, re, sqlite3, sys, urllib.request, urllib.error, pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BASE = "http://127.0.0.1:8000"
INST_ID = "japanese-jlpt-n3"
EMAIL = "selfcheck_verify@test.com"
PASSWORD = "scverify123"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
fails: list[str] = []


def http(method, path, *, cookie=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json", **({"Cookie": cookie} if cookie else {})},
    )
    try:
        with OPENER.open(req, timeout=30) as r:
            raw = r.read().decode()
            return r.status, json.loads(raw) if raw else {}, r.headers.get("Set-Cookie", "")
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw), ""
        except Exception:
            return e.code, {"raw": raw}, ""


def check(name, ok, detail=""):
    print(f"  [{PASS if ok else FAIL}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        fails.append(name)


# 提为 admin 沙盒测试
con = sqlite3.connect(ROOT / "data/itutor.db")
con.execute("UPDATE users SET is_admin=1 WHERE email=?", (EMAIL,))
con.commit()
con.close()

http("POST", "/api/auth/register", body={"email": EMAIL, "password": PASSWORD, "name": "sc"})
con = sqlite3.connect(ROOT / "data/itutor.db")
con.execute("UPDATE users SET is_admin=1 WHERE email=?", (EMAIL,))
con.commit()
con.close()
s, login, sc = http("POST", "/api/auth/login", body={"email": EMAIL, "password": PASSWORD})
check("login 200", s == 200 and login.get("ok"))
m = re.search(r"itutor_session=[^;]+", sc)
cookie = m.group(0) if m else ""

# 清空旧测试数据（仅自己）
con = sqlite3.connect(ROOT / "data/itutor.db")
con.execute("DELETE FROM weekly_self_checks WHERE user_id=(SELECT id FROM users WHERE email=?)", (EMAIL,))
con.commit()
con.close()

# 1) GET latest (空)
s, j, _ = http("GET", f"/api/instance/{INST_ID}/self-check/latest", cookie=cookie)
check("GET latest 200", s == 200)
check("latest == null (空)", j.get("latest") is None)

# 2) POST 提交一次
s, sub, _ = http("POST", f"/api/instance/{INST_ID}/self-check", cookie=cookie,
    body={"week": 1, "scores": [5, 4, 3, 5, 4, 2], "notes": ["n1","n2","n3","n4","n5","n6"]})
check("POST 200", s == 200, f"status={s} body={sub}")
check("passed_cnt=4", sub.get("passed_cnt") == 4)
check("passed=True", sub.get("passed") is True)
check("r1_rule.code=advance", sub.get("r1_rule", {}).get("code") == "advance")

# 3) GET latest (有数据)
s, j, _ = http("GET", f"/api/instance/{INST_ID}/self-check/latest", cookie=cookie)
check("latest 200", s == 200)
latest = j.get("latest") or {}
check("latest.week=1", latest.get("week") == 1)
check("latest.passed=True", latest.get("passed") is True)
items = latest.get("items") or []
check("items len=6", len(items) == 6)
if items:
    check("item[0].name 非空", bool(items[0].get("name")))
    check("item[0].score ∈ [0,5]", 0 <= items[0].get("score", -1) <= 5)

# 4) 覆盖提交（同一 week）
s, sub2, _ = http("POST", f"/api/instance/{INST_ID}/self-check", cookie=cookie,
    body={"week": 1, "scores": [1, 1, 1, 1, 1, 1]})
check("POST override 200", s == 200)
check("override passed_cnt=0", sub2.get("passed_cnt") == 0)
check("override passed=False", sub2.get("passed") is False)
check("override r1=reset", sub2.get("r1_rule", {}).get("code") == "reset")

# 5) 多个 week 历史
for w in range(2, 5):
    http("POST", f"/api/instance/{INST_ID}/self-check", cookie=cookie,
         body={"week": w, "scores": [3, 3, 3, 3, 3, 3]})
s, j, _ = http("GET", f"/api/instance/{INST_ID}/self-check/history?limit=10", cookie=cookie)
check("history 200", s == 200)
hist = j.get("items") or []
check("history count=4", j.get("count") == 4, f"got={j.get('count')}")
weeks = [it.get("week") for it in hist]
check("history desc by week", weeks == sorted(weeks, reverse=True), f"weeks={weeks}")

# 6) 错误处理
s, j, _ = http("POST", f"/api/instance/{INST_ID}/self-check", cookie=cookie,
    body={"week": 0, "scores": [1, 2, 3]})  # 长度 3
check("reject scores_len!=6", s == 400, f"status={s}")

s, j, _ = http("POST", f"/api/instance/{INST_ID}/self-check", cookie=cookie,
    body={"week": 1, "scores": [6, 0, 0, 0, 0, 0]})  # 越界
check("reject score>5", s == 400, f"status={s}")

# 7) 401 未登录
s, _, _ = http("POST", f"/api/instance/{INST_ID}/self-check", body={"week": 1, "scores": [0]*6})
check("401 未登录", s == 401)

# 8) R1 规则覆盖所有档位
from core.self_check import _r1_rule
check("R1 advance", _r1_rule(6)["code"] == "advance")
check("R1 advance(4)", _r1_rule(4)["code"] == "advance")
check("R1 reduce(3)", _r1_rule(3)["code"] == "reduce")
check("R1 repeat(2)", _r1_rule(2)["code"] == "repeat")
check("R1 repeat(1)", _r1_rule(1)["code"] == "repeat")
check("R1 reset(0)", _r1_rule(0)["code"] == "reset")

print("\n=== 总结 ===")
if fails:
    print(f"❌ {len(fails)} 项未通过：")
    for f in fails:
        print(f"   - {f}")
    sys.exit(1)
print(f"✅ 全部通过（{len(fails) + 23} 项）")
