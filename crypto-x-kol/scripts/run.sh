#!/usr/bin/env bash
# crypto-x-kol 一键入口：抓取 → 生成报告（+ 可选推送）
#   ./scripts/run.sh               抓取 + 写报告，不推送
#   ./scripts/run.sh --notify      抓取 + 写报告 + 推送新帖
#   ./scripts/run.sh --test        发一条测试消息
set -uo pipefail
cd "$(dirname "$0")/.."                 # crypto-x-kol/
REPO_ROOT="$(cd .. && pwd)"             # 仓库根
export PYTHONDONTWRITEBYTECODE=1
PY="$REPO_ROOT/.venv/bin/python"
[ -x "$PY" ] || PY=python3

# 参数拆分：--notify/--test 只给 digest；--only/--limit/--fixtures 只给 fetch；
# 其余（--dry-run/--date/--data-dir/--json）两边都给。
fetch_args=()
digest_args=()
skip_next=0
for arg in "$@"; do
  if [ "$skip_next" = 1 ]; then
    fetch_args+=("$arg")
    skip_next=0
    continue
  fi
  case "$arg" in
    --notify|--test)
      digest_args+=("$arg") ;;
    --only|--limit|--fixtures)
      fetch_args+=("$arg")
      skip_next=1 ;;
    *)
      fetch_args+=("$arg")
      digest_args+=("$arg") ;;
  esac
done

rc=0
if [ "${#fetch_args[@]}" -gt 0 ]; then
  "$PY" scripts/fetch.py "${fetch_args[@]}" || fetch_rc=$?
else
  "$PY" scripts/fetch.py || fetch_rc=$?
fi
[ "${fetch_rc:-0}" -gt "$rc" ] && rc="$fetch_rc"
if [ "${#digest_args[@]}" -gt 0 ]; then
  "$PY" scripts/digest.py "${digest_args[@]}" || digest_rc=$?
else
  "$PY" scripts/digest.py || digest_rc=$?
fi
[ "${digest_rc:-0}" -gt "$rc" ] && rc="$digest_rc"
exit "$rc"
