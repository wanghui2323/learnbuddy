"""LearnBuddy 学习伙伴的核心数据模型（pydantic v2）。

设计原则：
- 强类型 + 字段校验，所有 LLM 输出都过这一层校验
- 领域无关：不写"英语/编程"等具体领域逻辑，由 LLM 填空
- 与原 AI 技术学习空间 itutor_engine.py 的 GENERATE JSON 字段保持兼容（便于未来反向迁移）

主要模型：
    - Message:           单条对话消息
    - DomainCategory:    领域分类枚举
    - Intensity:         路径强度枚举
    - EvalQuestion:      单道基线评估题
    - EvalResponse:      用户对评估题的双坐标回答
    - BaselineSummary:   基线评估总结
    - Milestone:         月度里程碑
    - WeeklyTheme:       周主题
    - LearningMethods:   学习方法（核心方法 / 反遗忘 5 机制 / 6 关自检 6 维度）
    - UserParams:        完整参数包（对话引擎 GENERATE 阶段输出 + 后续生成器输入）
    - DaySlot:           单天的一个学习时段
    - WeekDay:           单天日历
    - WeekPlan:          完整周日历
    - Instance:          实例 meta
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# ============================================================================
# 通用枚举
# ============================================================================


class DomainCategory(str, Enum):
    """领域分类。LLM 在 GENERATE 阶段判断，影响学习手册模板的领域翻译。"""

    LANGUAGE = "language"
    STEM = "stem"
    HUMANITY = "humanity"
    SKILL = "skill"
    PROFESSIONAL = "professional"
    OTHER = "other"


class Intensity(str, Enum):
    """学习路径强度。conservative=保守 / standard=标准 / intensive=激进。"""

    CONSERVATIVE = "conservative"
    STANDARD = "standard"
    INTENSIVE = "intensive"


class InstanceStatus(str, Enum):
    """实例生命周期状态。V0 阶段只用 DRAFT 和 ACTIVE。"""

    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    DONE = "done"
    ARCHIVED = "archived"


# ============================================================================
# 对话与评估
# ============================================================================


class Message(BaseModel):
    """OpenAI 风格的单条对话消息。"""

    role: Literal["system", "user", "assistant"]
    content: str


class EvalQuestion(BaseModel):
    """单道基线评估题（LLM 现场为指定领域生成）。

    双坐标系评估：用户回答时分别标记"独立完成情况"和"AI 协作完成情况"，
    用于区分"会做"和"借助 AI 能做"，对应 LearnBuddy 设计的核心差异点。
    """

    id: str = Field(description="题目 ID，例如 q1 / q2")
    question: str = Field(description="题目内容（自然语言）")
    type: Literal["choice", "open", "fill", "code"] = Field(
        description="题型：选择 / 开放 / 填空 / 代码"
    )
    options: Optional[list[str]] = Field(
        default=None, description="如为 choice 题，给出选项；其他题型为 None"
    )
    expected_dimension: str = Field(
        description="本题想测的能力维度（领域适配，例如：语法基础 / 代码阅读 / 元素记忆）"
    )
    answer_hint: Optional[str] = Field(
        default=None, description="参考答案要点（仅供 LLM 后续判分参考，不展示给用户）"
    )
    phase: Literal["independent", "ai_collaboration"] = Field(
        default="independent", description="评估阶段：独立作答或站内 AI 协作"
    )
    capability: str = Field(
        default="independent_foundation", description="V0.53 六维能力键"
    )
    difficulty: int = Field(default=2, ge=1, le=5)
    grading_method: Literal["deterministic", "rubric"] = "rubric"
    correct_answer: Optional[str] = Field(
        default=None, description="choice/fill 的规范答案，仅服务端保存"
    )
    rubric: list[dict] = Field(default_factory=list, description="开放题评分 Rubric")
    common_errors: list[str] = Field(default_factory=list)


class EvalResponse(BaseModel):
    """用户对单道评估题的双坐标回答。"""

    question_id: str
    answer: str = Field(description="用户原始回答文本")
    independent_score: Optional[int] = Field(
        default=None, ge=0, le=5, description="独立完成自评分（0-5）"
    )
    with_ai_score: Optional[int] = Field(
        default=None, ge=0, le=5, description="AI 协作下能完成的自评分（0-5）"
    )
    phase: Optional[Literal["independent", "ai_collaboration"]] = None
    self_confidence: Optional[int] = Field(
        default=None, ge=0, le=5, description="答题后的主观信心，仅用于校准"
    )
    verification_notes: str = Field(default="", max_length=2000)
    duration_ms: int = Field(default=0, ge=0, le=3_600_000)


class ScoreEvidence(BaseModel):
    """V0.53 单题评分证据；任何能力分都必须能回指到 evidence_id。"""

    evidence_id: str
    question_id: str
    phase: Literal["independent", "ai_collaboration"]
    capability: str
    observed_score: float = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    grading_method: Literal["deterministic", "rubric"]
    reason: str
    rubric_scores: dict[str, float] = Field(default_factory=dict)
    flags: list[str] = Field(default_factory=list)
    evaluator_version: str = "assessment-scorer.v0.53"


class AbilityDimensionScore(BaseModel):
    """可解释能力维度：分数、置信度和证据引用缺一不可。"""

    key: str
    label: str
    score: float = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    evidence_ids: list[str] = Field(default_factory=list)


class BaselineSummary(BaseModel):
    """基线评估总结。LLM 在对话引擎汇总评估结果时输出。"""

    summary: str = Field(description="1-2 句话的水平总结")
    strengths: list[str] = Field(default_factory=list, description="优势维度（≤3 条）")
    weaknesses: list[str] = Field(default_factory=list, description="待补维度（≤3 条）")
    raw_responses: list[EvalResponse] = Field(
        default_factory=list, description="原始回答（如有）"
    )


class BaselineDimensionScore(BaseModel):
    """基线的单个能力维度，同时保留独立与 AI 协作坐标。"""

    dimension: str
    independent_score: float = Field(ge=0, le=5)
    with_ai_score: float = Field(ge=0, le=5)
    gap: float = Field(ge=-5, le=5)
    response_count: int = Field(ge=1)


class BaselineProfile(BaseModel):
    """可复测、可绑定实例的结构化基线事实。"""

    assessment_id: str
    summary: str
    independent_score: float = Field(ge=0, le=5)
    with_ai_score: float = Field(ge=0, le=5)
    gap: float = Field(ge=-5, le=5)
    dimensions: list[BaselineDimensionScore] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    questions: list[EvalQuestion] = Field(default_factory=list)
    responses: list[EvalResponse] = Field(default_factory=list)
    completed_at: str
    contract_version: str = "baseline.v0.50.2"
    assessment_method: Literal["self_report_v1", "observed_v2"] = "self_report_v1"
    evidence_level: Literal["self_report", "observed", "provisional"] = "self_report"
    status: Literal["completed", "needs_review"] = "completed"
    profile_id: Optional[str] = None
    profile_version: str = "1"
    profile_confidence: float = Field(default=0.0, ge=0, le=1)
    observed_independent_score: Optional[float] = Field(default=None, ge=0, le=100)
    observed_with_ai_score: Optional[float] = Field(default=None, ge=0, le=100)
    self_confidence: Optional[float] = Field(default=None, ge=0, le=100)
    calibration_gap: Optional[float] = Field(default=None, ge=-100, le=100)
    abilities: list[AbilityDimensionScore] = Field(default_factory=list)
    score_evidence: list[ScoreEvidence] = Field(default_factory=list)


# ============================================================================
# 学习路径结构
# ============================================================================


class Milestone(BaseModel):
    """月度里程碑。"""

    month: int = Field(ge=1, le=12)
    title: str
    deliverable: str = Field(description="本月末必须交付的具体产物")


class WeeklyTheme(BaseModel):
    """周主题（L2 蓝图 · master.json 中每周一行）。"""

    week: int = Field(ge=1, le=52)
    title: str
    key_outcomes: list[str] = Field(
        default_factory=list, description="本周必出的产物（2-4 条）"
    )


class LearningMethods(BaseModel):
    """学习方法三件套（领域翻译后的版本）。

    - core_method: 类似"读+改+解释"在该领域的等价物
    - anti_forgetting: 反遗忘 5 机制中适用本领域的 2-5 项
    - self_check_dimensions: 6 关自检在本领域的 6 个维度翻译
    """

    core_method: str = Field(description="该领域的核心学习方法（如：背+造句+讲解）")
    anti_forgetting: list[str] = Field(
        default_factory=list,
        description="反遗忘机制（micro-review / 间隔重复 / 周回测 / 月度雪球 / 解释关延迟 的领域翻译）",
    )
    self_check_dimensions: list[str] = Field(
        default_factory=list,
        description="6 关自检的 6 个领域维度",
    )


# ============================================================================
# 完整参数包（GENERATE 标记输出 + 生成器输入）
# ============================================================================


class UserParams(BaseModel):
    """完整参数包：对话引擎在 <<<GENERATE>>> 阶段输出 + 生成器消费。

    与原 itutor_engine.py 的 GENERATE JSON schema 保持字段兼容。
    """

    domain: str = Field(description="学习领域（用户原话 / 贴近原话）")
    domain_category: DomainCategory = Field(default=DomainCategory.OTHER)
    target: str = Field(description="可衡量目标")

    weeks: int = Field(ge=4, le=24, description="周数（V0 限定 4-24）")
    intensity: Intensity = Field(default=Intensity.STANDARD)

    weekday_hours: float = Field(ge=0, le=8, description="每个工作日投入小时")
    weekend_hours: float = Field(ge=0, le=16, description="每个周末日投入小时")
    weekly_total_hours: Optional[float] = Field(
        default=None, description="每周总投入小时（如未给，按 5*weekday + 2*weekend 计算）"
    )

    baseline_summary: str = Field(description="基线评估总结的一两句话")
    baseline_profile: Optional[BaselineProfile] = Field(
        default=None,
        description="结构化双坐标基线；旧 GENERATE 输出可不提供",
    )
    preferences: str = Field(default="", description="用户偏好（侧重 / 节奏 / 反遗忘选项等）")

    milestones: list[Milestone] = Field(default_factory=list)
    weekly_themes: list[WeeklyTheme] = Field(default_factory=list)
    learning_methods: LearningMethods = Field(
        default_factory=lambda: LearningMethods(
            core_method="（待 LLM 填）",
            anti_forgetting=[],
            self_check_dimensions=[],
        )
    )

    @model_validator(mode="after")
    def _auto_compute_weekly_total(self) -> "UserParams":
        """如未提供周总投入，自动按 5×weekday + 2×weekend 计算。

        必须用 model_validator(mode="after") 而非 field_validator——
        pydantic v2 的 field validator 默认不会被默认值触发，会导致
        weekly_total_hours 仅在用户显式传入时才参与计算。
        """
        if self.weekly_total_hours is None:
            self.weekly_total_hours = self.weekday_hours * 5 + self.weekend_hours * 2
        return self


# ============================================================================
# 周日历（W1.json 等）
# ============================================================================


class DaySlot(BaseModel):
    """单天里的一个学习时段。"""

    time: str = Field(description="如 '09:00-11:00' / '晚 20:00-21:30'")
    task: str = Field(description="该时段的具体任务")
    output: Optional[str] = Field(default=None, description="期望产出，如有")


class WeekDay(BaseModel):
    """单天日历。"""

    day: int = Field(ge=1, le=7, description="本周第几天（1=周一）")
    date: date
    weekday: str = Field(description="周一 / 周二 / ...")
    is_weekend: bool
    planned_hours: float = Field(ge=0)
    slots: list[DaySlot] = Field(default_factory=list)
    notes: Optional[str] = None


class WeekPlan(BaseModel):
    """完整周日历（W1.json 的 schema）。"""

    week: int = Field(ge=1, le=52)
    title: str
    date_range: str = Field(description="如 '2026-05-25 ~ 2026-05-31'")
    key_outcomes: list[str] = Field(default_factory=list)
    days: list[WeekDay] = Field(default_factory=list)


# ============================================================================
# 实例 meta
# ============================================================================


class Instance(BaseModel):
    """学习路径实例的 meta（每个 data/instances/{slug}/meta.json）。"""

    id: str = Field(description="实例 slug，例如 japanese-n3 / ielts / nutrition-101")
    domain: str
    domain_category: DomainCategory
    target: str
    weeks: int
    intensity: Intensity
    status: InstanceStatus = InstanceStatus.DRAFT
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    start_date: date = Field(description="第 1 天的日历日期")

    @field_validator("id")
    @classmethod
    def _validate_id_format(cls, v: str) -> str:
        """学习路径 id 必须是小写字母+数字+短横线，2-40 字符。"""
        import re

        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,39}", v):
            raise ValueError(
                f"instance id '{v}' 必须为小写字母/数字/短横线，2-40 字符"
            )
        return v


# ============================================================================
# 入口便捷函数
# ============================================================================


__all__ = [
    "DomainCategory",
    "Intensity",
    "InstanceStatus",
    "Message",
    "EvalQuestion",
    "EvalResponse",
    "BaselineSummary",
    "BaselineDimensionScore",
    "BaselineProfile",
    "Milestone",
    "WeeklyTheme",
    "LearningMethods",
    "UserParams",
    "DaySlot",
    "WeekDay",
    "WeekPlan",
    "Instance",
]
