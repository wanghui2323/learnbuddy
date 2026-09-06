"""阶段闸门单测（Batch 2C · #5）：阶段聚合 + 通关/解锁逻辑。纯函数，无 IO。"""

MASTER = {"months": [
    {"id": "M1", "title": "基础", "weeks": [1, 2]},
    {"id": "M2", "title": "进阶", "weeks": [3, 4]},
    {"id": "M3", "title": "高级", "weeks": [5, 6]},
]}


def _items(specs):
    return [{"week": w, "status": s, "name": f"c{i}"} for i, (w, s) in enumerate(specs)]


def test_no_months_returns_empty():
    from core.phases import compute_phases

    assert compute_phases({}, []) == []


def test_gate_clears_current_locked():
    from core.phases import compute_phases, current_phase_max_week

    its = _items([(1, "mastered"), (2, "mastered"),     # M1 100% → cleared
                  (3, "mastered"), (4, "frontier"),      # M2 50% < 70% → current
                  (5, "locked"), (6, "locked")])         # M3 → locked
    ph = compute_phases(MASTER, its)
    assert [p["status"] for p in ph] == ["cleared", "current", "locked"]
    assert ph[0]["ratio"] == 1.0 and ph[1]["ratio"] == 0.5
    assert ph[1]["gate_passed"] is False
    assert current_phase_max_week(ph) == 4  # 当前阶段 M2 的最后一周


def test_empty_phase_does_not_block():
    from core.phases import compute_phases

    its = _items([(3, "frontier"), (4, "locked")])  # M1 无知识点
    ph = compute_phases(MASTER, its)
    assert ph[0]["status"] == "cleared"   # 空阶段视为通关，不阻断
    assert ph[1]["status"] == "current"


def test_all_cleared_points_to_last():
    from core.phases import compute_phases, current_phase, current_phase_max_week

    its = _items([(w, "mastered") for w in range(1, 7)])
    ph = compute_phases(MASTER, its)
    assert all(p["status"] == "cleared" for p in ph)
    assert current_phase(ph)["id"] == "M3"
    assert current_phase_max_week(ph) == 6
