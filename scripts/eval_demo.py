"""即时基线评估器 CLI demo。

跑法：
    .venv/bin/python scripts/eval_demo.py "日语 N3 备考" "N5 水平"
    .venv/bin/python scripts/eval_demo.py "前端中高级" ""
    .venv/bin/python scripts/eval_demo.py "营养学入门" "本科非生物背景" 5

参数：
    1. domain         (必需) 学习领域
    2. baseline_hint  (可选，默认空) 用户给的水平提示
    3. num_questions  (可选，默认 4) 题数 3-5

LLM 异常时会自动 fallback 到通用题集，主流程不中断。
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.evaluator import BaselineEvaluator  # noqa: E402
from core.llm_client import LLMConfigError  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1

    domain = sys.argv[1]
    baseline_hint = sys.argv[2] if len(sys.argv) > 2 else ""
    num_questions = int(sys.argv[3]) if len(sys.argv) > 3 else 4

    print("=" * 60)
    print(f"  Itutor · 即时基线评估 demo")
    print("=" * 60)
    print(f"  领域       : {domain}")
    print(f"  baseline   : {baseline_hint or '(未给出)'}")
    print(f"  num        : {num_questions}")
    print()

    try:
        evaluator = BaselineEvaluator()
    except LLMConfigError as e:
        print(f"❌ {e}")
        return 1

    print("→ 调 LLM 现场出题...")
    questions = evaluator.generate_questions(
        domain=domain,
        baseline_hint=baseline_hint,
        num_questions=num_questions,
    )

    print(f"\n收到 {len(questions)} 题：\n")
    for q in questions:
        print(f"[{q.id}] ({q.type}) 维度：{q.expected_dimension}")
        print(f"  问题：{q.question}")
        if q.options:
            for i, opt in enumerate(q.options, start=1):
                print(f"    {i}. {opt}")
        if q.answer_hint:
            print(f"  参考要点：{q.answer_hint}")
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
