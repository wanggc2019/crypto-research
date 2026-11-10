#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core.py 单元测试（离线、合成数据）。"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
MOD = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(MOD, "scripts"))
import core  # noqa: E402

FIX = os.path.join(HERE, "fixtures")


def have_klines():
    """K 线 fixtures 在 tests/fixtures/data/...，但仓库 hygiene 规则禁止追踪 data/，
    因此 CI 上没有这些文件；缺文件时跳过依赖真实 K 线的测试。"""
    return os.path.isdir(os.path.join(FIX, "data", "raw", "klines"))


def make_bars(closes, volumes=None):
    bars = []
    for i, c in enumerate(closes):
        bars.append({
            "date": f"2026-01-{i + 1:02d}" if i < 31 else f"2026-02-{i - 30:02d}",
            "open": c, "high": c * 1.01, "low": c * 0.99, "close": c,
            "volume": (volumes or [1000.0] * len(closes))[i],
        })
    return bars


class TestBasics(unittest.TestCase):
    def test_sma(self):
        self.assertAlmostEqual(core.sma([1, 2, 3], 2), 2.5)
        self.assertIsNone(core.sma([1, 2], 3))

    def test_rets(self):
        self.assertAlmostEqual(core.rets([100, 110, 99])[0], 0.1)
        self.assertAlmostEqual(core.rets([100, 110, 99])[1], -0.1)

    def test_max_drawdown(self):
        dd, pk, tr = core.max_drawdown([1.0, 1.2, 1.0, 1.5, 0.9])
        self.assertAlmostEqual(dd, -0.4)
        self.assertEqual((pk, tr), (3, 4))

    def test_pct_and_money(self):
        self.assertEqual(core.pct(1.234), "+1.2%")
        self.assertEqual(core.pct(None), "n/a")
        self.assertEqual(core.money(1234.5), "$1,234")


class TestState(unittest.TestCase):
    @unittest.skipUnless(have_klines(), "缺少 K 线 fixtures（data/ 不入库，CI 跳过）")
    def test_state_row_from_fixture(self):
        bars = core.load_bars("BTC", FIX)
        row = core.state_row("BTC", bars)
        self.assertEqual(row["sym"], "BTC")
        self.assertAlmostEqual(row["last"], bars[-1]["close"])
        for key in ("ma50", "ma200", "vs_ma50_pct", "vs_ma200_pct", "ath",
                    "dd_from_ath_pct", "hi52", "lo52", "pos_52w_pct",
                    "vs_52w_low_pct", "hi90", "vs_90d_high_pct",
                    "vol_ratio_30_90", "ret_30d_pct", "ret_1y_pct", "rv30_pct"):
            self.assertIn(key, row)
        self.assertLessEqual(row["dd_from_ath_pct"], 0)
        self.assertGreaterEqual(row["pos_52w_pct"], 0)
        self.assertLessEqual(row["pos_52w_pct"], 100)

    @unittest.skipUnless(have_klines(), "缺少 K 线 fixtures（data/ 不入库，CI 跳过）")
    def test_basket_and_correlation(self):
        px = {s: {b["date"]: b["close"] for b in core.load_bars(s, FIX)} for s in core.SYMS}
        bs = core.equal_weight_stats(px)
        self.assertLessEqual(bs["maxdd_pct"], 0)
        self.assertGreater(bs["vol_ann_pct"], 0)
        corr = core.correlation(px)
        self.assertEqual(len(corr), 6)
        for k, v in corr.items():
            self.assertGreaterEqual(v, -1.0)
            self.assertLessEqual(v, 1.0)

    def test_ma200_zone_stats(self):
        # 价格长期维持在 MA200 上方 2%：构造缓慢上升序列
        closes = [100 * (1.002 ** i) for i in range(320)]
        bars = make_bars(closes)
        stt = core.ma200_zone_stats(bars, 0, 5)
        self.assertGreaterEqual(stt["day"]["n"], 0)
        self.assertGreaterEqual(len(stt["episodes"]), 0)
        if stt["day"]["n"]:
            self.assertGreaterEqual(stt["day"]["pct_positive"], 0)

    def test_band_of_price(self):
        bands = {"B": (0, 70000), "A": (81000, 87400), "C": (90000, None)}
        self.assertEqual(core.band_of_price(68000, bands), "B")
        self.assertEqual(core.band_of_price(83000, bands), "A")
        self.assertEqual(core.band_of_price(95000, bands), "C")
        self.assertEqual(core.band_of_price(75000, bands), "other")

    def test_brier(self):
        self.assertEqual(core.brier({"A": 1.0, "B": 0.0}, "A"), 0.0)
        self.assertAlmostEqual(core.brier({"A": 0.0, "B": 1.0}, "A"), 2.0)
        self.assertGreater(core.brier({"A": 0.5, "B": 0.5}, "A"),
                           core.brier({"A": 0.9, "B": 0.1}, "A"))


if __name__ == "__main__":
    unittest.main()
