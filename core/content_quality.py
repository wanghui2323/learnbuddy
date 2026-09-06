"""内容质量契约（Quality Contract）。

LLM 可以负责生成，但 LearnBuddy 必须自己负责验货：这里用确定性规则检查
一节 lesson 是否足够像“可学习的教学链”，而不是只是一段浅讲义。
"""

from __future__ import annotations

import re
from typing import Any

REQUIRED_CARD_TYPES = ("goal", "source", "think", "example")
OUTPUT_CARD_TYPES = ("produce", "check")
CORE_CARD_TYPES = {"think", "explain", "visual", "compare", "example", "practice", "check"}
DEEP_TEACHING_CARD_TYPES = {"explain", "visual", "compare", "example", "practice", "check"}
MIN_TEACHING_CHARS = 1600
MIN_DENSE_TEACHING_CARDS = 3
MIN_EXPANSION_BLOCKS = 3
FULL_UNIT_MINUTES = 60
FULL_UNIT_MIN_TEACHING_CHARS = 3200
REQUIRED_EXPANSION_GROUPS = {
    "mechanism": {"mechanism"},
    "boundary": {"boundary", "contrast", "counterexample", "common_mistake"},
    "worked_example": {"worked_example"},
}
REQUIRED_FULL_UNIT_ACTIVITY_GROUPS = {
    "lecture": {"lecture", "explain", "concept", "teach", "reading"},
    "worked_example": {"worked_example", "example", "case", "demo"},
    "drill": {"drill", "practice", "exercise", "quiz"},
    "produce": {"produce", "output", "project", "design_task", "task"},
    "reflection": {"reflection", "review", "check", "self_check", "extension"},
}
CLASSROOM_TERMS = (
    "同学们", "各位同学", "老师会", "老师带", "课堂上", "上课时", "课后作业", "讲台", "授课",
)
RAW_PERSONALIZATION_LABELS = (
    "已具备：", "已具备:", "薄弱点：", "薄弱点:", "验收偏好：", "验收偏好:",
)
SELF_STUDY_TEMPLATE_KEYWORDS = (
    "模板", "架构图模板", "规则表", "检查清单", "评分表", "填写表", "表格模板",
)
REFERENCE_ANSWER_KEYWORDS = (
    "参考答案", "参考样例", "参考方案", "样例产物", "示例产物", "示范答案", "合格示例", "对照答案", "样例方案",
)
HIGH_ORDER_PRACTICE_KEYWORDS = (
    "批判", "挑错", "找出问题", "修复一个", "修复错误", "错误方案", "改进方案", "设计评审",
    "反例分析", "边界诊断", "故障定位", "debug", "critique", "repair", "design review",
)
DEPTH_KEYWORDS = (
    "判断", "规则", "标准", "边界", "反例", "例子", "例如", "比如", "误区", "易错", "区别",
    "什么时候", "如果", "因为", "所以", "不能", "应该", "验收", "自查", "criteria",
    "rule", "boundary", "edge case", "counterexample", "example", "because", "mistake",
)
SOURCE_CLAIM_PATTERN = re.compile(
    r"(基于|依据|参考|来自).{0,24}(官方文档|论文|教材|书籍|文献|OpenAI|LangChain|AutoGen|arXiv|IEEE|ACM)",
    re.I,
)
URL_PATTERN = re.compile(r"https?://", re.I)
ASCII_DIAGRAM_CHARS = set("+-=|<>^v/\\┌┐└┘├┤┬┴┼─━│")


def _text(value: Any, limit: int = 8000) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return " ".join(_text(v, limit) for v in value.values())[:limit]
    if isinstance(value, list):
        return " ".join(_text(v, limit) for v in value)[:limit]
    return str(value).strip()[:limit]


def _items_count(card: dict) -> int:
    items = card.get("items") or []
    return len(items) if isinstance(items, list) else 0


def _has_interaction(card: dict) -> bool:
    options = card.get("options") or []
    if not isinstance(options, list) or len([x for x in options if str(x).strip()]) < 2:
        return False
    try:
        answer = int(card.get("answer"))
    except (TypeError, ValueError):
        return False
    if answer < 0 or answer >= len(options):
        return False
    return len(_text(card.get("why"))) >= 12


