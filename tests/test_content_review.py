"""教学内容生成工作流单测。

两条链路：
- 单段式兜底（ITUTOR_LESSON_PIPELINE=0）：起草 → 自检 → 本地质量门。
- 三段式流水线（默认）：课设(reasoner) → 写深内容(chat) → 结构门 → 锐度评审(reasoner) → 定向返工。

用 FakeClient 按调用次序返回固定文本，不打真 LLM；trace 默认关闭不写库。
"""

import json

import pytest

from core.content import generate_lesson_reviewed
from core.llm_client import LLMError

DRAFT = json.dumps({
    "topic": "T", "explain": {"title": "t", "summary": "s", "points": ["p1"],
                              "examples": [{"text": "e", "note": "n"}]},
    "practice": [{"q": "q", "options": ["a", "b"], "answer": 0, "why": "w"}],
    "produce": {"task": "用自己的话复述"},
}, ensure_ascii=False)

REVIEW_PASS = json.dumps({"verdict": "pass", "issues": []}, ensure_ascii=False)

REVIEW_REVISE = json.dumps({
    "verdict": "revise", "issues": ["答案标错了"],
    "lesson": {
        "topic": "T2", "explain": {"title": "t2", "summary": "s2", "points": ["p2"]},
        "practice": [{"q": "q2", "options": ["a", "b"], "answer": 1, "why": "w2"}],
        "produce": {"task": "改后的产出"},
    },
}, ensure_ascii=False)

