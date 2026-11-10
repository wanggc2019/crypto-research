#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""agent_repair.py —— AI 修复执行器（异步）。

由 `healthcheck --agent` 在发现异常时【后台拉起】，不阻塞采集主任务：
  1. 读 health.json 里「处理前」的 checks / diagnosis
  2. 运行 `<module>/scripts/inspect.sh`（内部 dsh headless，新会话最小上下文）
  3. 复检一次（healthcheck --module X --json）
  4. 统一发一条「修复报告」到 Telegram（恢复 / 仍异常 / 超时都会发）
  5. 结果写回 health.json（last_agent_ts / rc / state_after / tail）
  6. 释放单实例锁

单实例：`data/state/agent.lock`（超过 15 分钟的旧锁自动清理）。
测试：环境变量 `HEALTHCHECK_AGENT_CMD` 可覆盖真正执行的命令（不跑 AI，便于端到端测试）。
"""
import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TOOLS = REPO / "tools"
sys.path.insert(0, str(REPO / "libs" / "crypto_core" / "src"))
sys.path.insert(0, str(TOOLS))

import healthcheck as hc  # noqa: E402


def module_dir(module):
    return REPO / module


def acquire_lock(module, stale_min=15):
    lock = module_dir(module) / "data" / "state" / "agent.lock"
    if lock.exists():
        age_min = (time.time() - lock.stat().st_mtime) / 60.0
        if age_min < stale_min:
            return None
        subprocess.run(["rm", "-rf", str(lock)])
    lock.mkdir(parents=True, exist_ok=True)
    return lock


def run_inspect(module, timeout=600):
    override = os.environ.get("HEALTHCHECK_AGENT_CMD")
    if override:
        cmd = shlex.split(override)
    else:
        cmd = ["/bin/bash", str(module_dir(module) / "scripts" / "inspect.sh")]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=str(REPO))
        lines = (proc.stdout + "\n" + proc.stderr).strip().splitlines()
        return {"rc": proc.returncode, "tail": lines[-3:]}
    except subprocess.TimeoutExpired:
        return {"rc": "timeout", "tail": ["AI 巡检超过 %s 秒，已中止" % timeout]}


def recheck(module):
    proc = subprocess.run(
        [sys.executable, str(TOOLS / "healthcheck.py"), "--module", module, "--json"],
        capture_output=True, text=True, cwd=str(REPO),
    )
    try:
        return json.loads(proc.stdout)
    except ValueError:
        return {"state": "fail", "checks": [], "diagnosis": {"causes": []}}


def main(argv=None):
    parser = argparse.ArgumentParser(description="异步 AI 修复执行器（由 healthcheck --agent 拉起）")
    parser.add_argument("--module", required=True)
    parser.add_argument("--timeout", type=int, default=600, help="AI 巡检超时秒数（默认 600）")
    args = parser.parse_args(argv)

    if not module_dir(args.module).is_dir():
        print("模块不存在：%s" % module_dir(args.module))
        return 2

    lock = acquire_lock(args.module)
    if lock is None:
        print("已有 AI 分析在跑，跳过本次")
        return 0

    try:
        state_path = module_dir(args.module) / "data" / "state" / "health.json"
        try:
            before = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            before = {}
        before_checks = before.get("checks") or []
        diag = before.get("diagnosis") or {"causes": []}

        print("===== %s AI 巡检 start =====" % time.strftime("%F %T"))
        result = run_inspect(args.module, args.timeout)
        after = recheck(args.module)
        hc.notify_repair(args.module, before_checks, diag, result, after.get("checks") or [])

        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}
        state["last_agent_ts"] = time.time()
        state["last_agent_rc"] = result.get("rc")
        state["last_agent_state_after"] = after.get("state")
        state["last_agent_tail"] = result.get("tail")
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
        print("===== %s AI 巡检 done（rc=%s，处理后=%s）=====" % (
            time.strftime("%F %T"), result.get("rc"), after.get("state")))
        return 0
    finally:
        subprocess.run(["rm", "-rf", str(lock)])


if __name__ == "__main__":
    sys.exit(main())
