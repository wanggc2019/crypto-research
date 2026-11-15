#!/usr/bin/env bash
# crypto-jobs 定时任务入口。同一个 launchd 任务有两个触发时间：
#   周日 10:00（上午）→ 周报轮：先采一份最新快照（不推日报），再生成并推送周报
#   每天 18:00（下午）→ 日更轮：采集/快照/差分/日报推送；周日 18:00 额外补跑一次周报
# 锁 / start-done 日志 / healthcheck / 每日 prune 由 tools/lib/module_round.sh 统一处理。
source "$(dirname "$0")/../../tools/lib/module_round.sh"

body() {
  local rc wrc
  if [ "$(date +%u)" = "7" ] && [ "$(date +%H)" -lt 15 ]; then
    ./scripts/run.sh
    rc=$?
    ./scripts/run.sh --weekly --notify
    wrc=$?
  else
    ./scripts/run.sh --notify
    rc=$?
    # 周日 18:00 补跑周报（若上午已推送，会命中同日去重只刷新文件）
    ./scripts/run.sh --weekly-if-due --notify
    wrc=$?
  fi
  echo "weekly rc=$wrc"
  [ "$wrc" -ne 0 ] && rc=$wrc
  return "$rc"
}

round_main body