# 一份能通过结构质量门的"高质量"讲解，复用在多个场景里。
RICH_LESSON = {
    "topic": "T 深化版",
    "explain": {
        "title": "把 T 学成可判断的规则",
        "summary": "这一节不只记定义，而是学会判断 T 在真实任务里什么时候成立、什么时候不该用。",
        "analogy": "把 T 当成一把工具：先确认材料和边界，再决定是否动手。",
        "points": [
            "先判断 T 解决的是哪类问题，不要把相邻概念混用；如果问题类型都没对齐，后面套再多步骤也只是形式正确。",
            "看到边界场景时，用输入、过程、输出三步检查：输入是否具备前置条件，过程是否符合约束，输出能否被验收。",
            "能自己造一个反例，才说明不是死记定义；反例要和正例只差一个关键条件，这样才能暴露真正边界。",
        ],
        "examples": [
            {"text": "例子 A：一个场景满足 T 的前置输入，过程也能产生可验收输出，因此可以使用 T；判断时要先写出条件，再写出验收信号。", "note": "演示正确使用不是看名称，而是看条件链是否完整。"},
            {"text": "反例 B：另一个场景表面词汇很像 T，但缺少关键输入，只能得到模糊结论；这时继续使用 T 会把相邻概念混在一起。", "note": "演示边界：少一个关键输入，结论就不成立。"},
        ],
        "tip": "最常见的错误是只背名字，不检查适用条件。",
    },
    "cards": [
        {"type": "goal", "title": "本节目标", "body": "你要能用 T 判断一个真实场景：说清适用条件、边界和验收标准。最后的验收不是背出定义，而是能解释为什么某个相邻场景不该用 T。"},
        {"type": "source", "title": "本节依据", "body": "本节暂无外部来源，按通用知识生成。", "items": ["无外部来源时不伪造引用。"]},
        {"type": "think", "title": "先判断", "body": "如果一个场景只满足表面相似，你会不会使用 T？先不要急着选，要问：它的输入是否满足、过程是否可执行、输出是否能验收。", "options": ["直接使用", "先查输入/过程/输出", "只看名称"], "answer": 1, "why": "T 的关键不是名称，而是条件和边界是否满足。A 错在把表面相似当成立，C 错在跳过验收；真正的判断要先查三件事。"},
        {"type": "compare", "title": "判断表", "body": "把 T 和相邻概念分开：不要问“像不像”，要问目标、输入、产出三件事是否同时成立。只要其中一项不成立，就不要继续套 T 的步骤，因为这时你得到的只是套壳方案；真正的判断是先排除相邻概念，再进入 T 的流程。", "items": ["目标：T 解决的是一类明确问题；如果目标只是泛泛改善体验，就可能是相邻概念，继续使用 T 会把方案做偏。", "输入：T 需要关键前置条件；缺少这个条件时，输出再完整也不可靠，因为模型/学习者只能补想象，无法证明判断成立。", "产出：T 的结果必须能被验收；如果只能得到主观描述，就不能算完成 T，应该改用更适合描述或探索的方式。"], "expansions": [
            {"kind": "mechanism", "title": "为什么三问能防止套壳", "body": "T 的误用通常不是因为学习者完全不知道定义，而是因为他们只看到表面词汇相似。目标、输入、产出三问把抽象定义拆成可检查条件：目标确认问题类型，输入确认前置条件，产出确认结果可验收。三问都成立时，T 才是合适工具；任意一问失败，就说明只是把名字套上去了。"},
            {"kind": "boundary", "title": "什么时候该切到相邻概念", "body": "如果目标只是探索可能性、输入缺少关键条件、或产出无法被第三方验收，就不要继续用 T。反例是：场景里出现了 T 的关键词，但缺少必要输入，学习者仍然写出完整流程；这会产生看似完整、实际不可验证的答案。正确做法是先写一句改道理由，再选择更适合描述、探索或澄清的相邻概念。"},
        ]},
        {"type": "example", "title": "worked example：只差一个条件", "body": "用一个正例和一个反例建立边界。关键不是多举例，而是看一个条件变化后结论如何翻转：同样的表面词汇，只要少掉关键输入，就必须换判断。这个例子训练的是“看到相似时先找缺失条件”的动作，而不是把定义背得更熟。", "items": [{"step": "1", "text": "正例：条件齐全时使用 T，先写出目标、输入和预期产出。", "note": "这一步验证它不是只在名称上像 T，而是确实满足前置条件。"}, {"step": "2", "text": "执行过程里检查是否有可观察信号，证明每一步都能推进到产出。", "note": "过程可观察，才不会变成凭感觉判断，也方便之后复盘。"}, {"step": "3", "text": "反例：只有名称相似，但缺少关键输入。", "note": "少掉这个输入后，边界不成立，应切到相邻概念，而不是继续补话术。"}, {"step": "4", "text": "如果学习者仍想用 T，就让他说出验收标准；说不出，就说明只是套壳。", "note": "验收标准能逼出真正理解，也能暴露哪里只是记住了名词。"}], "expansions": [
            {"kind": "worked_example", "title": "完整判断推演", "items": [
                {"step": "1", "text": "先读正例：目标、输入、产出都明确，因此可以进入 T 的流程；如果只看到关键词，不算通过。", "note": "第一步确认问题类型。"},
                {"step": "2", "text": "执行过程中写下可观察信号，例如中间状态、验收标准或判断证据；没有信号时，流程只是叙述。", "note": "第二步确认过程可检查。"},
                {"step": "3", "text": "把反例只改掉一个关键输入，再重新跑三问；此时输入失败，所以结论必须翻转。", "note": "第三步用最小差异暴露边界。"},
                {"step": "4", "text": "最后写改道理由：为什么不用 T、切到哪个相邻概念、原本期待的产出要放弃什么。", "note": "第四步训练迁移，不只是背定义。"},
            ]},
        ]},
        {"type": "check", "title": "三问验收", "body": "判断 T 是否成立时，用三问卡住自己：每个问题都要有具体回答。它们的作用是防止你只看概念名称，而忽略条件、边界和可验证产出；只要答不上其中一问，就应该回到场景重新拆，而不是继续把后面的步骤写完。真正掌握 T 的标志，是能解释为什么某个相似场景不适用，并能指出下一步该换成哪个相邻概念。", "items": ["它解决的目标是否正是 T 的目标，而不是相邻概念的目标？如果目标说不清，后面所有步骤都会变成套模板。", "关键输入是否已经具备，缺一个会不会让结果失效？如果缺失仍强行使用 T，输出通常只能靠猜。", "输出是否能被第三方检查，而不是只靠主观感觉？如果不能验收，就说明你还没把 T 转成可执行判断。", "如果三问里有一问失败，就写一句改道理由：为什么不用 T、改用什么、放弃哪个原产出。"]},
        {"type": "produce", "title": "轮到你输出", "body": "用自己的话解释 T，并造一个正例和一个反例。反例必须只改动一个关键条件，最后写出为什么这个改动让 T 不成立。再补一句：如果不用 T，你会切到哪个相邻概念，为什么。这样输出才证明你会迁移，而不是只会复述；也能暴露你是否真的理解边界。", "items": ["说清条件，不只写定义。", "写出边界，说明哪一个条件翻转了结论。", "给出验收标准，让别人能判断你的例子是否成立。"]},
    ],
    "produce": {"task": "用自己的话复述 T 的判断规则，并造一个正例和一个反例。", "hint": "先写适用条件，再写不适用的边界。"},
    "practice": [
        {"q": "什么时候可以使用 T？", "options": ["只要名称相似", "满足关键条件并能验收", "越复杂越适合"], "answer": 1, "why": "正确判断要看条件和验收，不是看名称。"},
        {"q": "哪种输出最能证明你理解了 T？", "options": ["背定义", "造正反例并说明边界", "复制教材句子"], "answer": 1, "why": "正反例能检验是否掌握边界；背定义和复制句子都可能只是记住表述，没有证明会判断。"},
    ],
    "citations": [],
}

