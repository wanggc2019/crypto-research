#!/usr/bin/env bash
# 自动巡检入口：起一个「新会话、最小上下文」的 headless agent 做健康检查/诊断/有限自愈。
# 用哪个 AI、放行哪些命令：config/ai.yaml 的 agents.tasks.news.inspect（tools/agent_run.py 启动）。
# 由 tools/agent_repair.py 在 healthcheck 发现 FAIL 时异步调用。
set -uo pipefail
cd "$(dirname "$0")/.."                 # crypto-news/
REPO_ROOT="$(cd .. && pwd)"             # 仓库根
PY="$REPO_ROOT/.venv/bin/python"
[ -x "$PY" ] || PY=python3
PROMPT_FILE="$REPO_ROOT/tools/agents/inspect-prompt.md"
export PATH="$HOME/.local/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

echo "===== $(date '+%F %T') inspect start ====="
"$PY" "$REPO_ROOT/tools/agent_run.py" news.inspect "$PROMPT_FILE"
rc=$?
echo "===== $(date '+%F %T') inspect done (rc=$rc) ====="
exit "$rc"
