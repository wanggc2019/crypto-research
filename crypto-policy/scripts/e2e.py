#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""e2e.py —— 离线端到端回放（CI 用，禁止联网）。

跑两件事：
1. main.py --fixtures tests/fixtures --dry-run：列表解析 → 规则门 → 卡片，退出码 0；
2. weekly.py --dry-run：周报生成，退出码 0。

只读 fixtures、不写生产 data、不推送。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures")
PY = sys.executable


def run(args):
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, env=env, timeout=180)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def main():
    rc, out = run([PY, "scripts/main.py", "--fixtures", FIX, "--dry-run", "--date", "2026-11-08"])
    if rc != 0:
        print("❌ fixtures 回放失败（rc=%s）\n%s" % (rc, out[-1500:]))
        return 1
    if "新条目" not in out:
        print("❌ 回放没有产出速报摘要\n%s" % out[-1500:])
        return 1

    rc, out = run([PY, "scripts/weekly.py", "--dry-run"])
    if rc != 0 or "政策周报" not in out:
        print("❌ 周报回放失败（rc=%s）\n%s" % (rc, out[-1500:]))
        return 1

    print("✅ crypto-policy e2e 通过（fixtures 回放 + 周报）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
