"""Concept 图谱单测（Batch 2A）：sanitize/断环/拓扑/状态 —— 纯逻辑，不调 LLM。"""

from core.concepts import (
    build_unlocks,
    concept_status,
    sanitize,
    topo_order,
    MASTERY_THRESHOLD,
)
from core.mastery import mastered_set


def test_sanitize_drops_invalid_prereqs_and_self_ref():
    raw = [
        {"id": "c1", "name": "A", "prerequisites": ["c1", "ghost"]},  # 自指 + 不存在
        {"id": "c2", "name": "B", "prerequisites": ["c1"]},
    ]
    clean = sanitize(raw)
    by_id = {c["id"]: c for c in clean}
    assert by_id["c1"]["prerequisites"] == []
    assert by_id["c2"]["prerequisites"] == ["c1"]


def test_sanitize_breaks_cycle():
    raw = [
        {"id": "c1", "name": "A", "prerequisites": ["c2"]},
        {"id": "c2", "name": "B", "prerequisites": ["c1"]},  # 环
    ]
    clean = sanitize(raw)
    # 断环后必须是合法 DAG：能完整拓扑排序
    order = topo_order(clean)
    assert set(order) == {"c1", "c2"}
    # 任一节点的前置都必须排在它前面
    placed = set()
    by_id = {c["id"]: c for c in clean}
    for cid in order:
        for p in by_id[cid]["prerequisites"]:
            assert p in placed
        placed.add(cid)


def test_sanitize_skips_nameless_and_coerces_difficulty():
    raw = [
        {"id": "c1", "name": "", "prerequisites": []},   # 无名 → 丢
        {"id": "c2", "name": "B", "difficulty": 99},      # 越界 → 收敛到 10
        {"id": "c3", "name": "C", "difficulty": -5},      # 越界 → 收敛到 1
    ]
    clean = sanitize(raw)
    names = {c["name"] for c in clean}
    assert "B" in names and "C" in names and "" not in names
    by_name = {c["name"]: c for c in clean}
    assert by_name["B"]["difficulty"] == 10
    assert by_name["C"]["difficulty"] == 1


def test_sanitize_preserves_authoritative_sources():
    clean = sanitize([
        {
            "id": "c1",
            "name": "A",
            "sources": [
                {"title": "官方文档", "url": "https://example.org/doc", "kind": "official", "note": "权威入口"},
                {"title": "权威书刊", "url": "", "kind": "book"},
                {"title": "坏链接", "url": "javascript:alert(1)", "kind": "paper"},
            ],
        }
    ])
    srcs = clean[0]["sources"]
    assert srcs[0]["url"] == "https://example.org/doc"
    assert srcs[1]["kind"] == "book"
    assert srcs[2]["url"] == ""


def test_concept_status_frontier_locked_mastered():
    concepts = sanitize([
        {"id": "c1", "name": "基础", "prerequisites": []},
        {"id": "c2", "name": "进阶", "prerequisites": ["c1"]},
        {"id": "c3", "name": "高级", "prerequisites": ["c2"]},
    ])
    # 只掌握了"基础"
    status = concept_status(concepts, {"基础"})
    assert status["c1"] == "mastered"
    assert status["c2"] == "frontier"   # 前置已掌握、自身未掌握
    assert status["c3"] == "locked"     # 前置未掌握


def test_build_unlocks_inverse_edges():
    concepts = sanitize([
        {"id": "c1", "name": "A", "prerequisites": []},
        {"id": "c2", "name": "B", "prerequisites": ["c1"]},
        {"id": "c3", "name": "C", "prerequisites": ["c1"]},
    ])
    unlocks = build_unlocks(concepts)
    assert set(unlocks["c1"]) == {"c2", "c3"}
    assert unlocks["c2"] == []


def test_mastered_set_threshold():
    mastery = {"A": {"prob": 0.9}, "B": {"prob": 0.5}, "C": {"prob": MASTERY_THRESHOLD}}
    ms = mastered_set(mastery, MASTERY_THRESHOLD)
    assert ms == {"A", "C"}  # >= 阈值