QUALITY_REVISE = json.dumps({
    "verdict": "revise",
    "issues": ["本地质量门：内容太浅"],
    "lesson": RICH_LESSON,
}, ensure_ascii=False)

# 三段式流水线用的固定响应
DESIGN_JSON = json.dumps({
    "topic": "T",
    "learning_question": "这一节要解决在真实任务里如何判断 T 是否成立的问题。",
    "outcome": "学完后你能对一个新场景判断该不该用 T，并说出理由。",
    "main_thread": "围绕一个把相邻概念混用导致出错的真实任务展开。",
    "boundary": "本节不展开 T 的历史与底层推导，只练判断。",
    "angle": "T 的关键不是定义，而是输入是否满足前置条件这个非显然判断。",
    "anti_generic": ["理解概念很重要", "多练习就会了"],
    "key_steps": [
        {"name": "识别问题类型", "purpose": "避免概念混用", "learner_action": "对一个场景说出它属于哪类问题", "stuck_point": "把相邻概念当成同一个"},
        {"name": "三步检查", "purpose": "建立可操作判断", "learner_action": "用输入过程输出三步检查一个例子", "stuck_point": "只看表面相似就下结论"},
        {"name": "造反例", "purpose": "检验是否真懂边界", "learner_action": "自己造一个不成立的反例", "stuck_point": "造不出和正例有区别的反例"},
    ],
    "quick_checks": ["能不能一句话说清 T 解决什么问题？", "能不能造一个反例？"],
}, ensure_ascii=False)

WRITE_JSON = json.dumps(RICH_LESSON, ensure_ascii=False)

CRITIC_PASS = json.dumps({
    "verdict": "pass",
    "scores": {"specificity": 85, "insight": 82, "depth": 80, "grounding": 75},
    "issues": [],
    "rewrite_instructions": [],
}, ensure_ascii=False)

CRITIC_REVISE = json.dumps({
    "verdict": "revise",
    "scores": {"specificity": 40, "insight": 45, "depth": 55, "grounding": 70},
    "issues": ["讲解太通用，换个主题也成立"],
    "rewrite_instructions": ["把 angle（前置条件判断）落到 example 卡的具体例子上"],
}, ensure_ascii=False)

