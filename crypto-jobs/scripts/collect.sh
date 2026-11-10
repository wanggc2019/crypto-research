#!/usr/bin/env bash
# crypto-jobs 定时任务入口：加锁跑一轮日更（采集 → 技能抽取 → 快照/差分 → 日报），
# 然后健康检查 + 每日数据清理（与 crypto-price/crypto-policy 的运维约定一致）。
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

# 同一个 launchd 任务有两个触发时间：
#   周日 10:00（上午）→ 周报轮：先采一份最新快照（不推日报），再生成并推送周报
#   每天 18:00（下午）→ 日更轮：采集/快照/差分/日报推送；周日 18:00 额外补跑一次周报
WEEKLY_MORNING=0
if [ "$(date +%u)" = "7" ] && [ "$(date +%H)" -lt 15 ]; then
  WEEKLY_MORNING=1
fi

{
  echo "===== $(date '+%F %T') start ====="
  if [ "$WEEKLY_MORNING" = "1" ]; then
    mkdir -p data/state
    ./scripts/run.sh
    rc=$?
    ./scripts/run.sh --weekly --notify
    wrc=$?
    echo "weekly rc=$wrc"
    [ "$wrc" -ne 0 ] && rc=$wrc
  else
    ./scripts/run.sh --notify
    rc=$?
    # 周日 18:00 补跑周报（若上午已推送，会命中同日去重只刷新文件）
    ./scripts/run.sh --weekly-if-due --notify
    wrc=$?
    echo "weekly rc=$wrc"
    [ "$wrc" -ne 0 ] && rc=$wrc
  fi
  echo "===== $(date '+%F %T') done (rc=$rc) ====="
} >> data/logs/collect.log 2>&1

# 健康检查：正常零输出；异常才推 Telegram（--fix 允许安全自愈：清锁/重载任务/清理数据）
"$PY" "$REPO_ROOT/tools/healthcheck.py" --module "$MODULE" --quiet --notify --fix \
  >> data/logs/healthcheck.out.log 2>&1 || true

# 每日维护（幂等）：raw 90d / logs 30d / exports 7d / 单日志 2MB / data 总量 500MB 上限
STAMP="data/state/.prune-$(date +%F)"
if [ ! -f "$STAMP" ]; then
  "$PY" "$REPO_ROOT/tools/prune.py" --module "$MODULE" --apply \
    >> data/logs/healthcheck.out.log 2>&1 || true
  touch "$STAMP"
fi

exit "$rc"
