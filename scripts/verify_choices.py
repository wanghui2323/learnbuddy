"""验证对话其余阶段会用 <<<CHOICES>>> 点选块（时间预算 / 路径三选一 等）。"""
from __future__ import annotations
import json, sys, time, urllib.request

BASE = "http://127.0.0.1:8000"
SESSION = "verify-choices-" + str(time.time())
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

TURNS = [
    "我想从零学 Python 数据分析，目标是独立做部门月度报表。目标确认，别再追问目标了。",
    "直接出题测我水平，我点选。",
    "我的摸底选择：q1=完全不会；q2=不知道从哪入手；q3=饼图或环形图",
    "好了，继续下一步吧。",
    "继续。",
]

def post(msg):
    body = json.dumps({"session_id": SESSION, "message": msg}).encode()
    req = urllib.request.Request(f"{BASE}/api/chat", data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    out = []
    with OPENER.open(req, timeout=120) as resp:
        buf = ""
        for raw in resp:
            buf += raw.decode("utf-8")
            while "\n\n" in buf:
                evt, buf = buf.split("\n\n", 1)
                ev, ds = "message", ""
                for line in evt.split("\n"):
                    if line.startswith("event:"): ev = line[6:].strip()
                    elif line.startswith("data:"): ds = line[5:].strip()
                if ds and ev == "token":
                    out.append(json.loads(ds).get("token", ""))
    return "".join(out)

for i, m in enumerate(TURNS, 1):
    print(f"\n【你】{m}")
    txt = post(m)
    tag = "CHOICES" if "<<<CHOICES>>>" in txt else ("QUIZ" if "<<<QUIZ>>>" in txt else "")
    print(f"【教练{('·'+tag) if tag else ''}】{txt[:240]}{'…' if len(txt) > 240 else ''}")
    if "<<<CHOICES>>>" in txt:
        raw = txt.split("<<<CHOICES>>>")[1].split("<<<END>>>")[0].split("```json")[1].split("```")[0].strip()
        d = json.loads(raw)
        print(f"\n✅ 检测到 CHOICES！问题={d.get('question','')[:40]} | multi={d.get('multi')} | 选项={d['options']}")
        sys.exit(0)

print("\n⚠️ 跑完未见 CHOICES（可加轮次）")
sys.exit(3)
