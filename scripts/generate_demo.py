"""一键生成完整学习系统实例的 CLI demo（Day 3 收口验证）。

跑法：
    .venv/bin/python scripts/generate_demo.py

会跑一次：
    - 用一个固化的「日语 N3 备考」UserParams
    - 调 generator.generate_instance（含 LLM polish 学习手册）
    - 写到 data/instances/japanese-n3/（重复跑会自动 -2 / -3）

期望产物：
    data/instances/japanese-n3/
    ├── meta.json            实例元数据
    ├── master.json          12 周月度蓝图 + 周主题
    ├── W1.json              第 1 周详细日历（7 天 + 时段）
    ├── 学习手册.md          领域适配方法论（含反遗忘 5 + 6 关）
    └── 愿景与契约.md        用户专属契约 + R1-R5 调整规则
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.generator import generate_instance  # noqa: E402
from core.llm_client import LLMConfigError  # noqa: E402
from core.schemas import (  # noqa: E402
    DomainCategory,
    Intensity,
    LearningMethods,
    Milestone,
    UserParams,
    WeeklyTheme,
)


def _build_demo_params() -> UserParams:
    """日语 N3 备考 12 周示例参数包。"""
    return UserParams(
        domain="日语 N3 备考",
        domain_category=DomainCategory.LANGUAGE,
        target="3 个月内通过 JLPT N3（合格分 95/180）",
        weeks=12,
        intensity=Intensity.STANDARD,
        weekday_hours=2,
        weekend_hours=4,
        baseline_summary="N5 水平，能识平假/片假，词汇约 600，未学语法体系",
        preferences=(
            "重听说弱写作；每周 1 次外教对话；用 Anki 不抗拒；"
            "希望有月模考节奏；有 1 个朋友学过 N2 可问"
        ),
        milestones=[
            Milestone(month=1, title="N5 巩固 + N4 词汇", deliverable="词汇量 1500+ / N4 词汇 ≥ 70%"),
            Milestone(month=2, title="N4 语法体系建立", deliverable="N4 模考 ≥ 80% / 能写 200 字日记"),
            Milestone(month=3, title="N3 冲刺 + 应试", deliverable="N3 模考 ≥ 65% / 1 次外教模拟面"),
        ],
        weekly_themes=[
            WeeklyTheme(
                week=1,
                title="起跑 + 节奏建立 + N5 模考",
                key_outcomes=["完成 1 次 N5 模考", "Anki 累计 ≥ 200 张", "学习手册看完 1 遍"],
            ),
            WeeklyTheme(
                week=2,
                title="N4 词汇冲刺第 1 周",
                key_outcomes=["新词 300 + 复习 200", "1 篇日记"],
            ),
            WeeklyTheme(
                week=3,
                title="N4 词汇冲刺第 2 周 + 语法启动",
                key_outcomes=["新词 300", "N4 语法 5 个"],
            ),
            WeeklyTheme(
                week=4,
                title="M1 月末测 + 节奏调整",
                key_outcomes=["N5 二次模考 ≥ 90%", "R3 月末评估"],
            ),
        ],
        learning_methods=LearningMethods(
            core_method="背 + 造句 + 影子跟读 + 月度对话",
            anti_forgetting=[
                "每日 Anki 推送 30 张新词 + 复习全部到期卡",
                "每周写日记 1 篇（≥150 字，必含本周新语法）",
                "周日整周回顾 + 错题二刷",
                "月底把 4 周内容串成思维导图",
                "每月给朋友讲 1 次本月学到的 3 个最重要的点",
            ],
            self_check_dimensions=["听", "说", "读", "写", "翻译", "教别人"],
        ),
    )


def _print_section(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print("=" * 60)


def _print_file_preview(path: Path, max_lines: int = 30) -> None:
    """打印文件前 max_lines 行 + 总行数。"""
    if not path.exists():
        print(f"  (文件不存在: {path})")
        return
    lines = path.read_text(encoding="utf-8").splitlines()
    print(f"  路径: {path}")
    print(f"  行数: {len(lines)}")
    print(f"  字节: {path.stat().st_size}")
    print("  -- 前", min(max_lines, len(lines)), "行 --")
    for line in lines[:max_lines]:
        print(f"    {line}")
    if len(lines) > max_lines:
        print(f"    ... ({len(lines) - max_lines} 行省略)")


def main() -> int:
    print("Itutor 学习系统 · 一键生成 demo")
    print(f"项目根：{PROJECT_ROOT}")

    try:
        params = _build_demo_params()
    except Exception as e:
        print(f"❌ 构造 UserParams 失败：{e}")
        return 1

    _print_section("输入参数包")
    print(f"  domain:   {params.domain}")
    print(f"  weeks:    {params.weeks}")
    print(f"  hours:    {params.weekly_total_hours}h/week")
    print(f"  baseline: {params.baseline_summary}")

    _print_section("调 generate_instance（含 LLM polish 学习手册）")
    try:
        inst_dir = generate_instance(
            params,
            start_date=date(2026, 5, 25),
            use_llm_for_manual=True,
        )
    except LLMConfigError as e:
        print(f"❌ {e}")
        return 1

    print(f"✅ 实例目录: {inst_dir}")

    _print_section("生成的文件")
    for name in ["meta.json", "master.json", "W1.json", "学习手册.md", "愿景与契约.md"]:
        p = inst_dir / name
        size = p.stat().st_size if p.exists() else 0
        marker = "✅" if p.exists() else "❌"
        print(f"  {marker} {name:20} {size:>6} bytes")

    _print_section("master.json 预览")
    _print_file_preview(inst_dir / "master.json", max_lines=25)

    _print_section("学习手册.md 预览")
    _print_file_preview(inst_dir / "学习手册.md", max_lines=40)

    _print_section("结论")
    print("🎉 Day 3 生成器端到端 demo 全过。")
    print(f"   下一步用浏览器打开实例目录看完整 5 件套：")
    print(f"   open {inst_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
