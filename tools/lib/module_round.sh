# shellcheck shell=bash
# tools/lib/module_round.sh —— launchd 定时任务「一轮」的公共骨架（被各模块 scripts/collect.sh source）。
#
# 用法（模块 collect.sh）：
#   #!/usr/bin/env bash
#   source "$(dirname "$0")/../../tools/lib/module_round.sh"
#   body() {                       # 本模块一轮的业务步骤；stdout/stderr 已重定向到 data/logs/collect.log
#     ./scripts/run.sh --notify    # 返回值 = 本轮 rc（透传给 launchd）
#   }
#   round_main body [--no-lock] [--health-args "--agent"]
#
# round_main 统一负责：
#   1. cd 到模块目录，设置 REPO_ROOT / PY / MODULE / CRYPTO_JOB_LABEL（未注入时补默认 Label）
#   2. 单实例锁 data/state/collect.lock（mkdir 原子锁；上一轮未结束 → 记 skip 并 exit 0）
#      --no-lock：锁由业务进程自己管理（如 crypto-price 的 run.py，冲突时退出码 4）
#   3. collect.log 写 `===== <ts> start =====` / `===== <ts> done (rc=N) =====`（healthcheck 依赖此格式）
#   4. 完整一轮之后跑 healthcheck（--quiet --notify --fix [+ --health-args]），输出到 data/logs/healthcheck.out.log
#   5. 当天首轮跑 prune（data/state/.prune-<date> 标记，幂等）
#   6. exit 本轮 rc（healthcheck/prune 失败不影响 rc）
set -uo pipefail

_ROUND_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[1]}")" && pwd)"   # <module>/scripts
cd "$_ROUND_SCRIPT_DIR/.." || exit 1
REPO_ROOT="$(cd .. && pwd)"
MODULE="$(basename "$PWD")"
PY="$REPO_ROOT/.venv/bin/python"
[ -x "$PY" ] || PY=python3
export PYTHONDONTWRITEBYTECODE=1
# launchd 会把 XPC_SERVICE_NAME 覆盖成 0，且旧 plist 可能没注入；这里补默认 Label，保证告警能显示任务名
export CRYPTO_JOB_LABEL="${CRYPTO_JOB_LABEL:-com.crypto-research.${MODULE}}"
export REPO_ROOT MODULE PY

ROUND_LOG="data/logs/collect.log"
ROUND_HEALTH_LOG="data/logs/healthcheck.out.log"

round_ts() { date '+%F %T'; }

round_main() {
  local body_fn="$1"; shift
  local use_lock=1 health_args=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --no-lock) use_lock=0 ;;
      --health-args) health_args="$2"; shift ;;
      *) echo "round_main: unknown option $1" >&2; exit 2 ;;
    esac
    shift
  done

  mkdir -p data/logs data/state
  if [ "$use_lock" = "1" ]; then
    local lockdir="data/state/collect.lock"
    if ! mkdir "$lockdir" 2>/dev/null; then
      echo "$(round_ts) skip: previous run still in progress" >> "$ROUND_LOG"
      exit 0
    fi
    # shellcheck disable=SC2064
    trap "rmdir '$lockdir' 2>/dev/null || true" EXIT
  fi

  local rc=0
  {
    echo "===== $(round_ts) start ====="
    "$body_fn"
    rc=$?
    echo "===== $(round_ts) done (rc=$rc) ====="
  } >> "$ROUND_LOG" 2>&1

  # 健康检查放在完整一轮之后：首轮也有 start/done 可解析，不会误报「找不到完整一轮」
  # shellcheck disable=SC2086
  "$PY" "$REPO_ROOT/tools/healthcheck.py" --module "$MODULE" --quiet --notify --fix $health_args \
    >> "$ROUND_HEALTH_LOG" 2>&1 || true

  # 每日维护（幂等）：保留期与体积上限见 tools/prune.py
  local stamp
  stamp="data/state/.prune-$(date +%F)"
  if [ ! -f "$stamp" ]; then
    echo "--- maintenance $(round_ts) ---" >> "$ROUND_LOG"
    "$PY" "$REPO_ROOT/tools/prune.py" --module "$MODULE" --apply >> "$ROUND_HEALTH_LOG" 2>&1 || true
    touch "$stamp"
  fi

  exit "$rc"
}
