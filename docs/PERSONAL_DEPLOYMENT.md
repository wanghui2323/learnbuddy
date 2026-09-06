# LearnBuddy Personal · 30 分钟私有部署

LearnBuddy Personal 面向一个真实使用者：你自备 LLM Key，数据留在自己的卷中，需要时再启用独立 OpenClaw + 自有飞书应用。

> 这是开源发布前的第一轮部署基线。容器、插件与配置已可做静态验证；真实 OpenClaw Gateway 加载、飞书收发和恢复演练仍是发布门，未留下证据前不应宣称“开箱即用”。

## 1. 你将部署什么

| 组件 | 默认状态 | 职责 | 持久化 |
|---|---|---|---|
| `learnbuddy` | 启动 | 学习路径、任务、进度、掌握度、记忆和提醒规则的唯一真相源 | `learnbuddy_data` |
| `openclaw` | 可选 `agent` profile | Agent 会话、飞书 WebSocket 通道、调用 LearnBuddy 工具 | `openclaw_state` + `openclaw_workspace` |

OpenClaw 不直连 SQLite，只能经过 LearnBuddy 的 requester-scoped MCP 工具。定时提醒也只有一个投递者：`LearnBuddy scheduler -> core/feishu_sender -> 飞书`；OpenClaw 只处理会话入站和 Agent 回复，不轮询 reminder queue。

## 2. 准备

- macOS / Linux 或云服务器，已安装 Docker Engine/Desktop 与 Docker Compose v2。
- 一个 OpenAI-compatible 模型 API Key。DeepSeek 可直接用 `https://api.deepseek.com` + `deepseek-chat`。
- 只用 LearnBuddy 时，1C2G 可作起步规格，但需以实际生成任务验证。同机启用 OpenClaw 时建议从 2C4G 起；这是保守起点，不是压测结论。
- 计划接飞书时，准备你自己可管理的飞书应用。个人开发者能否创建和发布，以飞书开放平台当前认证与租户规则为准；LearnBuddy 不要求你把凭据交给项目运营方。

本 Compose 固定官方镜像 `2026.8.2`，并安装匹配的 `@openclaw/feishu@2026.8.2`，两者都不跟随 `latest`。这个版本下官方 Plugin SDK 已提供 LearnBuddy 所需的 requester-scoped MCP resolver；较早的 `2026.7.1-2` 不满足该接口契约。容器已包含 Node；官方预构建镜像也避免了本机从源码构建 OpenClaw 时约 6 GB 内存的要求。

LearnBuddy 核心与 OpenClaw Agent 是两个独立的模型客户端：前者读取 `.env` 中的 `LEARNBUDDY_LLM_*`，后者在 onboarding 向导中配置 Provider。两者可以使用同一家 Provider/密钥，但不会自动互相复制配置。

## 3. 0–10 分钟：启动 LearnBuddy

```bash
git clone https://github.com/wanghui2323/learnbuddy.git learnbuddy
cd learnbuddy
cp .env.example .env
chmod 600 .env
```

编辑 `.env`，至少填写：

```dotenv
LEARNBUDDY_EDITION=personal
LEARNBUDDY_LLM_API_KEY=your_key
LEARNBUDDY_LLM_BASE_URL=https://api.deepseek.com
LEARNBUDDY_LLM_MODEL=deepseek-chat
```

旧的 `DEEPSEEK_*` 变量仍兼容。然后运行：

```bash
bash scripts/setup_personal.sh
docker compose ps
```

脚本会生成三个本地 secret，并强制把 Web 端口绑到 `127.0.0.1`。请先在 `http://127.0.0.1:8000/login` 创建唯一 owner，或用不保存密码的交互脚本：

```bash
python3 scripts/init_personal_owner.py
```

Personal 模式的第一次注册是 setup-only：数据库无用户时可创建 owner，之后所有注册请求都应被拒绝。不要把 owner 密码写入 `.env`。

### 在远程服务器上初始化

未创建 owner 前，不得将端口改绑 `0.0.0.0`。先从自己的电脑建 SSH tunnel：

```bash
ssh -L 8000:127.0.0.1:8000 your-user@your-server
```

然后在本地打开 `http://127.0.0.1:8000/login`。初始化、登录和核心学习链路验证后，再用 Nginx/Caddy 反向代理到 HTTPS。开启 HTTPS 后设置：

