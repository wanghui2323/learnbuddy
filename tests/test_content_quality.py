from core.content_quality import evaluate_lesson_quality, format_quality_issues


def _rich_lesson() -> dict:
    return {
        "topic": "AI 工程 harness",
        "design": {
            "learning_question": "如何判断一个 AI 工作流是否已经具备可复现、可观测、可评估的 harness？",
            "outcome": "能画出一个最小 AI harness 链路，并说明 trace、评估样例和失败回放的位置。",
            "main_thread": "围绕一次工具调用失败后的修复与回放，把模型、工具、trace、评估串成一条工程链路。",
            "boundary": "本节不展开具体平台选型、权限体系和大规模部署细节。",
            "key_steps": [
                {
                    "name": "定义契约",
                    "purpose": "让模型输出和工具调用都变成可解析、可验收的对象。",
                    "learner_action": "写出输入、输出、失败状态三项契约。",
                    "stuck_point": "只写 prompt，不写输出格式和失败条件。",
                },
                {
                    "name": "接入工具",
                    "purpose": "把模型意图转成真实可执行的工具调用。",
                    "learner_action": "标出工具参数、调用结果和异常回传位置。",
                    "stuck_point": "把 Function Calling 误解成模型已经执行了工具。",
                },
                {
                    "name": "加 trace 与评估",
                    "purpose": "让失败可以复现，效果可以用样例集衡量。",
                    "learner_action": "在链路图里标出 trace、eval set 和失败回放。",
                    "stuck_point": "只看最终回答，不记录中间状态。",
                },
            ],
            "quick_checks": [
                "看到只有聊天框，能判断它还不是完整 harness。",
                "看到失败无法回放，能指出缺少 trace 或评估样例。",
            ],
        },
        "explain": {
            "title": "把 harness 做成可验证工程",
            "summary": "harness 的核心不是调用模型，而是把模型、工具、数据流和评估组织成可复现的工程链路。",
            "analogy": "像测试流水线：每个输入、输出、错误边界都要能被追踪。",
            "points": [
                "先定义输入输出契约，再决定 agent 或工具怎么编排；否则模型说得再顺，也无法判断它有没有真正完成任务。",
                "每个环节都要有可观测信号，不能只看最终回答；trace 至少要记录输入、工具参数、工具返回、异常和最终响应。",
                "评估样例和失败回放是 harness 的验收标准；没有样例集，就无法知道一次 prompt 修改到底是变好还是碰巧成功。",
            ],
            "examples": [
                {"text": "正例：用户让系统查订单，模型先输出结构化 tool call，服务端执行失败后把错误码写入 trace，再让模型根据错误选择重试或澄清。", "note": "体现可观测和可恢复：失败不是消失在对话里，而是进入可回放链路。"},
                {"text": "反例：只让模型自由对话，回答里说“我帮你查了”，但没有工具参数、调用结果和失败状态。", "note": "这不是 harness，因为没有任何证据能复现模型到底做了什么。"},
            ],
            "tip": "最容易误解的是把 harness 当成一个 prompt，而不是工程化运行框架。",
        },
        "cards": [
            {"type": "goal", "title": "本节目标", "body": "你要能判断一个 AI 工作流是否具备 harness：有输入输出契约、工具编排、trace、评估样例和失败回放。验收不是“回答看起来更完整”，而是失败时能回放、修改后能用同一批样例比较。"},
            {"type": "source", "title": "本节依据", "body": "优先依据参考资料中的工程实践材料。", "items": [{"title": "来源1", "url": "https://example.com", "note": "工具调用契约"}]},
            {"type": "think", "title": "先判断", "body": "一个只会聊天的 agent 算不算 harness？先别看它回答是否聪明，要看它有没有把任务拆成可记录的运行过程。", "options": ["算，只要能回答", "不一定，要看是否可复现和可评估", "只要接了 API 就算"], "answer": 1, "why": "harness 的判断标准是工程链路是否可复现、可观测、可评估。A 错在只看最终文本，C 错在把接 API 当成工程闭环；真正要看输入、工具调用、状态、失败和评估是否可追踪。"},
            {"type": "compare", "title": "边界对比", "body": "Prompt、Agent、Harness 三者的区别在职责边界：prompt 解决“怎么问”，agent 解决“下一步做什么”，harness 解决“运行过程能否被验证”。", "items": ["Prompt：只约束单次模型输出；判断标准是这次回答是否符合格式，但失败后通常不知道中间发生了什么。", "Agent：允许模型选择工具或步骤；判断标准是动作是否合理，但如果没有 trace，动作链仍然不可复现。", "Harness：把模型、工具、trace、eval set 和回放接成工程链；判断标准是同一输入能重跑、失败能定位、改动能比较。"], "expansions": [
                {"kind": "mechanism", "title": "为什么 harness 必须记录中间态", "body": "模型输出只是最终表象，真正决定可复现的是中间态是否被记录。一次订单查询失败时，如果只保存最终回答，你不知道错在用户输入解析、工具参数、权限、工具返回还是模型总结。harness 把每一步变成可检查对象，所以失败可以定位，prompt 改动也能用同一批样例比较。"},
                {"kind": "boundary", "title": "什么时候还不算 harness", "body": "只接了工具、只写了 prompt、只做了漂亮界面，都不等于 harness。边界判断是：同一输入能否重跑，失败能否定位到具体环节，修改后能否用固定样例比较。如果这三问任意一问答不上，它最多是 demo 或 agent 原型，还不是工程 harness。反例是客服机器人说“我查过了”，但没有 trace、没有工具返回、没有固定样例，失败时只能人工猜。"},
            ]},
            {"type": "example", "title": "worked example：订单查询失败", "body": "用户说“帮我查上周那个订单”。不要直接看最终回答，要沿运行链判断它是不是 harness：它要先把模糊请求变成可执行输入，再调用工具，最后把失败和澄清都留在 trace 里。", "items": [{"step": "1", "text": "先看输入契约：用户的模糊请求是否被解析成 user_id、时间范围、候选订单条件。", "note": "没有结构化输入，后面的工具调用就无法验收。"}, {"step": "2", "text": "再看工具调用：是否记录 search_orders 的参数、返回结果、错误码和耗时。", "note": "这决定失败能不能回放。"}, {"step": "3", "text": "如果工具返回多个候选，系统是否进入澄清，而不是编一个订单号。", "note": "这是边界判断，能区分 harness 和自由聊天。"}, {"step": "4", "text": "最后用 eval case 重跑：同样的模糊订单请求，修改 prompt 后是否更少误报。", "note": "评估样例让改动可比较。"}], "expansions": [
                {"kind": "worked_example", "title": "完整失败回放", "items": [
                    {"step": "1", "text": "把“上周那个订单”解析成 user_id、time_range、candidate_query，并记录解析结果。如果这里没有结构化输入，后面查错时只能猜。", "note": "第一步验证输入契约。"},
                    {"step": "2", "text": "调用 search_orders，并记录参数、返回候选和错误码。如果工具返回多个订单，系统应进入澄清，而不是编一个订单号。", "note": "第二步验证工具调用和边界分支。"},
                    {"step": "3", "text": "把这次 trace 加入 eval case，修改 prompt 后重跑同一输入，比较误报率和澄清率。", "note": "第三步验证改动是否真的更好。"},
                ]},
            ]},
            {"type": "check", "title": "最小验收清单", "body": "判断一个方案是否到 harness，不看技术名词堆了多少，看它能不能经受三次追问：失败能不能定位、案例能不能重跑、改动能不能比较。只要其中一问答不上，就说明它还只是 demo，而不是能长期迭代的工程链路；能答上，才值得进入真实用户场景。这一步是上线底线。", "items": ["失败在哪里记录：至少能定位到模型输出、工具参数、工具返回或权限错误；如果只看到最终回答，就无法判断错误来自模型还是工具。", "如何重跑同一案例：同一输入、同一 prompt 版本、同一工具 mock 能复现旧结果；否则修复只是凭感觉。", "如何证明改好了：有固定 eval set，而不是只挑一个成功 demo；通过率、误报率或人工评分要能前后对比。"]},
            {"type": "produce", "title": "轮到你输出", "body": "画出一个最小 AI harness：输入、模型、工具、trace、评估、失败回放。每个节点写一句验收标准，尤其标出失败如何回放。", "items": ["至少 6 个节点。", "标出失败如何回放。", "给出 eval set 的 2 个样例。"]},
        ],
        "produce": {"task": "用自己的话解释 AI harness，并画一个最小链路，包含 trace 和评估。", "hint": "先写输入输出，再写工具和失败回放。"},
        "practice": [
            {"q": "下面哪项最像 harness 的验收标准？", "options": ["回答更长", "失败可回放且能用样例评估", "界面更好看"], "answer": 1, "why": "harness 关心工程可复现、可观测和可评估。"},
            {"q": "只写一个 prompt 缺少什么？", "options": ["颜色", "运行状态和评估闭环", "更多形容词"], "answer": 1, "why": "没有状态、trace 和评估，就很难稳定落地。"},
        ],
        "citations": ["来源1：工具调用契约"],
    }


