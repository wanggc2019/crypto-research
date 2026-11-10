#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sources / market / http 解析单测（全部离线，用内联样本数据）。"""
import json
import unittest

from crypto_core import http
from crypto_core.market import binance, defillama, okx
from crypto_core.sources import announcements, rss, telegram

RSS_SAMPLE = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item>
    <title>Binance &amp; OKX update</title>
    <link>https://example.com/a?utm_source=x</link>
    <description>&lt;p&gt;hello&lt;/p&gt;</description>
    <pubDate>Sat, 07 Nov 2026 10:00:00 GMT</pubDate>
  </item>
</channel></rss>"""

ATOM_SAMPLE = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>SEC statement</title>
    <link href="https://example.com/sec"/>
    <updated>2026-11-07T10:00:00Z</updated>
  </entry>
</feed>"""

BINANCE_SAMPLE = json.dumps(
    {
        "data": {
            "catalogs": [
                {
                    "catalogId": 48,
                    "articles": [
                        {"title": "BNB listed", "code": "abc", "releaseDate": 1794000000000}
                    ],
                }
            ]
        }
    }
).encode()

OKX_SAMPLE = json.dumps(
    {"data": [{"details": [{"title": "OKX update", "url": "https://okx.com/x", "publishDate": "1794000000000"}]}]}
).encode()

BYBIT_SAMPLE = json.dumps(
    {
        "result": {
            "list": [
                {
                    "title": "Bybit notice",
                    "url": "https://bybit.com/y",
                    "description": "<b>desc</b>",
                    "publishTime": "1794000000000",
                }
            ]
        }
    }
).encode()

TG_SAMPLE = (
    '<div class="tgme_widget_message_text">hello <b>world</b></div>'
    '<div data-post="chan/12"></div>'
).encode()

FUNDING_SAMPLE = json.dumps({"symbol": "BTCUSDT", "lastFundingRate": "0.00085", "markPrice": "76500.1"}).encode()

STABLE_SAMPLE = json.dumps(
    {
        "peggedAssets": [
            {"symbol": "USDT", "price": 0.995},
            {"symbol": "USDC", "price": 1.0001},
            {"symbol": "OTHER", "price": 0.9},
        ]
    }
).encode()

AGG_TRADES_SAMPLE = json.dumps(
    [
        {"a": 1001, "p": "76500.5", "q": "0.5", "T": 1794000000123, "m": False},
        {"a": 1002, "p": "76499.0", "q": "1.2", "T": 1794000005123, "m": True},
        {"a": 1003, "p": "bad", "q": "1", "T": 1794000000789, "m": False},
    ]
).encode()

CEX_FLOWS_SAMPLE = json.dumps(
    {
        "cexs": [
            {
                "name": "Binance",
                "slug": "binance",
                "currentTvl": 1200000000,
                "inflows_24h": 568000000,
                "inflows_1w": -5570000000,
                "inflows_1m": 100,
            },
            {"name": "OKX", "slug": "okx", "currentTvl": "900000000", "inflows_24h": -12000000},
        ]
    }
).encode()

STABLE_SUPPLY_SAMPLE = json.dumps(
    {
        "peggedAssets": [
            {
                "symbol": "USDT",
                "circulating": {"peggedUSD": 120000000000},
                "circulatingPrevDay": {"peggedUSD": 119000000000},
                "circulatingPrevWeek": {"peggedUSD": 118000000000},
            },
            {
                "symbol": "USDC",
                "circulating": {"peggedUSD": 60000000000},
                "circulatingPrevDay": {},
            },
            {"symbol": "FDUSD"},
            {"symbol": "OTHER", "circulating": {"peggedUSD": 1}},
        ]
    }
).encode()

OKX_LIQ_SAMPLE = json.dumps(
    {
        "code": "0",
        "data": [
            {
                "instId": "BTC-USDT-SWAP",
                "uly": "BTC-USDT",
                "details": [
                    {"bkPx": "76000", "sz": "3", "side": "sell", "posSide": "long", "ts": "1794000004000"},
                    {"bkPx": "75900", "sz": "1", "side": "buy", "posSide": "short", "ts": "1794000002000"},
                ],
            },
            {
                "instId": "ETH-USDT-SWAP",
                "uly": "ETH-USDT",
                "details": [
                    {"bkPx": "3000", "sz": "10", "side": "sell", "posSide": "long", "ts": "1794000003000"},
                    {"bkPx": "bad", "sz": "1", "side": "sell", "posSide": "long", "ts": "1794000005000"},
                ],
            },
        ],
    }
).encode()

OKX_INSTRUMENTS_SAMPLE = json.dumps(
    {
        "code": "0",
        "data": [
            {"instId": "BTC-USDT-SWAP", "ctVal": "0.01", "ctValCcy": "BTC"},
            {"instId": "ETH-USDT-SWAP", "ctVal": "0.1", "ctValCcy": "ETH"},
            {"instId": "", "ctVal": "1", "ctValCcy": "X"},
        ],
    }
).encode()