EXPAND_PATCH = json.dumps({
    "issues_fixed": ["补足 90 分钟单元的核心讲解密度", "新增边界反例展开层"],
    "explain_examples": [
        {
            "text": "边界反例：一个场景只有 T 的关键词，但缺少关键输入；此时继续套 T 会产生不可验收输出。",
            "note": "演示为什么只看表面词汇会误判。",
        }
    ],
    "cards": [
        {
            "type": "explain",
            "title": "补强卡：从规则复述到可验收判断",
            "body": (
                "补强的第一步不是增加术语，而是让学习者把判断过程写成可验收对象。"
                "一个合格判断必须包含三件事：适用条件、结论翻转条件、第三方检查方式。"
                "适用条件回答为什么这个场景可以用 T；翻转条件回答少了哪一个条件就不能用；"
                "检查方式回答别人如何根据你的文本复现同一个判断。如果这三件事缺任意一项，"
                "学习者看似写了很多，其实仍然停留在定义复述。90 分钟单元要训练的是这种可复现判断，而不是更长的说明文字。"
            ),
            "items": [
                "适用条件：写出目标、输入、产出三项证据。",
                "翻转条件：反例必须只改动一个关键条件。",
                "检查方式：让第三方能复现同一个判断。",
            ],
            "expansions": [
                {
                    "kind": "mechanism",
                    "title": "为什么可验收判断比定义复述更重要",
                    "body": (
                        "定义复述只能证明学习者记住了表述，不能证明他会迁移。可验收判断把知识拆成证据链："
                        "目标决定问题类型，输入决定规则能否启动，产出决定结果能否被检查。三项都清楚时，别人可以沿同一条证据链复现结论；"
                        "任意一项缺失时，答案就会滑向主观解释。这个机制让学习者遇到相似场景时，不靠感觉判断，而靠条件链判断。"
                    ),
                }
            ],
        },
        {
            "type": "compare",
            "title": "补强卡：边界反例与改道规则",
            "body": (
                "90 分钟单元必须让学习者练会边界判断，而不是只记住 T 的正向流程。边界判断的核心动作是："
                "把正例里最关键的输入拿掉，再重新检查目标、输入和产出三件事。如果目标仍像 T，但输入缺失，"
                "就不能继续套 T，因为后续步骤只能靠猜。学习者要写出改道理由：为什么不用 T、应该切到哪个相邻概念、"
                "原本期待的产出要放弃什么。这个动作让他从“会复述规则”进入“会处理相似但不成立的场景”。"
                "最后还要写一条复盘记录：下一次遇到同类相似场景时，先检查哪个条件，避免再次套壳。"
                "这条记录也要能被检查。"
            ),
            "items": [
                "先找关键输入：缺少关键输入时，正例结论必须翻转。",
                "再写改道理由：不用 T 的原因、改用的相邻概念、放弃的原产出。",
                "最后给验收标准：别人能否复现你的判断，并指出哪一个条件导致结论改变。",
            ],
            "expansions": [
                {
                    "kind": "boundary",
                    "title": "什么时候不用 T",
                    "body": (
                        "不用 T 的典型场景有三类：第一，目标只是探索或描述，没有明确可验收产出；第二，关键输入缺失，"
                        "学习者只能靠想象补齐条件；第三，输出无法被第三方检查，只能说“看起来合理”。这些情况继续使用 T，"
                        "会产生形式完整但不可验证的答案。正确做法是写出改道理由，再切到澄清、探索或相邻概念。"
                    ),
                },
                {
                    "kind": "worked_example",
                    "title": "反例推演：只差一个条件",
                    "items": [
                        {"step": "1", "text": "先读正例，确认目标、输入、产出都满足 T。", "note": "建立基准。"},
                        {"step": "2", "text": "拿掉关键输入，其他描述保持不变。", "note": "只改一个条件，才能看清边界。"},
                        {"step": "3", "text": "重新检查产出是否还能被验收；如果不能，就写改道理由。", "note": "训练结论翻转。"},
                        {"step": "4", "text": "让同伴根据你的标准复现判断。", "note": "复现不了就不是合格产物。"},
                    ],
                },
            ],
        }
    ],
    "rubric_additions": ["反例必须只改变一个关键条件，并说明结论为什么翻转。"],
    "produce": {
        "task": (
            "产出一页可检查判断方案：先写一个适用 T 的正例，再写一个只缺少关键输入的反例；"
            "每个例子都要包含目标、输入、产出、判断结论和验收标准。最后写一句改道理由，说明反例为什么不能继续用 T，"
            "应该切到哪个相邻概念，以及别人如何复现你的判断。"
            "填写模板：目标 / 输入 / 产出 / 正例 / 反例 / 改道理由 / 验收标准。"
            "参考答案：合格样例会用一个缺少关键输入的反例展示结论翻转。"
            "高阶练习：评审并修复一个只写定义、没有边界诊断的错误方案。"
        ),
        "hint": "不要只写定义；用正反例和验收标准证明你会判断边界。",
    },
    "citations": [],
}, ensure_ascii=False)

REVIEW_RICH = json.dumps({
    "verdict": "revise",
    "issues": ["按锐度评审重写"],
    "lesson": {**RICH_LESSON, "topic": "T 锐化版"},
}, ensure_ascii=False)

REVIEW_BAD = json.dumps({
    "verdict": "revise",
    "issues": ["错误地修浅了"],
    "lesson": {
        "topic": "T 变浅版",
        "explain": {"title": "浅讲 T", "summary": "知道 T。", "points": ["定义"], "examples": [{"text": "例子", "note": "说明"}]},
        "cards": [
            {"type": "goal", "title": "目标", "body": "了解 T。"},
            {"type": "source", "title": "来源", "body": "本节暂无外部来源，按通用知识生成。"},
            {"type": "think", "title": "先想", "body": "T 对吗？", "options": ["对", "错"], "answer": 0, "why": "因为符合定义。"},
            {"type": "example", "title": "例子", "body": "一个例子。", "items": ["T 可以用。"]},
            {"type": "produce", "title": "输出", "body": "复述 T。"},
        ],
        "produce": {"task": "复述 T。"},
        "practice": [{"q": "T 是什么？", "options": ["A", "B"], "answer": 0, "why": "A。"}],
        "citations": [],
    },
}, ensure_ascii=False)


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0
        self.messages = []
        self.kwargs = []

    def chat(self, messages, **kw):
        self.messages.append(messages)
        self.kwargs.append(kw)
        r = self.responses[self.calls]
        self.calls += 1
        if isinstance(r, Exception):
            raise r
        return r


def _gen(client, **kw):
    return generate_lesson_reviewed(domain="日语", target="N3", topic="T",
                                    baseline="零基础", context="", client=client, **kw)


