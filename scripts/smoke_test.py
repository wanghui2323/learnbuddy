"""LLM 连通性 smoke test。

用法：
    1. cp .env.example .env
    2. 编辑 .env 填入真实 DEEPSEEK_API_KEY
    3. python scripts/smoke_test.py

通过条件：
    - ✅ 同步调用拿到非空回复
    - ✅ 流式调用至少 yield 1 个非空 token

任一失败：检查 .env 配置、网络、API key 有效性。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# 让 scripts/ 直接 python 跑时也能 import core
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.llm_client import LLMClient, LLMConfigError, LLMError  # noqa: E402
from core.schemas import Message  # noqa: E402


SMOKE_PROMPT = "你好。请用一句话介绍你自己（不超过 20 字）。"


def _print_section(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print("=" * 60)


def run_sync_test(client: LLMClient) -> bool:
    _print_section("Test 1 · 同步 chat")
    try:
        text = client.chat(
            [Message(role="user", content=SMOKE_PROMPT)],
            temperature=0.7,
            max_tokens=200,
        )
    except LLMError as e:
        print(f"❌ 同步调用失败：{e}")
        return False

    if not text.strip():
        print("❌ 同步调用返回空文本")
        return False

    print(f"✅ 同步调用成功（{len(text)} 字符）")
    print(f"   model = {client.model}")
    print(f"   reply = {text.strip()}")
    return True


async def run_stream_test(client: LLMClient) -> bool:
    _print_section("Test 2 · 异步流式 chat")
    try:
        chunks: list[str] = []
        async for token in client.chat_stream(
            [Message(role="user", content=SMOKE_PROMPT)],
            temperature=0.7,
            max_tokens=200,
        ):
            chunks.append(token)
            print(token, end="", flush=True)
        print()
    except LLMError as e:
        print(f"❌ 流式调用失败：{e}")
        return False

    full = "".join(chunks)
    if not full.strip():
        print("❌ 流式调用没收到任何 token")
        return False

    print(f"\n✅ 流式调用成功（{len(chunks)} 块 / {len(full)} 字符）")
    return True


async def main() -> int:
    print("Itutor 学习系统 · LLM Smoke Test")
    print(f"项目根：{PROJECT_ROOT}")

    try:
        client = LLMClient()
    except LLMConfigError as e:
        print(f"\n❌ 客户端初始化失败：{e}")
        print("\n请确认：")
        print("  1. 已 cp .env.example .env")
        print("  2. .env 中 DEEPSEEK_API_KEY 不是 your_deepseek_api_key_here")
        return 1

    print(f"\n✅ 客户端就绪：base_url={client.base_url} / model={client.model}")

    sync_ok = run_sync_test(client)
    stream_ok = await run_stream_test(client)

    _print_section("结果")
    print(f"  同步 chat: {'✅' if sync_ok else '❌'}")
    print(f"  流式 chat: {'✅' if stream_ok else '❌'}")

    if sync_ok and stream_ok:
        print("\n🎉 Day 1 LLM 基础设施 smoke test 全绿，可以进 Day 2。")
        return 0
    else:
        print("\n⚠️ 有失败项，请按上面错误信息定位。")
        return 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
