# LearnBuddy 学习伙伴 · 工程 SPEC（原 Itutor 学习系统）

> 1 周 MVP 工程蓝图 + 5 天拆解 + 关键设计决策（ADR）
>
> 创建：2026-05-23 · 版本：V0 · 状态：Day 0 进行中

---

## 1. MVP 范围（V0 = 1 周端到端 demo）

### 1.1 必做（5 项核心闭环 · 砍掉任意 1 项 demo 不成立）

1. **对话引擎** — system prompt + 8–15 轮多轮对话 + `<<<GENERATE>>>` 标记触发
2. **即时基线评估** — LLM 现场生成 3–5 题领域适配测试，双坐标系（独立完成 vs AI 协作完成）
3. **个性化生成器** — 参数包 → 4 件套：
   - `master.json`（4–12 周月度蓝图 + 周主题）
   - `W1.json`（第 1 周日历，按周一三五日产出节奏）
   - `学习手册.md`（领域适配方法论 + 反遗忘 5 机制 + 6 关自检 6 维度翻译）
   - `愿景与契约.md`（用户原话嵌入 + R1–R10 调整规则精简版）
4. **单页可视化** — 对话首页（`index.html`）+ 学习路径展示页（`instance.html`，含 4 周时间线 + W1 日历 + 学习手册预览）
5. **一键启动 + 1 个完整演示场景**（日语 N3 备考 3 个月）

### 1.2 V0 砍（持续运营机制，demo 阶段用户还没开始用）

- ❌ 注册登录 / 多租户（用 `instance_id` 在 URL 里区分即可）
- ❌ 数据库（本地 JSON 文件 `data/instances/{id}/` 够了）
- ❌ 周报 / 月报生成器
- ❌ 进度追踪 / 卡点画像
- ❌ Anki 集成 / micro-review 自动化

### 1.3 V0 预埋占位（生成内容里就有，让用户看到完整愿景）

- ✅ **6 关自检模板**：作为学习手册的一节存在（领域适配的 6 维度，由 LLM 翻译）
- ✅ **反遗忘 5 机制说明**：写进学习手册（每个机制按领域翻译一行话）
- ✅ **周固定结构**：W1.json 里就按"周一三五日产出节奏"排时段
- ✅ **R1–R10 调整规则**：愿景与契约.md 末尾贴一份精简版

---

## 2. 技术栈

| 层 | 选型 | 备注 |
|---|---|---|
| 语言 | Python 3.11+ | 与原 `AI技术学习空间` 一致，便于经验迁移 |
| Web 框架 | FastAPI + uvicorn[standard] | 异步 + SSE 天然支持 |
| LLM SDK | `openai>=1.50` | DeepSeek 走 OpenAI 兼容协议 |
| LLM Provider | **DeepSeek**（`deepseek-chat`，base_url `https://api.deepseek.com`）| 价格低 / 中文好 / demo 阶段够用 |
| 数据建模 | pydantic v2 | 强类型 + JSON schema 自动 |
| 前端 | 纯 HTML + 原生 JS + 单 CSS | 5 天最快 |
| 持久化 | 本地 JSON 文件 | MVP 够用 |
| 测试 | pytest + httpx | 单测 + 集成测试 |
| 启动 | `uvicorn server:app` + 一键 `.command` 脚本 | macOS 友好 |

---

## 3. 项目结构

```text
Itutor学习系统/
├── core/                     # 可复用核心引擎包（pip install -e .）
│   ├── __init__.py           # 包入口 + 版本号
│   ├── schemas.py            # pydantic 数据模型
│   ├── llm_client.py         # DeepSeek 客户端（同步 + 流式）
│   ├── engine.py             # 对话引擎
│   ├── evaluator.py          # 即时基线评估
│   ├── generator.py          # 路径生成器
│   └── prompts/
│       ├── system.md         # LearnBuddy 学习伙伴 system prompt
│       ├── evaluator.md      # 评估题生成 prompt
│       ├── learning_manual.md  # 学习手册生成 prompt 模板
│       └── vision_contract.md  # 愿景契约生成 prompt 模板
├── web/
│   ├── index.html            # 对话首页
│   ├── instance.html         # 路径展示页
│   └── assets/
│       └── style.css
├── server.py                 # FastAPI 服务（路由 + SSE）
├── data/
│   └── instances/
│       └── _index.json       # 实例索引
├── scripts/
│   └── smoke_test.py         # LLM 连通性自测
├── tests/
│   ├── test_engine.py
│   ├── test_evaluator.py
│   └── test_generator.py
├── pyproject.toml
├── requirements.txt
├── .env.example
├── .gitignore
├── 启动.command
└── 文档：README.md / SPEC.md / CLAUDE.md
```

---

## 4. 5 天拆解 + 验收标准

### Day 0（今天 · 半天 · 进行中）— 工程基石

**产出**：3 份核心文档（README / SPEC / CLAUDE）+ 项目骨架文件 + git init。

**验收**：
- [ ] README.md / SPEC.md / CLAUDE.md 三件齐
- [ ] requirements.txt / pyproject.toml / .env.example / .gitignore 齐
- [ ] 目录结构创建完整（core/ web/ data/ tests/ scripts/）
- [ ] git 仓库初始化 + 首次 commit

### Day 1 — LLM 基础设施

**产出**：
- `core/__init__.py`（版本 + 包级别 API）
- `core/schemas.py`（pydantic 模型：`UserParams` / `EvalQuestion` / `Instance` / `WeekDay` 等）
- `core/llm_client.py`（DeepSeek 封装，支持同步 + 流式 SSE）
- `scripts/smoke_test.py`（验证 LLM 一次完整 chat 调通）

**验收**：
- [ ] `python scripts/smoke_test.py` 输出 LLM 真实回复
- [ ] 流式调用能逐 token 输出
- [ ] pytest 跑过 schemas 单测

### Day 2 — 对话引擎 + 基线评估

**产出**：
- `core/prompts/system.md`（LearnBuddy 学习伙伴 system prompt，参考原 `itutor_engine.py` 的 SYSTEM_PROMPT 但重写）
- `core/engine.py`：
  - `class ConversationEngine`：管理对话状态 + system prompt 加载
  - `extract_generate_params(text) -> Optional[dict]`（GENERATE 标记解析）
  - `domain_to_slug(domain) -> str`（slug 生成）
- `core/evaluator.py`：
  - `class BaselineEvaluator`：调 LLM 生成 3–5 题领域适配测试
  - 双坐标系评估（独立完成 vs AI 协作完成）
- `tests/test_engine.py` + `tests/test_evaluator.py`

**验收**：
- [ ] CLI 可模拟一轮 8 轮对话 → LLM 输出 GENERATE 标记 → 解析成功
- [ ] 给定"日语 N3"领域，evaluator 能生成 3 题日语题
- [ ] pytest 全绿

### Day 3 — 个性化生成器（核心交付物）

**产出**：
- `core/prompts/learning_manual.md` + `core/prompts/vision_contract.md`
- `core/generator.py`：
  - `class SystemGenerator`
  - `build_master(params, start_date) -> dict`（4–12 周月度 + 周主题）
  - `build_w1(params, start_date) -> dict`（第 1 周日历，按周一三五日节奏排时段）
  - `build_learning_manual(params) -> str`（领域适配 markdown，预埋反遗忘 + 6 关自检）
  - `build_vision_contract(params) -> str`（用户原话嵌入 + R1–R10 精简版）
  - `generate_instance(params) -> Path`（写到 `data/instances/{slug}/`，更新 _index.json）

**验收**：
- [ ] 给定示例参数包（日语 N3 / 12 周 / standard），生成完整 4 件套
- [ ] `data/instances/japanese-n3/master.json` 包含 12 周 schedule
- [ ] 学习手册.md 中"反遗忘 5 机制"+"6 关自检 6 维度"针对日语已翻译
- [ ] _index.json 自动追加这条实例

### Day 4 — Web 端（FastAPI + SSE + 双页面）

**产出**：
- `server.py`：
  - `GET /` → `index.html`
  - `POST /api/chat`（SSE 流式 chat，传入对话历史，返回流式 token + 末段 GENERATE 检测）
  - `POST /api/generate`（输入参数包，调 generator，返回 instance_id）
  - `GET /api/eval-questions?domain=...&baseline=...`（即时评估题）
  - `GET /instance/{id}` → `instance.html`（注入实例数据）
  - `GET /api/instance/{id}` → JSON 全量数据
- `web/index.html`：对话框 + 开场问候 + 流式渲染 + GENERATE 检测后跳转 instance 页
- `web/instance.html`：4 周时间线（甘特/卡片）+ W1 日历 + 学习手册渲染（marked.js）
- `web/assets/style.css`：紫色 + 暖橙主题（参考原项目）

**验收**：
- [ ] 浏览器打开 `localhost:8000` 看到对话框
- [ ] 完整聊一遍 → 自动跳到 `/instance/japanese-n3`
- [ ] 实例页能看到 4 周时间线 + W1 5 天日历 + 学习手册章节

### Day 5 — Demo 化 + 收尾

**产出**：
- `启动.command`（一键 chmod +x + uvicorn）
- `README.md` 快速启动段补完
- 1 个完整 `data/instances/japanese-n3/` 真实示例（手工跑一遍生成）
- V1 backlog 写入 README（6 关自检 / 反遗忘 / 多租户 / 持续运营）
- 录 5 分钟演示视频（QuickTime 屏录）

**验收**：
- [ ] `./启动.command` 双击能起服务
- [ ] 全新会话能从 0 走完"对话 → 评估 → 生成 → 展示"
- [ ] README 任何人能照着 5 步跑起来

---

## 5. 关键设计决策（ADR）

### ADR-001 · 抽出 `core/` 包，不复用原代码

**Context**：原 `AI技术学习空间/itutor_engine.py` + `itutor_generator.py` 已 dogfood 4 个月。
**Decision**：从 0 写 `core/`，不 copy 原代码。
**Reason**：
- 原代码是单文件、硬编码、个人 dogfood 风格
- 通用平台需要 pydantic 强类型 + 包结构 + 测试覆盖
- 完全隔离更干净，避免"copy 后不重构"陷阱
**Consequence**：第 1 天慢一点，第 2 周起加速。

### ADR-002 · DeepSeek 直连 + OpenAI 兼容 SDK

**Context**：原项目走云雾代理（多模型路由）。
**Decision**：MVP 直连 DeepSeek，`deepseek-chat` 主用。
**Reason**：
- 单供应商最简，1 个 API key 搞定
- DeepSeek 中文质量好 + 价格低（V3 $0.27/1M input）
- 流式协议和 OpenAI 一致，未来切 provider 改 base_url 即可
**Consequence**：`llm_client.py` 抽象到能 1 行切 provider，但 V0 不实装多 provider。

### ADR-003 · Day 1 就上 SSE 流式

**Context**：可选先同步、第 4 天再加流式。
**Decision**：Day 1 写 `llm_client.py` 时就内置 stream 接口。
**Reason**：
- 流式是对话产品的核心 UX，不能砍
- DeepSeek 流式协议成熟，多花半天值得
- 避免第 4 天大改架构
**Consequence**：`llm_client.py` 复杂度 +30%，但下游所有调用直接用。

### ADR-004 · 5 大机制中只跑通 3 个

**Context**：完整 5 机制需要至少 4 周工程。
**Decision**：V0 只跑通"对话 + 评估 + 生成"，剩余"反遗忘 + 6 关自检 + 持续运营"以模板预埋在生成的内容里。
**Reason**：
- demo 阶段用户还没开始 4–12 周路径，运营机制无意义
- 但生成的内容里必须有"完整骨架"，让用户看到产品愿景
**Consequence**：`learning_manual.md` 和 `vision_contract.md` 模板要做得"看上去很专业"，不能空白。

### ADR-005 · 默认 demo 场景 = 日语 N3

**Context**：原项目 dogfood 是 AI 全栈学习。
**Decision**：第 1 个对外 demo 用日语 N3，跳出 AI/技术圈。
**Reason**：
- 验证"通用领域引擎"不是口号
- 语言学习用户基数最大、需求最明确
- N3 是公认标准，路径合理性可被外部验证
**Consequence**：Day 5 录 demo 时用日语 N3 跑通，但代码层面任何领域都行。

### ADR-006 · 完全隔离原项目，不反向插拔

**Context**：可选第 1 天就把 core 反向接到原项目。
**Decision**：MVP 期间原项目纹丝不动，新项目独立从 0。
**Reason**：
- 原项目还在 dogfood，反向插拔有风险
- core API 还没稳定，反向接进去会拖累迭代速度
- demo 跑通后再考虑（V0.5 阶段）
**Consequence**：原项目 5 月份继续按它的节奏跑，本项目独立推进。

### ADR-016 · harness = 模型 + agent + 全部外部工程

**Context**：项目当前文档里没有 harness 的官方定义，导致“harness 相关能力”边界模糊；与此同时，用户希望借 LearnBuddy 把当前主流 harness 技术体系都实践一遍。如果不在 SPEC 里锁定定义，后面 harness 对齐文档和排期会跑偏。

**Decision**：
- harness 在本项目中的口径 = **模型 + agent + 让模型真正可用的全部外部工程**。
- 至少包含：模型接入、工具 / MCP、上下文工程、记忆、Agentic Loop、Skill、输出解析与护栏、调度、身份、观测、运行时、安全 / 攻防、Eval 与 Token 经济。
- 边界判定：**没模型就跑不起来的能力** = harness；纯确定性能力（如 SM-2 调度、SQLite 索引）= 工程基建，不属于 harness。
- 任何 harness 相关章节必须能映射到 [`docs/Harness技术体系对齐.html`](docs/Harness技术体系对齐.html) 的 23 篇骨架；新增篇目需先更新该页再写代码。
- 三条边界：不变成 AI 名词百科 · 不被热点牵着跑 · 不退回工具测评视角。

**Reason**：
- 把“harness”锁成“可验证的工程清单”，让所有讨论有共同对象
- 让“借 LearnBuddy 过一遍主流 harness”这件事可以直接拆成 17 个新任务（T-21 → T-37），不再停留在口号
- 避免后面又把 OpenClaw / 个人助理模式当成新层，重新分裂术语

**Consequence**：
- `core/` 的现有能力必须能映射到 23 篇；无法映射的，要么归到“工程基建”要么补 harness 对齐
- `docs/02-技术方案.md` / `docs/02-技术方案.html` 必须新增“Harness 技术体系总图”章节并指向对齐页
- `docs/03-开发方案与计划.md` 的待办必须包含 Harness 对齐扩展任务（T-21 → T-37）

### ADR-017 · OpenClaw 接入方案 = 个人助理升级模式（Pro）的承载层

**Context**：ADR-010 已在 2026-06-18 定下 OpenClaw “不 fork + 双层部署” 决策方向，但实际方案散在 8 个文档里，没有独立专题页，也没有跨 `docs/Harness 技术体系对齐` 的映射。后续要落地 T-38 → T-44 任务时，会缺乏共同语言。

**Decision**：
- 锁定 OpenClaw 在 LearnBuddy 体系里的定位：**个人助理升级模式（Pro）的运行时和承载层**，不替代主产品。
- 落地物：`docs/06-OpenClaw接入方案.md` + `docs/OpenClaw接入方案.html` + `docs/OpenClaw接入方案.svg`，包含双层定位、接入架构图、LearnBuddy 侧与 OpenClaw 侧接入清单、最小验证路径、安全边界、T-38 → T-44 排期。
- 在 [`docs/Harness技术体系对齐.html`](docs/Harness技术体系对齐.html) 新增 “OpenClaw 在 23 篇里的对应” 章节，把 OpenClaw 跨 L4/L5 多篇的落地状态写明。
- 所有 HTML 顶部导航加入 `OpenClaw` 入口。
- 沿用 ADR-010 的硬约束：**不 fork 源码 · 不替用户托管实例 · 学习真相源与账本仍在主系统**。

**Reason**：
- 把 OpenClaw 从“散在各处的提及”升级为“可独立打开的专题页”
- 让 harness 对齐与 OpenClaw 接入保持一致口径，避免再分裂
- 给 T-38 → T-44 一个明确的优先级（M4/M5），并明确属于“辅助目标”，不挤占产品主线

**Consequence**：
- 后续所有 OpenClaw 相关讨论必须能映射到 `OpenClaw接入方案.html` 章节
- 修改 OpenClaw 接入边界（账本 / 学习真相源 / 安全）必须同步更新 ADR-017 与专题页
- “中心化飞书机器人”属于 T-41 原型阶段，必须先做 dogfood 再决定是否投入

### ADR-018 · 三目标对齐：产品 > 技术 > 文章

**Context**：项目里同时存在三个目标：完成产品 / 落地前沿技术 / 输出公众号文章。它们会在排期和取舍时打架。如果不固定优先级，后面会反复纠结“先做产品还是先做技术 demo”或“先赶文章还是先做产品”。

**Decision**：
- **首要目标**：完成软件开发，构建一个好的产品
- **辅助目标**：落地所有前沿技术方案（含 harness 主流体系 + OpenClaw 个人助理升级模式）
- **间接目标**：输出公众号文章系列
- 优先级固定：**产品 > 技术 > 文章**
- 当三者冲突时，先砍文章、再砍技术 demo，绝不砍产品主线
- 任何 harness 对齐任务（T-21 → T-37）与 OpenClaw 接入任务（T-38 → T-44）都属于“辅助目标”，必须挂在 M4/M5，不能挤占“产品主线”4 周排期（T-01 → T-20）

**Reason**：
- 避免“为了做 harness 对齐而让产品变慢”或“为赶文章阻塞产品”
- 让所有任务都有清晰归属，对应到目标层级
- 让公众号文章成为“产品与技术的副产品”，而不是独立阻塞线

**Consequence**：
- `docs/开发方案与计划.html` 必须新增 “三目标对齐” 章节并放在整体判断之前
- 任何新增任务必须标 “首要/辅助/间接” 层级
- 文章输出节奏跟随产品/技术节奏，不独立排期

### ADR-019 · #12 知识星图 = 已建成（Batch 2D 收口）

**Context**：AGENTS.md 把 `#12 知识星图` 列为 “下一候选”，但 `core/concepts.py` + `core/mastery.py` + `core/phases.py` + `server.py /api/instance/{id}/graph` + `web/instance.html` 三个 tab（疆域地图 / SVG 星图 / 周视图）已全部就位；浏览器实测前没人写过端到端验证。这条 ADR 把状态从 “候选” 收口为 “建成”，并把验证记录在案。

**Decision**：
- #12 知识星图 = **已建成**，不再列入下一候选
- API：`GET /api/instance/{instance_id}/graph` 返回 `{source, threshold, counts, phases, concepts}`，`concepts` 含 `{id, name, week, difficulty, prerequisites, prereq_names, unlocks, status, mastery, objective_pct, self_label, due}`，status ∈ {`mastered` / `frontier` / `locked`}
- 前端三个 tab：
  - 知识疆域地图（map）= 列表式 + 阶段闸门条 + 已征服/前线/未探索三段
  - 知识星图（star）= 真 SVG，按周分列 + 按难度分行 + 节点环=掌握度 + 状态色（绿/青/灰）+ 滚轮缩放 + 拖拽平移 + 悬停 dim 高亮依赖边 + 锁定节点点击提示前置
  - 周视图（weeks）= 原周列表
- 浏览器实测（首次）：用 Playwright headless 跑 /instance/{id}，截三个 tab 的图，发现问题修一轮（详见本次实测记录）
- 下一候选（首批）：手机号验证码登录（支付前置）/ 6 关自检交互页（V0 模板→真实交互）/ FSRS 调度

**Reason**：
- #12 已经在“主路径 + 锁定机制 + 阶段闸门”三个产品核心差异点上支撑住了
- 不再用 “重新写 #12” 浪费时间；改为 “收口 + 端到端实测”
- 让“下一候选”清单反映真实剩余缺口，而不是“看起来还没做”

**Consequence**：
- AGENTS.md 第 119 行 “下一候选” 同步改为 “下一候选 = 手机号验证码登录 / 6 关自检交互页 / FSRS 调度”
- `core/concepts.py` / `core/mastery.py` / `core/phases.py` 后续改动必须回归 `/graph` API + SVG 渲染，不能破坏三 tab
- 下一站 P0 = 把手机号验证码 + 6 关自检交互页这两个产品主线阻塞点之一打通

### ADR-020 · 6 关自检交互页（V0 模板预埋 → 真实交互）

**Context**：AGENTS.md 把 “6 关自检” 列为 V0 模板预埋状态，实际只有 `LearningMethods.self_check_dimensions` + 学习手册里的 6 关通过标志表，**没有任何交互页/数据回流**。每周日用户无去去做，R1 规则也无法触发。这条 ADR 把状态升级为真实交互。

**Decision**：
- 数据层：[`core/self_check.py`](core/self_check.py) 新增 `weekly_self_checks` 表（user_id, instance_id, week, scores_json, notes_json, passed_cnt, r1_action, ai_feedback, created_at），同 (user, instance, week) 唯一 + 二次提交覆盖
- API：
  - `POST /api/instance/{id}/self-check` — 提交 6 关评分 + 笔记 + 可选 AI 反馈
  - `GET /api/instance/{id}/self-check/latest` — 最近一次（前端预填）
  - `GET /api/instance/{id}/self-check/history?limit=12` — 历史（雷达/趋势）
- 业务规则：`passed_cnt ≥ 4 = advance / 3 = reduce / 1-2 = repeat / 0 = reset`，R1 复用学习手册 R1 规则
- 前端：[`web/self-check.html`](web/self-check.html) 交互页 + `server.py` `/self-check/{id}` 路由 + `web/space.html` hero 加 「6 关自检」入口
- AI 反馈：提交成功后异步调 `/coach` 拿一段 ≤200 字反馈，写回 `ai_feedback` 字段
- 不做：评分修改/删除历史（自检是状态快照，不允许追溯篡改）

**Reason**：
- “6 关自检” 是 V0 反遗忘 5 机制（间隔重复/周回测/月度雪球）的核心起点，没它 R1 调不起来
- 数据模型在 [`docs/02-技术方案.md`](docs/02-技术方案.md) `WeeklySelfCheck` 已有但未实现，填上技术债
- 该页能闭环：用户评分 → R1 判断 → AI 反馈 → 下周路径推荐，是 LearnBuddy 区别于一般 ChatGPT 的可验证点

