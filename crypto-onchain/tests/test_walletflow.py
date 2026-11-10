#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""walletflow.py 的离线单测：Transfer 解码、阈值过滤、去重、标签方向、扫描区间。

运行：cd crypto-onchain && ../.venv/bin/python -m unittest tests.test_walletflow -v
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(ROOT), "libs", "crypto_core", "src"))

import walletflow  # noqa: E402

USDT = {"symbol": "USDT", "address": "0xdAC17F958D2ee523a2206206994597C13D831ec7", "decimals": 6}
TRANSFER = walletflow.TRANSFER_TOPIC
FROM = "0x1111111111111111111111111111111111111111"
TO = "0x2222222222222222222222222222222222222222"


def _topic(addr):
    return "0x" + "0" * 24 + addr.lower().replace("0x", "")


def log(value_usd, block=100, log_index=0, tx="0xabc", token_addr=USDT["address"], frm=FROM, to=TO):
    return {
        "address": token_addr,
        "topics": [TRANSFER, _topic(frm), _topic(to)],
        "data": hex(int(value_usd * 1_000_000)),
        "blockNumber": hex(block),
        "logIndex": hex(log_index),
        "transactionHash": tx,
    }


class TestDecode(unittest.TestCase):
    def test_large_transfer(self):
        rec = walletflow.decode_transfer(log(8_000_000), USDT, min_usd=5_000_000)
        self.assertIsNotNone(rec)
        self.assertEqual(rec["symbol"], "USDT")
        self.assertAlmostEqual(rec["usd"], 8_000_000)
        self.assertEqual(rec["from"], FROM)
        self.assertEqual(rec["to"], TO)
        self.assertEqual(rec["block"], 100)
        self.assertEqual(rec["log_index"], 0)

    def test_below_threshold(self):
        self.assertIsNone(walletflow.decode_transfer(log(1_000_000), USDT, min_usd=5_000_000))

    def test_mint_burn_kind_and_exclusion(self):
        mint = log(9_000_000, frm="0x" + "0" * 40)
        rec = walletflow.decode_transfer(mint, USDT, min_usd=5_000_000)
        self.assertEqual(rec["kind"], "mint")
        rows = walletflow.select_and_classify([mint], USDT, 5_000_000)
        self.assertEqual(rows, [])  # 默认排除
        rows2 = walletflow.select_and_classify([mint], USDT, 5_000_000, include_mint_burn=True)
        self.assertEqual(len(rows2), 1)

    def test_bad_logs(self):
        self.assertIsNone(walletflow.decode_transfer(None, USDT, 0))
        self.assertIsNone(walletflow.decode_transfer({"topics": []}, USDT, 0))
        bad = log(6_000_000)
        bad["data"] = "0xzz"
        self.assertIsNone(walletflow.decode_transfer(bad, USDT, 0))


class TestSelect(unittest.TestCase):
    def test_filter_and_dedup(self):
        logs = [log(8_000_000), log(1_000_000), log(8_000_000)]  # 第三条与第一条同 tx/log_index
        rows = walletflow.select_and_classify(logs, USDT, 5_000_000)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["direction"], "unknown")

    def test_labels_direction(self):
        labels = {TO: {"name": "Binance 热钱包", "type": "cex"}}
        rows = walletflow.select_and_classify([log(6_000_000)], USDT, 5_000_000, labels=labels)
        self.assertEqual(rows[0]["direction"], "cex_in")
        self.assertEqual(rows[0]["counterparty"]["name"], "Binance 热钱包")

        labels2 = {FROM: {"name": "OKX", "type": "cex"}}
        rows2 = walletflow.select_and_classify([log(6_000_000)], USDT, 5_000_000, labels=labels2)
        self.assertEqual(rows2[0]["direction"], "cex_out")

        # 双边都是交易所 → 内部划转，不判为入所/出所
        labels3 = {FROM: {"name": "Binance", "type": "cex"}, TO: {"name": "OKX", "type": "cex"}}
        rows3 = walletflow.select_and_classify([log(6_000_000)], USDT, 5_000_000, labels=labels3)
        self.assertEqual(rows3[0]["direction"], "cex_internal")

        # 做市商（type=mm）不参与方向判定
        labels4 = {TO: {"name": "Jump", "type": "mm"}}
        rows4 = walletflow.select_and_classify([log(6_000_000)], USDT, 5_000_000, labels=labels4)
        self.assertEqual(rows4[0]["direction"], "unknown")


class TestRange(unittest.TestCase):
    def test_first_run_init_window(self):
        start, end, skipped = walletflow.pending_range(None, 1000, 120, 120)
        self.assertEqual((start, end, skipped), (881, 1000, 0))

    def test_incremental(self):
        start, end, skipped = walletflow.pending_range(1000, 1010, 120, 120)
        self.assertEqual((start, end, skipped), (1001, 1010, 0))

    def test_gap_is_capped(self):
        start, end, skipped = walletflow.pending_range(1000, 1500, 120, 120)
        self.assertEqual((start, end), (1381, 1500))
        self.assertEqual(skipped, 499 - 120 + 1)

    def test_no_new_blocks(self):
        # 游标已在最新块 → 本轮不需要扫（返回 None，而不是空区间）
        self.assertEqual(walletflow.pending_range(1000, 1000, 120, 120), (None, None, 0))
        self.assertIsNone(walletflow.pending_range(1000, None, 120, 120)[0])


class TestSummaryAndFixture(unittest.TestCase):
    def test_summarize(self):
        recs = [{"usd": 8_000_000, "symbol": "USDT"}, {"usd": 12_000_000, "symbol": "USDC"}]
        meta = {"chains": [
            {"name": "ethereum", "latest_block": 100, "from_block": 90, "to_block": 100,
             "scanned_blocks": 11, "n_large": 2, "usd_total": 20_000_000},
            {"name": "bsc", "latest_block": 200, "from_block": 180, "to_block": 200,
             "scanned_blocks": 21, "n_large": 0, "usd_total": 0, "error": None},
        ]}
        s = walletflow.summarize(recs, meta, None)
        self.assertEqual(s["n_large"], 2)
        self.assertAlmostEqual(s["usd_total"], 20_000_000)
        self.assertEqual([c["name"] for c in s["chains"]], ["ethereum", "bsc"])
        self.assertEqual(s["source"], "eth_getLogs/public-rpc")

    def test_fixture_records_multichain(self):
        cfg = {
            "min_usd": 5_000_000,
            "chains": [
                {"name": "ethereum", "tokens": [
                    USDT, {"symbol": "USDC", "address": "0xA0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
                           "decimals": 6}]},
                {"name": "bsc", "tokens": [
                    {"symbol": "USDT", "address": "0x55d398326f99059fF775485246999027B3197955",
                     "decimals": 18}]},
            ],
        }
        records = walletflow.fixture_records(os.path.join(ROOT, "tests", "fixtures"), cfg)
        self.assertEqual(len(records), 3)  # ETH: 8M USDT + 12M USDC；BSC: 7M USDT（mint/低于阈值被排除）
        self.assertEqual({(r["chain"], r["symbol"]) for r in records},
                         {("ethereum", "USDT"), ("ethereum", "USDC"), ("bsc", "USDT")})


if __name__ == "__main__":
    unittest.main()
