"""统一工具注册层（Batch L · ADR-011 地基）。

把 LearnBuddy 的核心能力收敛成一组**带类型与描述的工具**，作为公共底座同时喂给：
- FastAPI 端点（透明列出 / 未来委托）
- MCP server（mcp_server.py → OpenClaw / Claude Desktop 等 agent）
- 未来的动态工作流 / 真 agent

设计：每个工具是「确定性入口 + 显式 user_id」，不依赖 HTTP Request；返回 JSON-able dict。
工具内部复用 core 既有引擎（learning / rag / concepts / mastery / content / coach），
不重复造逻辑。组合型（graph / lesson+RAG）在此薄封装一层。

注意：本层是**新增的能力底座**，不改动现有 server.py 端点行为；后续可让端点委托到这里。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Optional

from core import learning, rag
from core.coach import coach_reply
from core.concepts import (
    MASTERY_THRESHOLD,
    build_unlocks,
    concept_status,
    load_or_generate,
)
from core.content import ensure_lesson_cards, generate_lesson_reviewed
from core.generator import DEFAULT_INSTANCES_DIR
from core.mastery import concept_mastery, mastered_set
from core.source_search import build_source_pack, format_sources_context

INSTANCES_DIR = DEFAULT_INSTANCES_DIR


# ----------------------------- 通用辅助 -----------------------------

class ToolError(Exception):
    """工具执行错误（参数非法 / 无权访问 / 学习路径不存在）。"""


def _inst_dir(instance_id: str) -> Path:
    d = INSTANCES_DIR / instance_id
    if not d.is_dir():
        raise ToolError(f"实例 {instance_id} 不存在")
    return d


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _instance_owner(instance_id: str) -> Optional[int]:
    meta = _read_json(INSTANCES_DIR / instance_id / "meta.json")
    uid = meta.get("user_id")
    return uid if isinstance(uid, int) else None


def _assert_access(user_id: int, instance_id: str) -> None:
    """Agent 工具只允许访问明确归属当前用户的学习路径。"""
    owner = _instance_owner(instance_id)
    if owner != user_id:
        raise ToolError("无权访问该学习路径")


def _instance_context(instance_id: str) -> dict:
    inst = _inst_dir(instance_id)
    meta = _read_json(inst / "meta.json")
    master = _read_json(inst / "master.json")
    north = master.get("north_star", {})
    return {
        "inst": inst,
        "master": master,
        "domain": meta.get("domain") or north.get("domain") or "学习",
        "target": meta.get("target") or north.get("target") or "",
        "baseline": north.get("baseline_summary") or "",
    }


def _current_week(master: dict) -> int:
    ns = master.get("north_star") or {}
    weeks = ns.get("weeks") or len(master.get("weeks") or []) or 1
    sd = (master.get("schedule") or {}).get("start_date")
    if not sd:
        return 1
    try:
        start = date.fromisoformat(sd)
    except ValueError:
        return 1
    diff = (date.today() - start).days
    if diff < 0:
        return 1
    return min(weeks, diff // 7 + 1)


# ----------------------------- 工具 handlers -----------------------------

def t_list_instances(*, user_id: int) -> dict:
    index = _read_json(INSTANCES_DIR / "_index.json")
    mine = [
        {k: e.get(k) for k in ("id", "domain", "target", "weeks", "status", "domain_category")}
        for e in index.get("instances", [])
        if e.get("user_id") == user_id
    ]
    return {"count": len(mine), "instances": mine}


def t_get_today_plan(*, user_id: int, instance_id: str) -> dict:
    _assert_access(user_id, instance_id)
    ctx = _instance_context(instance_id)
    master = ctx["master"]
    w = _current_week(master)
    weeks = master.get("weeks") or []
    theme = next((x.get("title") for x in weeks if x.get("week") == w), ctx["domain"])
    w1 = _read_json(ctx["inst"] / "W1.json")
    days = w1.get("days") or []
    today_idx = ((datetime.now().weekday()) % 7) + 1  # 1=Mon..7=Sun
    today = next((d for d in days if d.get("day") == today_idx), days[0] if days else {})
    slots = today.get("slots") or []
    return {
        "week": w,
        "total_weeks": (master.get("north_star") or {}).get("weeks") or len(weeks),
        "theme": theme,
        "slots": [{"time": s.get("time"), "task": s.get("task"), "output": s.get("output")} for s in slots],
    }


def t_generate_lesson(*, user_id: int, instance_id: str, topic: str = "", custom_instruction: str = "") -> dict:
    _assert_access(user_id, instance_id)
    ctx = _instance_context(instance_id)
    topic = (topic or "").strip() or f"{ctx['domain']} 入门第一课"
    # 来源层：用户知识库 RAG + 可选联网来源搜索
    context_parts, sources = [], []
    res = rag.search(user_id, instance_id, topic, 4)
    if res.get("hits"):
        parts = []
        for i, h in enumerate(res["hits"], 1):
            parts.append(f"[来源{i}]《{h['source_title']}》\n{h['text']}")
            sources.append({"n": i, "title": h["source_title"], "kind": "kb"})
        context_parts.append("\n\n".join(parts))
    source_pack = build_source_pack(" ".join(x for x in (ctx["domain"], ctx["target"], topic) if x), count=4)
    web_sources = source_pack.get("sources") or []
    if web_sources:
        start = len(sources) + 1
        context_parts.append(format_sources_context(web_sources, start=start))
        for n, src in enumerate(web_sources, start):
            sources.append({
                "n": n,
                "title": src.get("title", ""),
                "url": src.get("url", ""),
                "site": src.get("site", ""),
                "kind": src.get("kind", "web"),
                "date_published": src.get("date_published", ""),
            })
    context = "\n\n".join(x for x in context_parts if x)
    lesson = generate_lesson_reviewed(
        domain=ctx["domain"], target=ctx["target"], topic=topic,
        baseline=ctx["baseline"], context=context,
        custom_instruction=(custom_instruction or "").strip(),
    )
    ensure_lesson_cards(lesson)
    if sources:
        lesson["_sources"] = sources
    lesson["_source_pack"] = source_pack
    return lesson


def t_record_answer(
    *, user_id: int, instance_id: str, topic: str,
    answers: Optional[list] = None, self_rating: Optional[str] = None,
    week: Optional[int] = None,
) -> dict:
    _assert_access(user_id, instance_id)
    if not (topic or "").strip():
        raise ToolError("topic 不能为空")
    res = learning.record_lesson_result(
        user_id=user_id, instance_id=instance_id, topic=topic.strip(),
        week=week, answers=answers or [], self_rating=self_rating,
    )
    _refresh_memory_safe(user_id, instance_id)  # 学练完成 → 刷新派生记忆
    return res


def _refresh_memory_safe(user_id: int, instance_id: str) -> None:
    """刷新派生记忆，失败静默（绝不影响主流程）。"""
    try:
        from core.memory import refresh_derived
        refresh_derived(user_id, instance_id)
    except Exception:  # noqa: BLE001
        pass


def t_get_analytics(*, user_id: int, instance_id: str) -> dict:
    _assert_access(user_id, instance_id)
    _inst_dir(instance_id)
    return learning.analytics_summary(user_id, instance_id)


def t_get_graph(*, user_id: int, instance_id: str) -> dict:
    _assert_access(user_id, instance_id)
    ctx = _instance_context(instance_id)
    graph = load_or_generate(ctx["inst"], ctx["master"])
    concepts = graph.get("concepts") or []
    mastery = concept_mastery(user_id, instance_id)
    mastered = mastered_set(mastery, MASTERY_THRESHOLD)
    status = concept_status(concepts, mastered)
    unlocks = build_unlocks(concepts)
    by_id = {c["id"]: c for c in concepts}
    counts = {"mastered": 0, "frontier": 0, "locked": 0, "total": len(concepts)}
    items = []
    for c in concepts:
        st = status.get(c["id"], "locked")
        counts[st] = counts.get(st, 0) + 1
        m = mastery.get(c["name"]) or {}
        items.append({
            "id": c["id"], "name": c["name"], "week": c["week"],
            "difficulty": c["difficulty"], "status": st,
            "mastery": m.get("prob", 0.0),
            "prereq_names": [by_id[p]["name"] for p in c["prerequisites"] if p in by_id],
            "unlocks": unlocks.get(c["id"], []),
        })
    from core.phases import compute_phases
    phases = compute_phases(ctx["master"], items)
    return {"threshold": MASTERY_THRESHOLD, "counts": counts, "concepts": items, "phases": phases}


def t_kb_search(*, user_id: int, instance_id: str, query: str, k: int = 4) -> dict:
    _assert_access(user_id, instance_id)
    try:
        k = max(1, min(int(k), 10))
    except (TypeError, ValueError):
        k = 4
    return rag.search(user_id, instance_id, (query or "").strip(), k)


def t_submit_feedback(
    *, user_id: int, instance_id: str, rating: str,
    scene: str = "lesson", topic: str = "", reason: str = "",
) -> dict:
    _assert_access(user_id, instance_id)
    from core.feedback import record_feedback
    try:
        res = record_feedback(
            user_id=user_id, instance_id=instance_id, scene=scene,
            rating=rating, topic=topic, reason=reason,
        )
    except ValueError as e:
        raise ToolError(str(e))
    return {"ok": True, **res}


def t_coach_reply(*, user_id: int, instance_id: str, history: Optional[list] = None) -> dict:
    _assert_access(user_id, instance_id)
    ctx = _instance_context(instance_id)
    analytics = learning.analytics_summary(user_id, instance_id)
    from core.memory import format_for_prompt, recall
    memory = format_for_prompt(recall(user_id, instance_id))
    reply = coach_reply(
        domain=ctx["domain"], target=ctx["target"], baseline=ctx["baseline"],
        analytics=analytics, history=history or [], memory=memory,
    )
    return {"reply": reply}


def t_recommend_today(*, user_id: int, instance_id: str) -> dict:
    """今日该学什么：复习/新课/弱项的有优先级推荐（确定性编排）。"""
    _assert_access(user_id, instance_id)
    from core.recommend import daily_plan
    ctx = _instance_context(instance_id)
    return daily_plan(user_id=user_id, instance_id=instance_id, inst_dir=ctx["inst"], master=ctx["master"])


def t_check_replan(*, user_id: int, instance_id: str) -> dict:
    """检测路径是否该调整（三触发器：卡壳/超额/中断），给可执行建议。"""
    _assert_access(user_id, instance_id)
    from core.replan import detect
    ctx = _instance_context(instance_id)
    return detect(user_id=user_id, instance_id=instance_id, inst_dir=ctx["inst"], master=ctx["master"])


def t_recall_memory(*, user_id: int, instance_id: str = "", limit: int = 8) -> dict:
    """取关于该学习者的长期记忆（全局 + 该空间）。"""
    if instance_id:
        _assert_access(user_id, instance_id)
    from core.memory import recall
    mems = recall(user_id, instance_id or None, limit)
    return {"count": len(mems), "memories": mems}


def t_remember(
    *, user_id: int, content: str, kind: str = "preference",
    instance_id: str = "",
) -> dict:
    """让系统记住一条关于学习者的事实/偏好（手写记忆）。"""
    if instance_id:
        _assert_access(user_id, instance_id)
    from core.memory import write_memory
    try:
        res = write_memory(
            user_id=user_id, content=content, kind=kind,
            instance_id=instance_id or None, source="user",
        )
    except ValueError as e:
        raise ToolError(str(e))
    return {"ok": True, **res}


def t_get_reminder_policy(*, user_id: int) -> dict:
    """读取当前用户的主动学习提醒规则。"""
    from core.reminders import get_policy

    policy = get_policy(user_id)
    return {"configured": policy is not None, "policy": policy}


def t_set_reminder_policy(
    *,
    user_id: int,
    instance_id: str = "",
    timezone: str = "Asia/Shanghai",
    local_time: str = "08:00",
    weekdays: Optional[list] = None,
    quiet_start: str = "",
    quiet_end: str = "",
    enabled: bool = True,
) -> dict:
    """创建、修改或暂停当前用户的学习提醒。"""
    if instance_id:
        _assert_access(user_id, instance_id)
    from core.reminders import ReminderError, set_policy

    try:
        policy = set_policy(
            user_id,
            instance_id=instance_id,
            timezone_name=timezone,
            local_time=local_time,
            weekdays=weekdays,
            quiet_start=quiet_start,
            quiet_end=quiet_end,
            enabled=enabled,
        )
    except ReminderError as exc:
        raise ToolError(str(exc)) from exc
    return {"ok": True, "policy": policy}


# ----------------------------- 注册表 -----------------------------

@dataclass
class Tool:
    name: str
    description: str
    handler: Callable[..., dict]
    params: dict[str, Any] = field(default_factory=dict)   # JSON-schema-ish: {name: {type, required, desc}}
    mutates: bool = False                                   # 是否写数据（agent 权限收窄用）

    def schema(self) -> dict:
        return {"name": self.name, "description": self.description, "mutates": self.mutates, "params": self.params}


def _p(type_: str, desc: str, required: bool = False) -> dict:
    return {"type": type_, "desc": desc, "required": required}


TOOLS: list[Tool] = [
    Tool("list_instances", "列出当前用户的学习空间（实例）", t_list_instances, {}),
    Tool("get_today_plan", "取某学习空间今天该推进的主题与时段安排", t_get_today_plan,
         {"instance_id": _p("string", "学习空间 id", True)}),
    Tool("generate_lesson", "就某主题现场生成一节微课（讲解+练习，已接 RAG 接地+citation）", t_generate_lesson,
         {"instance_id": _p("string", "学习空间 id", True),
          "topic": _p("string", "本节主题/知识点", False),
          "custom_instruction": _p("string", "本次生成要求，如更偏案例/更深/贴近某场景", False)}),
    Tool("record_answer", "记录一次学练完成（逐题作答 + 自评）", t_record_answer,
         {"instance_id": _p("string", "学习空间 id", True), "topic": _p("string", "知识点", True),
          "answers": _p("array", "逐题作答 [{question,options,answer_index,chosen_index,correct,why}]", False),
          "self_rating": _p("string", "自评 cant/with_help/independent", False),
          "week": _p("integer", "周次", False)}, mutates=True),
    Tool("get_analytics", "取某学习空间的学情数据包（双坐标/弱项/复习队列/趋势）", t_get_analytics,
         {"instance_id": _p("string", "学习空间 id", True)}),
    Tool("get_graph", "取知识疆域地图（Concept 图谱 + 掌握度 + 已征服/前线/锁定）", t_get_graph,
         {"instance_id": _p("string", "学习空间 id", True)}),
    Tool("kb_search", "在该空间知识库做混合检索（BM25+可选向量）", t_kb_search,
         {"instance_id": _p("string", "学习空间 id", True), "query": _p("string", "查询", True),
          "k": _p("integer", "返回条数(默认4)", False)}),
    Tool("coach_reply", "LearnBuddy 复盘/追问（基于学情）", t_coach_reply,
         {"instance_id": _p("string", "学习空间 id", True),
          "history": _p("array", "对话历史 [{role,content}]，空=首次复盘", False)}),
    Tool("submit_feedback", "对 AI 产出提交反馈（up/down/error 纠错）", t_submit_feedback,
         {"instance_id": _p("string", "学习空间 id", True),
          "rating": _p("string", "up / down / error", True),
          "scene": _p("string", "lesson/coach/graph(默认 lesson)", False),
          "topic": _p("string", "反馈对象/主题", False),
          "reason": _p("string", "文字补充(这里错了/原因)", False)}, mutates=True),
    Tool("recommend_today", "今日该学什么：复习/新课/弱项的有优先级推荐（确定性编排）", t_recommend_today,
         {"instance_id": _p("string", "学习空间 id", True)}),
    Tool("check_replan", "检测路径是否该调整：卡壳/超额/中断三触发器 + 可执行建议", t_check_replan,
         {"instance_id": _p("string", "学习空间 id", True)}),
    Tool("recall_memory", "取关于学习者的长期记忆（全局+该空间，喂个性化）", t_recall_memory,
         {"instance_id": _p("string", "学习空间 id(空=只取全局)", False),
          "limit": _p("integer", "条数(默认8)", False)}),
    Tool("remember", "记住一条关于学习者的事实/偏好（手写记忆）", t_remember,
         {"content": _p("string", "要记住的一句话", True),
          "kind": _p("string", "preference/goal/fact/struggle/strength/pace", False),
          "instance_id": _p("string", "学习空间 id(空=全局记忆)", False)}, mutates=True),
    Tool("get_reminder_policy", "读取当前学习者的主动提醒时间、星期和开关", t_get_reminder_policy, {}),
    Tool("set_reminder_policy", "创建、修改或暂停当前学习者的主动提醒", t_set_reminder_policy,
         {"instance_id": _p("string", "关联的学习空间 id（可空）", False),
          "timezone": _p("string", "IANA 时区，如 Asia/Shanghai", False),
          "local_time": _p("string", "本地提醒时间 HH:MM", False),
          "weekdays": _p("array", "1-7 表示周一到周日", False),
          "quiet_start": _p("string", "静默开始 HH:MM（可空）", False),
          "quiet_end": _p("string", "静默结束 HH:MM（可空）", False),
          "enabled": _p("boolean", "true=启用，false=暂停", False)}, mutates=True),
]

TOOLS_BY_NAME: dict[str, Tool] = {t.name: t for t in TOOLS}


def list_tools() -> list[dict]:
    """注册表的可序列化描述（供 /api/tools、MCP、文档使用）。"""
    return [t.schema() for t in TOOLS]


def call_tool(name: str, *, user_id: int, args: Optional[dict] = None) -> dict:
    """统一调度入口。未知工具 / 缺必填参数 → ToolError。"""
    tool = TOOLS_BY_NAME.get(name)
    if tool is None:
        raise ToolError(f"未知工具：{name}")
    args = dict(args or {})
    if "user_id" in args:
        raise ToolError("user_id 由服务端身份注入，不允许作为工具参数")
    for pname, spec in tool.params.items():
        if spec.get("required") and pname not in args:
            raise ToolError(f"工具 {name} 缺少必填参数：{pname}")
    return tool.handler(user_id=user_id, **args)


__all__ = ["Tool", "TOOLS", "TOOLS_BY_NAME", "list_tools", "call_tool", "ToolError"]
