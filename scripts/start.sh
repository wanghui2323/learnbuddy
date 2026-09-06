#!/usr/bin/env bash
# Itutor 一键启动（V0）
#
# 关键点：用项目外的本地 venv（~/.local/share/itutor/.venv），
# 避免从 iCloud 同步目录导入 fastapi/openai 等大包时被 iCloud 守护进程
# 卡住几分钟。代码本身（core/、server.py）仍从 iCloud 项目目录读，小文件无碍。
#
# 用法：
#   bash scripts/start.sh
#   PORT=8001 bash scripts/start.sh

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCAL_VENV="$HOME/.local/share/itutor/.venv"
PY="$LOCAL_VENV/bin/python"

cd "$PROJECT_ROOT"

if [[ ! -x "$PY" ]]; then
  echo "❌ 找不到本地 venv：$PY"
  echo "   请先创建：uv venv \"$LOCAL_VENV\" --python 3.11 && \"$PY\" -m pip install -r requirements.txt"
  exit 1
fi

export PYTHONUNBUFFERED=1
export HOST="${HOST:-127.0.0.1}"
export PORT="${PORT:-8000}"

echo "Itutor 启动中… → http://$HOST:$PORT"
echo "（用本地 venv：$PY）"
echo "按 Ctrl+C 停止。"

exec "$PY" -m uvicorn server:app --host "$HOST" --port "$PORT"
