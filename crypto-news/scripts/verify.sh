#!/usr/bin/env bash
# 一键自检：静态检查 + 共享库/模块单测 + 端到端评分（可选网络冒烟）
# 用法： ./scripts/verify.sh            纯本地，不联网
#        ./scripts/verify.sh --with-network   额外跑几个真实源
set -euo pipefail
cd "$(dirname "$0")/.."                 # crypto-news/
REPO_ROOT="$(cd .. && pwd)"             # 仓库根（crypto-news/ 的上一级）
export PYTHONDONTWRITEBYTECODE=1

PY="$REPO_ROOT/.venv/bin/python"
[ -x "$PY" ] || PY=python3
RUFF="$REPO_ROOT/.venv/bin/ruff"
LIBS="$REPO_ROOT/libs/crypto_core/src"

echo "== 1/4 ruff 静态检查 =="
if [ -x "$RUFF" ]; then
  (cd "$REPO_ROOT" && "$RUFF" check libs/crypto_core/src tools crypto-news/scripts crypto-news/tests --no-cache)
else
  echo "（未装 ruff，跳过）"
fi

echo "== 2/4 共享库单测（crypto_core）=="
PYTHONPATH="$LIBS" "$PY" -m unittest discover -s "$REPO_ROOT/libs/crypto_core/tests"

echo "== 3/4 本模块单测 =="
"$PY" -m unittest discover -s tests -v

echo "== 4/4 端到端：对现有数据重新评分 =="
"$PY" scripts/score.py

if [ "${1:-}" = "--with-network" ]; then
  echo "== 5/5 网络冒烟（4 个真实源）=="
  "$PY" scripts/fetch.py --only binance_ann,sec_press,binance_funding_btc,defillama_stablecoins
fi

echo "全部通过 ✅"
