#!/usr/bin/env bash
# headless 会话复用：同一自动化任务固定使用一个 DSH 会话，避免每次 headless 都新建会话。
# 用法：在脚本里 source 本文件（需先设置 DSH_BIN），然后：
#   headless_run <repo_root> <sid_file> <prompt_file>
# 首次运行创建会话并把 id 写入 sid_file；之后一直用 --session-id 追加，不再新增。
set -uo pipefail

# 自动化会话使用独立 DSH_HOME（默认 ~/.dsh-automation），不污染 Web 主库
: "${DSH_AUTOMATION_HOME:=$HOME/.dsh-automation}"
if [ -d "$DSH_AUTOMATION_HOME" ]; then
  export DSH_HOME="$DSH_AUTOMATION_HOME"
fi

_sessions_dir() {
  local repo="$1"
  local enc="--$(printf '%s' "$repo" | sed 's|^/||; s|/|-|g')--"
  echo "${DSH_HOME:-$HOME/.dsh}/sessions/$enc"
}

headless_run() {
  local repo="$1" sidf="$2" prompt="$3"
  [ -n "${DSH_BIN:-}" ] || { echo "headless_run: DSH_BIN 未设置" >&2; return 2; }
  [ -f "$prompt" ] || { echo "headless_run: 缺少 prompt $prompt" >&2; return 2; }
  local sdir; sdir="$(_sessions_dir "$repo")"
  mkdir -p "$(dirname "$sidf")"

  if [ -f "$sidf" ]; then
    local sid; sid="$(cat "$sidf" 2>/dev/null)"
    if [ -n "$sid" ] && [ -d "$sdir/$sid" ]; then
      ( cd "$repo" && "$DSH_BIN" headless --session-id "$sid" - < "$prompt" )
      return $?
    fi
    echo "headless_run: 记录的会话 $sid 已不存在，重新创建" >&2
    rm -f "$sidf"
  fi

  # 首次：记录运行前会话清单，运行后取“新增的那个”写回
  local before after new
  before="$(ls -1 "$sdir" 2>/dev/null | sort; :)"
  ( cd "$repo" && "$DSH_BIN" headless - < "$prompt" )
  local rc=$?
  after="$(ls -1 "$sdir" 2>/dev/null | sort; :)"
  new="$(comm -13 <(printf '%s\n' "$before") <(printf '%s\n' "$after") | sed '/^$/d')"
  local count; count="$(printf '%s\n' "$new" | sed '/^$/d' | wc -l | tr -d ' ')"
  if [ "$count" = "1" ]; then
    printf '%s\n' "$new" > "$sidf"
    echo "headless_run: 已锁定会话 $(cat "$sidf")（以后复用，不再新建）" >&2
  else
    echo "headless_run: 新增会话数=$count，未锁定（下次重试）" >&2
  fi
  return "$rc"
}