def _full_unit_lesson_json() -> str:
    lesson = json.loads(json.dumps(RICH_LESSON, ensure_ascii=False))
    lesson["estimated_minutes"] = 90
    lesson["activity_plan"] = [
        {"type": "lecture", "title": "核心讲解", "minutes": 20, "task": "看懂 T 的核心判断、机制、适用条件和边界，并写下一个自己最容易混淆的点。"},
        {"type": "worked_example", "title": "完整例题", "minutes": 20, "task": "按四步推演一个正例和一个反例，标出每一步为什么能让结论成立或翻转。"},
        {"type": "drill", "title": "分层练习", "minutes": 15, "task": "完成基础判断、边界判断和迁移判断，并为每题写一句排除干扰项的理由。"},
        {"type": "produce", "title": "主动产出", "minutes": 25, "task": "填写判断规则表模板，设计一个可检查的新场景判断方案，再对照参考答案修复一个缺少边界的错误方案。"},
        {"type": "reflection", "title": "回顾自查", "minutes": 10, "task": "按 rubric 检查产物，记录仍不确定的判断点，并写下一步需要补的知识。"},
    ]
    lesson["rubric"] = [
        "能说清 T 解决的真实问题和前置条件。",
        "能给出正例、反例和结论翻转的关键条件。",
        "能写出产物的验收标准，而不是只复述定义。",
    ]
    lesson["produce"]["task"] = (
        "设计一个新的真实场景判断方案：先写场景输入，再判断是否适用 T，"
        "给出一个正例和一个只差一个关键条件的反例，最后写出验收标准和改道理由；"
        "如果你的反例不成立，要说明缺了哪个条件，以及应该切到哪个相邻概念。"
        "填写模板：场景输入 / 适用条件 / 判断步骤 / 正例 / 反例 / 验收标准 / 改道理由。"
        "参考答案：合格样例会明确写出一个关键输入缺失后结论如何翻转，并给出可复现验收标准。"
        "高阶练习：设计评审并修复一个只写定义、没有反例和验收标准的错误方案。"
    )
    lesson["cards"].append({
        "type": "explain",
        "title": "加厚讲解：从判断规则到可检查产物",
        "body": (
            "90 分钟单元不能只让学习者知道 T 的定义，还要让他经历一次完整的判断迁移。"
            "第一层是规则复述：说清 T 解决哪类问题、依赖哪些输入、输出如何验收。"
            "第二层是边界翻转：把正例里一个关键条件拿掉，观察结论为什么必须改变。"
            "第三层是产物化：把判断过程写成别人能检查的方案，而不是停留在“我理解了”。"
            "如果学习者只能写出定义，说明还在记忆层；如果能解释反例为什么不成立，说明进入判断层；"
            "如果能把正反例、验收标准、改道理由写成一页方案，才算完成这一节的产出。"
            "这一卡还要训练学习者的自我纠错：当他发现自己的反例只是换了表述、没有改变关键条件时，"
            "必须回到输入条件重新构造；当他发现验收标准只能靠主观感觉判断时，必须补出第三方可检查的证据。"
            "这一步把“知道规则”推进到“能修订自己的判断过程”，是 90 分钟完整学习单元和短讲义的分界。"
            "最终交付时还要写清：谁来检查、检查哪些字段、失败后回到哪一步重做；缺少这三项，就不能判定为完成。"
            "如果检查者无法根据你的文本复现同一个判断，说明还要继续补证据。"
            "复现不了，就不是合格学习产物。"
        ),
        "items": [
            "规则复述：输入条件、判断步骤、验收标准三项必须同时出现，少一项就只是概念摘抄。",
            "边界翻转：反例只能改一个关键条件，这样才能看出到底是哪条规则让结论改变。",
            "产物化：最终答案要能被别人检查，因此必须包含正例、反例、改道理由和自查标准。",
        ],
        "expansions": [
            {
                "kind": "worked_example",
                "title": "从一个判断失败推到一组验收样例",
                "body": (
                    "完整学习不能停在“我知道 T 的定义”。先选择一个正例，写出它满足的目标、输入和产出；"
                    "再只改动一个关键输入，构造反例，并解释为什么结论必须翻转。接着把这次翻转变成验收样例："
                    "如果新场景仍满足三项条件，就应该选择 T；如果缺少关键输入，就必须写出改道理由，说明应该换到哪个相邻概念。"
                    "最后让另一个人只看你的样例和标准来判断答案是否成立，如果对方无法检查，说明你的产物还只是说明文字，"
                    "没有成为可复用的学习证据。这个推演链条把定义、边界、反例和迁移练习连成一个完整单元，才能支撑 90 分钟学习。"
                ),
            },
            {
                "kind": "boundary",
                "title": "哪些输出仍然不算完成",
                "body": (
                    "有些输出看起来完整，但还不能算完成。第一种是定义型答案：它把 T 说得很顺，却没有说明输入条件和验收标准。"
                    "第二种是例子型答案：它给了一个正例，但没有反例，无法证明学习者知道边界在哪里。第三种是模板型答案："
                    "它列出步骤，却没有解释为什么某个条件缺失会导致结论翻转。合格输出必须让另一个人能独立检查："
                    "这个场景为什么适用 T，哪个相邻场景为什么不适用，如果不适用应该改用什么判断。"
                    "只要这些问题答不上，就说明 90 分钟单元还没有完成，应该继续在当前节内修订产物，而不是把问题推给后续课程。"
                ),
            }
        ],
    })
    return json.dumps(lesson, ensure_ascii=False)


