#!/usr/bin/env bash
# crypto-onchain 定时任务入口（每 5 分钟）：一轮采集/归因/告警 → 21:00 后首轮推每日摘要。
# 锁 / start-done 日志 / healthcheck / 每日 prune 由 tools/lib/module_round.sh 统一处理。
# 本模块无 inspect.sh，不传 --agent。
source "$(dirname "$0")/../../tools/lib/module_round.sh"

body() {
  ./scripts/run.sh
}

# 每日摘要（21:00 后首个轮次；在 healthcheck/prune 之后，失败不影响本轮 rc）
after() {
  "$PY" scripts/digest.py --if-due --notify
}

round_main body
