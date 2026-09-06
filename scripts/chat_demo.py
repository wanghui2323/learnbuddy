"""Itutor 对话引擎 CLI 真实联调 demo。

跑法：
    .venv/bin/python scripts/chat_demo.py

需要 .env 中 DEEPSEEK_API_KEY 已填。
对话过程中：
    - 输入消息后按 Enter 发送
    - 输入 :q 或 :quit 退出
    - 输入 :r 或 :reset 清空对话重来
    - 输入 :state 查看当前对话状态

LLM 输出 GENERATE 标记后会自动展示解析出的参数包并退出。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.engine import ConversationEngine, domain_to_slug, opening_greeting  # noqa: E402
from core.llm_client import LLMConfigError, LLMError  # noqa: E402
from core.schemas import UserParams  # noqa: E402


def _print_banner() -> None:
    print("=" * 60)
    print("  Itutor · CLI 对话 demo")
    print("  Day 2 · ConversationEngine 真实联调")
    print("=" * 60)
    print()
    print(opening_greeting())
    print()
    print("-- 提示 --")
    print("  发送：直接输入文字按 Enter")
    print("  退出：输入 :q 或 :quit")
    print("  重来：输入 :r 或 :reset")
    print("  查看状态：输入 :state")
    print()


def _print_params_summary(params: UserParams) -> None:
    print()
    print("=" * 60)
    print("  ✅ GENERATE 触发，已解析完整参数包")
    print("=" * 60)
    print(f"  domain               = {params.domain}")
    print(f"  domain_category      = {params.domain_category.value}")
    print(f"  target               = {params.target}")
    print(f"  weeks                = {params.weeks}")
    print(f"  intensity            = {params.intensity.value}")
    print(
        f"  workday/weekend hours = {params.weekday_hours} / {params.weekend_hours}"
        f" → 周总投入 {params.weekly_total_hours}h"
    )
    print(f"  baseline             = {params.baseline_summary}")
    print(f"  preferences          = {params.preferences[:80]}")
    print(f"  milestones           = {len(params.milestones)} 项")
    for m in params.milestones:
        print(f"    M{m.month}: {m.title} → {m.deliverable}")
    print(f"  weekly_themes        = {len(params.weekly_themes)} 项（W1-W{params.weeks}）")
    if params.weekly_themes:
        first = params.weekly_themes[0]
        print(f"    W{first.week}: {first.title}")
    print(f"  learning_methods")
    print(f"    core_method        = {params.learning_methods.core_method}")
    print(f"    anti_forgetting    = {len(params.learning_methods.anti_forgetting)} 项")
    print(f"    self_check_dims    = {len(params.learning_methods.self_check_dimensions)} 项")
    print(f"  → 推 instance slug   = {domain_to_slug(params.domain)}")
    print("=" * 60)
    print()
    print("⚙️ Day 3 的 generator 将以这个参数包为输入，输出 master.json + W1.json")
    print("   + 学习手册.md + 愿景与契约.md 共 4 件套到 data/instances/{slug}/")


async def main() -> int:
    _print_banner()

    try:
        engine = ConversationEngine()
    except LLMConfigError as e:
        print(f"❌ {e}")
        return 1

    while True:
        try:
            user_input = input("你 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\n再见！")
            return 0

        if not user_input:
            continue

        if user_input in (":q", ":quit", "exit", "退出"):
            print("再见！")
            return 0

        if user_input in (":r", ":reset", "重来"):
            engine.reset()
            print("[已清空对话]\n")
            continue

        if user_input in (":state",):
            print(f"[当前轮数：{engine.turn_count}，history 共 {len(engine.history)} 条消息]\n")
            continue

        print("\n教练 > ", end="", flush=True)
        try:
            async for token in engine.respond_stream(user_input):
                print(token, end="", flush=True)
        except LLMError as e:
            print(f"\n❌ LLM 调用失败：{e}")
            continue
        print("\n")

        params = engine.try_extract_params()
        if params is not None:
            _print_params_summary(params)
            return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
