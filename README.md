# LearnBuddy 学习伙伴

> 一个对话框，10–30 分钟对话，给你一条量身定做的 **4–24 周** 个性化学习路径。
> 不限领域：英语、日语、编程、营养学、写作、考研、职业转型……都行。

**当前状态**：线上 V0.52；本地 V0.53 已完成工程回归，仍待人工评分、生产备份、部署与线上回归。不应把 V0.53 描述为已上线。
**开源状态**：`v0.53.0-rc.1` 是首个 MIT 公开源码候选版，已完成本地工程回归；干净机 Docker 安装、真实 OpenClaw Gateway 加载和飞书消息往返仍是独立发布门，暂不宣称“开箱即用”。
**技术栈**：Python 3.11+ / FastAPI / SQLite / pydantic v2 / OpenAI-compatible LLM / 原生 HTML+CSS+JS / SSE / PWA

---

## 一句话定位

**公众号配套文章（2026-09-07 同步）**：[完整最终稿](./docs/AIBuilder第一周.md) · [Agent 三层图](./docs/assets/learning-map/agent-three-layer-aigc.png) · [分类资料与源码映射](./docs/README.md)。已有文章引用地址继续有效。

**想学习这个项目如何构建？** 从 [开源学习地图](./docs/README.md) 开始：产品判断 → 需求与设计 → 前后端 → Agent → 开发工作台 → 验收。仓库同时保留可视化页面、真实页面截图、源码和演进依据；HTML 预览方法也在地图中。

不是 Coursera（不是课程平台）。不是 Notion 模板（不是表单）。不是多邻国（不是单领域工具）。

**LearnBuddy 是你的个性化 AI 学习伙伴：生成专属学习路径，并陪你复习、练习和调整节奏。**

用户进来 = 一个对话框。说说你想学什么 → LearnBuddy 即时出 3-5 题做基线评估 → 生成 4-24 周学习路径 → 每天告诉你该学什么、该复习什么，学完后根据表现调整节奏。

## 一份代码，两种运行方式

| 模式 | 适合谁 | LLM 和数据 | 飞书 / OpenClaw |
|---|---|---|---|
| `cloud` | 想先在公有云体验完整产品逻辑的用户 | 平台模型，有 token/额度和限频，多用户隔离 | 不托管用户飞书，不运行共享 OpenClaw |
| `personal` | 要长期使用、自备 Key、数据自主的个人 | OpenAI-compatible BYOK，本地 SQLite/JSON，唯一 owner | 可选独立 OpenClaw 容器 + 用户自有飞书应用 |

版本切换的唯一入口是 `LEARNBUDDY_EDITION=cloud|personal`。后端是权限与额度的真实守门人，前端只根据后端 capabilities 展示可用功能。

---

## 来源

这套系统的 5 个核心机制（对话引擎 / 个性化生成 / 反遗忘 5 机制 / 6 关自检 / 学习画像）已经在创建者本人 4 个月的“AI 技术学习空间”实践里 dogfood 验证过。原始个人空间不属于本公开仓库，本项目只保留从中抽象出的通用机制。

本项目是把那套系统**抽象为可复用的核心引擎**，从 0 重写一个生产级、领域无关的版本。对外品牌为 **LearnBuddy 学习伙伴**；内部包名与历史文档中仍会保留部分 `itutor` 命名，用于兼容既有数据、路由和部署脚本。

---

## 5 大核心机制

| 机制 | 作用 | V0 状态 |
|---|---|---|
| **1. 对话引擎** | 8–15 轮自然对话，覆盖暖场/目标/基线/时间/路径/微调/确认 7 阶段 | ✅ 已实装（`core/engine.py` + SSE 流式）|
| **2. 即时基线评估** | LLM 现场生成 3–5 题领域适配测试，双坐标系（独立 vs AI 协作）| ✅ 已实装（`core/evaluator.py`，6.2s 出 4 题）|
| **3. 个性化生成器** | 参数包 → master.json + W1.json + 学习手册.md + 愿景与契约.md | ✅ 已实装（`core/generator.py`，5.5s 出 5 件套）|
| **4. 反遗忘 5 机制** | micro-review / 间隔重复 / 周回测 / 月度雪球 / 解释关延迟 | ✅ 已工程化（SM-2/FSRS 调度 + 每日推荐 + 阶段闸门）|
| **5. 6 关自检 + R1–R10 调整规则** | 周自检、掌握度更新、卡壳/超额/中断触发的动态重规划 | ✅ 已工程化（`self_check.py` + `mastery.py` + `replan.py`）|

