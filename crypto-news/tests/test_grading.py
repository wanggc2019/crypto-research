# -*- coding: utf-8 -*-
"""score.grade_scored 分级规则边界（硬条件 / 封顶）。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import score  # noqa: E402

TH = {"scoring": {"levels": {"P0": 80, "P1": 60, "P2": 40},
                  "hard": {"window_hours": 48, "stale_window_hours": 96, "min_decay": 0.1}}}


def item(**kw):
    s = {"score": 50, "hard": [], "source_tiers": {"a": "T1"}, "components": {"D": 1.0},
         "age_hours": 1, "assets": ["BTC"], "category": "exchange"}
    s.update(kw)
    return s


def grade(**kw):
    return score.grade_scored([item(**kw)], TH)[0]


class GradeTest(unittest.TestCase):
    def test_score_only(self):
        self.assertEqual(grade(score=85)["level"], "P0")
        self.assertEqual(grade(score=65)["level"], "P1")
        self.assertEqual(grade(score=45)["level"], "P2")
        self.assertEqual(grade(score=10)["level"], "ARCHIVE")

    def test_hard_confirmed_fresh_is_p0(self):
        self.assertEqual(grade(hard=["hack"], source_tiers={"a": "T0"})["level"], "P0")

    def test_hard_confirmed_stale_is_p1_then_backfill(self):
        self.assertEqual(grade(hard=["hack"], source_tiers={"a": "T0"}, age_hours=60)["level"], "P1")
        g = grade(hard=["hack"], source_tiers={"a": "T0"}, age_hours=200)
        self.assertEqual((g["level"], g.get("backfill")), ("P2", True))

    def test_hard_single_trusted_source_is_unconfirmed_p1(self):
        g = grade(hard=["hack"], source_tiers={"a": "T2"})
        self.assertEqual((g["level"], g["unconfirmed"]), ("P1", True))

    def test_t3_and_page_update_capped_at_p2(self):
        self.assertEqual(grade(score=90, source_tiers={"a": "T3"})["level"], "P2")
        self.assertEqual(grade(score=90, kind="page_update")["level"], "P2")

    def test_macro_caps(self):
        self.assertEqual(grade(score=90, category="macro")["level"], "P1")
        self.assertEqual(grade(score=90, category="macro", assets=[])["level"], "P2")

    def test_labor_caps_by_confirmation(self):
        self.assertEqual(grade(score=90, category="industry_labor")["level"], "P2")
        self.assertEqual(grade(score=90, category="industry_labor", source_tiers={"a": "T0"})["level"], "P1")


if __name__ == "__main__":
    unittest.main()
