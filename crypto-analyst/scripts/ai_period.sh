#!/usr/bin/env bash
# 月/季/年 AI 分析：生成区间报告后调用（AI 失败不影响报告）
set -uo pipefail
export PATH="$HOME/.local/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
cd "$(dirname "$0")/.."
REPO_ROOT="$(cd .. && pwd)"
PROMPT="$REPO_ROOT/tools/agents/period-analysis-prompt.md"
DSH="$(command -v dsh || true)"; [ -n "$DSH" ] || { echo "未找到 dsh"; exit 2; }
[ -f "$PROMPT" ] || { echo "缺少 prompt"; exit 2; }
if [ -z "${PYTHON:-}" ]; then
  if [ -x "$REPO_ROOT/.venv/bin/python" ]; then PY="$REPO_ROOT/.venv/bin/python"; else PY="python3"; fi
else PY="$PYTHON"; fi
if [ -z "${DEEPSEEK_API_KEY:-}" ] && [ -f "$HOME/.dsh/.credentials.yaml" ]; then
  DEEPSEEK_API_KEY="$("$PY" -c 'import sys
try:
    import yaml
    d=yaml.safe_load(open(sys.argv[1],encoding="utf-8")) or {}
    print((d.get("refs") or {}).get("DEEPSEEK_API_KEY",""))
except Exception: print("")' "$HOME/.dsh/.credentials.yaml")"
  export DEEPSEEK_API_KEY
fi
export CRYPTO_JOB_LABEL="${CRYPTO_JOB_LABEL:-crypto-analyst.ai-period}"
source "$REPO_ROOT/tools/headless_session.sh"
DSH_BIN="$DSH"; headless_run "$REPO_ROOT" "data/state/headless-session-ai-period" "$PROMPT"; rc=$?
cat data/reports/*-ai-period-summary.txt 2>/dev/null | tail -40 > /tmp/period_summary.txt
[ -s /tmp/period_summary.txt ] && "$PY" scripts/send_text.py /tmp/period_summary.txt || echo "无摘要，跳过发送"
exit "$rc"