当前版本已将 5 大机制接入端到端学习闭环；V0.53 进一步增加了真实能力诊断与可追溯能力画像，但仍需完成发布门后才能对外宣称上线。详见 [`SPEC.md`](./SPEC.md)。

---

## Personal 快速启动（Docker Compose）

```bash
git clone https://github.com/wanghui2323/learnbuddy.git && cd learnbuddy
cp .env.example .env
# 编辑 .env，填 LEARNBUDDY_LLM_API_KEY / BASE_URL / MODEL
bash scripts/setup_personal.sh
```

Compose 默认只启动 LearnBuddy，端口只绑定 `127.0.0.1`。创建唯一 owner 后，如需飞书学习助手，再填入你自己的飞书 App ID/Secret 并运行：

```bash
bash scripts/setup_openclaw.sh
```

完整的 30 分钟安装、飞书应用、安全、备份和升级步骤见 [`docs/PERSONAL_DEPLOYMENT.md`](./docs/PERSONAL_DEPLOYMENT.md)。

## Cloud 管理员初始化

Cloud 的公开注册永远只创建普通用户，登录也不会因为邮箱出现在环境变量里自动晋升。先用注册页创建并确认你能登录目标账号，再在部署机本地执行：

```bash
# 源码部署
.venv/bin/python scripts/init_cloud_admin.py owner@example.com

# Docker Compose 部署
docker compose exec learnbuddy python scripts/init_cloud_admin.py owner@example.com
```

脚本会交互读取该账号当前密码，不接受命令行密码；密码不匹配、账号不存在或运行在 `personal` 模式都会拒绝。首次执行授予管理员权限，再次对同一账号执行保持幂等。历史数据库中已经是管理员的账号不受影响；旧的 `ITUTOR_ADMIN_EMAILS` 邮箱白名单不再授予权限。

## 本地开发启动

```bash
# 1. 克隆 + 进项目根
git clone https://github.com/wanghui2323/learnbuddy.git && cd learnbuddy

# 2. 配 LLM key（默认示例为 DeepSeek）
cp .env.example .env
# 编辑 LEARNBUDDY_LLM_API_KEY / BASE_URL / MODEL

# 3. 创建环境、安装依赖、启动
uv venv --python 3.11
uv pip install -r requirements.txt
.venv/bin/python server.py
```

