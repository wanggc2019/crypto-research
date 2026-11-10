#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""holdings.py 的离线单测：BTC/ETH/ERC20 解析、余额换算、变化阈值、缓存 TTL、坏数据降级。

运行：cd crypto-onchain && ../.venv/bin/python -m unittest tests.test_holdings -v
全部离线（fixtures + mock），不联网、不发送、不读凭据。
"""
import contextlib
import datetime
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(ROOT), "libs", "crypto_core", "src"))

import holdings  # noqa: E402
import holdings_collect  # noqa: E402

import common  # noqa: E402

BTC = "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq"
HOLDER = "0x2222222222222222222222222222222222222222"
TOKEN = {"address": "0xdAC17F958D2ee523a2206206994597C13D831ec7", "symbol": "USDT", "decimals": 6}
NOW = datetime.datetime(2026, 11, 10, 12, 0, 0)
PRICE = {"assets": {"BTC": {"price": 100000.0}, "ETH": {"price": 2000.0}}}


def watchlist(btc_threshold=5.0, erc20_threshold=5.0):
    return {
        "schema_version": 1,
        "entries": [
            {"chain": "btc", "address": BTC, "label": "示例地址 A（未核实）",
             "source_note": "示例：规范文档地址，未核实", "threshold_pct": btc_threshold},
            {"chain": "erc20", "address": HOLDER, "token": TOKEN, "price_usd": 1.0,
             "label": "示例地址 B（未核实）", "source_note": "示例：占位地址，未核实",
             "threshold_pct": erc20_threshold},
        ],
    }


def btc_payload(sats=1000000):
    return {"chain_stats": {"funded_txo_sum": int(sats) + 100, "spent_txo_sum": 100,
                            "tx_count": 3}}


def fixture(btc_sats=1000000, token_raw=1500000, btc_error=None):
    data = {"btc:%s" % BTC: btc_payload(btc_sats), "erc20:%s" % HOLDER: hex(int(token_raw))}
    if btc_error:
        data["btc:%s" % BTC] = {"error": btc_error}
    return data


class TestBtcParse(unittest.TestCase):
    def test_balance_from_chain_stats(self):
        result = holdings.parse_btc_address({
            "chain_stats": {"funded_txo_sum": 18223423, "spent_txo_sum": 14293, "tx_count": 125},
            "mempool_stats": {"funded_txo_sum": 999, "spent_txo_sum": 0},
        })
        self.assertTrue(result["ok"])
        self.assertAlmostEqual(result["balance"], (18223423 - 14293) / 1e8)
        self.assertEqual(result["balance_sat"], 18209130)
        self.assertEqual(result["tx_count"], 125)

    def test_bad_payloads(self):
        self.assertFalse(holdings.parse_btc_address(None)["ok"])
        self.assertFalse(holdings.parse_btc_address({"chain_stats": {}})["ok"])
        self.assertFalse(holdings.parse_btc_address({"chain_stats": {"funded_txo_sum": "x"}})["ok"])
        neg = holdings.parse_btc_address({"chain_stats": {"funded_txo_sum": 1, "spent_txo_sum": 2}})
        self.assertFalse(neg["ok"])


class TestEthParse(unittest.TestCase):
    def test_eth_balance(self):
        result = holdings.parse_eth_balance("0x4ff3dc194c4b4b20")
        self.assertTrue(result["ok"])
        self.assertAlmostEqual(result["balance"], int("0x4ff3dc194c4b4b20", 16) / 1e18)

    def test_bad_hex(self):
        for value in (None, "", "0xzz", "0x", 123):
            self.assertFalse(holdings.parse_eth_balance(value)["ok"])

    def test_erc20_data_encoding(self):
        data = holdings.erc20_balance_of_data(HOLDER)
        self.assertEqual(data, "0x70a08231" + "0" * 24 + "2" * 40)
        self.assertEqual(len(data), 10 + 64)
        self.assertIsNone(holdings.erc20_balance_of_data("0x1234"))
        self.assertIsNone(holdings.erc20_balance_of_data(None))

    def test_erc20_balance_decimals(self):
        raw = 1500000
        result = holdings.parse_erc20_balance(hex(raw), 6)
        self.assertTrue(result["ok"])
        self.assertAlmostEqual(result["balance"], 1.5)
        self.assertAlmostEqual(holdings.parse_erc20_balance(hex(10**18), 18)["balance"], 1.0)

    def test_bad_erc20(self):
        self.assertFalse(holdings.parse_erc20_balance("0xzz", 6)["ok"])
        self.assertFalse(holdings.parse_erc20_balance(hex(1), -1)["ok"])
        self.assertFalse(holdings.parse_erc20_balance(hex(1), "x")["ok"])


class TestConfig(unittest.TestCase):
    def test_entries_normalized(self):
        entries, problems = holdings.normalize_entries(watchlist())
        self.assertEqual(problems, [])
        self.assertEqual([e["chain"] for e in entries], ["btc", "erc20"])
        self.assertEqual(entries[1]["token"]["symbol"], "USDT")
        self.assertEqual(entries[1]["threshold_pct"], 5.0)
        self.assertEqual(entries[1]["price_usd"], 1.0)

    def test_threshold_default(self):
        wl = watchlist()
        del wl["entries"][0]["threshold_pct"]
        entries, _ = holdings.normalize_entries(wl)
        self.assertEqual(entries[0]["threshold_pct"], holdings.DEFAULT_THRESHOLD_PCT)

    def test_reject_missing_source_note(self):
        wl = watchlist()
        del wl["entries"][0]["source_note"]
        entries, problems = holdings.normalize_entries(wl)
        self.assertEqual(len(entries), 1)
        self.assertIn("source_note", problems[0])

    def test_erc20_requires_token(self):
        wl = watchlist()
        del wl["entries"][1]["token"]
        entries, problems = holdings.normalize_entries(wl)
        self.assertIn("token", problems[0])
        self.assertEqual(len(entries), 1)

    def test_reject_bad_chain_and_duplicates(self):
        wl = watchlist()
        wl["entries"].append({"chain": "sol", "address": "x", "label": "x", "source_note": "x"})
        wl["entries"].append(dict(wl["entries"][0]))
        _entries, problems = holdings.normalize_entries(wl)
        self.assertTrue(any("chain" in p for p in problems))
        self.assertTrue(any("重复" in p for p in problems))

    def test_missing_entries(self):
        self.assertEqual(holdings.normalize_entries({}), ([], []))
        self.assertEqual(holdings.normalize_entries({"entries": "x"}), ([], ["entries 必须是列表"]))


class TestChangeAndCache(unittest.TestCase):
    def test_change_pct(self):
        self.assertAlmostEqual(holdings.compute_change_pct(100.0, 110.0), 10.0)
        self.assertAlmostEqual(holdings.compute_change_pct(100.0, 90.0), -10.0)
        self.assertIsNone(holdings.compute_change_pct(0, 10))
        self.assertIsNone(holdings.compute_change_pct(None, 10))
        self.assertIsNone(holdings.compute_change_pct(10, None))

    def test_cache_ttl(self):
        now = int(NOW.timestamp())
        self.assertTrue(holdings.is_fresh({"updated_ts": now - 60}, now, 1800))
        self.assertFalse(holdings.is_fresh({"updated_ts": now - 1800}, now, 1800))
        self.assertFalse(holdings.is_fresh({"updated_ts": now - 10}, now, 0))
        self.assertFalse(holdings.is_fresh({}, now, 1800))
        self.assertFalse(holdings.is_fresh({"updated_ts": "x"}, now, 1800))

    def test_price_resolution(self):
        entry = {"chain": "btc", "token": None, "price_usd": None}
        self.assertEqual(holdings.price_for(entry, PRICE), 100000.0)
        self.assertIsNone(holdings.price_for(entry, {}))
        fixed = {"chain": "erc20", "token": TOKEN, "price_usd": 1.0}
        self.assertEqual(holdings.price_for(fixed, PRICE), 1.0)
        self.assertIsNone(holdings.price_for({"chain": "btc", "token": None, "price_usd": None}, {"assets": {}}))

    def test_threshold_events(self):
        rows = [
            {"chain": "btc", "address": BTC, "label": "A", "symbol": "BTC",
             "prev_balance": 1.0, "balance": 1.06, "change_pct": 6.0, "threshold_pct": 5.0},
            {"chain": "eth", "address": "0x" + "1" * 40, "label": "B", "symbol": "ETH",
             "prev_balance": 1.0, "balance": 1.01, "change_pct": 1.0, "threshold_pct": 5.0},
            {"chain": "erc20", "address": HOLDER, "label": "C", "symbol": "USDT",
             "prev_balance": 0, "balance": 5.0, "change_pct": None, "threshold_pct": 5.0},
            {"chain": "btc", "address": BTC, "label": "D", "symbol": "BTC",
             "prev_balance": 1.0, "balance": 5.0, "change_pct": 400.0, "threshold_pct": 5.0,
             "severity": "P1"},
        ]
        events = holdings.build_events(rows, "P2")
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["severity"], "P2")
        self.assertEqual(events[0]["key"], "btc:%s:up" % BTC)
        self.assertEqual(events[1]["severity"], "P1")
        # 恰好等于阈值也触发（>=）
        exact = [dict(rows[0], change_pct=5.0)]
        self.assertEqual(len(holdings.build_events(exact, "P2")), 1)

    def test_select_push_events(self):
        events = [{"severity": "P2", "key": "a"}, {"severity": "P1", "key": "b"}]
        self.assertEqual([e["key"] for e in holdings.select_push_events(events, {})], ["b"])
        self.assertEqual(len(holdings.select_push_events(events, {"push_severities": ["P0", "P1", "P2"]})), 2)
        self.assertEqual(holdings.select_push_events([{"severity": "P2"}], {}), [])

    def test_render_alert(self):
        text = holdings.render_alert(holdings.build_events([
            {"chain": "btc", "address": BTC, "label": "示例地址 A（未核实）", "symbol": "BTC",
             "prev_balance": 0.1, "balance": 0.12, "change_pct": 20.0, "threshold_pct": 5.0},
        ]))
        self.assertIn("示例地址 A", text)
        self.assertIn("↑", text)
        self.assertIn("不做归属推断", text)
        for word in ("某机构", "某巨鲸", "某基金", "某个人"):
            self.assertNotIn(word, text)


class TestCollect(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="holdings-")
        self.paths = common.data_paths(self.tmp)
        self.app = {"holdings": {"enabled": True, "cache_ttl_seconds": 1800,
                                 "history_max_lines": 2000, "alerts": {"enabled": True, "push": False}}}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _collect(self, now=NOW, fixtures=True, dry_run=False, wl=None, **kwargs):
        return holdings_collect.collect(
            {"app": self.app}, self.paths, PRICE, now=now,
            fixtures_dir="/nonexistent" if fixtures else None,
            fixture=fixture(**kwargs) if fixtures else None,
            dry_run=dry_run, watchlist=wl or watchlist(),
        )

    def _read_state(self):
        with open(os.path.join(self.tmp, "state", "holdings.json"), encoding="utf-8") as f:
            return json.load(f)

    def _history_lines(self):
        path = os.path.join(self.tmp, "state", "holdings-history.jsonl")
        with open(path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def test_first_round_writes_state_and_history(self):
        meta = self._collect()
        self.assertTrue(meta["enabled"])
        self.assertFalse(meta["cached"])
        self.assertEqual(meta["n"], 2)
        btc = [e for e in meta["entries"] if e["chain"] == "btc"][0]
        self.assertAlmostEqual(btc["balance"], 0.01)
        self.assertAlmostEqual(btc["usd"], 1000.0)
        self.assertIsNone(btc["change_pct"])
        usdt = [e for e in meta["entries"] if e["chain"] == "erc20"][0]
        self.assertAlmostEqual(usdt["balance"], 1.5)
        self.assertAlmostEqual(usdt["usd"], 1.5)
        self.assertEqual(meta["changes"], [])
        self.assertEqual(meta["n_errors"], 0)
        state = self._read_state()
        self.assertEqual(set(state["entries"]), {"btc:" + BTC, "erc20:" + HOLDER})
        self.assertEqual(len(self._history_lines()), 2)

    def test_second_round_change_and_event(self):
        self._collect()
        meta = self._collect(btc_sats=1100000)
        btc = [e for e in meta["entries"] if e["chain"] == "btc"][0]
        self.assertAlmostEqual(btc["change_pct"], 10.0)
        self.assertEqual(len(meta["changes"]), 1)
        event = meta["changes"][0]
        self.assertEqual(event["chain"], "btc")
        self.assertEqual(event["severity"], "P2")
        events_path = os.path.join(self.tmp, "raw", NOW.strftime("%Y-%m-%d"), "holdings-events.jsonl")
        with open(events_path, encoding="utf-8") as f:
            self.assertEqual(len([line for line in f if line.strip()]), 1)

    def test_below_threshold_no_event(self):
        self._collect()
        meta = self._collect(btc_sats=1010000)  # +1%
        self.assertAlmostEqual(meta["entries"][0]["change_pct"], 1.0)
        self.assertEqual(meta["changes"], [])

    def test_cache_within_ttl_skips_fetch(self):
        self._collect()
        before = len(self._history_lines())
        meta = holdings_collect.collect({"app": self.app}, self.paths, PRICE, now=NOW,
                                fixtures_dir=None, watchlist=watchlist())
        self.assertTrue(meta["cached"])
        self.assertEqual(meta["n"], 2)
        self.assertEqual(meta["n_errors"], 0)
        self.assertEqual(len(self._history_lines()), before)
        self.assertEqual(meta["changes"], [])
        # 缓存命中时仍带上次采集的余额
        self.assertAlmostEqual(meta["entries"][0]["balance"], 0.01)

    def test_single_address_failure_isolated(self):
        meta = self._collect(btc_error="timeout")
        self.assertEqual(meta["n"], 2)
        self.assertEqual(meta["n_errors"], 1)
        self.assertIn("timeout", meta["entries"][0]["error"])
        self.assertIsNotNone(meta["entries"][1]["balance"])

    def test_failure_keeps_previous_balance(self):
        self._collect()
        meta = self._collect(btc_error="rpc down")
        btc = [e for e in meta["entries"] if e["chain"] == "btc"][0]
        self.assertTrue(btc["stale"])
        self.assertAlmostEqual(btc["balance"], 0.01)
        self.assertIsNone(btc["change_pct"])

    def test_missing_fixture_degrades(self):
        meta = holdings_collect.collect({"app": self.app}, self.paths, PRICE, now=NOW,
                                fixtures_dir="/nonexistent", fixture={}, watchlist=watchlist())
        self.assertEqual(meta["n_errors"], 2)
        self.assertEqual(meta["changes"], [])

    def test_dry_run_writes_nothing(self):
        meta = self._collect(dry_run=True, btc_sats=1100000)
        self.assertTrue(meta["enabled"])
        self.assertEqual(os.listdir(self.tmp), [])

    def test_disabled(self):
        self.app["holdings"]["enabled"] = False
        meta = self._collect()
        self.assertEqual(meta, {"enabled": False})

    def test_history_is_bounded(self):
        self.app["holdings"]["history_max_lines"] = 3
        for i in range(5):
            self._collect(now=NOW + datetime.timedelta(minutes=30 * i))
        self.assertEqual(len(self._history_lines()), 3)

    def test_problems_recorded(self):
        wl = watchlist()
        del wl["entries"][0]["source_note"]
        meta = self._collect(wl=wl)
        self.assertTrue(meta["problems"])
        self.assertEqual(meta["n"], 1)

    def test_push_credentials_missing_degrades(self):
        self._collect()
        meta = self._collect(btc_sats=1100000)
        self.assertEqual(len(meta["changes"]), 1)
        cfg = {"push": True, "push_severities": ["P2"], "daily_cap": 3}
        with mock.patch.object(holdings_collect.common, "load_bot_env", return_value={}):
            sent, error, _suppressed = holdings_collect.push_change_alerts(meta["changes"], cfg, self.paths, NOW)
        self.assertEqual(sent, 0)
        self.assertIn("凭据", error)

    def test_cli_status(self):
        self._collect()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = holdings_collect.main(["--status", "--data-dir", self.tmp, "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(buf.getvalue())
        self.assertEqual(data["n"], 2)
        self.assertEqual(data["state_file"], os.path.join(self.tmp, "state", "holdings.json"))


if __name__ == "__main__":
    unittest.main()
