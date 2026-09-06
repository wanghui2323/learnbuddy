"""LearnBuddy 个性化学习路径生成器。

职责：
- 输入：UserParams（来自对话引擎 GENERATE 阶段）
- 输出：核心学习路径产物 → 写到 data/instances/{slug}/

核心产物：
- meta.json           实例元数据（id / domain / target / 创建时间 / 状态）
- master.json         L1 月度蓝图 + L2 周主题 + 路径研究/质检元数据
- W1.json             第 1 周详细日历（按周一三五日产出节奏排时段；不调 LLM）
- 学习手册.md         领域适配方法论（核心方法 + 反遗忘 5 机制 + 6 关自检；调 LLM 做操作要点 + 6 关具体标志的领域翻译）
- 愿景与契约.md       用户专属契约（用户原话嵌入 + R1-R5 调整规则精简版；不调 LLM）
- concepts.json       路径级概念图谱（优先教研 Agent，失败时线性兜底）

使用方式：

    from core.generator import generate_instance
    from core.schemas import UserParams

    params = UserParams(...)
    instance_dir = generate_instance(params)
    print(f"已生成：{instance_dir}")
"""

from __future__ import annotations

import json
import re
import secrets
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

from core.concepts import load_or_generate
from core.engine import domain_to_slug
from core.llm_client import LLMClient, LLMError, get_default_client
from core.path_deepbuild import (
    OutlineRefinementError,
    build_generation_workflow,
    build_linear_concept_graph,
    build_outline_research,
    evaluate_outline_quality,
    refine_master_from_research,
)
from core.schemas import (
    DaySlot,
    Instance,
    InstanceStatus,
    LearningMethods,
    Message,
    UserParams,
    WeekDay,
    WeekPlan,
)


# ============================================================================
# 路径
# ============================================================================


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INSTANCES_DIR = PROJECT_ROOT / "data" / "instances"
PROMPTS_DIR = Path(__file__).parent / "prompts"
LEARNING_MANUAL_PROMPT_PATH = PROMPTS_DIR / "learning_manual.md"


def _load_learning_manual_prompt() -> str:
    if not LEARNING_MANUAL_PROMPT_PATH.exists():
        raise FileNotFoundError(
            f"找不到学习手册 prompt: {LEARNING_MANUAL_PROMPT_PATH}"
        )
    return LEARNING_MANUAL_PROMPT_PATH.read_text(encoding="utf-8")


# ============================================================================
# 反遗忘 5 机制（V0 内置默认翻译，LLM 出问题时兜底）
# ============================================================================


DEFAULT_ANTI_FORGETTING_5 = [
    ("micro-review 微复习", "每天开课前 5 分钟扫一眼昨日重点"),
    ("间隔重复", "1/3/7/14/30 天节奏复习关键概念"),
    ("周回测", "每周日花 30 分钟自测本周新内容"),
    ("月度雪球", "每月末把过去 4 周的内容串成一张图，找断点"),
    ("解释关延迟", "学完 1 周后能讲给同领域朋友听 5 分钟"),
]


def _baseline_decisions(profile) -> list[dict]:
    """把能力画像转成可审计路径决策；每条决策都保留评分证据引用。"""
    if profile is None or getattr(profile, "assessment_method", "") != "observed_v2":
        return []
    if getattr(profile, "status", "") != "completed":
        return []
    abilities = {item.key: item for item in (profile.abilities or [])}
    rules = [
        ("independent_foundation", 60, "foundation_repair", "前两周增加先修诊断、概念辨析和无工具练习，补齐独立底座。"),
        ("independent_transfer", 65, "transfer_drills", "每周至少安排两次跨情境输出，并用新材料复测迁移。"),
        ("verification_correction", 70, "verification_gate", "AI 产出必须经过反例、来源或测试验收，未通过不得计为完成。"),
        ("self_calibration", 65, "confidence_calibration", "关键练习先报信心分再验收，持续记录高估和低估偏差。"),
        ("learning_strategy", 60, "short_feedback_loop", "把任务拆成 20–40 分钟闭环，每个闭环都留下产出和复盘。"),
    ]
    decisions: list[dict] = []
    for key, threshold, rule, action in rules:
        ability = abilities.get(key)
        if ability is None or ability.score >= threshold:
            continue
        decisions.append(
            {
                "rule": rule,
                "ability": key,
                "observed_score": ability.score,
                "threshold": threshold,
                "evidence_ids": list(ability.evidence_ids),
                "action": action,
            }
        )
    independent = float(getattr(profile, "observed_independent_score", 0) or 0)
    assisted = float(getattr(profile, "observed_with_ai_score", 0) or 0)
    if assisted - independent >= 20:
        ai = abilities.get("ai_collaboration")
        decisions.append(
            {
                "rule": "ai_dependency_guard",
                "ability": "ai_collaboration",
                "observed_score": assisted,
                "threshold": independent + 20,
                "evidence_ids": list(ai.evidence_ids) if ai else [],
                "action": "保留 AI 协作训练，但每个主题先完成一次无工具尝试，再开放 AI。",
            }
        )
    return decisions[:4]


