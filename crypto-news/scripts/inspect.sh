#!/usr/bin/env bash
# 自动巡检入口：用 dsh headless 起一个「新会话、最小上下文」的 agent 做健康检查/诊断/自愈
# 由 launchd 任务 com.crypto-research.crypto-news.inspect 每 4 小时触发
set -uo pipefail
cd "$(dirname "$0")/.."                 # crypto-news/
REPO_ROOT="$(cd .. && pwd)"             # 仓库根
PROMPT_FILE="$REPO_ROOT/tools/agents/inspect-prompt.md"
export PATH="$HOME/.local/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

# headless 需要 DEEPSEEK_API_KEY：优先用启动环境变量；否则从 DSH 凭据库（~/.dsh/.credentials.yaml 的 refs）取
if [ -z "${DEEPSEEK_API_KEY:-}" ] && [ -f "$HOME/.dsh/.credentials.yaml" ] && [ -x "$REPO_ROOT/.venv/bin/python" ]; then
  DEEPSEEK_API_KEY="$("$REPO_ROOT/.venv/bin/python" -c '
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

DSH_BIN="$(command -v dsh || true)"
[ -n "$DSH_BIN" ] || { echo "$(date '+%F %T') dsh 未找到，跳过巡检"; exit 1; }
[ -f "$PROMPT_FILE" ] || { echo "$(date '+%F %T') 缺少巡检 prompt：$PROMPT_FILE"; exit 1; }
if [ -z "${DEEPSEEK_API_KEY:-}" ]; then
  echo "$(date '+%F %T') 缺少 DEEPSEEK_API_KEY（可在启动环境 export，或确认 ~/.dsh/.credentials.yaml 里有 refs.DEEPSEEK_API_KEY）"
  exit 2
fi

echo "===== $(date '+%F %T') inspect start ====="
cd "$REPO_ROOT"
source "$REPO_ROOT/tools/headless_session.sh"
headless_run "$REPO_ROOT" "data/state/headless-session-inspect" "$PROMPT_FILE"
rc=$?
echo "===== $(date '+%F %T') inspect done (rc=$rc) ====="
exit "$rc"
