#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""score.py 单元测试：确定性合成数据，不联网、不写真实日志。"""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
MOD = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(MOD, "scripts"))
import score  # noqa: E402


def bars_from(closes, start="2026-01-01"):
    import datetime
    d0 = datetime.date.fromisoformat(start)
    out = []
    for i, c in enumerate(closes):
        out.append({"date": (d0 + datetime.timedelta(days=i)).isoformat(),
                    "open": c, "high": c * 1.01, "low": c * 0.99,
                    "close": c, "volume": 1000.0})
    return out


def make_entry(review_by="2026-06-01", entry_ts="2026-01-01T00:00:00+04:00", scored=False):
    return {
        "id": "test-01",
        "ts": entry_ts,
        "review_by": review_by,
        "scored": scored,
        "scenarios_1_3m": [
            {"id": "A", "prob": 50, "band_px": [81000, 87400]},
            {"id": "B", "prob": 35, "band_px": [68000, 70000]},
            {"id": "C", "prob": 15, "band_px": [90000, 1000000]},
        ],
    }


class TestHelpers(unittest.TestCase):
    def test_find_bar(self):
        bars = bars_from([100, 101, 102, 103])
        self.assertEqual(score.find_bar(bars, "2026-01-02")["close"], 101)
        self.assertEqual(score.find_bar(bars, "2026-01-10")["close"], 103)
        self.assertIsNone(score.find_bar(bars, "2025-12-31"))

    def test_invalidated_between(self):
        # 前 200 天平稳在 200，后段大跌到 120 → 跌破 MA200
        closes = [200.0] * 200 + [120.0] * 40
        bars = bars_from(closes)
        invalid, on = score.invalidated_between(bars, "2026-01-01", "2026-08-20")
        self.assertTrue(invalid)
        self.assertIsNotNone(on)

    def test_not_invalidated(self):
        closes = [100 * (1.001 ** i) for i in range(240)]
        bars = bars_from(closes)
        invalid, _ = score.invalidated_between(bars, "2026-01-01", "2026-06-01")
        self.assertFalse(invalid)


class TestScoreEntry(unittest.TestCase):
    def test_not_due(self):
        bars = bars_from([100 * (1.001 ** i) for i in range(260)])
        entry = make_entry(review_by="2026-12-01")
        res, reason = score.score_entry(entry, bars, as_of="2026-06-01")
        self.assertIsNone(res)
        self.assertEqual(reason, "未到期")

    def test_due_and_band_brier(self):
        # 构造：复盘日价格 ~68,500 → 落入 B
        closes = [100 * (1.001 ** i) for i in range(240)]
        bars = bars_from(closes)
        entry = make_entry(review_by="2026-03-01")
        # 覆盖复盘日价格为 68,500
        for b in bars:
            if b["date"] == "2026-03-01":
                b["close"] = 68500.0
        res, reason = score.score_entry(entry, bars, as_of="2026-03-02")
        self.assertIsNone(reason)
        self.assertEqual(res["realized_band"], "B")
        expected = score.core.brier({"A": 0.5, "B": 0.35, "C": 0.15}, "B")
        self.assertAlmostEqual(res["brier"], expected)
        self.assertIn("realized_px", res)

    def test_already_scored(self):
        bars = bars_from([100 * (1.001 ** i) for i in range(260)])
        entry = make_entry(scored=True)
        res, reason = score.score_entry(entry, bars, as_of="2026-07-01")
        self.assertIsNone(res)
        self.assertEqual(reason, "已打分")


class TestRunCli(unittest.TestCase):
    def test_dry_run_does_not_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = os.path.join(tmp, "log.jsonl")
            entry = make_entry(review_by="2026-03-01")
            with open(log, "w") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            # 用真缓存 K 线跑（存在则跳过，不存在返回 0）
            before = open(log).read()
            rc = score.run("2026-03-02", dry_run=True, json_out=True, log_path=log)
            self.assertEqual(rc, 0)
            self.assertEqual(open(log).read(), before)


if __name__ == "__main__":
    unittest.main()
