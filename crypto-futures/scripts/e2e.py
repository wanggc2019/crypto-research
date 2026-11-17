#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""e2e.py —— crypto-futures 离线端到端回放（make gate / CI 自动发现；退出码 0 即通过）。

用 tests/fixtures/replay 的真实 5m 数据，在 BNB P0 样例周期刚收盘时（UTC 10-29 21:15）跑一整轮 run.py：
冷启动回补 → 规则 → 卡片 → exports → 30 天缓存与历史参照预热。全程不联网、不推送、写临时目录。
断言：rc=0、出现 BNB P0 拥挤加杠杆卡片、exports 符合 futures/snapshot/v1、last_run.json 健康。
"""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(ROOT, "tests", "fixtures", "replay")
NOW = 1793308500   # 2026-10-29T21:15:00Z：BNB P0 样例那个 5m 周期（21:10Z）刚收盘


def fail(msg, proc=None):
    print("❌ " + msg)
    if proc is not None:
        print(proc.stdout[-1500:] + proc.stderr[-1500:])
    return 1


def main():
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    import common  # noqa: F401  (注入 libs 路径)
    from crypto_core import contracts

    with tempfile.TemporaryDirectory(prefix="futures-e2e-") as tmp:
        cmd = [sys.executable, os.path.join(ROOT, "scripts", "run.py"), "--fixtures", FIXTURES,
               "--now", str(NOW), "--data-dir", tmp, "--json"]
        # 固定 UTC：P0 日上限与「当天首次拉缓存」按本地日判断，任何机器上结果都一样
        env = dict(os.environ, TZ="UTC")
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=180, env=env)
        if proc.returncode != 0:
            return fail("run.py 退出码 %s" % proc.returncode, proc)
        out = json.loads(proc.stdout)
        rules = {(c["card"]["asset"], c["card"]["rule"], c["card"]["severity"]) for c in out["cards"]}
        if ("BNB", "crowding_build", "P0") not in rules:
            return fail("没有出现 BNB P0 拥挤加杠杆卡片：%s" % sorted(rules))
        if any(c["sent"] for c in out["cards"]):
            return fail("回放不应推送")
        with open(os.path.join(tmp, "exports", "latest.json"), encoding="utf-8") as f:
            problems = contracts.validate("futures/snapshot/v1", json.load(f))
        if problems:
            return fail("exports 不符合契约：%s" % problems)
        with open(os.path.join(tmp, "state", "last_run.json"), encoding="utf-8") as f:
            if not json.load(f).get("primary_ok"):
                return fail("last_run.json 不应标记接口失效")
        if not os.path.exists(os.path.join(tmp, "state", "cache30d-BNB.json")):
            return fail("30 天缓存没有生成")
    print("✅ crypto-futures e2e：%d 张卡片，exports 符合契约" % len(out["cards"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
