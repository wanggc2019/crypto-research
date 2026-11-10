#!/usr/bin/env bash
# crypto-jobs 周报入口（手动补跑）：只读历史快照 → 技能趋势周报 → Telegram；
# 定时任务不单独装：周日 10:00 的 collect.sh 会先采集最新快照再调用 --weekly --notify
set -euo pipefail
cd "$(dirname "$0")/.."
REPO_ROOT="$(cd .. && pwd)"
export PYTHONDONTWRITEBYTECODE=1
PY="$REPO_ROOT/.venv/bin/python"
[ -x "$PY" ] || PY=python3

"$PY" scripts/main.py --weekly --notify "$@"
