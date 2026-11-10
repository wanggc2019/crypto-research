#!/usr/bin/env bash
# crypto-analyst 每日 AI 综合（每天 10:05，紧跟 10:00 日报）：读四维输入 → 生成 AI 日报 → 推送一条
set -uo pipefail
export PATH="$HOME/.local/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
cd "$(dirname "$0")/.."
REPO_ROOT="$(cd .. && pwd)"
PROMPT_FILE="$REPO_ROOT/tools/agents/daily-analysis-prompt.md"
DSH_BIN="$(command -v dsh || true)"
TODAY="$(date +%F)"

if [ -z "${PYTHON:-}" ]; then
  if [ -x "$REPO_ROOT/.venv/bin/python" ]; then PY="$REPO_ROOT/.venv/bin/python"; else PY="python3"; fi
else
  PY="$PYTHON"
fi

echo "===== $(date '+%F %T') ai-daily start ====="
[ -f "$PROMPT_FILE" ] || { echo "缺少 prompt：$PROMPT_FILE"; exit 2; }
[ -n "$DSH_BIN" ] || { echo "未找到 dsh"; exit 2; }

if [ -z "${DEEPSEEK_API_KEY:-}" ] && [ -f "$HOME/.dsh/.credentials.yaml" ]; then
  DEEPSEEK_API_KEY="$("$PY" -c '
import sys
try:
    import yaml
    data = yaml.safe_load(open(sys.argv[1], encoding="utf-8")) or {}
    print((data.get("refs") or {}).get("DEEPSEEK_API_KEY", ""))
except Exception:
    print("")
' "$HOME/.dsh/.credentials.yaml")"
  export DEEPSEEK_API_KEY
fi
[ -n "${DEEPSEEK_API_KEY:-}" ] || { echo "缺少 DEEPSEEK_API_KEY"; exit 2; }

# 1) 当日日报必须存在（正常由 10:00 日报生成；缺失则补生成，不推送）
if [ ! -f "data/reports/$TODAY-daily-brief.md" ]; then
  "$PY" scripts/fetch_history.py
  "$PY" scripts/daily_brief.py || { echo "日报生成失败"; exit 1; }
fi

# 2) AI 综合（新会话，最小上下文，仓库根执行）
export CRYPTO_JOB_LABEL="${CRYPTO_JOB_LABEL:-crypto-analyst.ai-daily}"
source "$REPO_ROOT/tools/headless_session.sh"
headless_run "$REPO_ROOT" "data/state/headless-session-ai-daily" "$PROMPT_FILE"
rc=$?
echo "===== $(date '+%F %T') ai-daily done (rc=$rc) ====="

# 3) 发送摘要（每天一条）
SUMMARY="data/reports/$TODAY-ai-daily-summary.txt"
if [ -f "$SUMMARY" ]; then
  "$PY" scripts/send_text.py "$SUMMARY" || echo "摘要发送失败"
else
  echo "未找到摘要文件：$SUMMARY（已跳过发送）"
fi
exit "$rc"