def test_quality_contract_passes_rich_grounded_lesson():
    report = evaluate_lesson_quality(_rich_lesson(), context="[来源1] 工具调用契约")
    assert report["passed"] is True
    assert report["score"] >= 78
    assert report["checks"]["interactive_cards"] == 1
    assert report["checks"]["design_issues"] == 0
    assert report["checks"]["expansion_blocks"] >= 3
    assert {"mechanism", "boundary", "worked_example"}.issubset(set(report["checks"]["expansion_kinds"]))
    assert report["dimension_scores"]["depth"]["score"] >= 70
    assert report["dimension_scores"]["pedagogy"]["score"] >= 70
    assert report["dimension_scores"]["source_coverage"]["score"] >= 70


def test_quality_contract_flags_shallow_lesson():
    report = evaluate_lesson_quality(
        {
            "topic": "T",
            "explain": {"title": "T", "summary": "一句话", "points": ["点"], "examples": []},
            "cards": [{"type": "goal", "title": "目标", "body": "了解一下"}],
            "practice": [{"q": "对吗", "options": ["对", "错"], "answer": 0, "why": "对"}],
        }
    )
    assert report["passed"] is False
    assert any("教学卡片少于 6 张" in x for x in report["issues"])
    assert any("缺少教学设计摘要" in x for x in report["issues"])
    assert report["checks"]["practice_count"] == 1
    assert report["dimension_scores"]["rhythm"]["score"] < 70


