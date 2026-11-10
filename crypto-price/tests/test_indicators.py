#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""indicators.py 的离线单测：窗口参照价 / 样本不足 / 除零 / 量比 / OI 变化边界。

运行：cd crypto-price && ../.venv/bin/python -m unittest tests.test_indicators -v
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(ROOT), "libs", "crypto_core", "src"))

import indicators  # noqa: E402

NOW = 1794096000


def candle(close_time, close, volume=10.0, open_time=None):
    """造一根已完成 K 线（只需 indicators 用得到的字段）。"""
    return {
        "open_time": close_time - 59 if open_time is None else open_time,
        "open": close,
        "high": close * 1.001,
        "low": close * 0.999,
        "close": close,
        "volume": volume,
        "close_time": close_time,
    }


def series(closes, volumes=None, last_close_time=NOW - 61, step=60):
    """按旧→新造一串 K 线；closes[-1] 对应 last_close_time。"""
    volumes = volumes or [10.0] * len(closes)
    rows = []
    for i, close in enumerate(closes):
        close_time = last_close_time - (len(closes) - 1 - i) * step
        rows.append(candle(close_time, close, volumes[i]))
    return rows


class TestPctChange(unittest.TestCase):
    def test_normal(self):
        self.assertAlmostEqual(indicators.pct_change(110, 100), 10.0)
        self.assertAlmostEqual(indicators.pct_change(90, 100), -10.0)
        self.assertAlmostEqual(indicators.pct_change(100, 100), 0.0)

    def test_invalid(self):
        for price, reference in [
            (None, 100), (100, None), (100, 0), (100, -1), (0, 100), (-1, 100),
            ("bad", 100), (100, "bad"), (float("nan"), 100), (100, float("inf")),
        ]:
            with self.subTest(price=price, reference=reference):
                self.assertIsNone(indicators.pct_change(price, reference))

    def test_numeric_strings_accepted(self):
        self.assertAlmostEqual(indicators.pct_change("110", "100"), 10.0)


class TestWindowMoves(unittest.TestCase):
    WINDOWS = {"m1": 60, "m5": 300, "m15": 900}

    def test_reference_is_last_completed_at_or_before_cutoff(self):
        # m5 参照价必须是 close_time <= now-300 的最后一根：close_time=-301 的 100，
        # 比 cutoff 晚的 -299 那根不能当参照。
        rows = [
            candle(NOW - 5000, 80),
            candle(NOW - 301, 100),
            candle(NOW - 299, 999),
            candle(NOW - 61, 777),   # 会作为 m1 的参照
        ]
        moves = indicators.window_moves(rows, 90, NOW, {"m5": 300, "m1": 60})
        self.assertAlmostEqual(moves["m5"], -10.0)   # 90/100-1
        self.assertAlmostEqual(moves["m1"], 90 / 777 * 100 - 100)

    def test_last_candle_before_cutoff_wins(self):
        rows = [
            candle(NOW - 900, 50),        # 更老的样本
            candle(NOW - 301, 100),       # m5 的参照
            candle(NOW - 60, 200),        # 刚完成，不应作为 m5 参照
        ]
        moves = indicators.window_moves(rows, 90, NOW, {"m5": 300})
        self.assertAlmostEqual(moves["m5"], -10.0)

    def test_incomplete_candles_ignored(self):
        rows = [
            candle(NOW - 301, 100),
            candle(NOW + 30, 1),          # 未来/未完成 K 线必须忽略
        ]
        moves = indicators.window_moves(rows, 110, NOW, {"m5": 300})
        self.assertAlmostEqual(moves["m5"], 10.0)

    def test_unsorted_input_ok(self):
        rows = [candle(NOW - 61, 200), candle(NOW - 301, 100)]
        moves = indicators.window_moves(rows, 90, NOW, {"m5": 300})
        self.assertAlmostEqual(moves["m5"], -10.0)

    def test_sample_shortage_skips_window(self):
        self.assertEqual(indicators.window_moves([], 100, NOW, self.WINDOWS), {})
        rows = [candle(NOW - 61, 100)]
        moves = indicators.window_moves(rows, 110, NOW, {"m1": 60, "m5": 300})
        self.assertIn("m1", moves)
        self.assertNotIn("m5", moves, "样本不足的窗口应缺席，由调用方跳过")

    def test_bad_inputs_do_not_raise(self):
        for price, rows, windows in [
            (None, [], self.WINDOWS), (100, None, self.WINDOWS),
            (100, [], None), (100, [], {}), (100, [{"close_time": "x"}], self.WINDOWS),
        ]:
            with self.subTest(price=price, windows=windows):
                self.assertEqual(indicators.window_moves(rows, price, NOW, windows), {})

    def test_exact_boundary_close_time(self):
        # close_time 恰好等于 cutoff 时应被采用。
        rows = [candle(NOW - 900, 100)]
        moves = indicators.window_moves(rows, 90, NOW, {"m15": 900})
        self.assertAlmostEqual(moves["m15"], -10.0)


