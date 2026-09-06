#!/usr/bin/env bash
# 恢复 LearnBuddy 个人版命名卷。需显式 --confirm，且会先备份当前数据。

set -euo pipefail
umask 077

LB_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LB_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-learnbuddy}"
LB_ARCHIVE_IMAGE="alpine:3.20"
LB_SOURCE_DIR="${1:-}"
LB_CONFIRM="${2:-}"

if [[ -z "$LB_SOURCE_DIR" || "$LB_CONFIRM" != "--confirm" ]]; then
  echo "用法：bash scripts/restore_personal.sh /absolute/path/to/backup --confirm" >&2
  exit 2
fi
[[ "$LB_SOURCE_DIR" = /* ]] || { echo "❌ 备份目录必须是绝对路径" >&2; exit 2; }
[[ -d "$LB_SOURCE_DIR" ]] || { echo "❌ 备份目录不存在：$LB_SOURCE_DIR" >&2; exit 2; }
[[ -f "$LB_SOURCE_DIR/learnbuddy_data.tar.gz" ]] || { echo "❌ 缺少 learnbuddy_data.tar.gz" >&2; exit 2; }
command -v docker >/dev/null 2>&1 || { echo "❌ 未找到 Docker" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "❌ 未找到 python3" >&2; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "❌ 需要 Docker Compose v2（docker compose）" >&2; exit 1; }

LB_VERIFY_ARGS=(verify "$LB_SOURCE_DIR" --project "$LB_PROJECT_NAME")
if [[ "${LEARNBUDDY_RESTORE_ALLOW_PROJECT_MISMATCH:-0}" == "1" ]]; then
  LB_VERIFY_ARGS+=(--allow-project-mismatch)
fi
python3 "$LB_ROOT/scripts/verify_personal_backup.py" "${LB_VERIFY_ARGS[@]}"

cd "$LB_ROOT"
echo "! 即将覆盖 ${LB_PROJECT_NAME} 的持久卷内容："
echo "  $LB_SOURCE_DIR"
read -r -p "输入 RESTORE 继续：" LB_ACK
[[ "$LB_ACK" == "RESTORE" ]] || { echo "已取消"; exit 1; }

if docker volume inspect "${LB_PROJECT_NAME}_learnbuddy_data" >/dev/null 2>&1; then
  echo "▶ 先备份当前状态"
  bash "$LB_ROOT/scripts/backup_personal.sh"
else
  echo "- 当前不存在主数据卷，无需预备份"
fi
docker compose --profile agent down

restore_volume() {
  local logical_name="$1"
  local archive_path="$LB_SOURCE_DIR/${logical_name}.tar.gz"
  local volume_name="${LB_PROJECT_NAME}_${logical_name}"
  if [[ ! -f "$archive_path" ]]; then
    if docker volume inspect "$volume_name" >/dev/null 2>&1; then
      docker volume rm "$volume_name" >/dev/null
      echo "- 源备份不含 $logical_name，已清除旧卷，避免残留状态"
    else
      echo "- 源备份不含 $logical_name，当前也无旧卷"
    fi
    return
  fi
  docker volume create \
    --label "com.docker.compose.project=$LB_PROJECT_NAME" \
    --label "com.docker.compose.volume=$logical_name" \
    "$volume_name" >/dev/null
  docker run --rm \
    -v "$volume_name:/target" \
    -v "$LB_SOURCE_DIR:/backup:ro" \
    "$LB_ARCHIVE_IMAGE" \
    sh -eu -c "find /target -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +; tar -xzf '/backup/${logical_name}.tar.gz' -C /target"
  echo "✓ 已恢复 $volume_name"
}

restore_volume learnbuddy_data
restore_volume openclaw_state
restore_volume openclaw_workspace

if [[ -f "$LB_SOURCE_DIR/openclaw_state.tar.gz" ]]; then
  docker compose --profile agent up -d learnbuddy openclaw
else
  docker compose up -d learnbuddy
fi

docker compose exec -T learnbuddy python -c \
  "import json, urllib.request; payload=json.load(urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=5)); assert payload.get('ready') is True, payload"

echo "✅ 恢复完成，LearnBuddy readiness 已通过。源备份未删除：$LB_SOURCE_DIR"
