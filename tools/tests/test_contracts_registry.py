# -*- coding: utf-8 -*-
"""仓库级契约守卫：代码里出现的 schema 都有登记；所有带 schema 的测试 fixture 都符合登记表。"""
import glob
import json
import os
import re
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "libs", "crypto_core", "src"))
from crypto_core import contracts  # noqa: E402

SCHEMA_LITERAL = re.compile(r"""["']schema["']\s*:\s*["']([a-z_-]+(?:/[a-z0-9_-]+)*/v\d+)["']""")


class ContractsRegistryTest(unittest.TestCase):
    def test_every_schema_literal_is_registered(self):
        found = {}
        for path in glob.glob(os.path.join(REPO, "crypto-*", "scripts", "*.py")):
            with open(path, encoding="utf-8") as f:
                for sid in SCHEMA_LITERAL.findall(f.read()):
                    found.setdefault(sid, os.path.relpath(path, REPO))
        known = set(contracts.CONTRACTS) | contracts.INTERNAL
        missing = {sid: where for sid, where in found.items() if sid not in known}
        self.assertEqual(missing, {}, "新 schema 请登记到 crypto_core.contracts（跨模块）或 INTERNAL（仅模块内）")
        self.assertTrue(set(contracts.CONTRACTS) <= set(found), "登记了但没有生产方：%s" % (set(contracts.CONTRACTS) - set(found)))

    def test_producer_module_matches_literal_location(self):
        for sid, spec in contracts.CONTRACTS.items():
            hits = []
            for path in glob.glob(os.path.join(REPO, spec["module"], "scripts", "*.py")):
                with open(path, encoding="utf-8") as f:
                    if sid in SCHEMA_LITERAL.findall(f.read()):
                        hits.append(path)
            with self.subTest(schema=sid):
                self.assertTrue(hits, "%s 登记的生产方 %s 里找不到该 schema" % (sid, spec["module"]))

    def test_fixtures_with_schema_match_contracts(self):
        for path in glob.glob(os.path.join(REPO, "crypto-*", "tests", "fixtures", "**", "*.json"), recursive=True):
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, ValueError):
                continue
            sid = data.get("schema") if isinstance(data, dict) else None
            if sid in contracts.CONTRACTS:
                with self.subTest(fixture=os.path.relpath(path, REPO)):
                    self.assertEqual(contracts.validate(sid, data), [])


if __name__ == "__main__":
    unittest.main()
