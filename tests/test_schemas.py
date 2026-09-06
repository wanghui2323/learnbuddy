"""core.schemas 数据模型单元测试（不需要 API key 即可跑）。

跑法：
    pytest tests/test_schemas.py -v
"""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from core.schemas import (
    BaselineProfile,
    DaySlot,
    DomainCategory,
    EvalQuestion,
    EvalResponse,
    Instance,
    InstanceStatus,
    Intensity,
    LearningMethods,
    Message,
    Milestone,
    UserParams,
    WeekDay,
    WeekPlan,
    WeeklyTheme,
)


# ============================================================================
# Message
# ============================================================================


class TestMessage:
    def test_basic(self):
        m = Message(role="user", content="你好")
        assert m.role == "user"
        assert m.content == "你好"

    def test_invalid_role(self):
        with pytest.raises(ValidationError):
            Message(role="bot", content="x")  # type: ignore[arg-type]


# ============================================================================
# EvalQuestion / EvalResponse
# ============================================================================


class TestEvalQuestion:
    def test_choice_question(self):
        q = EvalQuestion(
            id="q1",
            question="日语 「いらっしゃいませ」 通常出现在哪种场合？",
            type="choice",
            options=["商店欢迎客人", "电话问候", "正式信函", "葬礼"],
            expected_dimension="日常会话场景识别",
        )
        assert q.options is not None
        assert len(q.options) == 4

    def test_open_question_no_options(self):
        q = EvalQuestion(
            id="q2",
            question="解释一下 て形 在日语中的作用。",
            type="open",
            expected_dimension="语法理解",
        )
        assert q.options is None


class TestEvalResponse:
    def test_double_score(self):
        r = EvalResponse(
            question_id="q1",
            answer="商店欢迎客人",
            independent_score=4,
            with_ai_score=5,
        )
        assert r.independent_score == 4
        assert r.with_ai_score == 5

    def test_score_range(self):
        with pytest.raises(ValidationError):
            EvalResponse(question_id="q1", answer="...", independent_score=10)


# ============================================================================
# UserParams（核心模型）
# ============================================================================


class TestUserParams:
    def _japanese_n3_params(self) -> UserParams:
        return UserParams(
            domain="日语 N3 备考",
            domain_category=DomainCategory.LANGUAGE,
            target="3 个月内通过 JLPT N3",
            weeks=12,
            intensity=Intensity.STANDARD,
            weekday_hours=2,
            weekend_hours=4,
            baseline_summary="N5 水平，能识平假/片假，词汇约 600，未学语法",
            preferences="重听说，每周 1 次外教对话",
            milestones=[
                Milestone(month=1, title="N5 巩固 + N4 词汇", deliverable="词汇量 1500+"),
                Milestone(month=2, title="N4 语法体系", deliverable="N4 模考 ≥ 80%"),
                Milestone(month=3, title="N3 冲刺", deliverable="N3 模考 ≥ 65%"),
            ],
            weekly_themes=[
                WeeklyTheme(week=1, title="起跑 + 学习节奏建立", key_outcomes=["完成 1 次 N5 模考"]),
            ],
            learning_methods=LearningMethods(
                core_method="背 + 造句 + 影子跟读",
                anti_forgetting=["每日 Anki 单词", "每周写 1 篇日记", "周日整周回顾"],
                self_check_dimensions=["听", "说", "读", "写", "翻译", "教别人"],
            ),
        )

    def test_basic_construction(self):
        p = self._japanese_n3_params()
        assert p.domain == "日语 N3 备考"
        assert p.weeks == 12
        assert p.intensity == Intensity.STANDARD

    def test_auto_compute_weekly_total(self):
        """如未给 weekly_total_hours，应按 5×weekday + 2×weekend 自动算。"""
        p = UserParams(
            domain="x",
            target="x",
            weeks=4,
            weekday_hours=2,
            weekend_hours=4,
            baseline_summary="x",
        )
        assert p.weekly_total_hours == 2 * 5 + 4 * 2  # = 18

    def test_weeks_range(self):
        """V0 限定 4-24 周。"""
        with pytest.raises(ValidationError):
            UserParams(
                domain="x",
                target="x",
                weeks=2,  # 不到 4
                weekday_hours=2,
                weekend_hours=4,
                baseline_summary="x",
            )

    def test_json_roundtrip(self):
        """模型应能正常 dump 成 json 再 load 回来。"""
        p = self._japanese_n3_params()
        as_json = p.model_dump_json()
        assert "日语" in as_json
        p2 = UserParams.model_validate_json(as_json)
        assert p2.domain == p.domain
        assert p2.weeks == p.weeks
        assert p2.milestones[0].title == "N5 巩固 + N4 词汇"

    def test_optional_structured_baseline_roundtrip(self):
        p = self._japanese_n3_params()
        p.baseline_profile = BaselineProfile(
            assessment_id="ba_test",
            summary="独立 2.5/5，AI 协作 4.5/5。",
            independent_score=2.5,
            with_ai_score=4.5,
            gap=2.0,
            completed_at="2026-07-10T10:00:00",
        )

        p2 = UserParams.model_validate_json(p.model_dump_json())

        assert p2.baseline_profile is not None
        assert p2.baseline_profile.assessment_id == "ba_test"


# ============================================================================
# WeekDay / WeekPlan
# ============================================================================


class TestWeekPlan:
    def test_construction(self):
        plan = WeekPlan(
            week=1,
            title="起跑 + 学习节奏建立",
            date_range="2026-05-25 ~ 2026-05-31",
            key_outcomes=["完成 1 次 N5 模考", "Anki 累计 ≥ 200 张"],
            days=[
                WeekDay(
                    day=1,
                    date=date(2026, 5, 25),
                    weekday="周一",
                    is_weekend=False,
                    planned_hours=2,
                    slots=[
                        DaySlot(time="20:00-21:00", task="N5 模考", output="错题表"),
                        DaySlot(time="21:00-22:00", task="错题分析"),
                    ],
                )
            ],
        )
        assert plan.days[0].slots[0].task == "N5 模考"


# ============================================================================
# Instance
# ============================================================================


class TestInstance:
    def test_valid_id(self):
        inst = Instance(
            id="japanese-n3",
            domain="日语 N3 备考",
            domain_category=DomainCategory.LANGUAGE,
            target="3 个月通过 N3",
            weeks=12,
            intensity=Intensity.STANDARD,
            start_date=date(2026, 5, 25),
        )
        assert inst.status == InstanceStatus.DRAFT
        assert inst.id == "japanese-n3"

    @pytest.mark.parametrize(
        "bad_id",
        [
            "Japanese-N3",  # 大写
            "日语-N3",  # 中文
            "x",  # 太短
            "a" * 50,  # 太长
            "-leading-dash",  # 短横线开头
            "with space",  # 空格
        ],
    )
    def test_invalid_id(self, bad_id):
        with pytest.raises(ValidationError):
            Instance(
                id=bad_id,
                domain="x",
                domain_category=DomainCategory.OTHER,
                target="x",
                weeks=4,
                intensity=Intensity.STANDARD,
                start_date=date(2026, 5, 25),
            )
