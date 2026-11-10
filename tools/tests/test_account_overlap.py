#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""账号边界单测：crypto-news 的高管名单与 crypto-x-kol 的 KOL 名单不得重叠。

为什么要有这条测试：
- crypto-news 的 T2「高管发声」与 crypto-x-kol 都通过 r.jina.ai 抓 X 主页；
- 若同一个 handle 出现在两边，同一条推文会被两个模块各推一次（双推）；
- 两个配置都是手改的，靠注释约束不牢，用测试把「不重叠」变成硬约束。

也顺带校验 crypto-x-kol 名单的规模（设计约束 ≤30）与唯一性。
不联网、不读 state/raw，只读两个模块的 config/。
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from crypto_core import config as core_config  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
PEOPLE_YAML = REPO / "crypto-news" / "config" / "people.yaml"
KOL_YAML = REPO / "crypto-x-kol" / "config" / "kol.yaml"
MAX_KOL_ACCOUNTS = 30


class TestKolNewsAccountBoundary(unittest.TestCase):
    def setUp(self):
        self.people = core_config.load_yaml(PEOPLE_YAML) or {}
        self.kol = core_config.load_yaml(KOL_YAML) or {}
        if not self.people or not self.kol:
            self.skipTest("crypto-news/crypto-x-kol 配置缺失，跳过边界检查")
        self.news_handles = {
            str(p.get("x", "")).strip().lstrip("@").lower()
            for p in (self.people.get("people") or [])
            if str(p.get("x", "")).strip()
        }
        self.kol_accounts = list(self.kol.get("accounts") or [])
        self.kol_handles = [
            str(a.get("handle", "")).strip().lstrip("@")
            for a in self.kol_accounts
            if str(a.get("handle", "")).strip()
        ]

    def test_account_sets_are_disjoint(self):
        """crypto-news 高管与 crypto-x-kol 名单不能有交集（含 disabled 账号，防以后打开）。"""
        overlap = sorted(self.news_handles & {h.lower() for h in self.kol_handles})
        self.assertEqual(
            overlap, [],
            "同一 X 账号被两个模块监控会导致双推：%s；请从 kol.yaml 移除，"
            "或从 crypto-news/config/people.yaml 移除" % "、".join(overlap))

    def test_kol_list_size_and_uniqueness(self):
        self.assertLessEqual(len(self.kol_handles), MAX_KOL_ACCOUNTS,
                             "KOL 名单超过 %d 个，匿名 r.jina.ai 的轮询与限流需要重新评估"
                             % MAX_KOL_ACCOUNTS)
        dupes = sorted({h.lower() for h in self.kol_handles
                        if [x.lower() for x in self.kol_handles].count(h.lower()) > 1})
        self.assertEqual(dupes, [], "kol.yaml 里 handle 重复：%s" % "、".join(dupes))

    def test_kol_accounts_have_required_fields(self):
        for item in self.kol_accounts:
            handle = str(item.get("handle", "")).strip()
            if not handle:
                continue
            with self.subTest(handle=handle):
                self.assertTrue(str(item.get("name", "")).strip(), "%s 缺少 name" % handle)
                self.assertTrue(str(item.get("why", "")).strip(), "%s 缺少 why（说明为什么关注）" % handle)
                self.assertIn(str(item.get("tier") or "T2"), ("T1", "T2"), "%s 的 tier 只能是 T1/T2" % handle)


if __name__ == "__main__":
    unittest.main(verbosity=2)