def _has_ascii_diagram(text: str) -> bool:
    lines = str(text or "").splitlines()
    for line in lines:
        s = line.strip()
        if len(s) < 10:
            continue
        count = sum(1 for ch in s if ch in ASCII_DIAGRAM_CHARS)
        if count >= 6 and count / max(1, len(s)) > 0.32:
            return True
    return False


def _depth_units(card: dict) -> int:
    body = _text(card.get("body"))
    whole = _text(card)
    units = 0
    if len(body) >= 70 or len(whole) >= 120:
        units += 1
    if _items_count(card) >= 2:
        units += 1
    if any(k.lower() in whole.lower() for k in DEPTH_KEYWORDS):
        units += 1
    if _has_interaction(card):
        units += 1
    return units


def _teaching_chars(value: Any) -> int:
    """估算真正用于教学的字符量；过滤 source/citation 这类元信息。"""
    return len(_text(value, 12000).replace(" ", "").replace("\n", ""))


def _card_teaching_chars(card: dict) -> int:
    fields = {
        "body": card.get("body"),
        "items": card.get("items"),
        "expansions": card.get("expansions"),
        "why": card.get("why"),
        "completion_rule": card.get("completion_rule"),
        "expected_signal": card.get("expected_signal"),
    }
    return _teaching_chars(fields)


def _card_expansions(card: dict) -> list[dict]:
    expansions = card.get("expansions") or []
    if not isinstance(expansions, list):
        return []
    return [x for x in expansions if isinstance(x, dict)]


def _valid_expansion_kinds(card: dict) -> list[str]:
    kinds = []
    for block in _card_expansions(card):
        kind = str(block.get("kind") or block.get("type") or "").strip().lower()
        body = _text({"body": block.get("body"), "items": block.get("items")}, 6000)
        if not kind or len(body.replace(" ", "").replace("\n", "")) < 120:
            continue
        kinds.append(kind)
    return kinds


def _expansion_stats(cards: list[dict]) -> tuple[int, set[str], list[str]]:
    blocks = []
    titles = []
    for card in cards:
        ctype = str(card.get("type") or "").strip().lower()
        if ctype not in DEEP_TEACHING_CARD_TYPES:
            continue
        for kind in _valid_expansion_kinds(card):
            blocks.append(kind)
            title = _text(card.get("title") or kind, 80)
            if title:
                titles.append(title)
    return len(blocks), set(blocks), titles


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _session_target_minutes(lesson: dict) -> int:
    contract = lesson.get("_session_contract") if isinstance(lesson.get("_session_contract"), dict) else {}
    return _safe_int(contract.get("target_minutes")) if contract else 0


def _session_min_estimated_minutes(lesson: dict, target_minutes: int) -> int:
    contract = lesson.get("_session_contract") if isinstance(lesson.get("_session_contract"), dict) else {}
    configured = _safe_int(contract.get("min_estimated_minutes")) if contract else 0
    if configured > 0:
        return configured
    if target_minutes >= FULL_UNIT_MINUTES:
        return max(FULL_UNIT_MINUTES, int(target_minutes * 0.7))
    return 0


def _activity_plan_stats(lesson: dict) -> dict[str, Any]:
    plan = lesson.get("activity_plan") or []
    if not isinstance(plan, list):
        plan = []
    blocks = [x for x in plan if isinstance(x, dict)]
    activity_types: set[str] = set()
    total_minutes = 0
    useful_blocks = 0
    for block in blocks:
        kind = str(block.get("type") or block.get("kind") or "").strip().lower()
        if kind:
            activity_types.add(kind)
        minutes = max(0, _safe_int(block.get("minutes") or block.get("duration_minutes")))
        total_minutes += minutes
        task = _text(block.get("task") or block.get("body") or block.get("items"), 2000)
        if minutes >= 5 and len(task.replace(" ", "").replace("\n", "")) >= 20:
            useful_blocks += 1
    missing_groups = []
    for group, aliases in REQUIRED_FULL_UNIT_ACTIVITY_GROUPS.items():
        if not (activity_types & aliases):
            missing_groups.append(group)
    return {
        "activity_blocks": len(blocks),
        "useful_activity_blocks": useful_blocks,
        "activity_minutes": total_minutes,
        "activity_types": sorted(activity_types),
        "missing_activity_groups": missing_groups,
    }


