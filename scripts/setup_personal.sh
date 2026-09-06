#!/usr/bin/env bash
# LearnBuddy 个人版安全启动：生成本地 secret，校验配置，只绑定到 127.0.0.1。

set -euo pipefail
umask 077

LB_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LB_ENV_FILE="$LB_ROOT/.env"

cd "$LB_ROOT"

fail() {
  echo "❌ $*" >&2
  exit 1
}

env_value() {
  local key="$1"
  sed -n "s/^${key}=//p" "$LB_ENV_FILE" | tail -n 1 | sed 's/[[:space:]]*#.*$//; s/^[[:space:]]*//; s/[[:space:]]*$//'
}

set_env_value() {
  local key="$1"
  local value="$2"
  local tmp_file
  tmp_file="$(mktemp "${TMPDIR:-/tmp}/learnbuddy-env.XXXXXX")"
  awk -v key="$key" -v value="$value" '
    BEGIN { replaced = 0 }
    $0 ~ "^" key "=" { print key "=" value; replaced = 1; next }
    { print }
    END { if (!replaced) print key "=" value }
  ' "$LB_ENV_FILE" > "$tmp_file"
  mv "$tmp_file" "$LB_ENV_FILE"
}

random_hex() {
  od -An -N48 -tx1 /dev/urandom | tr -d ' \n'
}

command -v docker >/dev/null 2>&1 || fail "未找到 Docker，请先安装 Docker Engine/Desktop。"
docker compose version >/dev/null 2>&1 || fail "需要 Docker Compose v2（docker compose）。"

if [[ ! -f "$LB_ENV_FILE" ]]; then
  cp .env.example "$LB_ENV_FILE"
  echo "✓ 已从 .env.example 创建 .env"
fi
chmod 600 "$LB_ENV_FILE"

set_env_value "LEARNBUDDY_EDITION" "personal"
set_env_value "LEARNBUDDY_BIND_ADDRESS" "127.0.0.1"
set_env_value "ITUTOR_AGENT_REMINDERS_ENABLED" "1"

for secret_key in ITUTOR_AGENT_TOKEN_SECRET ITUTOR_OPENCLAW_BRIDGE_TOKEN OPENCLAW_GATEWAY_TOKEN; do
  if [[ -z "$(env_value "$secret_key")" ]]; then
    set_env_value "$secret_key" "$(random_hex)"
    echo "✓ 已本地生成 $secret_key"
  fi
done

generic_key="$(env_value LEARNBUDDY_LLM_API_KEY)"
legacy_key="$(env_value DEEPSEEK_API_KEY)"
if [[ -z "$generic_key" && -z "$legacy_key" ]]; then
  cat >&2 <<'EOF'
❌ 尚未配置 LLM API key。
   请编辑 .env，优先填写 LEARNBUDDY_LLM_API_KEY / BASE_URL / MODEL；
   旧版 DEEPSEEK_API_KEY / BASE_URL / MODEL 仍可兼容。填好后重新运行本脚本。
EOF
  exit 2
fi

docker compose config --quiet
docker compose up -d --build learnbuddy
docker compose exec -T learnbuddy python -c \
  "import os; assert os.access('/app/data', os.W_OK), '/app/data is not writable by the LearnBuddy uid'"
docker compose exec -T learnbuddy python -c \
  "import json, urllib.request; payload=json.load(urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=5)); assert payload.get('ready') is True, payload"

lb_public_port="$(env_value LEARNBUDDY_PORT)"
lb_public_port="${lb_public_port:-8000}"

cat <<EOF

✅ LearnBuddy 个人版已启动且 readiness 通过（仅本机可访问）
   地址：http://127.0.0.1:${lb_public_port}

下一步：
1. 先在本机打开 /login，创建唯一 owner。
2. 远程服务器请先建 SSH tunnel，未初始化前不要公开端口。
3. 需飞书 Agent 时再运行：bash scripts/setup_openclaw.sh

状态：docker compose ps
日志：docker compose logs -f learnbuddy
EOF
