#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""e2e.py —— crypto-price 离线端到端回放（make gate / CI 自动发现并执行）。

回放 tests/fixtures 的正常/冲击两个行情快照，跑完整规则链路（--dry-run --json）。
全部离线、不推送；需要写盘的步骤使用临时 data 目录，不碰生产 data/。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                                "libs", "crypto_core", "src"))
from crypto_core import contracts  # noqa: E402

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
        # 契约：非 dry-run（不带 --notify → 不推送）把 exports 写进临时 data 目录，再按登记表校验
        live = os.path.join(tmp, "contract")
        proc = subprocess.run([PY, "scripts/run.py", "--snapshot", "tests/fixtures/snapshot_shock.json",
                               "--data-dir", live, "--json"], cwd=ROOT, capture_output=True, text=True, timeout=300)
        if proc.returncode != 0:
            print("❌ crypto-price e2e [契约导出] rc=%s\n%s" % (proc.returncode, (proc.stderr or "")[-1500:]))
            return 1
        exports = os.path.join(live, "exports")
        daily = sorted(f for f in os.listdir(exports) if f.startswith("daily-")) if os.path.isdir(exports) else []
        if not daily:
            print("❌ crypto-price e2e 契约：%s 下没有 daily-*.json 导出" % exports)
            return 1
        for schema_id, name in (("price/snapshot/v1", "latest.json"), ("price/daily/v1", daily[-1])):
            try:
                with open(os.path.join(exports, name), encoding="utf-8") as f:
                    problems = contracts.validate(schema_id, json.load(f))
            except (OSError, ValueError) as e:
                problems = ["读不到 %s：%s" % (name, e)]
            if problems:
                print("❌ crypto-price e2e 契约 %s：%s" % (schema_id, problems))
                return 1
        print("✅ 契约导出（price/snapshot/v1 + price/daily/v1）")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
