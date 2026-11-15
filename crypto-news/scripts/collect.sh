#!/usr/bin/env bash
# crypto-news 定时任务入口（每 10 分钟）：采集 + 评分 + 推送。
# 锁 / start-done 日志 / healthcheck / 每日 prune 由 tools/lib/module_round.sh 统一处理。
source "$(dirname "$0")/../../tools/lib/module_round.sh"

body() {
  ./scripts/run.sh --notify
  local rc=$?
  # 仓库级 launchd 任务健康（"任务挂了"自动报警；同一批异常 60 分钟内只报一次）
  "$PY" "$REPO_ROOT/tools/healthcheck.py" --module all --quiet --notify || true
  return "$rc"
}

round_main body --health-args "--agent"
