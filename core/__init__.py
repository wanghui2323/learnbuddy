"""LearnBuddy 学习伙伴核心引擎包。

主要能力：
    - core.schemas: pydantic 数据模型
    - core.llm_client: OpenAI-compatible 模型客户端（同步 + 流式）
    - core.engine: 对话引擎（system prompt + GENERATE 解析）
    - core.evaluator: 即时基线评估（LLM 现场出题）
    - core.generator: 个性化学习路径生成器
    - core.baseline: 真实能力诊断与可追溯画像
    - core.runtime: Cloud / Personal 运行边界

详细设计见项目根 SPEC.md。
"""

__version__ = "0.53.0rc1"
__all__ = [
    "__version__",
]
