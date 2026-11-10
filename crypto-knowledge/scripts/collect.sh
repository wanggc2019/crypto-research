#!/usr/bin/env bash
# crypto-knowledge 定时任务入口：每天 13:00 / 19:00 / 23:00（本地时区）三次。
# 每次由 main.py 按当前小时选时段；同日同时段只发一次（data/state/progress.json）。
# 与其它模块一致：加锁 → 跑一轮 → 健康检查 → 每日 prune。
set -uo pipefail
cd "$(dirname "$0")/.."
REPO_ROOT="$(cd .. && pwd)"
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
  ./scripts/run.sh --notify
  rc=$?
  echo "fetch: total=1 ok=$([ "$rc" -eq 0 ] && echo 1 || echo 0) fail=$([ "$rc" -eq 0 ] && echo 0 || echo 1)"
  echo "===== $(date '+%F %T') done (rc=$rc) ====="
} >> data/logs/collect.log 2>&1

# 健康检查：正常零输出；异常才推 Telegram（--fix 允许安全自愈：清锁/重载任务/清理数据）
"$PY" "$REPO_ROOT/tools/healthcheck.py" --module "$MODULE" --quiet --notify --fix \
  >> data/logs/healthcheck.out.log 2>&1 || true

# 每日维护（幂等）：raw 90d / logs 30d / reports 365d / 单日志 2MB / data 总量 500MB 上限
STAMP="data/state/.prune-$(date +%F)"
if [ ! -f "$STAMP" ]; then
  "$PY" "$REPO_ROOT/tools/prune.py" --module "$MODULE" --apply \
    >> data/logs/healthcheck.out.log 2>&1 || true
  touch "$STAMP"
fi

exit "$rc"
