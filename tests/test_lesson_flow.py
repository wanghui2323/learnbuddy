from core.lesson_flow import attach_lesson_flow, build_lesson_flow


def _lesson() -> dict:
    return {
        "topic": "AI Agent 工程落地",
        "design": {
            "learning_question": "如何跑通一个可观测的 Agent 工具调用最小闭环？",
            "outcome": "能画出 Agent 调用工具、记录 trace、处理失败的最小链路。",
            "main_thread": "从一次搜索工具调用失败出发，补齐契约、trace 和失败处理。",
            "boundary": "暂不展开多 Agent 协作和复杂权限系统。",
            "key_steps": [
                {
                    "name": "定义工具契约",
                    "purpose": "让模型知道什么时候调用工具、传什么参数。",
                    "learner_action": "写出工具名、参数和返回结构。",
                    "stuck_point": "只说调用搜索，不写参数约束。",
                },
                {
                    "name": "记录 trace",
                    "purpose": "让工具调用过程可追踪、可复盘。",
                    "learner_action": "标出请求、响应和异常三类记录。",
                    "stuck_point": "只保存最终答案。",
                },
                {
                    "name": "处理失败",
                    "purpose": "让系统遇到坏返回时能重试或降级。",
                    "learner_action": "写出失败条件和下一步动作。",
                    "stuck_point": "把错误直接展示给用户。",
                },
            ],
            "quick_checks": ["能指出工具参数缺失的问题。", "能说明 trace 放在哪里。"],
        },
        "_sources": [
            {
                "n": 1,
                "title": "OpenAI Docs: Function calling",
                "url": "https://platform.openai.com/docs/guides/function-calling",
                "source_type": "official",
            }
        ],
        "_quality": {
            "passed": True,
            "score": 86,
            "dimension_scores": {
                "depth": {"score": 82, "note": "有判断规则"},
                "pedagogy": {"score": 88, "note": "有课设主线"},
                "source_coverage": {"score": 90, "note": "有来源"},
                "interactivity": {"score": 78, "note": "有互动"},
                "actionability": {"score": 84, "note": "有产出"},
                "rhythm": {"score": 80, "note": "节奏完整"},
            },
        },
        "explain": {"title": "Agent 最小闭环", "summary": "跑通第一个工具调用。"},
        "cards": [
            {"type": "goal", "title": "本节目标", "body": "理解 Agent 最小闭环。"},
            {
                "type": "think",
                "title": "先判断",
                "body": "工具调用失败时应该怎么处理？",
                "options": ["忽略", "记录 trace 并重试", "重新写 UI"],
                "answer": 1,
                "why": "工程落地需要可观测和可恢复。",
            },
            {"type": "example", "title": "真实例子", "items": [{"text": "调用搜索工具", "note": "返回结构化结果"}]},
        ],
        "produce": {"task": "画出一个 Agent 调工具的最小链路。", "hint": "包含输入、模型、工具、trace。"},
        "practice": [
            {
                "q": "哪项更像工程化 Agent？",
                "options": ["只聊天", "有工具契约和 trace", "只换模型"],
                "answer": 1,
                "why": "工程化需要契约和观测。",
            }
        ],
    }


def test_build_lesson_flow_from_legacy_lesson():
    flow = build_lesson_flow(_lesson(), source_pack={"status": "ok"}, generated_at="2026-06-26T10:00:00")

    assert flow["schema_version"] == 2
    assert flow["contract_version"] == "lesson-loop.v0.46"
    assert flow["topic"] == "AI Agent 工程落地"
    assert flow["quality_score"] == 86
    types = [e["type"] for e in flow["elements"]]
    assert types[0] == "source"
    assert "think" in types
    assert "produce" in types
    assert "practice" in types
    assert types[-1] == "reflect"
    assert flow["elements"][0]["items"][0]["source_type"] == "official"
    assert flow["elements"][1]["type"] == "goal"
    assert flow["elements"][1]["quality"]["dimension"] == "pedagogy"
    assert flow["design"]["learning_question"].startswith("如何跑通")


def test_attach_lesson_flow_preserves_legacy_fields():
    lesson = _lesson()
    returned = attach_lesson_flow(lesson, source_pack={"status": "curated"})

    assert returned is lesson
    assert lesson["cards"]
    assert lesson["practice"]
    assert lesson["_lesson_flow"]["source_pack_status"] == "curated"
    assert len(lesson["_lesson_flow"]["elements"]) >= 5


def test_lesson_flow_preserves_v046_card_contract_fields():
    lesson = _lesson()
    lesson["cards"].append(
        {
            "type": "visual",
            "title": "运行链路图",
            "body": "按输入、执行、观测三个节点看。",
            "completion_rule": "能画出三个节点，并说明失败如何回放。",
            "expected_signal": "能指出 trace 在哪里产生。",
            "free_input_prompt": "用自己的话补一个失败场景。",
            "source_refs": ["来源1"],
            "diagram": {
                "caption": "从左到右看一次工具调用。",
                "nodes": [
                    {"id": "input", "title": "输入契约", "desc": "约束任务和输出"},
                    {"id": "run", "title": "工具调用", "desc": "执行外部动作"},
                    {"id": "trace", "title": "Trace", "desc": "记录请求、响应和异常"},
                ],
                "edges": [{"from": "input", "to": "run", "label": "触发"}],
            },
        }
    )

    flow = build_lesson_flow(lesson, source_pack={"status": "ok"})
    visual = next(e for e in flow["elements"] if e["type"] == "visual")

    assert visual["completion_rule"].startswith("能画出三个节点")
    assert visual["expected_signal"].startswith("能指出")
    assert visual["free_input_prompt"].startswith("用自己的话")
    assert visual["source_refs"] == ["来源1"]
    assert visual["diagram"]["nodes"][0]["title"] == "输入契约"
    assert visual["diagram"]["edges"][0]["label"] == "触发"
