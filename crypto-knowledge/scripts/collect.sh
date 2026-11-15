#!/usr/bin/env bash
# crypto-knowledge 定时任务入口（13:00 / 19:00 / 21:00 / 23:00）：main.py 按当前小时选时段，同日同时段只发一次。
# 锁 / start-done 日志 / healthcheck / 每日 prune 由 tools/lib/module_round.sh 统一处理。
source "$(dirname "$0")/../../tools/lib/module_round.sh"

body() {
  ./scripts/run.sh --notify
  local rc=$?
  # 沿用原有的单行结果摘要（仅供人工看日志；healthcheck 不解析此行）
  echo "fetch: total=1 ok=$([ "$rc" -eq 0 ] && echo 1 || echo 0) fail=$([ "$rc" -eq 0 ] && echo 0 || echo 1)"
  return "$rc"
}

round_main body
