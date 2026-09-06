# OpenClaw 接入方案（LearnBuddy 对照版）

> **历史方案提示（2026-09-02）**：本文记录早期“大众 SaaS + Pro 升级”设计，其中“中心化飞书机器人 / 共享托管 OpenClaw”不再是当前产品方向。现行口径是 Cloud 仅提供限额 Web 体验，Personal 由每位用户自行部署 OpenClaw 与飞书；请以 [`07-开源与公有云双版本架构.md`](./07-开源与公有云双版本架构.md) 和 SPEC ADR-058 为准。本文暂留作迁移背景，不作为实现验收依据。

> 本文件是 `02-技术方案.md` / `03-开发方案与计划.md` / `docs/Harness技术体系对齐.html` 之外的**专题方案页**，
> 把 ADR-010 “OpenClaw 集成策略：不 fork + 双层部署” 落到可执行的接入清单、最小验证路径和排期。
>
> 对应 SPEC ADR：**ADR-010（不 fork + 双层部署）+ ADR-017（OpenClaw 与个人助理模式的双层关系图）**
>
> 对应可视化：[`OpenClaw接入方案.html`](./OpenClaw接入方案.html)

---

## 1. 一句话定位

> **OpenClaw = 个人助理升级模式（Pro）的运行时和承载层，不替代 LearnBuddy。**

OpenClaw 不替代 Web / PWA、不改学习真相源、不动账本；它负责“主动找你 / 主动执行 / 跨端连续”，
让“个人助理升级模式”这个用户自选模式有真正能跑的外部 runtime。

---

## 2. 为什么需要 OpenClaw

- Web / PWA 能“学得深”，但不会主动找用户
- 主动提醒、晚复盘、跨设备交接 → 需要 agent 级别循环
- 用户如果只为“多一个 IM 入口”就用不到 OpenClaw；如果要“主动学习推进 + agent 自治玩法”，就需要 OpenClaw

---

## 3. 双层定位（沿用 ADR-010）

| 层 | 用户 | 形态 | 是否经过 OpenClaw | 部署门槛 | 核心价值 |
|---|---|---|---|---|---|
| **大众层** | 所有 LearnBuddy 用户 | LearnBuddy 多租户 SaaS + Web/PWA + **中心化飞书机器人** | ❌ 不经过 OpenClaw | 零部署，飞书 OAuth 即可 | 移动触点、卡片测验、推送、晚复盘 |
| **Pro 层（opt-in）** | 想自托管、要数据自主、要 agent 自由度的极客 | **自托管 OpenClaw + iTutor skill 包** | ✅ 经过 OpenClaw | 自跑实例 + 自备 LLM key + 自建飞书应用 | 进阶自治玩法 + 跨端 agent 能力 |

**关键决策（已锁定）**
- ❌ 不 fork 改 OpenClaw 源码（高速迭代 = 永久 rebase 地狱）
- ❌ 不替每个用户托管一个 OpenClaw 实例（N 个进程沙箱/限额 = 运维三杀）
- ✅ LearnBuddy 把能力暴露为 **MCP / 稳定 HTTP 工具 + skill 包**，OpenClaw 通过 skill/bindings 扩展点接入

---

## 4. OpenClaw 接入架构图

