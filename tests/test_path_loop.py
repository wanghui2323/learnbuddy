"""路径级 Loop 状态单测。"""

from __future__ import annotations

import json

from core.path_loop import (
    advance_after_self_check,
    load_path_loop_state,
    ordered_units,
    record_lesson_completed,
    set_pregeneration_state,
)


def _write_instance(inst_dir):
    inst_dir.mkdir(parents=True)
    (inst_dir / "master.json").write_text(
        json.dumps(
            {
                "north_star": {"domain": "AI Agent", "target": "交付原型"},
                "schedule": {"start_date": "2026-06-22", "weekday_hours": 1, "weekend_hours": 2},
                "weeks": [
                    {"week": 1, "title": "Agent 记忆系统", "date_range": "2026-06-22 ~ 2026-06-28", "key_outcomes": ["画结构图", "做对比表"]},
                    {"week": 2, "title": "工具调用闭环", "date_range": "2026-06-29 ~ 2026-07-05", "key_outcomes": ["跑通一次调用", "写失败清单"]},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (inst_dir / "W1.json").write_text(
        json.dumps(
            {
                "week": 1,
                "title": "Agent 记忆系统",
                "days": [
                    {"day": 1, "date": "2026-06-22", "weekday": "周一", "slots": [{"time": "20:00", "task": "推进新内容：记忆三层架构", "output": "结构图"}]},
                    {"day": 2, "date": "2026-06-23", "weekday": "周二", "slots": [{"time": "20:00", "task": "轻量消化：情景记忆 vs 语义记忆", "output": "对比表"}]},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_ordered_units_expand_week_days(tmp_path):
    inst_dir = tmp_path / "p1"
    _write_instance(inst_dir)

    units = ordered_units(inst_dir)

    assert [u["unit_id"] for u in units] == ["w1-d1", "w1-d2"]
    assert units[0]["topic"] == "Agent 记忆系统"
    assert units[1]["topic"].startswith("Agent 记忆系统 · D2")


def test_path_state_uses_completed_topics_without_over_completing_week(tmp_path):
    inst_dir = tmp_path / "p1"
    _write_instance(inst_dir)

    state = load_path_loop_state(inst_dir, user_id=7, completed_topics=["Agent 记忆系统"])

    assert "w1-d1" in state["completed_unit_ids"]
    assert "w1-d2" not in state["completed_unit_ids"]
    assert state["current_unit"]["unit_id"] == "w1-d2"


def test_record_lesson_completed_is_idempotent_and_advances(tmp_path):
    inst_dir = tmp_path / "p1"
    _write_instance(inst_dir)

    state1 = record_lesson_completed(
        inst_dir,
        7,
        topic="Agent 记忆系统",
        week=1,
        summary={"correct": 1},
        completed_topics=[],
    )
    state2 = record_lesson_completed(
        inst_dir,
        7,
        topic="Agent 记忆系统",
        week=1,
        summary={"correct": 1},
        completed_topics=["Agent 记忆系统"],
    )

    assert state1["current_unit"]["unit_id"] == "w1-d2"
    assert state2["current_unit"]["unit_id"] == "w1-d2"
    assert len([e for e in state2["events"] if e["kind"] == "lesson_completed"]) == 1


def test_pregeneration_state_persists(tmp_path):
    inst_dir = tmp_path / "p1"
    _write_instance(inst_dir)
    state = load_path_loop_state(inst_dir, user_id=7, completed_topics=["Agent 记忆系统"])
    unit = state["current_unit"]

    saved = set_pregeneration_state(
        inst_dir,
        7,
        unit=unit,
        status="running",
        job_id="job1",
        stage_id="sources",
        stage_label="整理外部参考来源",
        progress_percent=57,
        completed_topics=["Agent 记忆系统"],
    )

    assert saved["pre_generation"]["unit_id"] == "w1-d2"
    assert saved["pre_generation"]["status"] == "running"
    assert saved["pre_generation"]["progress_percent"] == 57
    reloaded = load_path_loop_state(inst_dir, user_id=7, completed_topics=["Agent 记忆系统"])
    assert reloaded["pre_generation"]["job_id"] == "job1"


def _complete_w1(inst_dir):
    units = ordered_units(inst_dir)
    topics = []
    state = None
    for unit in units:
        topics.append(unit["topic"])
        state = record_lesson_completed(
            inst_dir,
            7,
            topic=unit["topic"],
            week=1,
            completed_topics=topics,
        )
    return state, topics


def test_completed_week_waits_for_self_check_without_fake_future_unit(tmp_path):
    inst_dir = tmp_path / "p1"
    _write_instance(inst_dir)

    state, _topics = _complete_w1(inst_dir)

    assert state["status"] == "awaiting_self_check"
    assert state["current_unit"] is None
    assert state["generated_weeks"] == [1]
    assert not (inst_dir / "W2.json").exists()


def test_failed_self_check_blocks_and_passed_check_atomically_builds_next_week(tmp_path):
    inst_dir = tmp_path / "p1"
    _write_instance(inst_dir)
    _state, topics = _complete_w1(inst_dir)

    blocked = advance_after_self_check(
        inst_dir,
        7,
        week=1,
        passed=False,
        passed_count=3,
        completed_topics=topics,
    )
    assert blocked["week_gate"]["status"] == "blocked"
    assert not (inst_dir / "W2.json").exists()

    unlocked = advance_after_self_check(
        inst_dir,
        7,
        week=1,
        passed=True,
        passed_count=4,
        completed_topics=topics,
    )
    assert unlocked["week_gate"]["status"] == "unlocked"
    assert unlocked["current_unit"]["unit_id"] == "w2-d1"
    assert unlocked["generated_weeks"] == [1, 2]
    assert (inst_dir / "W2.json").exists()
    assert len(json.loads((inst_dir / "W2.json").read_text(encoding="utf-8"))["days"]) == 7
    master = json.loads((inst_dir / "master.json").read_text(encoding="utf-8"))
    assert [week["status"] for week in master["weeks"]] == ["completed", "active"]

    reloaded = load_path_loop_state(inst_dir, 7, topics)
    assert reloaded["current_unit"]["unit_id"] == "w2-d1"


def test_self_check_cannot_skip_unfinished_week(tmp_path):
    inst_dir = tmp_path / "p1"
    _write_instance(inst_dir)

    try:
        advance_after_self_check(
            inst_dir,
            7,
            week=1,
            passed=True,
            passed_count=6,
            completed_topics=[],
        )
    except ValueError as exc:
        assert "未完成" in str(exc)
    else:
        raise AssertionError("未完成的周不应解锁")
