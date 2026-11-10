#!/usr/bin/env bash
# crypto-analyst 周报入口（launchd 每周日 20:00）：更新数据 → 生成周报 → 推送 Telegram → 抓宏观
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -z "${PYTHON:-}" ]; then
  if [ -x "../.venv/bin/python" ]; then PY="../.venv/bin/python"; else PY="python3"; fi
else
  PY="$PYTHON"
fi
"$PY" scripts/fetch_history.py
"$PY" scripts/weekly.py            # 只生成本地周报（不在周报层推送，Telegram 由 AI 摘要一条承担）
"$PY" scripts/macro.py >/dev/null 2>&1 || true
