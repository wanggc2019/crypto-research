#!/usr/bin/env bash
# crypto-policy 唯一的 launchd 入口：采集 → 推送 → 有新政策就立即解读 → 每轮自检
set -uo pipefail
cd "$(dirname "$0")/.."
REPO_ROOT="$(cd .. && pwd)"
PY="$REPO_ROOT/.venv/bin/python"
[ -x "$PY" ] || PY=python3
mkdir -p data/logs data/state
LOCKDIR=data/state/collect.lock
if ! mkdir "$LOCKDIR" 2>/dev/null; then
  echo "$(date '+%F %T') skip: previous run still in progress" >> data/logs/collect.log
  exit 0
fi
trap 'rmdir "$LOCKDIR" 2>/dev/null || true' EXIT
{
  echo "===== $(date '+%F %T') start ====="
  ./scripts/run.sh --notify
  rc=$?

  # 事件驱动解读：本轮或此前积压的 pending 非空 → 立即生成并推送解读草稿
  PENDING_COUNT=$(find data/pending -maxdepth 1 -name '*.json' 2>/dev/null | wc -l | tr -d ' ')
  if [ "${PENDING_COUNT:-0}" != "0" ]; then
    echo "$(date '+%F %T') 检测到 $PENDING_COUNT 条待解读，立即触发 AI 解读"
    ./scripts/interpret.sh
  fi

  # 每轮自检（FAIL 才推 TG；与 crypto-news 的做法一致）
  "$PY" "$REPO_ROOT/tools/healthcheck.py" --module crypto-policy --fix --notify --quiet \
    >> data/logs/healthcheck.log 2>&1
  echo "===== $(date '+%F %T') done (rc=$rc) ====="
} >> data/logs/collect.log 2>&1

# 把本轮 rc 透传给 launchd（否则脚本最后一句是 echo，launchd 永远看到 0）
exit "${rc:-0}"
