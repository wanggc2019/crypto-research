#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""market/binance|okx|coinbase 解析单测（全部离线，内联样本）。

覆盖：正常解析、坏数据降级（None/[]）、毫秒→秒换算、K 线旧→新排序、字段换算。
"""
import json
import unittest

from crypto_core.market import binance, coinbase, okx

# ---------------------------------------------------------------- Binance 样本
BINANCE_TICKER24H = json.dumps(
    [
        {
            "symbol": "BTCUSDT",
            "priceChange": "-136.28",
            "priceChangePercent": "-0.179",
            "lastPrice": "75796.69000000",
            "openPrice": "75932.97000000",
            "highPrice": "76251.10000000",
            "lowPrice": "75569.10000000",
            "volume": "2055.90000000",
            "quoteVolume": "156072345.10000000",
            "openTime": 1794009600000,
            "closeTime": 1794096000000,
        },
        {
            "symbol": "ETHUSDT",
            "priceChange": "38.10",
            "priceChangePercent": "1.204",
            "lastPrice": "3201.50",
            "openPrice": "3163.40",
            "highPrice": "3210.00",
            "lowPrice": "3150.20",
            "volume": "152000.5",
            "quoteVolume": "480000000.0",
            "openTime": 1794009600000,
            "closeTime": 1794096000000,
        },
    ]
).encode()

BINANCE_TICKER24H_SINGLE = json.dumps(
    {
        "symbol": "BTCUSDT",
        "priceChangePercent": "-0.179",
        "lastPrice": "75790.54",
        "openPrice": "75928.4",
        "highPrice": "76251.1",
        "lowPrice": "75569.1",
        "volume": "2055.9",
        "quoteVolume": "156072345.1",
        "closeTime": 1794096000000,
    }
).encode()

BINANCE_KLINES = json.dumps(
    [
        # 故意新→旧，验证解析层会排序成旧→新
        [
            1794096060000,
            "75900.10",
            "75910.20",
            "75890.30",
            "75905.40",
            "12.50",
            1794096119999,
            "949000.10",
            120,
            "6.20",
            "470000.00",
            "0",
        ],
        [
            1794096000000,
            "75790.54",
            "75820.00",
            "75700.00",
            "75799.90",
            "20.10",
            1794096059999,
            "1520000.00",
            200,
            "10.00",
            "750000.00",
            "0",
        ],
    ]
).encode()

BINANCE_OI = json.dumps({"symbol": "BTCUSDT", "openInterest": "95982.052", "time": 1794096000000}).encode()
BINANCE_FUNDING = json.dumps(
    {"symbol": "BTCUSDT", "lastFundingRate": "0.00005475", "markPrice": "75756.0", "time": 1794096000000}
).encode()

# ---------------------------------------------------------------- OKX 样本
OKX_TICKER = json.dumps(
    {
        "code": "0",
        "msg": "",
        "data": [
            {
                "instType": "SPOT",
                "instId": "BTC-USDT",
                "last": "75790.5",
                "lastSz": "0.01",
                "askPx": "75790.6",
                "askSz": "1.0",
                "bidPx": "75790.4",
                "bidSz": "1.0",
                "open24h": "75928.4",
                "high24h": "76251.1",
                "low24h": "75569.1",
                "volCcy24h": "156072345.1",
                "vol24h": "2055.9",
                "sodUtc0": "75800.0",
                "sodUtc8": "75800.0",
                "ts": "1794096000000",
            }
        ],
    }
).encode()

OKX_CANDLES = json.dumps(
    {
        "code": "0",
        "msg": "",
        "data": [
            # 新→旧；[ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]
            ["1794096120000", "75905.4", "75910.2", "75890.3", "75900.1", "12.5", "949000.1", "949000.1", "1"],
            ["1794096060000", "75900.1", "75908.0", "75880.0", "75905.4", "8.0", "607000.0", "607000.0", "1"],
            ["1794096000000", "75790.5", "75920.0", "75700.0", "75900.1", "20.1", "1520000.0", "1520000.0", "0"],
        ],
    }
).encode()

# ---------------------------------------------------------------- Coinbase 样本
COINBASE_STATS = json.dumps(
    {
        "open": "75928.4",
        "high": "76251.1",
        "low": "75569.1",
        "volume": "2055.9",
        "last": "75790.54",
        "volume_30d": "61234.5",
        "vwap": "75850.1",
    }
).encode()

COINBASE_TICKER = json.dumps(
    {
        "trade_id": 123456,
        "price": "75790.54",
        "size": "0.01",
        "bid": "75790.40",
        "ask": "75790.68",
        "volume": "2055.9",
        "time": "2026-11-08T00:00:00.123456Z",
    }
).encode()


class TestBinanceTickers24h(unittest.TestCase):
    def test_list(self):
        rows = binance.parse_tickers24h(BINANCE_TICKER24H)
        self.assertEqual([r["symbol"] for r in rows], ["BTCUSDT", "ETHUSDT"])
        btc = rows[0]
        self.assertAlmostEqual(btc["last"], 75796.69)
        self.assertAlmostEqual(btc["open"], 75932.97)
        self.assertAlmostEqual(btc["high"], 76251.1)
        self.assertAlmostEqual(btc["low"], 75569.1)
        self.assertAlmostEqual(btc["volume"], 2055.9)
        self.assertAlmostEqual(btc["quote_volume"], 156072345.1)
        self.assertAlmostEqual(btc["change_pct"], -0.179)
        # 毫秒 -> epoch 秒
        self.assertEqual(btc["ts"], 1794096000)
        self.assertIsInstance(btc["ts"], int)

    def test_single_dict_is_wrapped(self):
        rows = binance.parse_tickers24h(BINANCE_TICKER24H_SINGLE)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["symbol"], "BTCUSDT")
        self.assertAlmostEqual(rows[0]["last"], 75790.54)

    def test_str_input(self):
        rows = binance.parse_tickers24h(BINANCE_TICKER24H_SINGLE.decode("utf-8"))
        self.assertEqual(len(rows), 1)

    def test_bad_rows_skipped(self):
        rows = binance.parse_tickers24h(
            json.dumps([{"symbol": "X"}, {"symbol": "Y", "lastPrice": "1.5", "closeTime": 1794096000000}])
        )
        self.assertEqual([r["symbol"] for r in rows], ["Y"])
        self.assertIsNone(rows[0]["open"])

    def test_bad_input(self):
        self.assertEqual(binance.parse_tickers24h(b"<not-json"), [])
        self.assertEqual(binance.parse_tickers24h(json.dumps("nope")), [])
        self.assertEqual(binance.parse_tickers24h(None), [])


class TestBinanceKlines(unittest.TestCase):
    def test_normal_sorted_old_to_new(self):
        rows = binance.parse_klines(BINANCE_KLINES)
        self.assertEqual([r["open_time"] for r in rows], [1794096000, 1794096060])
        self.assertEqual([r["close_time"] for r in rows], [1794096059, 1794096119])
        first = rows[0]
        self.assertAlmostEqual(first["open"], 75790.54)
        self.assertAlmostEqual(first["close"], 75799.90)
        self.assertAlmostEqual(first["volume"], 20.10)

    def test_bad_rows_skipped(self):
        rows = binance.parse_klines(
            json.dumps(
                [
                    [1794096000000, "1", "2", "0.5", "1.5", "10", 1794096059999],
                    [1794096060000, "bad", "2", "1", "1.5", "10", 1794096119999],
                    ["not-a-row"],
                ]
            )
        )
        self.assertEqual([r["open_time"] for r in rows], [1794096000])

    def test_bad_input(self):
        self.assertEqual(binance.parse_klines(b"[]"), [])
        self.assertEqual(binance.parse_klines(b'{"a": 1}'), [])


class TestBinanceDerivatives(unittest.TestCase):
    def test_open_interest(self):
        parsed = binance.parse_open_interest(BINANCE_OI)
        self.assertEqual(parsed["symbol"], "BTCUSDT")
        self.assertAlmostEqual(parsed["oi"], 95982.052)
        self.assertEqual(parsed["ts"], 1794096000)

    def test_open_interest_bad(self):
        self.assertIsNone(binance.parse_open_interest(b'{"symbol":"X"}'))
        self.assertIsNone(binance.parse_open_interest(b"not-json"))

    def test_funding_compat_and_ts(self):
        parsed = binance.parse_funding(BINANCE_FUNDING)
        self.assertEqual(parsed["symbol"], "BTCUSDT")
        self.assertAlmostEqual(parsed["rate"], 0.00005475)
        self.assertEqual(parsed["mark_price"], "75756.0")  # 兼容旧行为：原样透传
        self.assertEqual(parsed["ts"], 1794096000)

    def test_funding_bad(self):
        self.assertIsNone(binance.parse_funding(b'{"symbol":"X"}'))


class TestOkxTicker(unittest.TestCase):
    def test_normal(self):
        parsed = okx.parse_ticker(OKX_TICKER)
        self.assertEqual(parsed["symbol"], "BTC-USDT")
        self.assertAlmostEqual(parsed["last"], 75790.5)
        self.assertAlmostEqual(parsed["open"], 75928.4)
        self.assertAlmostEqual(parsed["high"], 76251.1)
        self.assertAlmostEqual(parsed["low"], 75569.1)
        self.assertAlmostEqual(parsed["volume"], 2055.9)
        self.assertAlmostEqual(parsed["quote_volume"], 156072345.1)
        self.assertAlmostEqual(parsed["change_pct"], (75790.5 - 75928.4) / 75928.4 * 100.0)
        self.assertEqual(parsed["ts"], 1794096000)

    def test_bad(self):
        self.assertIsNone(okx.parse_ticker(b'{"code":"50011","msg":"err","data":[]}'))
        self.assertIsNone(okx.parse_ticker(b'{"code":"0","data":[]}'))
        self.assertIsNone(okx.parse_ticker(b"not-json"))
        self.assertIsNone(okx.parse_ticker(json.dumps({"code": "0", "data": [{"last": "abc"}]})))


class TestOkxCandles(unittest.TestCase):
    def test_normal_old_to_new(self):
        rows = okx.parse_candles(OKX_CANDLES)
        self.assertEqual([r["open_time"] for r in rows], [1794096000, 1794096060, 1794096120])
        # close_time = open_time + 60 - 1
        self.assertEqual([r["close_time"] for r in rows], [1794096059, 1794096119, 1794096179])
        self.assertAlmostEqual(rows[0]["close"], 75900.1)
        self.assertAlmostEqual(rows[0]["quote_volume"], 1520000.0)
        self.assertEqual([r["confirm"] for r in rows], [0, 1, 1])

    def test_single_row_default_bar(self):
        rows = okx.parse_candles(
            json.dumps({"code": "0", "data": [["1794096000000", "1", "2", "0.5", "1.5", "10", "15", "15", "1"]]})
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["close_time"], 1794096000 + 59)

    def test_bad(self):
        self.assertEqual(okx.parse_candles(b'{"code":"1","data":[]}'), [])
        self.assertEqual(okx.parse_candles(b"not-json"), [])
        self.assertEqual(okx.parse_candles(json.dumps({"code": "0", "data": [["x"]]})), [])


class TestCoinbase(unittest.TestCase):
    def test_stats(self):
        parsed = coinbase.parse_stats(COINBASE_STATS, symbol="BTC")
        self.assertEqual(parsed["symbol"], "BTC")
        self.assertAlmostEqual(parsed["last"], 75790.54)
        self.assertAlmostEqual(parsed["open"], 75928.4)
        self.assertAlmostEqual(parsed["change_pct"], (75790.54 - 75928.4) / 75928.4 * 100.0)
        self.assertIsNone(parsed["ts"])  # stats 接口无时间戳

    def test_stats_bad(self):
        self.assertIsNone(coinbase.parse_stats(b'{"open":"1"}'))
        self.assertIsNone(coinbase.parse_stats(b"not-json"))

    def test_ticker(self):
        parsed = coinbase.parse_ticker(COINBASE_TICKER, symbol="BTC")
        self.assertEqual(parsed["symbol"], "BTC")
        self.assertAlmostEqual(parsed["last"], 75790.54)
        self.assertAlmostEqual(parsed["bid"], 75790.40)
        self.assertAlmostEqual(parsed["ask"], 75790.68)
        self.assertAlmostEqual(parsed["volume"], 2055.9)
        self.assertEqual(parsed["ts"], 1794096000)
        self.assertIsInstance(parsed["ts"], int)

    def test_ticker_no_fraction(self):
        raw = json.dumps({"price": "1.0", "time": "2026-11-08T00:00:00Z"}).encode()
        self.assertEqual(coinbase.parse_ticker(raw)["ts"], 1794096000)

    def test_ticker_bad(self):
        self.assertIsNone(coinbase.parse_ticker(b'{"bid":"1"}'))
        self.assertIsNone(coinbase.parse_ticker(b"not-json"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
