#!/usr/bin/env bash
# 在独立容器中初始化 OpenClaw + 飞书 + LearnBuddy 本地插件。

set -euo pipefail

LB_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LB_ENV_FILE="$LB_ROOT/.env"
LB_SKIP_ONBOARD=0

if [[ "${1:-}" == "--skip-onboard" ]]; then
  LB_SKIP_ONBOARD=1
elif [[ $# -gt 0 ]]; then
  echo "用法：bash scripts/setup_openclaw.sh [--skip-onboard]" >&2
  exit 2
fi

cd "$LB_ROOT"

fail() {
  echo "❌ $*" >&2
  exit 1
}

env_value() {
  local key="$1"
  sed -n "s/^${key}=//p" "$LB_ENV_FILE" | tail -n 1 | sed 's/[[:space:]]*#.*$//; s/^[[:space:]]*//; s/[[:space:]]*$//'
}

[[ -f "$LB_ENV_FILE" ]] || fail "缺少 .env，请先运行 bash scripts/setup_personal.sh"
command -v docker >/dev/null 2>&1 || fail "未找到 Docker。"
docker compose version >/dev/null 2>&1 || fail "需要 Docker Compose v2。"

[[ "$(env_value LEARNBUDDY_EDITION)" == "personal" ]] || fail "LEARNBUDDY_EDITION 必须是 personal。"
[[ -n "$(env_value ITUTOR_AGENT_TOKEN_SECRET)" ]] || fail "缺少 ITUTOR_AGENT_TOKEN_SECRET。"
[[ -n "$(env_value ITUTOR_OPENCLAW_BRIDGE_TOKEN)" ]] || fail "缺少 ITUTOR_OPENCLAW_BRIDGE_TOKEN。"
[[ -n "$(env_value OPENCLAW_GATEWAY_TOKEN)" ]] || fail "缺少 OPENCLAW_GATEWAY_TOKEN。"
[[ -n "$(env_value FEISHU_APP_ID)" ]] || fail "缺少 FEISHU_APP_ID；完整提醒闭环需要你自有飞书应用的凭据。"
[[ -n "$(env_value FEISHU_APP_SECRET)" ]] || fail "缺少 FEISHU_APP_SECRET。"
LB_OPENCLAW_IMAGE="$(env_value OPENCLAW_IMAGE)"
LB_OPENCLAW_IMAGE="${LB_OPENCLAW_IMAGE:-ghcr.io/openclaw/openclaw:2026.8.2}"
[[ "$LB_OPENCLAW_IMAGE" != *":latest" ]] || fail "OPENCLAW_IMAGE 禁止使用 latest，请固定版本或 digest。"
LB_FEISHU_PLUGIN_VERSION="$(env_value OPENCLAW_FEISHU_PLUGIN_VERSION)"
LB_FEISHU_PLUGIN_VERSION="${LB_FEISHU_PLUGIN_VERSION:-2026.8.2}"
[[ "$LB_FEISHU_PLUGIN_VERSION" =~ ^[0-9]{4}\.[0-9]+\.[0-9]+([.-][0-9A-Za-z.-]+)?$ ]] \
  || fail "OPENCLAW_FEISHU_PLUGIN_VERSION 必须是明确版本，禁止 latest/tag 漂移。"

LB_COMPOSE=(docker compose --profile agent)

docker compose --profile agent config --quiet
echo "▶ 拉取固定版 OpenClaw 镜像"
"${LB_COMPOSE[@]}" pull openclaw

if [[ "$LB_SKIP_ONBOARD" -eq 0 ]]; then
  echo "▶ OpenClaw 首次 onboarding（请在向导里配置你自己的模型 Provider）"
  "${LB_COMPOSE[@]}" run --rm --no-deps --entrypoint node openclaw \
    dist/index.js onboard --mode local --no-install-daemon
fi

echo "▶ 安装并启用本仓 LearnBuddy 插件"
"${LB_COMPOSE[@]}" run --rm --no-deps --entrypoint sh openclaw -lc '
  set -eu
  archive="$(cd /opt/learnbuddy-plugin && npm pack --silent --pack-destination /tmp)"
  cd /app
  node dist/index.js plugins install "npm-pack:/tmp/$archive" --force --accept-capabilities
  node dist/index.js plugins enable learnbuddy --accept-capabilities
'

echo "▶ 安装与固定镜像匹配的飞书官方插件"
"${LB_COMPOSE[@]}" run --rm --no-deps --entrypoint node openclaw \
  dist/index.js plugins install "npm:@openclaw/feishu@$LB_FEISHU_PLUGIN_VERSION" \
  --force --pin --accept-capabilities
"${LB_COMPOSE[@]}" run --rm --no-deps --entrypoint node openclaw \
  dist/index.js plugins enable feishu --accept-capabilities

echo "▶ 配置飞书通道（选手动模式，使用 .env 中同一个自有应用）"
"${LB_COMPOSE[@]}" run --rm --no-deps --entrypoint node openclaw \
  dist/index.js channels login --channel feishu

echo "▶ 配置 requester-scoped LearnBuddy MCP 与最小工具边界"
"${LB_COMPOSE[@]}" run --rm --no-deps --entrypoint node openclaw \
  dist/index.js mcp set learnbuddy \
  '{"url":"http://learnbuddy:8000/api/mcp","transport":"streamable-http","requestTimeoutMs":30000,"connectionTimeoutMs":5000}'
"${LB_COMPOSE[@]}" run --rm --no-deps --entrypoint node openclaw \
  dist/index.js config set --batch-json \
  '[{"path":"gateway.mode","value":"local"},{"path":"gateway.bind","value":"lan"},{"path":"channels.feishu.dmPolicy","value":"pairing"},{"path":"channels.feishu.groupPolicy","value":"disabled"},{"path":"channels.feishu.dynamicAgentCreation.enabled","value":false},{"path":"session.dmScope","value":"per-account-channel-peer"},{"path":"tools.allow","value":["learnbuddy","learnbuddy_bind","learnbuddy__*"]},{"path":"tools.deny","value":["exec","process","write","edit","apply_patch","browser","computer","filesystem"]}]'

echo "▶ 启动 OpenClaw Gateway"
"${LB_COMPOSE[@]}" up -d openclaw

echo "▶ 静态诊断（真实飞书收发仍需手工验收）"
"${LB_COMPOSE[@]}" exec openclaw node dist/index.js plugins inspect learnbuddy --runtime --json
"${LB_COMPOSE[@]}" exec openclaw node dist/index.js mcp status --verbose

cat <<'EOF'

✅ OpenClaw 容器已启动。
首次私聊会返回 pairing code；请核对后执行：
docker compose --profile agent exec openclaw node dist/index.js pairing approve feishu PAIRING_CODE --notify
请继续执行《个人部署指南》的真实验收：飞书私聊、LB 绑定、“今天学什么”、调整提醒、定时收到提醒。
定时提醒的唯一投递者是 LearnBuddy scheduler/core/feishu_sender；OpenClaw 不轮询 reminder queue。
EOF
