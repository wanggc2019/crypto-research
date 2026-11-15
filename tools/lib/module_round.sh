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
#   body 最后一条命令的退出码即本轮 rc；有多条命令时用 `local rc=$?` 紧跟主命令取值再 `return "$rc"`。
#   可选 after()：在 healthcheck + prune 之后执行（输出进 collect.log，失败不影响 rc），
#   用于依赖 healthcheck --fix 结果的步骤（如仓库级 launchd 检查、每日摘要）。
#
# round_main 统一负责：
#   1. cd 到模块目录，设置 REPO_ROOT / PY / MODULE / CRYPTO_JOB_LABEL（未注入时补默认 Label）
#   2. 单实例锁 data/state/collect.lock（mkdir 原子锁；上一轮未结束 → 记 skip 并 exit 0）
#      --no-lock：锁由业务进程自己管理（如 crypto-price 的 run.py，冲突时退出码 4）
#   3. collect.log 写 `===== <ts> start =====` / `===== <ts> done (rc=N) =====`（healthcheck 依赖此格式）
#   4. 健康检查由仓库级 watchdog（每 5 分钟）统一负责；只有 watchdog 心跳
#      data/state/watchdog.json 超过 ROUND_WATCHDOG_STALE_MIN（默认 15）分钟未更新时，
#      本轮才兜底自查（healthcheck --quiet --notify --fix [+ --health-args]，输出到 data/logs/healthcheck.out.log）
#   5. 当天首轮跑 prune（data/state/.prune-<date> 标记，幂等）
#   6. 若定义了 after()，执行之
#   7. exit 本轮 rc（healthcheck/prune/after 失败不影响 rc）
#   --health-args 只接受空格分隔的简单 flag（不做引号解析）。
set -uo pipefail

# 调用方（<module>/scripts/collect.sh）的位置决定模块目录；定位后立即自检，调用方式不对就大声失败，
# 绝不静默在错误目录里加锁/写日志。
_ROUND_CALLER="${BASH_SOURCE[1]:-}"
_ROUND_SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
if [ -z "$_ROUND_CALLER" ] || ! cd "$(dirname "$_ROUND_CALLER")/.." 2>/dev/null; then
  echo "module_round.sh: 必须由 <repo>/crypto-*/scripts/collect.sh 以 source 方式加载" >&2
  exit 2
fi
REPO_ROOT="$(cd .. && pwd)"
MODULE="$(basename "$PWD")"
case "$MODULE" in
  crypto-*) ;;
  *) echo "module_round.sh: 模块目录名应为 crypto-*，实际 $PWD" >&2; exit 2 ;;
esac
if [ "$REPO_ROOT/tools/lib/module_round.sh" != "$_ROUND_SELF" ]; then
  echo "module_round.sh: 调用方 $PWD 与骨架 $_ROUND_SELF 不在同一仓库" >&2
  exit 2
fi
PY="$REPO_ROOT/.venv/bin/python"
[ -x "$PY" ] || PY=python3
export PYTHONDONTWRITEBYTECODE=1
# launchd 会把 XPC_SERVICE_NAME 覆盖成 0，且旧 plist 可能没注入；这里补默认 Label，保证告警能显示任务名
export CRYPTO_JOB_LABEL="${CRYPTO_JOB_LABEL:-com.crypto-research.${MODULE}}"
export REPO_ROOT MODULE PY

ROUND_LOG="data/logs/collect.log"
ROUND_WATCHDOG_STATE="$REPO_ROOT/data/state/watchdog.json"
ROUND_WATCHDOG_STALE_MIN="${ROUND_WATCHDOG_STALE_MIN:-15}"
ROUND_HEALTH_LOG="data/logs/healthcheck.out.log"

round_ts() { date '+%F %T'; }

# watchdog 心跳新鲜 → 0；缺失/过期 → 1（find -mmin 在 BSD/GNU 上都可用）
round_watchdog_alive() {
  [ -f "$ROUND_WATCHDOG_STATE" ] && [ -n "$(find "$ROUND_WATCHDOG_STATE" -mmin "-$ROUND_WATCHDOG_STALE_MIN" 2>/dev/null)" ]
}

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

  # 健康检查：watchdog 活着就交给它（避免每轮重复检查/重复告警）；watchdog 挂了才兜底自查。
  # 放在完整一轮之后：首轮也有 start/done 可解析，不会误报「找不到完整一轮」
  if ! round_watchdog_alive; then
    # shellcheck disable=SC2086
    "$PY" "$REPO_ROOT/tools/healthcheck.py" --module "$MODULE" --quiet --notify --fix $health_args \
      >> "$ROUND_HEALTH_LOG" 2>&1 || true
  fi

  # 每日维护（幂等）：保留期与体积上限见 tools/prune.py
  local stamp
  stamp="data/state/.prune-$(date +%F)"
  if [ ! -f "$stamp" ]; then
    echo "--- maintenance $(round_ts) ---" >> "$ROUND_LOG"
    "$PY" "$REPO_ROOT/tools/prune.py" --module "$MODULE" --apply >> "$ROUND_HEALTH_LOG" 2>&1 || true
    touch "$stamp"
  fi

  if declare -F after >/dev/null; then
    after >> "$ROUND_LOG" 2>&1 || true
  fi

  exit "$rc"
}
