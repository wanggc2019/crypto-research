#!/usr/bin/env bash
# crypto-policy 唯一的 launchd 入口（每 1 小时）：采集 → 推送 → 有新政策就立即解读。
# 锁 / start-done 日志 / healthcheck / 每日 prune 由 tools/lib/module_round.sh 统一处理。
source "$(dirname "$0")/../../tools/lib/module_round.sh"

body() {
  ./scripts/run.sh --notify
  local rc=$?
  # 事件驱动解读：本轮或此前积压的 pending 非空 → 立即生成并推送解读草稿
  local pending
  pending=$(find data/pending -maxdepth 1 -name '*.json' 2>/dev/null | wc -l | tr -d ' ')
  if [ "${pending:-0}" != "0" ]; then
    echo "$(round_ts) 检测到 $pending 条待解读，立即触发 AI 解读"
    ./scripts/interpret.sh
  fi
  return "$rc"
}

round_main body
