# Changelog

> 遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) · 版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

---

## [0.53.0-rc.1] - 2026-09-06 · 首个公开源码候选版

### 新增

- 一套代码支持 `cloud` / `personal` 两种受后端 capabilities 约束的运行方式。
- Personal 提供 OpenAI-compatible BYOK、唯一 owner、Docker Compose、备份/恢复与可选 OpenClaw + 自有飞书应用。
- OpenClaw 插件通过 requester-scoped MCP 交换短时凭据；LearnBuddy 仍是计划、进度、掌握度、记忆和提醒的唯一真相源。
- Cloud 本地管理员初始化脚本，要求显式账号与当前密码校验。

### 安全与可靠性

- Cloud 基线评估的所有 LLM 入口统一经过额度门，避免平台 token 被绕过。
- Personal 误用多用户数据库时 fail closed，仅保留健康/能力诊断端点。
- 公开注册与登录不再按邮箱环境变量自动获得管理员权限。
- Web Push endpoint、退订和实例绑定补齐 owner 隔离与竞态保护。
- Personal 恢复在覆盖卷之前校验 manifest、SHA-256、归档可读性和路径安全。

### 验证边界

- Python 全量回归、前端语法与 Cloud/Personal 关键浏览器流程已通过。
- 当前为 RC；干净机 Docker、真实 OpenClaw Gateway/飞书消息往返和生产上线需继续独立验收。

## [0.0.1] - 2026-05-23 · V0 demo

> 5 天端到端构建：从 0 到一个能跑的 Itutor 学习系统。

### 新增

#### Day 0 · 工程骨架
- 3 份核心文档（`README.md` / `SPEC.md` / `CLAUDE.md`）
- 6 个 ADR（Architectural Decision Records）记录在 SPEC.md
- `.env.example` / `.gitignore` / `pyproject.toml` / `requirements.txt`
- `data/instances/_index.json` 空模板

#### Day 1 · LLM 基础设施
- `core/schemas.py`：pydantic v2 数据模型
  - `Message` / `DomainCategory` / `Intensity` / `InstanceStatus`
  - `EvalQuestion` / `EvalResponse`（双坐标系评估）
  - `Milestone` / `WeeklyTheme` / `LearningMethods` / `UserParams`
  - `DaySlot` / `WeekDay` / `WeekPlan` / `Instance`
- `core/llm_client.py`：DeepSeek 客户端封装
  - `LLMClient.chat()` 同步对话
  - `LLMClient.chat_stream()` 异步流式
  - `LLMConfigError` / `LLMError` 错误分类
- `scripts/smoke_test.py`：LLM 连通性自测
- `tests/test_schemas.py`：18 个单测全绿

#### Day 2 · 对话引擎 + 即时评估
- `core/prompts/system.md`：iTutor 教练 system prompt（7 阶段对话流 + GENERATE 协议）
- `core/prompts/evaluator.md`：评估题生成 prompt（双坐标系 + JSON 数组）
- `core/engine.py`：
  - `ConversationEngine` 同步 + 异步流式
  - `extract_generate_params()` GENERATE 标记解析（容忍各类 JSON 错）
  - `domain_to_slug()` 中文领域 → 英文 slug
- `core/evaluator.py`：
  - `BaselineEvaluator` LLM 现场出题
  - LLM 异常时回退到 5 题通用题集
  - JSON 提取容忍 markdown 包裹 / 前后多余文字
- `scripts/chat_demo.py`：终端流式对话（含 `:q` `:r` `:state` 命令）
- `scripts/eval_demo.py`：单次出题命令行
- `tests/test_engine.py` + `tests/test_evaluator.py`：46 个新单测全绿

#### Day 3 · 个性化生成器
- `core/prompts/learning_manual.md`：学习手册领域适配 prompt
- `core/generator.py`：
  - `build_master()` master.json（月度蓝图 + 周主题 + north_star）
  - `build_week_plan()` W1.json（周一三五日产出节奏 + 周末双段 + 周日 6 关自检）
  - `build_learning_manual()` 学习手册.md（含 LLM polish 操作要点 + 6 关具体标志）
  - `build_vision_contract()` 愿景与契约.md（含用户原话 + R1-R5 调整规则精简版）
  - `generate_instance()` 一键生成完整 5 件套 + 自动更新 _index.json + 冲突自动 -2 后缀
- `scripts/generate_demo.py`：一键日语 N3 12 周 demo
- `tests/test_generator.py`：24 个新单测全绿

#### Day 4 · FastAPI 服务 + Web UI
- `server.py`：7 个 endpoint
  - `GET /` / `GET /instance/{id}` 页面路由
  - `GET /assets/*` 静态资源
  - `GET /api/opening` 暖场问候
  - `GET /api/instances` / `GET /api/instance/{id}`
  - `POST /api/chat` SSE 流式（核心）
- `web/index.html`：对话首页
  - localStorage session_id
  - fetch + ReadableStream 流式 SSE 解析
  - GENERATE 检测 + 1.5s 自动跳转
- `web/instance.html`：路径展示页
  - 紫色渐变 hero + 4 metric-card
  - 月度时间线 + 周主题网格 + W1 日历 + 学习手册/契约 tab
  - marked.js CDN 渲染 markdown
- `web/assets/style.css`：紫色 (#6366F1) + 暖橙 (#F59E0B) 主题
  - 8px 网格 + 6/8/12/16px 圆角
  - 流式光标动画 (▋ blink)
  - markdown-body 表格 / blockquote / code 完整样式

#### Day 5 · 收口
- `scripts/start.sh`：一键启动脚本（检查 + 装依赖 + 启 server + 自动开浏览器）
- `docs/V1_BACKLOG.md`：V1 路线图（P0/P1/P2/P3 优先级 + 暴露的待修问题）
- `CHANGELOG.md`：本文件
- 完成 README 快速开始 3 步流程

### 修复
- `domain_to_slug` 「英语雅思」会被替换成「englishielts」粘连 → 改为按 key 长度降序 + 替换两侧加空格隔离
- `UserParams.weekly_total_hours` 用 `@field_validator(mode="before")` 不会触发自动计算 → 改为 `@model_validator(mode="after")`
- `_build_day_slots` 工作日产出 task 字符串构造越界 → 改为 `_WEEKDAY_OUTPUT_TASKS` 字典明确映射

### 验证
- 88 个单元测试全绿（schemas 18 + engine 25 + evaluator 21 + generator 24）
- LLM 真实联调：smoke / evaluator (6.2s) / generator (5.5s) / 多轮对话
- 浏览器 MCP 验证：对话首页 + 实例页面视觉效果通过

### 已知问题（V0 暴露）
- `V0-#1`: LLM 偶发出题选项重复 → P0-4 在 V1 修
- `V0-#2`: _index.json 与磁盘目录可能不同步 → P0-3 在 V1 用 DB 修
- `V0-#3`: session 重启丢失 → P3-1 引入 Durable State 修
- 详见 `docs/V1_BACKLOG.md`
