#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""crypto-policy 解读/代理抓取离线单测。"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import brief  # noqa: E402
import classify  # noqa: E402
import interpret  # noqa: E402

import fetch  # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures")


def read_fixture(name):
    with open(os.path.join(FIX, name), "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def rec_42():
    detail = fetch.parse_detail(read_fixture("pbc_detail_42.html"))
    item = {
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
    rec = classify.build_record(item)
    rec["supersedes"] = classify.supersedes_of(rec)
    return rec


class TestRuleDraft(unittest.TestCase):
    def test_summary_and_impact(self):
        draft = interpret._rule_draft(rec_42())
        self.assertIn("银发〔2026〕42号", draft["summary"])
        self.assertTrue(draft["key_points"])
        self.assertTrue(any("虚拟货币" in x for x in draft["impact"]))
        self.assertIn("银发〔2021〕237号", " ".join(draft["changes"]))

    def test_markdown_sections(self):
        md = interpret._render_md(rec_42(), interpret._rule_draft(rec_42()))
        for section in ("## 一句话", "## 原文要点", "## 与旧规的关系", "## 影响", "## 风险提示"):
            self.assertIn(section, md)

    def test_card(self):
        draft = interpret._rule_draft(rec_42())
        card = brief.render_interpret_card(rec_42(), draft)
        self.assertIn("解读草稿", card)
        self.assertIn("非法律意见", card)
        self.assertIn("银发〔2026〕42号", card)


class TestMarkdownReader(unittest.TestCase):
    def test_parse_list_markdown(self):
        text = (
            "[司法解释一](https://www.spp.gov.cn/spp/xwfbh/202601/t1.shtml)\n"
            "[首页](https://www.spp.gov.cn/)\n"
        )
        items = fetch.parse_list_markdown(text, {"item_href_re": r"/spp/xwfbh/"})
        self.assertEqual(len(items), 1)
        self.assertIn("司法解释一", items[0]["title"])

    def test_parse_detail_markdown(self):
        text = "# 关于办理虚拟货币案件的通知\n\n发布：2026年2月6日\n\n虚拟货币相关业务活动属于非法金融活动，一律严格禁止。\n"
        detail = fetch.parse_detail_markdown(text)
        self.assertEqual(detail["title"], "关于办理虚拟货币案件的通知")
        self.assertEqual(detail["published_at"], "2026-02-06")
        self.assertIn("非法金融活动", detail["body"])


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestQuietPolicy(unittest.TestCase):
    def test_policy_ignores_quiet_hours(self):
        import main as policy_main

        self.assertFalse(policy_main._defer_for_quiet({"delivery": {"ignore_quiet_hours": True}}))

    def test_default_quiet_can_defer(self):
        import main as policy_main

        # 未配置 ignore 时走时间判断；这里只验证不会因缺少配置报错
        self.assertIn(policy_main._defer_for_quiet({"delivery": {}}), (True, False))