def _apply_baseline_decisions(master: dict, profile) -> dict:
    decisions = _baseline_decisions(profile)
    north_star = master.setdefault("north_star", {})
    north_star["baseline_decisions"] = decisions
    if not decisions:
        return master
    for index, decision in enumerate(decisions):
        weeks = master.get("weeks") or []
        if not weeks:
            break
        target = weeks[min(index, min(3, len(weeks) - 1))]
        outcomes = list(target.get("key_outcomes") or [])
        action = decision["action"]
        if action not in outcomes:
            outcomes.append(action)
        target["key_outcomes"] = outcomes
    return master


# ============================================================================
# build_master · 月度蓝图 + 周主题（不调 LLM）
# ============================================================================


def build_master(params: UserParams, start_date: date) -> dict:
    """从参数包构建 master.json（L1 月度 + L2 周主题）。"""
    weeks = params.weeks
    months_count = max(1, (weeks + 3) // 4)

    months_data = []
    for m_idx in range(1, months_count + 1):
        ms = next((x for x in params.milestones if x.month == m_idx), None)
        m_start = start_date + timedelta(days=(m_idx - 1) * 28)
        m_end = m_start + timedelta(days=27)
        weeks_in_month = list(
            range((m_idx - 1) * 4 + 1, min(m_idx * 4, weeks) + 1)
        )
        months_data.append(
            {
                "id": f"M{m_idx}",
                "title": ms.title if ms else f"M{m_idx} 阶段",
                "deliverable": ms.deliverable if ms else "（待对话补充具体交付）",
                "date_range": f"{m_start.isoformat()} ~ {m_end.isoformat()}",
                "weeks": weeks_in_month,
            }
        )

    weeks_data = []
    for w_idx in range(1, weeks + 1):
        wt = next((x for x in params.weekly_themes if x.week == w_idx), None)
        w_start = start_date + timedelta(days=(w_idx - 1) * 7)
        w_end = w_start + timedelta(days=6)
        m_id = f"M{(w_idx - 1) // 4 + 1}"
        weeks_data.append(
            {
                "week": w_idx,
                "month": m_id,
                "date_range": f"{w_start.isoformat()} ~ {w_end.isoformat()}",
                "title": wt.title if wt else f"W{w_idx} · 待补充",
                "key_outcomes": (
                    wt.key_outcomes if wt and wt.key_outcomes else ["（待对话补充）"]
                ),
                "status": "pending" if w_idx > 1 else "active",
                "plan_file": f"W{w_idx}.json",
            }
        )

    master = {
        "version": "0.0.1-itutor-generated",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "lock_status": "draft",
        "_note": (
            f"由 LearnBuddy 对话生成（{datetime.now().strftime('%Y-%m-%d %H:%M')}）。"
            f"领域：{params.domain}。"
            "用户可在实例视图手工编辑补充。"
        ),
        "north_star": {
            "domain": params.domain,
            "domain_category": params.domain_category.value,
            "target": params.target,
            "weeks": weeks,
            "intensity": params.intensity.value,
            "weekly_total_hours": params.weekly_total_hours,
            "baseline_summary": params.baseline_summary,
            "baseline_profile": (
                params.baseline_profile.model_dump(mode="json")
                if params.baseline_profile is not None
                else None
            ),
        },
        "schedule": {
            "weekday_hours": params.weekday_hours,
            "weekend_hours": params.weekend_hours,
            "start_date": start_date.isoformat(),
        },
        "months": months_data,
        "weeks": weeks_data,
        "totals": {
            "total_weeks": weeks,
            "total_months": months_count,
            "total_planned_hours": (params.weekly_total_hours or 0) * weeks,
        },
        "learning_methods": {
            "core_method": params.learning_methods.core_method,
            "anti_forgetting": params.learning_methods.anti_forgetting,
            "self_check_dimensions": params.learning_methods.self_check_dimensions,
        },
    }
    return _apply_baseline_decisions(master, params.baseline_profile)


# ============================================================================
# build_week_plan · 单周日历（工作日连续推进，复习嵌入每日开头，不调 LLM）
# ============================================================================


_WEEKDAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
# 工作日都应推进新内容/练习/产出；周日做整周回顾和自检。
_OUTPUT_DAYS_0IDX = {0, 1, 2, 3, 4, 6}

# 工作日任务主题：反遗忘只作为 5-10 分钟热身，不占掉整天。
_WEEKDAY_OUTPUT_TASKS = {
    0: "推进新内容：读 / 看 / 理解 1 个新知识点",
    1: "微复习 + 新内容推进：先 10 分钟回看昨日重点，再学习 1 个新知识点 / 案例",
    2: "巩固 + 输出：造句 / 讲解 / 演示给自己听",
    3: "错题回看 + 边界练习：先 10 分钟复盘卡点，再做 1 个迁移案例",
    4: "实战 + 错题：本周新内容用 1 次 + 错题整理",
}


def _build_day_slots(
    *,
    weekday_idx: int,
    is_weekend: bool,
    hours: float,
    week: int,
    is_output_day: bool,
) -> list[DaySlot]:
    """按时段规则排该天的 slots。

    规则（V0 简化版）：
    - 工作日 weekday_hours h：晚上单段（如 20:00-22:00）
    - 周末 weekend_hours h：上午 + 下午两段
    - 工作日：都推进新内容、练习或输出；复习只嵌入开头 5-10 分钟
    - 周末（六日）：长任务推进 / 整周回顾 + 6 关自检
    """
    slots: list[DaySlot] = []
    if hours <= 0:
        return slots

    if is_weekend:
        half = hours / 2
        is_sunday = weekday_idx == 6
        slots.append(
            DaySlot(
                time=f"上午 09:00-{_offset_time('09:00', half)}",
                task=(
                    "整周回顾 + 关键卡片整理（周日重点）"
                    if is_sunday
                    else "本周长任务推进（实战 / 项目 / 模考）"
                ),
                output=("回顾笔记" if is_sunday else "本周关键产出 1 项"),
            )
        )
        slots.append(
            DaySlot(
                time=f"下午 14:00-{_offset_time('14:00', half)}",
                task=(
                    "6 关自检 + 下周节奏决策"
                    if is_sunday
                    else "实战 / 输出（写 / 讲 / 教）"
                ),
                output="6 关自检表" if is_sunday else "1 个产出物",
            )
        )
        return slots

    end_time = _offset_time("20:00", hours)
    if is_output_day:
        task = _WEEKDAY_OUTPUT_TASKS.get(weekday_idx, f"W{week} 节奏产出")
        slots.append(
            DaySlot(
                time=f"晚 20:00-{end_time}",
                task=task,
                output="1 段笔记 / 错题 / 输出",
            )
        )
    else:
        slots.append(
            DaySlot(
                time=f"晚 20:00-{end_time}",
                task="微复习 + 自主推进：先 10 分钟回看旧内容，再推进本周任务",
                output=None,
            )
        )

    return slots


def _offset_time(start_hhmm: str, hours: float) -> str:
    """简单的 HH:MM + hours 计算（不考虑跨天）。"""
    h, m = map(int, start_hhmm.split(":"))
    total_minutes = h * 60 + m + int(hours * 60)
    new_h = (total_minutes // 60) % 24
    new_m = total_minutes % 60
    return f"{new_h:02d}:{new_m:02d}"


def build_week_plan(
    params: UserParams,
    start_date: date,
    week_idx: int = 1,
    *,
    master: dict | None = None,
) -> WeekPlan:
    """构建第 week_idx 周的详细日历。"""
    master_week = next(
        (
            week
            for week in ((master or {}).get("weeks") or [])
            if int(week.get("week") or 0) == week_idx
        ),
        None,
    )
    weeks_data = params.weekly_themes
    wt = next((x for x in weeks_data if x.week == week_idx), None)

    week_start = start_date + timedelta(days=(week_idx - 1) * 7)
    week_end = week_start + timedelta(days=6)

    title = (
        str(master_week.get("title") or "").strip()
        if master_week
        else (wt.title if wt else f"W{week_idx} · 待对话补充")
    )
    key_outcomes = (
        list(master_week.get("key_outcomes") or [])
        if master_week
        else wt.key_outcomes
        if wt and wt.key_outcomes
        else [
            f"完成 {params.weekly_total_hours or 18}h 投入（不打折）",
            "周日 6 关自检 ≥ 4 关通过",
        ]
    )

    days: list[WeekDay] = []
    for i in range(7):
        d = week_start + timedelta(days=i)
        weekday_idx = d.weekday()
        is_weekend = weekday_idx >= 5
        hours = params.weekend_hours if is_weekend else params.weekday_hours
        is_output_day = weekday_idx in _OUTPUT_DAYS_0IDX

        slots = _build_day_slots(
            weekday_idx=weekday_idx,
            is_weekend=is_weekend,
            hours=hours,
            week=week_idx,
            is_output_day=is_output_day,
        )

        days.append(
            WeekDay(
                day=i + 1,
                date=d,
                weekday=_WEEKDAY_NAMES[weekday_idx],
                is_weekend=is_weekend,
                planned_hours=hours,
                slots=slots,
                notes=(
                    "产出节奏日：必出 1 份产物" if is_output_day and not is_weekend
                    else None
                ),
            )
        )

    return WeekPlan(
        week=week_idx,
        title=title,
        date_range=f"{week_start.isoformat()} ~ {week_end.isoformat()}",
        key_outcomes=key_outcomes,
        days=days,
    )


def build_week_plan_from_master(master: dict, week_idx: int) -> WeekPlan:
    """仅依赖已落盘 master 构建 W2–Wn，避免丢失原始 UserParams。"""
    schedule = (master or {}).get("schedule") or {}
    north_star = (master or {}).get("north_star") or {}
    try:
        start_date = date.fromisoformat(str(schedule.get("start_date") or ""))
    except ValueError as exc:
        raise ValueError("master.schedule.start_date 缺失或不合法。") from exc
    week = next(
        (
            item
            for item in ((master or {}).get("weeks") or [])
            if int(item.get("week") or 0) == int(week_idx)
        ),
        None,
    )
    if week is None:
        raise ValueError(f"master 中不存在 W{week_idx}。")
    try:
        weekday_hours = float(schedule.get("weekday_hours") or 0)
        weekend_hours = float(schedule.get("weekend_hours") or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("master.schedule 的学习时长不合法。") from exc
    week_start = start_date + timedelta(days=(int(week_idx) - 1) * 7)
    days: list[WeekDay] = []
    for i in range(7):
        current = week_start + timedelta(days=i)
        weekday_idx = current.weekday()
        is_weekend = weekday_idx >= 5
        hours = weekend_hours if is_weekend else weekday_hours
        slots = _build_day_slots(
            weekday_idx=weekday_idx,
            is_weekend=is_weekend,
            hours=hours,
            week=int(week_idx),
            is_output_day=weekday_idx in _OUTPUT_DAYS_0IDX,
        )
        days.append(
            WeekDay(
                day=i + 1,
                date=current,
                weekday=_WEEKDAY_NAMES[weekday_idx],
                is_weekend=is_weekend,
                planned_hours=hours,
                slots=slots,
                notes=("产出节奏日：必出 1 份产物" if weekday_idx in _OUTPUT_DAYS_0IDX and not is_weekend else None),
            )
        )
    title = str(week.get("title") or f"W{week_idx}").strip()
    outcomes = [str(item).strip() for item in (week.get("key_outcomes") or []) if str(item).strip()]
    if not outcomes:
        outcomes = [
            f"完成 W{week_idx} 全部日计划",
            "周末 6 关自检至少 4 关通过",
        ]
    return WeekPlan(
        week=int(week_idx),
        title=title or str(north_star.get("domain") or f"W{week_idx}"),
        date_range=f"{week_start.isoformat()} ~ {(week_start + timedelta(days=6)).isoformat()}",
        key_outcomes=outcomes,
        days=days,
    )


def ensure_week_plan(inst_dir: Path, week_idx: int) -> tuple[WeekPlan, bool]:
    """幂等、原子地为已通过闸门的周生成 Wn.json。"""
    week_idx = int(week_idx)
    if week_idx < 1:
        raise ValueError("week_idx 必须 ≥ 1。")
    target = inst_dir / f"W{week_idx}.json"
    if target.exists():
        return WeekPlan.model_validate_json(target.read_text(encoding="utf-8")), False
    master_path = inst_dir / "master.json"
    if not master_path.exists():
        raise ValueError("master.json 不存在。")
    master = json.loads(master_path.read_text(encoding="utf-8"))
    plan = build_week_plan_from_master(master, week_idx)
    temp = inst_dir / f".W{week_idx}.{secrets.token_hex(6)}.tmp"
    try:
        temp.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
        temp.replace(target)
    finally:
        if temp.exists():
            temp.unlink(missing_ok=True)
    return plan, True


# ============================================================================
# build_learning_manual · 领域适配学习手册（调 LLM 做操作要点 + 6 关具体标志）
# ============================================================================


_JSON_OBJECT_PATTERN = re.compile(r"\{[\s\S]*\}", re.DOTALL)
_JSON_CODE_BLOCK_PATTERN = re.compile(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", re.DOTALL)


def _extract_json_object(text: str) -> Optional[str]:
    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        return stripped
    m = _JSON_CODE_BLOCK_PATTERN.search(text)
    if m:
        return m.group(1).strip()
    m = _JSON_OBJECT_PATTERN.search(text)
    if m:
        return m.group(0)
    return None


def _llm_polish_manual(
    params: UserParams,
    client: LLMClient,
) -> tuple[list[str], list[str]]:
    """调 LLM 生成"操作要点"+"6 关具体通过标志"，失败时返回兜底。

    Returns:
        (core_method_steps, self_check_pass_signals)
    """
    fallback_steps = [
        f"按「{params.learning_methods.core_method}」每天投入 {params.weekday_hours}h",
        f"每周日做 1 次 6 关自检 + adjustments-log 记录",
        f"卡 7 天 → 拆 toy 版或求助",
    ]
    fallback_signals = [
        f"{dim}：可以独立完成本周新内容的 70%"
        for dim in (
            params.learning_methods.self_check_dimensions
            or ["维度 1", "维度 2", "维度 3", "维度 4", "维度 5", "维度 6"]
        )[:6]
    ]
    while len(fallback_signals) < 6:
        fallback_signals.append(f"维度 {len(fallback_signals) + 1}：（待补充）")

    template = _load_learning_manual_prompt()
    rendered = (
        template.replace("{domain}", params.domain)
        .replace("{target}", params.target)
        .replace("{core_method}", params.learning_methods.core_method)
        .replace(
            "{anti_forgetting}",
            json.dumps(params.learning_methods.anti_forgetting, ensure_ascii=False),
        )
        .replace(
            "{self_check_dimensions}",
            json.dumps(
                params.learning_methods.self_check_dimensions, ensure_ascii=False
            ),
        )
    )

    try:
        text = client.chat(
            [Message(role="user", content=rendered)],
            temperature=0.5,
            max_tokens=2000,
        )
    except LLMError:
        return fallback_steps, fallback_signals

    json_str = _extract_json_object(text)
    if json_str is None:
        return fallback_steps, fallback_signals

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        return fallback_steps, fallback_signals

    steps = data.get("core_method_steps") or []
    signals = data.get("self_check_pass_signals") or []
    if not isinstance(steps, list) or not isinstance(signals, list):
        return fallback_steps, fallback_signals

    steps = [str(s).strip() for s in steps if str(s).strip()]
    signals = [str(s).strip() for s in signals if str(s).strip()]

    if not steps:
        steps = fallback_steps
    if len(signals) < 6:
        signals = signals + fallback_signals[len(signals) :]
    signals = signals[:6]

    return steps, signals


def _format_anti_forgetting_table(items: list[str]) -> str:
    """把 anti_forgetting 列表对齐到 5 个机制名。"""
    rows = []
    for i, (mech_name, default_desc) in enumerate(DEFAULT_ANTI_FORGETTING_5):
        user_desc = items[i] if i < len(items) and items[i].strip() else default_desc
        rows.append(f"| {i + 1} | {mech_name} | {user_desc} |")
    return "\n".join(rows)


def _format_self_check_table(
    dimensions: list[str], signals: list[str]
) -> str:
    """6 关自检表，自动补齐到 6 行。"""
    rows = []
    for i in range(6):
        dim = dimensions[i] if i < len(dimensions) else f"维度 {i + 1}"
        sig = signals[i] if i < len(signals) else "（待补充）"
        rows.append(f"| {i + 1} | {dim} | {sig} |")
    return "\n".join(rows)


def build_learning_manual(
    params: UserParams,
    client: Optional[LLMClient] = None,
    *,
    use_llm: bool = True,
) -> str:
    """生成学习手册 markdown（含 LLM 领域适配的操作要点 + 6 关具体标志）。"""
    if use_llm:
        client = client or get_default_client()
        method_steps, pass_signals = _llm_polish_manual(params, client)
    else:
        method_steps = [
            f"按「{params.learning_methods.core_method}」每天投入 {params.weekday_hours}h",
            "周日 6 关自检 + adjustments-log 记录",
            "卡 7 天 → 拆 toy 版或求助",
        ]
        pass_signals = [
            f"{dim}：可独立完成本周新内容的 70%"
            for dim in (
                params.learning_methods.self_check_dimensions
                or ["维度 1", "维度 2", "维度 3", "维度 4", "维度 5", "维度 6"]
            )[:6]
        ]

    anti_forget_rows = _format_anti_forgetting_table(
        params.learning_methods.anti_forgetting
    )
    self_check_rows = _format_self_check_table(
        params.learning_methods.self_check_dimensions, pass_signals
    )

    method_steps_md = "\n".join(f"- {s}" for s in method_steps)

    return f"""# 学习手册 · {params.domain}

> 由 LearnBuddy 对话生成 · {datetime.now().strftime('%Y-%m-%d %H:%M')}
> 用户专属版 · 领域适配的方法论手册

---

## 一、北极星目标

- **领域**：{params.domain}
- **目标**：{params.target}
- **周期**：{params.weeks} 周（{params.intensity.value}）
- **周投入**：{params.weekly_total_hours}h（工作日 {params.weekday_hours}h × 5 + 周末 {params.weekend_hours}h × 2）
- **基线**：{params.baseline_summary}

---

## 二、核心学习方法

> {params.learning_methods.core_method}

**操作要点**（领域适配，由 LLM 现场生成）：

{method_steps_md}

---

## 三、反遗忘 5 机制（LearnBuddy 长期保留率核心机制）

| # | 机制 | 在你这领域的具体做法 |
|---|---|---|
{anti_forget_rows}

> 反遗忘机制不是可选项。任何"学完就忘"都是机制不到位的结果。

---

## 四、6 关自检（每周日必做）

| 关 | 维度 | 通过标志（由 LLM 现场领域适配） |
|---|---|---|
{self_check_rows}

**规则**：
- 每周日做 1 次 6 关自检
- **4 关以下** → 下周原地补，不进新内容（R1 规则）
- 月末按 4 个周末的自检趋势做 R3 评估

---

## 五、周固定结构（{params.weeks} 周不变）

| 时段 | 任务类型 |
|---|---|
| 周一 | 推新内容 + 写笔记 |
| 周二 | 微复习 10 分钟 + 新内容 / 案例推进 |
| 周三 | 巩固 + 造句/讲解/演示 |
| 周四 | 错题回看 10 分钟 + 边界练习 / 迁移案例 |
| 周五 | 实战 + 输出 1 件作品 |
| 周六 | 长任务推进：实战 / 项目 / 模考 |
| 周日 | 6 关自检 + 整周回顾（产出节奏日）|

---

## 六、节奏调整规则（精简版 R1-R5）

| 规则 | 触发 | 行动 |
|---|---|---|
| **R1** | 周自检 ≤ 4 关 | 下周原地补，不进新内容 |
| **R2** | 某项卡 ≥ 7 天 | 拆 toy 版 / 跳过 / 求助 |
| **R3** | 月末测有 ≥ 2 项不过 | 下月延期，不强推 |
| **R4** | 投入 > 150% 持续 2 周 | 暂停校准，调整目标或强度 |
| **R5** | 发现重要新内容/资源 | 评估是否吸纳，进 adjustments-log |

> 规则不是约束你的，是保护你不"带病推进"。

---

> 此手册由 LearnBuddy 生成。完整 R1-R10 规则手册 + 月度 R11 评估在后续阶段加入。
"""


# ============================================================================
# build_vision_contract · 愿景与契约（纯模板，不调 LLM）
# ============================================================================


def build_vision_contract(params: UserParams) -> str:
    """生成愿景与契约 markdown。"""
    preferences_section = (
        params.preferences.strip() if params.preferences.strip() else "（用户未在对话中明确说，可在实例视图后续补充）"
    )

    return f"""# 愿景与契约 · {params.domain}

> 由 LearnBuddy 对话生成 · {datetime.now().strftime('%Y-%m-%d %H:%M')}
> 这不是任务清单，是契约。

---

## 一、北极星

> **{params.domain}** · {params.target}

**为什么这件事值得 {params.weeks} 周？**

{preferences_section}

---

## 二、LearnBuddy 对你的承诺

1. 我会在每周日提醒你做 6 关自检
2. 我会在每月末做 R1-R5 评估，必要时主动建议调整路径
3. 我会保留你的全部学习数据，可一键导出
4. 你随时可以重新对话调整路径，所有变更进 `adjustments-log`
5. 我不会替你做决定——只给你判断的依据

---

## 三、你对自己的承诺

1. 周可投入 **{params.weekly_total_hours}h**（工作日 {params.weekday_hours}h × 5 + 周末 {params.weekend_hours}h × 2）
2. 6 关自检每周日必做（≤ 4 关 = 下周不进新内容）
3. 月末评估真实回答（不为"看起来好"虚报）
4. 卡 7 天主动求助，不闷头硬磕

---

## 四、当 X 发生时怎么办

### R1 周自检 ≤ 4 关
→ 下周原地补，不进新内容。承认进度问题比假装顺利更省时间。

### R2 某项卡 ≥ 7 天
→ 拆 toy 版（更小目标） / 跳过 / 求助。不要花 14 天硬磕同一个点。

### R3 月末测 ≥ 2 项不过
→ 下月延期，不强推。

### R4 投入 > 150% 持续 2 周
→ 暂停校准。说明目标定高了或强度选错了。

### R5 发现重要新内容/资源
→ 评估是否吸纳进路径。**新好内容比已计划内容更重要**——但不要追新成瘾。

---

## 五、签字行（可选）

签字日期：______________

签字：______________

> 这是写给 4 个月后的你的信。
"""


# ============================================================================
# generate_instance · 一键生成完整实例
# ============================================================================


def _read_index(instances_dir: Path) -> dict:
    index_path = instances_dir / "_index.json"
    if not index_path.exists():
        return {
            "_note": "LearnBuddy 学习路径实例索引",
            "_updated_at": None,
            "count": 0,
            "instances": [],
        }
    return json.loads(index_path.read_text(encoding="utf-8"))


def _write_index(instances_dir: Path, index: dict) -> None:
    index["_updated_at"] = datetime.now().isoformat(timespec="seconds")
    index["count"] = len(index.get("instances", []))
    (instances_dir / "_index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _resolve_unique_instance_id(instances_dir: Path, base_id: str) -> str:
    """如果 base_id 已存在，自动加 -2 / -3 / ... 后缀。"""
    candidate = base_id
    n = 2
    while (instances_dir / candidate).exists():
        candidate = f"{base_id}-{n}"[:40]
        n += 1
        if n > 99:
            raise RuntimeError(f"学习路径 id 冲突无法解决：{base_id}")
    return candidate


def generate_instance(
    params: UserParams,
    *,
    start_date: Optional[date] = None,
    instance_id: Optional[str] = None,
    instances_dir: Optional[Path] = None,
    llm_client: Optional[LLMClient] = None,
    use_llm_for_manual: bool = True,
    use_deep_outline: bool = True,
    use_outline_llm: bool = False,
    use_outline_web_search: bool = False,
    outline_user_sources: Optional[list[dict]] = None,
    outline_max_revisions: int = 2,
    require_outline_quality: Optional[bool] = None,
    progress: Optional[Callable[[str], None]] = None,
) -> Path:
    """一键生成完整实例并写到磁盘，返回实例目录。

    生成核心产物：
        - meta.json
        - master.json
        - W1.json
        - 学习手册.md
        - 愿景与契约.md
        - concepts.json

    并更新 instances_dir/_index.json。
    """
    if start_date is None:
        # 默认下周一
        today = date.today()
        days_to_monday = (7 - today.weekday()) % 7
        if days_to_monday == 0:
            days_to_monday = 7
        start_date = today + timedelta(days=days_to_monday)

    if instances_dir is None:
        instances_dir = DEFAULT_INSTANCES_DIR
    instances_dir.mkdir(parents=True, exist_ok=True)

    base_id = instance_id or domain_to_slug(params.domain)
    final_id = _resolve_unique_instance_id(instances_dir, base_id)
    inst_dir = instances_dir / final_id
    inst_dir.mkdir(parents=True, exist_ok=False)

    if require_outline_quality is None:
        require_outline_quality = use_outline_llm

    def emit(stage: str) -> None:
        if progress is not None:
            progress(stage)

    try:
        emit("profile")
        instance = Instance(
            id=final_id,
            domain=params.domain,
            domain_category=params.domain_category,
            target=params.target,
            weeks=params.weeks,
            intensity=params.intensity,
            status=InstanceStatus.DRAFT,
            start_date=start_date,
        )
        base_master = build_master(params, start_date)
        master = base_master
        refinement = {"status": "not_run", "attempts": [], "stop_reason": "disabled"}

        if use_deep_outline:
            emit("research")
            outline_research = build_outline_research(
                params,
                allow_network=use_outline_web_search,
                user_sources=outline_user_sources,
            )
            llm = llm_client or (get_default_client() if use_outline_llm else None)
            attempts = max(1, min(3, int(outline_max_revisions) + 1)) if use_outline_llm else 1
            prior_issues: list[str] = []
            concept_graph = None
            outline_quality = None
            for attempt_no in range(1, attempts + 1):
                emit("outline")
                attempt_trace: dict = {"attempt": attempt_no}
                if use_outline_llm:
                    try:
                        master, llm_trace = refine_master_from_research(
                            params,
                            base_master,
                            outline_research,
                            client=llm,
                            issues=prior_issues,
                        )
                        attempt_trace.update(llm_trace)
                    except OutlineRefinementError as exc:
                        prior_issues = [str(exc)]
                        attempt_trace.update({"status": "invalid", "issues": prior_issues})
                        refinement["attempts"].append(attempt_trace)
                        continue
                else:
                    master = base_master
                    attempt_trace["status"] = "deterministic"

                emit("concept")
                if use_outline_llm:
                    concept_graph = load_or_generate(
                        inst_dir,
                        master,
                        client=llm,
                        regenerate=attempt_no > 1,
                    )
                else:
                    concept_graph = build_linear_concept_graph(master)
                    (inst_dir / "concepts.json").write_text(
                        json.dumps(concept_graph, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                emit("plan")
                w1_plan = build_week_plan(params, start_date, week_idx=1, master=master)
                emit("quality")
                outline_quality = evaluate_outline_quality(
                    params,
                    master,
                    w1_plan,
                    outline_research,
                    concept_graph,
                )
                prior_issues = list(outline_quality.get("issues") or [])
                attempt_trace.update(
                    {
                        "status": "passed" if outline_quality.get("passed") else "needs_repair",
                        "score": outline_quality.get("score"),
                        "issues": prior_issues,
                    }
                )
                refinement["attempts"].append(attempt_trace)
                if outline_quality.get("passed"):
                    refinement["status"] = "passed"
                    refinement["stop_reason"] = "quality_gate_passed"
                    break

            if outline_quality is None:
                raise OutlineRefinementError("大纲修复次数已用尽，仍未产生可质检的路径。")
            if not outline_quality.get("passed"):
                refinement["status"] = "failed"
                refinement["stop_reason"] = "max_revisions_reached"
            if require_outline_quality and not outline_quality.get("passed"):
                raise OutlineRefinementError(
                    "路径质量门未通过：" + "；".join(outline_quality.get("issues") or ["未知原因"])
                )
            master["_outline_research"] = outline_research
            master["_outline_quality"] = outline_quality
            master["_outline_refinement"] = refinement
            master["_generation_workflow"] = build_generation_workflow(
                outline_research,
                outline_quality,
                concept_graph,
                refinement,
            )
        else:
            w1_plan = build_week_plan(params, start_date, week_idx=1, master=master)
            master["_generation_workflow"] = {
                "version": "0.1",
                "built_at": datetime.now().isoformat(timespec="seconds"),
                "stages": [
                    {
                        "id": "basic_outline",
                        "title": "基础路径生成",
                        "status": "done",
                        "note": "本次跳过路径级研究、概念图谱和质检。",
                    }
                ],
            }

        # 深度大纲可能重写周计划，落盘前再次施加可审计的画像决策。
        master = _apply_baseline_decisions(master, params.baseline_profile)
        w1_plan = build_week_plan(params, start_date, week_idx=1, master=master)

        # 所有必要产物都构建成功后再落盘，避免质量门失败留下伪实例。
        (inst_dir / "meta.json").write_text(instance.model_dump_json(indent=2), encoding="utf-8")
        (inst_dir / "master.json").write_text(
            json.dumps(master, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (inst_dir / "W1.json").write_text(w1_plan.model_dump_json(indent=2), encoding="utf-8")
        manual_md = build_learning_manual(
            params,
            client=llm_client,
            use_llm=use_llm_for_manual,
        )
        (inst_dir / "学习手册.md").write_text(manual_md, encoding="utf-8")
        (inst_dir / "愿景与契约.md").write_text(build_vision_contract(params), encoding="utf-8")

        index = _read_index(instances_dir)
        index_entry = {
            "id": final_id,
            "domain": params.domain,
            "domain_category": params.domain_category.value,
            "target": params.target,
            "weeks": params.weeks,
            "intensity": params.intensity.value,
            "status": instance.status.value,
            "start_date": start_date.isoformat(),
            "created_at": instance.created_at.isoformat(timespec="seconds"),
        }
        instances_list = [e for e in index.get("instances", []) if e.get("id") != final_id]
        instances_list.append(index_entry)
        index["instances"] = instances_list
        _write_index(instances_dir, index)
        return inst_dir
    except Exception:
        shutil.rmtree(inst_dir, ignore_errors=True)
        raise


__all__ = [
    "DEFAULT_INSTANCES_DIR",
    "build_master",
    "build_week_plan",
    "build_week_plan_from_master",
    "ensure_week_plan",
    "build_learning_manual",
    "build_vision_contract",
    "generate_instance",
]