def _thin_full_unit_lesson_json() -> str:
    lesson = json.loads(json.dumps(RICH_LESSON, ensure_ascii=False))
    lesson["estimated_minutes"] = 90
    lesson["activity_plan"] = [
        {"type": "lecture", "title": "核心讲解", "minutes": 20, "task": "看懂 T 的核心判断、机制、适用条件和边界，并写下自己最容易混淆的相邻概念。"},
        {"type": "worked_example", "title": "完整例题", "minutes": 20, "task": "按步骤推演一个正例和一个反例，标出每一步为什么让结论成立或翻转。"},
        {"type": "drill", "title": "分层练习", "minutes": 15, "task": "完成基础判断、边界判断和迁移判断，并为每题写一句排除干扰项的理由。"},
        {"type": "produce", "title": "主动产出", "minutes": 25, "task": "设计一个可检查的新场景判断方案，包含输入条件、判断步骤、反例、验收标准和改道理由。"},
        {"type": "reflection", "title": "回顾自查", "minutes": 10, "task": "按 rubric 检查产物，记录仍不确定的判断点，并写下一步需要补的知识。"},
    ]
    lesson["rubric"] = [
        "能说清 T 解决的真实问题和前置条件。",
        "能给出正例、反例和结论翻转的关键条件。",
        "能写出产物的验收标准，而不是只复述定义。",
    ]
    return json.dumps(lesson, ensure_ascii=False)


def _section_responses_from_full_unit() -> list[str]:
    lesson = json.loads(_full_unit_lesson_json())
    cards = lesson["cards"]
    core = {
        "explain": lesson["explain"],
        "cards": cards[:4],
        "citations": [],
    }
    examples = {
        "explain": {"examples": lesson["explain"]["examples"]},
        "cards": [c for c in cards if c.get("type") in {"example", "explain"}][:3],
        "citations": [],
    }
    practice = {
        "practice": lesson["practice"],
        "cards": [
            {
                "type": "practice",
                "title": "边界判断练习",
                "body": "先判断场景是否满足 T 的目标、输入和产出，再排除只看名称的干扰项。",
                "options": ["直接使用 T", "先检查条件链", "只看关键词"],
                "answer": 1,
                "why": "T 的关键不是名称，而是条件链是否完整；直接使用和只看关键词都会误判边界。",
            }
        ],
    }
    produce = {
        "estimated_minutes": lesson["estimated_minutes"],
        "activity_plan": lesson["activity_plan"],
        "rubric": lesson["rubric"],
        "produce": lesson["produce"],
        "cards": [c for c in cards if c.get("type") in {"produce", "check"}],
    }
    return [json.dumps(x, ensure_ascii=False) for x in (core, examples, practice, produce)]


@pytest.fixture
def legacy(monkeypatch):
    """把单段式兜底链路固定下来，单独测旧行为。"""
    monkeypatch.setenv("ITUTOR_LESSON_PIPELINE", "0")
    monkeypatch.setenv("ITUTOR_LESSON_REVIEW", "1")
    monkeypatch.setenv("ITUTOR_LESSON_QUALITY_GATE", "0")


# --------------------------- 单段式兜底链路 ---------------------------

def test_legacy_review_pass_keeps_draft(legacy):
    c = FakeClient([DRAFT, REVIEW_PASS])
    lesson = _gen(c)
    assert c.calls == 2                 # 起草 + 自检
    assert lesson["topic"] == "T"
    assert lesson.get("_reviewed") is True
    assert lesson.get("_revised") is not True


def test_legacy_review_revise_uses_revision(legacy):
    c = FakeClient([DRAFT, REVIEW_REVISE])
    lesson = _gen(c)
    assert c.calls == 2
    assert lesson["topic"] == "T2"
    assert lesson["practice"][0]["answer"] == 1
    assert lesson.get("_revised") is True
    assert "答案标错了" in lesson.get("_review_issues", [])


def test_legacy_custom_instruction_reaches_draft_and_review(legacy):
    c = FakeClient([DRAFT, REVIEW_PASS])
    lesson = _gen(c, custom_instruction="更偏 AI 落地场景，减少术语")
    assert lesson["topic"] == "T"
    prompts = [ms[0].content for ms in c.messages]
    assert len(prompts) == 2
    assert all("更偏 AI 落地场景，减少术语" in p for p in prompts)


