#!/usr/bin/env bash
# crypto-news 定时任务入口（每 10 分钟）：采集 + 评分 + 推送。
# 锁 / start-done 日志 / healthcheck / 每日 prune 由 tools/lib/module_round.sh 统一处理。
source "$(dirname "$0")/../../tools/lib/module_round.sh"

body() {
  ./scripts/run.sh --notify
}

# 仓库级 launchd 任务检查平时由 watchdog 负责；watchdog 心跳过期时由本模块兜底（同批异常 60 分钟内只报一次）
after() {
  round_watchdog_alive || "$PY" "$REPO_ROOT/tools/healthcheck.py" --module all --quiet --notify
}

round_main body --health-args "--agent"