启动后打开 [http://127.0.0.1:8000](http://127.0.0.1:8000)，开始对话。

### 不想用一键脚本？

```bash
# 用 uv 创建环境
uv venv --python 3.11
uv pip install -r requirements.txt

# 1. smoke test：验证 LLM 联调
.venv/bin/python scripts/smoke_test.py

# 2. evaluator demo：看 LLM 现场出题能力
.venv/bin/python scripts/eval_demo.py "日语 N3 备考" "N5 水平" 4

# 3. generator demo：一键生成完整学习路径档案
.venv/bin/python scripts/generate_demo.py
# → data/instances/japanese-n3/ 会出现 meta+master+W1+学习手册+愿景契约

# 4. CLI 对话 demo（终端版，含 :q :r :state 命令）
.venv/bin/python scripts/chat_demo.py

# 5. Web 服务
.venv/bin/python server.py
# 浏览器打开 http://127.0.0.1:8000
```

### 跑测试

```bash
.venv/bin/pytest tests/ -v
```

---

## 项目结构

```text
learnbuddy/
├── core/                          # ← 可复用核心引擎包（关键产物）
│   ├── __init__.py
│   ├── schemas.py                 # pydantic 数据模型（参数包 / 实例 / 5 件套）
│   ├── llm_client.py              # OpenAI-compatible 客户端（流式 + 同步）
│   ├── engine.py                  # 对话引擎（system prompt + GENERATE 解析 + slug）
│   ├── evaluator.py               # 即时基线评估（LLM 生成评估题 + fallback）
│   ├── generator.py               # 5 件套生成（master/W1/学习手册/契约）
│   └── prompts/                   # 中文 markdown prompt 模板
│       ├── system.md              #   - 7 阶段对话 system prompt
│       ├── evaluator.md           #   - 评估题生成 prompt
│       └── learning_manual.md     #   - 学习手册领域适配 prompt
├── web/
│   ├── index.html                 # 对话首页（流式 SSE + GENERATE 跳转）
│   ├── instance.html              # 路径展示页（hero + 月度时间线 + W1 + tab）
│   └── assets/style.css           # 紫色+暖橙主题 + 8px 网格 + markdown 样式
├── server.py                      # FastAPI + SSE + 认证/学习/Agent API
├── data/instances/                # 生成的实例（{slug}/ 子目录，被 .gitignore 排除）
│   └── _index.json                #   实例索引
├── scripts/
│   ├── start.sh                   # 本地开发启动
│   ├── setup_personal.sh          # Personal Compose 初始化
│   ├── setup_openclaw.sh          # 可选 OpenClaw + 飞书初始化
│   ├── init_cloud_admin.py       # Cloud 本机管理员初始化
│   ├── init_personal_owner.py    # Personal 唯一 owner 交互初始化
│   ├── backup_personal.sh         # 一致性命名卷备份
│   ├── restore_personal.sh        # 显式确认后恢复
│   ├── verify_personal_backup.py # 校验和/归档安全预检
│   ├── smoke_test.py              # LLM 同步/流式连通性
│   ├── eval_demo.py               # 评估器 CLI（参数：domain baseline num）
│   ├── chat_demo.py               # 终端流式对话（含 :q :r :state）
│   └── generate_demo.py           # 一键生成日语 N3 12 周示例
├── tests/                         # 单元、回归与专项合同测试
├── docs/
│   ├── PERSONAL_DEPLOYMENT.md      # 个人版 30 分钟部署/备份/升级
│   └── V1_BACKLOG.md              # V0→V1 路线图
├── Dockerfile                     # LearnBuddy Python 容器
├── docker-compose.yml             # 默认 LearnBuddy，可选 agent profile
├── pyproject.toml                 # 让 core 可被 pip install -e
├── requirements.txt
├── .env.example
├── .gitignore
├── SPEC.md                        # 工程蓝图、版本计划与 ADR
├── AGENTS.md                      # AI Coding 协作宪法
├── CLAUDE.md                      # Claude Code 兼容入口
├── CHANGELOG.md                   # 版本变更记录
└── README.md                      # 本文件
```

---

## 技术栈

| 层 | 选型 | 理由 |
|---|---|---|
| 后端 | Python 3.11+ / FastAPI / pydantic v2 | 异步 + SSE 流式天然支持 |
| LLM | OpenAI-compatible BYOK（默认示例 DeepSeek） | Personal 自备 Key；Cloud 由服务端统一配置与限额 |
| 前端 | 原生 HTML + CSS + JS | 轻量、可直接部署、PWA 地基简单 |
| 持久化 | SQLite + 本地 JSON 档案 | 用户/行为/学情入 SQLite，生成档案保留 JSON/Markdown 可读性 |
| 推送 | PWA + Web Push + APScheduler + 可选飞书 | Personal 可用自有飞书应用承接主动提醒 |

---

## 近期迭代计划

最新真相源见 [`SPEC.md`](./SPEC.md) §8-§10。当前优先级：

| 优先级 | 项 | 描述 |
|---|---|---|
| P0 | V0.53 发布门 | 完成开放题双人复核、生产备份、部署与线上回归 |
| P0 | Cloud / Personal 守门 | 后端 capabilities、唯一 owner、BYOK、额度和集成边界全部可测 |
| P0 | Personal 干净机验收 | 30 分钟安装、重启持久化、备份/恢复和错误诊断 |
| P0 | OpenClaw / 飞书真实联调 | owner 私聊、绑定、工具写回和单次定时提醒 |
| P1 | Cloud 体验闭环 | 有限摸底/路径/一节学习，额度可见，可导出或转 Personal |

---

## 与原 `AI技术学习空间` 的关系

- **架构**：本项目实现 `core/` 抽象引擎；原项目作为 dogfood 第 1 实例继续运行
- **代码**：完全独立从 0 写，不复制原 `itutor_engine.py` / `itutor_generator.py`，只复用产品哲学
- **后续**：`core/` 稳定后，原项目可以通过本仓的可编辑安装方式反向复用引擎，但不强制

---

## 相关文档

- [`SPEC.md`](./SPEC.md) — 工程蓝图、版本计划与架构决策记录
- [`AGENTS.md`](./AGENTS.md) — AI Coding 协作宪法与当前事实边界
- [`CLAUDE.md`](./CLAUDE.md) — Claude Code 兼容入口，指向 AGENTS
- [`CHANGELOG.md`](./CHANGELOG.md) — 版本变更记录
- [`docs/PERSONAL_DEPLOYMENT.md`](./docs/PERSONAL_DEPLOYMENT.md) — Personal 部署、OpenClaw/飞书、备份与升级
- [`docs/V1_BACKLOG.md`](./docs/V1_BACKLOG.md) — V0→V1 完整路线图

---

## License

本项目使用 [MIT License](./LICENSE)。
