#!/usr/bin/env bash
# crypto-price 一键入口：跑一轮（+ 可选推送）
#   ./scripts/run.sh                    跑一轮，只记录不推送
#   ./scripts/run.sh --notify           跑一轮并推送
#   ./scripts/run.sh --dry-run --json   只预览
set -euo pipefail
cd "$(dirname "$0")/.."
REPO_ROOT="$(cd .. && pwd)"
export PYTHONDONTWRITEBYTECODE=1
PY="$REPO_ROOT/.venv/bin/python"
[ -x "$PY" ] || PY=python3
exec "$PY" scripts/run.py "$@"
