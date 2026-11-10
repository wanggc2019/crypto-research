#!/usr/bin/env bash
# 一键自检：ruff + 共享库单测 + 本模块单测 + 离线 fixtures 端到端（可选联网冒烟）
#   ./scripts/verify.sh                纯本地，不联网
#   ./scripts/verify.sh --with-network 额外跑一次真实采集（只写临时目录）
set -euo pipefail
cd "$(dirname "$0")/.."
REPO_ROOT="$(cd .. && pwd)"
export PYTHONDONTWRITEBYTECODE=1
PY="$REPO_ROOT/.venv/bin/python"
[ -x "$PY" ] || PY=python3
RUFF="$REPO_ROOT/.venv/bin/ruff"
LIBS="$REPO_ROOT/libs/crypto_core/src"

echo "== 1/4 ruff 静态检查 =="
if [ -x "$RUFF" ]; then
  (cd "$REPO_ROOT" && "$RUFF" check libs/crypto_core/src crypto-onchain/scripts crypto-onchain/tests --no-cache)
else
  echo "（未装 ruff，跳过）"
fi

echo "== 2/4 共享库单测（crypto_core）=="
PYTHONPATH="$LIBS" "$PY" -m unittest discover -s "$REPO_ROOT/libs/crypto_core/tests"

echo "== 3/4 crypto-onchain 单测 =="
"$PY" -m unittest discover -s tests -v

echo "== 4/4 离线端到端（fixtures 回放，不联网）=="
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
"$PY" scripts/run.py --fixtures tests/fixtures --data-dir "$TMP" --json >/dev/null

if [ "${1:-}" = "--with-network" ]; then
  echo "== 5/5 联网冒烟（真实免费源，只预览）=="
  TMP2="$(mktemp -d)"
  "$PY" scripts/run.py --data-dir "$TMP2" --dry-run
  rm -rf "$TMP2"
fi

echo "全部通过 ✅"