def test_legacy_review_garbage_falls_back_to_draft(legacy):
    c = FakeClient([DRAFT, "这不是 JSON，随便说点啥"])
    lesson = _gen(c)
    assert c.calls == 2
    assert lesson["topic"] == "T"
    assert lesson.get("_reviewed") is True


def test_legacy_review_failure_still_attaches_quality(legacy, monkeypatch):
    monkeypatch.setenv("ITUTOR_LESSON_QUALITY_GATE", "1")
    monkeypatch.setenv("ITUTOR_LESSON_QUALITY_REPAIR", "0")
    c = FakeClient([DRAFT, "这不是 JSON，随便说点啥"])
    lesson = _gen(c)
    assert c.calls == 2
    assert lesson["topic"] == "T"
    assert lesson.get("_reviewed") is True
    assert lesson["_quality"]["passed"] is False


def test_legacy_review_disabled_skips_second_call(legacy, monkeypatch):
    monkeypatch.setenv("ITUTOR_LESSON_REVIEW", "0")
    c = FakeClient([DRAFT])
    lesson = _gen(c)
    assert c.calls == 1
    assert lesson["topic"] == "T"
    assert lesson.get("_reviewed") is not True


def test_legacy_draft_fallback_skips_review(legacy):
    c = FakeClient([LLMError("boom")])
    lesson = _gen(c)
    assert c.calls == 1
    assert lesson.get("_fallback") is True


def test_legacy_quality_gate_repairs_shallow_lesson(legacy, monkeypatch):
    monkeypatch.setenv("ITUTOR_LESSON_QUALITY_GATE", "1")
    monkeypatch.setenv("ITUTOR_LESSON_QUALITY_REPAIR", "1")
    c = FakeClient([DRAFT, REVIEW_PASS, QUALITY_REVISE])
    lesson = _gen(c)
    assert c.calls == 3
    assert lesson["topic"] == "T 深化版"
    assert lesson.get("_quality_repaired") is True
    assert lesson["_quality"]["passed"] is True
    assert "本地内容质量门未通过" in c.messages[-1][0].content


# --------------------------- 三段式流水线（默认） ---------------------------

@pytest.fixture
def pipeline(monkeypatch):
    monkeypatch.setenv("ITUTOR_LESSON_PIPELINE", "1")
    monkeypatch.setenv("ITUTOR_LESSON_REVIEW", "1")
    monkeypatch.setenv("ITUTOR_LESSON_REASONER", "1")


def test_pipeline_design_write_critic_pass(pipeline):
    c = FakeClient([DESIGN_JSON, WRITE_JSON, CRITIC_PASS])
    lesson = _gen(c)
    assert c.calls == 3                       # 课设 + 写作 + 锐度评审(pass)
    assert lesson["topic"] == "T 深化版"
    assert lesson.get("_pipeline") == "design_write"
    assert lesson["_critic"]["verdict"] == "pass"
    assert lesson.get("_critic_revised") is not True
    # 课设的 angle 被锁进 lesson.design
    assert "前置条件" in lesson["design"]["angle"]
    assert "真实任务里如何判断 T" in lesson["design"]["learning_question"]
    assert lesson["design"]["key_steps"][0]["name"] == "识别问题类型"
    assert lesson["_quality"]["passed"] is True


def test_pipeline_accepts_fenced_nested_design_json(pipeline):
    c = FakeClient([f"```json\n{DESIGN_JSON}\n```", WRITE_JSON, CRITIC_PASS])
    lesson = _gen(c)
    assert c.calls == 3
    assert lesson.get("_pipeline") == "design_write"
    assert "前置条件" in lesson["design"]["angle"]
    assert lesson["_quality_loop"]["stop_reason"] == "critic_pass"


def test_pipeline_uses_reasoner_for_design_and_critic(pipeline):
    c = FakeClient([DESIGN_JSON, WRITE_JSON, CRITIC_PASS])
    _gen(c)
    models = [kw.get("model") for kw in c.kwargs]
    assert models[0] == "deepseek-reasoner"   # 课设走 reasoner
    assert models[1] is None                   # 写作走默认 chat
    assert models[2] == "deepseek-reasoner"   # 锐度评审走 reasoner


def test_pipeline_reasoner_off_uses_chat(monkeypatch):
    monkeypatch.setenv("ITUTOR_LESSON_PIPELINE", "1")
    monkeypatch.setenv("ITUTOR_LESSON_REVIEW", "1")
    monkeypatch.setenv("ITUTOR_LESSON_REASONER", "0")
    c = FakeClient([DESIGN_JSON, WRITE_JSON, CRITIC_PASS])
    _gen(c)
    assert all(kw.get("model") is None for kw in c.kwargs)