def test_quality_contract_rejects_outline_like_complete_lesson():
    lesson = _rich_lesson()
    lesson["explain"]["summary"] = "这节课理解三件事。"
    lesson["explain"]["points"] = ["知道是什么", "知道怎么用", "避免误区"]
    lesson["explain"]["examples"] = [{"text": "例子 A", "note": "说明一下"}]
    lesson["cards"] = [
        {"type": "goal", "title": "目标", "body": "了解输入输出契约、trace 和评估。"},
        {"type": "source", "title": "来源", "body": "优先依据参考资料。", "items": [{"title": "来源1", "url": "https://example.com"}]},
        {"type": "think", "title": "先想", "body": "只会聊天算 harness 吗？", "options": ["算", "不算", "看情况"], "answer": 1, "why": "因为要能评估。"},
        {"type": "compare", "title": "对比", "body": "Prompt、Agent、Harness 不一样。", "items": ["Prompt：单次指令。", "Agent：选择动作。", "Harness：运行评估。"]},
        {"type": "example", "title": "例子", "body": "两个例子。", "items": [{"text": "有 trace。"}, {"text": "没 trace。"}]},
        {"type": "produce", "title": "输出", "body": "画一张图。", "items": ["输入", "模型", "工具"]},
    ]

    report = evaluate_lesson_quality(lesson, context="[来源1] 工具调用契约")

    assert report["passed"] is False
    assert report["checks"]["dense_teaching_cards"] < 3
    assert report["checks"]["expansion_blocks"] == 0
    assert any("深讲卡少于 3 张" in issue for issue in report["issues"])


