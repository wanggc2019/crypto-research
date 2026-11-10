#!/usr/bin/env bash
# crypto-onchain 一键入口：跑一轮影子采集（M1 不推送）
#   ./scripts/run.sh                    跑一轮，写影子快照/候选
#   ./scripts/run.sh --dry-run --json   只预览，不写文件
#   ./scripts/run.sh --fixtures tests/fixtures --data-dir /tmp/x --dry-run --json
set -euo pipefail
cd "$(dirname "$0")/.."
REPO_ROOT="$(cd .. && pwd)"
export PYTHONDONTWRITEBYTECODE=1
PY="$REPO_ROOT/.venv/bin/python"
[ -x "$PY" ] || PY=python3
exec "$PY" scripts/run.py "$@"
