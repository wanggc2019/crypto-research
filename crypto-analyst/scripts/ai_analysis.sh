#!/usr/bin/env bash
# crypto-analyst 周度 AI 综合分析（launchd 每周日 20:15）
# 流程：确保本周周报存在 → dsh headless 读 prompt 生成报告+摘要 → 发送摘要到 Telegram
set -uo pipefail
# launchd 的 PATH 很干净，dsh 在 ~/.local/bin，需要显式补上
export PATH="$HOME/.local/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
cd "$(dirname "$0")/.."
MODULE_DIR="$(pwd)"
REPO_ROOT="$(cd .. && pwd)"
PROMPT_FILE="$REPO_ROOT/tools/agents/analysis-prompt.md"
DSH_BIN="$(command -v dsh || true)"

if [ -z "${PYTHON:-}" ]; then
  if [ -x "$REPO_ROOT/.venv/bin/python" ]; then PY="$REPO_ROOT/.venv/bin/python"; else PY="python3"; fi
else
  PY="$PYTHON"
fi

echo "===== $(date '+%F %T') ai-analysis start ====="
[ -f "$PROMPT_FILE" ] || { echo "缺少 prompt：$PROMPT_FILE"; exit 2; }
[ -n "$DSH_BIN" ] || { echo "未找到 dsh"; exit 2; }

# headless 需要 DEEPSEEK_API_KEY：环境优先，否则从 DSH 凭据库取（与巡检同模式）
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

# 1) 本周周报缺失则先生成（不推送）
WEEK="$(date +%G-W%V)"
if [ ! -f "data/reports/$WEEK-weekly.md" ]; then
  "$PY" scripts/fetch_history.py
  "$PY" scripts/weekly.py || { echo "周报生成失败"; exit 1; }
fi

# 2) 运行 AI 分析（新会话、最小上下文；prompt 从 stdin 传入）
export CRYPTO_JOB_LABEL="${CRYPTO_JOB_LABEL:-crypto-analyst.ai-analysis}"
# 在仓库根执行，prompt 里的相对路径（crypto-analyst/...）才成立
source "$REPO_ROOT/tools/headless_session.sh"
headless_run "$REPO_ROOT" "data/state/headless-session-ai-weekly" "$PROMPT_FILE"
rc=$?
echo "===== $(date '+%F %T') ai-analysis done (rc=$rc) ====="

# 3) 发送摘要（AI 应写入 data/reports/<WEEK>-ai-summary.txt）
SUMMARY="data/reports/$WEEK-ai-summary.txt"
if [ -f "$SUMMARY" ]; then
  "$PY" scripts/send_text.py "$SUMMARY" || echo "摘要发送失败"
else
  echo "未找到摘要文件：$SUMMARY（已跳过发送）"
fi
exit "$rc"