def test_quality_contract_counts_expansion_based_dense_cards():
    lesson = _rich_lesson()
    lesson["citations"] = []
    lesson["cards"] = [
        {"type": "goal", "title": "目标", "body": "你要能判断记忆分层系统里每层的职责、写入时机、检索方式和失效边界。"},
        {"type": "source", "title": "来源", "body": "本节按工程通用知识生成，不伪造外部资料。"},
        {
            "type": "think",
            "title": "先想",
            "body": "用户问“上周那个问题”，不要直接猜某个数据库。先判断这个信息是否还在当前上下文、是否可能命中短 TTL 缓存、是否需要从长期记忆或业务记录检索。",
            "options": ["短时上下文", "工作缓存", "长期记忆"],
            "answer": 2,
            "why": "一周前的信息通常已经离开当前上下文和短 TTL 缓存，需要从长期记忆或业务数据库检索。这个判断训练的是生命周期和检索方式，而不是记住某个固定技术选型。",
        },
        {
            "type": "compare",
            "title": "短时记忆和工作缓存的边界",
            "body": "这张卡的正文故意很短，深讲放在展开层。",
            "expansions": [
                {
                    "kind": "mechanism",
                    "title": "为什么上下文窗口不是缓存",
                    "body": "上下文窗口解决的是当前推理可见性：模型只能根据窗口里还存在的 token 做判断。缓存解决的是跨请求状态复用：系统把结构化状态写入 Redis 或会话存储，再按 key 和 TTL 读取。两者的生命周期、写入主体和检索方式都不同，所以不能用“都能暂存信息”来混为一谈。",
                },
                {
                    "kind": "boundary",
                    "title": "什么时候必须离开短时记忆",
                    "body": "只要信息跨越当前对话窗口、需要被多个请求共享、或者需要精确更新和删除，就不应该只放在短时上下文里。比如用户刚才选择的订单 ID 可以进入工作缓存；用户长期偏好、历史订单和稳定事实则要进入长期记忆或业务数据库。边界判断不是“近不近”，而是是否需要可控的生命周期和检索键。",
                },
            ],
        },
        {
            "type": "example",
            "title": "worked example：查找上周的问题",
            "body": "正文保持短，完整推演在展开层。",
            "expansions": [
                {
                    "kind": "worked_example",
                    "title": "三步判断检索层",
                    "body": "完整推演时，不要从技术名词开始，而要从证据开始：当前窗口有没有、缓存是否还有效、长期层是否能召回并让用户确认。每一步都对应一个失败分支，能防止系统把模糊记忆说成确定事实。",
                    "items": [
                        {"step": "1", "text": "先看当前上下文：如果最近几轮没有“上周问题”的具体内容，短时记忆不能回答，只能提供当前意图。", "note": "短时层负责当前对话，不负责跨周回忆。"},
                        {"step": "2", "text": "再看工作缓存：如果 TTL 只有数小时，一周前信息大概率已过期，不能依赖 Redis 猜测。", "note": "缓存命中失败不是错误，而是设计边界。"},
                        {"step": "3", "text": "最后查长期记忆或业务记录：用用户 ID、时间范围和语义相似度找历史问题，再让用户确认候选。", "note": "长期层负责可追溯检索，但仍要防止误匹配。"},
                    ],
                }
            ],
        },
        {
            "type": "example",
            "title": "反例：把一切都写入长期记忆",
            "body": "正文短，反例在展开层。",
            "expansions": [
                {
                    "kind": "counterexample",
                    "title": "记忆污染如何发生",
                    "body": "如果系统把“用户今天在犹豫买耳机”直接写成长期偏好，下一周推荐时就会误以为这是稳定兴趣。正确做法是区分临时意图、已确认偏好和业务事实：临时意图进工作缓存，确认后的偏好才进长期记忆，订单状态则以业务数据库为准。这个反例训练的是写入时机判断，而不是存储技术名词。进一步说，长期记忆的写入应该有确认信号，例如用户明确保存、重复出现的稳定偏好、或来自业务系统的确定事实；没有这些信号，就应该只保留短期状态或等待澄清。验收时要追问写入触发者是谁、删除条件是什么、下次检索时如何避免把一次性行为当成长期偏好；三问答不上，就说明这条记忆不应该进入长期层。",
                }
            ],
        },
        {"type": "produce", "title": "输出任务", "body": "设计一个客服记忆分层方案：写出短时、工作、长期三层的存储、检索、写入时机和删除规则，并用一个失败案例说明如何避免记忆污染。"},
        {"type": "check", "title": "自查", "body": "用三问检查：信息是否跨会话，是否需要精确删除，是否已经被用户确认。只要答案不清楚，就不要直接写入长期记忆。", "items": ["能说出每层生命周期。", "能解释写入主体。", "能给出一个过期策略。"]},
    ]

    report = evaluate_lesson_quality(lesson, context="")

    assert report["checks"]["dense_teaching_cards"] >= 3
    assert not any("深讲卡少于 3 张" in issue for issue in report["issues"])
    assert {"mechanism", "boundary", "worked_example"}.issubset(set(report["checks"]["expansion_kinds"]))