def test_pipeline_critic_revise_runs_revision(pipeline):
    c = FakeClient([DESIGN_JSON, WRITE_JSON, CRITIC_REVISE, REVIEW_RICH, CRITIC_PASS])
    lesson = _gen(c)
    assert c.calls == 5                        # 课设 + 写作 + 评审(revise) + 返工 + 再评审(pass)
    assert lesson["topic"] == "T 锐化版"
    assert lesson.get("_critic_revised") is True
    assert lesson["_critic"]["verdict"] == "pass"
    assert "_quality_before" in lesson
    assert lesson["_quality_loop"]["stop_reason"] == "critic_pass"
    assert lesson["_quality_loop"]["best_round"] == 1
    assert "内容加深修订环节" in c.messages[-2][0].content


def test_pipeline_quality_loop_respects_max_revisions(pipeline, monkeypatch):
    monkeypatch.setenv("ITUTOR_LESSON_MAX_REVISIONS", "1")
    c = FakeClient([DESIGN_JSON, WRITE_JSON, CRITIC_REVISE, REVIEW_RICH])
    lesson = _gen(c)
    assert c.calls == 4
    assert lesson["topic"] == "T 锐化版"
    assert lesson["_quality_loop"]["stop_reason"] == "max_revisions"
    assert lesson["_quality_loop"]["max_revisions"] == 1
    assert lesson["_quality_loop"]["best_round"] == 1


def test_pipeline_quality_loop_keeps_best_when_revision_regresses(pipeline):
    c = FakeClient([DESIGN_JSON, WRITE_JSON, CRITIC_REVISE, REVIEW_BAD])
    lesson = _gen(c)
    assert c.calls == 4
    assert lesson["topic"] == "T 深化版"
    assert lesson["_quality_loop"]["stop_reason"] == "candidate_regressed"
    assert lesson["_quality_loop"]["best_round"] == 0
    assert lesson["_quality_loop"]["rounds"][-1]["accepted"] is False


def test_pipeline_design_failure_falls_back_to_legacy(pipeline, monkeypatch):
    monkeypatch.setenv("ITUTOR_LESSON_QUALITY_GATE", "0")
    # 课设输出垃圾 → 解析失败 → 回退单段式（起草 + 自检）
    c = FakeClient(["设计阶段输出了非 JSON", DRAFT, REVIEW_PASS])
    lesson = _gen(c)
    assert c.calls == 3
    assert lesson["topic"] == "T"
    assert lesson.get("_pipeline") is None


def test_pipeline_passes_learner_signals_into_design(pipeline):
    c = FakeClient([DESIGN_JSON, WRITE_JSON, CRITIC_PASS])
    _gen(c, learner_signals="掌握度偏弱：助词 は/が")
    design_prompt = c.messages[0][0].content
    assert "掌握度偏弱：助词 は/が" in design_prompt


def test_pipeline_passes_session_contract_into_prompts_and_quality(pipeline):
    c = FakeClient([DESIGN_JSON, *_section_responses_from_full_unit(), CRITIC_PASS])
    lesson = _gen(
        c,
        session_contract={
            "label": "1–2 小时",
            "target_minutes": 90,
            "min_estimated_minutes": 70,
            "required_activity_types": ["lecture", "worked_example", "drill", "produce", "reflection"],
        },
    )

    assert "学习时长合同" in c.messages[0][0].content
    assert "学习时长合同" in c.messages[1][0].content
    assert c.calls == 6
    assert lesson["_pipeline"] == "design_sections"
    assert len(lesson["_sections"]) == 4
    assert lesson["_session_contract"]["target_minutes"] == 90
    assert lesson["estimated_minutes"] == 90
    assert lesson["_quality"]["passed"] is True


def test_pipeline_expands_thin_full_unit_before_critic(pipeline, monkeypatch):
    monkeypatch.setenv("ITUTOR_LESSON_SECTION_WRITER", "0")
    c = FakeClient([DESIGN_JSON, _thin_full_unit_lesson_json(), EXPAND_PATCH, CRITIC_PASS])
    lesson = _gen(
        c,
        session_contract={
            "label": "1–2 小时",
            "target_minutes": 90,
            "min_estimated_minutes": 70,
            "required_activity_types": ["lecture", "worked_example", "drill", "produce", "reflection"],
        },
    )

    assert c.calls == 4
    assert c.kwargs[2].get("model") is None
    assert lesson.get("_expanded") is True
    assert "_quality_before_expand" in lesson
    assert lesson["_quality"]["passed"] is True
    assert lesson["_quality"]["checks"]["dense_teaching_cards"] >= 3
    assert lesson["_quality"]["checks"]["expansion_blocks"] >= 3
    assert lesson["_quality_loop"]["stop_reason"] == "critic_pass"
    expand_prompt = c.messages[2][0].content
    assert "内容分段补强环节" in expand_prompt
    assert "不要输出完整 lesson" in expand_prompt