def _learner_facing_text(lesson: dict) -> str:
    """只检查用户能看到的内容，避开 _learner_signals 这类内部观测字段。"""
    payload = {
        "explain": lesson.get("explain"),
        "cards": lesson.get("cards"),
        "produce": lesson.get("produce"),
        "practice": lesson.get("practice"),
        "activity_plan": lesson.get("activity_plan"),
        "rubric": lesson.get("rubric"),
        "lab": lesson.get("lab"),
    }
    return _text(payload, 30000)


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    lower = text.lower()
    return any(k.lower() in lower for k in keywords)


def _self_study_stats(lesson: dict, *, target_minutes: int) -> tuple[list[str], dict[str, Any]]:
    text = _learner_facing_text(lesson)
    classroom_terms = [term for term in CLASSROOM_TERMS if term in text]
    raw_labels = [label for label in RAW_PERSONALIZATION_LABELS if label in text]
    has_template = _contains_any(text, SELF_STUDY_TEMPLATE_KEYWORDS)
    has_reference_answer = _contains_any(text, REFERENCE_ANSWER_KEYWORDS)
    has_high_order_practice = _contains_any(text, HIGH_ORDER_PRACTICE_KEYWORDS)
    checks = {
        "self_study_template": has_template,
        "reference_answer": has_reference_answer,
        "high_order_practice": has_high_order_practice,
        "classroom_terms": classroom_terms,
        "raw_personalization_labels": raw_labels,
    }
    issues: list[str] = []
    if classroom_terms:
        issues.append("用户可见内容出现课堂话术：" + "、".join(classroom_terms[:4]) + "。")
    if raw_labels:
        issues.append("用户可见内容暴露内部学员标签：" + "、".join(raw_labels[:4]) + "；个性化应自然落在任务和验收标准里。")
    if target_minutes >= FULL_UNIT_MINUTES:
        if not has_template:
            issues.append("60 分钟以上单元缺少可直接填写的模板、表格、架构图或检查清单。")
        if not has_reference_answer:
            issues.append("60 分钟以上单元缺少参考答案、样例产物或合格示例，学习者无法对照修改自己的产出。")
        if not has_high_order_practice:
            issues.append("60 分钟以上单元缺少批判、修复、设计评审或边界诊断类高阶练习。")
    return issues, checks


def _duration_contract_issues(lesson: dict) -> tuple[list[str], dict[str, Any]]:
    target_minutes = _session_target_minutes(lesson)
    estimated = _safe_int(lesson.get("estimated_minutes"))
    min_estimated = _session_min_estimated_minutes(lesson, target_minutes)
    activity = _activity_plan_stats(lesson)
    checks = {
        "target_minutes": target_minutes,
        "estimated_minutes": estimated,
        "min_estimated_minutes": min_estimated,
        **activity,
    }
    if target_minutes < FULL_UNIT_MINUTES:
        return [], checks

    issues: list[str] = []
    if estimated < min_estimated:
        issues.append(
            f"本节目标学习时长约 {target_minutes} 分钟，但 estimated_minutes 只有 {estimated or 0}，无法支撑该学习时段。"
        )
    if activity["activity_blocks"] < 5 or activity["useful_activity_blocks"] < 5:
        issues.append("学习活动链少于 5 段：1–2 小时单元需要讲解、例题、练习、产出和自查。")
    if activity["activity_minutes"] < min_estimated:
        issues.append(
            f"activity_plan 合计只有 {activity['activity_minutes']} 分钟，低于本节最低有效学习时长 {min_estimated} 分钟。"
        )
    if activity["missing_activity_groups"]:
        label = {
            "lecture": "讲解",
            "worked_example": "完整例题",
            "drill": "分层练习",
            "produce": "主动产出",
            "reflection": "回顾自查/延展",
        }
        issues.append(
            "学习活动链缺少：" + "、".join(label.get(x, x) for x in activity["missing_activity_groups"]) + "。"
        )
    produce = lesson.get("produce") or {}
    if not isinstance(produce, dict) or len(_text(produce.get("task")).replace(" ", "")) < 80:
        issues.append("1–2 小时单元的 produce.task 过短，应要求可检查的设计、推演、改写、案例分析或小项目产物。")
    rubric = lesson.get("rubric") or []
    if not isinstance(rubric, list) or len([x for x in rubric if _text(x)]) < 3:
        issues.append("1–2 小时单元缺少至少 3 条产出评分/自查 rubric，学习者无法判断是否完成。")
    return issues, checks