def test_quality_contract_rejects_fake_sources_without_context():
    lesson = _rich_lesson()
    lesson["citations"] = ["来源1：OpenAI 官方文档"]
    lesson["cards"][1]["body"] = "基于 OpenAI 官方文档。"
    report = evaluate_lesson_quality(lesson, context="")
    assert report["passed"] is False
    assert any("不能伪造来源" in x or "资料依据" in x for x in report["issues"])
    assert "本地内容质量门未通过" in format_quality_issues(report)


def test_quality_contract_rejects_ascii_diagram_cards():
    lesson = _rich_lesson()
    lesson["cards"][3] = {
        "type": "visual",
        "title": "记忆系统三层架构图",
        "body": """
        +------------------+ +------------------+ +------------------+
        | 工作记忆          | | 情景记忆          | | 语义记忆          |
        +------------------+ +------------------+ +------------------+
        """,
        "items": ["工作记忆：保存当前会话状态", "情景记忆：保存历史事件", "语义记忆：沉淀长期知识"],
    }

    report = evaluate_lesson_quality(lesson, context="[来源1] 工具调用契约")

    assert report["passed"] is False
    assert report["checks"]["ascii_diagram_cards"] == 1
    assert any("ASCII 宽图" in issue for issue in report["issues"])


def test_quality_contract_rejects_full_unit_without_activity_plan():
    lesson = _rich_lesson()
    lesson["_session_contract"] = {
        "target_minutes": 90,
        "min_estimated_minutes": 70,
        "label": "1–2 小时",
    }

    report = evaluate_lesson_quality(lesson, context="[来源1] 工具调用契约")

    assert report["passed"] is False
    assert report["checks"]["target_minutes"] == 90
    assert any("无法支撑该学习时段" in issue or "学习活动链" in issue for issue in report["issues"])


