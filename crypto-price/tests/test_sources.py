#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sources.fetch_snapshot 单测（全部离线）。

两条路径：
1. mock ``crypto_core.http.get_json``：覆盖三源正常、单源失败、主源失败回退、全挂、价差/中位数；
2. fixture 回放：``tests/fixtures/normal`` 真实响应（§9.2 命名），并断言回放过程完全不联网。
"""
import copy
import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.parse
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.join(os.path.dirname(_HERE), "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import sources  # noqa: E402

from crypto_core import http  # noqa: E402

FIXTURES = os.path.join(_HERE, "fixtures", "normal")
FIXTURE_TS = 1794096000

WATCHLIST = {
    "assets": {
        "BTC": {"name": "Bitcoin", "binance": "BTCUSDT", "okx": "BTC-USDT", "coinbase": "BTC-USD"},
        "ETH": {"name": "Ethereum", "binance": "ETHUSDT", "okx": "ETH-USDT", "coinbase": "ETH-USD"},
        "BNB": {"name": "BNB", "binance": "BNBUSDT", "okx": "BNB-USDT", "coinbase": "BNB-USD"},
    }
}

APP_CFG = {
    "sources": {
        "binance": {
            "enabled": True,
            "spot_base": "https://api.binance.com",
            "futures_base": "https://fapi.binance.com",
        },
        "okx": {"enabled": True, "base": "https://www.okx.com"},
        "coinbase": {"enabled": True, "base": "https://api.exchange.coinbase.com"},
    },
    "derivatives": {"enabled": True, "funding": True, "open_interest": True},
}

MARKET = {
    "BTC": {
        "binance": {"last": 75790.54, "open": 75928.4, "high": 76251.1, "low": 75569.1, "volume": 2055.9, "change_pct": -0.179},
        "okx": {"last": 75790.5, "open": 75928.4, "high": 76251.1, "low": 75569.1, "volume": 2055.9, "change_pct": -0.182},
        "coinbase": {"last": 75790.6, "open": 75928.4, "high": 76251.1, "low": 75569.1, "volume": 2055.9, "change_pct": -0.182},
    },
    "ETH": {
        "binance": {"last": 3201.50, "open": 3163.4, "high": 3210.0, "low": 3150.2, "volume": 152000.5, "change_pct": 1.204},
        "okx": {"last": 3201.40, "open": 3163.4, "high": 3210.0, "low": 3150.2, "volume": 152000.5, "change_pct": 1.201},
        "coinbase": {"last": 3201.60, "open": 3163.4, "high": 3210.0, "low": 3150.2, "volume": 152000.5, "change_pct": 1.207},
    },
    "BNB": {
        "binance": {"last": 610.25, "open": 608.0, "high": 612.0, "low": 605.5, "volume": 49558.2, "change_pct": 0.370},
        "okx": {"last": 610.20, "open": 608.0, "high": 612.0, "low": 605.5, "volume": 49558.2, "change_pct": 0.362},
        "coinbase": {"last": 610.30, "open": 608.0, "high": 612.0, "low": 605.5, "volume": 49558.2, "change_pct": 0.378},
    },
}


# ---------------------------------------------------------------- fake 响应
def _symbol(asset, source):
    return WATCHLIST["assets"][asset][source]


def _asset_of(source, symbol):
    for asset, cfg in WATCHLIST["assets"].items():
        if cfg[source] == symbol:
            return asset
    raise AssertionError("unknown %s symbol %r" % (source, symbol))


def _binance_ticker(asset, market):
    m = market[asset]["binance"]
    return {
        "symbol": _symbol(asset, "binance"),
        "priceChangePercent": str(m["change_pct"]),
        "lastPrice": str(m["last"]),
        "openPrice": str(m["open"]),
        "highPrice": str(m["high"]),
        "lowPrice": str(m["low"]),
        "volume": str(m["volume"]),
        "quoteVolume": "156072345.1",
        "closeTime": FIXTURE_TS * 1000,
    }


def _okx_ticker(asset, market):
    m = market[asset]["okx"]
    return {
        "code": "0",
        "msg": "",
        "data": [
            {
                "instType": "SPOT",
                "instId": _symbol(asset, "okx"),
                "last": str(m["last"]),
                "open24h": str(m["open"]),
                "high24h": str(m["high"]),
                "low24h": str(m["low"]),
                "vol24h": str(m["volume"]),
                "volCcy24h": "156072345.1",
                "ts": str(FIXTURE_TS * 1000),
            }
        ],
    }


def _coinbase_stats(asset, market):
    m = market[asset]["coinbase"]
    return {
        "open": str(m["open"]),
        "high": str(m["high"]),
        "low": str(m["low"]),
        "volume": str(m["volume"]),
        "last": str(m["last"]),
        "volume_30d": "61234.5",
    }


def _binance_klines():
    rows = []
    for i in range(3):
        open_ms = (FIXTURE_TS - 180 + i * 60) * 1000
        rows.append(
            [open_ms, "100.0", "101.0", "99.0", "100.5", "10.0", open_ms + 59999, "1000.0", 10, "5.0", "500.0", "0"]
        )
    return rows


def _okx_candles():
    rows = []
    for i in range(3):
        ts = (FIXTURE_TS - 180 + i * 60) * 1000
        rows.append([str(ts), "100.0", "101.0", "99.0", "100.5", "10.0", "1000.0", "1000.0", "1"])
    return list(reversed(rows))  # 接口本身是新→旧


def _binance_funding(asset):
    return {
        "symbol": _symbol(asset, "binance"),
        "lastFundingRate": "0.00005475",
        "markPrice": "75756.0",
        "time": FIXTURE_TS * 1000,
    }


def _binance_open_interest(asset):
    return {"symbol": _symbol(asset, "binance"), "openInterest": "95982.052", "time": FIXTURE_TS * 1000}


def make_fake(market=None, fail_sources=()):
    """按 URL 路由返回内联样本；fail_sources 里的源抛 TimeoutError。"""
    market = market or MARKET

    def _maybe_fail(source):
        if source in fail_sources:
            raise TimeoutError("%s down" % source)

    def fake(url, **kwargs):
        parsed = urllib.parse.urlparse(url)
        query = urllib.parse.parse_qs(parsed.query)
        if parsed.netloc == "api.binance.com" and parsed.path == "/api/v3/ticker/24hr":
            _maybe_fail("binance")
            return _binance_ticker(_asset_of("binance", query["symbol"][0]), market)
        if parsed.netloc == "api.binance.com" and parsed.path == "/api/v3/klines":
            _maybe_fail("binance")
            return _binance_klines()
        if parsed.netloc == "fapi.binance.com" and parsed.path == "/fapi/v1/premiumIndex":
            _maybe_fail("binance")
            return _binance_funding(_asset_of("binance", query["symbol"][0]))
        if parsed.netloc == "fapi.binance.com" and parsed.path == "/fapi/v1/openInterest":
            _maybe_fail("binance")
            return _binance_open_interest(_asset_of("binance", query["symbol"][0]))
        if parsed.netloc == "www.okx.com" and parsed.path == "/api/v5/market/ticker":
            _maybe_fail("okx")
            return _okx_ticker(_asset_of("okx", query["instId"][0]), market)
        if parsed.netloc == "www.okx.com" and parsed.path == "/api/v5/market/candles":
            _maybe_fail("okx")
            return {"code": "0", "msg": "", "data": _okx_candles()}
        if parsed.netloc == "api.exchange.coinbase.com" and parsed.path.startswith("/products/"):
            _maybe_fail("coinbase")
            symbol = parsed.path.split("/")[2]
            return _coinbase_stats(_asset_of("coinbase", symbol), market)
        raise AssertionError("unexpected url: " + url)

    return fake


def _no_network(*args, **kwargs):
    raise AssertionError("fixture 回放不允许联网: %r" % (args,))


class SourcesTestBase(unittest.TestCase):
    def fetch(self, fake, **kwargs):
        kwargs.setdefault("now_ts", FIXTURE_TS)
        with mock.patch.object(http, "get_json", side_effect=fake):
            return sources.fetch_snapshot(WATCHLIST, APP_CFG, **kwargs)

    def fetch_fixture(self, fixture_dir, now_ts=None):
        kwargs = {"fixture_dir": fixture_dir}
        if now_ts is not None:
            kwargs["now_ts"] = now_ts
        return sources.fetch_snapshot(WATCHLIST, APP_CFG, **kwargs)


class TestThreeSources(SourcesTestBase):
    def setUp(self):
        self.snap = self.fetch(make_fake())

    def test_header(self):
        self.assertEqual(self.snap["ts_epoch"], FIXTURE_TS)
        self.assertTrue(self.snap["ts"])
        self.assertEqual(self.snap["errors"], [])
        self.assertEqual(self.snap["sources_ok"], 9)
        self.assertEqual(self.snap["sources_total"], 9)

    def test_quotes_median_and_sources(self):
        btc = self.snap["quotes"]["BTC"]
        self.assertAlmostEqual(btc["price"], 75790.54)  # 三源中位数
        self.assertEqual(btc["n_sources"], 3)
        self.assertTrue(btc["agree"])
        self.assertEqual(list(btc["sources"]), ["binance", "okx", "coinbase"])
        for name in ("binance", "okx", "coinbase"):
            self.assertEqual(btc["sources"][name]["ts"], FIXTURE_TS)
        self.assertAlmostEqual(self.snap["quotes"]["ETH"]["price"], 3201.50)
        self.assertAlmostEqual(self.snap["quotes"]["BNB"]["price"], 610.25)

    def test_spread_bps(self):
        btc = self.snap["quotes"]["BTC"]
        prices = [75790.54, 75790.5, 75790.6]
        expected = round((max(prices) - min(prices)) / 75790.54 * 10000.0, 2)
        self.assertAlmostEqual(btc["spread_bps"], expected)
        self.assertLess(btc["spread_bps"], 1.0)

    def test_stats_priority_and_candles(self):
        btc_stats = self.snap["stats"]["BTC"]
        self.assertEqual(btc_stats["source"], "binance")
        self.assertAlmostEqual(btc_stats["high"], 76251.1)
        self.assertAlmostEqual(btc_stats["change_pct"], -0.179)
        self.assertEqual(set(btc_stats["sources"]), {"binance", "okx", "coinbase"})

        btc_candles = self.snap["candles"]["BTC"]
        self.assertEqual(btc_candles["source"], "binance")
        self.assertEqual(len(btc_candles["rows"]), 3)
        self.assertEqual(btc_candles["errors"], [])
        self.assertEqual([r["open_time"] for r in btc_candles["rows"]], [FIXTURE_TS - 180, FIXTURE_TS - 120, FIXTURE_TS - 60])

    def test_derivatives(self):
        self.assertAlmostEqual(self.snap["funding"]["BTC"]["rate"], 0.00005475)
        self.assertAlmostEqual(self.snap["funding"]["BTC"]["mark_price"], 75756.0)
        self.assertEqual(self.snap["funding"]["BTC"]["ts"], FIXTURE_TS)
        self.assertAlmostEqual(self.snap["oi"]["BTC"]["oi"], 95982.052)
        self.assertEqual(self.snap["oi"]["BTC"]["ts"], FIXTURE_TS)


class TestDegrade(SourcesTestBase):
    def test_single_source_failure(self):
        snap = self.fetch(make_fake(fail_sources=("okx",)))
        btc = snap["quotes"]["BTC"]
        self.assertEqual(btc["n_sources"], 2)
        self.assertAlmostEqual(btc["price"], (75790.54 + 75790.6) / 2.0)  # 两个源取均值
        self.assertTrue(btc["agree"])
        self.assertNotIn("okx", btc["sources"])
        self.assertEqual(snap["sources_ok"], 6)
        self.assertEqual(snap["sources_total"], 9)
        self.assertTrue(any(e["source"] == "okx" for e in snap["errors"]))
        self.assertTrue(all(e["error"] == "TimeoutError" for e in snap["errors"] if e["source"] == "okx"))

    def test_binance_down_stats_and_candles_fall_back_to_okx(self):
        snap = self.fetch(make_fake(fail_sources=("binance",)))
        self.assertEqual(snap["quotes"]["BTC"]["n_sources"], 2)
        self.assertEqual(snap["stats"]["BTC"]["source"], "okx")
        self.assertEqual(snap["candles"]["BTC"]["source"], "okx")
        self.assertGreater(len(snap["candles"]["BTC"]["rows"]), 0)
        self.assertEqual(snap["funding"], {})  # 衍生品仅 Binance，失败即缺席
        self.assertEqual(snap["oi"], {})
        self.assertEqual(snap["sources_ok"], 6)

    def test_all_sources_down(self):
        snap = self.fetch(make_fake(fail_sources=("binance", "okx", "coinbase")))
        for asset in WATCHLIST["assets"]:
            self.assertIsNone(snap["quotes"][asset]["price"])
            self.assertEqual(snap["quotes"][asset]["n_sources"], 0)
            self.assertFalse(snap["quotes"][asset]["agree"])
            self.assertEqual(snap["candles"][asset]["source"], None)
            self.assertEqual(snap["candles"][asset]["rows"], [])
        self.assertEqual(snap["stats"], {})
        self.assertEqual(snap["funding"], {})
        self.assertEqual(snap["oi"], {})
        self.assertEqual(snap["sources_ok"], 0)
        self.assertEqual(snap["sources_total"], 9)
        # 快速失败：每个源只记一次错误（后续同源请求直接跳过）
        failed = {err["source"] for err in snap["errors"]}
        self.assertEqual(failed, {"binance", "okx", "coinbase"})
        self.assertGreaterEqual(len(snap["errors"]), 3)
        for err in snap["errors"]:
            self.assertEqual(set(err), {"source", "asset", "error"})


class TestSpread(SourcesTestBase):
    def test_single_outlier_is_excluded(self):
        market = copy.deepcopy(MARKET)
        market["BTC"]["coinbase"]["last"] = 77000.0
        snap = self.fetch(make_fake(market=market))
        btc = snap["quotes"]["BTC"]
        # Binance/OKX 一致，Coinbase 偏离 >40bps → 剔除 coinbase，用 2 源继续判断
        self.assertEqual(btc["n_sources"], 2)
        self.assertEqual(btc["excluded_sources"], ["coinbase"])
        self.assertLess(btc["spread_bps"], sources.MAX_SPREAD_BPS)
        self.assertTrue(btc["agree"])
        self.assertGreater(btc["raw_spread_bps"], 100)

    def test_two_sources_divergence_blocks_agreement(self):
        market = copy.deepcopy(MARKET)
        market["BTC"]["coinbase"]["last"] = 77000.0
        snap = self.fetch(make_fake(market=market, fail_sources=("binance",)))
        btc = snap["quotes"]["BTC"]
        # 只剩 2 源且分歧 → 没有第三票可剔除，只能判定不可信
        self.assertEqual(btc["n_sources"], 2)
        self.assertGreater(btc["spread_bps"], sources.MAX_SPREAD_BPS)
        self.assertFalse(btc["agree"])

    def test_two_sources_spread(self):
        snap = self.fetch(make_fake(fail_sources=("coinbase",)))
        btc = snap["quotes"]["BTC"]
        self.assertEqual(btc["n_sources"], 2)
        expected = round((75790.54 - 75790.5) / ((75790.54 + 75790.5) / 2.0) * 10000.0, 2)
        self.assertAlmostEqual(btc["spread_bps"], expected)
        self.assertTrue(btc["agree"])


class TestConfigSwitches(SourcesTestBase):
    def test_disabled_sources_and_derivatives(self):
        app_cfg = copy.deepcopy(APP_CFG)
        app_cfg["sources"]["okx"]["enabled"] = False
        app_cfg["derivatives"] = {"enabled": False}
        with mock.patch.object(http, "get_json", side_effect=make_fake()) as m:
            snap = sources.fetch_snapshot(WATCHLIST, app_cfg, now_ts=FIXTURE_TS)
        self.assertEqual(snap["sources_total"], 6)  # 3 资产 × 2 启用源
        self.assertEqual(snap["sources_ok"], 6)
        self.assertEqual(snap["quotes"]["BTC"]["n_sources"], 2)
        self.assertEqual(snap["funding"], {})
        self.assertEqual(snap["oi"], {})
        self.assertEqual(snap["errors"], [])
        called = " ".join(str(c.args[0]) for c in m.call_args_list)
        self.assertNotIn("okx.com", called)
        self.assertNotIn("fapi.binance.com", called)


class TestFixtures(SourcesTestBase):
    def test_replay_offline_normal(self):
        with mock.patch.object(http, "get_json", side_effect=_no_network):
            snap = self.fetch_fixture(FIXTURES)
        self.assertEqual(snap["errors"], [])
        self.assertEqual(snap["sources_ok"], 9)
        self.assertEqual(snap["primary_sources"], ["binance", "okx", "coinbase"])
        self.assertEqual(snap["primary_available"], ["binance", "okx", "coinbase"])
        self.assertEqual(snap["primary_unavailable"], [])
        self.assertTrue(snap["source_health"]["binance"]["ok"])
        self.assertEqual(snap["sources_total"], 9)
        for asset in WATCHLIST["assets"]:
            self.assertEqual(snap["quotes"][asset]["n_sources"], 3)
            self.assertEqual(snap["stats"][asset]["source"], "binance")
            self.assertEqual(snap["candles"][asset]["source"], "binance")
            self.assertEqual(len(snap["candles"][asset]["rows"]), 120)
        self.assertIn("BTC", snap["funding"])
        self.assertIn("BTC", snap["oi"])

        # 自动时间戳 = fixture 数据内最大时间戳
        candidates = [e["ts"] for q in snap["quotes"].values() for e in q["sources"].values()]
        candidates += [v["ts"] for v in snap["funding"].values()]
        candidates += [v["ts"] for v in snap["oi"].values()]
        candidates += [v["rows"][-1]["close_time"] for v in snap["candles"].values() if v["rows"]]
        self.assertEqual(snap["ts_epoch"], max(candidates))

    def test_replay_uses_fixture_prices(self):
        with open(os.path.join(FIXTURES, "binance", "ticker24h_BTC.json"), "rb") as f:
            raw = json.loads(f.read())
        with mock.patch.object(http, "get_json", side_effect=_no_network):
            snap = self.fetch_fixture(FIXTURES, now_ts=FIXTURE_TS)
        self.assertAlmostEqual(snap["quotes"]["BTC"]["sources"]["binance"]["price"], float(raw["lastPrice"]))
        self.assertEqual(snap["ts_epoch"], FIXTURE_TS)

    def test_replay_missing_binance_falls_back(self):
        tmp = tempfile.mkdtemp(prefix="price-fixtures-")
        self.addCleanup(shutil.rmtree, tmp, True)
        for asset in WATCHLIST["assets"]:
            for source, kinds in (
                ("okx", ("ticker", "candles")),
                ("coinbase", ("stats",)),
            ):
                dst_dir = os.path.join(tmp, source)
                os.makedirs(dst_dir, exist_ok=True)
                for kind in kinds:
                    shutil.copy(
                        os.path.join(FIXTURES, source, "%s_%s.json" % (kind, asset)),
                        os.path.join(dst_dir, "%s_%s.json" % (kind, asset)),
                    )
        with mock.patch.object(http, "get_json", side_effect=_no_network):
            snap = self.fetch_fixture(tmp, now_ts=FIXTURE_TS)
        btc = snap["quotes"]["BTC"]
        self.assertEqual(btc["n_sources"], 2)  # binance 缺文件 → OKX + Coinbase
        self.assertEqual(snap["candles"]["BTC"]["source"], "okx")
        self.assertGreater(len(snap["candles"]["BTC"]["rows"]), 0)
        self.assertEqual(snap["stats"]["BTC"]["source"], "okx")
        self.assertGreaterEqual(snap["sources_ok"], 6)
        self.assertTrue(any(e["source"] == "binance" and "missing_fixture" in e["error"] for e in snap["errors"]))

    def test_replay_empty_dir_never_raises(self):
        tmp = tempfile.mkdtemp(prefix="price-fixtures-empty-")
        self.addCleanup(shutil.rmtree, tmp, True)
        snap = self.fetch_fixture(tmp)
        self.assertEqual(snap["sources_ok"], 0)
        self.assertEqual(snap["sources_total"], 9)
        self.assertGreaterEqual(len(snap["errors"]), 9)
        for asset in WATCHLIST["assets"]:
            self.assertIsNone(snap["quotes"][asset]["price"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