def _is_dense_teaching_card(card: dict) -> bool:
    """核心深讲卡：不能只靠标题/选项/关键词刷过，必须有足够展开。"""
    ctype = str(card.get("type") or "").strip().lower()
    if ctype not in DEEP_TEACHING_CARD_TYPES:
        return False
    chars = _card_teaching_chars(card)
    items = _items_count(card)
    expansion_kinds = set(_valid_expansion_kinds(card))
    expansion_count = len(expansion_kinds)
    has_mechanism = "mechanism" in expansion_kinds
    has_boundary = bool(expansion_kinds & {"boundary", "contrast", "counterexample", "common_mistake"})
    has_worked = "worked_example" in expansion_kinds
    if ctype in {"visual", "compare"}:
        return chars >= 300 and (items >= 3 or expansion_count >= 2 or (has_mechanism and has_boundary))
    if ctype == "example":
        return chars >= 300 and (items >= 2 or has_worked or "counterexample" in expansion_kinds)
    if ctype == "practice":
        return chars >= 220 and (_has_interaction(card) or items >= 2 or expansion_count >= 1)
    if ctype == "check":
        return chars >= 220 and (items >= 3 or expansion_count >= 1)
    if expansion_count >= 1:
        return chars >= 300 and (has_mechanism or has_boundary or has_worked)
    return chars >= 260 and _depth_units(card) >= 3


def _card_types(cards: list[dict]) -> set[str]:
    return {str(c.get("type") or "").strip().lower() for c in cards if isinstance(c, dict)}


def _practice_issues(practice: list[dict]) -> list[str]:
    issues = []
    if len(practice) < 2:
        issues.append("练习题少于 2 道，难以判断学习者是否真的理解。")
    for i, q in enumerate(practice[:6], 1):
        options = q.get("options") or []
        why = _text(q.get("why"))
        try:
            answer = int(q.get("answer"))
        except (TypeError, ValueError):
            answer = -1
        if not _text(q.get("q")):
            issues.append(f"第 {i} 道练习缺少题干。")
        if not isinstance(options, list) or len([x for x in options if str(x).strip()]) < 3:
            issues.append(f"第 {i} 道练习选项少于 3 个，干扰项不足。")
        if answer < 0 or answer >= len(options):
            issues.append(f"第 {i} 道练习 answer 下标不合法。")
        if len(why) < 16:
            issues.append(f"第 {i} 道练习缺少有效解析。")
    return issues


def _design_issues(design: dict) -> list[str]:
    issues = []
    if not isinstance(design, dict) or not design:
        return ["缺少教学设计摘要，用户难以判断本节到底学什么。"]
    if len(_text(design.get("learning_question"))) < 12:
        issues.append("教学设计缺少明确的本节核心问题。")
    if len(_text(design.get("outcome"))) < 12:
        issues.append("教学设计缺少可观察的学习产出。")
    if len(_text(design.get("main_thread"))) < 12:
        issues.append("教学设计缺少贯穿案例/任务主线。")
    key_steps = design.get("key_steps") or []
    if not isinstance(key_steps, list) or len([s for s in key_steps if isinstance(s, dict)]) < 3:
        issues.append("教学设计关键步骤少于 3 个，认知递进不清楚。")
    else:
        for i, step in enumerate(key_steps[:5], 1):
            if not isinstance(step, dict):
                continue
            if len(_text(step.get("learner_action"))) < 8:
                issues.append(f"第 {i} 个关键步骤缺少学习者可观察动作。")
                break
            if len(_text(step.get("stuck_point"))) < 8:
                issues.append(f"第 {i} 个关键步骤缺少具体卡点描述。")
                break
    quick_checks = design.get("quick_checks") or []
    if not isinstance(quick_checks, list) or len([x for x in quick_checks if _text(x)]) < 2:
        issues.append("教学设计缺少短平快的快速验证点。")
    return issues


