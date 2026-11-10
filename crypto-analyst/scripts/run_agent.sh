#!/usr/bin/env bash
# crypto-analyst 单入口（一个 launchd 任务挂两个时间）：
#   每天 10:00      → 日报（规则）
#   周日 20:00      → 周报 + AI 综合分析（AI 失败不影响周报）
set -uo pipefail
export PATH="$HOME/.local/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
cd "$(dirname "$0")/.."
if [ -z "${PYTHON:-}" ]; then
  if [ -x "../.venv/bin/python" ]; then PY="../.venv/bin/python"; else PY="python3"; fi
else
  PY="$PYTHON"
fi

MODE="${1:-auto}"
if [ "$MODE" = "auto" ]; then
  if [ "$(date +%u)" = "7" ] && [ "$(date +%H)" -ge 20 ]; then MODE="weekly"
  elif [ "$(date +%d)" = "01" ] && [ "$(date +%H)" -ge 10 ]; then MODE="monthly"
  else MODE="daily"; fi
fi
echo "===== $(date '+%F %T') analyst start (mode=$MODE) ====="
rc=0

if [ "$MODE" = "monthly" ]; then
  "$PY" scripts/fetch_history.py || rc=1
  MONTHS="${MONTHS:-1}"
  if [ "$(date +%m%d)" = "0101" ]; then
    MONTHS=12                                   # 1 月 1 日：年报
  else
    case "$(date +%m)" in
      01|04|07|10) MONTHS=3 ;;                  # 季度首月：季报
      *) MONTHS=1 ;;                            # 其余月份 1 日：月报
    esac
  fi
  if [ "$MONTHS" = "1" ]; then
    "$PY" scripts/monthly.py --notify || { echo "月报失败"; exit 1; }
  else
    "$PY" scripts/period.py --months "$MONTHS" --notify || { echo "区间报告失败"; rc=1; }
  fi
  bash scripts/ai_period.sh || echo "区间 AI 分析失败（报告已发出）"
elif [ "$MODE" = "weekly" ]; then
  bash scripts/run_weekly.sh || { echo "周报失败"; exit 1; }
  bash scripts/ai_analysis.sh || echo "AI 分析失败（周报已发出，不影响退出码）"
else
  "$PY" scripts/fetch_history.py || rc=1
  "$PY" scripts/daily_brief.py || rc=1
  "$PY" scripts/send_brief.py --notify || rc=1
  "$PY" scripts/macro.py >/dev/null 2>&1 || true
  "$PY" scripts/archive_events.py || true   # 事件月度归档（供月/季/年报）
  bash scripts/ai_daily.sh || echo "AI 日报失败（日报已发出，不影响退出码）"
fi
echo "===== $(date '+%F %T') analyst done (mode=$MODE) ====="
# 把本轮 rc 透传给 launchd（否则脚本最后一句是 echo，失败永远看不到）
exit "$rc"
