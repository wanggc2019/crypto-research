# -*- coding: utf-8 -*-
"""crypto-onchain 契约：消费 news/price（标准样例假仓库）+ 产出 onchain/pressure/v1（fixtures 回放快照）。"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(ROOT), "libs", "crypto_core", "src"))
import context  # noqa: E402
import run  # noqa: E402

from crypto_core import contracts  # noqa: E402

DATE = "2026-11-08"


class OnchainContracts(unittest.TestCase):
    def test_consumes_news_and_price(self):
        with tempfile.TemporaryDirectory() as root:
            contracts.write_example_repo(root, DATE)
            self.assertEqual(len(context.load_news(root, DATE)), 3)
            self.assertIn("BTC", context.load_price_export(root)["assets"])
            self.assertEqual(context.load_news(root, "2026-01-01"), [], "缺文件降级为空")

    def test_price_fixture_matches_contract(self):
        with open(os.path.join(ROOT, "tests", "fixtures", "price_export.json"), encoding="utf-8") as f:
            self.assertEqual(contracts.validate("price/snapshot/v1", json.load(f)), [])

    def test_produces_pressure_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "run.py"),
                                   "--fixtures", os.path.join(ROOT, "tests", "fixtures"),
                                   "--data-dir", tmp, "--dry-run", "--json"],
                                  cwd=ROOT, capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr[-1000:])
        snapshot = json.loads(proc.stdout)
        self.assertEqual(contracts.validate("onchain/pressure/v1", run._export_payload(snapshot)), [])


if __name__ == "__main__":
    unittest.main()
