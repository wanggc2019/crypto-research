#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""crypto-policy 离线单测：列表解析 / 详情解析 / 规则门 / 法规库 / 卡片。"""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import brief  # noqa: E402
import classify  # noqa: E402
import index as policy_index  # noqa: E402

import fetch  # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures")


def read_fixture(name):
    with open(os.path.join(FIX, name), "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def pbc_source():
    return {
        "id": "pbc_tiaofasi",
        "name": "中国人民银行·条法司",
        "tier": "T0",
        "base_url": "https://www.pbc.gov.cn",
        "item_href_re": r"/tiaofasi/144941/3581332/\d{16,}/index\.html",
        "default_doc_type": "normative_doc",
    }


def detail_item():
    detail = fetch.parse_detail(read_fixture("pbc_detail_42.html"))
    return {
        "id": "test42",
        "source_id": "pbc_tiaofasi",
        "source_name": "中国人民银行·条法司",
        "tier": "T0",
        "title": detail["title"],
        "url": "https://www.pbc.gov.cn/tiaofasi/144941/3581332/2026020619591971323/index.html",
        "body": detail["body"],
        "body_hash": "hash",
        "published_at": detail["published_at"],
        "baseline": False,
    }


class TestListParse(unittest.TestCase):
    def test_pbc_list(self):
        items = fetch.parse_list(read_fixture("pbc_tiaofasi_list.html"), pbc_source())
        self.assertGreaterEqual(len(items), 5)
        self.assertTrue(any("虚拟货币" in i["title"] for i in items))
        self.assertTrue(all(i["url"].startswith("https://") for i in items))

    def test_safe_list(self):
        source = {
            "id": "safe_zcfg",
            "base_url": "https://www.safe.gov.cn",
            "item_href_re": r"/safe/20\d{2}/\d{4}/\d+\.html",
        }
        items = fetch.parse_list(read_fixture("safe_zcfg_list.html"), source)
        self.assertGreaterEqual(len(items), 3)
        self.assertTrue(any("外汇" in i["title"] for i in items))


class TestDetailClassify(unittest.TestCase):
    def setUp(self):
        self.rec = classify.build_record(detail_item())

    def test_doc_no(self):
        self.assertIn("银发〔2026〕42号", self.rec["doc_no"])

    def test_doc_type(self):
        self.assertEqual(self.rec["doc_type"], "normative_doc")

    def test_level_p0(self):
        self.assertEqual(self.rec["level"], "P0")

    def test_topics(self):
        self.assertIn("虚拟货币", self.rec["topics"])

    def test_effective(self):
        self.assertEqual(self.rec["effective_at"], "2026-02-06")

    def test_supersedes(self):
        sup = classify.supersedes_of(self.rec)
        self.assertIn("银发〔2021〕237号", sup)


class TestLevelRules(unittest.TestCase):
    def test_law_is_p0(self):
        self.assertEqual(classify.level_for("law", "中华人民共和国XX法", ["虚拟货币"], []), "P0")

    def test_unrelated_is_none(self):
        self.assertIsNone(classify.level_for("news", "某银行召开会议", [], []))

    def test_case_is_p1(self):
        self.assertEqual(classify.level_for("case", "涉虚拟货币典型案例", ["虚拟货币"], []), "P1")


class TestBrief(unittest.TestCase):
    def test_card_has_title_and_link(self):
        rec = classify.build_record(detail_item())
        card = brief.render_card(rec)
        self.assertIn("crypto-policy", card)
        self.assertIn("银发〔2026〕42号", card)
        self.assertIn("查看原文", card)

    def test_pick_points(self):
        rec = classify.build_record(detail_item())
        points = brief.pick_points(rec["body"], rec["topics"])
        self.assertTrue(points)
        self.assertTrue(any("虚拟货币" in p or "禁止" in p or "不得" in p for p in points))


class TestPolicyIndex(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old = policy_index.INDEX_PATH
        policy_index.INDEX_PATH = os.path.join(self.tmp, "policies.json")

    def tearDown(self):
        policy_index.INDEX_PATH = self._old

    def test_supersede_marks_repealed(self):
        idx = policy_index.load()
        old = {
            "id": "old237",
            "doc_no": "银发〔2021〕237号",
            "title": "旧通知",
            "doc_type": "normative_doc",
            "level": "P0",
            "url": "https://example.com/old",
        }
        policy_index.upsert(idx, old)
        new = classify.build_record(detail_item())
        new["supersedes"] = ["银发〔2021〕237号"]
        policy_index.upsert(idx, new)
        policy_index.save(idx)

        saved = policy_index.load()
        old_p = policy_index.find(saved, "银发〔2021〕237号")
        self.assertEqual(old_p["status"], "repealed")
        self.assertEqual(old_p["superseded_by"], "银发〔2026〕42号")
        self.assertIsNotNone(policy_index.find(saved, "银发〔2026〕42号"))


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestForeignPolicy(unittest.TestCase):
    def test_enforcement_p1(self):
        rec = classify.build_record(
            {
                "id": "x1",
                "source_name": "美国 SEC·新闻稿",
                "jurisdiction": "US",
                "title": "SEC Charges Crypto Exchange With Registration Violations",
                "body": "The SEC announced charges against a crypto exchange.",
                "url": "https://www.sec.gov/example",
            }
        )
        self.assertEqual(rec["jurisdiction"], "US")
        self.assertEqual(rec["doc_type"], "enforcement")
        self.assertEqual(rec["level"], "P1")

    def test_rulemaking_p0(self):
        rec = classify.build_record(
            {
                "id": "x2",
                "source_name": "美国 SEC·新闻稿",
                "jurisdiction": "US",
                "title": "SEC Proposes Rule on Digital Asset Custody",
                "body": "The Commission proposes a rule regarding digital asset custody.",
                "url": "https://www.sec.gov/example2",
            }
        )
        self.assertEqual(rec["doc_type"], "rulemaking")
        self.assertEqual(rec["level"], "P0")

    def test_foreign_requires_title_hit(self):
        rec = classify.build_record(
            {
                "id": "x3",
                "source_name": "美联储·新闻稿",
                "jurisdiction": "US",
                "title": "Federal Reserve issues FOMC statement",
                "body": "The Committee discussed stablecoin risks.",
                "url": "https://www.federalreserve.gov/example",
            }
        )
        self.assertIsNone(rec["level"])
        self.assertFalse(classify.is_relevant(rec))


class TestForeignTopicTerms(unittest.TestCase):
    def test_payment_token_guidance(self):
        rec = classify.build_record(
            {
                "id": "y1",
                "source_name": "英国 FCA·新闻",
                "jurisdiction": "UK",
                "title": "FCA publishes guidance for payment token issuers",
                "body": "The FCA set out expectations for payment token issuers.",
                "url": "https://www.fca.org.uk/example",
            }
        )
        self.assertIn("payment token", rec["title_topics"])
        self.assertEqual(rec["doc_type"], "guidance")
        self.assertEqual(rec["level"], "P1")

    def test_vasp_consultation(self):
        rec = classify.build_record(
            {
                "id": "y2",
                "source_name": "新加坡 MAS·新闻",
                "jurisdiction": "SG",
                "title": "MAS consults on VASP licensing requirements",
                "body": "The consultation covers virtual asset service provider licensing.",
                "url": "https://www.mas.gov.sg/example",
            }
        )
        self.assertIn("vasp", rec["title_topics"])
        self.assertEqual(rec["level"], "P1")


class TestRuleSourceHints(unittest.TestCase):
    def test_final_rule_hint_p0(self):
        rec = classify.build_record(
            {
                "id": "z1",
                "source_name": "美国联邦公报·规则",
                "jurisdiction": "US",
                "title": "Implementing the Stablecoins Act for the Issuance of Stablecoins",
                "body": "Final rule.",
                "url": "https://www.federalregister.gov/example",
                "doc_type_hint": "final_rule",
                "doc_no_hint": "2026-21706",
                "issuer_hint": "Treasury Department",
            }
        )
        self.assertEqual(rec["doc_type"], "final_rule")
        self.assertEqual(rec["level"], "P0")
        self.assertEqual(rec["doc_no"], "2026-21706")

    def test_proposed_rule_hint_p1(self):
        rec = classify.build_record(
            {
                "id": "z2",
                "source_name": "美国联邦公报·规则",
                "jurisdiction": "US",
                "title": "Proposed rules for digital asset brokers",
                "body": "Proposed rule.",
                "url": "https://www.federalregister.gov/example2",
                "doc_type_hint": "proposed_rule",
            }
        )
        self.assertEqual(rec["level"], "P1")

    def test_eu_regulation_title(self):
        rec = classify.build_record(
            {
                "id": "z3",
                "source_name": "欧盟 EUR-Lex",
                "jurisdiction": "EU",
                "title": "Regulation (EU) 2023/1114 on markets in crypto-assets",
                "body": "",
                "url": "https://eur-lex.europa.eu/example",
            }
        )
        self.assertEqual(rec["doc_type"], "rulemaking")
        self.assertIn("crypto-assets", rec["title_topics"])
        self.assertEqual(rec["level"], "P0")
