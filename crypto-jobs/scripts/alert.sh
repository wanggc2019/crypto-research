#!/usr/bin/env bash
# crypto-jobs 新岗位速报入口（launchd alert 任务，每 2 小时）：
# 抓取 → 只推「上次之后新增的目标岗位」；无新增则静默。
set -uo pipefail
cd "$(dirname "$0")/.."
REPO_ROOT="$(cd .. && pwd)"
export PYTHONDONTWRITEBYTECODE=1
PY="$REPO_ROOT/.venv/bin/python"
[ -x "$PY" ] || PY=python3

mkdir -p data/logs data/state
LOCKDIR=data/state/alert.lock
if ! mkdir "$LOCKDIR" 2>/dev/null; then
  echo "$(date '+%F %T') skip: previous alert run still in progress" >> data/logs/alerts.log
  exit 0
fi
trap 'rmdir "$LOCKDIR" 2>/dev/null || true' EXIT

{
  echo "===== $(date '+%F %T') alert start ====="
  ./scripts/run.sh --alerts --notify
  rc=$?
  echo "===== $(date '+%F %T') alert done (rc=$rc) ====="
} >> data/logs/alerts.log 2>&1

exit "$rc"
