#!/usr/bin/env bash
# crypto-policy AI 解读：事件驱动，由 collect.sh 在检测到新政策（pending 非空）后立即调用。
# pending 为空 / 静默时段 → 直接退出，零成本。
set -uo pipefail
cd "$(dirname "$0")/.."                 # crypto-policy/
REPO_ROOT="$(cd .. && pwd)"             # 仓库根
PY="$REPO_ROOT/.venv/bin/python"
[ -x "$PY" ] || PY=python3
PROMPT_FILE="$REPO_ROOT/crypto-policy/scripts/interpret-prompt.md"
export PATH="$HOME/.local/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

# pending 为空 → 不启动 model（零成本）
PENDING_COUNT=$(find "$REPO_ROOT/crypto-policy/data/pending" -maxdepth 1 -name '*.json' 2>/dev/null | wc -l | tr -d ' ')
if [ "${PENDING_COUNT:-0}" = "0" ]; then
  echo "$(date '+%F %T') pending 为空，跳过解读"
  exit 0
fi

# 干跑：只验证链路，不调用模型/不推送
if [ "${INTERPRET_DRY_RUN:-0}" = "1" ]; then
  echo "$(date '+%F %T') would run dsh headless (pending=$PENDING_COUNT)"
  exit 0
fi

# headless 需要 DEEPSEEK_API_KEY：优先环境变量；否则从 DSH 凭据库（~/.dsh/.credentials.yaml refs）取
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
[ -n "$DSH_BIN" ] || { echo "$(date '+%F %T') dsh 未找到，跳过解读"; exit 1; }
[ -f "$PROMPT_FILE" ] || { echo "$(date '+%F %T') 缺少解读 prompt：$PROMPT_FILE"; exit 1; }
if [ -z "${DEEPSEEK_API_KEY:-}" ]; then
  echo "$(date '+%F %T') 缺少 DEEPSEEK_API_KEY（~/.dsh/.credentials.yaml refs.DEEPSEEK_API_KEY）"
  exit 2
fi

echo "===== $(date '+%F %T') interpret start (pending=$PENDING_COUNT) ====="
cd "$REPO_ROOT"
source "$REPO_ROOT/tools/headless_session.sh"
headless_run "$REPO_ROOT" "data/state/headless-session-interpret" "$PROMPT_FILE"
rc=$?
echo "===== $(date '+%F %T') interpret done (rc=$rc) ====="
exit "$rc"
