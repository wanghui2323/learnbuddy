# LearnBuddy 学习伙伴 · AGENTS.md（原 Itutor 学习系统）

> 本文件由 Codex / Cursor / 任何 AI Coding Agent 在每次会话开始自动读取。
> 是项目的"宪法级"持久内存——所有跨会话的关键决策都在这里。
> 维护规则：每次重大版本升级必须同步刷新本文件。每次刷新前问"删掉这条会让 AI 犯错吗？"不会就删。

---

## 🎯 项目核心目标（不要篡改）

- **元目标**：把作者本人在“AI 技术学习空间” 4 个月 dogfood 验证过的"个性化学习路径生成系统"抽象为**可复用的核心引擎**，做成面向所有人的通用学习平台。
- **不限领域**：英语 / 日语 / 编程 / 营养学 / 写作 / 考研 / 职业转型……任何能用自然语言描述的学习需求都行。
- **当前阶段**：V0 = 1 周端到端 demo（Day 0–5）。详见 [`SPEC.md`](./SPEC.md)。
- **V0 演示场景**：日语 N3 备考 3 个月（跳出 AI/技术圈，验证通用性）。

---

## 📚 5 大核心机制（产品哲学，不可砍）

| # | 机制 | V0 状态 |
|---|---|---|
| 1 | **对话引擎**（8–15 轮 + GENERATE 标记触发）| ✅ 必做 |
| 2 | **即时基线评估**（LLM 现场出题 + 双坐标系：独立 vs AI 协作）| ✅ 必做（**核心差异点，砍了就和普通 ChatGPT 没区别**）|
| 3 | **个性化生成器**（参数包 → master/W1/学习手册/契约）| ✅ 必做 |
| 4 | **反遗忘 5 机制**（micro-review / 间隔重复 / 周回测 / 月度雪球 / 解释关延迟）| ⚙️ 模板预埋 |
| 5 | **6 关自检 + R1–R10 调整规则** | ⚙️ 模板预埋 |

---

## 🗂 项目目录约定

| 路径 | 用途 |
|---|---|
| `core/` | 可复用核心引擎包（pip install -e .） |
| `core/prompts/*.md` | 所有 LLM prompt 模板（中文 markdown 单独文件，便于版本管理） |
| `web/index.html` | 对话首页（一个对话框） |
| `web/instance.html` | 学习路径展示页 |
| `server.py` | FastAPI 服务（路由 + SSE） |
| `data/instances/{slug}/` | 生成的学习路径实例（内部历史目录名保留） |
| `tests/` | pytest 单测 |
| `scripts/smoke_test.py` | LLM 连通性自测 |

---

## 🛠 工程铁律（全部必守）

### Git

- 任何 ≥ 5 行代码改动前先 `git commit` 当前状态
- Commit message 强制中文 Conventional Commits + 30 字目的
  - 示例：`feat(core): 加 schemas 数据模型 + DeepSeek client 流式封装`
- `.gitignore` 必须排除：`.env*` / `**/secrets/` / `__pycache__/` / `data/instances/*`（保留 _index.json）

### 密钥安全

- API key 严禁出现在代码 / 注释 / 提交历史
- 用 `os.getenv("DEEPSEEK_API_KEY")` + `.env` 加载（python-dotenv）
- `.env.example` 只占位

### 验证（Anthropic 官方最高杠杆）

- `core/` 改动必须 pytest 通过
- web 改动必须浏览器实测一次
- LLM 输出必须有"看得到的回退路径"（GENERATE 解析失败要有错误提示，不能静默）

### 文档协议

- **不造新散落 markdown**：除非用户明确要求或绝对必要
- **优先编辑现有文档**而非创建新文件
- 任何重大决策记到 [`SPEC.md`](./SPEC.md) 第 5 节 ADR
- 5 天计划任何调整记到 [`SPEC.md`](./SPEC.md) 第 4 节

---

## 🤖 AI Coding 协作铁律

### 1. 4 阶段工作流（任何 ≥ 3 文件修改强制）

**Explore（读现有代码）→ Plan（写到 SPEC.md ADR）→ Implement（含验证）→ Commit**

### 2. 给 AI 验证机制（最高杠杆）

- 任何"看起来对"的 AI 输出必须有验证：测试 / 浏览器实测 / 期望输出
- 没法验证 = 不能合
- 每个 PR 必须自带验证脚本或测试

### 3. 5 大失败模式（立刻 STOP）

- ❌ Kitchen sink session（一会话多任务 → 上下文污染）→ `/clear` 后重起
- ❌ 反复纠错（同一问题改 2 次还错）→ `/clear` 后写更具体的 prompt
- ❌ AGENTS.md 过长（本文件超过 200 行）→ 砍 + 转 SPEC
- ❌ Trust-then-verify gap（看似对但没验证）→ 加 verification 再合
- ❌ 无限探索（没 scope 让 AI investigate）→ 用 Plan Mode 收敛

