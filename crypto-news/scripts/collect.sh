#!/usr/bin/env bash
# 定时任务入口：采集 + 评分 + 推送（带锁，防止上一轮没跑完又启动）
# 另外每天自动跑一次数据维护：raw 90d / logs 30d / exports 7d / 单日志 2MB 上限。
set -uo pipefail
cd "$(dirname "$0")/.."                 # crypto-news/
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
  rc=$?

  # 每轮顺带健康检查（--quiet 正常零输出；异常才告警/自愈/按需 AI 分析）
  "$PY" "$REPO_ROOT/tools/healthcheck.py" --module "$MODULE" --quiet --notify --fix --agent || true

  # 仓库级 launchd 任务健康（"任务挂了"自动报警；同一批异常 60 分钟内只报一次）
  "$PY" "$REPO_ROOT/tools/healthcheck.py" --module all --quiet --notify || true

  # 每日维护（幂等）：日志按数量+体积双重兜底，raw/exports 按保留期清理
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
