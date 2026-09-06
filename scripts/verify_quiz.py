"""验证基线评估改成点选式 QUIZ 块后，LLM 真的会输出 <<<QUIZ>>>。"""
from __future__ import annotations
import json, sys, urllib.request

BASE = "http://127.0.0.1:8000"
SESSION = "verify-quiz-" + str(__import__("time").time())
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

TURNS = [
    "我想从零开始学 Python 数据分析，目标是能独立做出部门月度数据报表。",
    "目标就这个，确认。现在请直接出题测测我的水平。",
    "就按零基础判断也行，但你说要测，那就出题吧，我点选。",
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
    has_quiz = "<<<QUIZ>>>" in txt
    print(f"【教练】{txt[:280]}{'…' if len(txt) > 280 else ''}")
    if has_quiz:
        block = txt.split("<<<QUIZ>>>")[1].split("<<<END>>>")[0]
        raw = block.split("```json")[1].split("```")[0].strip()
        data = json.loads(raw)
        print(f"\n✅ 检测到 QUIZ 块！题数={len(data['questions'])}")
        for q in data["questions"]:
            print(f"   - [{q.get('dimension','?')}] {q['prompt'][:40]} | 选项={q['options']}")
        sys.exit(0)

print("\n⚠️ 跑完未见 QUIZ 块（可能 LLM 还在追问目标）")
sys.exit(3)