### 4. 用户写作风格（中文优先 / 简短判断 / 第二人称）

- AI 教练 prompt 用第二人称（"你今天的核心是 X"）
- 简短有判断力，不要列长 bullet points
- 中文为主、关键术语保留英文

---

## 🌐 LLM Provider（V0 = DeepSeek 直连）

| 用途 | model | base_url | 备注 |
|---|---|---|---|
| 主对话 + 生成 | `deepseek-chat` | `https://api.deepseek.com` | V3 模型，中文好、价格低 |
| 推理重活（评估题校验，可选）| `deepseek-reasoner` | 同上 | R1 模型，慢但更准 |

**关键判断**：MVP 单 provider，OpenAI 兼容 SDK，未来切换只改 base_url + model 名。

---

## ⚠️ 当前阶段约束（迭代增强期 · V0.53 待人工复核与发布）

> ⛔ 历史提示：Day 0–5 的"不要做注册登录/数据库/进度追踪"**已作废**——这些早已建成，别再当约束。

- **已建成、不要重做**：注册登录 + SQLite 多用户（`core/auth.py` `core/db.py`）/ token 记账 + 管理后台 / 知识图谱 + 规则版掌握度（`concepts.py` `mastery.py`）/ 真 RAG（`rag.py`）/ Loop 化闭环（记忆 `memory.py`、每日推荐 `recommend.py`、动态重规划 `replan.py`）/ 阶段闸门 `phases.py` + SM-2 调度 `scheduler.py` / 工具注册表 + MCP（`tools.py` `mcp_server.py`）。
- **当前在做**：线上仍是 V0.52；V0.53“真实能力诊断与可追溯能力画像”已完成本地开发与工程回归，待开放题双人人工档位复核、生产备份、部署与线上回归。V0.53 已修复“原始答案未客观判分、自评分直接生成画像”的 P0。
- **仍暂不做（V0→V1 边界）**：运行时接入真实生图服务（用户明确暂缓）；移动端推送基建未上。
- **绝对不能砍**：即时基线评估（核心差异点）。

---

## 📌 当前会话快查（每次接手先看这里）

- **当前版本**：线上 V0.52 已部署；本地 V0.53 + 首个开源候选版 `v0.53.0-rc.1` 已完成工程回归（454 tests + 18 条真实 DeepSeek 开放题 + Cloud/Personal 桌面与 390px 浏览器流程），源码已公开到 [`wanghui2323/learnbuddy`](https://github.com/wanghui2323/learnbuddy)。Release Loop 仍准确阻断在 36 个双人人工评分单元，待评分、生产备份、部署与线上回归。不得把 V0.53 描述为已上线，也不得把未实测的 Docker/OpenClaw/飞书链路描述为“开箱即用”。
- **对外品牌**：备案网站名称 = `LearnBuddy 学习伙伴`；首页产品名 = `LearnBuddy`。用户可见层用"学习伙伴 / 学习路径 / 学习空间"，内部 `itutor` / `/instance` / `data/instances` 命名暂不迁移（见 `SPEC.md` ADR-013）。
- **🔑 真相源（新会话务必先读，胜过本文件细节）**：① `SPEC.md` ADR-055（V0.53 需求合同）+ ADR-051～054（已上线事实）；② `docs/LearnBuddy项目工作台.html` 的 V0.53 任务树；③ ADR-050 与 V0.52 验收证据；④ `docs/架构总览.html`。
- **⚠️ 运行目录**：始终在当前 Git 仓库根目录运行（默认端口 8000）。维护者 iCloud 中搬家前的旧副本已停用，不得作为代码或版本真相源。
- **下一里程碑**：完成干净机 Docker、真实 OpenClaw/飞书往返，以及 V0.53 开放题双人人工复核、生产备份、部署和线上回归。之后才启动 V0.54 深度教学，不得把 V0.50.1–V0.53 已完成任务重新开发。
- **🔧 内容生成开关（env）**：`ITUTOR_LESSON_PIPELINE`（默认开）/ `ITUTOR_LESSON_REASONER`（默认开）/ `ITUTOR_LESSON_REVIEW`（锐度评审）/ `ITUTOR_LESSON_QUALITY_LOOP`（默认开）/ `ITUTOR_LESSON_MAX_REVISIONS`（默认 3，最高 5）/ `ITUTOR_LESSON_TIMEOUT`（首请求默认 45s 转后台）/ `ITUTOR_LESSON_HARD_TIMEOUT`（默认 300s）/ `ITUTOR_SOURCE_MIN_AUTHORITY`（默认 0.75）。

---

> 本文件 ~150 行。如果超过 250 行必须砍。
> 任何条目删除前问："删掉会让 AI 犯错吗？"不会就删。
> 任何条目添加前问："不加 AI 会一定犯这个错吗？"不会就别加。
