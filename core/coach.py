"""LearnBuddy 复盘 workflow（元认知复盘）。

职责：拿当前学习空间的学情聚合（analytics_summary），让 LLM 现场生成
一段"有判断力的元认知复盘"，并支持追问（多轮）。

设计见 SPEC.md ADR-007：教练是"对话"而非"报告"。
不做额外检索，输入就是已有的 analytics 数据，避免重基建。

使用：
    from core.coach import coach_reply
    text = coach_reply(domain=..., target=..., baseline=..., analytics=summary, history=[...])
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from core.llm_client import LLMClient, LLMError, get_default_client
from core.schemas import Message
from core.tracing import mark_fallback, trace_llm

PROMPTS_DIR = Path(__file__).parent / "prompts"
COACH_PROMPT_PATH = PROMPTS_DIR / "coach.md"

_SELF_LABEL = {"cant": "还不会", "with_help": "需提示", "independent": "能独立", None: "—", "": "—"}


def _load_prompt() -> str:
    if not COACH_PROMPT_PATH.exists():
        raise FileNotFoundError(f"找不到教练 prompt: {COACH_PROMPT_PATH}")
    return COACH_PROMPT_PATH.read_text(encoding="utf-8")


def format_analytics(a: dict) -> str:
    """把 analytics_summary 压成 LLM 易读的紧凑文本块。"""
    if not a or a.get("topics_studied", 0) == 0:
        return "（学习者还几乎没有学练记录——数据不足以做深度分析。）"

    lines = [
        f"- 已学知识点：{a.get('topics_studied', 0)} 个",
        f"- 客观答对率（独立作答）：{a.get('objective_pct', 0)}%",
        f"- 待复习（到期）：{len(a.get('review_queue') or [])} 个",
    ]

    weak = a.get("weak_points") or []
    if weak:
        lines.append("- 弱项（该补强）：")
        for w in weak[:5]:
            pct = "—" if w.get("objective_pct") is None else f"{w['objective_pct']}%"
            lines.append(f"    · {w.get('topic', '')}：独立答对 {pct}，自评 {w.get('self_label', '—')}")

    topics = a.get("topics") or []
    if topics:
        lines.append("- 各知识点（独立掌握 vs 协作自评）：")
        for t in topics[:12]:
            obj = "—" if t.get("objective_pct") is None else f"{t['objective_pct']}%"
            due = "（待复习）" if t.get("due") else ""
            lines.append(
                f"    · {t.get('topic', '')}：独立 {obj}，自评 {t.get('self_label', '—')}{due}"
            )

    trend = a.get("trend") or []
    answered_days = [d for d in trend if d.get("answered")]
    if answered_days:
        lines.append(f"- 近 30 天有 {len(answered_days)} 天有作答记录（柱高=当天正确率）")

    return "\n".join(lines)


def coach_reply(
    *,
    domain: str,
    target: str,
    baseline: str = "",
    analytics: Optional[dict] = None,
    history: Optional[list] = None,
    memory: str = "",
    client: Optional[LLMClient] = None,
) -> str:
    """生成教练复盘 / 回答追问。

    history：[{role: "user"|"assistant", content: str}]，为空表示首次复盘。
    任何失败返回兜底文案，不抛异常。
    """
    client = client or get_default_client()
    system = (
        _load_prompt()
        .replace("{domain}", domain or "（未知领域）")
        .replace("{target}", target or "（未明确目标）")
        .replace("{baseline}", baseline or "（未知基线）")
        .replace("{analytics}", format_analytics(analytics or {}))
        .replace("{memory}", (memory or "").strip() or "（暂无长期记忆）")
    )

    msgs = [Message(role="system", content=system)]
    for h in history or []:
        role = h.get("role")
        content = (h.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            msgs.append(Message(role=role, content=content))
    if not history:
        msgs.append(Message(role="user", content="请基于以上学情，给我这一周的学习复盘。"))

    with trace_llm(scene="coach"):
        try:
            return client.chat(msgs, temperature=0.6, max_tokens=1200).strip()
        except LLMError:
            mark_fallback()
            return "（教练复盘暂时不可用，请稍后再试。）"


__all__ = ["coach_reply", "format_analytics", "COACH_PROMPT_PATH"]
