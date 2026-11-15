# -*- coding: utf-8 -*-
"""crypto-policy 产出 policy/daily/v1 符合契约（写到临时目录，不碰生产 exports/）。"""
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import main  # noqa: E402

from crypto_core import contracts  # noqa: E402  (main → common 已把 libs 加入 sys.path)


class PolicyExportContract(unittest.TestCase):
    def test_export_matches_contract(self):
        records = [{"id": "r1", "level": "P1", "title": "关于防范虚拟货币交易炒作风险的通知", "url": "https://example.com/p1",
                    "doc_no": "示例〔2026〕1号", "published_at": "2026-11-08"}]
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(main, "EXPORT_DIR", tmp):
            payload = main._export(records, {"policies": [1, 2]}, "2026-11-08")
            self.assertTrue(os.path.isfile(os.path.join(tmp, "policy-2026-11-08.json")))
        self.assertEqual(contracts.validate("policy/daily/v1", payload), [])


if __name__ == "__main__":
    unittest.main()
