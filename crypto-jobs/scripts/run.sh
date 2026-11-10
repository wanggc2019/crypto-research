#!/usr/bin/env bash
# crypto-jobs 一键入口：跑一轮（+ 可选推送）
set -euo pipefail
cd "$(dirname "$0")/.."
REPO_ROOT="$(cd .. && pwd)"
export PYTHONDONTWRITEBYTECODE=1
PY="$REPO_ROOT/.venv/bin/python"
[ -x "$PY" ] || PY=python3

"$PY" scripts/main.py "$@"