class TestRss(unittest.TestCase):
    def test_rss(self):
        items = rss.parse_feed(RSS_SAMPLE)
        self.assertEqual(len(items), 1)
        self.assertIn("Binance", items[0]["title"])
        self.assertEqual(items[0]["url"], "https://example.com/a")
        self.assertTrue(items[0]["published_at"])

    def test_atom(self):
        items = rss.parse_feed(ATOM_SAMPLE)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["url"], "https://example.com/sec")

    def test_bad_xml(self):
        self.assertEqual(rss.parse_feed(b"<not-xml"), [])


class TestAnnouncements(unittest.TestCase):
    def test_binance(self):
        items = announcements.parse_binance(BINANCE_SAMPLE)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["catalog_id"], 48)
        self.assertTrue(items[0]["url"].startswith("https://www.binance.com/"))

    def test_okx(self):
        items = announcements.parse_okx(OKX_SAMPLE)
        self.assertEqual(items[0]["title"], "OKX update")

    def test_bybit(self):
        items = announcements.parse_bybit(BYBIT_SAMPLE)
        self.assertEqual(items[0]["url"], "https://bybit.com/y")
        self.assertEqual(items[0]["body"], "desc")


class TestTelegramAndPages(unittest.TestCase):
    def test_preview(self):
        items = telegram.parse_telegram_preview(TG_SAMPLE, "chan")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["url"], "https://t.me/chan/12")

    def test_fingerprint(self):
        text, h = telegram.fingerprint(b"<html>a</html>")
        self.assertIn("a", text)
        self.assertEqual(len(h), 64)
        self.assertEqual(telegram.plain_text(b"<b>a</b>  b"), "a b")


class TestMarket(unittest.TestCase):
    def test_funding(self):
        parsed = binance.parse_funding(FUNDING_SAMPLE)
        self.assertEqual(parsed["symbol"], "BTCUSDT")
        self.assertAlmostEqual(parsed["rate"], 0.00085)

    def test_funding_bad(self):
        self.assertIsNone(binance.parse_funding(b'{"symbol":"X"}'))

    def test_open_interest_hist(self):
        raw = b'[{"symbol":"BTCUSDT","sumOpenInterest":"2.0","timestamp":1794177000000},' \
              b'{"symbol":"BTCUSDT","sumOpenInterest":"1.0","timestamp":1794176100000}]'
        rows = binance.parse_open_interest_hist(raw)
        self.assertEqual([r["oi"] for r in rows], [1.0, 2.0])  # 按 ts 旧→新
        self.assertEqual(rows[0]["ts"], 1794176100)

    def test_open_interest_hist_bad(self):
        self.assertEqual(binance.parse_open_interest_hist(b'{"symbol":"BTCUSDT"}'), [])
        self.assertEqual(binance.parse_open_interest_hist(b'not json'), [])
        self.assertEqual(binance.parse_open_interest_hist(None), [])

    def test_stablecoins(self):
        rows = defillama.parse_stablecoins(STABLE_SAMPLE)
        self.assertEqual([r["symbol"] for r in rows], ["USDT", "USDC"])
        self.assertAlmostEqual(rows[0]["dev_bps"], 50.0)


class TestAggTrades(unittest.TestCase):
    def test_parse(self):
        rows = binance.parse_agg_trades(AGG_TRADES_SAMPLE)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["id"], 1001)
        self.assertEqual(rows[0]["ts"], 1794000000)
        self.assertAlmostEqual(rows[0]["quote_qty"], 76500.5 * 0.5)
        self.assertEqual(rows[1]["ts"], 1794000005)

    def test_taker_side_mapping(self):
        raw = [
            {"a": 1, "p": "1", "q": "2", "T": 1000, "m": True},
            {"a": 2, "p": "1", "q": "2", "T": 2000, "m": False},
        ]
        rows = binance.parse_agg_trades(json.dumps(raw).encode())
        self.assertEqual([r["taker_side"] for r in rows], ["sell", "buy"])

    def test_invalid_row_skipped(self):
        rows = binance.parse_agg_trades([{"a": 1, "p": "1", "q": "2", "T": 1000}, "x"])
        self.assertEqual(len(rows), 1)

    def test_bad_input(self):
        for raw in (None, b"<not-json>", b"{}", b"[]"):
            self.assertEqual(binance.parse_agg_trades(raw), [])


