#!/usr/bin/env bash
# 一键：采集 + 评分（+ 可选推送）
# 用法：
#   ./scripts/run.sh               采集 + 评分，不推送
#   ./scripts/run.sh --notify      采集 + 评分 + 推送 P0/P1
set -euo pipefail
cd "$(dirname "$0")/.."                 # crypto-news/
REPO_ROOT="$(cd .. && pwd)"             # 仓库根（crypto-news/ 的上一级）
export PYTHONDONTWRITEBYTECODE=1

# 单根 venv 在仓库根；没有 venv 时回退系统 python3（零依赖原则）
PY="$REPO_ROOT/.venv/bin/python"
[ -x "$PY" ] || PY=python3

"$PY" scripts/fetch.py
"$PY" scripts/score.py

if [ "${1:-}" = "--notify" ]; then
  "$PY" scripts/notify.py --send
fi
