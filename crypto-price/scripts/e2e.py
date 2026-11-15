#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""e2e.py —— crypto-price 离线端到端回放（make gate / CI 自动发现并执行）。

回放 tests/fixtures 的正常/冲击两个行情快照，跑完整规则链路（--dry-run --json）。
全部离线、不推送；需要写盘的步骤使用临时 data 目录，不碰生产 data/。
"""
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable


def _steps(tmp):
    return [
        ("snapshot_%s 回放" % name, [
            "scripts/run.py", "--snapshot", "tests/fixtures/snapshot_%s.json" % name,
            "--data-dir", tmp, "--dry-run", "--json",
        ])
        for name in ("normal", "shock")
    ]


def main():
    tmp = tempfile.mkdtemp(prefix="crypto-price-e2e-")
    try:
        for name, args in _steps(tmp):
            proc = subprocess.run([PY] + args, cwd=ROOT, capture_output=True, text=True, timeout=300)
            if proc.returncode != 0:
                print("❌ crypto-price e2e [%s] rc=%s" % (name, proc.returncode))
                print((proc.stdout or "")[-1500:])
                print((proc.stderr or "")[-1500:])
                return 1
            print("✅ %s" % name)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
