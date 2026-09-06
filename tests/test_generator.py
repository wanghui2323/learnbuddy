"""core.generator 单元测试（不依赖真实 LLM）。

跑法：
    pytest tests/test_generator.py -v
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from core.generator import (
    build_learning_manual,
    build_master,
    build_vision_contract,
    build_week_plan,
    generate_instance,
)
from core.schemas import (
    AbilityDimensionScore,
    BaselineProfile,
    DomainCategory,
    Instance,
    InstanceStatus,
    Intensity,
    LearningMethods,
    Message,
    Milestone,
    UserParams,
    WeekPlan,
    WeeklyTheme,
)


# ============================================================================
# 测试 fixture
# ============================================================================


def _japanese_n3_params() -> UserParams:
    """共享的日语 N3 示例参数包。"""
    return UserParams(
        domain="日语 N3 备考",
        domain_category=DomainCategory.LANGUAGE,
        target="3 个月内通过 JLPT N3",
        weeks=12,
        intensity=Intensity.STANDARD,
        weekday_hours=2,
        weekend_hours=4,
        baseline_summary="N5 水平，能识平假/片假名，词汇约 600，未学语法",
        preferences="重听说，每周 1 次外教对话",
        milestones=[
            Milestone(month=1, title="N5 巩固 + N4 词汇", deliverable="词汇量 1500+"),
            Milestone(month=2, title="N4 语法体系", deliverable="N4 模考 ≥ 80%"),
            Milestone(month=3, title="N3 冲刺", deliverable="N3 模考 ≥ 65%"),
        ],
        weekly_themes=[
            WeeklyTheme(week=1, title="起跑 + 节奏建立", key_outcomes=["完成 1 次 N5 模考", "Anki 累计 ≥ 200 张"]),
            WeeklyTheme(week=2, title="N4 词汇冲刺第 1 周", key_outcomes=["新词 300 + 复习 200"]),
        ],
        learning_methods=LearningMethods(
            core_method="背 + 造句 + 影子跟读",
            anti_forgetting=[
                "每日 Anki 单词推送 30 张",
                "每周写日记 1 篇（≥150 字）",
                "周日整周回顾 + 错题二刷",
                "月底把 4 周内容串成思维导图",
                "每月给朋友讲 1 次本月学到的内容",
            ],
            self_check_dimensions=["听", "说", "读", "写", "翻译", "教别人"],
        ),
    )


class FakeChatClient:
    """假 LLM client：返回预设 chat 文本。"""

    def __init__(self, reply: str):
        self.reply = reply
        self.last_messages: list[Message] = []
        self.model = "fake"

    def chat(self, messages, **kwargs) -> str:
        self.last_messages = list(messages)
        return self.reply


# ============================================================================
# build_master
# ============================================================================


class TestBuildMaster:
    def test_basic_structure(self):
        params = _japanese_n3_params()
        master = build_master(params, start_date=date(2026, 5, 25))

        assert master["version"] == "0.0.1-itutor-generated"
        assert master["lock_status"] == "draft"
        assert master["north_star"]["domain"] == "日语 N3 备考"
        assert master["north_star"]["weeks"] == 12
        assert master["totals"]["total_weeks"] == 12
        assert master["totals"]["total_months"] == 3

    def test_months_count(self):
        params = _japanese_n3_params()
        master = build_master(params, start_date=date(2026, 5, 25))
        assert len(master["months"]) == 3
        assert master["months"][0]["id"] == "M1"
        assert master["months"][0]["title"] == "N5 巩固 + N4 词汇"
        assert master["months"][2]["title"] == "N3 冲刺"

    def test_weeks_count(self):
        params = _japanese_n3_params()
        master = build_master(params, start_date=date(2026, 5, 25))
        assert len(master["weeks"]) == 12
        assert master["weeks"][0]["week"] == 1
        assert master["weeks"][0]["title"] == "起跑 + 节奏建立"
        assert master["weeks"][0]["status"] == "active"
        assert master["weeks"][1]["status"] == "pending"

    def test_weeks_grouped_by_month(self):
        """W1-W4 → M1, W5-W8 → M2, W9-W12 → M3。"""
        params = _japanese_n3_params()
        master = build_master(params, start_date=date(2026, 5, 25))

        assert master["weeks"][0]["month"] == "M1"
        assert master["weeks"][3]["month"] == "M1"
        assert master["weeks"][4]["month"] == "M2"
        assert master["weeks"][7]["month"] == "M2"
        assert master["weeks"][8]["month"] == "M3"
        assert master["weeks"][11]["month"] == "M3"

    def test_short_weeks(self):
        """4 周路径只 1 个月。"""
        params = _japanese_n3_params()
        params.weeks = 4
        master = build_master(params, start_date=date(2026, 5, 25))
        assert master["totals"]["total_months"] == 1
        assert len(master["months"]) == 1
        assert len(master["weeks"]) == 4

    def test_structured_baseline_is_written_to_north_star(self):
        params = _japanese_n3_params()
        params.baseline_profile = BaselineProfile(
            assessment_id="ba_master",
            summary="独立 2.0/5，AI 协作 4.0/5。",
            independent_score=2,
            with_ai_score=4,
            gap=2,
            completed_at="2026-07-10T10:00:00",
        )

        master = build_master(params, start_date=date(2026, 5, 25))

        assert master["north_star"]["baseline_profile"]["assessment_id"] == "ba_master"
        assert master["north_star"]["baseline_profile"]["gap"] == 2

    def test_observed_baseline_creates_evidence_backed_path_decision(self):
        params = _japanese_n3_params()
        params.baseline_profile = BaselineProfile(
            assessment_id="ba_observed",
            profile_id="bp_observed",
            contract_version="baseline.v0.53",
            assessment_method="observed_v2",
            evidence_level="observed",
            status="completed",
            summary="独立底座需要补强。",
            independent_score=2.5,
            with_ai_score=4,
            gap=1.5,
            observed_independent_score=50,
            observed_with_ai_score=80,
            profile_confidence=0.9,
            abilities=[
                AbilityDimensionScore(
                    key="independent_foundation",
                    label="独立底座",
                    score=45,
                    confidence=0.9,
                    evidence_ids=["se_foundation"],
                ),
                AbilityDimensionScore(
                    key="ai_collaboration",
                    label="AI 协作",
                    score=80,
                    confidence=0.9,
                    evidence_ids=["se_ai"],
                ),
            ],
            completed_at="2026-07-11T10:00:00",
        )

        master = build_master(params, start_date=date(2026, 7, 13))
        decisions = master["north_star"]["baseline_decisions"]

        assert decisions[0]["rule"] == "foundation_repair"
        assert decisions[0]["evidence_ids"] == ["se_foundation"]
        assert any(decisions[0]["action"] in item for item in master["weeks"][0]["key_outcomes"])


# ============================================================================
# build_week_plan
# ============================================================================


class TestBuildWeekPlan:
    def test_w1_has_seven_days(self):
        params = _japanese_n3_params()
        plan = build_week_plan(params, start_date=date(2026, 5, 25), week_idx=1)
        assert isinstance(plan, WeekPlan)
        assert plan.week == 1
        assert len(plan.days) == 7

    def test_weekday_assignment(self):
        """2026-05-25 是周一。"""
        params = _japanese_n3_params()
        plan = build_week_plan(params, start_date=date(2026, 5, 25), week_idx=1)
        assert plan.days[0].weekday == "周一"
        assert plan.days[0].is_weekend is False
        assert plan.days[5].weekday == "周六"
        assert plan.days[5].is_weekend is True
        assert plan.days[6].weekday == "周日"
        assert plan.days[6].is_weekend is True

    def test_planned_hours_match_params(self):
        params = _japanese_n3_params()
        plan = build_week_plan(params, start_date=date(2026, 5, 25), week_idx=1)
        # 工作日（周一-周五）planned_hours = weekday_hours = 2
        for d in plan.days[:5]:
            assert d.planned_hours == 2
        # 周末（周六-周日）planned_hours = weekend_hours = 4
        for d in plan.days[5:]:
            assert d.planned_hours == 4

    def test_weekdays_have_specific_progress_tasks(self):
        """工作日都应推进学习，复习只作为微复习嵌入。"""
        params = _japanese_n3_params()
        plan = build_week_plan(params, start_date=date(2026, 5, 25), week_idx=1)

        # 周一（idx 0）= 推进新内容
        assert "推进新内容" in plan.days[0].slots[0].task
        # 周二（idx 1）= 微复习 + 新内容推进，不是整天消化
        assert "新内容推进" in plan.days[1].slots[0].task
        # 周三（idx 2）= 巩固
        assert "巩固" in plan.days[2].slots[0].task
        # 周四（idx 3）= 错题回看 + 边界练习，不是整天消化
        assert "边界练习" in plan.days[3].slots[0].task
        # 周五（idx 4）= 实战 + 错题
        assert "实战" in plan.days[4].slots[0].task

    def test_weekdays_do_not_become_full_review_days(self):
        """周二/周四不能被固定模板变成整天复习日。"""
        params = _japanese_n3_params()
        plan = build_week_plan(params, start_date=date(2026, 5, 25), week_idx=1)
        weekday_tasks = [d.slots[0].task for d in plan.days[:5]]

        assert all("轻量消化" not in task for task in weekday_tasks)
        assert all("30 分钟复习昨日重点" not in task for task in weekday_tasks)
        assert "10 分钟" in plan.days[1].slots[0].task
        assert "10 分钟" in plan.days[3].slots[0].task

    def test_sunday_has_self_check(self):
        params = _japanese_n3_params()
        plan = build_week_plan(params, start_date=date(2026, 5, 25), week_idx=1)
        sunday_tasks = " | ".join(s.task for s in plan.days[6].slots)
        assert "6 关自检" in sunday_tasks

    def test_uses_weekly_theme_when_available(self):
        params = _japanese_n3_params()
        plan = build_week_plan(params, start_date=date(2026, 5, 25), week_idx=1)
        assert plan.title == "起跑 + 节奏建立"
        assert "完成 1 次 N5 模考" in plan.key_outcomes

    def test_falls_back_when_no_theme(self):
        """没有对应周的 weekly_theme 时给默认值。"""
        params = _japanese_n3_params()
        plan = build_week_plan(params, start_date=date(2026, 5, 25), week_idx=10)
        assert "W10" in plan.title
        assert len(plan.key_outcomes) >= 1

    def test_uses_final_master_instead_of_stale_conversation_theme(self):
        params = _japanese_n3_params()
        master = build_master(params, start_date=date(2026, 5, 25))
        master["weeks"][0]["title"] = "N3 语法诊断与补漏"
        master["weeks"][0]["key_outcomes"] = ["完成 30 题诊断", "归类 5 个薄弱语法"]

        plan = build_week_plan(
            params,
            start_date=date(2026, 5, 25),
            week_idx=1,
            master=master,
        )

        assert plan.title == "N3 语法诊断与补漏"
        assert plan.key_outcomes == ["完成 30 题诊断", "归类 5 个薄弱语法"]


# ============================================================================
# build_learning_manual
# ============================================================================


class TestBuildLearningManual:
    def test_no_llm_mode(self):
        """use_llm=False 时不调 LLM，仍输出完整 markdown。"""
        params = _japanese_n3_params()
        md = build_learning_manual(params, use_llm=False)

        assert "# 学习手册 · 日语 N3 备考" in md
        assert "3 个月内通过 JLPT N3" in md
        assert "反遗忘 5 机制" in md
        assert "6 关自检" in md
        # 5 个反遗忘机制都有
        assert "micro-review" in md
        assert "间隔重复" in md
        assert "周回测" in md
        assert "月度雪球" in md
        assert "解释关延迟" in md
        # 6 关自检表有 6 行
        # 提取自检表后面的 markdown 表格行数
        assert md.count("| 1 |") >= 1
        assert md.count("| 6 |") >= 1

    def test_llm_polish_with_fake_client(self):
        """use_llm=True 时调 fake client，应使用 LLM 输出的 steps + signals。"""
        params = _japanese_n3_params()
        fake_reply = json.dumps(
            {
                "core_method_steps": [
                    "每天 Anki 推 30 张 N3 词 + 复习全部到期",
                    "把当天背的词造 5 个例句，必含本周新语法",
                    "影子跟读 NHK Easy News 1 篇 3 遍",
                ],
                "self_check_pass_signals": [
                    "听：能听懂 NHK Easy 70%",
                    "说：5 分钟独白用本周语法",
                    "读：N3 阅读 ≥ 65%",
                    "写：日记 200 字错误 ≤ 3",
                    "翻译：中→日 5 句意思准确",
                    "教别人：讲 1 个语法点零基础懂",
                ],
            },
            ensure_ascii=False,
        )
        client = FakeChatClient(fake_reply)
        md = build_learning_manual(params, client=client, use_llm=True)

        assert "每天 Anki 推 30 张" in md
        assert "影子跟读 NHK Easy News" in md
        assert "听：能听懂 NHK Easy 70%" in md
        assert "教别人：讲 1 个语法点零基础懂" in md

    def test_llm_garbage_falls_back(self):
        """LLM 返回乱码时回退到默认填空，不抛异常。"""
        params = _japanese_n3_params()
        client = FakeChatClient("Sorry, I cannot do that.")
        md = build_learning_manual(params, client=client, use_llm=True)
        assert "# 学习手册 · 日语 N3 备考" in md
        # fallback signals 有 6 个
        for dim in ["听", "说", "读", "写", "翻译", "教别人"]:
            assert dim in md


# ============================================================================
# build_vision_contract
# ============================================================================


class TestBuildVisionContract:
    def test_basic(self):
        params = _japanese_n3_params()
        md = build_vision_contract(params)

        assert "# 愿景与契约 · 日语 N3 备考" in md
        assert "3 个月内通过 JLPT N3" in md
        assert "重听说，每周 1 次外教对话" in md
        assert "LearnBuddy 对你的承诺" in md
        assert "你对自己的承诺" in md
        assert "R1 周自检 ≤ 4 关" in md
        assert "R5 发现重要新内容/资源" in md

    def test_no_preferences(self):
        params = _japanese_n3_params()
        params.preferences = ""
        md = build_vision_contract(params)
        assert "（用户未在对话中明确说" in md


# ============================================================================
# generate_instance（端到端）
# ============================================================================


class TestGenerateInstance:
    def test_full_flow(self, tmp_path: Path):
        """端到端生成完整 4+1 件套。"""
        params = _japanese_n3_params()
        client = FakeChatClient(
            json.dumps(
                {
                    "core_method_steps": ["step 1", "step 2", "step 3"],
                    "self_check_pass_signals": [
                        "听: x",
                        "说: x",
                        "读: x",
                        "写: x",
                        "翻译: x",
                        "教别人: x",
                    ],
                },
                ensure_ascii=False,
            )
        )

        inst_dir = generate_instance(
            params,
            start_date=date(2026, 5, 25),
            instances_dir=tmp_path,
            llm_client=client,
        )

        assert inst_dir.exists()
        assert inst_dir.name == "japanese-n3"
        # 5 件套全部存在
        assert (inst_dir / "meta.json").exists()
        assert (inst_dir / "master.json").exists()
        assert (inst_dir / "W1.json").exists()
        assert (inst_dir / "concepts.json").exists()
        assert (inst_dir / "学习手册.md").exists()
        assert (inst_dir / "愿景与契约.md").exists()
        # 索引更新
        index_path = tmp_path / "_index.json"
        assert index_path.exists()
        index = json.loads(index_path.read_text(encoding="utf-8"))
        assert index["count"] == 1
        assert index["instances"][0]["id"] == "japanese-n3"
        assert index["instances"][0]["domain"] == "日语 N3 备考"

    def test_meta_json_valid(self, tmp_path: Path):
        params = _japanese_n3_params()
        inst_dir = generate_instance(
            params,
            start_date=date(2026, 5, 25),
            instances_dir=tmp_path,
            use_llm_for_manual=False,
        )
        meta = json.loads((inst_dir / "meta.json").read_text(encoding="utf-8"))
        instance = Instance.model_validate(meta)
        assert instance.id == "japanese-n3"
        assert instance.weeks == 12
        assert instance.status == InstanceStatus.DRAFT

    def test_collision_auto_renames(self, tmp_path: Path):
        """同一 domain 重复生成应自动追加 -2 / -3 后缀。"""
        params = _japanese_n3_params()
        d1 = generate_instance(params, start_date=date(2026, 5, 25), instances_dir=tmp_path, use_llm_for_manual=False)
        d2 = generate_instance(params, start_date=date(2026, 5, 25), instances_dir=tmp_path, use_llm_for_manual=False)
        d3 = generate_instance(params, start_date=date(2026, 5, 25), instances_dir=tmp_path, use_llm_for_manual=False)
        assert d1.name == "japanese-n3"
        assert d2.name == "japanese-n3-2"
        assert d3.name == "japanese-n3-3"

        index = json.loads((tmp_path / "_index.json").read_text(encoding="utf-8"))
        assert index["count"] == 3

    def test_master_json_valid(self, tmp_path: Path):
        params = _japanese_n3_params()
        inst_dir = generate_instance(
            params,
            start_date=date(2026, 5, 25),
            instances_dir=tmp_path,
            use_llm_for_manual=False,
        )
        master = json.loads((inst_dir / "master.json").read_text(encoding="utf-8"))
        assert master["totals"]["total_weeks"] == 12
        assert master["north_star"]["domain"] == "日语 N3 备考"
        assert master["_outline_research"]["query_plan"]
        assert master["_outline_quality"]["score"] > 0
        assert master["_generation_workflow"]["stages"]

    def test_concepts_json_generated_without_llm(self, tmp_path: Path):
        params = _japanese_n3_params()
        inst_dir = generate_instance(
            params,
            start_date=date(2026, 5, 25),
            instances_dir=tmp_path,
            use_llm_for_manual=False,
        )
        graph = json.loads((inst_dir / "concepts.json").read_text(encoding="utf-8"))
        assert graph["_source"] == "linear_deepbuild"
        assert len(graph["concepts"]) >= 2

    def test_w1_json_valid(self, tmp_path: Path):
        params = _japanese_n3_params()
        inst_dir = generate_instance(
            params,
            start_date=date(2026, 5, 25),
            instances_dir=tmp_path,
            use_llm_for_manual=False,
        )
        w1 = json.loads((inst_dir / "W1.json").read_text(encoding="utf-8"))
        plan = WeekPlan.model_validate(w1)
        assert plan.week == 1
        assert len(plan.days) == 7

    def test_default_start_date_is_next_monday(self, tmp_path: Path):
        """不传 start_date 时默认下周一。"""
        params = _japanese_n3_params()
        inst_dir = generate_instance(
            params,
            instances_dir=tmp_path,
            use_llm_for_manual=False,
        )
        meta = json.loads((inst_dir / "meta.json").read_text(encoding="utf-8"))
        instance = Instance.model_validate(meta)
        # 默认 start_date 是下周一（weekday=0）
        assert instance.start_date.weekday() == 0
        # 必须是未来 1-7 天
        delta = (instance.start_date - date.today()).days
        assert 1 <= delta <= 7

    def test_outline_contract_failure_removes_partial_instance(self, tmp_path: Path):
        params = _japanese_n3_params()

        with pytest.raises(RuntimeError, match="大纲"):
            generate_instance(
                params,
                start_date=date(2026, 5, 25),
                instances_dir=tmp_path,
                llm_client=FakeChatClient("这不是 JSON"),
                use_llm_for_manual=False,
                use_outline_llm=True,
                outline_max_revisions=0,
                require_outline_quality=True,
            )

        assert not (tmp_path / "japanese-n3").exists()
        assert not (tmp_path / "_index.json").exists()

    def test_final_master_and_w1_share_same_refined_outline(self, tmp_path: Path, monkeypatch):
        params = _japanese_n3_params()

        def fake_refine(params, master, research, **kwargs):
            for week in master["weeks"]:
                week["title"] = f"N3 阶段训练 {week['week']}"
                week["key_outcomes"] = [f"完成阶段题 {week['week']}", f"输出复盘卡 {week['week']}"]
            return master, {"status": "done", "user_sources_used": 0, "external_sources_used": 0}

        def fake_graph(inst_dir, master, **kwargs):
            from core.path_deepbuild import build_linear_concept_graph

            graph = build_linear_concept_graph(master)
            (inst_dir / "concepts.json").write_text(json.dumps(graph, ensure_ascii=False), encoding="utf-8")
            return graph

        monkeypatch.setattr("core.generator.refine_master_from_research", fake_refine)
        monkeypatch.setattr("core.generator.load_or_generate", fake_graph)
        inst_dir = generate_instance(
            params,
            start_date=date(2026, 5, 25),
            instances_dir=tmp_path,
            llm_client=FakeChatClient("{}"),
            use_llm_for_manual=False,
            use_outline_llm=True,
            use_outline_web_search=False,
            require_outline_quality=True,
        )

        master = json.loads((inst_dir / "master.json").read_text(encoding="utf-8"))
        w1 = json.loads((inst_dir / "W1.json").read_text(encoding="utf-8"))
        assert master["weeks"][0]["title"] == w1["title"]
        assert master["weeks"][0]["key_outcomes"] == w1["key_outcomes"]
        assert master["_outline_refinement"]["stop_reason"] == "quality_gate_passed"