**Consequence**：
- AGENTS.md “V0 反遗忘 5 机制” 中 6 关自检 从 “模板预埋” 升级为 “已建成”
- 手机号验证码登录 仍为下一站 P0 候选
- 后续要加：6 关门门槛个可配置、教练主动接驳 Web Push 提醒自检
- 雷达图已在 V0.24 实现（详见 [ADR-021](SPEC.md#adr-021--6-关自检雷达图visualization--v024)）

### ADR-022 · FSRS-4.5 调度（替换 SM-2，env 切换 · V0.25）

**Context**：ADR-018 三目标 = 产品 > 技术 > 文章。`core/scheduler.py` 已有的 SM-2 是 Anki 早期 1987 年算法（3 档自评 + ease factor 启发式），缺 DSR 模型（Difficulty/Stability/Retrievability），对真实"答错-重学-再答"模式预测精度有限。FSRS（Free Spaced Repetition Scheduler，2022+）用 DSR 模型 + 17 权重默认 + R(t,S)=(1+FACTOR·t/S)^DECAY 曲线，是开源事实标准（[open-spaced-repetition/awesome-fsrs](https://github.com/open-spaced-repetition/awesome-fsrs)）。

**Decision**：
- 新增 [`core/fsrs.py`](core/fsrs.py)：FSRS-4.5 17 权重默认 + v4 线性 D0 公式（`D0(G) = w4 - (G-3)·w5`，与 17 权重版本匹配）
- 评分映射：LearnBuddy 3 档（cant/with_help/independent）→ FSRS 4 档（1/3/4）
- [`core/scheduler.py`](core/scheduler.py) 加 `next_interval(ratings, elapsed_days_list=None, algorithm=None)` 统一入口
  - `algorithm="sm2"` → 原有 SM-2
  - `algorithm="fsrs"` → 新增 FSRS
  - `algorithm=None` → env `ITUTOR_SCHEDULER` 选，缺省 sm2
- [`core/learning.py`](core/learning.py) 改用 `next_interval()` 替代 `sm2()`，向后兼容
- 不做：FSRS 短期记忆的 w17/w18 微调（已并入默认权重）
- 不做：把 events 表的 review_timestamp 算出 elapsed_days_list（待后续）

**Reason**：
- 评分曲线是"7:3 混合复习"质量的核心，FSRS 准确度比 SM-2 高 ~20%（业界共识）
- 现有数据无真实 elapsed → FSRS 默认"紧跟复习"行为保守，恰好与 SM-2 持平，不破坏体验
- 加 env 开关，让运营侧 A/B 验证：先小流量 fsrs → 全量切

**Consequence**：
- [`scripts/verify_fsrs.py`](scripts/verify_fsrs.py) 端到端 **60/60 PASS**（FSRS 数学 + 17 权重 + D/S 状态机 + 答错重置 + 答对增长 + scheduler 入口 + env 切换 + learning 集成 + clamp/NaN 边界）
- 任何调度器升级都通过 `next_interval()`，未来切到 FSRS-5（19 权重）只需替换 W
- 默认仍是 SM-2，不破坏线上已部署用户的体验

### ADR-023 · Web Push 端到端收口（V0.26 · 验证 + 补漏）

**Context**：Web Push + PWA 基建在 V0.18 已收（[`core/notify.py`](core/notify.py) [`core/push_subs.py`](core/push_subs.py) [`core/push_web.py`](core/push_web.py) [`core/push_scheduler.py`](core/push_scheduler.py) + `/api/push/*` 4 端点 + `web/sw.js` + `web/manifest.json`），但**没有端到端验证脚本**，只靠人脑 review。手机号 + 付费阻塞期间，先把基础设施测全。

**Decision**：
- 新增 [`scripts/verify_push.py`](scripts/verify_push.py) 60 项端到端验证：模块 import / payload 形态 / push_subs CRUD / 鉴权 / VAPID 优雅降级 / 410 清理 / sw.js 关键事件 / manifest 关键字段 / 多页 sw.js 注册
- 修 [`web/space.html`](web/space.html)：补 `navigator.serviceWorker.register('/sw.js')`（之前只在 instance.html 注册，space.html 用户首屏会错过 PWA 入口）
- 不做：rate limit、sw.js 版本号（V0.18 已实现，验证通过即可）

**Reason**：
- V0.18 主体代码已 OK，但任何"看起来对"的代码必须有 verification（AGENTS.md §2 "没法验证 = 不能合"）
- space.html 漏注册 sw.js → 用户在首页不会被推到 PWA 状态，Push 自然收不到
- 60 项验证脚本未来防回归（VAPID 配错 / sw.js 改坏 / 鉴权漏掉 都能秒查）

**Consequence**：
- [`scripts/verify_push.py`](scripts/verify_push.py) **60/60 PASS**（4 模块 + 4 API + 4 SW 关键事件 + 5 manifest 字段 + 2 页 sw.js 注册 + 11 端点 + 10 模块函数 + 边界降级）
- Web Push 链路从「凭印象 review」升级到「自动化端到端验证」
- 下一站可立刻接入产品：把 push 提醒（每日推荐 / 6 关自检）通过 `/api/instance/{id}/plan` 推送到用户手机

### ADR-024 · 6 关自检 + 雷达图联通 Web Push（V0.27）

**Context**：V0.23 6 关自检交互页 + V0.24 雷达图 已收，但用户经常忘记做（无触发）。V0.26 Web Push 主体已建（VAPID 已配），但 daily_push_all() 只发 daily_plan 一种。**核心阻塞**：6 关自检做了 V0.23/24 那么漂亮的可视化 + 历史 + 雷达图 + 趋势线，没用户触发就完全是 dead code。

**Decision**：
- [`core/notify.py`](core/notify.py) 加 `build_selfcheck_reminder(latest, *, instance_id, base_url)` + `should_remind_selfcheck(latest)` 状态机
  - first：用户从未做过（latest 为 None/空/坏）
  - weekly：距上次 ≥ 7 天
  - force：距上次 ≥ 14 天（streak 告急）
  - 否则返回 None（不打扰）
- [`core/push_subs.py`](core/push_subs.py) `already_sent_today` + `mark_sent` 加 `kind` 参数（默认 'daily'），用 `day+kind` 复合 key 落到同一 UNIQUE INDEX（不动 schema）
- [`core/push_scheduler.py`](core/push_scheduler.py) `daily_push_all()` 拆成两阶段：先 daily plan（kind='daily'），再 6 关自检（kind='selfcheck'）
- [`web/sw.js`](web/sw.js) `notificationclick` 按 `kind` 区分 prefix：selfcheck 跳 /self-check/，daily 跳 /instance/
- 不做：把 daily push + selfcheck 合并成一条（用户分开看更清晰）

**Reason**：
- V0.23-24 投资巨大却没用户触发 → 联通才能让"产品"体验完整
- streak 是 6 关自检的核心驱动力（怕掉），force 提醒（14 天）专打这个痛点
- 复合 key 不破坏 schema + 不需 migration → 一次部署生效
- 边界保护：空 dict / 坏 created_at 都视为 first（不漏掉）

**Consequence**：
- [`scripts/verify_push.py`](scripts/verify_push.py) 60/60 PASS（在 V0.26 60 项基础上增 11 项 6 关自检推送测试：状态机 + payload 形态 + 同日不同 kind 独立 + 边界 + sw.js 区分）
- 用户日推送分两条（daily + selfcheck），不合并不打扰
- 自检点开通知 → 直达 /self-check/{id}（v0.26 之前会跳错页）

### ADR-025 · 学练单元从静态讲义升级为互动学习链（V0.28）

**Context**：用户反馈学习页内容呈现“像一大段讲义”，互动性、学习节奏感和知识设计感不足；同时“重新生成”只能覆盖内容，无法承接用户真实要求。当前 lesson schema 已有 `explain / produce / lab / practice / summary`，但前端渲染层没有把它组织成清晰学习链。

**Decision**：
- 学练单元固定成“先想 → 看懂 → 自己讲 → 动手/练习 → 收束”的节奏轨；顶部只保留一个可读步骤轨，不再叠加圆点进度。
- 讲解内容拆成结构化卡：核心判断、直觉模型、规则、真实例子、易错点，避免长文压屏。
- 主动产出阶段必须有自查清单与参考要点，用户写完再对照，不直接把答案顶在眼前。
- 练习阶段必须即时反馈：选项给对错状态，解释框区分“为什么对 / 常见误区”，下一题按钮只在作答后开放。
- 总结阶段显示客观答对率、主观自评、下一步去向，并把“完成本节”作为明确动作。
- 重新生成支持用户输入本次要求（如“更偏案例 / 更深 / 用 FDE 和 AI 落地场景”），后端传入内容生成与质检 prompt；旧调用不传要求仍保持兼容。

**Consequence**：
- 先以现有 lesson schema 增量升级，不迁移历史 lesson cache；旧缓存可直接用新渲染体验打开。
- 后续深度搜索、多素材收集、多智能体备课工作流可以继续扩展到内容生成层，不需要重写学习页交互骨架。

### ADR-026 · 内容引擎 2.0 先落地“来源层 + 教学卡片链”（V0.29）

**Context**：用户反馈当前学习内容“太拉垮”，本质不是 UI 单点问题，而是 lesson 生成仍像“一次性让模型写讲义”：缺少深度素材、权威来源、教学设计链和可交互内容结构。继续只改页面会掩盖根因。

**Decision**
1. **最小闭环先做三层**：来源层（用户知识库 RAG + 可选联网搜索）→ 教学卡片链（`cards[]`）→ 质检修订（检查事实、难度、互动形态）。
2. **lesson schema 向后兼容**：继续保留 `explain / produce / lab / practice / citations`；新增 `cards[]` 作为前端优先渲染结构。旧 lesson cache 没有 `cards` 时，由后端解析层从旧结构自动合成基础卡片。
3. **来源搜索真实降级**：接入 `core/source_search.py`，读取 `BOCHA_API_KEY` 后才联网；未配置、搜索失败或无结果时返回状态包，不中断出课，也不伪造来源。
4. **教学卡片最低标准**：每节至少尝试生成 `goal / source / think / visual|compare / example / produce|check`，让学习页从“读长文”变成“目标 → 来源 → 判断 → 图解/对比 → 例子 → 输出”的节奏。
5. **多智能体暂不一次性上**：本轮不引入复杂 agent 框架，先用 workflow 契约和质检 prompt 固化输出；后续再拆 Research Agent / Curriculum Agent / Lesson Agent / Critic Agent。

**Consequence**
- 内容质量问题进入生成链路解决，而不是继续靠前端包装。
- 用户已有数据、lesson cache、学习记录无需迁移；新字段天然增量。
- 后续可把 Bocha、B 站、论文/教材源、手动知识库统一塞入来源层，再让 Lesson Agent 消费。

### ADR-027 · 学练内容密度与步骤回看（V0.30）

**Context**：用户认可“八个环节”的学习节奏，但反馈每个环节内容太浅；同时已学过的环节只能单向往后走，无法回看，导致学习过程不符合真实复习习惯。

**Decision**
1. **内容深度进入生成契约**：lesson prompt 明确要求每张核心卡至少包含两类信息：关键判断、具体例子或反例、边界条件、自查标准。深度来自可操作判断，不靠堆长段落。
2. **质检负责拦浅内容**：lesson review prompt 把“单句泛泛解释、没有判断规则或可检验标准”列为必须修订的问题。
3. **生成预算提高**：lesson 生成与质检 token 上限从 3600 提到 5200，给更完整的教学链、例子和练习留空间。
4. **已解锁步骤可回看**：学习页顶部步骤轨改成可点击按钮；已完成或已到达环节允许回看，未解锁环节保持锁定。
5. **回看不破坏进度**：先想、产出、练习、自评状态在当前学习页内保留；重复打开已答练习不会重复累计正确率、完成记录或积分奖励。

**Consequence**
- 新生成或重新生成的课程会更深；历史缓存不会被自动覆盖，避免无感消耗 token 和影响已有学习记录。
- 学习页从“一次性往后翻”变成“可回看、可对照、可复盘”的学习链，符合真实学习过程。

### ADR-028 · 内容质量契约进入生成工作流（V0.31）

**Context**：用户明确指出教学内容生成是 LearnBuddy 的核心环节，不能只靠“一个 prompt 调模型”。真正要保障效果，需要像内容工厂一样有资料、结构、质检和可回放的质量门。

**Decision**
1. **先做受控工作流，不做自由 Agent**：当前落地形态是 `起草 → LLM 质检 → 本地质量门 → 必要时定向返工`，后续再拆 Researcher / Planner / Lesson Builder / Critic。
2. **新增本地 Quality Contract**：`core/content_quality.py` 用确定性规则检查 lesson 是否满足教学链最低标准：6 张卡、必要卡型、互动卡、卡片深度、练习质量、主动产出、来源诚实。
3. **本地质量门默认开启**：`ITUTOR_LESSON_QUALITY_GATE` 可关；默认不依赖模型，只给出 `_quality` 报告。
4. **不合格最多返工一次**：`ITUTOR_LESSON_QUALITY_REPAIR` 默认开启；当模型自检放行但本地质量门发现“太浅/无互动/没来源/练习弱”时，把具体问题塞回评审 prompt，让其生成修订版，避免无限循环。
5. **质量可观测**：每节 lesson 返回 `_quality`、必要时返回 `_quality_before` / `_quality_repaired`，后续可进入后台质量分析和 golden case 评测。

**Consequence**
- 内容质量从“希望模型听话”升级为“系统可检查、可追责、可返工”。
- 这仍兼容旧 lesson schema 和缓存；旧缓存不强制迁移，新生成/重新生成内容先受益。
- 后续可以在同一质量门前面继续接深度搜索、B 站/论文/书籍素材包和多 Agent 教研链路。

### ADR-029 · 对话入口必须有语义起疑与会话锚定（V0.32）

**Context**：用户在创建学习路径时输入“harness”等多义技术词，系统曾反复把同一概念重新澄清，甚至在用户已说明“AI 领域的 harness 工程”后又回到默认追问。这说明入口对话不能只靠模型临场理解，也不能把某个词写死成单一语义。

**Decision**
1. **起疑不等于打断**：对多义概念先给“最可能语境 + 可纠正”确认，而不是直接替用户定死；普通目标继续进入正常对话。
2. **确认后写入会话锚点**：用户在最近对话里确认过语境后，后续同一会话复用该语义锚点，不再重复问同一类问题。
3. **搜索是澄清辅助，不是直接答案**：概念不清时可先拉公开来源或内置权威来源，用于判断最可能语境；最终仍以用户目标和纠正为准。
4. **输入交互尊重中文输入法**：主输入框和“其他补充”支持 Enter 发送、Shift+Enter 换行；输入法组词/选字阶段的 Enter 不触发提交。
5. **具体学习路径页只保留返回学习空间**：项目内部不再露出“新建学习/新建学习路径”入口，避免用户在当前路径里误以为要创建新任务。

**Consequence**
- LearnBuddy 的入口对话从“一次性问答”变成“起疑 → 查证 → 锚定 → 继续推进”的轻量工作流。
- 这不迁移历史会话和学习数据，只改变新对话的澄清与输入体验。
- 后续可继续把来源搜索、术语消歧、用户目标记忆沉淀为更完整的 onboarding agent。

### ADR-030 · LessonFlow / source_pack / Critic 三件套增量落地（V0.33）

**Context**：AI-Shifu 对比分析后，确认 LearnBuddy 当前最关键的短板不是单个页面 UI，而是“内容生成 → 学习运行 → 质量回流”还没有形成稳定运行时。现有 `lesson` 已有 `cards[]`、`_source_pack`、`_quality` 雏形，但三者彼此松散，旧缓存和新生成内容在前端仍主要靠固定阶段拼装。

**Decision**
1. **新增 LessonFlow 运行时层**：新增 `core/lesson_flow.py`，把旧 `lesson` 兼容升级为 `_lesson_flow`：`elements[]` 统一承载 source / think / explain / example / practice / produce / check / reflect 等环节，保留旧 `explain / cards / practice / produce` 不删。
2. **旧数据零迁移**：缓存命中或新生成后都调用 `attach_lesson_flow()`；旧 lesson cache 只是在读取时补 `_lesson_flow`，不改变历史学习记录，不要求批量迁移。
3. **source_pack 从“搜索结果”升级为“资料包”**：`build_source_pack()` 返回 query_plan、source_type、authority_score、freshness_score、relevance_score、claims、ambiguity 等元数据；前端仍可用 `_sources` 简表展示。
4. **Critic 结果结构化**：本地质量门在原有总分/问题列表基础上新增维度分：accuracy / depth / source_coverage / interactivity / actionability / rhythm，LessonFlow 元素带 `quality` 摘要，便于后台和前端解释“为什么这节不够好”。
5. **前端优先消费 LessonFlow**：学习页讲解阶段优先渲染 `_lesson_flow.elements` 合成的教学卡片；仍保留旧 lesson cards 的兜底，避免旧数据打不开。

**Consequence**
- 这是“课程运行时地基”的第一步，不引入复杂多智能体框架，不改数据库 schema，不动线上 `data/`。
- 后续 Research Agent / Planner / Lesson Builder / Critic Agent 可以围绕 LessonFlow 扩展，而不是继续在页面里堆逻辑。
- 学习内容从“模型输出一段结构”升级为“系统可追踪的学习对象”：可回看、可重生成、可质检、可分析。

### ADR-031 · 学练内容必须先课设后出课（V0.34）

**Context**：用户提供 `627.md` 作为参考，指出当前 lesson 虽然有 8 个环节，但内容仍容易变成浅层讲义：不知道本节到底解决什么问题、每个环节为什么出现、学完能做什么，也缺少贯穿主线和可观察的验收标准。

**Decision**
1. **lesson schema 新增 `design` 一等字段**：包含 `learning_question / outcome / main_thread / boundary / key_steps / quick_checks`；旧 lesson 缺字段时运行时补轻量课设骨架，不迁移历史数据。
2. **生成 prompt 先做六阶段课设**：借鉴“资料输入 → 理解判断 → 主线设计 → 节点打磨 → 快速验证 → 验收标准”，要求模型先确定最小可学问题，再写卡片链和练习。
3. **评审 prompt 不得丢课设**：`lesson_review` 自检与修订必须保留并修好 `design`，避免评审把结构化课设改回普通讲义。
4. **本地质量门新增 pedagogy 维度**：检查核心问题、可观察产出、贯穿主线、3–5 个关键步骤、学习者动作、卡点和快速验证；质量报告可解释给前端和后台。
5. **学习页先展示课设摘要**：进入一节课先看到“本节问题 / 学完能做 / 学习主线”，讲解页展示“本节怎么学”的步骤和快速验收，再展开内容卡片。

**Consequence**
- 新生成内容会更像“被设计过的一小节课”，而不是模型临场写的一组知识点。
- 旧缓存仍可打开，系统会用已有 `explain` 合成课设入口；但新内容的质量门会推动模型输出更完整的教学设计。
- 这仍是工作流增强，不引入不可控长循环 agent；后续可把 Research / Planner / Builder / Critic 拆成更明确的多阶段执行。

### ADR-032 · 对话入口从词典特例升级为语义接地路由（V0.35）

**Context**：用户指出创建路径时，系统把 MCP 这类通用技术概念当成未知词反问，同时又对 harness 等词出现过定制化、盲目套用的候选和来源提示。LearnBuddy 是通用学习产品，不能靠“用户指出一个词就硬编码一个词”来支撑长尾需求。

**Decision**
1. **四类路由替代单词补丁**：入口先判断目标属于“技术词可接地 / 多义概念 / 未知新词 / 快速变化领域”，再决定是资料接地、开放澄清、继续目标挖掘还是进入基线评估。
2. **通用技术词不反问领域**：MCP / RAG / LLM / API 等不在意图层逐个写释义；系统只抽取技术词生成搜索 query，把来源上下文注入模型，再由模型结合用户目标继续问产出、场景和基础。
3. **多义概念不硬塞选项**：harness / cursor / agent / FDE 等不再默认给固定选项；只有候选非常明确且互斥时才点选，否则用一句话让用户确认场景和最终产出。
4. **搜索结果进后台上下文，不直接噪声展示**：轻量来源搜索只作为额外 system context 帮模型接地，澄清阶段不再把 OpenAI / LangChain 等来源标题直接甩给用户。
5. **MCP 不是运行时工具调用**：当前 `mcp_server.py` 是给外部 Agent 连接 LearnBuddy 工具的接口，站内对话还没有 MCP tool-calling loop；站内先用 RAG / source_search / system context 做知识接地，未来再评估是否把 MCP 客户端能力接入对话运行时。

**Consequence**
- 这仍是轻量路由，不引入不可控自主 agent；但它把“是否搜索、是否澄清、是否直接进入评估”的判断前置成通用机制。
- 长尾新概念先走资料接地 + 目标产出确认，不靠逐个写死。
- 新增多轮入口评测集 `tests/fixtures/onboarding_eval_cases.json`，用数据集验收“通用技术词 / 多义词 / 未知新词 / 用户纠偏 / 普通目标”的行为回归。
- 后续如果接入真正 MCP 客户端或工具调用循环，应挂在此 ADR 下扩展，而不是继续改 prompt 话术。

### ADR-033 · 首轮目标澄清必须单问题点选（V0.36）

**Context**：用户反馈首轮对话里同时出现“最终产出目标”和“为什么想学”两个开放问题，而且编号都显示为 `1.`，交互也没有使用已有的“选择 + 补充输入”能力。这个问题不是某个领域词的特例，而是入口信息采集方式过于依赖模型自由发挥。

**Decision**
1. **首轮普通学习目标先问目标产出**：新增通用 `choose_goal_output` 路由，只在会话首轮触发，输出 `<<<CHOICES>>>`，让用户先选择学习用途/目标产出，也可自由补充。
2. **一次只问一个需要回答的问题**：系统提示改为“目标产出先点选，动机/基础/时间后续分轮问”，禁止在同一条回复里同时问最终产出和为什么想学。
3. **不写死具体领域词**：选项是通用产出类型（转岗、当前工作落地、作品项目、面试考试、先系统理解），不把具身智能、MCP、harness 等名词逐个硬编码成规则。

**Consequence**
- 首轮体验从开放双问变成可点选的单问题，减少用户不知道怎么答的问题。
- 后续仍由 LLM 结合用户补充、来源接地和基线测试继续细化，不影响多义词澄清与旧会话数据。

### ADR-034 · 摸底题从自评档位升级为场景化诊断（V0.37）

**Context**：用户指出当前考核题太模糊，例如“你能把这件事做到哪一步”只能测自我感觉，尤其在用户擅长用 AI 时，会高估真实理解、验收和落地能力。

**Decision**
1. **每道摸底题必须有具体材料**：题干要包含需求片段、用户场景、错误方案、AI 输出、约束数据或小案例，让用户做判断或选择下一步行动。
2. **选项不再默认是能力档位**：禁止把 choice 题写成“完全不会 / 需要 AI / 能独立”这种自评梯度；选项应优先是具体动作、判断路径、方案选择或错误识别。
3. **AI/产品方向要测真实落地能力**：产品经理/行业产品方向重点测需求澄清、用户场景、AI 能力边界、数据闭环、指标验收和跨团队落地，不只问技术实现。
4. **fallback 同步升级**：LLM 出题失败时的兜底题也必须是场景化诊断，不能退回空泛自评。

**Consequence**
- 基线评估更能区分“会让 AI 包装答案”和“能独立判断、验收、落地”的差异。
- 后续路径生成的 baseline_summary 能拿到更可靠的能力证据，减少课程难度错配。

### ADR-035 · 确认生成后必须立即展示前置进度（V0.38）

**Context**：用户点击“确认生成”后，后端需要先让对话模型输出 `<<<GENERATE>>>` 参数包，再进入真正的路径生成工作流。此前只有进入 `generate_instance` 后才发送 `generate_start`，导致前置参数生成阶段页面只显示“学习伙伴思考中”，一旦模型卡住用户会等待 10 分钟仍看不到任何进展。

**Decision**
1. **前端乐观进度**：用户发送“确认生成 / 就这套 / 开始生成”等确认语后，立即展示“整理生成参数 → 准备资料接地 → 等待进入构建”的进度面板。
2. **真实进度接管**：后端拿到 `GENERATE` 参数并进入 `generate_instance` 后，继续用真实 `generate_start / generate_progress` 事件接管同一个面板。
3. **前置模型超时**：对话流式生成默认 90 秒超时（`ITUTOR_CHAT_STREAM_TIMEOUT` 可调）；超时返回可见错误，不能无限挂起。

**Consequence**
- 用户能区分“正在整理参数”和“正在构建路径”，不会误以为页面卡死。
- 如果模型或网络异常，用户会收到错误提示，可以重试或缩小需求，而不是空等。

### ADR-036 · 学练单元生成必须可后台恢复（V0.39）

**Context**：用户反馈进入一节课后，“质量审查与保存”停留超过 10 分钟，页面仍只显示进行中，无法判断是模型慢、来源检索慢还是服务卡死。学练单元比路径生成更高频，不能让用户在单节内容上无限等待。

**Decision**
1. **后端转后台任务**：`/api/instance/{id}/lesson` 启动后台生成任务；V0.39 当时默认 `ITUTOR_LESSON_TIMEOUT=150` 秒内完成则直接返回 lesson，超过则返回 202，不取消后台任务。V0.49 起为适配多轮质量 loop，首请求默认改为 45 秒转后台轮询，后台硬超时默认 300 秒。
2. **生成完成仍会落盘**：生成、来源整理、质检和保存在线程中完整执行；即使浏览器断开，任务完成后仍写入 lesson cache。
3. **前端自动恢复**：收到 202 或浏览器请求超时后，前端轮询 `/api/instance/{id}/lesson/status`；状态变为 ready 后自动打开已保存内容。
4. **避免重复生成**：同一实例 + 主题 + 调整要求共享同一个内存 job；用户重复点击只复用当前任务，不重复消耗生成链路。
5. **明确长等待状态**：等待超过 120 秒时，进度面板提示已偏长；轮询阶段明确告诉用户“后台仍在生成，不需要手动刷新”。
6. **不改历史数据**：只新增运行时任务状态和状态查询接口；lesson cache、学习记录、积分流水和实例数据结构不变。

**Consequence**
- 用户不需要理解 499、刷新或缓存，生成完成后页面会自动恢复。
- 后续如果继续加深搜索、质检、多 Agent 备课，也必须走“可恢复后台任务 + 状态查询”的长任务模式。

### ADR-037 · 学练交互必须“选项独立 + 图解结构化”（V0.40）

**Context**：用户在“先想”环节点选项后，选项内容被自动写进输入框，后续换选项与手动补充互相干扰；同时 `visual/compare` 卡曾把架构图渲染成大段 ASCII 字符图，移动端和窄屏完全不可读。这两个问题都说明学习页不能只把模型文本原样排出来，必须把互动状态和图解形态产品化。

**Decision**
1. **选项与手动输入解耦**：先想环节的选项只代表选择，不自动填充或覆盖文本框；文本框只用于可选补充，用户可以“只选不写”或“选项 + 自己补充”。
2. **图解卡不展示 ASCII 宽图**：前端会过滤旧缓存里的 `+---+ / | / ----` 字符图，并优先用 `items` 渲染成节点结构图。
3. **生成契约约束图解输出**：lesson 与 review prompt 明确要求 `visual/compare` 用结构化 `items` 表达节点、职责、关系和判断标准，不再输出字符画。
4. **质量门硬性拦截**：`core/content_quality.py` 把 ASCII 宽图列为不可通过问题，避免新生成内容再次退化为难读图。

**Consequence**
- 旧 lesson cache 不迁移也能显示得更可读；新 lesson 从生成链路上减少 ASCII 图复发。
- 学习页交互状态更接近真实学习动作：选择是选择，补充是补充，不再互相污染。
- 后续内容智能体/skill 化时，图解卡应继续输出结构化节点，而不是依赖模型排版能力。

### ADR-038 · 时间盘点只问平均每天投入（V0.41）

**Context**：用户反馈对话里先问工作日、再问周末，交互成本过高；多数用户只需要先给一个粗略时间预算，特殊情况可以自己补充。

**Decision**
1. 对话只问一次“平均每天能投入多久？”，用 `<<<CHOICES>>>` 给 `30 分钟以内 / 30–60 分钟 / 1–2 小时 / 2–3 小时 / 3 小时以上`。
2. 不再拆成工作日 / 周末两轮追问；如果用户节奏差异很大，放到“其他 / 补充说明”里写。
3. 为兼容旧 schema，生成参数默认让 `weekday_hours = weekend_hours = 平均每天小时数`，`weekly_total_hours ≈ 7 × 平均每天小时数`；只有用户明确补充差异才拆分。

**Consequence**
- 对话复杂度下降一轮，用户不用被迫精细估算工作日和周末。
- 旧数据结构和计划生成器不迁移，仍能继续使用 `weekday_hours/weekend_hours` 字段。

### ADR-039 · 学练生成任务必须有真实状态与落盘恢复（V0.42）

**Context**：用户多次遇到学练单元生成页长时间停在“质量审查与保存”；实际退出再进入时内容已经生成，说明前端轮询没有识别落盘结果，任务状态也缺少硬超时和真实阶段。

**Decision**
1. 学练生成 job 记录真实阶段：缓存检查 / 路径上下文 / 资料库检索 / 外部来源 / 讲解起草 / 自检修订 / 质量审查保存。
2. `/lesson/status` 返回 `stage_label`、`progress_percent`、`steps`、`elapsed_seconds`、`timeout_seconds`、`can_retry`。
3. 状态接口每次轮询都先检查本节缓存文件；只要发现生成结果已落盘，就把 job 恢复为 `ready` 并立即返回 lesson。
4. 后台任务增加硬超时，超过上限进入可重试错误态，不能无限显示“进行中”。
5. 前端生成页消费后端真实状态，不再让本地假进度覆盖服务端完成/错误状态。

**Consequence**
- 用户不会再看到“已经生成但页面还在等”的假卡死。
- 后续内容生成如果继续拆 agent，必须继续复用这个 job/status 协议，而不是新增孤立轮询。

### ADR-040 · 路径生成也必须可恢复并先绑定归属（V0.43）

**Context**：学练单元已改成后台 job，但学习路径生成仍依赖 `/api/chat` 的 SSE 流最终事件来完成归属绑定、记录事件和跳转。浏览器刷新、网络断开或前端漏掉最后事件时，可能出现“后台已经生成、前端一直等”，甚至实例未写入当前用户归属，返回学习空间后看不到。

**Decision**
1. 路径生成进入后台 job：服务端创建 `job_id`，记录阶段、进度、耗时、超时和最终 `redirect_url`。
2. 新增 `/api/generate/status`，前端可在 SSE 断开、刷新或重新进入对话页时按 `job_id` 恢复状态。
3. 归属绑定前移到后台任务内部：`generate_instance` 完成后立即写 `meta.json` 与 `_index.json` 的 `user_id`，不依赖前端收到最终事件。
4. 前端把生成 `job_id` 存在 `sessionStorage`，收到 ready 后自动跳转；错误或超时给出可见提示，不再无限“思考中”。
5. 不修改旧实例结构：只新增运行时内存 job 与状态接口，历史 `master.json / W1.json / meta.json` 继续兼容。

**Consequence**
- 用户刷新或短暂断网后仍能接回路径生成结果，任务完成也不会因为 SSE 断开而丢归属。
- 后续深度搜索、多智能体教研、质量门变慢时，路径生成也有和学练生成一致的可靠性底座。

### ADR-041 · 教学内容生成升级为 Loop Engine 方案工作台（V0.44）

**Context**：用户明确提出这轮迭代非常重要，不应直接进入代码修改，而要先结合 AI-Shifu / LangGraph / DSPy / Ragas / Promptfoo / H5P 等开源调研，以及《让 AI 会返工：Loop 工程的最小实操手册》，明确产品方案、技术方案和迭代路径。当前 LearnBuddy 已有 LessonFlow、source_pack、Critic、课设字段和长任务恢复的增量能力，但它们还没有被统一成一个系统化的内容生成 Loop。

**Decision**
1. **先建方案工作台再实现**：新增 `docs/教学内容Loop开发工作台.html`，作为本轮需求打磨入口，统一承载产品方案、技术方案、迭代路径、Loop Map、数据契约、开放问题和红灯机制。
2. **产品方案聚焦“可学习内容”**：每节课必须能回答“本节解决什么问题、学完能做什么、依据什么来源、为什么这样互动、怎么验收”，不再接受浅层讲义或不可读字符图。
3. **技术方案统一为 Content Loop Engine**：将目标接地、资料研究、课设规划、互动设计、内容生成、Critic 返工和 RunSheet 保存视为一条受控工作流；先用现有 Python workflow 落地，暂不急于引入自由 Agent 或 LangGraph。
4. **核心契约先行**：围绕 `LearningIntent / ResearchPack / LessonSpec / LessonFlow / QualityReport / RunSheet` 建立兼容旧数据的结构化协议，再推动前端学习页和后台分析消费这些结构。
5. **迭代路径按 Loop 推进**：P0-A 先打磨工作台和质量标准；P0-B 做数据契约与测试集；P0-C 做单节 LessonBuildGraph；P0-D 做学习页运行时；P1 再扩展路径级 Loop 与后台质量分析。
6. **视频/B站作为学习参考源进入 source_pack**：第一版允许保存视频/B站链接、标题、摘要、来源类型和适用环节，用作学习参考内容；字幕抽取、视频转写和片段级理解后置，不作为 P0 硬依赖。
7. **兼容与可恢复是底线**：任何实现都不得破坏线上 `data/instances` 与已有学习记录；长任务必须沿用 job/status/缓存恢复协议；质量返工必须有轮次、时间和停止预算。

**Consequence**
- 本轮从“继续调 prompt/修页面”升级为“建立教学内容生产线的运行规则”。
- 后续代码实现要以工作台中的 Loop Map 和数据契约为验收基准，不能再把内容质量问题分散到前端文案、临时 prompt 或硬编码词表里。
- 如果后续引入多智能体、LangGraph、DSPy 或更复杂评测框架，也必须挂在这套受控 Loop 下逐步替换，不允许一次性重构影响线上用户数据。

### ADR-042 · 开发工作台改为版本化迭代管理（V0.45）

**Context**：`docs/教学内容Loop开发工作台.html` 第一版把 AI-Shifu 调研、Loop 工程文章、产品方案、技术方案、数据契约和开放问题集中在一个页面，信息完整但过重。用户明确指出本期内容和历史内容混在一起会导致后续无法使用，并进一步确认 LearnBuddy 会长期迭代，需要一个长期维护需求、技术方案和迭代进度的项目驾驶舱。

**Decision**
1. 新增 `docs/LearnBuddy项目工作台.html` 作为长期真相源和版本管理中心：默认展示项目总览、版本管理、当前版本、需求池、技术账、验收口径和归档资料。
2. 版本管理采用“一级入口 + 二级下拉”的查看方式：顶部只保留轻导航，版本号通过下拉进入对应版本的独立三件套页面。
3. 工作台长期维护四本账：需求账（用户痛点、场景、优先级、验收）、技术账（架构、数据契约、接口、兼容策略、风险）、进度账（版本状态、产物、验证、部署）、决策账（ADR、范围边界、归档材料）。
4. 每个正式开发版本必须有独立三件套页面，固定说明：需求方案、技术方案、开发范式、版本目标、当前状态、范围边界、核心产物、验收口径、下一步。
5. `docs/教学内容Loop开发工作台.html` 降级为 V0.44 专项档案，不再承担全局工作台职责；V0.44 是方案与调研档案，不代表已经完成开发。
6. 版本必须对应可独立发版的用户可感知功能，不能把纯数据契约、单个后端 Graph 或单个页面运行时拆成产品版本。本轮重排为：V0.46 教学内容 Loop v1（覆盖数据契约与评测集、单节 LessonBuildGraph、学习页运行时、验收兼容四个内部里程碑）；V0.47 路径级 Loop v1 标为待确认候选，依赖 V0.46 验证后再锁细节。
7. `docs/文档中心.html` 只承担目录入口，不承载版本细节；版本细节统一放在 `docs/versions/` 的独立三件套页面；OpenClaw / Harness / AI-Shifu 等材料归入竞品与外部调研或未来候选，不放在一级主线导航。
8. 历史材料默认进入归档资料，不再堆在第一屏；需要新增大量材料时，先创建新版本卡或独立报告，再从工作台链接。
9. 只重构文档信息架构，不改线上产品数据。

**Consequence**
- 后续需求管理从“持续加内容”改为“按版本推进”，每轮迭代都能独立验收、回溯和收口。
- 当前页面会更适合长期使用：看长期机制先看四本账，看进度先看版本总览，看细节再进入当前版本或归档资料。
- 后续实现不得绕过版本卡直接堆新方案；如果范围变大，必须拆成下一版本。

### ADR-043 · 教学内容 Loop v1 先落地契约与运行态（V0.46）

**Context**：V0.44/V0.45 已经把教学内容质量问题收敛为版本化方案，但用户明确要求“先开发 0.46 版本”。当前最紧的痛点不是再写一版长 prompt，而是让每节课有可验证的内容契约、来源/图解/互动字段能穿透到前端，并让用户在学练单元里的选择、输入、回看状态能持久保存。

**Decision**
1. LessonFlow 升级为 `schema_version=2` + `contract_version=lesson-loop.v0.46`，但旧 lesson cache 继续兼容读取。
2. 教学卡片保留 V0.46 契约字段：`completion_rule / expected_signal / free_input_prompt / answer_schema / source_refs / diagram`；前端优先消费这些结构化字段。
3. lesson prompt 明确视频/B站可作为学习参考源进入 source pack，但关键事实优先使用官方文档、原始文献、权威书刊和可信技术资料。
4. 学习运行态与内容缓存分离：新增 `lesson_runtime/{topic_hash}.json` 保存当前步骤、已完成步骤、卡片选项、自由输入和产出，不修改旧 `master/W1/meta` 结构。
5. 新增 `/api/instance/{id}/lesson/runtime` 读写接口，必须复用现有登录和实例访问校验，避免用户数据串号。
6. 选项只记录选择，不自动灌入输入框；保存逻辑使用嵌套合并，避免后续控件局部保存覆盖已完成内容。

**Consequence**
- V0.46 先把“可学习内容”的数据底座做实，后续再接更强的 LessonBuildGraph、评测集和 Critic 返工时，不需要推翻前端或历史数据。
- 学习页刷新、返回或回看步骤时，用户的学习痕迹不会丢；内容生成结果与用户学习过程也可以分别分析。
- 这仍是 v1：深度搜索、多轮返工、路径级 Loop 和后台质量分析继续作为后续版本演进，不在本次一次性重构。

### ADR-044 · 路径级 Loop v1 先落地状态账本与下一天预生成（V0.47）

**Context**：V0.46 解决了“单节内容能按契约运行并保存状态”，但路径层仍靠前端即时推断：用户完成一天后，下一天是否准备好、当前到底推进到哪一天、刷新/退出后是否恢复，缺少服务端真相源。用户多次遇到“界面显示还在生成，但退出再进已经有结果”的体验问题，说明路径级 Loop 必须先补可恢复状态层。

**Decision**
1. 新增 `core/path_loop.py`，在实例目录下独立保存 `path_loop.json`，不改 `master.json` / `W*.json` / 旧学习记录。
2. 路径状态以 `unit_id` 为主键，把周计划展开到每日单元；旧 `lesson_completions` 的已学 topic 仍作为推导依据，避免升级后历史数据失效。
3. 新增 `GET /api/instance/{id}/path-loop`，`GET /api/instance/{id}` 同步返回 `path_loop`，前端首页和计划页优先使用服务端当前单元。
4. 完成学练后 `/api/instance/{id}/event` 记录 `lesson_completed` PathEvent，并异步准备下一天内容；如果缓存已存在或已有 job 运行，直接复用，不重复耗积分。
5. 路径预生成状态持久化为 `pre_generation`，包含 `status / job_id / stage / progress / detail / error`；前端轮询该状态，展示“准备中 / 已准备好 / 失败可重试”。
6. 本版本不做完整路径重规划、不批量生成整周内容、不改变解锁规则；这些保留给后续路径级 Loop 的 replan / critic / analytics 演进。

**Reason**
- 路径级 Loop 的第一步不是“大模型自动改计划”，而是先让路径有可恢复的事实账本。
- 以独立文件保存状态，能兼容线上旧实例，且状态损坏时可从 SQLite 学习记录重新推导。
- 下一天预生成能降低学习断点：用户完成今天后，下一天内容后台准备，回来可以秒开或看见真实进度。

**Consequence**
- 新增路径状态和预生成测试，覆盖每日单元展开、历史 topic 推导、完成事件幂等、服务端 path_loop 返回。
- 前端 `web/instance.html` 增加路径状态轮询，不影响学情/资料页；单节生成仍沿用 V0.42/V0.46 的后台 job 与缓存恢复。
- 后续 V0.48+ 可以在同一账本上接“路径级质量分析、自动补弱、周级调整建议”，无需推翻本版状态契约。

### ADR-045 · 教学内容生成从巨型 prompt 升级为受控流水线（V0.48）

**Context**：V0.28–V0.46 已连续增强 lesson prompt、LessonFlow、source_pack、质量门和运行态，但用户仍反馈新生成教学内容“看着完整、读着空”。复盘确认根因不是单条 prompt 措辞，而是三层架构问题：单模型一次性输出巨型 JSON 导致注意力摊薄；确定性质量门偏结构计数与关键词，容易被模型刷分；来源层把低质摘要直接喂给模型，噪声会污染备课。

**Decision**
1. 单节内容生成统一升级为受控多阶段 workflow：`design_lesson`（课设）→ `write_lesson`（写课）→ `critic_lesson`（锐度评审）→ `revise_lesson`（定向修订）。
2. 课设阶段优先使用 `deepseek-reasoner`，只产 `design`：`learning_question / outcome / main_thread / boundary / key_steps / quick_checks / angle / anti_generic`，避免在同一调用里同时写讲义、卡片、题目和引用。
3. 写课阶段使用 `deepseek-chat`，把课设作为锁定输入，围绕单一 `main_thread` 写深 `explain / cards / produce / lab / practice / citations`，不再让模型临场重选结构。
4. Critic 阶段优先使用 `deepseek-reasoner`，按 `specificity / insight / depth / grounding` 评审，并输出具体重写指令；确定性质量门只负责缺字段、ASCII 图、伪造来源等硬问题，不再作为主要返工优化目标。
5. 来源层先过滤再接地：`source_pack` 使用 `authority_score / relevance_score` 过滤低质来源，只对 top 1–2 高质量来源抓取正文片段；如果没有好来源，宁可 context 留空并标注按通用知识生成，也不喂 SEO 噪声。
6. 回退路径必须保留：reasoner 关闭或解析失败时回退旧 `lesson.md` 单段生成；critic 失败时退草稿；旧 lesson schema、缓存和前端读取全部兼容。

**Reason**
- 教学质量来自“先决定这节课到底解决什么问题”，不是让模型一次性填满所有字段。
- 评审要问“换个主题是否仍成立、有没有非显然判断、边界是否讲透”，不能只看卡片数量、字数和关键词。
- 无可靠来源比低质来源更安全；噪声 grounding 会让模型把搜索摘要当教材。

**Consequence**
- 每节新生成内容可能从 1–2 次 LLM 调用增至 3–4 次；依靠后台 job、缓存、硬超时和 `ITUTOR_LESSON_REASONER=0` 降级开关控制成本与延迟。
- 新增 `_critic`、`_quality_before`、`_revised` 等可观测字段，便于后台分析和 golden case 回归。
- 后续如引入多智能体、LangGraph 或 DSPy，只能替换 workflow 内部节点，不允许改成自由 agent 互聊后产出。

### ADR-046 · 教学内容质量门必须检查“核心教学密度 + 可展开信息层 + 学习时长合同”（V0.49）

**Context**：V0.48 已把 lesson 生成拆成课设、写课、critic、修订，但实测样本仍出现“结构齐全、分数好看、内容偏少”的问题：页面有目标、来源、先想、图解、例子等卡片，却多数是短句清单，没有机制推演、边界比较和 worked example。V0.49 已先补上核心教学密度门槛；继续复盘后确认，还需要给核心教学内容一个**可展开的信息层**，否则只会在同一张卡里堆长正文，学习页要么拥挤、要么又退回提纲。用户进一步指出：如果 onboarding 已收集“每天学习 1–2 小时”，单节内容仍按 20–40 分钟微课生成，本质上是不匹配需求；质量 loop 的目标也不能只看“深度”，必须同时看知识完备性、学习质量、需求匹配度、个性化学习构建。

**Decision**
1. 写作 prompt 增加核心教学密度要求：至少 3 张深讲卡，覆盖机制推演、边界对比、worked example；核心教学内容不能少于约 1600 中文字符。
2. 确定性质量门新增 `teaching_chars` 与 `dense_teaching_cards` 检查，少于门槛即扣分并进入修订，不再只看卡片数量、关键词和互动题。
3. critic prompt 把“短句清单式内容”列为硬失败：如果核心卡少于 3 张可独立学习的深讲卡，`depth` 最高只能给 65 并必须 `revise`。
4. 新增 `lesson_revise.md` 作为流水线第 4 段专用返工 prompt；critic 触发修订时优先走内容加深修订，不再复用偏结构修正的旧 `lesson_review.md`。
5. V0.49 同版落地 **Expandable Info Layer v1**：核心卡保留短而硬的 `body/items`，额外支持 `expansions[]` 承载展开内容，避免把深度全部塞进首屏。
6. `expansions[]` 的首批类型固定为：`mechanism`（为什么）、`boundary`（边界）、`contrast`（对比）、`worked_example`（逐步例题）、`counterexample`（反例）、`common_mistake`（误区）、`source_evidence`（来源证据）、`advanced`（进阶）。
7. 每节高质量 lesson 至少应有三类展开：机制推演、边界/对比、worked example；有可靠来源时，至少 1 个 `source_evidence` 展开块说明事实依据。
8. 前端学习页默认展示核心卡，展开内容以卡内折叠区呈现（如“为什么 / 边界 / 看完整例子 / 常见误区 / 来源依据”），不新增一堆并列卡，也不做卡片套卡片。
9. 质量门从单一 `teaching_chars` 扩展为 `expansion_blocks / expansion_kinds / required_expansion_groups`，critic 必须评估展开内容是否有真实信息增量，而不是把正文拆标题。
10. 内容质量返工从“一次修订”升级为 bounded quality loop：`critic → revise → quality gate → critic`，默认最多 3 轮（`ITUTOR_LESSON_MAX_REVISIONS=3`，最高 5），每轮记录 `_quality_loop.rounds`，并按质量门 + 展开层 + critic verdict 保留 best 版本，而不是盲目返回最后一版。
11. V0.49 同版追加 **Session Duration Contract**：后端从已收集的 `schedule.weekday_hours / weekend_hours / weekly_total_hours` 推导 `target_minutes`，传入 `generate_lesson_reviewed(session_contract=...)` 并写入 lesson 的 `_session_contract`。
12. 当 `target_minutes >= 60` 时，lesson 必须输出 `estimated_minutes / activity_plan / rubric`；`activity_plan` 至少覆盖 `lecture / worked_example / drill / produce / reflection`，`produce.task` 必须是可检查产物，不再接受“3–5 句话复述”作为 1–2 小时学习任务。
13. Critic 评分从 specificity/insight/depth/grounding 扩展为八项：新增 `knowledge_completeness / learning_quality / demand_fit / personalization`，任一核心维度低于阈值都进入下一轮 revise。
14. 为避免多轮 loop 导致前端长请求报错，首个 `/lesson` 请求默认 45 秒内未完成即返回 202 转后台轮询；后台硬超时默认 300 秒，继续沿用 job/status/cache 恢复协议。
15. 60 分钟以上单元默认启用 section writer：先出课设，再分 `core / examples / practice / produce` 多段写作，由后端确定性合成 lesson，避免一次巨型 JSON 把深度挤掉。
16. 质量门和 critic 必须识别 `expansions[]` 中的深讲证据；同时保留 `_learner_signals` 并应用个性化脚手架，把“已具备 / 薄弱点 / 验收偏好 / 学习时长”写进 goal、activity_plan、rubric 和产出任务。

**Consequence**
- 新课生成会更长，单次输出 token 压力略增；但后台生成 + 缓存已吸收主要延迟。
- 旧 lesson schema 继续兼容；`expansions[]` 是增量字段，旧缓存和旧前端缺失时仍按 `body/items` 展示。
- `_quality_loop` 记录 stop_reason / best_round / 每轮分数，便于后续 golden case 和后台质量分析；若候选版本变差，保留前一版 best。
- 后续 golden case 应至少覆盖“结构完整但内容薄”的失败样本，防止再次被短句卡片刷过质量门。
- V0.49 的验收不只看“内容字数够不够”，还要看默认可读、按需可展开、展开内容确实提供机制/边界/例子/证据，并且能支撑用户实际选择的学习时长。
- 新增 `activity_plan` 后，前端讲解页可以展示“本节学习安排”；即使内容生成 loop 多跑几轮，用户也会看到真实后台阶段和自动恢复状态。
- 2026-06-30 线上 DeepSeek golden case 已通过：`scripts/verify_lesson_quality_loop.py` 跑 90 分钟 Agent memory 样本，耗时 180.00s（低于 300s 预算），quality 100，teaching_chars 5949，dense_teaching_cards 4，expansion_blocks 6，critic 最低分 80（personalization 88）。

### ADR-047 · 设计系统作为跨版本通用规范（不占版本号）

**Context**：V0.48/V0.49 已把教学内容生成补到“有课设、有深讲、有质量门”的阶段，但前端视觉仍像多个迭代阶段拼出来的原型：页面级内联样式多、组件语言不统一、暖纸底和卡片偏多、图片资产使用缺少标准。继续单点修页面会让产品气质漂移，尤其登录页、学习空间、路径页和学练单元无法共同传达“可信、清楚、可行动”的学习工作台感。

**Decision**
1. LearnBuddy 设计系统定义为 **跨版本通用设计规范**，不占用 V0.50 或任何单一版本号；具体页面改造再单独立版本需求。
2. 设计原则固定为：行动优先、学习工作台感、低视觉噪音、内容密度友好、状态透明、可访问性达标。
3. 抽出统一 design tokens：颜色、字体、间距、圆角、阴影、状态色、层级和焦点样式；页面不得继续各自发明主色、圆角、按钮和卡片规则。
4. 建立基础组件规范：button、icon button、tag、card、tabs、dialog、toast、progress、empty、loading、form、status chip；普通卡片圆角默认 8px，大容器最多 12px。
5. Visual Asset System 明确图片使用边界：图片只用于品牌第一印象、学习场景暗示、领域封面、成就/项目成果和内容辅助理解；不做纯装饰大图、不做模糊 stock 风背景、不用看不出产品语义的插画。
6. 登录页作为第一批生图模型落点：使用一张大幅背景图建立 LearnBuddy 的第一印象，画面应表达“安静、专注、个性化学习空间”，文本与表单覆盖区必须保持高对比和可读性；移动端允许裁切但不得遮挡关键视觉主体。
7. 学习空间和路径卡片允许使用领域封面图，但必须降低装饰权重：图片服务于领域识别，主信息仍是“现在学什么 / 状态 / 下一步动作”。
8. 学练单元优先升级排版和结构层级，不默认加大图；只有当图片能解释概念、流程、对比或案例时才进入内容卡片。
9. 每次涉及页面、组件、图片、AI 状态或学习流程展示的迭代，都必须先对照 `docs/设计规范.html`；大规模页面改造才需要单独立版本和截图验收。

**Consequence**
- 设计系统是体验基础设施，不新增学习功能；后续具体版本优先收口 `web/common.css` / `web/assets/style.css` 与页面内联样式，再逐页替换。
- 生图模型进入设计工作流，但生成资产必须有 prompt、用途、尺寸、裁切规则和回退背景；不能让随机图片决定产品风格。
- 后续页面默认遵守 Design System v1；如果新页面需要突破组件或图片规范，必须先更新本 ADR 或新增 ADR。
- 当前状态为长期规范：沉淀到 `docs/设计规范.html`，工作台和文档中心固定入口；V0.50 不再承载“设计系统”本身。

### ADR-048 · V0.49.1 自学可执行层作为内容质量 Loop 的下一轮任务

**Context**：V0.49 已完成核心教学密度、可展开信息层、学习时长合同和 bounded quality loop，线上 DeepSeek golden case 也通过了 90 分钟 Agent memory 样本验证。但人工从学习者视角阅读后仍认为课程体验不够：内容更厚了，却还不一定能支撑一个人独立完成 60-90 分钟学习。主要缺口不是继续加字数，而是缺少自学执行层：模板化产出、参考答案/样例产物、高阶练习、清晰 rubric，以及更自然的个性化表达。

**Decision**
1. 新增 V0.49.1 作为 V0.49 之后的体验质量补丁任务；V0.49 仍保留为可部署基线，不把已验收版本无限扩张。
2. V0.49.1 的唯一目标是让长课从“内容更厚”变成“学习者能独立完成”：打开页面即可知道怎么读、怎么做、怎么检查、怎么改进。
3. 首轮补丁范围固定为四件事：自学语言规范、模板化产出、参考答案/样例产物、高阶练习；不重做 V0.49 生成架构，不扩大到路径级重规划或大规模前端视觉重构。
4. Prompt 层禁止课堂话术和原始学员标签堆叠；个性化必须自然落在任务难度、提示粒度、验收标准和错误预警里。
5. 对 60 分钟以上课程，质量门必须能识别并要求：至少 1 个可直接填写的模板、1 个参考答案/样例产物、1 个 critique/repair/design review 类型高阶练习、1 个清晰 rubric。
6. Critic 增加自学可执行维度：`self_study_fit`、`artifact_completeness`、`assessment_authenticity`；缺少上述自学产物时不能给高分通过。
7. 开发方式采用工程 loop：观察真实样本 → 提出 1-2 个可验证假设 → 实现 → 用线上接入 LLM 真测 → 人工阅读验收；每轮都记录耗时、loop 轮数、stop_reason 和人工结论。
8. Loop 必须受控：默认最多 3 轮开发修订；如果连续两轮真实样本没有改善，停止堆 prompt，回到质量指标和技术方案重新设计；如果总耗时稳定超过 300 秒，优先优化后台任务、拆段和缓存，不继续增加模型调用。

**Consequence**
- V0.49.1 的验收标准从“内容长度/深度够不够”升级为“学习者能否独立完成学习和产出”；已用真实线上 LLM 样本和人工阅读共同收口。
- 该任务页沉淀到 `docs/versions/v0.49.1-自学可执行层.html`，工作台和文档中心均以它为当前版本入口。
- 已落地：`lesson_write.md`、`lesson_revise.md`、`lesson_critic.md`、`core/content_quality.py` 和真实 LLM 验证脚本均接入自学可执行层检查；旧 lesson schema 继续兼容，必要时由确定性长课合同兜底补齐模板、样例和高阶任务。
- 真实验收：Round 1 暴露缺模板与修订失败（266.77s / quality 47），Round 2 加入确定性兜底后通过但继续打磨，Round 3 通过（183.82s / quality 100 / activity 100min / critic 最低分 88 / stop_reason=critic_pass）。
- 这轮不再把“不够好”当作泛泛评价，必须把每次不满意落成可验证的学习阻断问题，再决定下一轮改动。

### ADR-049 · V0.49.2 路径可信度补丁：节奏质量门 + 真实生成 Trace

**Context**：V0.49.1 已让单节内容更可自学，但线上观察又暴露两个信任问题：第一，路径大纲会把周二/周四固定成“轻量消化 / Anki 推送 + 30 分钟复习昨日重点”，用户选择每天 1–2 小时时，这种安排像模板复读，不像真实学习路径；第二，学练单元生成页会展示多个阶段，但部分状态和计时来自前端估算，用户很难判断到底慢在检索、写作还是质检。

**Decision**
1. 新增 V0.49.2 作为 V0.49.1 后的小补丁，不重做 lesson 内容流水线，优先修复用户已经观察到的路径可信度问题。
2. `evaluate_outline_quality` 必须检查第一周工作日节奏：记录 `w1_progress_weekdays`、`w1_review_only_weekdays`，发现工作日整天复习/轻量消化时扣分并给出可读 issue。
3. 路径生成允许微复习，但必须嵌入新内容、练习、迁移案例或产出任务；周日可保留整周回顾，工作日不应被固定模板占满。
4. Lesson 后台任务必须维护真实 `stage_trace`：阶段开始、结束、耗时和状态都由后端记录，`/lesson/status` 返回给前端。
5. 前端生成页展示服务端返回的每阶段耗时；拿到真实状态后不再只靠本地时间轴猜测。

**Consequence**
- 路径大纲质量门从“有 7 天、有产出”升级为“每天是否真的推进学习”，能自动拦住用户指出的隔天复习模板。
- 学练单元生成体验变得可观测：后续判断耗时是否可接受时，不再只能看总耗时，而能看到卡在哪个阶段。
- 本轮仍不解决所有路径规划问题，例如跨周 FSRS、真实日历避让和动态重规划策略；这些留给后续路径级 Loop 版本。

### ADR-050 · V0.50 可信运行态与内容升级闭环

**Context**：V0.49.2 已补工作日节奏质量门和 lesson 阶段耗时，但存量 dogfood 又暴露三类会直接伤害信任的问题：学习空间仍按自然日期显示“第 N 周”，而路径页按完成事实停在 W1/D1；旧 lesson cache 只按 topic 命中，版本升级后仍直接返回不符合当前质量合同的内容；LLM Markdown 通过 `marked` 直接写入 `innerHTML`，缺少安全净化和键盘可达性兜底。

**Decision**
1. V0.50 只收口“当前用户已经会遇到的可信运行态”，不扩到结构化基线、真实 W2 自动生成或路径级 LLM 大纲重构。
2. `path_loop` 成为学习进度唯一真相源。`GET /api/instances` 为每条路径返回当前周/天、完成单元数和总单元数；学习空间不得再用自然日期推算进度。
3. lesson cache 引入显式 `cache_version`。只有版本匹配且通过当前质量门的缓存才能直接复用；旧版本或质量不通过的缓存进入后台升级，成功后原子替换，失败时保留旧文件并提供可重试错误。
4. lesson API 和生成页公开 `cache_status` / `cache_version`，让“已保存”“正在升级”“当前质量门通过”可区分，不再把旧内容统一标成可信缓存。
5. 所有 LLM / Markdown 内容必须经过统一 HTML allowlist 净化后才能写入 DOM；过滤脚本、事件属性和危险 URL 协议。资料档案不得绕过净化函数直接调用 `marked.parse`。
6. 路径内一级导航改为真实 button/tab 语义，支持键盘焦点和 `aria-selected`，移动端主要点击目标不低于 44px。
7. 本轮保留未跟踪的用户文件，不纳入提交；实现必须带缓存版本、进度真相源、安全 Markdown 和前端静态契约测试，并完成 1440×900 / 390×844 浏览器回归。

**Acceptance**
- 同一条未开始路径在学习空间和路径页都显示 W1/D1；自然日期变化不会让它自动升周。
- 当前版本且质量通过的缓存继续秒开；旧缓存不会直接伪装成新版内容，升级失败不会覆盖旧文件。
- 恶意 Markdown 中的 `<script>`、`onerror`、`javascript:` 不会进入可执行 DOM。
- 路径导航可用 Tab 聚焦并用 Enter/Space 切换；桌面和移动端无横向溢出。
- 全量 pytest 通过，新增测试覆盖上述四项合同。

**Follow-up tasks**
- V0.50.1：让路径研究包和用户知识库真正参与周主题生成，并用 repair loop 阻止低分大纲落盘。
- V0.50.2：结构化保存独立底座 / AI 协作 / 验收纠错 / 迁移能力基线，接入 `BaselineEvaluator` 和学前/学后对照。
- V0.51：完成 W2–Wn lazy build、6 关自检解锁与可确认的 replan 应用闭环。
- 生产底线：持久化对话/生成任务，绑定 `user_id + session_id`，补 HTTPS、Secure Cookie、限流、备份、账户删除和数据保留期。

**Status**：✅ 已验收。304 个 pytest 全部通过；真实账号 4 条路径在空间页与路径页均显示 W1/D1；1440×900 与 390×844 无横向溢出；Enter/Space 导航、44px 触点和前端零控制台错误通过浏览器实测。

### ADR-051 · V0.50.1 Grounded PathBuild 与大纲修复门

**Context**：现有 `build_outline_research()` 在 `build_master()` 之后运行，研究包只被挂到 `_outline_research`；`weekly_themes` 和 W1 不会因来源或用户知识改变。`_outline_quality.passed=false` 仍照常落盘，导致“有研究过程”与“研究真正参与决策”被混为一谈。

**Decision**
1. 路径研究先于最终 master/W1。外部权威来源与该用户已有知识库片段分别保留来源身份，不把用户材料伪装成官方来源。
2. 新增受控 `research → draft → concept graph → quality → repair` 流水线。LLM 只返回完整周主题/月里程碑 JSON；后端负责周数、字段、编号和可衡量产出的确定性校验。
3. W1 必须从修订后的 master 周主题构建，concept graph 也必须消费同一份 master；三者不得继续各用一份旧参数。
4. 最多 2 次大纲修复。线上深度生成最终仍未通过质量门时回滚实例目录且不写索引；离线/测试模式可明确标记 draft，但不得伪装成通过。
5. `master._outline_refinement` 记录输入来源、每轮分数、修复问题和停止原因；生成进度改为真实阶段回调，不再只靠定时器推进。

**Acceptance**
- 测试来源中的关键要求能出现在最终周产出，证明研究改变了 master，而非只附元数据。
- W1.title 与最终 master W1.title 一致，concept graph 周次来自同一 master。
- 强制质量门失败时实例目录和索引都不存在；错误可见且可重试。
- 有跨路径用户知识命中时记录 `user_source_count` 和具体 source_id；无命中时诚实为 0。

**Status**：✅ 已验收。用户知识库跨本人路径检索、证据化大纲、最多 2 次 repair、W1/master/概念图同源与失败原子回滚已落地；生成进度由真实阶段回调驱动。全量 311 个 pytest 通过。

### ADR-052 · V0.50.2 结构化双坐标基线

**Context**：`BaselineEvaluator` 和 `EvalResponse/BaselineSummary` 已存在，但生产对话只依赖模型自由输出 QUIZ，最终只把一段 `baseline_summary` 塞进 UserParams。题目、选项、维度、原始回答和独立/AI 协作坐标都没有事实表，无法复测或证明学习提升。

**Decision**
1. `baseline_assessments` / `baseline_responses` 成为基线事实源，归属 `user_id + session_id`，生成实例后绑定 `instance_id`。
2. 题目由 `BaselineEvaluator` 生成并通过 API 返回；前端提交每题选择、独立完成分和 AI 协作分，后端确定性汇总四个能力维度。
3. `UserParams` 新增可选 `baseline_profile`，兼容旧 GENERATE JSON；generator 将结构化基线完整写入 `master.north_star.baseline_profile`，文本摘要只做展示/旧接口兼容。
4. 学情页展示入学双坐标；后续复测使用同一 schema，不能用自由文本覆盖原始基线。

**Acceptance**
- 基线创建、提交、刷新、会话恢复和实例绑定使用同一个 assessment_id。
- 独立分与 AI 协作分分开保存，后端拒绝缺题、越界分数和跨用户 assessment。
- 生成后的 master 与学情 API 均能读取同一结构化基线。

**Status**：✅ 已验收。`BaselineEvaluator` 通过独立 API 现场出题，主对话只发起请求，避免重复 LLM 生成；题目快照、原始回答、每题独立/AI 坐标、维度汇总和实例绑定已落地。全量 318 tests 通过；真实浏览器已验证刷新恢复、结构化提交、1280px / 390px 无溢出与零控制台错误。

### ADR-053 · V0.51 周计划与自适应闭环

**Context**：只有 W1 真正落盘，W2–Wn 是前端 `weekPreviewDays()` 伪预览；自检页面存在但主路径无入口；`replan.detect()` 只给建议，不能确认、应用或撤销。

**Decision**
1. 周计划归属 generator/path_loop：当前周完成且自检通过后，按 master + 上周结果 lazy build 下一周 `Wn.json`，原子落盘并记录 build state。
2. 周末/周完成后在路径首页和计划页显示真实“6 关自检”入口；未通过时保持当前周并显示 R1 动作，通过后才解锁下一周。
3. replan 引入 `proposal → confirm → applied/rejected` 生命周期和持久 proposal_id。只有用户确认后才修改 master/周计划，且保留 before/after snapshot 与一次撤销能力。
4. 删除前端通用假预览；未生成周只显示周目标、生成条件和真实状态。

**Acceptance**
- 完成 W1 + 自检通过后生成 W2，刷新后仍存在并解锁；未通过不会生成或越周。
- 路径页能进入正确 week 的自检，提交结果后回到同一 instance。
- replan 检测本身不改计划；确认后 master 变化且刷新保持；拒绝/撤销路径准确。

**Status**：✅ 已验收。未生成周不再伪造 7 天预览；周日单元完成且自检通过后原子生成 Wn、更新 master 状态并刷新可恢复。replan 实现 `proposed → applied/rejected → undone` 持久生命周期，master 漂移时会拒绝覆盖。全量 328 tests 通过；真实浏览器已验证 W2 从未生成到解锁、自检周次与返回链路、replan 确认/撤销，390px 无溢出且零控制台错误。

### ADR-054 · V0.52 运行可靠性与生产底线

**Context**：对话 engine、路径 job 和 lesson job 主要在进程内存；聊天 session_id 未绑定 user_id；重启/多 worker 后任务可能失联。Cookie 未按部署环境启用 Secure，关键写 API 缺少轻量限流和数据生命周期出口。

**Decision**
1. 对话从 `conversation_messages` 按 `user_id + session_id` 恢复，内存 key 同样绑定 user_id；拒绝跨用户复用 session_id。
2. 长任务状态写入 SQLite `background_jobs`，服务启动/状态查询可从落盘结果恢复 ready/error，不把进程内 task 当唯一真相源。
3. Cookie 的 `secure` 由 HTTPS/环境决定，生产模式强制 Secure；认证和生成写接口增加可配置的按用户滑动窗口限流。
4. 增加当前用户数据导出、账户删除和保留期清理服务；删除动作需要密码确认并原子清理 SQLite 用户数据与实例目录。
5. 单机 SQLite 是本轮明确生产边界；多机队列/对象存储不伪装完成，留给 V1 基础设施。

**Acceptance**
- 重启后对话能恢复同一用户历史，另一用户复用 session_id 看不到也不能污染。
- 生成完成记录可从持久 job 查询；僵尸 running 有明确 recovered/error 状态。
- Secure Cookie、限流、导出、删除和保留期清理均有正反测试。

**Status**：✅ 已验收。`conversation_sessions` 与 `conversation_messages` 支持 user+session 归属、上下文和最后一组结构化选项恢复；路径/学练任务写入 `background_jobs`，服务重启把遗留 running 转成可重试错误并保留 ready/error 查询。生产环境强制 Secure Cookie；登录、对话、摸底、课程生成和 replan 使用 SQLite 滑动窗口限流；账号页提供 JSON 导出与密码确认删除，保留期清理只删过期日志、不删学习成果。全量 349 tests 通过；隔离浏览器完成桌面/390px、跨账号、刷新、两类任务重启、30/31 限流与账号删除回归，控制台零错误。

**Deployment**：✅ 2026-07-11 已部署到火山引擎 ECS / `https://learnbuddy.top`。发布前备份位于 `/opt/itutor-backups/20260711-082658`，包含 SQLite 在线快照、7 个实例、旧代码、生产 `.env`、systemd 与 Nginx 配置；备份和线上数据库 `PRAGMA integrity_check=ok`。部署后用户数保持 6、实例数保持 7，`conversation_sessions` / `background_jobs` / `rate_limit_events` 迁移完成，生产 Secure Cookie 判定为 True，服务无启动错误。

### ADR-055 · V0.53 真实能力诊断与可追溯能力画像（本地开发与工程回归完成）

**Context**：V0.50.2 已把题目、原始回答、独立/AI 坐标和实例绑定变成持久事实，但当前画像仍直接平均用户填写的 `independent_score` / `with_ai_score`，保存的答案和 `answer_hint` 没有进入客观判分。产品表面上完成了“现场出题”，实际仍是场景题辅助下的结构化自评；这会把错误基线继续传给路径、课程难度、学情雷达和重规划。V0.53 必须先修复测量有效性，再进入 V0.54 深度教学内容升级。

#### 目标与边界

**目标**
1. 把需求收集与能力评估彻底分开：目标、时间、偏好只约束路径，不直接贡献能力分。
2. 形成有证据来源的六维能力画像，并明确区分真实表现、AI 协作表现、主观信心和评分置信度。
3. 让每个路径补强决策都能追溯到具体题目、回答、评分依据和证据 ID。
4. 保留 V0.50.2 的 user/session/instance 归属、刷新恢复、限流和数据生命周期能力，不重做已验收地基。

**本期不做**
- 不在 V0.53 同时重写教学内容流水线；深度课程进入 V0.54，消费本期的新画像。
- 不引入自由多 Agent、LangGraph 或 OpenClaw；诊断采用确定性判分 + 受控 evaluator workflow。
- 不上 IRT/CAT 大规模题库、在线监考或跨用户训练；先用版本化 Blueprint + 30 条离线 Eval Case 建立可信基线。
- 不把“使用外部 AI 是否作弊”做成强监控；产品只保证站内独立阶段不提供 AI，结果报告诚实标注测量边界。
- 不删除或覆写旧基线；旧数据保留但必须标记为 `self_report_v1`，不得伪装成客观诊断。

#### 用户与关键场景

| 场景 | 需要识别的真实差异 | 系统必须给出的结果 |
|---|---|---|
| 零基础但自评较高 | 自信不等于掌握 | 实际独立分低、校准差高，并从前置能力补起 |
| 基础扎实但不善用 AI | 独立能力与协作能力不能混为一谈 | 保留高基础，增加 AI 拆解/验收练习，不重复补基础 |
| 借助 AI 能产出但不会验收 | “让 AI 代做”不等于 AI 协作 | 协作产出分与验收纠错分分开，优先补验证能力 |
| 答案正确但推理错误 | 偶然正确不能当稳定掌握 | 降低评分置信度，追加验证题或进入 `needs_review` |
| 开放领域缺少唯一答案 | 不能硬造标准答案 | 用版本化 Rubric 多维评分，并展示证据与不确定性 |

#### 对象模型与真相源

| 对象 | Canonical ID | 所有者 | 关键字段 | 真相源 |
|---|---|---|---|---|
| `AssessmentBlueprint` | `blueprint_id + version` | `core/evaluator.py` / 新 assessment service | domain、target、capabilities、difficulty_plan、stage_plan、time_budget | 版本化配置/快照 |
| `AssessmentAttempt` | `assessment_id` | baseline service | user_id、session_id、instance_id、mode、stage、status、blueprint_version | SQLite `baseline_assessments` 演进 |
| `AssessmentItemSnapshot` | `assessment_id + item_id` | baseline service | capability、type、difficulty、prompt、options、rubric、reference_answer、grading_method | 评估创建时快照，提交后不可变 |
| `AssessmentResponse` | `response_id` | baseline service | phase、answer、self_confidence、assist_trace、duration、submitted_at | SQLite 响应事实表 |
| `ScoreEvidence` | `evidence_id` | scoring service | observed_score、rubric_scores、reason、evaluator_version、confidence、flags | SQLite 新评分事实表 |
| `AbilityProfile` | `profile_id + version` | profile service | 六维分、证据引用、profile_confidence、completed_at | 由 ScoreEvidence 确定性聚合 |

规则：所有页面、路径生成器和学情接口只读取同一个 `profile_id/version`；不得从聊天文本、用户自评分或前端临时状态重新推导另一份画像。

#### 六维能力合同

| 维度 | 含义 | 主要证据 |
|---|---|---|
| `independent_foundation` | 不借助 AI 的概念、规则和基础应用能力 | 独立阶段客观题 + 开放题 Rubric |
| `independent_transfer` | 把知识迁移到新任务或真实场景的能力 | 独立迁移任务、解释与产出 |
| `ai_collaboration` | 能否拆任务、补上下文、迭代并形成最终产出 | 站内 AI 协作阶段的过程与最终答案 |
| `verification_correction` | 能否发现来源、边界、逻辑和结果错误并修复 | 错误 AI 产物诊断、校验计划、修订结果 |
| `self_calibration` | 主观信心与真实表现是否一致 | 答题后信心分与 observed score 的差值 |
| `learning_strategy` | 能否选择合适的练习、复盘和验收方法 | 场景策略题与迁移任务 Rubric |

内部统一使用 0–100 分；前端只展示能力等级、证据和改进建议，不用一个总分掩盖维度差异。`self_confidence` 只计算校准差，绝不能直接计入 `observed_score`。每个维度同时给 `score`、`confidence` 和 `evidence_ids[]`。

#### 诊断流程与状态机

1. **需求事实冻结**：确认 domain、目标产出、时间预算和已有经验；这些字段只进入 Blueprint。
2. **Blueprint 构建**：按目标生成 6–10 题的能力/难度/题型计划；先生成候选池，再校验答案可判定性、歧义、难度和能力覆盖。
3. **独立阶段**：完成概念判断、应用和迁移任务；站内不提供 AI；每题作答后再填信心，避免信心影响回答。
4. **AI 协作阶段**：完成一组配对任务，允许使用站内 AI；保存用户指令、AI 输出、用户修改、校验动作和最终答案，不以“用了几次 AI”直接加减分。
5. **评分阶段**：客观题确定性判分；开放/代码/设计题按 Rubric 由独立 evaluator 评分；低置信或矛盾结果追加验证题或进入人工/再次评估状态。
6. **证据报告**：展示六维画像、代表性证据、主要误区、评分置信度与下一步，不展示隐藏答案给仍可继续作答的题。
7. **绑定路径**：只有 `completed` 且达到最低画像置信度时才能作为正式路径输入；否则只能生成明确标记的 provisional 路径或继续诊断。

状态固定为：

`created → independent → ai_collaboration → grading → completed`

异常分支：`needs_review`（可追加题/重新评分）｜`failed`（可重试，保留已提交回答）｜`superseded`（新评估取代旧版本但不删除历史）。刷新、断网、重启后必须从同一 `assessment_id + stage` 恢复。

#### 出题与判分合同

- `choice/fill`：必须有规范化标准答案和解释；后端确定性判分，LLM 不得覆盖结果。
- `open/code/design`：每题必须有 3–5 个 Rubric 维度、每档锚点、常见错误和关键失败项； evaluator 只返回结构化评分与证据句，不得自由改题。
- `AI collaboration`：至少评估任务拆解、上下文质量、迭代修正、结果验收四项；最终答案正确但完全未验收，`verification_correction` 不能自动判高。
- 结果正确但解释/过程与参考逻辑矛盾时标记 `lucky_correct`，追加等价验证题。
- evaluator `confidence < 0.7`、两个评分器差异 > 15 分或答案存在歧义时进入 `needs_review`，不能静默平均。
- 每次评分记录 `blueprint_version`、`item_version`、`rubric_version`、`evaluator_version`、model、prompt_version、latency 和 token usage。
- `answer_hint` 改为内部 reference/rubric 输入；任何公开 API 和前端 payload 都不得返回。

#### 产品交互合同

- 开始前明确说明预计 8–12 分钟、包含独立和 AI 协作两段、进度会自动保存。
- 页面固定展示当前阶段、已完成题数、可退出继续；不能用聊天气泡模拟持久评估状态。
- 独立阶段与 AI 协作阶段必须视觉和文案明确区分，避免用户误以为两阶段规则相同。
- 结果页优先展示“证据 → 判断 → 路径影响”，而不是只有雷达图；用户可查看评分依据、提交异议或重新评估。
- 对低置信结果显示“诊断证据不足”，不得使用确定语气生成强结论。
- 390px 与桌面均支持长题干、代码、Rubric 和断点恢复；返回/取消不得误提交。

#### 路径消费规则

- `generator.py` 只消费 `AbilityProfile.observed_*` 和证据摘要，不再把自评分当基线事实。
- 每个补强周或降难度决策必须写 `decision_evidence_ids[]`；没有证据只能标为默认策略。
- `independent_foundation` 低、`ai_collaboration` 高：先补独立底座，同时增加 AI 验收任务，不能因为 AI 产出高而跳级。
- 独立能力高、AI 协作低：保留知识难度，增加拆解、上下文和验收练习，不重复基础课。
- `profile_confidence` 低于门槛：禁止在用户无感知的情况下生成“精准画像”；必须提示继续诊断或接受 provisional 路径。

#### API 与模块边界（实现时可在不破坏语义的前提下微调路由名）

| 能力 | API 意图 | 写入对象 | 关键守卫 |
|---|---|---|---|
| 创建/恢复 | `POST /api/baseline/assessments`、`GET /current` | AssessmentAttempt + item snapshots | user+session 幂等、跨用户 404 |
| 增量答题 | `PUT /{assessment_id}/responses/{item_id}` | AssessmentResponse | 当前 stage、题目归属、重复提交幂等 |
| 获取下一题 | `GET /{assessment_id}/next` | 只读/可追加验证 item | 已提交前置题、不得泄露 reference |
| AI 协作 | `POST /{assessment_id}/assist` | assist trace | 仅 ai_collaboration stage、限流、可追溯 |
| 提交评分 | `POST /{assessment_id}/submit` | ScoreEvidence + AbilityProfile | 完整性、评分状态、后台任务可恢复 |
| 结果/异议 | `GET /{assessment_id}/result`、`POST /dispute` | profile / review request | 本人可见、完成后才公开证据 |

#### 兼容、隐私与失败边界

- 旧 `BaselineProfile` 保持可读，新增 `assessment_method=self_report_v1`、`evidence_level=self_report`；不得把旧分数迁移成 observed score。
- 新画像使用独立 contract version；旧实例可继续学习，用户重新评估后通过新 profile version 显式替换当前画像。
- AI 协作 trace 属于账户导出/删除范围；管理后台只看匿名聚合，默认不展示用户完整回答。
- 评分 LLM 超时或解析失败时 attempt 进入 `failed/needs_review`，已答内容不丢；禁止回退成“直接采用用户自评分”。
- 诊断失败不扣除完整路径生成额度；重试和重复提交必须幂等。

#### Eval Set 与发布验收

开发前先建立 30 条固定 Eval Case：语言、编程、写作、考试、职业技能、通识各 5 条；覆盖零基础/中阶/高阶、信心高但答错、结果对但理由错、AI 代做不验收、题目歧义和 evaluator 分歧。

**P0 验收门**
- 客观题标准样本判分 100% 正确，越权、漏题、重复题、reference 泄露均有负例。
- 开放题在冻结样本上与双人人工评分的档位一致率 ≥80%，平均绝对误差 ≤10/100；不达标不得发布。
- 自评分变化不能改变 observed score；同一答案重复评分在相同版本下结果稳定。
- 至少 3 条端到端样例能从 Blueprint → 回答 → evidence → profile → path decision 追溯同一 ID。
- 创建、增量保存、刷新恢复、阶段切换、评分失败重试、跨账号、异议和重新评估全部达到 6/6 闭环。
- 旧基线在 UI/API 中明确标为“历史自评画像”，新旧数据均可打开且不互相覆盖。
- 全量 pytest、真实 DeepSeek 评分样本、桌面与 390px 浏览器回归、零控制台错误通过。

**效果观察指标（上线后，不作为小样本发布阻塞）**：诊断完成率、平均耗时、追加题率、`needs_review` 率、画像置信度分布、用户异议率、路径证据覆盖率，以及后续 V0.55 的学前—学后增益关联。

**Status**：🟢 本地开发与工程回归已完成，生产仍为 V0.52，尚未发布 V0.53。已落地版本化 Blueprint、双阶段增量作答、确定性/Rubric 判分、低置信追加验证、六维证据画像、路径 evidence_id 决策、旧 `self_report_v1` 兼容、账户数据生命周期和匿名质量聚合。固定 Eval Set 为六领域各 5 条共 30 条；其中 18 条开放题已有真实领域材料、Rubric 和当前 DeepSeek evaluator 冻结分数，双人人工评分表与一致率/MAE 计算器已完成。全量 398 tests 通过，真实日语 N3 端到端样本得到 6 条评分证据和 89% 画像置信度；1440×900 / 390×844 均无横向溢出，刷新恢复与控制台零错误通过。两位人工独立评分、生产备份、部署与线上回归仍属于 V053-10，全部通过后才能标记发布完成。

### ADR-056 · V0.53 发布采用不可绕过的 Release Loop（执行中）

**Context**：V0.53 的人工复核、pytest、生产备份、代码同步和线上回归已经分别有工具或历史操作证据，但缺少一个统一入口。当前 `scripts/deploy_to_ecs.sh` 可以直接上传并重启服务，无法证明人工评分门已经通过，也不会强制先做生产快照。继续依赖人工记忆会让“工程回归完成”和“版本已发布”再次混淆，也无法在失败时准确回答停在哪一关。

**Decision**：

1. 新增单一发布编排入口 `scripts/release_loop.py`，V0.53 固定按 `human_review → local_regression → clean_commit → production_backup → deploy → online_regression` 顺序运行；任一关失败立即停止，不执行后续副作用。
2. `status` 是只读命令：读取冻结的 18 条开放题评分表，输出当前关卡、缺失评分数、指标和唯一下一步；评分未完成或不达标时，V0.54 保持锁定。
3. `verify-local` 只在双人人工门通过后运行全量 pytest，并要求 tracked worktree 干净；用户自己的未跟踪输出不作为发布阻断，也不得被自动提交。
4. `release` 是显式生产命令：必须提供版本确认串，先通过全部本地门，再通过 SSH 创建带时间戳的生产备份并校验 SQLite 完整性，然后才调用现有部署脚本。`deploy_to_ecs.sh` 继续只负责代码传输、重启和基础 HTTP 检查，不再作为正式发布的推荐入口。
5. 线上回归至少验证公网入口、systemd active、SQLite `integrity_check=ok` 和关键版本文件已更新；任何失败都输出备份位置与恢复提示，不能写“发布成功”。
6. Release Loop 只解决 V053-10 的发布闭环，不提前实现 V0.54，不修改生产数据结构，不覆盖远端 `data/` 或 `.env`。

**Exit Gate**：

- 人工档位一致率 ≥80%，MAE ≤10/100，且两位评审完成全部 18 条。
- 全量 pytest 通过，tracked worktree 指向一个明确提交。
- 生产备份存在且数据库完整；部署后服务、入口、数据库和版本文件回归通过。
- `SPEC.md`、项目工作台与 `AGENTS.md` 只在上述证据齐全后同步为“V0.53 已发布”。

**Status**：🟡 Release Loop 本地机制已完成，新增 7 条门禁测试后全量 `398 passed`。`status`、`verify-local` 和 `release --confirm V0.53` 均准确停在 `human_review`，当前 18 条样本完成 0 条、缺 36 个评分单元；未连接生产、未备份、未部署。

### ADR-057 · 2C 飞书学习助手采用共享 OpenClaw 与用户级逻辑隔离（本地 PoC）

**Context**：LearnBuddy 是预计百人以内起步的 2C 产品。用户希望在自己的飞书里和学习助手私聊，查看并调整个人学习计划、记录学习事实和接收主动提醒。不能要求每个用户创建飞书应用、准备 LLM key，或单独部署 OpenClaw 进程。ADR-010 的“大众层不经 OpenClaw”是较早讨论结论；OpenClaw 当前已有官方飞书通道、按发送者分会话和 requester-scoped MCP 连接解析能力，因此可在不为每人起实例的前提下复用。

**Decision**：

1. **对外只有一个公司飞书应用机器人**：学习者只需在飞书里打开/安装并私聊机器人，不创建自己的飞书应用。早期使用可外部共享的企业自建应用；应用商店与租户管理员安装留作后续企业化分发通道。
2. **共享一套 OpenClaw Gateway，不共享用户会话**：采用按 channel + account + sender 分区的 session，不做每用户进程、workspace 或 heartbeat。OpenClaw 只负责飞书通道和 Agent 编排；LearnBuddy 仍是学习计划、进度、掌握度、记忆与提醒规则的唯一事实源。
3. **绑定是显式的两端闭环**：已登录用户在 Web 生成一次性绑定码，再将该码发给飞书机器人；乔接层只使用 OpenClaw 传入的可信 `requesterSenderId` 兑换绑定，不接受模型或用户参数传 `user_id`。明文绑定码只返回一次，库内仅保存不可逆摘要，且有过期、消费和失效状态。
4. **Agent 不持有用户长期凭证**：OpenClaw 插件使用服务端 bridge secret + 可信发送者标识换取短时 HMAC bearer token，token 含 `binding_id/user_id/scopes/exp`；远程 MCP 验签后在服务端注入 `user_id`。任何工具都必须继续经过现有路径归属检查。
5. **提醒是确定性调度，不是每用户 Agent 定时循环**：`reminder_policy` 保存时区、本地时间、星期、静默时段、路径与开关；全局 scheduler 轮询到期规则，用幂等键生成 delivery 事实，再由飞书通道发送。提醒内容优先复用 `recommend_today`，调度本身不调 LLM。
6. **数据生命周期纳入账户边界**：绑定、规则和发送事实可导出；摘要、bridge secret、bearer token 不导出。用户解绑立即中止后续 token 签发，删除账户时通过外键级联删除全部个人集成数据。

**核心对象与状态**：

- `channel_binding`：`pending/active/revoked`，唯一约束为 `(provider, account_id, external_user_id)`；一个飞书身份不能同时归属两个 LearnBuddy 账号。
- `binding_code`：`pending/consumed/expired`，成功兑换是单次事务，旧绑定码不可重放。
- `reminder_policy`：`enabled/paused`，用户可通过 Web 或 Agent 读取、修改、暂停；无 active 绑定时可保存规则，但不生成飞书发送。
- `notification_delivery`：`pending/sent/failed/skipped`，以 policy + channel + scheduled time 幂等；失败可重试，但不重复产生学习记录。

**权限边界**：首期只开放 LearnBuddy 学习工具与飞书私聊提醒，不给 Agent 系统命令、任意文件、任意网络或管理员权限。新建/修改用户飞书任务、日历、文档属于另一个权限层，必须引入该用户的 Feishu OAuth `user_access_token` 和单独授权页后才能开放，不在本地 PoC 内。

**闭环验收（发布前必须 6/6）**：

1. LearnBuddy 用户 A 生成一次码，飞书 A 可兑换并看到绑定状态。
2. 同一码重放、过期码、飞书 A 绑到另一个 LearnBuddy 账号均被明确拒绝。
3. 飞书 A 只能通过远程 MCP 读写用户 A 的学习数据，伪造 `user_id`、过期 token、解绑后 token 都失效。
4. 用户 A 通过 Agent 设置提醒后，Web 立即读到同一条规则；静默时段/暂停/解绑不产生发送。
5. 同一调度槽重复扫描只产生一条 delivery，发送失败有可见错误与可重试状态。
6. 账户导出包含可读的绑定/规则/发送事实但不含秘密；删除账户后所有归属记录和旧 token 均不可用。

**分阶段交付**：本轮先交付数据模型、一次绑定码、bridge 验证、用户级短 token、HTTP MCP、提醒规则和 Web 绑定入口，以及 OpenClaw 插件/配置骨架。真实飞书联调与主动发送需要飞书 App ID/App Secret、机器人发布与服务器环境复核后再开启。

**2026-08-15 ECS 只读容量复核**：当前火山引擎主机为 2 vCPU / 7.8 GB RAM / 20 GB 系统盘，复核时可用内存约 6.8 GB、剩余磁盘 12 GB、负载均为 0.00；`itutor` 服务约占 151 MB，生产数据目录约 7.8 MB。这个余量可支持百人以内、低并发的共享 Gateway 首期验证，但发布前仍需用真实对话+定时提醒做并发冒烟，不把容量推断写成压测结论。当前 Node.js 为 18.19.1，低于 OpenClaw 当前要求的 22.22.3+，且主机未安装 Docker；部署时应为 OpenClaw 单独安装 Node 22 运行时和 systemd 服务，不覆盖现有 Node 18，避免影响 3001–3003 的已有服务。

**Status**：🟢 本地 PoC 底座已完成：绑定码→可信 sender 绑定→短 token→HTTP MCP→解绑失效闭环已通，提醒规则、幂等队列、5 次有界重试、飞书发送器、Web 入口和 OpenClaw requester-scoped 插件骨架已落地。新增专项 46 条，全量 `413 passed`；1440×900 / 390×844 无横向溢出、弹窗可滚动，绑定码和提醒保存的真实浏览器交互通过。真实 OpenClaw Gateway 加载、飞书应用发布/认证、生产密钥、并发冒烟与线上回归仍未执行；本分支不影响线上 V0.52，也不取代 V0.53 的人工评分与发布门禁。

### ADR-058 · 一套代码、Cloud 体验版与 Personal 开源版；不再共享托管 OpenClaw

**Context**：产品定位调整为开源优先。用户希望每个人拥有并可自行部署自己的 LearnBuddy，自备模型 Key，并可使用自己的飞书与 OpenClaw；同时保留一个公有云网站用于直观体验，但平台只承担有限 token。OpenClaw 官方安全边界是一套 Gateway 对应一个信任域，不能把“按 sender 分会话”视为互不信任 2C 用户之间的运行时隔离，因此 ADR-057 的共享 Gateway 只能保留为历史 PoC，不能继续成为产品目标。

**Decision**：

1. 使用同一代码仓，通过 `LEARNBUDDY_EDITION=cloud|personal` 和后端 `RuntimeCapabilities` 切换发行模式；默认 `cloud` 兼容现有部署，非法值启动失败。
2. `cloud` 是受限 SaaS 体验：开放注册、用户隔离、平台 LLM、积分/token 配额、限频、管理员观测；不接用户飞书，不托管共享 OpenClaw，也不保存用户的 App Secret。
3. `personal` 是完整个人版：首次只创建一个 owner，随后关闭注册；用户自备 LLM Key，关闭平台积分和多用户运营后台；学习数据进入用户自己的持久卷。
4. OpenClaw 是 Personal 的可选独立运行时，不替代 LearnBuddy Core。计划、任务、进度、掌握度、记忆和提醒规则继续只有一个真相源；OpenClaw 负责 Agent 循环、会话和飞书通道。
5. 第一版个人飞书对话只走 OpenClaw 官方飞书通道，不再并行建设 LearnBuddy 原生飞书入站 Agent。用户拥有自己的飞书应用和凭据，Gateway 仅本机/私网监听，默认只允许 owner sender，禁用群聊与高风险系统工具。
6. 后端权限守卫先于前端展示；Cloud 必须拒绝个人集成接口，Personal 必须拒绝第二账号、积分运营和管理 API。旧共享桥接代码仅保留迁移兼容，不再作为默认用户路径。

**Product contract**：公有云闭环为“注册 → 有限摸底/路径/一节学习 → 看到画像和剩余额度 → 导出或部署个人版”；个人版闭环为“部署 → 唯一 owner → BYOK → 长期学习 → 可选 OpenClaw/飞书 → 备份升级”。本周不做支付、订阅、组织、多 Gateway 托管和企业 SSO。

**Acceptance**：发布门槛与完整能力矩阵见 [`docs/07-开源与公有云双版本架构.md`](docs/07-开源与公有云双版本架构.md)。只有干净环境安装、重启持久化、额度拒绝、租户隔离、OpenClaw 工具回写、owner 飞书消息往返和备份恢复均有独立证据后，才能把首个开源版本标记为可发布。

**Status**：🟡 本轮实施中。ADR-057 代码保留兼容，其“共享一套 OpenClaw Gateway”的产品决策由本 ADR 覆盖。

### ADR-059 · 开源发布前安全门：额度、Personal 单 owner 与管理员初始化必须后端闭合

**Context**：Cloud / Personal 双模式实现后，发布前负向审计确认了三类后端边界缺口：基线评估的三个 LLM 入口可绕过 Cloud 积分门；旧 Cloud 多用户数据库切到 Personal 时只有 readiness 报错但业务仍可访问；公开注册仅凭管理员邮箱环境变量即可获得后台权限。以上问题会分别造成平台成本失控、Personal 信任边界失效和管理员权限被抢注。

**Decision**：

1. 所有会触发基线 LLM 的 HTTP 入口在调用前统一执行 `check_quota`；Personal 仍由 provider 自有额度负责，Cloud 无额度时返回 429，且创建评估也进入明确的 usage scene。
2. Personal 检测到超过一个用户时 fail closed：启动失败，同时 HTTP 层只允许 `/healthz`、`/readyz` 与 `/api/runtime-capabilities` 三个诊断端点，不能靠前端隐藏或反向代理 readiness 代替权限边界。
3. 公开注册永远创建普通用户，普通登录永远不按邮箱或环境变量自动晋升；历史数据库里已经落定的 `is_admin=1` 继续有效。
4. Cloud 管理员只允许在部署机上对一个显式目标账号执行初始化：目标账号必须已经存在，操作者必须输入该账号当前密码；脚本重复执行幂等，不接收命令行密码，也不从公开请求授予权限。

**Acceptance**：零额度时三条基线路由均拒绝且底层函数未调用；Personal 双用户库的诊断端点可读但所有业务页面/API 返回 503，启动钩子明确失败；配置旧管理员邮箱环境变量也不能让公开注册或登录获得管理员；本机管理员脚本对错密码/Personal 模式拒绝，对正确账号首次晋升、再次执行幂等。

**Status**：✅ 已落地；专项回归与全量测试通过，真实生产部署仍由发布门单独验收。

---

## 6. 工程铁律

### 6.1 Git

- 任何 ≥ 5 行代码改动前先 `git commit` 当前状态
- Commit message 中文 Conventional Commits + 30 字目的
  - 示例：`feat(core): 加 schemas 数据模型 + DeepSeek client 流式封装`
- `.gitignore` 强制排除：`.env*` / `__pycache__/` / `data/instances/*` / `.pytest_cache/`

### 6.2 密钥安全

- API key 严禁出现在代码 / 注释 / 提交历史
- 用 `os.getenv("DEEPSEEK_API_KEY")` + `.env`（python-dotenv 加载）
- `.env.example` 只占位不填值

### 6.3 验证（Anthropic 官方最高杠杆）

- 任何"看起来对"的输出必须能验证
- `core/` 改动必须 pytest 通过
- web 改动必须浏览器实测一次

### 6.4 文档协议

- 不造新散落 markdown
- 任何重大决策记到 SPEC.md ADR 段
- 5 天计划任何调整记到 SPEC.md 或 README

---

## 7. V1 Backlog（V0 之后的事）

按优先级排：

| P | 项 | 大致工作量 |
|---|---|---|
| P0 | 6 关自检交互页（每周日触发，6 维度评分 + AI 反馈） | 2–3 天 |
| P0 | 反遗忘 5 机制实装（micro-review 卡片 / Anki 导出 / 周回测） | 4–5 天 |
| P0 | 多租户（user_id + 数据隔离）+ 注册登录 | 3–4 天 |
| P1 | 持续运营仪表盘（today / calendar / progress 多页面） | 5–7 天 |
| P1 | 月末 R11 评估 + 节奏自动调整（adjustments-log 自动化）| 3–4 天 |
| P1 | 翻译层原则（任何技术动作的"业务语义 + 风险提示"翻译）| 2 天 |
| P2 | 工程底线 8 项（密钥/环境/行级权限/备份/鉴权/兜底/回滚/隐私）| 5–7 天 |
| P2 | Claude Agent SDK 5 大模式（Durable State / Cost Caps / Circuit Breakers / Tool Permission / Eval Hooks）| 7–10 天 |
| P2 | 多 LLM provider 路由（云雾代理）| 1–2 天 |
| P3 | 反向插拔到原 `AI技术学习空间`（替换 itutor_engine + generator）| 2–3 天 |

---

## 8. 版本日志

| 日期 | 版本 | 修订 |
|---|---|---|
| 2026-05-23 | V0 草稿 | Day 0 创建 · 5 天计划 + 6 个 ADR + V1 backlog |
| 2026-06-18 | V0.6 | 对齐《个性化 AI 学习平台·需求方案》：差距分析 + Batch 路线图（§9）+ ADR-007 |
| 2026-06-18 | V0.7 | Batch 1（教练复盘 + 4-2-1 + 防幻觉）+ Batch 2A（知识图谱 keystone + 规则版掌握度，ADR-008）；27 点清单折入 §9.5 |
| 2026-06-18 | V0.8 | 真 RAG 检索层（BM25+可选 dense 混合 + 知识库入库/检索 + 讲解引用来源，ADR-009）；embedding 可插拔，未配 key 真实降级 BM25 |
| 2026-06-18 | 决策 | ADR-010：OpenClaw（龙虾）集成策略——不 fork + 双层部署（大众=中心化飞书机器人，Pro=自托管 OpenClaw+iTutor skill 包）；仅决策，未落地 |
| 2026-06-18 | 决策 | ADR-011：技术分层（确定性/工具/工作流/动态工作流/Agent）+ Loop 化自迭代闭环；新增 Batch L（§9.3）+ 落地可用性盘点（§9.6）；仅文档，未落地 |
| 2026-06-18 | V0.9 | Batch L 第 1 步落地：统一工具注册层 `core/tools.py`（8 工具）+ `GET /api/tools` 透明 + `mcp_server.py`（标准库 spec 兼容 MCP stdio，对接 OpenClaw/Claude）；124 测试通过；架构总览页同步技术分层/Loop 化 |
| 2026-06-18 | V0.10 | Batch L 第 2 步落地（环 B 的眼睛）：反馈采集（`feedback` 表 + 讲解 👍👎/纠错 + `submit_feedback` 工具，共 9 工具）+ LLM Tracing（`llm_traces` 表 + `core/tracing.py`，接 lesson/coach/graph）+ 管理后台质量面板 `/api/admin/quality` + golden 解析回归；136 测试通过；架构总览/自改进闭环同步 |
| 2026-06-18 | V0.11 | Batch L 第 3 步落地：讲解升级 evaluator-optimizer 工作流（起草→自检→修订，`generate_lesson_reviewed` + `lesson_review.md`，env 可关）；server/工具改用 reviewed 版；两步分别 tracing；前端「已自检」标；141 测试通过 + 真 LLM E2E |
| 2026-06-18 | V0.12 | Batch L 第 4-5 步落地（环 A 断点）：每日推荐编排器 `core/recommend.py`（复习/新课/弱项按学情确定性路由，不调 LLM）+ `GET /recommend` + 前端「今日聚焦」卡；个人记忆系统 `user_memory` 表 + `core/memory.py`（派生弱项/强项/节奏 + 手写偏好，召回注入教练）+ `GET/POST/DELETE /memory` + 学情页「关于你」面板；新增 `recommend_today`/`recall_memory`/`remember` 三工具（共 12）；152 测试通过 + TestClient E2E |
| 2026-06-18 | V0.13 | Batch L 第 6 步落地（环 A 决策收尾）：动态重规划 `core/replan.py`（卡壳/超额/中断三触发器，确定性 + 从图谱取前置，给可执行 action）+ `GET /replan` + `check_replan` 工具（共 13）+ 今日页分色横幅；环 A「决策」断点全闭合；157 测试通过 + TestClient E2E（卡壳触发 + 派生记忆同源验证）|
| 2026-06-18 | V0.14 | Batch 2C 落地（建在图谱+掌握度上）：#5 阶段闸门 `core/phases.py`（按 master.months 聚合掌握率，≥70% 解锁下一阶段）+ `/graph`/`get_graph` 返回 `phases` + 路径页分色阶段卡；#2 混合复习——`recommend.py` 升级 7:3 配比 + 新课受阶段闸门约束（不越级）+ 输出 mix/phase；27 点清单 #2/#5/#8 转 ✅；162 测试通过 + TestClient E2E（M2 越级新课被闸门挡住）|
| 2026-06-18 | V0.15 | #2+ SM-2 科学间隔调度 `core/scheduler.py`（回放自评历史→ease factor + 间隔；答对拉长/答错重置/封顶 180 天），替换旧"间隔×2^次数"；`topic_stats` 输出 interval_days/ease，推荐复习项展示下次间隔；167 测试通过（新增 test_scheduler 5 例）|
| 2026-06-18 | V0.16 | #12 知识星图：路径页新增第 3 tab，自绘 SVG 分层 DAG（x 按周分列、y 按难度堆叠）+ prereq 依赖连线 + 掌握度环 + 滚轮缩放/拖拽平移/悬停双向高亮（前置上行+解锁下行）+ 点击开课/锁定提示；零新前端依赖；Node 双层校验（主脚本语法 + renderStarMap 渲染/邻接断言）|
| 2026-06-19 | V0.17 | 移动端推送触点·轨道 A（PWA+Web Push）：通道无关 payload `core/notify.py` + 订阅 CRUD `core/push_subs.py`（events 表取最近活跃 instance、endpoint 幂等 upsert、当日去重）+ pywebpush 发送 `core/push_web.py`（未配 VAPID 真实降级，对齐 ADR-009）+ APScheduler 后台调度 `core/push_scheduler.py`（env 守卫防多 worker 重复、coalesce/max_instances/misfire、Asia/Shanghai）+ `/api/push/{vapid-public,subscribe,test}` + `sw.js`/`manifest.json` + instance 页订阅按钮与 deep-link；闭合环 A「行动 Act」断点；ADR-012。183 测试通过 + 端点 E2E（降级/401/manifest/sw 全通）|
| 2026-06-19 | V0.18 | **首次公网上线**：火山引擎广州 ECS，SSH key 部署 + venv + systemd 常驻 uvicorn:8000 + Nginx 反代（SSE 友好）。配套：① 数据隔离修复 `server.py`——实例严格按 owner 过滤，无主实例不再跨用户泄漏（管理员除外）；② 对话页 `web/index.html` Markdown 渲染 + 单选可重复点 + `<<<标记>>>` 泄漏清理；③ 沉淀本地部署经验 + 一键脚本 `scripts/deploy_to_ecs.sh`（排除 `data/`/`.env`，不覆盖生产数据）。精确实例、EIP、安全组和 SSH 坐标仅保留在运营者本地，不进入公开仓库。|
| 2026-06-19 | V0.19 | **LearnBuddy 品牌适配**：备案/域名对外名确定为「LearnBuddy 学习伙伴」；首页产品名 LearnBuddy；用户可见层统一“学习伙伴 / 学习路径 / 学习空间 / 今天一起学什么”，PWA manifest、service worker、FastAPI title、README、架构总览同步；内部包名、`/instance` 路由、DB 字段暂不迁移，避免破坏历史数据与部署脚本；ADR-013。|
| 2026-06-19 | 决策 | ADR-014：方案文档治理收口——`docs/03-开发方案与计划.md` 升级为唯一长期维护主文档（统一需求/技术/进度/计划），`工作台.html` 改为 HTML 镜像总览，`01/02/04` 转为专题与协作文档，避免多文档版本漂移。|
| 2026-06-20 | 决策 | ADR-015：需求方案页与技术方案页升级为图示化专题页，必须包含可读架构图、关键链路、模块边界和非目标，不再用纯表格代替“架构图”。|
| 2026-06-20 | V0.20 | ADR-016：harness = 模型 + agent + 全部外部工程；新增 [`docs/Harness技术体系对齐.html`](docs/Harness技术体系对齐.html) + [`docs/05-Harness技术体系对齐.md`](docs/05-Harness技术体系对齐.md) + 17 个 Harness 对齐任务（T-21 → T-37）。|
| 2026-06-20 | V0.21 | ADR-017：OpenClaw = 个人助理升级模式（Pro）承载层；新增 [`docs/OpenClaw接入方案.html`](docs/OpenClaw接入方案.html) + [`docs/06-OpenClaw接入方案.md`](docs/06-OpenClaw接入方案.md) + 7 个 OpenClaw 接入任务（T-38 → T-44）。ADR-018：三目标对齐：产品 > 技术 > 文章；主方案页新增“三目标对齐”章节。|
| 2026-06-20 | V0.22 | ADR-019：#12 知识星图 = 已建成（Batch 2D 收口）。代码层 `core/concepts.py` + `core/mastery.py` + `core/phases.py` + `server.py /api/instance/{id}/graph` + `web/instance.html` 三个 tab（疆域地图 / SVG 星图 / 周视图，含缩放/拖拽/依赖高亮/锁定提示）已全部就位。AGENTS.md “下一候选” 同步更新为 “手机号验证码登录 / 6 关自检交互页 / FSRS 调度”。首次端到端实测（API + HTML 静态结构 34/34 PASS），发现的视觉/交互问题（SVG `role="img"` / `aria-label` / legend 加 “双指缩放” 提示）在 V0.22 修掉。|
| 2026-06-20 | V0.23 | ADR-020：6 关自检交互页（V0 模板预埋 → 真实交互）。新增 `core/self_check.py` 表 weekly_self_checks + 3 个 API（submit/latest/history）+ `web/self-check.html` 交互页 + `web/space.html` hero 加 「6 关自检」入口 + `/self-check/{id}` 路由。R1 规则：≥4 关 advance / 3 reduce / 1-2 repeat / 0 reset。提交后异步调 `/coach` 拿 AI 反馈回写表。回归测试 `scripts/verify_selfcheck.py` 23/23 PASS。|
| 2026-06-20 | V0.24 | ADR-021：6 关自检雷达图（visualization）。`web/self-check.html` 加 `renderRadar(svg, scores, prev, labels, opts)` + `refreshRadar()`：纯 SVG 6 维轮廓 + 周环比叠加 + 历史区迷你雷达；零外部图表库依赖；激活条件 6 关填齐；clamp [0,5] 对浮点/NaN/字符串安全。`scripts/verify_radar.py` 43/43 PASS（DOM + CSS + JS + 6 顶点几何数学 + clamp 边界）。**范围排除**：手机号验证码登录 / 支付前置审批中，本轮不做。|
| 2026-06-20 | V0.25 | ADR-022：FSRS-4.5 调度（替换 SM-2，env 切换）。新增 `core/fsrs.py`（17 权重默认 + v4 线性 D0 公式 + DSR 模型）+ `core/scheduler.py` 加 `next_interval(ratings, elapsed_days_list, algorithm)` 统一入口 + `core/learning.py` 改用 `next_interval()`。env `ITUTOR_SCHEDULER=fsrs` 切到 FSRS；缺省 sm2（向后兼容）。LearnBuddy 3 档自评映射 FSRS 4 档：cant→1 / with_help→3 / independent→4。`scripts/verify_fsrs.py` 60/60 PASS（FSRS 数学 + 状态机 + 答错重置 + env 切换 + learning 集成 + 边界）。|
| 2026-06-20 | V0.26 | ADR-023：Web Push 端到端收口。`scripts/verify_push.py` 60/60 PASS（4 模块 + 4 API + 4 SW 关键事件 + 5 manifest 字段 + 2 页 sw.js 注册 + VAPID 优雅降级 + 410 清理）。`web/space.html` 补 `serviceWorker.register('/sw.js')`（之前仅 instance.html 注册）。VAPID 已配（.env），链路从「凭印象 review」升级到「自动化端到端验证」。**范围排除**：手机号 + 付费前置审批中。|
| 2026-06-20 | V0.27 | ADR-024：6 关自检 + 雷达图联通 Web Push。`core/notify.py` 加 `build_selfcheck_reminder()` + `should_remind_selfcheck()` 状态机（first/weekly/force 3 档：7 天 weekly，14 天 force streak 告急）。`core/push_subs.py` `mark_sent/already_sent_today` 加 `kind` 参数（默认 'daily'，用 `day+kind` 复合 key 落 UNIQUE INDEX 不改 schema）。`core/push_scheduler.py` `daily_push_all()` 拆成 daily + selfcheck 两阶段。`web/sw.js` `notificationclick` 按 kind 区分 prefix（selfcheck 跳 /self-check/，daily 跳 /instance/）。`verify_push.py` 60/60 PASS（含 11 项 6 关自检推送测试）。|
| 2026-06-25 | V0.30 | ADR-027：学练内容密度与步骤回看。lesson/review prompt 增加“每环节教学密度”约束，核心卡必须有关键判断、例子/反例/边界或自查标准；生成与质检 token 上限提到 5200；学习页已解锁步骤可点击回看，先想/产出/练习/自评状态保留，完成与统计只记一次。|
| 2026-06-25 | V0.31 | ADR-028：内容质量契约进入生成工作流。新增 `core/content_quality.py` 本地质量门，检查卡片链完整性、互动卡、内容深度、练习质量、主动产出和来源诚实；`generate_lesson_reviewed` 在 LLM 自检后附加 `_quality` 报告，不合格时最多触发一次定向修订；新增质量契约与返工单测。|
| 2026-06-25 | V0.32 | ADR-029：对话入口增加语义起疑、轻量来源探测和会话锚定；harness 等多义概念不会在用户已确认语境后反复追问；输入框改为 Enter 发送 / Shift+Enter 换行并兼容中文输入法组词；具体学习路径页移除新建入口，只保留返回学习空间。|
| 2026-06-26 | V0.33 | ADR-030：LessonFlow / source_pack / Critic 三件套增量落地。新增课程运行时 `_lesson_flow`，旧 lesson cache 读取时兼容升级；`source_pack` 增加 query_plan、source_type、authority/freshness/relevance、claims、ambiguity；质量门新增维度分并挂到 LessonFlow 元素，前端优先消费 LessonFlow。|
| 2026-06-27 | V0.34 | ADR-031：学练内容生成进入“先课设后出课”。lesson 新增兼容字段 `design`，prompt/review/质量门统一校验核心问题、可观察产出、贯穿主线、关键步骤、学习者动作、卡点和快速验证；学习页首屏展示本节问题/学完能做/学习主线，讲解页展示“本节怎么学”；旧 lesson cache 读取时自动补课设骨架。|
| 2026-06-27 | V0.35 | ADR-032：对话入口从词典特例升级为语义接地路由。技术词/新词先抽取搜索 query 并把来源作为后台上下文，多义概念不再硬塞固定选项；新增多轮入口评测集 `tests/fixtures/onboarding_eval_cases.json`，验收通用技术词、多义词、未知新词、用户纠偏和普通目标；明确站内对话暂未接 MCP tool-calling loop。|
| 2026-06-27 | V0.36 | ADR-033：首轮目标澄清必须单问题点选。普通学习目标首轮进入 `choose_goal_output`，用 `<<<CHOICES>>>` 收集目标产出/学习用途并支持自定义补充；系统提示禁止同一条回复同时问最终产出和为什么想学，避免编号重复和开放双问。|
| 2026-06-27 | V0.37 | ADR-034：摸底题从自评档位升级为场景化诊断。基线题必须包含具体材料/任务约束，选项写成具体动作、判断路径、方案选择或错误识别；产品经理/AI 落地方向重点测需求澄清、能力边界、数据闭环、指标验收和跨团队落地；fallback 同步升级。|
| 2026-06-27 | V0.38 | ADR-035：确认生成后立即展示前置进度。前端识别确认生成语后展示“整理生成参数 / 准备资料接地 / 等待进入构建”，后端真实 `generate_start/progress` 接管；对话流式生成默认 90 秒超时，避免模型卡住导致用户无限等待。|
| 2026-06-27 | V0.39 | ADR-036：学练单元生成改为可恢复后台任务。`/lesson` 默认 150 秒内完成直接返回，超时返回 202 且后台继续生成并保存；新增 `/lesson/status`，前端收到 202 或请求超时后自动轮询，生成好后自动打开，不再要求用户手动刷新。|
| 2026-06-27 | V0.40 | ADR-037：学练交互改为“选项独立 + 图解结构化”。先想环节点选项不再自动写入文本框；旧缓存 ASCII 宽图前端过滤并渲染为节点结构图；lesson/review prompt 禁止字符画；质量门把 `visual/compare` ASCII 图设为硬性不通过。|
| 2026-06-28 | V0.41 | ADR-038：对话时间盘点只问“平均每天能投入多久”。不再拆工作日/周末两轮；差异节奏让用户在“其他 / 补充说明”里写；生成参数默认把平均每天同步到 `weekday_hours/weekend_hours`，保持旧 schema 兼容。|
| 2026-06-28 | V0.42 | ADR-039：学练生成任务改为真实状态监控。后端返回阶段、进度、硬超时和可重试状态；状态轮询优先检查落盘缓存，避免内容已生成但页面仍显示进行中。|
| 2026-06-28 | V0.43 | ADR-040：学习路径生成改为可恢复后台任务。`/api/chat` 创建 `job_id`，后台任务完成后先绑定用户归属并保存；新增 `/api/generate/status`，前端刷新或 SSE 断开后可继续轮询并自动跳转。|
| 2026-06-28 | V0.44 | ADR-041：教学内容生成升级为 Loop Engine 方案工作台。新增 `docs/教学内容Loop开发工作台.html`，把 AI-Shifu 调研、Loop 工程文章和当前内容质量问题合并为产品方案、技术方案、迭代路径、Loop Map、数据契约与开放问题；本轮先打磨方案，再进入实现。|
| 2026-06-28 | V0.45 | ADR-042：新增 `docs/LearnBuddy项目工作台.html` 作为长期项目驾驶舱和版本管理中心，版本管理改为一级入口 + 二级下拉，点击版本号进入 `docs/versions/` 下对应三件套详情页；`docs/教学内容Loop开发工作台.html` 降级为 V0.44 教学内容 Loop 方案档案；版本必须是可独立发版功能，原 V0.46-V0.48 工程阶段合并为 V0.46 教学内容 Loop v1，原 V0.49 调整为 V0.47 路径级 Loop v1 待确认。|
| 2026-06-28 | V0.46 | ADR-043：教学内容 Loop v1 先落地契约与运行态。LessonFlow 输出 `contract_version=lesson-loop.v0.46`，卡片保留 completion/source/diagram/free-input 等结构化字段；新增 lesson runtime 独立落盘与读写接口，学习页保存步骤、选项、输入和产出；旧实例与 lesson cache 继续兼容。|
| 2026-06-28 | V0.47 | ADR-044：路径级 Loop v1 先落地状态账本与下一天预生成。新增 `core/path_loop.py` 与 `/api/instance/{id}/path-loop`，完成学练后写 PathEvent 并异步准备下一天；首页/计划页读取 `path_loop.current_unit` 和 `pre_generation`，可显示准备中/已准备好/失败状态并轮询恢复；旧学习记录继续作为推导依据，不迁移线上数据。|
| 2026-06-29 | V0.48 | ADR-045：教学内容生成从巨型 prompt 升级为受控流水线。新增 grounding 质量过滤与正文抓取；单节生成拆成课设、写课、锐度评审、定向修订四段，reasoner 可开关降级；保留旧 lesson schema 和单段 prompt 兜底，重点修复“结构完整但内容空”的根因。|
| 2026-06-29 | V0.49 | ADR-046：教学内容质量门补上“核心教学密度 + 可展开信息层 + bounded quality loop + 学习时长合同”。写作 prompt 要求至少 3 张深讲卡（机制推演 / 边界对比 / worked example）；质量门新增 `teaching_chars`、`dense_teaching_cards`、`expansion_blocks`、`expansion_kinds`、`activity_plan` 与 `estimated_minutes` 检查；critic 按知识完备性、学习质量、需求匹配度、个性化构建等指标迭代；学习页渲染 `expansions[]` 和本节学习安排；`generate_lesson_reviewed` 默认最多 3 轮 `critic → revise` 并保留 best，首请求 45 秒转后台轮询避免前端长等待。2026-06-30 线上 DeepSeek golden case 已通过：180.00s / quality 100 / critic 最低分 80。|
| 2026-06-29 | 决策 | ADR-047：设计系统不作为 V0.50 版本需求，而沉淀为跨版本通用设计规范 `docs/设计规范.html`。后续任何页面、组件、图片资产或 AI 状态表达迭代都先对照该规范；具体页面改造再单独立版本。|
| 2026-06-30 | V0.49.1 | ADR-048：自学可执行层作为内容质量 Loop 的下一轮任务。V0.49 保持可部署基线；本轮只补模板化产出、参考答案/样例、高阶练习、自然个性化和自学验收维度；开发按“真实样本观察 → 可验证假设 → 实现 → 线上 LLM 真测 → 人工阅读”推进。已开发并通过真实线上 DeepSeek golden case：183.82s / quality 100 / activity 100min / critic 最低分 88 / stop_reason=critic_pass。|
| 2026-06-30 | V0.49.2 | ADR-049：路径可信度补丁。大纲质量门新增工作日节奏检查，记录 `w1_progress_weekdays` / `w1_review_only_weekdays`，拦截“隔天整天复习/轻量消化”模板；学练单元后台任务新增真实 `stage_trace`，返回阶段开始、结束、耗时和状态，前端生成页展示每阶段耗时，减少假进度感。|
| 2026-07-10 | V0.50 | ADR-050：可信运行态与内容升级闭环已验收。统一学习空间与路径页的完成进度真相源；lesson cache 引入版本、质量复用门和原子升级；LLM Markdown 统一 allowlist 净化并加资源版本号；路径导航补 button/tab、Enter/Space 与 44px 触点。304 tests 通过，1440×900 / 390×844 无横向溢出，控制台无错误。|
| 2026-07-10 | V0.50.1–V0.52 | ADR-051～054：剩余优化进入连续交付。依次完成 Grounded PathBuild、结构化双坐标基线、W2–Wn/自检/replan 闭环，以及会话与长任务恢复、安全 Cookie、限流和数据生命周期底线；每版独立验收并更新项目工作台。|
| 2026-07-10 | V0.50.1 | ADR-051 已验收：路径研究可消费当前用户跨路径知识库，LLM 大纲通过确定性结构合同后成为 master/W1/概念图的唯一来源；质量不通过最多修复 2 次，最终失败原子清理实例且不写索引；311 tests 通过。|
| 2026-07-10 | V0.50.2 | ADR-052 已验收：基线题由独立 BaselineEvaluator 现场生成，assessment/responses 事实表保留原始题目、回答和独立/AI 双坐标；支持刷新恢复、实例绑定、master/学情同源消费；318 tests + 桌面/移动浏览器通过。|
| 2026-07-10 | V0.51 | ADR-053 已验收：未生成周只展示周目标与真实解锁条件；周日单元完成 + 6 关自检通过后才原子生成下一周并更新 master 状态；replan 引入持久提案、明确确认、拒绝与安全撤销；328 tests + 桌面/移动浏览器通过。|
| 2026-07-10 | V0.52 | ADR-054 已验收：对话按 user+session 持久恢复并隔离账号，结构化选项刷新后可继续；路径与学练后台任务进入 SQLite，重启僵尸任务转可重试错误；生产 Secure Cookie、关键接口限流、账号 JSON 导出、密码确认删除与保留期清理已闭环。349 tests + 桌面/390px + 跨账号/重启/限流/删除负例通过。|
| 2026-07-11 | V0.52 发布 | 火山 ECS 生产发布完成：发布前完成 SQLite/实例/旧代码/配置全量备份；部署提交 `344652e` 且排除生产 `data/` 与 `.env`；线上文件哈希与本地一致，数据库完整性、6 个用户、7 个实例均保持，三张新表迁移成功，`https://learnbuddy.top` 返回 302→登录页 200，systemd active 且无启动错误。|
| 2026-07-11 | V0.53 需求冻结 | ADR-055：把当前“场景题 + 双坐标自评”升级为真实能力诊断。冻结 AssessmentBlueprint / Attempt / ItemSnapshot / Response / ScoreEvidence / AbilityProfile 对象，六维能力模型、独立与 AI 协作双阶段、确定性与 Rubric 判分、低置信复核、证据化路径消费、旧自评画像兼容、30 条 Eval Case 和 P0 发布门；当前仅完成需求细化，尚未开发。|
| 2026-07-11 | V0.53 本地开发与工程回归 | 真实能力诊断已完成本地实现：6–10 题 Blueprint、独立与 AI 协作双阶段、逐题保存/刷新恢复、站内协作 trace、确定性与版本化 Rubric 判分、低置信追加验证、六维 AbilityProfile、证据化路径决策、异议/重评、旧基线兼容、导出删除和后台匿名聚合均落地。30 条六领域离线 Eval Case、18 条真实开放题 DeepSeek 冻结评分与全量 391 tests 通过；双人人工评分表和一致率/MAE 计算器已完成；端到端样本产出 6 条证据和 89% 置信度；桌面/390px 无横向溢出且控制台零错误。生产仍为 V0.52，待两位人工填写评分与备份发布。|

---

## 9. 方案对齐：差距分析 + Batch 路线图（2026-06-18）

> 依据《个性化 AI 学习平台·需求方案》（5 大方法论 + 5 大模块 + 三层记忆 + 评估引擎）做的对齐。
> 原则不变：抵御功能膨胀——每个要做的功能都必须映射到一条方法论支柱。

### 9.1 五大方法论 → 当前实现

| 方法论 | 状态 | 缺口 |
|---|---|---|
| 逆向设计 Backward Design | 🟡 对话 onboarding + 基线评估 | 提案**承诺机制** + **可达性/完成概率**预估 |
| 间隔重复 + 主动回忆 | 🟡 间隔天数复习队列 | **主动产出**环节(4-2-1 的 2)、FSRS 调度 |
| 精熟进阶 Mastery-based | 🟡 按"周"推进 | 按**掌握度**推进、超前/回炉/失衡三触发器、掌握度热力图 |
| 项目化 Project-Based | 🔴 无 | 每节 **1 产出物**、每 Phase **强制项目** |
| 元认知 Metacognition | 🟡 数据看板 | **每周 AI 教练复盘对话**（最强差异点） |

### 9.2 五大模块 → 当前实现

| 模块 | 状态 | 缺口 |
|---|---|---|
| A 对话 Onboarding | 🟡 | 承诺机制 + 可达性评估 |
| B 动态路径 | 🟡 | 掌握度推进 + 三触发器 + 热力图 + 版本回滚 |
| C 即学即练 | 🟡 讲解/选择题/代码lab/自评 | **4-2-1 结构** + 反 Khanmigo 引导 + 防幻觉标注 |
| D 学情&教练 | 🟡 双坐标/队列/趋势 | **每周教练对话** + 宏观行为画像 |
| E 知识沉淀 | 🟡 错题/要点卡/档案(单空间) | 跨空间 Concept 视图 + 时间线 + 知识迁移 |

### 9.3 Batch 路线图（V0 可落地优先，不引入重基建/不接真实生图）

**Batch 1（进行中 · 反 ChatGPT 套壳的两个核心差异点）**
- 每周 **LearnBuddy 复盘**：analytics 数据 → 元认知洞察 + 可追问对话（模块 D / 元认知）
- **4-2-1 学练改造**：讲解 → **主动产出/复述** → 练习 → 小结；每节 **1 产出物归档**到知识库（模块 C / 主动回忆 + 项目化）
- **防幻觉标注**：AI 生成讲解显式标注"请核对"；强化"不直接给答案、引导式"（反 Khanmigo）

**Batch 2C（建在知识图谱 + 掌握度地基上）**
- ✅ **#5 阶段闸门 Phase Gate（2026-06-18 已做）**：`core/phases.py` `compute_phases` 按 master.months（月=阶段）聚合各阶段 concept 的真实掌握率，**达标率 ≥70% 才通关解锁下一阶段**（空阶段视为通关不阻断）；状态 cleared/current/locked；`/graph` 与 `get_graph` 工具返回 `phases`；路径页顶部「阶段闸门」分色进度卡（含交付物 deliverable）。
- ✅ **#2 混合复习（2026-06-18 已做）**：`core/recommend.py` 升级为 ~**7:3**（复习名额 = `round(max_items*0.7)`，但永远给新课留 ≥1 位）；新课候选受**阶段闸门约束**（`current_phase_max_week`，不越级推下一阶段）；输出 `mix`（review/new/weak 配比）+ `phase`（当前阶段简报）。
- ✅ **#2+ SM-2 科学间隔调度（2026-06-18 已做）**：`core/scheduler.py` `sm2()` 回放每个主题的自评历史推出 ease factor + 间隔天数（答对拉长、答错重置重学，封顶 180 天），替换旧的"基础间隔 × 2^次数"；`topic_stats` 接入并输出 `interval_days`/`ease`；推荐复习项展示"下次 N 天后再见"。FSRS 为现代升级，后续可平滑替换。
- **后续**：#2++ FSRS 调度；#12 知识星图可视化；跨空间 Concept 视图 + 时间线（模块 E）；记忆库 L2 可视化；数据一键导出（JSON+MD）；提案承诺机制（模块 A）。

**✅ 已做（按用户要求"技术方案真做"提前实现）**
- **真 RAG（Hybrid 检索 + citation 防幻觉，§3.2）** — `core/rag.py`（BM25 始终可用 + dense 余弦可选 + 分数融合）；`core/embeddings.py` 可插拔 OpenAI 兼容 embedding（未配 key → 真实降级 BM25，非 mock）；知识库入库/检索端点 + 讲解生成接入检索并标注引用来源（ADR-009）

**Batch L（Loop 化 · 让产品"成环可用" · ADR-011）** — 从落地/可用性视角，这是优先级最高的一批
> 目标：把散点功能接成"用户学习闭环 + 系统自迭代闭环"两个飞轮。落地顺序按杠杆：
1. ✅ **工具注册层 + MCP**（地基，2026-06-18 已做）：`core/tools.py` 统一工具注册表（8 个工具 + `list_tools`/`call_tool` 调度 + 显式 user_id + 访问守卫）；`GET /api/tools` 透明列出；`mcp_server.py` 用标准库实现 spec 兼容的 MCP stdio 服务（JSON-RPC 2.0，env `ITUTOR_MCP_USER_EMAIL` 绑定用户）→ 可直接挂到 OpenClaw/Claude Desktop。官方 `mcp` SDK 需 py3.10+，记入 `pyproject [mcp]` 选装，升级后平滑替换。
2. ✅ **反馈采集 + 极简 LLM Tracing/评测台**（环 B 的眼睛，2026-06-18 已做）：`feedback` 表 + `core/feedback.py`（up/down/error 纠错 + 聚合）；讲解卡 👍👎/「这里有错」入库（`POST /api/instance/{id}/feedback`，亦作 `submit_feedback` 工具）；`llm_traces` 表 + `core/tracing.py`（每次生成型调用落 延迟/成败/是否 fallback，复用 usage contextvars 不改函数签名）；`generate_lesson`/`coach_reply`/`generate_graph` 已接入；管理后台 `GET /api/admin/quality` 质量面板（反馈好评率 + fallback 率 + 延迟 + 最近纠错）；`tests/test_golden.py` 解析回归（锁住 LLM 输出→结构契约）。
3. ✅ **讲解升级为 evaluator-optimizer 工作流**（2026-06-18 已做）：检索 → 起草（`generate_lesson`）→ 自检（`review_lesson` + `core/prompts/lesson_review.md`，查 事实/防幻觉/不泄题/难度/练习唯一解）→ 按需修订；`generate_lesson_reviewed` 编排，起草失败/关闭(`ITUTOR_LESSON_REVIEW=0`)直接退草稿，修订丢字段时回填草稿保稳；两步分别 trace `lesson` / `lesson_review`（质量面板可见）；前端讲解卡标「已自检」。真 LLM E2E 实测会真改稿（如给零基础补术语解释）。
4. ✅ **每日推荐编排器（动态工作流 · 环 A 决策，2026-06-18 已做）**：`core/recommend.py` `daily_plan` 按学情运行时路由——到期复习（抗遗忘最优先）→ 图谱前线新课 → 攻弱项，合成有优先级的今日计划 + 一句话 rationale；**确定性，不调 LLM**（能用规则别用模型）；无图谱时退本周主题兜底；`GET /api/instance/{id}/recommend` + `recommend_today` 工具；前端「今日聚焦」卡（复习/新课/弱项分色，点开即学）。
5. ✅ **个人记忆系统（D6，2026-06-18 已做）**：`user_memory` 表（全局/单空间 · 派生/手写）+ `core/memory.py`（`write_memory`/`recall`/`format_for_prompt`/`refresh_derived`/`delete_memory`）；**派生**：学练完成后从学情信号幂等 upsert 弱项/强项/节奏记忆并清理过时项；**手写**：用户加偏好/目标；召回注入**教练复盘**（`coach.md` 新增记忆段）；工具 `recall_memory`/`remember`；`GET/POST/DELETE /api/instance/{id}/memory`；学情页「关于你」面板（自动记忆 + 手写偏好 + 删除）。
6. ✅ **动态重规划（三触发器 · 环 A 收尾，2026-06-18 已做）**：`core/replan.py` `detect` 监测学习状态——**卡壳**（同点学 ≥2 次仍 cant / 客观<40% → 降速 + 从图谱补前置）、**超额**（多数独立且 ≥85%、无弱项无到期 → 加压/跳级）、**中断**（距上次 ≥7 天 → 低门槛重启）；**确定性规则不调 LLM**，每触发器给"有判断力的一句话 + 可执行 action（带 topic 可直接开课）"；`GET /api/instance/{id}/replan` + `check_replan` 工具（共 13 工具）；今日页顶部按 severity 分色横幅。
7. ✅ **后台任务运行器（APScheduler）**（2026-06-19 已做，见 V0.17 / ADR-012）：`AsyncIOScheduler` 单进程 env 守卫（`ITUTOR_SCHEDULER_RUN`）防多 worker 重复；每日定时跑推送（`core/push_scheduler.py`）；记忆巩固 / 异步评估 / 延迟回测后续复用同框架。
8. **prompt 版本化/AB**：环 B 的"改 prompt"收尾（待）。

**Batch 3（基建型，按用户规模再上）**
- 掌握度推进 + 三触发器动态重规划（模块 B）
- embedding 服务接入（用户提供 OpenAI 兼容 key 后开 dense）→ 向量库规模化（Postgres+pgvector）
- DKT 掌握度推断、路径模板市场（§4.3）
- 题目难度标定（IRT 式，从真实作答数据）、多 LLM provider 容灾路由、文件解析管线（PDF/视频→RAG）

### 9.4 明确不做（沿用方案 §六 + 本项目 V0 约束）
- ❌ 游戏化连胜 streak（方案明确反对，严肃学习有害）
- ❌ 路径模板市场 / 创作者经济 / 社群（规模型，远期）
- ❌ 移动端原生 App、多模态学习内容
- ❌ 接真实生图服务（封面用既有静态图 + SVG 兜底即可）

### 9.5 27 点优化清单对照（2026-06-18 补 · 来源：用户产品优化清单）

状态：✅ 已做 / 🟡 部分(Batch1) / 🔜 已排期 / ⏳ 远期。Batch 1 已覆盖 6/27。

| # | 优化点 | 状态 | 归批 |
|---|---|---|---|
| 1 | 知识图谱 + Concept 依赖 + 拓扑排序 + 疆域地图 | ✅ Batch 2A 已做 | — |
| 2 | 混合复习 30%新+70%复习(FSRS) | ✅ Batch 2C 已做（7:3 配比 + 阶段闸门约束新课；FSRS 调度待升级） | — |
| 3 | 规则版 BKT 掌握度概率 | ✅ Batch 2A 已做 | — |
| 4 | 苏格拉底引导 + 模式判断器(透明) | 🟡 prompt 级已做 | 补全 |
| 5 | Phase Gate 精熟门禁 | ✅ Batch 2C 已做（core/phases.py 按月聚合掌握率，≥70% 解锁下一阶段；约束推荐新课） | — |
| 6 | 每周教练对话(人格化) | ✅ 核心已做；人格选择/引用历史待补 | — |
| 7 | 学习产物强制(没产出不算完成) | 🟡 produce+归档已做；Phase 强制待补 | 补全 |
| 8 | 召回=重规划而非提醒 | ✅ Batch L 已做（replan.py 中断触发器→低门槛重启而非单纯提醒；推送触点待补） | — |
| 9 | 365 天学习贡献图 | 🔜 cheap win | **Batch 2B** |
| 10 | 同侪 Cohort | ⏳ | Batch 3 |
| 11 | Supertag 结构化笔记类型 | 🔜 | **Batch 2B** |
| 12 | 双向链接 + 知识星图 | ✅ Batch 2C 已做（路径页第 3 tab：自绘 SVG 分层 DAG + 缩放/拖拽/悬停双向高亮 + 掌握度环；双向链接=前置/解锁邻接） | — |
| 13 | Markdown/Obsidian 导出 | 🔜 cheap win | **Batch 2B** |
| 14 | 学习成果页(公开分享) | ⏳ 增长引擎 | Batch 3 |
| 15-18 | 路径配方市场/版本fork/口碑/创作者分成 | ⏳ 重基建 | Batch 3 |
| 19 | 三入口 Onboarding(上传素材→RAG) | 🟡 RAG 检索层已就绪(可粘贴文本入库)；上传 PDF/视频解析待做 | Batch 3 |
| 20 | 学习契约书 + 签署承诺 | 🟡 GENERATE 确认已有 | Batch 2C |
| 21 | 学习空间分 4 类型 | 🔜 | Batch 2C |
| 22 | 产出占比实时进度条 + 打断 | 🟡 produce 步骤已做 | 补全 |
| 23 | 内嵌代码沙箱 | ✅ Runno 已做；失败 AI 诊断待补 | — |
| 24 | 拍照/截图提问 | ⏳ 移动端 | Batch 3 |
| 25 | 学习行为画像 | 🔜（需埋时段数据） | Batch 2C |
| 26 | 反游戏化(无 streak/徽章/榜) | ✅ 已对齐 | — |
| 27 | XP=学习强度量化(非装饰) | 🔜 | Batch 2C |

**下一步建议**：Batch 2A + 真 RAG 已完成。从**落地/可用性视角**，下一步优先 **Batch L（Loop 化）**——它决定产品"成环可用"（不成环 = 用户学几次就流失）；其次才是 2B 快赢 / 2C 深化。

### 9.6 落地视角 · 整体可用性盘点（2026-06-18）

> 评判标准不是"功能多"，而是"一个真实用户能不能持续用下去"。

| 维度 | 现状 | 可用性卡点 |
|---|---|---|
| **能不能用起来**（首跑通）| ✅ 注册→对话→生成路径→学练→学情→资料，端到端跑通 | 服务仅本机；无引导/示例，冷启动空 |
| **能不能学下去**（单次体验）| ✅ 讲解+4-2-1+练习+代码 Lab+图谱+RAG 引用 | 讲解质量靠单次生成、未自检；无"今天学什么"的明确指引 |
| **会不会再回来**（留存闭环）| ❌ **最大短板** | 无每日推荐、无提醒触点、无跨任务记忆 → 学完即走，**环没闭** |
| **越用越好吗**（个性化迭代）| ❌ | 无记忆、无反馈采集 → 系统不会因你而变，也不会自我改进 |
| **内容可信吗**（防幻觉）| 🟡 标注+RAG citation 已做 | 无内容质量评测；KB 空时仍靠模型记忆 |
| **可运营/可迭代吗**（团队侧）| 🟡 有 usage/admin | 无 LLM Tracing/评测台 → 改 prompt 盲改，无回归 |

**结论**：可用性最大缺口在**"留存闭环"和"自迭代"**两格——正是 **Batch L** 要补的。功能广度够了，深度（成环）不够。

### ADR-008 · 知识图谱是路径的结构地基（#1 优先于 #2/#5/#12）

**Context**：当前 master.json 用"周主题 + key_outcomes 字符串"描述路径，Concept 是孤立的；清单 #2(混合复习)/#3(掌握度)/#5(Phase Gate)/#12(星图) 都依赖一个真实的 Concept 图谱。
**Decision**：把 Concept 升级为带 `prerequisites/unlocks/difficulty_level` 的图谱节点，路径生成在图谱上做**拓扑排序**而非让 LLM 线性排课；前沿之外锁定。
**Reason**：可靠、可解释、可被 #3 掌握度概率驱动；是后续多个优化点的公共前置，先做收敛最大。
**Consequence**：generator 输出 schema 扩展（concepts[] + 依赖边）；instance.html 路径页加"知识疆域地图"。先 V0 用规则拓扑排序，掌握度先用规则版 BKT。

### ADR-007 · 学习复盘做成"对话"而非"报告"

**Context**：方案把"每周 AI 教练复盘"列为 MVP 必做，且强调"是一段对话，不只是报告"。
**Decision**：教练复盘走 LLM 现场生成 + 可追问的轻量多轮，而非静态拼装的数据报告。
**Reason**：元认知洞察（"问题不在概念在符号阅读"）只有 LLM 结合学情数据才能给；可追问才形成"教练"体验，是与 ChatGPT 拉开差距的关键。
**Consequence**：新增 `coach` Agent（prompt + 端点），usage 归因 scene=`coach`；输入是 analytics_summary，不做额外重检索。

### ADR-009 · RAG 真做，但 embedding 可插拔（dense 可后挂）

**Context**：用户要求"技术方案真做，不 mock"，RAG 接真实的；但暂不提供 embedding key（"如需我后续提供真实 API"）。
**Decision**：检索层真实实现 Hybrid（纯 Python BM25 始终可用 + dense 余弦在配置 embedding key 后启用 + 分数融合）；embedding 客户端做成可插拔（env 配置 OpenAI 兼容代理），**未配 key 时真实降级为 BM25**，不是假数据。讲解生成接入检索，事实性陈述基于来源并标注 citation。
**Reason**：既满足"真做"，又不被外部 key 阻塞；BM25 本身就是真实词法检索，dense 是增量增强；降级路径可见、可验证。
**Consequence**：新增 `core/embeddings.py` / `core/rag.py`、`kb_sources`/`kb_chunks` 表、`/kb` 增删查 + `/kb/search` 端点；lesson prompt 加 `{context}` + `citations`。用户提供 `ITUTOR_EMBED_API_KEY` 后 dense 自动生效，无需改代码。向量库规模化（pgvector）留待 Batch 3。

### ADR-010 · OpenClaw（龙虾）集成策略：不 fork + 双层部署（讨论决策，未落地）

> 来源：2026-06-18 架构讨论。用户提出"iTutor 搭配 OpenClaw（开源自托管 AI Agent，🦞），用飞书 IM 打通移动端，并让 agent 做进阶学习推进玩法"。本 ADR 只记决策方向，**暂不实现**（"不急立马落地"）。

**Context**
- OpenClaw = 开源**单租户**自治个人 agent（开源版 Manus：能执行命令/浏览网页/管文件/操作飞书文档·多维表格·日历·任务），靠官方插件 `@larksuite/openclaw-lark` 经 **WebSocket 长连接**接飞书 IM（国内版零公网 IP）。
- 它的价值是"**能主动动手**"——早推计划、卡片测验、晚复盘、间隔重复 nudge，这是纯 web 的 iTutor 给不出的"主动找你"体验。
- 核心痛点（用户点名）：OpenClaw 单租户 → 每个用户要自跑实例 + 自备 LLM key + 自建飞书应用 + 改配置，**大众部署门槛极高**。

**Decision**
1. **不 fork 改源码**。iTutor 把能力暴露成 **MCP / 稳定 HTTP 工具**（`get_today_plan`/`generate_lesson`/`record_answer`/`get_analytics`/`get_graph`/`kb_search`/`coach_reply` 多数已是现成端点）+ 一段"iTutor 学习教练"人设 prompt，做成 OpenClaw 的 **skill 包**，挂在它的 plugin/skill/bindings 扩展点上。理由：OpenClaw 高速迭代，fork = 永久 rebase 地狱（踩 CLAUDE.md 失败模式）。
2. **双层定位（用户选 both）**：
   - **大众层**：iTutor 多租户 SaaS 核心（已建）+ web/PWA；移动触点 = **一个我们自己托管的中心化飞书机器人**（iTutor 服务器直连飞书 WebSocket，单应用，用户只需授权，**零部署**）。**这条路不经过 OpenClaw**，并升级取代早前"PWA+WebPush+Email"作为首选移动通道（飞书卡片做测验/任务、推送、多维表格记录都更顺）。
   - **Pro 层（opt-in）**：要数据自主 + agent 自由折腾的极客，**自托管 OpenClaw + 装 iTutor skill 包**，LLM 成本他们自担；进阶自治玩法都在这一层。
3. **绝不做"我们替每个用户托管一个 OpenClaw 实例"**：N 个能执行命令的进程的沙箱/隔离/限额 = 安全+成本+运维三杀，与其单租户设计对着干。

**Reason**：既拿到 agent 自治带来的"主动学习推进"差异化，又不被单租户部署门槛拖死大众增长；成本与安全风险按层隔离。

**Risks / 待定**
- 飞书本身是安装门槛（偏企业/效率党）→ 天然把 iTutor 往"开发者/效率向学习工具"推；若做大众消费级，飞书+OpenClaw 都不理想，PWA 仍是低门槛兜底。
- 给 agent 真实动作权限有幻觉/越权/prompt 注入风险（官方插件已警告）→ Pro 层工具要**收窄**（只读计划/发卡片/写进度，禁系统命令），建议只作私人助手别拉群。
- Lark 国际版无 WebSocket → 出海需 webhook + 公网 URL。
- 资源能否同时撑"中心化飞书机器人"+"Pro skill 包"两条线待评估。

**最小验证路径（先于任何落地）**：作者本人先把现有 iTutor 接自托管 OpenClaw + 飞书，dogfood 跑通"早推计划 / 卡片测验 / 晚复盘"，验证体验价值后再决定是否投入中心化飞书机器人。

**Status**：✅ 决策方向已定；⏳ 未实现。落地前置 = 上面的 dogfood 验证 + 定位/资源再确认。

### ADR-011 · 技术分层（确定性/工具/工作流/动态工作流/Agent）+ 自迭代闭环（Loop 化）

> 来源：2026-06-18 技术框架审视。用户问"是不是什么都用 agent，能否用 workflow / 动态 workflow / skill"，并要求"从落地视角看整体可用性"。本 ADR 定方向，分批落地。

**Context**
- 现状盘点：`core/` 里被叫作"agent"的（engine/content/coach/concepts）**其实都是"单次 LLM 调用 + 协议解析"**，不是自主循环 agent；mastery/learning/rag 排序/generator 大部分是**确定性代码**。即"几乎没有真 agent"。
- 两个真问题：(1) 缺**明确的技术分层与编排中枢**——orchestration 散在 server.py 端点里；(2) 缺让系统"转起来、越转越好"的**自迭代闭环**。

**Decision — 技术分层取舍（按可预测性递减）**
1. **确定性代码**（无 LLM）：调度/BKT/DAG/检索排序/统计 → 维持，能不用 LLM 就不用。
2. **Skill / Tool**：把 `generate_lesson`/`get_today_plan`/`kb_search`/`record_answer`/`get_graph`/`coach_reply` 等收敛成**统一工具注册层（MCP / 工具表）**——同时喂给动态工作流、未来 agent、OpenClaw（一鱼三吃，最高杠杆地基）。
3. **Workflow（固定编排）**：多数"agent"正名为 workflow step；高价值的升级为多步：
   - **讲解 = evaluator-optimizer**：`RAG 检索 → 起草 → 自检(难度/事实/是否泄题) → 修订`（质量 + 防幻觉立竿见影）。
   - **Onboarding = routing**：先判领域类型/基线/是否上传素材，再走分支。
4. **动态 Workflow（运行时在有限选项里选路）**：**每日学习编排器**——按掌握度/到期/行为信号在 `{新课, 复习, 测验, 教练}` 里决定"今天学什么"。这是 Loop 的中枢。
5. **真 Agent（自主循环 + 工具）**：只留 ① OpenClaw 学习陪伴（ADR-010 Pro 层）；② 未来"备课/建库 agent"（自主搜资料入 RAG）。**默认不轻易上真 agent**（不可控/贵/难验证，违背 CLAUDE.md 铁律）。

**Decision — 自迭代闭环（Loop 化）= 两个咬合的飞轮**
- **环 A · 用户学习闭环**：`学 → 主动产出 → 评估掌握度 → 决定下一步 → 沉淀记忆 →（更懂你地）推下一节`。~~现断点：决定下一步+记忆为空~~ → **2026-06-18 全闭合**：每日推荐编排器（`recommend.py`，决定下一步）+ 动态重规划（`replan.py`，三触发器调整路径）+ 个人记忆系统（`memory.py`，沉淀+召回注入教练/讲解）。剩"行动 Act"（移动推送触点）待补。
- **环 B · 系统自迭代闭环**：`采集反馈(答题/👍👎/regenerate) → 评测 LLM 输出 → 改 prompt/难度模型/图谱 → 更好`。现断点：**无反馈采集 + 无 LLM 评测台** → 补 **反馈采集 + LLM Tracing/评测台 + 题目难度标定**。

**自迭代缺口清单（6 段闭环打分）**：感知 🟡（缺显式反馈/保留率）｜记忆 ❌（D6 缺）｜评估反思 🟡（缺产出质量评测/难度标定）｜决策 ❌（缺每日推荐/重规划/Phase Gate）｜执行 ✅内容 / ❌推送 ｜系统自改进 ❌（缺评测台/Tracing/跨用户反哺）。

**Reason**：能用确定性别用 LLM、能用 workflow 别用 agent；先把"散点功能"接成环，产品才"可用"（不成环 = 用户学几次就流失）。工具注册层是后续一切（动态工作流/agent/OpenClaw）的公共地基。

**Consequence / 落地批次**：见 §9.3 新增 **Batch L（Loop 化）**；先文档与分层约定，不一次性重写代码。

---

### ADR-012 · 移动端推送触点双轨：PWA+Web Push 公共地基先行（轨道 A 落地，飞书 IM 留轨道 B）

> 来源：2026-06-19。环 A「行动 Act」断点（§9.6 留存闭环是最大短板）补齐。用户定双轨边界：不装 App 就 web 自闭环（PWA+Web Push）；要装移动端就做全套（飞书 IM，ADR-010）。两条共享地基。

**Context**
- 系统会算"今天学什么"（`recommend.py`）但不会主动找用户——学完即走，环 A 没闭合。
- 推送 = 内容源（recommend，已有）+ 调度（APScheduler）+ 通道（飞书/PWA）。通道选型：飞书门槛最高（用户装飞书+建应用+托管服务器），PWA 零安装门槛最低。

**Decision**
1. **通道无关 payload**（`core/notify.py`）：`daily_plan` → `{title,body,url,tag,data}`，换通道只换发送器；payload 确定性、不调 LLM。
2. **未配 VAPID 真实降级**（对齐 ADR-009）：`is_configured()=False` 时不发、不报错、返回 `skipped` 让 scheduler/admin 可感知；前端据 `/api/push/vapid-public` 的 `enabled:false` 隐藏订阅按钮（而非点了报错）。
3. **一用户一天一条**：`push_send_log(user_id,day)` UNIQUE 去重；最近活跃 instance 查 `events` 表（非 `_index.json`——其条目无 `user_id`，无法归属到用户）。
4. **APScheduler 单进程 env 守卫**：`AsyncIOScheduler` + `coalesce=True/max_instances=1/misfire_grace_time=3600` + 显式 `timezone="Asia/Shanghai"`；`ITUTOR_SCHEDULER_RUN=0` 让 web worker 不跑、独立进程跑 scheduler，防 `uvicorn --workers N` 重复执行（**最大坑**）。reload 模式 worker 只一个，startup 不会重复起 scheduler。
5. **轨道 B（飞书 IM）本次不做**：绑"是否托管服务器"独立决策；地基（notify + push_subs + 调度）就绪后，复用 `core/notify.py` 直接接 `core/push_lark.py` 即可。

**Reason**：先把留存闭环"成环"（不成环=用户流失），用最轻门槛（浏览器零安装、localhost secure context 可直测、VAPID 自管非第三方）验证"会不会再回来"这个最贵假设；飞书重基建等验证通过再投，不浪费。

**Consequence**
- 新增 `push_subs`/`push_send_log` 两表、4 个端点、`sw.js`/`manifest.json`、instance 页订阅 UI + deep-link；VAPID 自管（`scripts/gen_vapid.py`）。
- `localhost`/`127.0.0.1` 是 secure context 可直测 Web Push；跨域部署需 HTTPS + `ITUTOR_BASE_URL`（否则通知点击 deep-link 无效）；iOS 16.4+ 需"添加到主屏幕"+ manifest `standalone`。
- pywebpush 同步阻塞 → `asyncio.to_thread` 包；404/410 自动删失效订阅。
- `@app.on_event` 启动钩子沿用现状（不单独引入 lifespan 造成两套生命周期共存），未来统一迁 lifespan 另记。

**Status**：✅ 地基 + 轨道 A 落地（2026-06-19，V0.17，183 测试 + 端点 E2E）；⏳ 轨道 B 飞书 IM 待独立规划（依赖托管决策）。

### ADR-013 · 对外品牌定为 LearnBuddy，内部 itutor 命名暂不迁移

> 来源：2026-06-19 备案与域名决策。域名为 learnbuddy，备案网站名称选定为「LearnBuddy 学习伙伴」，首页产品名为 LearnBuddy。

**Context**
- 项目早期工程名为 Itutor / iTutor，代码、路由、数据目录、提示词和文档均有历史命名。
- 备案、域名和真实用户访问需要统一品牌，否则页面会出现“learnbuddy 域名 + Itutor 页面”的割裂感。
- 但内部全量重命名会牵动包名、`data/instances`、API 路由、MCP 工具、部署脚本和历史数据，当前收益低、风险高。

**Decision**
1. **对外用户可见层统一为 LearnBuddy 学习伙伴**：页面标题、登录页、学习空间、学习路径页、PWA manifest、通知兜底标题、FastAPI title、README 和架构总览统一使用 LearnBuddy。
2. **中文产品语义统一**：`学习系统` 在用户界面中改为 `学习路径` / `学习空间`；`AI 教练` 在主文案中改为 `学习伙伴` / `LearnBuddy 复盘`；`今日推荐` 改为更自然的 `今天一起学什么`。
3. **内部技术命名暂不迁移**：保留 `itutor` 包名、`/instance/{id}` 路由、`data/instances` 目录、数据库字段、工具名等，避免破坏兼容性。需要对外解释时写明“内部命名为历史兼容”。
4. **后续若要全量迁移**，必须单独开迁移版本：包含路由兼容层、数据迁移脚本、部署回滚方案和浏览器端 localStorage key 迁移。

**Reason**：品牌一致性是备案与公开访问的基础；内部大迁移不是当前瓶颈。先把用户体验和公开材料统一，保留工程稳定性。

**Consequence**
- 短期会存在“对外 LearnBuddy、内部 itutor/instance”的双命名状态，这是有意的兼容策略，不是遗漏。
- 后续 AI Coding Agent 不应为了“看起来干净”擅自全仓重命名。

**Status**：✅ 已落地用户可见层；⏳ 内部命名迁移暂缓。

### ADR-014 · 方案文档治理收口：03 为主文档，工作台为 HTML 镜像

> 来源：2026-06-19 文档治理整理。用户明确提出“多个计划文档应合到一起，明确当前整个需求方案、技术方案、开发进度和后续开发计划；文档应长期迭代，每次开发后都要完善；工作台与其他文档应统一管理”。

**Context**
- 当前 `docs/01-需求方案.md`、`docs/02-技术方案.md`、`docs/03-开发方案与计划.md`、`docs/04-进度管理.md`、`docs/工作台.html` 同时存在，原本有分工，但都包含一定程度的“当前状态”和“后续计划”描述。
- 项目进入 V0.19 后，真实状态已经从早期 `V0.5` 规划明显偏离，导致多个文档口径开始漂移，容易让用户和 AI Agent 误判当前优先级。
- 用户希望后续每轮开发结束都能沉淀到同一个长期维护的主文档里，而不是到处同步多份计划文本。

**Decision**
1. **`docs/03-开发方案与计划.md` 升级为唯一长期维护主文档**：统一维护当前需求方案、技术方案、开发进度、后续开发计划。
2. **`docs/工作台.html` 改为主文档的 HTML 镜像总览**：用于快速浏览“当前状态 / 当前重点 / 后续路线”，但不再独立维护一套与主文档竞争的叙述。
3. **`docs/01-需求方案.md`、`docs/02-技术方案.md` 保留为专题背景与展开说明**：可继续保留历史分析和深度内容，但若与主文档冲突，以主文档为准。
4. **`docs/04-进度管理.md` 收口为协作规则文档**：只保留看板规则、DoD、更新流程等协作机制，不再维护单独的项目现状与路线图。
5. **每轮开发结束后的文档更新顺序固定**：先更新 `03` 主文档，再同步 `工作台.html`；重大设计决策仍进入 `SPEC.md` ADR。

**Reason**
- 降低口径漂移与维护成本，让“当前真相源”只有一个。
- 保留专题文档和 HTML 总览的阅读价值，同时避免它们承担“当前真实状态”的主写职责。
- 更适合 AI Coding Agent 持续接手：先看 `AGENTS.md`，再看 `SPEC.md`，再看 `docs/03` 和 `工作台.html`，上下文最短。

**Consequence**
- 短期内仍会保留多份文档，但角色发生变化：`03` 主、`工作台` 镜像、`01/02` 专题、`04` 协作。
- 后续如果需求、技术、进度有重大变化，优先改 `03` 和 `工作台`，不再到多个计划类文档平行维护。
- 若未来发现 `01/02` 的专题内容也长期过时，再考虑继续收口，但当前先不做激进删除，避免丢失历史思考。

**Status**：✅ 已采纳并开始执行。

### ADR-015 · 需求方案页与技术方案页升级为图示化专题页

> 来源：2026-06-20 文档增强。用户明确反馈“需求方案和技术方案都不够详细，也没有任何架构图”，说明现有专题页虽然已挂出“架构图”标题，但仍停留在表格罗列层，无法承担真正的方案说明职责。

**Context**
- `docs/需求方案.html`、`docs/技术方案.html` 已承担专题页角色，但当前内容更像概要表格，缺少可直观看懂的系统图、链路图和模块边界说明。
- 对外讨论产品时，单靠术语和表格很难快速解释 LearnBuddy 的核心结构：用户入口、评估、路径、学习执行、推荐/重规划、账号/支付/权益、升级模式之间如何协同。
- 对 AI Coding Agent 来说，如果专题页没有图示化结构，后续很容易继续把“架构图”写成标题 + 表格，无法保持长期可读性。

**Decision**
1. **需求方案页必须包含 4 类内容**：需求分层图、用户旅程/闭环图、商业化与账号链路图、边界与非目标说明。
2. **技术方案页必须包含 4 类内容**：系统分层图、关键运行链路图、账号/支付/权益技术架构图、Agent/OpenClaw 接入边界图。
3. **“架构图”不再用纯表格代替**：允许使用 ASCII / 代码块图 / HTML 图示块，但必须让读者一眼看出“层次、流向、边界”。
4. **专题页与 Markdown 真相源同步增强**：`docs/01-需求方案.md`、`docs/02-技术方案.md` 也要补相同的结构化图和关键链路，避免 HTML 只有展示、Markdown 没有真相源。
5. **主方案页只做摘要与导航**：`docs/开发方案与计划.html` 继续作为主方案入口，但详细结构下沉到需求/技术专题页。

**Reason**
- 用户需要的是“能直接拿来看和讨论”的方案页，而不是只适合作者自己记忆的提纲。
- 图示化比长表格更适合表达系统层次、数据流和职责边界，也更适合持续迭代。
- 固化标准后，后续无论人工还是 AI 更新文档，都有一致的最低完成度要求。

**Consequence**
- `需求方案.html`、`技术方案.html` 的篇幅会更长，但阅读价值显著提升。
- `01/02` Markdown 专题文档需要同步维护，不再只保留历史背景。
- 后续若新增专题页，默认也应遵循“图示 + 链路 + 边界 + 非目标”的组织方式。

**Status**：✅ 已采纳，2026-06-20 起执行。

### ADR-016 · 积分权益、计划可编辑与知识来源关联必须增量落地

> 来源：2026-06-22。用户要求：学习计划可调整；重要知识点关联权威来源；注册默认送 1000 积分；管理员可赠送积分/生成积分卡；普通用户不能进入后台；任何更新不能影响线上已有用户数据。

**Decision**
1. **用户侧只展示积分，不展示 token**：token 继续作为后台成本核算与真实消耗流水；用户看到的是积分余额、已消耗积分、积分卡兑换。
2. **默认权益**：新注册用户默认赠送 1000 积分；历史用户首次查余额时幂等补齐同一笔注册送分。
3. **积分折算**：默认 `ITUTOR_TOKENS_PER_POINT=1000`，即 1000 积分约 100 万 token，覆盖一次路径生成和约两周学习；后续可用环境变量调整，不迁移历史数据。
4. **学习完成小额奖励**：完成一节学练默认奖励 20 积分；同一用户/空间/知识点/自然日只奖励一次；每日奖励默认上限 60 积分（约 3 节），防止刷积分；可用 `ITUTOR_STUDY_REWARD_POINTS` / `ITUTOR_STUDY_REWARD_DAILY_LIMIT` 调整。
5. **管理员后台隔离**：积分赠送、积分卡生成、用户使用情况只挂在 `/api/admin/*`，继续由 `require_admin` 后端校验；普通用户即使访问 `/admin` 也会被挡回学习空间，API 返回 403。
6. **线上数据保护**：只做 `CREATE TABLE IF NOT EXISTS` / `ALTER TABLE ADD COLUMN` 级增量迁移；部署包继续排除 `data/` 与 `.env`；每次生产部署前先备份 SQLite 与实例数据目录。
7. **计划编辑**：允许用户在学习路径页编辑周主题/关键产出，先保存到现有 `master.json`，不重建历史学习记录。
8. **知识来源**：Concept 支持 `sources[]`（标题、链接、类型、说明）；LLM 可建议来源，但不强制编造链接，用户可在图谱上维护权威来源。

**Consequence**
- 后台仍保留 token、成本、调用次数，方便运营核算；前台改用积分语言，降低用户理解成本。
- 已有线上用户、学习路径、RAG 资料、usage 流水不会被覆盖；新功能通过新增表和 JSON 字段自然兼容旧数据。

**Status**：🚧 本轮落地。

### ADR-019 · 路径生成必须进入 DeepBuild 工作流，而不是秒级套模板

> 来源：2026-06-27。用户反馈“学习大纲生成太快，感觉没有深度分析和构建”。排查发现旧链路主要把对话参数包里的 `weekly_themes` 写入 `master.json`，路径级外部来源、概念图谱和质量审查没有参与实例生成。

**Decision**
1. **实例生成阶段新增路径研究包**：生成 `master.json` 前先形成 `_outline_research`，记录检索 query、检索计划、歧义判断、来源数量、来源类型和教学假设；服务端真实对话生成默认启用联网来源搜索，离线/测试生成可关闭联网。
2. **概念图谱前置生成**：新路径生成时同步落 `concepts.json`；优先由教研 Agent 生成依赖图谱，模型或配置失败时落线性兜底，避免路径没有知识结构。
3. **路径质量契约前置**：`master.json` 写入 `_outline_quality`，从外部来源、周目标可衡量性、第一周每日产出、概念覆盖、基线个性化等维度打分，并留下问题列表。
4. **生成过程可追溯**：`master.json` 写入 `_generation_workflow`，记录“对话画像 → 路径研究 → 概念图谱 → 质量审查”四阶段状态，后续可在前端/后台展示。
5. **保持历史数据兼容**：新增字段均以下划线扩展挂在 `master.json`，旧 `north_star / weeks / months / W1.json` 不改结构；旧实例缺这些字段也必须继续可打开。

**Reason**
- LearnBuddy 的核心价值不是“很快生成一个大纲”，而是能用资料、画像、图谱和质量门槛构建更可信的学习路径。
- 路径级 DeepBuild 和 lesson 级内容质量检查职责不同：前者保证路线方向和结构，后者保证每天/每节课的教学内容。
- 生成慢一点但可解释，比秒级输出一个浅模板更符合产品定位。

**Consequence**
- 新实例生成时间会增加，尤其启用联网搜索和概念图谱时更明显；前端生成遮罩必须展示真实阶段，避免用户误以为卡住。
- 若外部搜索不可用，系统会明确标记 `pending_search/disabled/error`，不伪造来源，也不阻断路径生成。
- 后续可以把 `_outline_quality` 分数低的路径引导到“重新调研/补充目标/重建图谱”，形成路径生成的质量闭环。

**Status**：🚧 本轮落地。

### ADR-021 · 路径生成长任务必须展示真实阶段进度

> 来源：2026-06-27。用户反馈确认生成后只看到“学习伙伴思考中”，无法判断是在深度构建、卡死还是没有响应。

**Decision**
1. `/api/chat` 在触发路径生成时先发 `generate_start`，再持续发 `generate_progress`，最终发 `generate` 跳转。
2. 进度阶段来自后端真实工作流语义：目标画像、资料检索、能力图谱、计划生成、质量审查与保存；前端只渲染后端事件，不再播放固定假动画。
3. 生成期间前端展示遮罩、进度条、当前阶段说明和已用时间，降低等待焦虑；生成完成后再跳转到学习空间。
4. 生成任务仍在线程池执行，避免同步 LLM 调用阻塞事件循环；不改 `data/instances` 结构，不影响历史路径数据。

**Consequence**
- 用户能明确知道系统仍在工作，尤其 DeepBuild、联网搜索、多智能体/多阶段生成变慢后不会误以为卡死。
- 后续如果增加“搜索到哪些来源 / 正在审查哪些模块 / 质量分”等更细颗粒度状态，只需要扩展后端事件 payload。

**Status**：✅ 已落地。

### ADR-020 · 摸底评估必须校准 AI 协作能力，不能只出知识题

> 来源：2026-06-27。用户指出：如果自己擅长用 AI，普通知识题都可以让 AI 做出来，但这并不能评估真实水平和学习情况。

**Decision**
1. **摸底不是考“AI 能不能答对”**：基线评估必须区分用户的独立底座、AI 协作能力、验收纠错能力和真实落地风险。
2. **题目结构改为三层**：至少覆盖独立概念/判断底座、AI 协作任务拆解、AI 产物验收纠错；题量足够时再加迁移落地和学习策略。
3. **选项从知识答案改为能力档位**：优先使用“完全不会 → AI 代做但难验收 → 能协作完成部分 → 能独立判断并指挥 AI 落地”的梯度；知识判断题最多 1 道，只用于关键边界识别。
4. **前端必须回传完整题干**：摸底提交不能只截断题干，否则模型无法基于具体问题分析用户选择；回传内容包含题号、维度、完整题干和用户选择/补充。
5. **fallback 也要可评估**：LLM 出题失败时的兜底题不能退回空泛自评，必须保留独立底座、AI 协作、验收纠错三个维度。

**Reason**
- LearnBuddy 面向的是“学会并能落地”，而不是简单判断用户能否搜索答案。
- AI 时代的真实能力差异在于：能不能定义问题、拆解任务、提供上下文、判断输出质量、设计验证闭环。
- 如果摸底不评估验收能力，系统会高估“会让 AI 代做”的用户，生成过难或不匹配的路径。

**Consequence**
- 摸底题看起来会更像真实场景选择和能力画像，而不是传统考试题。
- 后续基线总结必须明确写出：独立底座、AI 协作、验收纠错、落地风险。
- 不同领域的题目仍保持领域适配，但 AI 相关方向尤其不能退回纯 HTML/CSS/JS 或术语定义题。

**Status**：🚧 本轮落地。

### ADR-018 · 学习空间与路径页首屏改为行动优先

> 来源：2026-06-22。用户反馈：页面布局重点不清晰、无效元素过多、卡片偏大、首屏有效信息不足，且学习任务不应无限创建。

**Decision**
1. **每个用户最多 5 个在建学习任务**：`draft / active / paused` 都算在建；达到上限后，生成新路径与恢复已完成路径都由后端拒绝，前端只做提示。
2. **学习空间不再强推“继续学习”**：用户从下方学习路径卡片自主选择入口；顶部只保留新建、积分、账号等轻量全局操作。
3. **首屏行动优先**：学习空间先渲染路径列表，跨路径统计与积分详情异步补齐；路径页先显示“当前最该推进 / 现在可以学”，再展示统计、阶段目标和解释性内容。
4. **降低视觉噪音**：积分大卡、欢迎大横幅、重复 CTA、超大封面和大面积统计卡都降权或压缩；卡片密度更接近工作台。
5. **慢资源按需加载**：代码运行组件只在进入代码实验室时加载，避免每次打开路径页都阻塞首屏。
6. **用户语言优先**：路径页不再直接暴露“前线/闸门/疆域/未探索”等内部算法词；统一换成“现在可以学 / 阶段目标 / 后面再学 / 已掌握”等学习者能立即理解的表述。
7. **路径首页接管学习入口**：独立“今日”页降级为学习首页中的下一步建议；默认入口回答“我在哪一周、现在学哪一节、后续计划是什么”。
8. **学情从统计页改为诊断页**：默认展示“诊断结论 → 能力画像 → 下一步决策 → 证据”，借鉴旧 `AI技术学习空间` 的能力画像思路；双坐标、热力图、系统记忆是底层证据，不抢首屏。
9. **路径档案前置**：资料页默认打开“路径档案”（原生成档案），再进入错题本、要点卡、我的产出和知识库。
10. **学前诊断是路径质量入口**：路径生成前后的信息收集必须结构化覆盖目标、基础、时间、偏好、约束和基线题；学习后的学情分析负责更新画像，而不是替代学前诊断。
11. **周计划不是学习任务列表**：学习计划页按阶段/周展开，周只负责查看本周目标与日程；具体学习入口保留在首页当前推荐内容，或当前周展开后的明确按钮，避免 8 周被误解成 8 个学习任务。
12. **实例数据向后兼容优先**：新 UI 必须兼容既有 `master.json` + `W1.json` 数据；新增 `week_plans` 只收集真实存在的 `W*.json`，不得用模板伪造未来周日程，也不得要求历史实例迁移后才能打开。
13. **周/天是学习进度单元，不是自然日期**：首页只回答“下一步学哪一天”；计划页展示全部周目录，未解锁周也要给 D1-D7 的每日路径预览，但不能进入学习；只有前序周完成且真实存在 `W*.json` 的当前周才开放入口，并且只给下一个未完成日主按钮，已完成日允许回看。用户断更不会按日期自动跳课。
14. **学情页是课程能力分析，不是流水统计**：首屏必须面向当前学习路径，展示雷达图式能力画像、下一步决策和学习进步轨迹；统计证据、学前档案和教练复盘作为解释层放在后面。

**Consequence**
- 用户第一屏能更快看到“我现在可以点哪里”，而不是先读说明和看装饰。
- 上限规则保护用户学习负荷，也避免无限生成路径带来的运营成本与空间混乱。
- 后续页面设计默认遵守“行动入口 > 结构化诊断 > 当前状态 > 解释信息 > 运营信息”的优先级。
- “今日”仍可通过旧 deep link 兼容到学习首页，避免历史推送或链接失效；资料与学情只调整展示层，不迁移已有用户数据。

**Status**：🚧 本轮落地。

### ADR-017 · 对话留痕与每日运营分析进入管理员后台

> 来源：2026-06-22。用户希望后续基于用户过程中的对话记录做分析，并每天自动发现问题和使用痛点，呈现在后台。

**Decision**
1. **新增对话事实表**：`conversation_messages` 只追加记录 `user_id/session_id/instance_id/scene/role/content/created_at`，用于后续分析，不替代运行时内存会话。
2. **记录范围**：主对话 `chat` 与复盘 `coach` 先落库；lesson 讲解正文不作为用户对话记录，继续由 lesson cache / tracing / usage 承担。
3. **每日分析快照**：`daily_analysis_reports` 保存每天分析结果，来源包括对话关键词、负向反馈、LLM tracing、积分耗尽、积分卡兑换等事实表。
4. **先确定性后 LLM**：第一版用规则识别痛点（收费替代、不会太难、计划调整、来源可信度、登录账号、积分额度、生成失败），避免早期把分析黑盒化；后续可把样例交给 LLM 做自然语言归纳。
5. **后台可见**：每日分析只挂 `/api/admin/analysis*` 与 `/admin` 面板，继续由 `require_admin` 保护；普通用户不可访问。
6. **调度边界**：默认每天凌晨生成一次；后台可手动刷新某天报告。调度器受 `ITUTOR_ANALYSIS_ENABLED` 与 `ITUTOR_SCHEDULER_RUN` 控制，避免多 worker 重复执行。

**Consequence**
- 后续可以做用户痛点、转化漏斗、卡点主题和 prompt 改进分析。
- 数据量会增长，但先按纯文本事实表保存，后续需要再加脱敏、保留期和导出能力。

**Status**：🚧 本轮落地。