def test_quality_contract_accepts_full_unit_activity_plan():
    lesson = _rich_lesson()
    lesson["_session_contract"] = {
        "target_minutes": 90,
        "min_estimated_minutes": 70,
        "label": "1–2 小时",
    }
    lesson["estimated_minutes"] = 90
    lesson["activity_plan"] = [
        {"type": "lecture", "title": "核心讲解", "minutes": 20, "task": "看懂 harness 的核心判断、三层边界和验收标准。"},
        {"type": "worked_example", "title": "完整例题", "minutes": 20, "task": "按步骤推演订单查询失败案例，标出每一步的证据和失败分支。"},
        {"type": "drill", "title": "分层练习", "minutes": 15, "task": "完成基础判断、边界判断和错误定位三类练习。"},
        {"type": "produce", "title": "主动产出", "minutes": 25, "task": "填写 harness 架构图模板，写出节点、trace 字段、eval case 和回放方式，再对照参考答案修复一个缺 trace 的错误方案。"},
        {"type": "reflection", "title": "回顾自查", "minutes": 10, "task": "按 rubric 检查产物，记录仍不确定的判断点。"},
    ]
    lesson["rubric"] = [
        "链路必须包含输入契约、模型、工具、trace、评估样例和失败回放。",
        "每个节点必须写出一个可观察验收标准。",
        "至少给出一个失败案例，并说明如何重跑比较。",
    ]
    lesson["produce"]["task"] = (
        "设计一个最小 AI harness 链路：画出输入、模型、工具、trace、评估样例和失败回放，"
        "每个节点写一句验收标准，再用一个订单查询失败案例说明如何定位问题和证明改动有效。"
        "填写模板：节点/输入/输出/失败信号/回放方法/评估样例。"
        "参考答案：合格样例应至少包含 search_orders 参数、工具返回、错误码、澄清分支和 eval case。"
        "高阶练习：评审并修复一个只列工具清单、没有 trace 字段和失败回放的错误方案。"
    )
    lesson["cards"].append({
        "type": "explain",
        "title": "加厚讲解：harness 为什么必须产物化",
        "body": (
            "90 分钟的 harness 学习单元不能只停留在“知道 trace 很重要”。学习者要完成一次从概念到工程产物的转换："
            "先把用户输入、模型决策、工具参数、工具返回、异常状态和最终回答拆成独立节点；再为每个节点写出可观察证据；"
            "最后把失败样例放回同一条链路重跑。这个过程的关键判断是：如果一个节点不能被记录、重放或比较，它就不能成为 harness 的可靠部分。"
            "例如订单查询失败时，最终回答“没查到”并不够；你必须能看到解析出的用户条件、search_orders 的参数、候选订单列表、错误码、"
            "模型为什么选择澄清而不是编造答案，以及 prompt 修改后同一批 eval case 的结果变化。只有这些证据都在，harness 才能支撑持续迭代。"
        ),
        "items": [
            "节点化：把输入、模型、工具、trace、eval 和 replay 拆开，每个节点有自己的验收信号。",
            "证据化：每个判断都要留下可观察数据，例如参数、返回、错误码、耗时、分支选择。",
            "可比较：修 prompt 或工具后，用同一批 eval case 重跑，比较误报率、澄清率和失败定位速度。",
        ],
        "expansions": [
            {
                "kind": "worked_example",
                "title": "从一次失败回放到一组验收样例",
                "body": (
                    "完整推演要从一个失败开始。第一步，把用户原始输入和模型抽取出的结构化条件并排写出来，"
                    "检查是否存在缺字段、误解析或歧义。第二步，查看工具调用参数和返回：如果工具返回多个候选，"
                    "harness 应该记录候选列表并进入澄清分支；如果工具超时，应该记录错误码和重试策略，而不是让模型直接总结。"
                    "第三步，把这次失败转成 eval case：固定输入、固定工具 mock、固定期望行为，例如“返回多个候选时必须澄清”。"
                    "第四步，修改 prompt 或工具契约后重跑同一组样例，观察误报率、澄清率和失败定位时间是否改善。"
                    "这套步骤训练的是工程判断：你不是让模型回答更长，而是让每次失败都能变成下一轮迭代的证据。"
                ),
            },
            {
                "kind": "boundary",
                "title": "哪些内容不能算 harness 产物",
                "body": (
                    "边界同样重要。第一种不合格产物是“聊天截图”：它只能证明某一次回答看起来合理，不能证明链路可复现。"
                    "第二种不合格产物是“工具列表”：只列出接了哪些 API，却没有记录参数、返回、异常和权限状态，失败时仍然只能猜。"
                    "第三种不合格产物是“主观优化总结”：写了 prompt 改得更清楚，但没有固定 eval set，也没有前后指标，所以无法判断是真的变好还是刚好命中一个样例。"
                    "合格产物必须同时回答三问：失败在哪里被记录，如何用同一输入重跑，修改后用什么指标比较。"
                    "如果学习者的方案缺少这三问中的任意一问，就应该回到 activity_plan 的 worked example 阶段重做，而不是进入下一节。"
                ),
            }
        ],
    })

    report = evaluate_lesson_quality(lesson, context="[来源1] 工具调用契约")

    assert report["passed"] is True
    assert report["checks"]["activity_minutes"] == 90
    assert report["checks"]["missing_activity_groups"] == []
    assert report["checks"]["self_study_template"] is True
    assert report["checks"]["reference_answer"] is True
    assert report["checks"]["high_order_practice"] is True