def _dimension_scores(
    *,
    cards_count: int,
    shallow_count: int,
    interactive_count: int,
    practice_count: int,
    has_output: bool,
    design_issue_count: int,
    teaching_chars: int,
    dense_teaching_count: int,
    has_context: bool,
    has_source_mark: bool,
    has_fake_source_issue: bool,
    expansion_blocks: int,
    missing_expansion_groups: list[str],
    duration_issue_count: int,
    self_study_issue_count: int,
) -> dict[str, dict[str, Any]]:
    """面向前端/后台的可解释维度分，不替代总分。"""

    def clamp(v: int) -> int:
        return max(0, min(100, int(v)))

    accuracy = 84 if has_context and has_source_mark else (68 if not has_fake_source_issue else 25)
    source_coverage = 86 if has_context and has_source_mark else (52 if not has_context else 38)
    depth = 92 - min(54, shallow_count * 12) - (12 if cards_count < 6 else 0)
    if teaching_chars < MIN_TEACHING_CHARS:
        depth -= 18
    if dense_teaching_count < MIN_DENSE_TEACHING_CARDS:
        depth -= 16
    if expansion_blocks < MIN_EXPANSION_BLOCKS:
        depth -= 14
    if missing_expansion_groups:
        depth -= 10
    interactivity = 42 + min(46, interactive_count * 28 + practice_count * 9)
    actionability = 84 if has_output else 46
    rhythm = 86 if cards_count >= 6 and has_output and practice_count >= 2 else 56
    if duration_issue_count:
        depth -= min(18, duration_issue_count * 6)
        actionability -= min(18, duration_issue_count * 6)
        rhythm -= min(24, duration_issue_count * 8)
    if self_study_issue_count:
        actionability -= min(24, self_study_issue_count * 8)
        rhythm -= min(18, self_study_issue_count * 6)
    pedagogy = 90 - min(54, design_issue_count * 12)
    self_study_fit = 92 - min(60, self_study_issue_count * 14) - min(12, duration_issue_count * 4)
    return {
        "accuracy": {"score": clamp(accuracy), "note": "事实与边界是否可靠，是否避免伪造来源。"},
        "depth": {"score": clamp(depth), "note": "是否有判断规则、例子/反例、边界和自查标准。"},
        "pedagogy": {"score": clamp(pedagogy), "note": "是否先完成教学设计：核心问题、主线、步骤、卡点和快速验证。"},
        "source_coverage": {"score": clamp(source_coverage), "note": "是否把参考资料真正纳入讲解依据。"},
        "interactivity": {"score": clamp(interactivity), "note": "是否有互动判断、练习和解释反馈。"},
        "actionability": {"score": clamp(actionability), "note": "是否要求学习者产生可观察输出。"},
        "rhythm": {"score": clamp(rhythm), "note": "学习链是否完整，节奏是否从理解走向练习和收束。"},
        "self_study_fit": {"score": clamp(self_study_fit), "note": "是否像一份可独立执行的学习工作单，而不是课堂讲义或内部画像展示。"},
    }