参见 [`OpenClaw接入方案.html#arch`](./OpenClaw接入方案.html#arch) 与 SVG：
[`OpenClaw接入方案.svg`](./OpenClaw接入方案.svg)

图示要点：
- LearnBuddy 主系统（学习真相源 + 商业化底座）**始终在左**
- 工具注册层 `core/tools.py`（Batch L 第 1 步） + MCP Server `mcp_server.py` 是公共地基
- **大众层** = LearnBuddy 直接拉 WebSocket 接飞书 IM（不经 OpenClaw）
- **Pro 层** = OpenClaw 通过 skill 包调 MCP 工具，再由 OpenClaw 经 WebSocket 接飞书
- OpenClaw 永远不能跨过 MCP / 受控接口去直连 DB / 改账 / 改学习真相源

---

## 5. LearnBuddy 侧必须已就绪的接入清单

| 能力 | 当前状态 | 接入 OpenClaw 之前必须 |
|---|---|---|
| `core/tools.py` 统一工具注册 | ✅ Batch L 落地 | 工具描述 / 参数 schema 标准化 |
| `mcp_server.py` MCP stdio 服务 | ✅ Batch L 落地 | 至少一个 stdio client 跑通 |
| `generate_lesson` / `get_today_plan` / `record_answer` / `get_analytics` / `get_graph` / `kb_search` / `coach_reply` | ✅ 多数已存在 | 文档化入参出参与错误码 |
| 用户身份绑定 | ✅ `ITUTOR_MCP_USER_EMAIL` 已支持 | 增加 `ITUTOR_MCP_TOKEN` 二选一 |
| 速率限制 | ✅ `core/auth.py` | MCP 路径增加连接级限频 |
| 审计日志 | 🟡 tracing 在，审计缺 | Pro 层强制启用审计 |

---

## 6. OpenClaw 侧必须已就绪的接入清单

| 项 | 说明 |
|---|---|
| Skill 包 | iTutor skill 包：注册 MCP 端点 + iTutor 教练 prompt + 工具映射 |
| Plugin | `@larksuite/openclaw-lark`（官方飞书插件） |
| 飞书应用 | 自建应用，IM 机器人权限、消息发送/接收、卡片模板 |
| WebSocket | OpenClaw ↔ 飞书长连接；国内版零公网 IP |
| 模型 | OpenClaw 用户自备 LLM key，**LearnBuddy 不承担** |
| 命令执行 | **Pro 层禁用系统命令**（仅 read-only + 进度写入） |

---

## 7. 最小验证路径（先于任何落地）

> 来源：ADR-010 “最小验证路径”：作者本人先把现有 iTutor 接自托管 OpenClaw + 飞书，dogfood 跑通下面三件事。

| 验证项 | 说明 | 验收 |
|---|---|---|
| 早推计划 | 早 9 点由 OpenClaw 触发，主动推送今日计划 | 飞书卡片能收到 |
| 卡片测验 | 用户在卡片里直接作答，反写回 LearnBuddy | 完成度可查 |
| 晚复盘 | 晚 9 点自动复盘当日学了什么 / 没学什么 | 摘要卡片 + 留存记录 |

三件至少跑一周无异常，才能决定是否投入“中心化飞书机器人”。

---

## 8. 安全边界（与个人助理模式共享）

| 项 | 边界 |
|---|---|
| 学习真相源 | 计划 / 学情 / 记忆 / 掌握度 / 推荐 / 权益统一归主系统 |
| 账本 | 支付 / 积分 / 订阅只在主系统，OpenClaw 只能读取/消费权益 |
| 数据库 | OpenClaw 不允许直连 DB |
| 工具 | 只能走 `core/tools.py` 注册的受控工具 |
| 命令执行 | Pro 层默认禁用；如开放必须沙盒 + 用户确认 |
| 提示注入 | 启用 AgentGuard 拦截 + 输入审计 |

---

## 9. 排期：T-38 → T-44

| ID | 任务 | 验收标准 | 关联模块 |
|---|---|---|---|
| T-38 | OpenClaw 接入方案页落地 | 当前文档可独立回答“OpenClaw 是什么 / 怎么接 / 谁来用” | docs/06-OpenClaw接入方案.md |
| T-39 | MCP 端点 schema 标准化 | 工具描述 / 参数 / 错误码全文档化，可被外部 client 直接消费 | mcp_server.py |
| T-40 | iTutor skill 包 v0 | skill.yaml + 工具映射 + 教练 prompt，能挂在 OpenClaw plugin 扩展点 | iTutor-skill/ |
| T-41 | 中心化飞书机器人原型 | LearnBuddy 直接拉飞书 WebSocket，单应用 OAuth 跑通 | core/lark_bot/ |
| T-42 | AgentGuard 接入 | prompt / indirect injection 拦截 + 审计日志 | core/security/agentguard.py |
| T-43 | 自托管 OpenClaw + 飞书 dogfood | 作者本人完成早推 / 卡片测验 / 晚复盘三件验证 | scripts/dogfood/ |
| T-44 | OpenClaw 升级模式商业化 | Pro 层订阅开通后自动下发 skill 包 / token | core/assistant/ |

---

## 10. 三条边界（与 Harness 对齐页保持一致）

- **不变成 AI 名词百科**：本文件只回答“OpenClaw 在 LearnBuddy 怎么接”，不抄 OpenClaw 文档
- **不被热点牵着跑**：OpenClaw 升级时再回来刷新文档，不每个版本都改
- **不退回工具测评视角**：OpenClaw 是承接方，不是 LearnBuddy 的核心

---

## 11. 与现有页面的对应

| 主题 | 主页 | 本专题页 |
|---|---|---|
| 双层定位 | `SPEC.md` ADR-010 | 本页 §3 |
| 接入架构 | `技术方案.html` | 本页 §4 |
| 接入清单 | `02-技术方案.md` | 本页 §5 §6 |
| 排期 | `开发方案与计划.html#todo` | 本页 §9（T-38 → T-44） |
| Harness 对齐 | `Harness技术体系对齐.html` | 跨 L4 / L5 / 19 / 20 多篇 |
| 个人助理模式 | `需求方案.html` | 本页 §8 共享安全边界 |
