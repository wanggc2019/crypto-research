# -*- coding: utf-8 -*-
"""crypto_core.contracts：登记表、校验、读取降级、样例仓库。"""
import json
import os
import tempfile
import unittest

from crypto_core import contracts


class ContractsTest(unittest.TestCase):
    def test_examples_satisfy_contracts(self):
        for sid in contracts.CONTRACTS:
            with self.subTest(schema=sid):
                self.assertEqual(contracts.validate(sid, contracts.example(sid)), [])

    def test_validate_reports_problems(self):
        bad = contracts.example("news/daily/v1")
        bad["schema"] = "news/daily/v0"
        del bad["counts"]
        bad["items"][0].pop("severity")
        bad["date"] = 20261108
        problems = " | ".join(contracts.validate("news/daily/v1", bad))
        for needle in ("schema 字段", "缺少字段 counts", "items[0] 缺少 severity", "字段 date 类型为 int"):
            self.assertIn(needle, problems)
        self.assertEqual(contracts.validate("news/daily/v1", []), ["顶层不是对象"])

    def test_map_container_items(self):
        bad = contracts.example("price/snapshot/v1")
        bad["assets"]["BTC"].pop("price")
        self.assertIn("assets[BTC] 缺少 price", contracts.validate("price/snapshot/v1", bad))

    def test_export_path(self):
        p = contracts.export_path("kol/daily/v1", date="2026-11-08", repo="/r")
        self.assertEqual(p, os.path.join("/r", "crypto-x-kol", "data", "exports", "daily-2026-11-08.json"))
        with self.assertRaises(ValueError):
            contracts.export_path("news/daily/v1")
        with self.assertRaises(KeyError):
            contracts.export_path("nope/v1")

    def test_read_degrades(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertIsNone(contracts.read("price/snapshot/v1", repo=root))
            self.assertEqual(contracts.read("price/snapshot/v1", repo=root, default={}), {})
            path = contracts.export_path("price/snapshot/v1", repo=root)
            os.makedirs(os.path.dirname(path))
            with open(path, "w") as f:
                f.write("{broken")
            self.assertIsNone(contracts.read("price/snapshot/v1", repo=root))
            with open(path, "w") as f:
                json.dump({"schema": "price/snapshot/v2", "assets": {}}, f)
            self.assertIsNone(contracts.read("price/snapshot/v1", repo=root), "schema 不符视为不可用")

    def test_example_repo_roundtrip(self):
        with tempfile.TemporaryDirectory() as root:
            contracts.write_example_repo(root, "2026-11-09")
            for sid, spec in contracts.CONTRACTS.items():
                with self.subTest(schema=sid):
                    data = contracts.read(sid, date="2026-11-09", repo=root)
                    self.assertIsNotNone(data)
                    if "date" in data:
                        self.assertEqual(data["date"], "2026-11-09")


if __name__ == "__main__":
    unittest.main()