def evaluate_lesson_quality(
    lesson: dict,
    *,
    context: str = "",
    min_score: int = 78,
) -> dict:
    """返回一份确定性质量报告。

    passed=False 并不代表 lesson 不能展示，而是说明它没有达到 LearnBuddy
    的“高质量教学内容”门槛，生成 workflow 应尝试修订。
    """
    issues: list[str] = []
    checks: dict[str, Any] = {}
    cards = lesson.get("cards") or []
    cards = [c for c in cards if isinstance(c, dict)]
    types = _card_types(cards)
    score = 100

    design_report = _design_issues(lesson.get("design") or {})
    if design_report:
        issues.extend(design_report[:5])
        score -= min(24, 6 * len(design_report))
    checks["design_issues"] = len(design_report)

    checks["cards_count"] = len(cards)
    if len(cards) < 6:
        issues.append("教学卡片少于 6 张，学习链不完整。")
        score -= 18

    missing = [t for t in REQUIRED_CARD_TYPES if t not in types]
    if missing:
        issues.append("缺少关键教学卡片：" + "、".join(missing) + "。")
        score -= 8 * len(missing)
    if not any(t in types for t in OUTPUT_CARD_TYPES):
        issues.append("缺少 produce/check 输出或自检卡，学习者没有明确产出动作。")
        score -= 12

    interactive_cards = [c for c in cards if _has_interaction(c)]
    checks["interactive_cards"] = len(interactive_cards)
    if not interactive_cards:
        issues.append("缺少带选项、答案和解释的互动卡。")
        score -= 14

    shallow_titles = []
    ascii_diagram_titles = []
    for card in cards:
        ctype = str(card.get("type") or "").strip().lower()
        if ctype in CORE_CARD_TYPES and _depth_units(card) < 2:
            shallow_titles.append(_text(card.get("title") or ctype, 60))
        if ctype in {"visual", "compare"} and _has_ascii_diagram(_text(card.get("body"), 5000)):
            ascii_diagram_titles.append(_text(card.get("title") or ctype, 60))
    if shallow_titles:
        shown = "、".join(x for x in shallow_titles[:4] if x)
        issues.append(f"部分核心卡片过浅：{shown}。")
        score -= min(28, 7 * len(shallow_titles))
    checks["shallow_core_cards"] = len(shallow_titles)
    if ascii_diagram_titles:
        shown = "、".join(x for x in ascii_diagram_titles[:3] if x)
        issues.append(f"图解/对比卡使用 ASCII 宽图，移动端难以阅读：{shown}。")
        score -= min(16, 8 * len(ascii_diagram_titles))
    checks["ascii_diagram_cards"] = len(ascii_diagram_titles)

    teaching_chars = _teaching_chars(lesson.get("explain") or {})
    dense_teaching_titles = []
    for card in cards:
        ctype = str(card.get("type") or "").strip().lower()
        if ctype != "source":
            teaching_chars += _card_teaching_chars(card)
        if _is_dense_teaching_card(card):
            dense_teaching_titles.append(_text(card.get("title") or ctype, 60))
    checks["teaching_chars"] = teaching_chars
    checks["dense_teaching_cards"] = len(dense_teaching_titles)
    if teaching_chars < MIN_TEACHING_CHARS:
        issues.append(
            f"核心教学内容总量不足（{teaching_chars} 字符），容易变成提纲而不是可学习讲解。"
        )
        score -= 18
    if len(dense_teaching_titles) < MIN_DENSE_TEACHING_CARDS:
        issues.append("深讲卡少于 3 张：至少需要机制/边界/例子等核心卡展开到可独立学习。")
        score -= 18

    expansion_blocks, expansion_kinds, expansion_titles = _expansion_stats(cards)
    missing_expansion_groups = [
        group
        for group, aliases in REQUIRED_EXPANSION_GROUPS.items()
        if not (expansion_kinds & aliases)
    ]
    checks["expansion_blocks"] = expansion_blocks
    checks["expansion_kinds"] = sorted(expansion_kinds)
    if expansion_blocks < MIN_EXPANSION_BLOCKS:
        issues.append("可展开信息层少于 3 块：核心卡需要补机制、边界、worked example 等展开内容。")
        score -= 14
    if missing_expansion_groups:
        label = {
            "mechanism": "机制推演",
            "boundary": "边界/反例",
            "worked_example": "完整例题",
        }
        issues.append(
            "可展开信息层缺少：" + "、".join(label.get(x, x) for x in missing_expansion_groups) + "。"
        )
        score -= min(18, 7 * len(missing_expansion_groups))
    if expansion_titles:
        checks["expansion_titles"] = expansion_titles[:6]

    explain = lesson.get("explain") or {}
    examples = explain.get("examples") or []
    if not isinstance(examples, list) or len([e for e in examples if _text(e)]) < 2:
        issues.append("讲解例子少于 2 个，缺少正反/边界示范。")
        score -= 8

    practice = lesson.get("practice") or []
    practice = [q for q in practice if isinstance(q, dict)]
    p_issues = _practice_issues(practice)
    if p_issues:
        issues.extend(p_issues[:5])
        score -= min(22, 6 * len(p_issues))
    checks["practice_count"] = len(practice)

    produce = lesson.get("produce") or {}
    if not isinstance(produce, dict) or len(_text(produce.get("task"))) < 20:
        issues.append("主动产出任务不够明确，无法驱动学习者用自己的话输出。")
        score -= 10

    duration_issues, duration_checks = _duration_contract_issues(lesson)
    if duration_checks:
        checks.update(duration_checks)
    self_study_issues, self_study_checks = _self_study_stats(
        lesson,
        target_minutes=_safe_int(duration_checks.get("target_minutes")),
    )
    checks.update(self_study_checks)
    if duration_checks.get("target_minutes", 0) >= FULL_UNIT_MINUTES and teaching_chars < FULL_UNIT_MIN_TEACHING_CHARS:
        issues.append(
            f"1–2 小时单元的核心教学内容不足（{teaching_chars} 字符），需要更完整的机制、边界和案例展开。"
        )
        score -= 12
    if duration_issues:
        issues.extend(duration_issues[:6])
        score -= min(28, 7 * len(duration_issues))
    if self_study_issues:
        issues.extend(self_study_issues[:6])
        score -= min(32, 8 * len(self_study_issues))

    has_context = bool((context or "").strip())
    citations = [x for x in (lesson.get("citations") or []) if str(x).strip()]
    source_cards = [c for c in cards if str(c.get("type") or "").strip().lower() == "source"]
    source_text = " ".join(_text(c) for c in source_cards)
    if has_context:
        checks["source_mode"] = "grounded"
        if not citations and not any(_items_count(c) for c in source_cards):
            issues.append("已有参考资料，但 lesson 没有在 citations 或 source 卡中标明依据。")
            score -= 10
        if "暂无外部来源" in source_text or "无外部来源" in source_text:
            issues.append("已有参考资料时，source 卡不应写暂无外部来源。")
            score -= 8
    else:
        checks["source_mode"] = "ungrounded"
        all_text = _text(lesson)
        if citations:
            issues.append("本次没有参考资料，citations 应为空，不能伪造来源。")
            score -= 10
        if URL_PATTERN.search(all_text) or SOURCE_CLAIM_PATTERN.search(all_text):
            issues.append("本次没有参考资料，却出现了具体 URL 或资料依据表述。")
            score -= 12

    score = max(0, min(100, score))
    has_source_mark = bool(citations or any(_items_count(c) for c in source_cards))
    has_fake_source_issue = any("伪造来源" in x or "资料依据" in x for x in issues)
    has_output = bool(any(t in types for t in OUTPUT_CARD_TYPES)) or (
        isinstance(produce, dict) and len(_text(produce.get("task"))) >= 20
    )
    return {
        "passed": bool(
            score >= min_score
            and teaching_chars >= MIN_TEACHING_CHARS
            and (
                duration_checks.get("target_minutes", 0) < FULL_UNIT_MINUTES
                or teaching_chars >= FULL_UNIT_MIN_TEACHING_CHARS
            )
            and len(dense_teaching_titles) >= MIN_DENSE_TEACHING_CARDS
            and expansion_blocks >= MIN_EXPANSION_BLOCKS
            and not missing_expansion_groups
            and not ascii_diagram_titles
            and not duration_issues
            and not self_study_issues
            and not any("伪造来源" in x for x in issues)
        ),
        "score": score,
        "min_score": min_score,
        "issues": issues[:10],
        "checks": checks,
        "dimension_scores": _dimension_scores(
            cards_count=len(cards),
            shallow_count=len(shallow_titles),
            interactive_count=len(interactive_cards),
            practice_count=len(practice),
            has_output=has_output,
            design_issue_count=len(design_report),
            teaching_chars=teaching_chars,
            dense_teaching_count=len(dense_teaching_titles),
            has_context=has_context,
            has_source_mark=has_source_mark,
            has_fake_source_issue=has_fake_source_issue,
            expansion_blocks=expansion_blocks,
            missing_expansion_groups=missing_expansion_groups,
            duration_issue_count=len(duration_issues),
            self_study_issue_count=len(self_study_issues),
        ),
    }


def format_quality_issues(report: dict) -> str:
    """把本地质量门结果转成给修订 prompt 使用的短要求。"""
    issues = [str(x).strip() for x in (report.get("issues") or []) if str(x).strip()]
    lines = [
        f"本地内容质量门未通过：{int(report.get('score') or 0)}/100。",
        "请直接修订 lesson，必须解决以下问题：",
    ]
    lines.extend(f"- {x}" for x in issues[:8])
    lines.append("修订后仍保持原 schema；不要解释质检过程，只输出合格 lesson。")
    return "\n".join(lines)


__all__ = ["evaluate_lesson_quality", "format_quality_issues"]
