"""路径级 DeepBuild 单元测试。"""

from __future__ import annotations

from datetime import date

from core.generator import build_master, build_week_plan
from core.path_deepbuild import (
    build_generation_workflow,
    build_linear_concept_graph,
    build_outline_research,
    evaluate_outline_quality,
    refine_master_from_research,
)
from core.schemas import DomainCategory, Intensity, LearningMethods, UserParams, WeeklyTheme


def _params() -> UserParams:
    return UserParams(
        domain="AI Agent 应用/工作流",
        domain_category=DomainCategory.PROFESSIONAL,
        target="8 周内能交付一个企业内部 AI 助手 SaaS 原型",
        weeks=8,
        intensity=Intensity.STANDARD,
        weekday_hours=1,
        weekend_hours=2,
        baseline_summary="会 Python 和基础 API 调用，但缺少 Agent、RAG、评测和上线观测的系统工程经验。",
        preferences="希望结合真实工程案例，每周都有一个可运行的小闭环。",
        weekly_themes=[
            WeeklyTheme(week=1, title="Agent 最小闭环", key_outcomes=["跑通一次工具调用", "写出失败边界清单"]),
            WeeklyTheme(week=2, title="RAG 数据流", key_outcomes=["接入一个知识库", "做一次答案引用校验"]),
        ],
        learning_methods=LearningMethods(
            core_method="读文档 + 拆闭环 + 小项目验证",
            anti_forgetting=["每周复盘一次失败案例"],
            self_check_dimensions=["理解", "实现", "评测", "观测", "复盘", "迁移"],
        ),
    )


def test_outline_research_without_network_keeps_process_metadata():
    research = build_outline_research(_params(), allow_network=False)

    assert research["status"] == "pending_search"
    assert research["query_plan"]
    assert research["source_count"] == 0
    assert research["teaching_assumptions"]


def test_outline_research_keeps_user_knowledge_separate_from_web_sources():
    research = build_outline_research(
        _params(),
        allow_network=False,
        user_sources=[
            {
                "source_id": 7,
                "instance_id": "old-path",
                "title": "团队 Agent 复盘",
                "text": "真实项目里工具调用失败率是首要观测指标。",
                "score": 0.8,
            }
        ],
    )

    assert research["source_count"] == 0
    assert research["user_source_count"] == 1
    assert research["total_source_count"] == 1
    assert research["user_sources"][0]["ref_id"] == "user:7"
    assert research["signals"]["has_user_grounding"] is True


class _OutlineClient:
    model = "fake-outline"

    def __init__(self, weeks: int):
        self.weeks = weeks

    def chat(self, messages, **kwargs) -> str:
        import json

        return json.dumps(
            {
                "months": [{"month": 1, "title": "从单点到闭环", "deliverable": "可演示 Agent 原型"}],
                "weeks": [
                    {
                        "week": i,
                        "title": f"能力闭环 {i}",
                        "key_outcomes": [f"交付可运行示例 {i}", f"记录 3 条失败边界 {i}"],
                        "source_refs": ["user:7"] if i <= 4 else [],
                        "rationale": "按依赖顺序递进。",
                    }
                    for i in range(1, self.weeks + 1)
                ],
            },
            ensure_ascii=False,
        )


def test_refine_master_uses_research_and_preserves_schedule_contract():
    params = _params()
    base = build_master(params, date(2026, 6, 29))
    research = build_outline_research(
        params,
        user_sources=[{"source_id": 7, "title": "Agent 复盘", "text": "失败率观测"}],
    )

    refined, trace = refine_master_from_research(
        params,
        base,
        research,
        client=_OutlineClient(params.weeks),
    )

    assert refined["weeks"][0]["title"] == "能力闭环 1"
    assert refined["weeks"][0]["source_refs"] == ["user:7"]
    assert refined["weeks"][0]["date_range"] == base["weeks"][0]["date_range"]
    assert refined["weeks"][0]["plan_file"] == "W1.json"
    assert trace["user_sources_used"] == 1


def test_outline_quality_blocks_sources_that_are_not_linked_to_weeks():
    params = _params()
    master = build_master(params, date(2026, 6, 29))
    for week in master["weeks"]:
        week["title"] = f"能力闭环 {week['week']}"
        week["key_outcomes"] = ["交付一个可运行示例", "写出三条失败边界"]
    w1 = build_week_plan(params, date(2026, 6, 29), master=master)
    research = build_outline_research(
        params,
        user_sources=[{"source_id": 7, "title": "Agent 复盘", "text": "失败率观测"}],
    )
    graph = build_linear_concept_graph(master)

    quality = evaluate_outline_quality(params, master, w1, research, graph)

    assert quality["passed"] is False
    assert "research_linkage" in quality["critical_failures"]
    assert quality["checks"]["grounded_weeks"] == []


def test_outline_quality_scores_master_and_concept_graph():
    params = _params()
    master = build_master(params, date(2026, 6, 29))
    w1 = build_week_plan(params, date(2026, 6, 29), week_idx=1)
    research = build_outline_research(params, allow_network=False)
    graph = build_linear_concept_graph(master)

    quality = evaluate_outline_quality(params, master, w1, research, graph)
    workflow = build_generation_workflow(research, quality, graph)

    assert quality["score"] > 0
    assert quality["dimension_scores"]["concept_coverage"]["score"] > 0
    assert quality["dimension_scores"]["schedule_fit"]["score"] > 0
    assert quality["checks"]["concept_count"] == len(graph["concepts"])
    assert quality["checks"]["w1_review_only_weekdays"] == []
    assert workflow["stages"][-1]["id"] == "quality_gate"


def test_outline_quality_flags_review_only_weekdays():
    params = _params()
    master = build_master(params, date(2026, 6, 29))
    w1 = build_week_plan(params, date(2026, 6, 29), week_idx=1).model_dump(mode="json")
    research = {
        "source_count": 3,
        "query_plan": ["AI Agent memory curriculum", "RAG memory design"],
    }
    graph = build_linear_concept_graph(master)
    w1["days"][1]["slots"] = [
        {
            "time": "晚 20:00-21:30",
            "task": "轻量消化：Anki 推送 + 30 分钟复习昨日重点",
            "output": "复习笔记",
        }
    ]
    w1["days"][3]["slots"] = [
        {
            "time": "晚 20:00-21:30",
            "task": "轻量消化：Anki 推送 + 30 分钟复习昨日重点",
            "output": "复习笔记",
        }
    ]

    quality = evaluate_outline_quality(params, master, w1, research, graph, min_score=95)

    assert quality["passed"] is False
    assert quality["checks"]["w1_review_only_weekdays"] == [2, 4]
    assert any("工作日整天复习" in issue for issue in quality["issues"])
    assert quality["dimension_scores"]["schedule_fit"]["score"] < 90
