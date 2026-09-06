"""Golden 解析回归（Batch L · 环 B）：锁住 LLM 输出 → 结构的解析契约。

不调 LLM：用固定的 golden 输出喂解析层，防止改 prompt/解析时静默破坏前端结构。
任何让这些断言失败的改动都必须显式更新 golden（= 故意的契约变更）。
"""

import json

from core.concepts import sanitize, topo_order
from core.content import _coerce_lesson, _extract_json_object

# 一份"标准"的 LLM 微课输出（含讲解/练习/产出/引用）
GOLDEN_LESSON_JSON = json.dumps(
    {
        "topic": "は vs が",
        "explain": {
            "title": "助词 は 和 が 的区别",
            "summary": "は 提示主题，が 强调主语。",
            "analogy": "は 像聚光灯打在话题上，が 像手指点出谁。",
            "points": ["は 提示已知主题", "が 引入新信息或强调", "   "],
            "examples": [
                {"text": "私は学生です。", "note": "我（作为话题）是学生"},
                {"text": "", "note": "空例句应被丢弃"},
            ],
            "tip": "回答疑问词时用 が。",
        },
        "practice": [
            {"q": "谁来了？", "options": ["誰が", "誰は"], "answer": 0, "why": "疑问词用 が"},
            {"q": "坏题没选项", "options": ["只有一个"], "answer": 0},  # 选项<2 应被过滤
            {"q": "", "options": ["a", "b"], "answer": 5},              # 空题应被过滤
        ],
        "produce": {"task": "用 は 和 が 各造一个句子", "hint": "先想话题再想强调"},
        "citations": ["《新标日》初级 上 第3课", "  "],
    },
    ensure_ascii=False,
)


def test_extract_json_from_fenced_block():
    fenced = "这是讲解：\n```json\n{\"a\": 1}\n```\n谢谢"
    got = _extract_json_object(fenced)
    assert got is not None
    assert json.loads(got) == {"a": 1}


def test_golden_lesson_structure_stable():
    data = json.loads(GOLDEN_LESSON_JSON)
    lesson = _coerce_lesson(data, topic="は vs が", n_questions=3)

    # 顶层契约
    assert lesson["topic"] == "は vs が"
    assert set(lesson["design"]) == {"learning_question", "outcome", "main_thread", "boundary", "key_steps", "quick_checks"}
    assert lesson["design"]["learning_question"]
    assert lesson["design"]["outcome"]
    assert set(lesson["explain"]) == {"title", "summary", "analogy", "points", "examples", "tip"}

    # 空白 point / 空例句被清洗
    assert lesson["explain"]["points"] == ["は 提示已知主题", "が 引入新信息或强调"]
    assert len(lesson["explain"]["examples"]) == 1
    assert lesson["explain"]["examples"][0]["text"] == "私は学生です。"

    # 非法练习题被过滤，只剩 1 道合法题
    assert len(lesson["practice"]) == 1
    assert lesson["practice"][0]["answer"] == 0
    assert set(lesson["practice"][0]) == {"q", "options", "answer", "why"}

    # 4-2-1 产出 + 引用（去空白）保留
    assert lesson["produce"]["task"].startswith("用 は")
    assert lesson["citations"] == ["《新标日》初级 上 第3课"]

    # 新版互动卡片：即使模型只给旧结构，也能合成新前端可消费的 cards
    card_types = [c["type"] for c in lesson["cards"]]
    assert "goal" in card_types
    assert "visual" in card_types
    assert "produce" in card_types


def test_lesson_cards_are_sanitized():
    data = {
        "topic": "t",
        "explain": {},
        "cards": [
            {
                "type": "think",
                "title": "先判断",
                "body": "哪个说法更稳？",
                "options": ["A", "B"],
                "answer": "1",
                "why": "B 更接近核心规则。",
                "expansions": [
                    {
                        "kind": "mechanism",
                        "title": "为什么 B 更稳",
                        "body": "B 把判断规则和适用条件放在一起，而不是只给结论；这样学习者能知道什么时候复用这个判断。",
                    }
                ],
            },
            {"type": "unknown", "title": "兜底类型", "body": "应变成 explain"},
            {"type": "goal", "title": "   ", "body": "   "},
        ],
        "practice": [],
    }
    lesson = _coerce_lesson(data, topic="t", n_questions=3)
    assert lesson["design"]["learning_question"]
    assert lesson["cards"][0]["type"] == "think"
    assert lesson["cards"][0]["answer"] == 1
    assert lesson["cards"][0]["expansions"][0]["kind"] == "mechanism"
    assert lesson["cards"][1]["type"] == "explain"
    assert len(lesson["cards"]) == 2


def test_golden_lesson_answer_out_of_range_clamped():
    data = {"topic": "t", "explain": {}, "practice": [
        {"q": "x", "options": ["a", "b"], "answer": 99},
    ]}
    lesson = _coerce_lesson(data, topic="t", n_questions=3)
    assert lesson["practice"][0]["answer"] == 0


# 一份"标准"的 Concept 图谱输出（含自环/悬空前置/重复，应被 sanitize 清洗成合法 DAG）
GOLDEN_GRAPH = [
    {"id": "c1", "name": "五十音", "week": 1, "difficulty": 1, "prerequisites": []},
    {"id": "c2", "name": "动词分类", "week": 2, "difficulty": 2, "prerequisites": ["c1", "c2"]},  # 自环
    {"id": "c3", "name": "て形", "week": 3, "difficulty": 3, "prerequisites": ["c2", "cX"]},      # cX 悬空
]


def test_golden_graph_sanitize_to_valid_dag():
    clean = sanitize(GOLDEN_GRAPH)
    by_id = {c["id"]: c for c in clean}
    # 自环被去除
    assert "c2" not in by_id["c2"]["prerequisites"]
    # 悬空前置被去除
    assert "cX" not in by_id["c3"]["prerequisites"]
    # 仍可拓扑排序（无环）
    order = topo_order(clean)
    assert order.index("c1") < order.index("c2") < order.index("c3")
