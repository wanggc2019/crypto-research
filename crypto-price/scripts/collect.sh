#!/usr/bin/env bash
# crypto-price 定时任务入口（launchd 每 60 秒调用）：采集 → 规则 → 推送，然后健康检查 + 每日清理。
# 单实例锁在 run.py 内（data/state/collect.lock），上一轮未结束会跳过（退出码 4）。
set -uo pipefail
cd "$(dirname "$0")/.."
REPO_ROOT="$(cd .. && pwd)"
export PYTHONDONTWRITEBYTECODE=1
PY="$REPO_ROOT/.venv/bin/python"
[ -x "$PY" ] || PY=python3
MODULE="$(basename "$PWD")"
# launchd 的 XPC_SERVICE_NAME 会被覆盖成 0，且已装 plist 可能没重载；
# 这里显式导出 CRYPTO_JOB_LABEL，保证告警/日报/健康检查都能显示完整 Label。
export CRYPTO_JOB_LABEL="com.crypto-research.${MODULE}"

mkdir -p data/logs data/state
{
  echo "===== $(date '+%F %T') start ====="
  ./scripts/run.sh --notify
  rc=$?
  echo "===== $(date '+%F %T') done (rc=$rc) ====="
} >> data/logs/collect.log 2>&1

# 健康检查放在完整一轮之后：首轮也有 start/done 可解析，不会误报"找不到完整一轮"。
# --quiet 正常零输出；异常才推 Telegram + 自愈（清锁/重载任务/清理数据）。
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
