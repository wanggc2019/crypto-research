#!/usr/bin/env bash
# crypto-price 定时任务入口（每 60 秒）：采集 → 规则 → 推送。
# 单实例锁在 run.py 内（data/state/collect.lock，冲突退出码 4），所以这里 --no-lock。
# start-done 日志 / healthcheck / 每日 prune 由 tools/lib/module_round.sh 统一处理。
source "$(dirname "$0")/../../tools/lib/module_round.sh"

body() {
  ./scripts/run.sh --notify
}

round_main body --no-lock