def test_quality_contract_rejects_full_unit_without_self_study_artifacts():
    lesson = _rich_lesson()
    lesson["_session_contract"] = {
        "target_minutes": 90,
        "min_estimated_minutes": 70,
        "label": "1–2 小时",
    }
    lesson["estimated_minutes"] = 90
    lesson["activity_plan"] = [
        {"type": "lecture", "title": "核心讲解", "minutes": 20, "task": "看懂 harness 的核心判断、三层边界和验收标准。"},
        {"type": "worked_example", "title": "完整例题", "minutes": 20, "task": "按步骤推演订单查询失败案例，标出每一步的证据和失败分支。"},
        {"type": "drill", "title": "分层练习", "minutes": 15, "task": "完成基础判断、边界判断和错误定位三类练习。"},
        {"type": "produce", "title": "主动产出", "minutes": 25, "task": "设计一个最小 harness 链路，写出节点、trace 字段、eval case 和回放方式。"},
        {"type": "reflection", "title": "回顾自查", "minutes": 10, "task": "按 rubric 检查产物，记录仍不确定的判断点。"},
    ]
    lesson["rubric"] = [
        "链路必须包含输入契约、模型、工具、trace、评估样例和失败回放。",
        "每个节点必须写出一个可观察验收标准。",
        "至少给出一个失败案例，并说明如何重跑比较。",
    ]

    report = evaluate_lesson_quality(lesson, context="[来源1] 工具调用契约")

    assert report["passed"] is False
    assert report["checks"]["self_study_template"] is False
    assert report["checks"]["reference_answer"] is False
    assert report["checks"]["high_order_practice"] is False
    assert any("参考答案" in issue or "高阶练习" in issue for issue in report["issues"])


def test_quality_contract_rejects_classroom_talk_and_raw_personalization_labels():
    lesson = _rich_lesson()
    lesson["cards"][0]["body"] += " 同学们，老师带你在课堂上完成。已具备：LLM 基础。"

    report = evaluate_lesson_quality(lesson, context="[来源1] 工具调用契约")

    assert report["passed"] is False
    assert "同学们" in report["checks"]["classroom_terms"]
    assert "已具备：" in report["checks"]["raw_personalization_labels"]


def test_quality_contract_rejects_full_unit_with_thin_core_content():
    lesson = _rich_lesson()
    lesson["_session_contract"] = {
        "target_minutes": 90,
        "min_estimated_minutes": 70,
        "label": "1–2 小时",
    }
    lesson["estimated_minutes"] = 90
    lesson["activity_plan"] = [
        {"type": "lecture", "title": "核心讲解", "minutes": 20, "task": "看懂核心判断和适用边界。"},
        {"type": "worked_example", "title": "完整例题", "minutes": 20, "task": "按步骤推演一个真实案例。"},
        {"type": "drill", "title": "分层练习", "minutes": 15, "task": "完成基础判断和边界判断。"},
        {"type": "produce", "title": "主动产出", "minutes": 25, "task": "设计一个可检查的方案，包含节点、规则、失败分支和验收标准。"},
        {"type": "reflection", "title": "回顾自查", "minutes": 10, "task": "按 rubric 检查产物并记录疑点。"},
    ]
    lesson["rubric"] = ["标准 1", "标准 2", "标准 3"]
    for card in lesson["cards"]:
        if isinstance(card, dict) and card.get("type") != "source":
            card["body"] = "这个点需要理解，但这里没有展开。"
            card["items"] = []
            card["expansions"] = []
    lesson["explain"]["points"] = ["理解概念", "完成练习", "做一次产出"]
    lesson["explain"]["examples"] = [{"text": "例子", "note": "说明"}]

    report = evaluate_lesson_quality(lesson, context="[来源1] 工具调用契约")

    assert report["passed"] is False
    assert any("1–2 小时单元的核心教学内容不足" in issue for issue in report["issues"])
