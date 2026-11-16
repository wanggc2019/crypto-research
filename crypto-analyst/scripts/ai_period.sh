#!/usr/bin/env bash
# 月/季/年 AI 分析：生成区间报告后调用（AI 失败不影响报告）
# 用哪个 AI、放行哪些命令：config/ai.yaml 的 agents.tasks.analyst.period
set -uo pipefail
export PATH="$HOME/.local/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
cd "$(dirname "$0")/.."
REPO_ROOT="$(cd .. && pwd)"
PROMPT="$REPO_ROOT/tools/agents/period-analysis-prompt.md"
[ -f "$PROMPT" ] || { echo "缺少 prompt"; exit 2; }
if [ -z "${PYTHON:-}" ]; then
  if [ -x "$REPO_ROOT/.venv/bin/python" ]; then PY="$REPO_ROOT/.venv/bin/python"; else PY="python3"; fi
else PY="$PYTHON"; fi
export CRYPTO_JOB_LABEL="${CRYPTO_JOB_LABEL:-crypto-analyst.ai-period}"
"$PY" "$REPO_ROOT/tools/agent_run.py" analyst.period "$PROMPT"; rc=$?
cat data/reports/*-ai-period-summary.txt 2>/dev/null | tail -40 > /tmp/period_summary.txt
[ -s /tmp/period_summary.txt ] && "$PY" scripts/send_text.py /tmp/period_summary.txt || echo "无摘要，跳过发送"
exit "$rc"
