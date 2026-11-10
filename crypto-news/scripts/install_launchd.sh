#!/usr/bin/env bash
# 安装/卸载 launchd 定时任务（薄封装，实际逻辑在仓库 tools/render_launchd.py）
# 用法： ./scripts/install_launchd.sh            安装并立即加载
#        ./scripts/install_launchd.sh --uninstall 卸载
#        ./scripts/install_launchd.sh --status    查看状态
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

exec "$PY" "$REPO_ROOT/tools/render_launchd.py" --module "$MODULE" "$ACTION"
