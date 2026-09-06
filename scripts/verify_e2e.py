"""端到端验证：通过真实 HTTP + 真实 DeepSeek 跑一轮对话直到生成实例。

用法：
    ~/.local/share/itutor/.venv/bin/python scripts/verify_e2e.py

通过条件：
    - /api/chat 真实流式返回 token（真实对话）
    - 多轮后 LLM 输出 GENERATE → 后端真实生成实例（真实构建）
    - data/instances/{id}/ 下 4 件套文件真实落盘
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8000"
SESSION = "verify-e2e-001"

# 绕开公司代理（HTTP_PROXY 等会拦截 localhost 返回 403）
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# 扮演一个把信息一次性给全、强行推向生成的用户
USER_TURNS = [
    "我要在12周内考过日语N3。现在五十音会读，但语法基本零基础，听力最弱。"
    "工作日每天能学2小时，周末每天4小时。",
    "不用给我出测试题了，就按我说的判断：N5水平偏下，语法零基础。直接用标准强度。",
    "路径就选标准。我确认了，就这套，请现在生成完整学习系统。",
    "确认生成。",
    "OK 生成。",
    "就这套，生成。",
]


def post_chat(message: str):
    """POST 一轮，解析 SSE，返回 (assistant_text, generate_payload_or_None)。"""
    body = json.dumps({"session_id": SESSION, "message": message}).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE}/api/chat",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    assistant = []
    generate = None
    error = None
    with _OPENER.open(req, timeout=120) as resp:
        buf = ""
        for raw in resp:
            buf += raw.decode("utf-8")
            while "\n\n" in buf:
                evt, buf = buf.split("\n\n", 1)
                event = "message"
                data_str = ""
                for line in evt.split("\n"):
                    if line.startswith("event:"):
                        event = line[6:].strip()
                    elif line.startswith("data:"):
                        data_str = line[5:].strip()
                if not data_str:
                    continue
                data = json.loads(data_str)
                if event == "token":
                    assistant.append(data.get("token", ""))
                elif event == "generate":
                    generate = data
                elif event == "error":
                    error = data.get("error")
    return "".join(assistant), generate, error


def main() -> int:
    print("=== Itutor 端到端真实验证 ===\n")
    for i, msg in enumerate(USER_TURNS, 1):
        print(f"\n【你 · 第{i}轮】{msg}")
        try:
            text, generate, error = post_chat(msg)
        except Exception as e:  # noqa: BLE001
            print(f"❌ 请求失败：{type(e).__name__}: {e}")
            return 1

        if error:
            print(f"❌ 错误事件：{error}")
            return 1

        print(f"【教练】{text[:500]}{'…' if len(text) > 500 else ''}")

        if generate:
            inst_id = generate.get("instance_id")
            print(f"\n🎉 真实生成触发！instance_id = {inst_id}")
            inst_dir = Path("data/instances") / inst_id
            files = sorted(p.name for p in inst_dir.iterdir()) if inst_dir.exists() else []
            print(f"   落盘目录：{inst_dir}")
            print(f"   文件：{files}")
            expected = {"meta.json", "master.json", "W1.json", "学习手册.md", "愿景与契约.md"}
            missing = expected - set(files)
            if missing:
                print(f"⚠️ 缺文件：{missing}")
                return 2
            print("✅ 4 件套齐全，真实构建成功。")
            return 0

    print("\n⚠️ 跑完所有轮次仍未触发生成（LLM 还在追问）。可加轮次或调 prompt。")
    return 3


if __name__ == "__main__":
    sys.exit(main())
