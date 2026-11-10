#!/usr/bin/env bash
# crypto-x-kol 定时任务入口：加锁跑一轮（抓取 → 报告 → 推送），并做每轮自检与每日维护。
# 日志格式与 crypto-news 对齐，复用 tools/healthcheck.py 的 start/done/「采集完成」解析。
set -uo pipefail
cd "$(dirname "$0")/.."                 # crypto-x-kol/
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
  ./scripts/run.sh --notify
  rc=$?                                                  # 必须紧跟 run.sh 取值（export_daily 的 || true 会覆盖 $?）
  "$PY" scripts/export_daily.py >/dev/null 2>&1 || true   # 跨模块只读契约 kol/daily/v1

  # 每轮顺带健康检查（--quiet 正常零输出；异常才告警/自愈）
  # 本模块没有 scripts/inspect.sh，不传 --agent（否则是静默 no-op 的假开关）
  "$PY" "$REPO_ROOT/tools/healthcheck.py" --module "$MODULE" --quiet --notify --fix || true

  # 每日维护（幂等）：raw 90d / logs 30d / reports 365d / 单日志 2MB / data 500MB 上限
  STAMP="data/state/.prune-$(date +%F)"
  if [ ! -f "$STAMP" ]; then
    echo "--- maintenance $(date '+%F %T') ---"
    "$PY" "$REPO_ROOT/tools/prune.py" --module "$MODULE" --apply || true
    touch "$STAMP"
  fi

  echo "===== $(date '+%F %T') done (rc=$rc) ====="
} >> data/logs/collect.log 2>&1

# 把本轮 rc 透传给 launchd（否则脚本最后一句是 echo，launchd 永远看到 0）
exit "${rc:-0}"
