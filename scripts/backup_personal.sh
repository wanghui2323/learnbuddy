#!/usr/bin/env bash
# 备份 LearnBuddy 个人版命名卷。.env 默认不复制，但 OpenClaw 状态仍可能含凭据。

set -euo pipefail
umask 077

LB_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LB_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-learnbuddy}"
LB_BACKUP_ROOT="${LEARNBUDDY_BACKUP_DIR:-$LB_ROOT/../learnbuddy-backups}"
LB_TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LB_DEST="$LB_BACKUP_ROOT/$LB_TIMESTAMP"
LB_ARCHIVE_IMAGE="alpine:3.20"

command -v docker >/dev/null 2>&1 || { echo "❌ 未找到 Docker" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "❌ 未找到 python3" >&2; exit 1; }
cd "$LB_ROOT"
mkdir -p "$LB_DEST"
docker pull "$LB_ARCHIVE_IMAGE" >/dev/null

container_is_running() {
  local service_name="$1"
  local container_id
  container_id="$(docker compose --profile agent ps -q "$service_name" 2>/dev/null || true)"
  [[ -n "$container_id" ]] && [[ "$(docker inspect -f '{{.State.Running}}' "$container_id" 2>/dev/null || true)" == "true" ]]
}

LB_LEARNBUDDY_WAS_RUNNING=0
LB_OPENCLAW_WAS_RUNNING=0
container_is_running learnbuddy && LB_LEARNBUDDY_WAS_RUNNING=1
container_is_running openclaw && LB_OPENCLAW_WAS_RUNNING=1

restart_previous_services() {
  if [[ "$LB_LEARNBUDDY_WAS_RUNNING" -eq 1 ]]; then
    docker compose --profile agent start learnbuddy >/dev/null || true
  fi
  if [[ "$LB_OPENCLAW_WAS_RUNNING" -eq 1 ]]; then
    docker compose --profile agent start openclaw >/dev/null || true
  fi
}

trap restart_previous_services EXIT
if [[ "$LB_OPENCLAW_WAS_RUNNING" -eq 1 ]]; then
  docker compose --profile agent stop openclaw >/dev/null
fi
if [[ "$LB_LEARNBUDDY_WAS_RUNNING" -eq 1 ]]; then
  docker compose --profile agent stop learnbuddy >/dev/null
fi

backup_volume() {
  local logical_name="$1"
  local required="${2:-0}"
  local volume_name="${LB_PROJECT_NAME}_${logical_name}"
  local archive_path="$LB_DEST/${logical_name}.tar.gz"
  if ! docker volume inspect "$volume_name" >/dev/null 2>&1; then
    if [[ "$required" == "1" ]]; then
      echo "❌ 主数据卷不存在，拒绝生成不完整备份：$volume_name" >&2
      return 1
    fi
    echo "- 跳过不存在的卷：$volume_name"
    return
  fi
  if ! docker run --rm \
    -v "$volume_name:/source:ro" \
    "$LB_ARCHIVE_IMAGE" \
    tar -czf - -C /source . > "$archive_path"; then
    rm -f "$archive_path"
    return 1
  fi
  echo "✓ 已备份 $volume_name"
}

backup_volume learnbuddy_data 1
backup_volume openclaw_state
backup_volume openclaw_workspace

LB_GIT_COMMIT="$(git -C "$LB_ROOT" rev-parse HEAD 2>/dev/null || echo unknown)"
printf 'format_version=1\ncreated_at=%s\ngit_commit=%s\ncompose_project=%s\nsensitive_data=yes\n' \
  "$LB_TIMESTAMP" "$LB_GIT_COMMIT" "$LB_PROJECT_NAME" > "$LB_DEST/manifest.txt"
python3 "$LB_ROOT/scripts/verify_personal_backup.py" checksum "$LB_DEST"

if [[ "${LEARNBUDDY_BACKUP_INCLUDE_ENV:-0}" == "1" && -f "$LB_ROOT/.env" ]]; then
  cp "$LB_ROOT/.env" "$LB_DEST/env.snapshot"
  chmod 600 "$LB_DEST/env.snapshot"
  echo "! 已按显式开关备份 .env；请只存放在加密位置。"
fi

chmod 600 "$LB_DEST"/*
echo "! 备份含学习数据；OpenClaw 状态还可能含模型/飞书凭据、配对关系和会话。请加密保存。"

restart_previous_services
trap - EXIT
echo "✅ 备份完成：$LB_DEST"
