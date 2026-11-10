#!/usr/bin/env bash
# 安装/卸载 crypto-price launchd 定时任务（60 秒一轮；薄封装 tools/render_launchd.py）
#   ./scripts/install_launchd.sh            安装并立即加载
#   ./scripts/install_launchd.sh --status   查看状态
#   ./scripts/install_launchd.sh --uninstall
set -euo pipefail
MODULE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
REPO_ROOT="$(cd "$MODULE_DIR/.." && pwd)"
MODULE="$(basename "$MODULE_DIR")"

PY="$REPO_ROOT/.venv/bin/python"
[ -x "$PY" ] || PY=python3

case "${1:-}" in
  --uninstall) ACTION="--uninstall" ;;
  --status)    ACTION="--status" ;;
  "")          ACTION="--install" ;;
  *) echo "用法：$0 [--status|--uninstall]"; exit 2 ;;
esac

exec "$PY" "$REPO_ROOT/tools/render_launchd.py" --module "$MODULE" --interval 60 "$ACTION"
