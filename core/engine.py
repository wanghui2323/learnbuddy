"""LearnBuddy 对话引擎。

职责：
- 加载 system prompt（从 core/prompts/system.md）
- 维护一次对话的消息历史
- 提供同步 / 异步流式两种响应入口
- 实时检测 LLM 输出中的 <<<GENERATE>>>...<<<END>>> 标记
- 把 GENERATE 块的 JSON 解析为 UserParams（强类型）

不做：
- LLM 调用细节（委托给 core.llm_client）
- 实例生成（Day 3 的 core.generator 做）
- 持久化（Day 4 的 server.py 路由做）
- 评估题生成（独立成 core.evaluator）

使用方式：

    from core.engine import ConversationEngine, opening_greeting

    engine = ConversationEngine()
    print(opening_greeting())

    while True:
        user_input = input("你：")
        text = engine.respond(user_input)
        print(f"LearnBuddy：{text}\\n")
        params = engine.try_extract_params()
        if params:
            print("已收到完整参数包，下一步交给 generator")
            break
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from typing import AsyncIterator, Iterable, Optional

from pydantic import ValidationError

from core.llm_client import LLMClient, LLMNetworkError, get_default_client
from core.schemas import Message, UserParams


# ============================================================================
# 路径与 prompt 加载
# ============================================================================


PROMPTS_DIR = Path(__file__).parent / "prompts"
SYSTEM_PROMPT_PATH = PROMPTS_DIR / "system.md"


def _load_system_prompt() -> str:
    """从 core/prompts/system.md 读 system prompt。"""
    if not SYSTEM_PROMPT_PATH.exists():
        raise FileNotFoundError(
            f"找不到 system prompt: {SYSTEM_PROMPT_PATH}。"
            "确认 core/prompts/system.md 存在。"
        )
    return SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")


# ============================================================================
# 暖场问候（首次进入对话页时由前端展示一次，不计入 history）
# ============================================================================


OPENING_GREETING = """你好。我是 LearnBuddy。

