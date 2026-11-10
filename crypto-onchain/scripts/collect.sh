#!/usr/bin/env bash
# launchd 入口（M1/M2 影子期：只落盘、不推送）：
#   单实例锁 → 跑一轮影子采集 → healthcheck（异常才告警/自愈）→ 当天首轮 prune
# 日志格式与其它模块对齐（start/done），仓库级看门狗据此判定最近一轮。
set -uo pipefail
cd "$(dirname "$0")/.."                 # crypto-onchain/
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
  ./scripts/run.sh
  rc=$?
  echo "===== $(date '+%F %T') done (rc=$rc) ====="
} >> data/logs/collect.log 2>&1

# 每轮自检（正常静默；异常才告警/自愈；本模块无 inspect.sh，不传 --agent）
"$PY" "$REPO_ROOT/tools/healthcheck.py" --module "$MODULE" --quiet --notify --fix || true

# 每日维护（幂等）
STAMP="data/state/.prune-$(date +%F)"
if [ ! -f "$STAMP" ]; then
  echo "--- maintenance $(date '+%F %T') ---" >> data/logs/collect.log
  "$PY" "$REPO_ROOT/tools/prune.py" --module "$MODULE" --apply || true
  touch "$STAMP"
fi

# 每日影跑摘要（21:00 后首个轮次；只汇总不告警，失败不影响本轮 rc）
"$PY" scripts/digest.py --if-due --notify >> data/logs/collect.log 2>&1 || true

# 把本轮 rc 透传给 launchd
exit "${rc:-0}"
