#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""agent_run.py —— 按 config/ai.yaml 的 agents 配置启动一次 headless agent（ADR 0011）。

用法（各模块脚本调用，cwd 任意）：
    .venv/bin/python tools/agent_run.py <agent 功能名> <prompt 文件>          # 真跑；stdout/stderr 原样输出
    .venv/bin/python tools/agent_run.py <agent 功能名> <prompt 文件> --dry-run  # 只打印将执行的命令（不含任何 key）

runner=claude：claude -p，新会话（--no-session-persistence），不读用户级设置/hooks（--setting-sources project,local），
  只放行 allowed_tools，其余工具调用自动拒绝（--permission-prompts none）；
  auth=subscription 用本机 Claude Code 登录（会从环境里去掉 ANTHROPIC_API_KEY，避免被按量计费）；
  auth=api 用 --bare + 根 .env 的 ANTHROPIC_API_KEY。
runner=dsh：沿用 tools/headless_session.sh（同一任务复用一个 DSH 会话），key 读 DEEPSEEK_API_KEY → ~/.dsh 凭据。
退出码：agent 的退出码；配置/环境问题 2；超时 124。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "libs" / "crypto_core" / "src"))

from crypto_core import llm  # noqa: E402

EXTRA_PATH = [str(Path.home() / ".local" / "bin"), "/usr/local/bin", "/opt/homebrew/bin", "/usr/bin", "/bin"]
DSH_CREDENTIALS = Path.home() / ".dsh" / ".credentials.yaml"
# 无论配置怎么写都拒绝：读写凭据文件、上网（agent 只处理本地文件；网页内容可能含注入指令）
ALWAYS_DENY = ["Read(**/.env)", "Read(**/.env.*)", "Edit(**/.env)", "Edit(**/.env.*)", "Bash(cat *.env*)",
               "Bash(*credentials*)", "WebFetch", "WebSearch"]


def _path_env(env):
    parts = [p for p in (env.get("PATH") or "").split(os.pathsep) if p]
    env["PATH"] = os.pathsep.join(parts + [p for p in EXTRA_PATH if p not in parts])
    return env


def claude_command(spec, binary="claude"):
    """claude -p 的参数（prompt 走 stdin）；--disallowedTools/--allowedTools 是可变参数，放最后。"""
    cmd = [binary, "-p", "--no-session-persistence", "--permission-prompts", "none",
           "--setting-sources", "project,local"]
    if spec["auth"] == "api":
        cmd.append("--bare")
    if spec.get("model"):
        cmd += ["--model", spec["model"]]
    if spec.get("effort"):
        cmd += ["--effort", spec["effort"]]
    return cmd + ["--disallowedTools"] + ALWAYS_DENY + ["--allowedTools"] + spec["allowed_tools"]


def claude_env(spec, base_env=None, find_key=llm.find_api_key):
    """subscription：去掉 API key（否则 Claude Code 会改用按量计费）；api：注入 ANTHROPIC_API_KEY（环境变量 → 根 .env）。"""
    env = _path_env(dict(os.environ if base_env is None else base_env))
    if spec["auth"] == "subscription":
        for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
            env.pop(name, None)
        return env, ""
    key = find_key()
    if not key:
        return env, "auth=api 但没有 %s（仓库根 .env）" % llm.API_KEY_ENV
    env[llm.API_KEY_ENV] = key
    return env, ""


def _dsh_key():
    try:
        import yaml

        data = yaml.safe_load(DSH_CREDENTIALS.read_text(encoding="utf-8")) or {}
        return str((data.get("refs") or {}).get("DEEPSEEK_API_KEY") or "").strip()
    except Exception:  # noqa: BLE001 - 凭据缺失/损坏 → 由调用方报错
        return ""


def dsh_command(spec, prompt):
    sidf = "data/state/headless-session-%s" % spec["task"]
    script = 'source "$1" && headless_run "$2" "$3" "$4"'
    return ["/bin/bash", "-c", script, "agent_run", str(REPO / "tools" / "headless_session.sh"),
            str(REPO), sidf, str(prompt)]


def build(task, prompt, cfg=None):
    """返回 (spec, cmd, env, stdin 文件或 None, 错误信息)；错误信息非空时不应运行。"""
    spec = llm.resolve_agent(task, cfg=cfg)
    if spec["runner"] == "claude":
        env, err = claude_env(spec)
        binary = shutil.which("claude", path=env["PATH"])
        if not binary:
            return spec, None, env, None, "找不到 claude 命令（Claude Code 未安装或不在 PATH）"
        return spec, claude_command(spec, binary), env, prompt, err
    env = _path_env(dict(os.environ))
    binary = shutil.which("dsh", path=env["PATH"])
    if not binary:
        return spec, None, env, None, "找不到 dsh 命令"
    env["DSH_BIN"] = binary
    if not env.get("DEEPSEEK_API_KEY"):
        env["DEEPSEEK_API_KEY"] = _dsh_key()
    if not env["DEEPSEEK_API_KEY"]:
        return spec, None, env, None, "runner=dsh 但没有 DEEPSEEK_API_KEY（环境变量或 ~/.dsh/.credentials.yaml refs）"
    return spec, dsh_command(spec, prompt), env, None, ""


def main(argv=None):
    parser = argparse.ArgumentParser(description="按 config/ai.yaml 启动一次 headless agent")
    parser.add_argument("task", help="agents.tasks 里的功能名，如 news.inspect")
    parser.add_argument("prompt", help="prompt 文件路径")
    parser.add_argument("--dry-run", action="store_true", help="只打印命令，不运行")
    args = parser.parse_args(argv)
    prompt = Path(args.prompt).resolve()
    if not prompt.is_file():
        print("agent_run: 缺少 prompt %s" % prompt, file=sys.stderr)
        return 2
    try:
        spec, cmd, env, stdin_path, err = build(args.task, prompt)
    except llm.LLMError as e:
        print("agent_run: AI 配置有误：%s" % e, file=sys.stderr)
        return 2
    if err:
        print("agent_run: %s" % err, file=sys.stderr)
        return 2
    label = "%s/%s%s" % (spec["runner"], spec["auth"] if spec["runner"] == "claude" else "deepseek",
                         (" " + spec["model"]) if spec.get("model") and spec["runner"] == "claude" else "")
    if args.dry_run:
        print(json.dumps({"task": spec["task"], "agent": label, "cmd": cmd,
                          "stdin": str(stdin_path) if stdin_path else None}, ensure_ascii=False, indent=1))
        return 0
    if llm.offline():
        print("agent_run: %s=1（离线模式），不启动 agent" % llm.OFFLINE_ENV, file=sys.stderr)
        return 2
    print("agent_run: %s → %s" % (spec["task"], label), file=sys.stderr)
    stdin = open(stdin_path, "rb") if stdin_path else subprocess.DEVNULL
    try:
        return subprocess.run(cmd, cwd=str(REPO), env=env, stdin=stdin, timeout=spec["timeout_seconds"]).returncode
    except subprocess.TimeoutExpired:
        print("agent_run: 超过 %s 秒，已中止" % spec["timeout_seconds"], file=sys.stderr)
        return 124
    finally:
        if stdin_path:
            stdin.close()


if __name__ == "__main__":
    sys.exit(main())