```dotenv
ITUTOR_ENV=production
ITUTOR_BASE_URL=https://your-domain.example
```

只对外反代 LearnBuddy `8000`；OpenClaw Gateway `18789` 保持本机/私网可见。

## 4. 10–25 分钟：接入自有飞书 + OpenClaw

### 4.1 创建飞书应用

1. 在[飞书开放平台](https://open.feishu.cn/) 创建企业自建应用，开启机器人能力。
2. 按 OpenClaw 飞书向导授予消息收发权限，订阅 `im.message.receive_v1`，并发布/安装应用。
3. 使用 WebSocket 长连接模式，不需要公网 webhook URL。
4. 将该应用的 `App ID` / `App Secret` 填入本机 `.env`：

```dotenv
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=xxx
```

OpenClaw 官方也提供二维码快速建机器人，并会把 DM 限定给扫码本人。但当前 LearnBuddy 定时提醒由自身 sender 投递，因此完整闭环仍需要 `.env` 中可用的同一组 App ID / Secret；首版建议选手动模式。

### 4.2 启动 Agent profile

```bash
bash scripts/setup_openclaw.sh
```

该脚本会：

1. 拉取固定版官方 OpenClaw 镜像。
2. 在独立 Node 容器里执行 onboarding，配置你自己的 Agent 模型。
3. 打包并安装本仓 `integrations/openclaw-learnbuddy` 插件。
4. 安装与 Gateway 匹配的固定版飞书官方插件。
5. 注册 requester-scoped MCP，禁用群聊、动态多 Agent 和高风险系统工具。
6. 运行飞书通道向导，再启动 Gateway。

第二次运行可以跳过模型 onboarding：

```bash
bash scripts/setup_openclaw.sh --skip-onboard
```

## 5. 25–30 分钟：绑定与验收

1. 先私聊机器人获取 pairing code，核对后只批准你自己：

   ```bash
   docker compose --profile agent exec openclaw \
     node dist/index.js pairing list feishu
   docker compose --profile agent exec openclaw \
     node dist/index.js pairing approve feishu PAIRING_CODE --notify
   ```

2. 在 LearnBuddy 账号页生成一次性 `LB-...` 绑定码。
3. 私聊你的飞书机器人：`绑定 LB-...`。
4. 询问“今天学什么”，确认回答来自你的 LearnBuddy 数据。
5. 说“工作日 20:30 提醒我学习”，回到 Web 确认提醒规则已更新。
6. 在到期时确认只收到一条提醒。
7. 重启后再查一次学习进度：

```bash
docker compose --profile agent restart
docker compose --profile agent ps
docker compose --profile agent logs --tail=200 learnbuddy openclaw
```

只有下列全部有证据，才可以标记该安装完成：

| 门 | 证据 |
|---|---|
| 启动 | `docker compose ps` 中 LearnBuddy healthy，OpenClaw Gateway 运行 |
| 单 owner | 首次注册成功，第二个注册被拒绝 |
| BYOK | 真实完成一次对话/基线题/生成，错 key 有可见错误 |
| 隔离 | OpenClaw 只能通过带短 token 的 MCP 访问 owner 数据 |
| 飞书 | owner 私聊消息往返、工具写回、定时提醒均成功 |
| 持久化 | 容器重启后账号、路径、进度和会话状态仍在 |
| 恢复 | 备份到新卷后上述数据可读 |

## 6. 安全基线

- `.env` 只留在部署机，权限保持 `600`；不要截图、发群或提交。
- SQLite 与 OpenClaw 状态在 Docker 卷内也是明文；部署机应开启磁盘/云盘加密并限制主机管理权限。
- 机器人 DM 必须是 `allowlist` / pairing，只允许 owner `open_id`；群聊保持 `disabled`。
- OpenClaw Gateway 不公开到互联网，Control UI 仅 `127.0.0.1` 或 SSH tunnel 可访问。
- 默认仅允许 LearnBuddy 绑定/MCP 工具，禁止 `exec` / browser / filesystem / write 等高风险工具。
- 不要把 Personal Gateway 做成不互信的多用户公共服务。每个部署是一个信任边界。
- 泄露后同时轮换 LLM Key、飞书 App Secret、`ITUTOR_AGENT_TOKEN_SECRET`、`ITUTOR_OPENCLAW_BRIDGE_TOKEN` 和 `OPENCLAW_GATEWAY_TOKEN`。

## 7. 备份、恢复与升级

### 备份

```bash
bash scripts/backup_personal.sh
```

默认写到仓库同级的 `learnbuddy-backups/<UTC 时间>/`，不会复制 `.env`。备份时会短暂停止容器，确保 SQLite/OpenClaw 状态一致。脚本要求主数据卷必须存在，并为每次备份生成 `manifest.txt` 和 `SHA256SUMS`；不会把缺主数据的目录误报为成功。

整个备份都应按高敏数据处理：`learnbuddy_data` 含个人学习数据；OpenClaw 状态卷还可能含模型/飞书凭据、配对关系和私聊上下文。脚本只会收紧本地文件权限，不会自动加密；请存入加密磁盘或密码库。如果确实需要把环境文件一起快照：

```bash
LEARNBUDDY_BACKUP_INCLUDE_ENV=1 bash scripts/backup_personal.sh
```

该开关复制的 `env.snapshot` 仍是明文；请立即转入加密位置，不要上传到代码托管或普通网盘。

### 恢复

恢复会覆盖指定 Compose 项目的三个卷，脚本会先再做一份当前备份，并要求输入 `RESTORE`：

```bash
bash scripts/restore_personal.sh /absolute/path/to/learnbuddy-backups/20260902T120000Z --confirm
```

覆盖任何卷之前，脚本会先校验 manifest 版本、Compose project、SHA-256、tar 可读性和路径安全。默认拒绝把其他 Compose project 的备份恢复进当前项目；只有确认这是迁移操作时，才可显式设置：

```bash
LEARNBUDDY_RESTORE_ALLOW_PROJECT_MISMATCH=1 \
  bash scripts/restore_personal.sh /absolute/path/to/backup --confirm
```

如源备份不含可选的 OpenClaw 卷，恢复会清除当前同名旧卷，防止旧凭据或会话残留。恢复脚本不会删除源备份，并会在完成后检查 LearnBuddy `/readyz`。在生产机上操作前，先用新 Compose project/volume 做一次演练。

### 升级

```bash
bash scripts/backup_personal.sh
git pull --ff-only
docker compose build --pull learnbuddy
docker compose up -d learnbuddy
docker compose --profile agent pull openclaw
docker compose --profile agent up -d openclaw
```

OpenClaw 升级不能改为 `latest`。明确选定新版本、阅读 release notes，成对更新 `.env` 中的 `OPENCLAW_IMAGE` 与 `OPENCLAW_FEISHU_PLUGIN_VERSION`，再重跑 `setup_openclaw.sh --skip-onboard` 和全部飞书验收门。

## 8. 诊断

```bash
docker compose config --quiet
docker compose ps
docker compose logs --tail=200 learnbuddy
docker compose --profile agent logs --tail=200 openclaw
docker compose --profile agent exec openclaw node dist/index.js plugins inspect learnbuddy --runtime --json
docker compose --profile agent exec openclaw node dist/index.js mcp status --verbose
```

- LearnBuddy 容器对外的 Compose 健康门是 `/readyz`，它检查运行版本和数据库是否可接流量；`/healthz` 只是 liveness。LLM 不作为进程 readiness 的硬依赖，仍需用一次真实模型请求单独验收。
- OpenClaw 容器使用官方 `/healthz` 做 liveness；需确认飞书通道时，再人工检查 `/readyz` 和 Gateway 日志。
- 飞书不收消息时，依次检查应用是否发布/安装、`im.message.receive_v1`、WebSocket 长连接、DM allowlist 与 Gateway 日志。
- 能聊天但不能读计划时，检查 LearnBuddy 绑定是否完成、bridge token 是否两端一致、MCP 状态与插件 runtime registrations。
- 重启后数据丢失时，先停止写入，确认 Compose 仍使用同一 project name 与命名卷，不要直接初始化新 owner。

## 参考

- [OpenClaw Docker](https://docs.openclaw.ai/install/docker)
- [OpenClaw Node 版本](https://docs.openclaw.ai/install/node)
- [OpenClaw 飞书通道](https://docs.openclaw.ai/channels/feishu)
- [OpenClaw 插件安装](https://docs.openclaw.ai/cli/plugins)
- [OpenClaw MCP 配置](https://docs.openclaw.ai/cli/mcp)
