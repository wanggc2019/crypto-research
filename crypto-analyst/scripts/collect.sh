#!/usr/bin/env bash
# crypto-analyst 定时任务入口（launchd 单任务双时间）：
#   每天 10:00 日报（规则）；周日 20:00 周报 + AI 分析与区间报告（run_agent.sh 按星期/日期判断）
# 锁 / start-done 日志 / healthcheck / 每日 prune 由 tools/lib/module_round.sh 统一处理。
source "$(dirname "$0")/../../tools/lib/module_round.sh"

body() {
  ./scripts/run_agent.sh
}

round_main body
