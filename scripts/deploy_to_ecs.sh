#!/usr/bin/env bash
# LearnBuddy → 通用 ECS 一键部署 / 更新
#
# 用法：
#   .venv/bin/python scripts/release_loop.py release --confirm V0.53
#                                                   # 正式版本发布（推荐且带完整门禁）
#   ITUTOR_ALLOW_DIRECT_DEPLOY=1 bash scripts/deploy_to_ecs.sh
#                                                   # 紧急直接部署（显式绕过并留痕）
#   bash scripts/deploy_to_ecs.sh --deps          # 同上 + 在服务器重装依赖
#   bash scripts/deploy_to_ecs.sh --no-restart    # 只传不重启（纯静态页改动够用）
#   bash scripts/deploy_to_ecs.sh web/index.html server.py   # 快速模式：只传指定文件
#
# 安全铁律：永不覆盖服务器的运行时数据与生产配置。
#   → 始终排除整个 data/（实例 / 数据库 / 用户）和 .env（线上 key）。
#   → 首次部署的主机、systemd、反向代理与 .env 由运营者在服务器端单独配置。
#
# 配置（可用环境变量覆盖）：
#   SSH_ALIAS=learnbuddy  REMOTE_DIR=/opt/learnbuddy  SERVICE=learnbuddy
#   PUBLIC_URL=https://learn.example.com

set -euo pipefail

SSH_ALIAS="${SSH_ALIAS:-itutor}"
REMOTE_DIR="${REMOTE_DIR:-/opt/itutor}"
SERVICE="${SERVICE:-itutor}"
PUBLIC_URL="${PUBLIC_URL:-https://learnbuddy.top}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAME="$(basename "$ROOT")"
SSH="ssh -o BatchMode=yes ${SSH_ALIAS}"
SCP="scp -o BatchMode=yes"

cd "$ROOT"
[[ -f server.py && -f requirements.txt ]] || { echo "❌ 请在项目根运行（缺 server.py/requirements.txt）"; exit 1; }

# 排除清单：运行时数据、密钥、本地环境一律不传
EXCLUDES=(.venv .git __pycache__ '*.pyc' data .env '.env.*' .DS_Store
          .pytest_cache .mypy_cache .ruff_cache '*.egg-info' '*.log' node_modules)

DO_DEPS=0; DO_RESTART=1; FILES=()
for a in "$@"; do
  case "$a" in
    --deps) DO_DEPS=1 ;;
    --no-restart) DO_RESTART=0 ;;
    -h|--help) sed -n '2,18p' "$0"; exit 0 ;;
    *) FILES+=("$a") ;;
  esac
done

# 正式部署必须由 Release Loop 调用；紧急操作必须显式声明绕过。
# 纯静态、无重启上传不改变运行版本，可继续直接使用。
if [[ $DO_RESTART -eq 1 && "${ITUTOR_RELEASE_LOOP:-0}" != "1" && "${ITUTOR_ALLOW_DIRECT_DEPLOY:-0}" != "1" ]]; then
  echo "❌ 正式发布请使用：.venv/bin/python scripts/release_loop.py release --confirm V0.53"
  echo "   紧急绕过需显式设置 ITUTOR_ALLOW_DIRECT_DEPLOY=1，并自行保留备份与回归证据。"
  exit 2
fi

verify() {
  echo "── 验证 ──"
  local code
  code=$($SSH "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/" || echo "ERR")
  echo "  服务器本地 8000 → $code"
  code=$(curl -s -m 15 -o /dev/null -w '%{http_code}' "$PUBLIC_URL/" || echo "ERR")
  echo "  公网 $PUBLIC_URL → $code"
  $SSH "systemctl is-active $SERVICE" | sed 's/^/  服务状态：/'
}

# ── 快速模式：只传指定文件（保留相对路径）──
if [[ ${#FILES[@]} -gt 0 ]]; then
  echo "▶ 快速模式：上传 ${#FILES[@]} 个文件到 ${SSH_ALIAS}:${REMOTE_DIR}"
  restart_needed=0
  for f in "${FILES[@]}"; do
    [[ -f "$f" ]] || { echo "  跳过（不存在）：$f"; continue; }
    $SSH "mkdir -p '${REMOTE_DIR}/$(dirname "$f")'"
    $SCP "$f" "${SSH_ALIAS}:${REMOTE_DIR}/$f"
    echo "  ✓ $f"
    [[ "$f" == *.py ]] && restart_needed=1
  done
  if [[ $DO_RESTART -eq 1 && $restart_needed -eq 1 ]]; then
    echo "▶ 检测到 .py 改动，重启 $SERVICE"
    $SSH "systemctl restart $SERVICE && sleep 2"
  fi
  verify
  exit 0
fi

# ── 全量模式：打包 → 上传 → 解压 → (依赖) → 重启 → 验证 ──
TGZ="$(mktemp -t itutor-deploy-XXXX).tgz"
trap 'rm -f "$TGZ"' EXIT
RELEASE_COMMIT=""
if [[ "${ITUTOR_RELEASE_LOOP:-0}" == "1" ]]; then
  RELEASE_COMMIT="$(git rev-parse HEAD)"
  echo "▶ 从 Git 提交 ${RELEASE_COMMIT:0:12} 打包（不包含未跟踪文件）"
  git archive --format=tar.gz --prefix="${NAME}/" -o "$TGZ" HEAD
else
  echo "▶ 紧急直接打包（排除 data/ 与 .env）"
  tar_excludes=(); for e in "${EXCLUDES[@]}"; do tar_excludes+=(--exclude="$e"); done
  tar czf "$TGZ" "${tar_excludes[@]}" -C "$ROOT/.." "$NAME"
fi
echo "  包大小：$(du -h "$TGZ" | cut -f1)"

echo "▶ 上传并解压到 ${SSH_ALIAS}:${REMOTE_DIR}"
$SCP "$TGZ" "${SSH_ALIAS}:/tmp/itutor-deploy.tgz"
$SSH "mkdir -p '${REMOTE_DIR}' && tar xzf /tmp/itutor-deploy.tgz -C '${REMOTE_DIR}' --strip-components=1 --warning=no-unknown-keyword && rm -f /tmp/itutor-deploy.tgz"
if [[ -n "$RELEASE_COMMIT" ]]; then
  $SSH "printf '%s\\n' '${RELEASE_COMMIT}' > '${REMOTE_DIR}/.release-commit'"
fi

if [[ $DO_DEPS -eq 1 ]]; then
  echo "▶ 安装依赖（.venv/bin/pip install -r requirements.txt）"
  $SSH "cd '${REMOTE_DIR}' && .venv/bin/pip install -q -U pip wheel && .venv/bin/pip install -q -r requirements.txt && echo '  依赖 OK'"
fi

if [[ $DO_RESTART -eq 1 ]]; then
  echo "▶ 重启 $SERVICE"
  $SSH "systemctl restart $SERVICE && sleep 2"
fi

verify
echo "✅ 部署完成 → $PUBLIC_URL"