说说你最近想学什么、想成为什么样的人？
不限领域——英语、日语、编程、营养、写作、考研、设计……都行。
10–30 分钟的对话，我会为你定制一条量身的 4–24 周学习路径。"""


def opening_greeting() -> str:
    return OPENING_GREETING


# ============================================================================
# GENERATE 标记解析
# ============================================================================


GENERATE_MARKER_PATTERN = re.compile(
    r"<<<GENERATE>>>\s*```json\s*(.+?)\s*```\s*<<<END>>>",
    re.DOTALL,
)


def has_generate_signal(text: str) -> bool:
    """快速检测（不解析），用于流式输出时边写边判断。"""
    return "<<<GENERATE>>>" in text and "<<<END>>>" in text


def extract_generate_params(text: str) -> Optional[UserParams]:
    """从 LLM 输出中提取 GENERATE 标记块并解析为 UserParams。

    解析失败的所有情况都返回 None，由调用方决定如何告知用户：
    - 没找到标记
    - JSON 损坏
    - JSON 缺必要字段
    - JSON 字段类型错误（如 weeks 不是数字）
    - 任何 pydantic 校验失败（如 weeks 超出 4-24）
    """
    if not has_generate_signal(text):
        return None

    match = GENERATE_MARKER_PATTERN.search(text)
    if not match:
        return None

    raw_json = match.group(1).strip()
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError:
        return None

    try:
        return UserParams.model_validate(data)
    except ValidationError:
        return None


# ============================================================================
# Slug 生成（中文领域 → 英文 slug 用作 instance id）
# ============================================================================


_SLUG_REPLACEMENTS = {
    # 语言
    "英语": "english",
    "日语": "japanese",
    "韩语": "korean",
    "法语": "french",
    "德语": "german",
    "西班牙语": "spanish",
    "雅思": "ielts",
    "托福": "toefl",
    "考研": "kaoyan",
    "GRE": "gre",
    "JLPT": "jlpt",
    "CEFR": "cefr",
    # 编程 / 技术
    "编程": "programming",
    "前端": "frontend",
    "后端": "backend",
    "全栈": "fullstack",
    "AI": "ai",
    "机器学习": "ml",
    "深度学习": "dl",
    "数据分析": "data-analysis",
    "数据科学": "data-science",
    # 学科
    "化学": "chemistry",
    "物理": "physics",
    "数学": "math",
    "生物": "biology",
    "营养": "nutrition",
    "心理": "psychology",
    "经济学": "economics",
    "法律": "law",
    "金融": "finance",
    # 软技能
    "设计": "design",
    "写作": "writing",
    "产品": "product",
    "运营": "operations",
    "营销": "marketing",
    "演讲": "speaking",
    "管理": "management",
}


def domain_to_slug(domain: str) -> str:
    """从中文 domain 推 slug 用作 instance id。

    规则：
    1. 词典替换中文关键词为英文（按 key 长度降序，避免短词先替换吞掉长词的一部分）
    2. 替换时两侧加空格隔离，避免连续 2 个中文词替换后粘连
       （如「英语雅思」不应变成「englishielts」，应是「english-ielts」）
    3. 全部转小写
    4. 非 [a-z0-9-] 字符替换为 -
    5. 折叠多余 - 并去首尾 -
    6. 限定 2-40 字符；为空或太短回退为 "instance"
    """
    s = domain.strip()
    # 按 key 长度降序：先替换更长更具体的词（如先「数据分析」再「数据」）
    sorted_items = sorted(_SLUG_REPLACEMENTS.items(), key=lambda kv: -len(kv[0]))
    for cn, en in sorted_items:
        s = s.replace(cn, f" {en} ")
    s = s.lower()
    s = re.sub(r"[^a-z0-9-]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    if not s or len(s) < 2:
        s = "instance"
    return s[:40]


# ============================================================================
# ConversationEngine
# ============================================================================


class ConversationEngine:
    """一次完整对话的状态机。

    生命周期：
        engine = ConversationEngine()
        engine.respond(user_msg)               # 同步轮 1
        engine.respond(user_msg)               # 同步轮 2
        ...
        async for token in engine.respond_stream(user_msg):  # 流式版
            ...
        params = engine.try_extract_params()   # 检查是否到达生成阶段

    线程/协程安全说明：
        单实例不共享，每个用户/会话独立 new 一个。Day 4 server.py 会按
        instance_id 维护 engine 实例池。
    """

    def __init__(
        self,
        client: Optional[LLMClient] = None,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = 2000,
    ):
        self.client = client or get_default_client()
        self.system_prompt = system_prompt or _load_system_prompt()
        self.temperature = temperature
        self.max_tokens = max_tokens

        # 持久消息历史（不含 system prompt，每次发起 LLM 调用时拼上）
        self.history: list[Message] = []
        # 最近一次助手输出（用于 GENERATE 检测，避免每次扫全量历史）
        self._last_assistant_text: str = ""

    # ------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------

    @property
    def turn_count(self) -> int:
        """已完成的对话轮数（user+assistant 各算 1 条，每完整轮 = 2 条）。"""
        return len(self.history) // 2

    def reset(self) -> None:
        """清空历史，重新开始。"""
        self.history = []
        self._last_assistant_text = ""

    def restore(self, messages: Iterable[Message | dict], *, max_messages: int = 60) -> None:
        """从持久事实消息恢复上下文，不触发 LLM 调用。

        只接受 user/assistant，丢弃可能存在的 system/损坏记录，并从最近一条
        user 消息开始，避免截断窗口以孤立 assistant 开头。
        """
        restored: list[Message] = []
        for raw in messages:
            try:
                message = raw if isinstance(raw, Message) else Message.model_validate(raw)
            except (TypeError, ValidationError):
                continue
            if message.role in {"user", "assistant"} and message.content.strip():
                restored.append(message)
        restored = restored[-max(1, min(120, int(max_messages or 60))):]
        while restored and restored[0].role != "user":
            restored.pop(0)
        self.history = restored
        self._last_assistant_text = next(
            (message.content for message in reversed(restored) if message.role == "assistant"),
            "",
        )

    def respond(self, user_message: str) -> str:
        """同步一轮：把 user_message 加入 history，调 LLM，返回完整回复。

        用于 CLI 自测、单元测试。生产 SSE 走 `respond_stream`。
        """
        self._append_user(user_message)
        text = self.client.chat(
            self._build_messages_for_llm(),
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        self._append_assistant(text)
        return text

    async def respond_stream(
        self,
        user_message: str,
        *,
        extra_system_context: str = "",
    ) -> AsyncIterator[str]:
        """异步流式一轮：把 user_message 加入 history，调 LLM，逐 token yield。

        流式结束后 self._last_assistant_text 是完整回复，可立即调
        try_extract_params() 检查是否到达 GENERATE 阶段。
        """
        self._append_user(user_message)
        chunks: list[str] = []
        timeout = float(os.getenv("ITUTOR_CHAT_STREAM_TIMEOUT", "90"))
        try:
            async with asyncio.timeout(timeout):
                async for token in self.client.chat_stream(
                    self._build_messages_for_llm(extra_system_context=extra_system_context),
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                ):
                    chunks.append(token)
                    yield token
        except TimeoutError as e:
            raise LLMNetworkError(
                f"对话生成超时（>{int(timeout)} 秒）。请重试，或把需求拆小一点再生成。"
            ) from e
        finally:
            full = "".join(chunks)
            if full:
                self._append_assistant(full)

    def try_extract_params(self) -> Optional[UserParams]:
        """尝试从最近一次助手输出中解析 GENERATE 参数包。

        返回：
            - UserParams：解析成功 → 调用方应跳到 generator 阶段
            - None：还没到生成阶段或解析失败 → 继续对话
        """
        if not self._last_assistant_text:
            return None
        return extract_generate_params(self._last_assistant_text)

    def record_exchange(self, user_message: str, assistant_message: str) -> None:
        """记录一轮非 LLM 生成的对话，用于前置澄清等系统消息。"""
        self._append_user(user_message)
        self._append_assistant(assistant_message)

    # ------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------

    def _append_user(self, content: str) -> None:
        self.history.append(Message(role="user", content=content))

    def _append_assistant(self, content: str) -> None:
        self.history.append(Message(role="assistant", content=content))
        self._last_assistant_text = content

    def _build_messages_for_llm(self, *, extra_system_context: str = "") -> Iterable[Message]:
        """拼出送给 LLM 的完整消息列表：system prompt + history。"""
        messages = [Message(role="system", content=self.system_prompt)]
        extra_system_context = (extra_system_context or "").strip()
        if extra_system_context:
            messages.append(Message(role="system", content=extra_system_context))
        return [*messages, *self.history]


__all__ = [
    "OPENING_GREETING",
    "opening_greeting",
    "has_generate_signal",
    "extract_generate_params",
    "domain_to_slug",
    "ConversationEngine",
    "PROMPTS_DIR",
    "SYSTEM_PROMPT_PATH",
]