class TestVolumeRatio(unittest.TestCase):
    def test_normal(self):
        # 55 根基线量 10 + 最近 5 根量 50：近端每分钟均量 50 / 基线 10 = 5 倍。
        rows = series([100.0] * 60, volumes=[10.0] * 55 + [50.0] * 5)
        out = indicators.volume_ratio(rows, NOW)
        self.assertIsNotNone(out)
        self.assertAlmostEqual(out["ratio"], 5.0)
        self.assertAlmostEqual(out["window_volume"], 50.0)
        self.assertAlmostEqual(out["baseline_avg"], 10.0)
        self.assertEqual(out["window_min"], 5)

    def test_sample_shortage(self):
        rows = series([100.0] * 59)
        self.assertIsNone(indicators.volume_ratio(rows, NOW))

    def test_zero_baseline(self):
        rows = series([100.0] * 60, volumes=[0.0] * 55 + [50.0] * 5)
        self.assertIsNone(indicators.volume_ratio(rows, NOW))

    def test_negative_volume(self):
        rows = series([100.0] * 60, volumes=[10.0] * 55 + [-1.0] * 5)
        self.assertIsNone(indicators.volume_ratio(rows, NOW))

    def test_incomplete_candle_excluded(self):
        rows = series([100.0] * 60, volumes=[10.0] * 55 + [50.0] * 5)
        rows.append(candle(NOW + 60, 100.0, volume=1e9))  # 未完成的高量 K 线不能算进去
        out = indicators.volume_ratio(rows, NOW)
        self.assertAlmostEqual(out["ratio"], 5.0)

    def test_custom_windows(self):
        rows = series([100.0] * 12, volumes=[10.0] * 7 + [30.0] * 5)
        out = indicators.volume_ratio(rows, NOW, window_min=5, baseline_min=7)
        self.assertAlmostEqual(out["ratio"], 3.0)
        self.assertEqual(out["window_min"], 5)

    def test_empty_and_bad(self):
        self.assertIsNone(indicators.volume_ratio([], NOW))
        self.assertIsNone(indicators.volume_ratio(None, NOW))
        self.assertIsNone(indicators.volume_ratio([], None))
        self.assertIsNone(indicators.volume_ratio([], NOW, window_min=0))


class TestOiChange(unittest.TestCase):
    def history(self):
        return [
            {"ts": NOW - 3600, "oi": 80.0},
            {"ts": NOW - 900, "oi": 100.0},   # 15m 窗口的参照（恰好等于 cutoff）
            {"ts": NOW - 600, "oi": 101.0},
            {"ts": NOW, "oi": 105.0},         # 最新
        ]

    def test_normal(self):
        self.assertAlmostEqual(indicators.oi_change_pct(self.history(), NOW), 5.0)

    def test_cutoff_boundary_inclusive(self):
        # ts == cutoff 的样本可作参照；再晚一分钟的样本不行。
        history = [
            {"ts": NOW - 900, "oi": 100.0},
            {"ts": NOW - 899, "oi": 999.0},
            {"ts": NOW, "oi": 105.0},
        ]
        self.assertAlmostEqual(indicators.oi_change_pct(history, NOW), 5.0)

    def test_unsorted_and_dirty_entries(self):
        history = [
            {"ts": NOW, "oi": 105.0},
            {"ts": "bad", "oi": 1.0},
            {"ts": NOW - 900, "oi": 100.0},
            {"ts": NOW - 600, "oi": None},
        ]
        self.assertAlmostEqual(indicators.oi_change_pct(history, NOW), 5.0)

    def test_sample_shortage(self):
        self.assertIsNone(indicators.oi_change_pct([], NOW))
        recent_only = [{"ts": NOW - 60, "oi": 100.0}, {"ts": NOW, "oi": 105.0}]
        self.assertIsNone(indicators.oi_change_pct(recent_only, NOW))
        stale_only = [{"ts": NOW - 1000, "oi": 100.0}]
        self.assertIsNone(indicators.oi_change_pct(stale_only, NOW), "只有窗口外的旧样本应返回 None")

    def test_invalid_reference(self):
        history = [{"ts": NOW - 900, "oi": 0.0}, {"ts": NOW, "oi": 105.0}]
        self.assertIsNone(indicators.oi_change_pct(history, NOW))

    def test_bad_args(self):
        self.assertIsNone(indicators.oi_change_pct(None, NOW))
        self.assertIsNone(indicators.oi_change_pct([], None))
        self.assertIsNone(indicators.oi_change_pct([], NOW, window_min=0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
