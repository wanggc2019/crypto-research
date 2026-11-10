#!/usr/bin/env bash
# crypto-analyst 定时任务入口（launchd 单任务双时间，日志格式与其它模块对齐）：
#   每天 10:00 日报（规则）；周日 20:00 周报 + AI 分析与区间报告（脚本按星期/日期判断）
# 完整一轮之后做 healthcheck（首轮也有 start/done 可解析）与当天首轮 prune。
set -uo pipefail
cd "$(dirname "$0")/.."                 # crypto-analyst/
REPO_ROOT="$(cd .. && pwd)"             # 仓库根
export PYTHONDONTWRITEBYTECODE=1
PY="$REPO_ROOT/.venv/bin/python"
[ -x "$PY" ] || PY=python3
MODULE="$(basename "$PWD")"

mkdir -p data/logs data/state
LOCKDIR=data/state/collect.lock
if ! mkdir "$LOCKDIR" 2>/dev/null; then
  echo "$(date '+%F %T') skip: previous run still in progress" >> data/logs/collect.log
  exit 0
fi
trap 'rmdir "$LOCKDIR" 2>/dev/null || true' EXIT
{
  echo "===== $(date '+%F %T') start ====="
  ./scripts/run_agent.sh
  rc=$?
  echo "===== $(date '+%F %T') done (rc=$rc) ====="
} >> data/logs/collect.log 2>&1

# 健康检查放在完整一轮之后：首轮也不会误报「找不到完整一轮」
"$PY" "$REPO_ROOT/tools/healthcheck.py" --module "$MODULE" --quiet --notify --fix || true

# 每日维护（幂等）
STAMP="data/state/.prune-$(date +%F)"
if [ ! -f "$STAMP" ]; then
  echo "--- maintenance $(date '+%F %T') ---" >> data/logs/collect.log
  "$PY" "$REPO_ROOT/tools/prune.py" --module "$MODULE" --apply || true
  touch "$STAMP"
fi

# 把本轮 rc 透传给 launchd（否则脚本最后一句是 echo，launchd 永远看到 0）
exit "${rc:-0}"
