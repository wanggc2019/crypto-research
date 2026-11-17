#!/usr/bin/env bash
# crypto-futures 定时任务入口（launchd 调用）。
# 锁 / start-done 日志 / healthcheck / 每日 prune / rc 透传由 tools/lib/module_round.sh 统一处理；
# 这里只写本模块一轮的业务步骤：body 最后一条命令的退出码即本轮 rc；
# 需要在 healthcheck/prune 之后做的事写在可选的 after() 里。
source "$(dirname "$0")/../../tools/lib/module_round.sh"

body() {
  ./scripts/run.sh --notify
}

round_main body
