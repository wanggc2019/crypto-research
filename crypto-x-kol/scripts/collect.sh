#!/usr/bin/env bash
# crypto-x-kol 定时任务入口（每 5 分钟）：抓取 → 报告 → 推送 → 导出跨模块契约。
# 锁 / start-done 日志 / healthcheck / 每日 prune 由 tools/lib/module_round.sh 统一处理。
# 本模块没有 scripts/inspect.sh，不传 --agent。
source "$(dirname "$0")/../../tools/lib/module_round.sh"

body() {
  ./scripts/run.sh --notify
  local rc=$?                                            # 必须紧跟 run.sh 取值
  "$PY" scripts/export_daily.py >/dev/null 2>&1 || true   # 跨模块只读契约 kol/daily/v1
  return "$rc"
}

round_main body