class TestCexFlows(unittest.TestCase):
    def test_parse(self):
        rows = defillama.parse_cex_flows(CEX_FLOWS_SAMPLE)
        self.assertEqual([r["name"] for r in rows], ["Binance", "OKX"])
        self.assertAlmostEqual(rows[0]["tvl"], 1200000000.0)
        self.assertAlmostEqual(rows[0]["inflows_24h"], 568000000.0)
        self.assertAlmostEqual(rows[0]["inflows_1w"], -5570000000.0)
        self.assertAlmostEqual(rows[0]["inflows_1m"], 100.0)
        self.assertAlmostEqual(rows[1]["tvl"], 900000000.0)
        self.assertIsNone(rows[1]["inflows_1w"])
        self.assertIsNone(rows[1]["inflows_1m"])

    def test_bad_input(self):
        for raw in (None, b"<not-json>", b"{}", b'{"cexs": {}}', b"[]"):
            self.assertEqual(defillama.parse_cex_flows(raw), [])


class TestStablecoinSupply(unittest.TestCase):
    def test_parse(self):
        rows = defillama.parse_stablecoin_supply(STABLE_SUPPLY_SAMPLE)
        self.assertEqual([r["symbol"] for r in rows], ["USDT", "USDC", "FDUSD"])
        usdt = rows[0]
        self.assertAlmostEqual(usdt["circulating"], 120000000000.0)
        self.assertAlmostEqual(usdt["prev_day"], 119000000000.0)
        self.assertAlmostEqual(usdt["net_issue_24h"], 1000000000.0)
        self.assertAlmostEqual(usdt["net_issue_1w"], 2000000000.0)

    def test_missing_prev_side(self):
        usdc = defillama.parse_stablecoin_supply(STABLE_SUPPLY_SAMPLE)[1]
        self.assertAlmostEqual(usdc["circulating"], 60000000000.0)
        self.assertIsNone(usdc["prev_day"])
        self.assertIsNone(usdc["net_issue_24h"])
        self.assertIsNone(usdc["net_issue_1w"])

    def test_missing_circulating(self):
        fdusd = defillama.parse_stablecoin_supply(STABLE_SUPPLY_SAMPLE)[2]
        self.assertIsNone(fdusd["circulating"])
        self.assertIsNone(fdusd["net_issue_24h"])
        self.assertIsNone(fdusd["net_issue_1w"])

    def test_symbols_filter(self):
        rows = defillama.parse_stablecoin_supply(STABLE_SUPPLY_SAMPLE, symbols=("USDT",))
        self.assertEqual([r["symbol"] for r in rows], ["USDT"])

    def test_bad_input(self):
        for raw in (None, b"<not-json>", b"{}", b'{"peggedAssets": {}}', b"[]"):
            self.assertEqual(defillama.parse_stablecoin_supply(raw), [])


class TestOkxMarket(unittest.TestCase):
    def test_liquidations_flatten_and_sort(self):
        rows = okx.parse_liquidations(OKX_LIQ_SAMPLE)
        self.assertEqual(len(rows), 3)
        self.assertEqual([r["ts"] for r in rows], [1794000002, 1794000003, 1794000004])
        self.assertEqual(
            [r["inst_id"] for r in rows], ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "BTC-USDT-SWAP"]
        )
        first = rows[0]
        self.assertEqual(first["uly"], "BTC-USDT")
        self.assertEqual(first["side"], "buy")
        self.assertEqual(first["pos_side"], "short")
        self.assertAlmostEqual(first["price"], 75900.0)
        self.assertAlmostEqual(first["size"], 1.0)

    def test_liquidations_bad_input(self):
        for raw in (
            None,
            b"<not-json>",
            b"{}",
            b'{"code": "50000", "data": [{"instId": "X", "details": []}]}',
            b'{"code": "0", "data": []}',
        ):
            self.assertEqual(okx.parse_liquidations(raw), [])

    def test_instruments(self):
        inst = okx.parse_instruments(OKX_INSTRUMENTS_SAMPLE)
        self.assertEqual(sorted(inst), ["BTC-USDT-SWAP", "ETH-USDT-SWAP"])
        self.assertAlmostEqual(inst["BTC-USDT-SWAP"]["ct_val"], 0.01)
        self.assertEqual(inst["BTC-USDT-SWAP"]["ct_val_ccy"], "BTC")
        self.assertAlmostEqual(inst["ETH-USDT-SWAP"]["ct_val"], 0.1)

    def test_instruments_missing_ct_val(self):
        raw = json.dumps({"code": "0", "data": [{"instId": "X", "ctVal": "", "ctValCcy": ""}]}).encode()
        inst = okx.parse_instruments(raw)
        self.assertIsNone(inst["X"]["ct_val"])
        self.assertEqual(inst["X"]["ct_val_ccy"], "")

    def test_instruments_bad_input(self):
        for raw in (
            None,
            b"<not-json>",
            b"{}",
            b'{"code": "0", "data": []}',
            b'{"code": "1", "data": [{"instId": "X"}]}',
        ):
            self.assertEqual(okx.parse_instruments(raw), {})


class TestHttp(unittest.TestCase):
    def test_ua_defined(self):
        self.assertIn("Mozilla", http.UA_DEFAULT)

    def test_redirect_handler_supports_308(self):
        self.assertTrue(hasattr(http._Redirect308, "http_error_308"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
