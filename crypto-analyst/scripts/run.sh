#!/usr/bin/env bash
# crypto-analyst 一键运行：更新数据 → 抓宏观（失败不阻塞）→ 生成每日简报。
#
# 用法：
#   ./scripts/run.sh              # 生成 data/reports/<today>-daily-brief.md
#   ./scripts/run.sh --dry-run    # 只打印
#   ./scripts/run.sh --date 2026-11-08
#   ./scripts/run.sh --notify     # 生成并推送 Telegram
#
# 退出码：0 成功 / 1 数据缺失或简报生成失败 / 2 参数错误
set -euo pipefail

cd "$(dirname "$0")/.."
if [ -z "${PYTHON:-}" ]; then
  if [ -x "../.venv/bin/python" ]; then PY="../.venv/bin/python"; else PY="python3"; fi
else
  PY="$PYTHON"
fi

NOTIFY=""
ARGS=()
for arg in "$@"; do
  case "$arg" in
    --notify) NOTIFY=1 ;;
    *) ARGS+=("$arg") ;;
  esac
done

"$PY" scripts/fetch_history.py
if [ "${#ARGS[@]}" -gt 0 ]; then
  "$PY" scripts/daily_brief.py "${ARGS[@]}"   # 日报优先，不等待宏观抓取
else
  "$PY" scripts/daily_brief.py
fi
if [ -n "$NOTIFY" ]; then
  "$PY" scripts/send_brief.py --notify
fi
"$PY" scripts/macro.py >/dev/null 2>&1 || true   # 宏观源可能限流/离线，失败不阻塞日报
