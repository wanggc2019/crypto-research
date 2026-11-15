#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""e2e.py —— crypto-analyst 离线端到端回放（make gate / CI 自动发现并执行）。

回放每日简报、周报（tests/fixtures）与复盘打分（--dry-run）。
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
        ("日报回放", ["scripts/daily_brief.py", "--fixtures", "tests/fixtures", "--date", "2026-11-08", "--dry-run"]),
        ("周报回放", ["scripts/weekly.py", "--fixtures", "tests/fixtures", "--date", "2026-11-08", "--dry-run"]),
        ("复盘打分 dry-run", ["scripts/score.py", "--dry-run", "--as-of", "2026-11-08"]),
    ]


def main():
    tmp = tempfile.mkdtemp(prefix="crypto-analyst-e2e-")
    try:
        for name, args in _steps(tmp):
            proc = subprocess.run([PY] + args, cwd=ROOT, capture_output=True, text=True, timeout=300)
            if proc.returncode != 0:
                print("❌ crypto-analyst e2e [%s] rc=%s" % (name, proc.returncode))
                print((proc.stdout or "")[-1500:])
                print((proc.stderr or "")[-1500:])
                return 1
            print("✅ %s" % name)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
