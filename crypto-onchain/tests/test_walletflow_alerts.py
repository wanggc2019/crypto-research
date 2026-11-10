#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""walletflow_alerts.py 的离线单测：分级、日限额扩容、静默、去重、推测备注、预览不写状态。

运行：cd crypto-onchain && ../.venv/bin/python -m unittest tests.test_walletflow_alerts -v
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(ROOT), "libs", "crypto_core", "src"))

import walletflow_alerts as wa  # noqa: E402

CFG = {
    "enabled": True, "min_usd": 10_000_000, "only_p0": False, "p0_usd": 50_000_000,
    "p0_cex_in_usd": 25_000_000, "daily_cap": 10, "burst_trigger_ratio": 2,
    "burst_cap": 30, "max_per_round": 5, "quiet_hours": "", "guess": True,
    "whale_usd": 100_000_000, "whale_cex_in_usd": 50_000_000, "whale_daily_cap": 5,
    "smart": {"enabled": False},
    "special": {"enabled": True, "manual": False, "p0_transfers_today": 600, "max_messages": 30,
                "btc_move_24h_pct": 6.0, "cex_outflow_bps": 150,
                "stablecoin_outflow_usd": -1_500_000_000},
}
LABELS = {
    "0x28c6c06298d514db089934071355e5743bf21d60": {"name": "Binance 14", "type": "cex"},
}
NOW_DAY = datetime(2026, 11, 9, 12, 0, 0)
NOW_NIGHT = datetime(2026, 11, 9, 23, 30, 0)


def rec(usd, tx="0xabc", log_index=0, direction="unknown", frm="0x1111111111111111111111111111111111111111",
        to="0x2222222222222222222222222222222222222222", symbol="USDT"):
    return {"tx": tx, "log_index": log_index, "usd": usd, "direction": direction,
            "from": frm, "to": to, "symbol": symbol, "chain": "ethereum"}


def paths(tmp):
    return {"root": tmp, "raw": os.path.join(tmp, "raw"), "state": os.path.join(tmp, "state"),
            "logs": os.path.join(tmp, "logs")}


class TestSeverityAndCap(unittest.TestCase):
    def test_severity(self):
        self.assertEqual(wa.severity_of(rec(60_000_000), CFG), "P0")
        self.assertEqual(wa.severity_of(rec(30_000_000, direction="cex_in"), CFG), "P0")
        self.assertEqual(wa.severity_of(rec(30_000_000), CFG), "P1")
        self.assertEqual(wa.severity_of(rec(10_000_000, direction="cex_in"), CFG), "P1")

    def test_daily_cap(self):
        self.assertEqual(wa.daily_cap(CFG, 5), 10)
        self.assertEqual(wa.daily_cap(CFG, 20), 10)       # 刚好 trigger 不扩容
        self.assertEqual(wa.daily_cap(CFG, 40), 20)       # (40-20)//2 = 10 → 20
        self.assertEqual(wa.daily_cap(CFG, 1000), 30)     # 硬上限

    def test_quiet_hours(self):
        self.assertTrue(wa.in_quiet_hours(NOW_NIGHT, "22:00-07:00"))
        self.assertFalse(wa.in_quiet_hours(NOW_DAY, "22:00-07:00"))
        # 设计约束全天即时发：空字符串/off 视为不静默
        self.assertFalse(wa.in_quiet_hours(NOW_NIGHT, ""))
        self.assertFalse(wa.in_quiet_hours(NOW_NIGHT, "off"))

    def test_special_mode(self):
        self.assertTrue(wa.special_mode(CFG, 1, {"btc_change_24h": -7.0}))
        self.assertTrue(wa.special_mode(CFG, 650, {}))
        self.assertFalse(wa.special_mode(CFG, 350, {}))
        self.assertTrue(wa.special_mode(CFG, 1, {"cex_net_24h_bps": -200}))
        self.assertTrue(wa.special_mode(CFG, 1, {"stablecoin_net_issue_24h_usd": -2e9}))
        self.assertFalse(wa.special_mode(CFG, 1, {"btc_change_24h": -1.0}))
        manual = dict(CFG, special=dict(CFG["special"], manual=True))
        self.assertTrue(wa.special_mode(manual, 1, {}))
        # 特殊期仍有硬上限 30 条/天：messages=30 时不再发
        at_cap = {"date": NOW_DAY.strftime("%Y-%m-%d"), "messages": 30, "sent": [], "suppressed": []}
        ts_s, sup_s, _ = wa.select_alerts([rec(150_000_000, tx="0xs")], manual, at_cap, NOW_DAY, LABELS, {}, 1)
        self.assertEqual(ts_s, [])
        self.assertEqual(sup_s[0]["reason"], "daily_cap")


class TestGuess(unittest.TestCase):
    def test_link_guess(self):
        hist = {"0x1111111111111111111111111111111111111111": {
            "links": {"Binance 14": 3}, "count": 3}}
        g = wa.guess_counterparty(rec(20_000_000), LABELS, hist)
        self.assertIn("疑似与 Binance 14 关联", g)

    def test_frequency_guess(self):
        hist = {"0x1111111111111111111111111111111111111111": {"links": {}, "count": 6}}
        self.assertIn("做市商", wa.guess_counterparty(rec(20_000_000), LABELS, hist))

    def test_no_guess(self):
        self.assertIsNone(wa.guess_counterparty(rec(20_000_000), LABELS, {}))

    def test_known_label_not_guessed(self):
        r = rec(20_000_000, to="0x28c6c06298d514db089934071355e5743bf21d60")
        self.assertIsNone(wa.guess_counterparty(r, LABELS, {}))


class TestSelectAndRender(unittest.TestCase):
    def test_below_threshold_filtered(self):
        to_send, _sup, _st = wa.select_alerts([rec(5_000_000)], CFG, {}, NOW_DAY, LABELS, {}, 1)
        self.assertEqual(to_send, [])

    def test_dedupe(self):
        st = {"date": NOW_DAY.strftime("%Y-%m-%d"), "messages": 0,
              "sent": [{"key": "0xabc:0"}], "suppressed": []}
        to_send, _sup, _st = wa.select_alerts([rec(20_000_000)], CFG, st, NOW_DAY, LABELS, {}, 1)
        self.assertEqual(to_send, [])

    def test_quiet_suppresses_p1_but_not_p0(self):
        # 默认配置全天即时（quiet_hours 为空）；该用例显式启用 22:00-07:00 验证 P0 穿透
        cfg_quiet = dict(CFG, quiet_hours="22:00-07:00")
        _ts, sup, _st = wa.select_alerts([rec(20_000_000, tx="0xa")], cfg_quiet, {}, NOW_NIGHT, LABELS, {}, 1)
        self.assertEqual(sup[0]["reason"], "quiet_hours")
        ts2, sup2, _st2 = wa.select_alerts([rec(60_000_000, tx="0xb")], cfg_quiet, {}, NOW_NIGHT, LABELS, {}, 1)
        self.assertEqual(len(ts2), 1)
        self.assertEqual(sup2, [])

    def test_cap_suppresses_but_p0_survives(self):
        # today_large=1 → cap 仍为 10；messages 已到 10 → P1 被限额抑制
        st = {"date": NOW_DAY.strftime("%Y-%m-%d"), "messages": 10, "sent": [], "suppressed": []}
        _ts, sup, _st = wa.select_alerts([rec(20_000_000, tx="0xa")], CFG, st, NOW_DAY, LABELS, {}, 1)
        self.assertEqual(sup[0]["reason"], "daily_cap")
        # P0 受 burst_cap（30）限制而不是 daily_cap，仍可发
        ts2, _sup2, _st2 = wa.select_alerts(
            [rec(60_000_000, tx="0xb")], CFG,
            {"date": NOW_DAY.strftime("%Y-%m-%d"), "messages": 10, "sent": [], "suppressed": []},
            NOW_DAY, LABELS, {}, 1)
        self.assertEqual(len(ts2), 1)
        # only_p0=true：P1 即使有额度也不发（记 suppressed，reason=only_p0）
        p0_only = dict(CFG, only_p0=True)
        ts4, sup4, _st4 = wa.select_alerts([rec(20_000_000, tx="0xd")], p0_only, {}, NOW_DAY, LABELS, {}, 1)
        self.assertEqual(ts4, [])
        self.assertEqual(sup4[0]["reason"], "only_p0")
        ts5, _sup5, _st5 = wa.select_alerts([rec(60_000_000, tx="0xe")], p0_only, {}, NOW_DAY, LABELS, {}, 1)
        self.assertEqual(len(ts5), 1)
        # 巨鲸保护：已发满 10 条后，普通 P0 抑制，但 ≥$100M 仍发送
        st_cap = {"date": NOW_DAY.strftime("%Y-%m-%d"), "messages": 10, "sent": [], "suppressed": []}
        tw, _sw, _stw = wa.select_alerts([rec(150_000_000, tx="0xw")], p0_only, st_cap, NOW_DAY, LABELS, {}, 1)
        self.assertEqual(len(tw), 1)
        self.assertTrue(wa.is_whale(rec(150_000_000), p0_only))
        # 巨鲸额外额度用尽（messages=15）后也抑制
        st_full18 = {"date": NOW_DAY.strftime("%Y-%m-%d"), "messages": 15, "sent": [], "suppressed": []}
        tw2, _sw2, _stw2 = wa.select_alerts([rec(150_000_000, tx="0xw2")], p0_only, st_full18, NOW_DAY, LABELS, {}, 1)
        self.assertEqual(tw2, [])

        # only_p0 常规也守住 daily_cap=10（messages 已满 10 → 普通 P0 也抑制）
        st_full = {"date": NOW_DAY.strftime("%Y-%m-%d"), "messages": 10, "sent": [], "suppressed": []}
        ts6, sup6, _st6 = wa.select_alerts([rec(60_000_000, tx="0xf")], p0_only, st_full, NOW_DAY, LABELS, {}, 1)
        self.assertEqual(ts6, [])
        self.assertEqual(sup6[0]["reason"], "daily_cap")

        # 特殊期（manual）解除日限额：P1 也能发
        special = dict(CFG, special=dict(CFG["special"], manual=True))
        ts3, _sup3, _st3 = wa.select_alerts(
            [rec(20_000_000, tx="0xc")], special,
            {"date": NOW_DAY.strftime("%Y-%m-%d"), "messages": 10, "sent": [], "suppressed": []},
            NOW_DAY, LABELS, {}, 1)
        self.assertEqual(len(ts3), 1)

    def test_round_limit(self):
        records = [rec(20_000_000 + i * 1_000_000, tx="0x%02d" % i) for i in range(8)]
        ts, sup, _st = wa.select_alerts(records, CFG, {}, NOW_DAY, LABELS, {}, 1)
        self.assertEqual(len(ts), 5)
        self.assertTrue(any(s["reason"] == "round_limit" for s in sup))

    def test_render_with_guess_marks_speculation(self):
        hist = {"0x1111111111111111111111111111111111111111": {"links": {"Binance 14": 3}, "count": 3}}
        text = wa.render_message([rec(20_000_000)], CFG, LABELS, hist, NOW_DAY)
        self.assertIn("流向：未知 → 未知（unknown · 无法判断买卖方向）（推测：疑似与 Binance 14 关联", text)
        self.assertIn("【crypto-onchain】", text)

    def test_smart_low_context_suppressed(self):
        cfg = dict(CFG, smart={"enabled": True, "min_score": 2, "stress_move_24h_pct": 3.0,
                               "news_lookback_hours": 6, "cex_outflow_bps": 100,
                               "stablecoin_outflow_usd": 500_000_000})
        flat = {"assets": {"BTC": {"change_24h_pct": 0.1}}}
        ts, sup, _ = wa.select_alerts([rec(60_000_000, tx="0xF")], cfg, {}, NOW_DAY, LABELS, {}, 1,
                                      context=flat)
        self.assertEqual(ts, [])
        self.assertEqual(sup[0]["reason"], "low_context")
        stress = {"assets": {"BTC": {"change_24h_pct": -4.0}}}
        ts2, _sup2, _ = wa.select_alerts([rec(60_000_000, tx="0xE")], cfg, {}, NOW_DAY, LABELS, {}, 1,
                                          context=stress)
        self.assertEqual(len(ts2), 1)

    def test_same_tx_multi_leg_kept_once(self):
        records = [rec(60_000_000, tx="0xsame", log_index=1),
                   rec(60_000_000, tx="0xsame", log_index=2,
                       frm="0x2222222222222222222222222222222222222222",
                       to="0x1111111111111111111111111111111111111111")]
        ts, _sup, _st = wa.select_alerts(records, CFG, {}, NOW_DAY, LABELS, {}, 1)
        self.assertEqual(len(ts), 1)

    def test_render_groups_same_pair(self):
        records = [rec(60_000_000, tx="0xa"), rec(60_000_000, tx="0xb")]
        text = wa.render_message(records, CFG, LABELS, {}, NOW_DAY)
        self.assertIn("（2 笔）", text)
        self.assertEqual(text.count("→ 0x"), 1)  # from→to 行只有一行（流向行是实体名）

    def test_render_cex_in_and_no_speculation(self):
        r = rec(30_000_000, direction="cex_in", to="0x28c6c06298d514db089934071355e5743bf21d60")
        text = wa.render_message([r], CFG, LABELS, {}, NOW_DAY)
        self.assertIn("流向：未知 → 交易所 Binance 14（cex_in · 转入交易所）", text)
        self.assertNotIn("推测", text)

    def test_flow_unknown_when_no_labels(self):
        text = wa.render_message([rec(20_000_000)], CFG, LABELS, {}, NOW_DAY)
        self.assertIn("流向：未知 → 未知（unknown · 无法判断买卖方向）", text)


class TestRareMode(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wf-rare-")
        os.makedirs(os.path.join(self.tmp, "state"), exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _cfg(self):
        return dict(CFG, enabled=True, rare_only=True,
                    rare={"min_usd": 500_000_000, "flow_anomaly_ratio": 3,
                          "flow_min_usd": 100_000_000, "daily_cap": 2})

    def test_rare_single_500m_unknown(self):
        res = wa.process([rec(600_000_000, tx="0xrare1")], self._cfg(), paths(self.tmp),
                         LABELS, dry_run=True, now=NOW_DAY)
        self.assertEqual(res["n"], 1)
        self.assertIn("单向未知", res["text"])

    def test_rare_ignores_small_and_labelled_direction(self):
        res = wa.process([rec(100_000_000, tx="0xsmall")], self._cfg(), paths(self.tmp),
                         LABELS, dry_run=True, now=NOW_DAY)
        self.assertFalse(res["sent"])
        self.assertEqual(res["text"], "")

    def test_flow_anomaly_detected(self):
        import datetime as _dt
        # 过去 7 天同时段各 20M 净流入；今天同时段 120M → 6 倍异常
        for d in range(3, 10):
            date = "2026-11-%02d" % d
            os.makedirs(os.path.join(self.tmp, "raw", date), exist_ok=True)
            ts = int(_dt.datetime(2026, 11, d, 22, 30).timestamp())
            with open(os.path.join(self.tmp, "raw", date, "walletflow.jsonl"), "w", encoding="utf-8") as f:
                f.write(json.dumps({"ts": ts, "usd": 20_000_000, "direction": "cex_in"}) + "\n")
        today = "2026-11-10"
        os.makedirs(os.path.join(self.tmp, "raw", today), exist_ok=True)
        ts = int(_dt.datetime(2026, 11, 10, 22, 20).timestamp())
        with open(os.path.join(self.tmp, "raw", today, "walletflow.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"ts": ts, "usd": 120_000_000, "direction": "cex_in"}) + "\n")
        now = _dt.datetime(2026, 11, 10, 23, 5)
        an = wa.flow_anomaly(paths(self.tmp), self._cfg(), now)
        self.assertIsNotNone(an)
        self.assertGreater(an["ratio"], 3)


class TestTagEnrich(unittest.TestCase):
    def test_parse_etherscan_tag(self):
        html = '<html>\n\tBinance 14 | Address: 0x28c6c062...43bf21d60 | Etherscan\n</html>'
        self.assertEqual(wa.parse_etherscan_tag(html), "Binance 14")
        self.assertEqual(wa.parse_etherscan_tag("not html"), "")

    def test_enrich_uses_cache_without_network(self):
        tmp = tempfile.mkdtemp(prefix="wf-tags-")
        self.addCleanup(shutil.rmtree, tmp, True)
        os.makedirs(os.path.join(tmp, "state"), exist_ok=True)
        addr = "0x1111111111111111111111111111111111111111"
        with open(os.path.join(tmp, "state", "address-tags.json"), "w", encoding="utf-8") as f:
            json.dump({addr: {"tag": "Coinbase 29", "ts": int(__import__("time").time())}}, f)
        tags = wa.enrich_tags([rec(20_000_000)], paths(tmp), {"enrich": {"etherscan_tags": True}})
        self.assertEqual(tags.get(addr), "Coinbase 29")


class TestProcessDryRun(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wf-alerts-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_dry_run_no_state(self):
        os.makedirs(os.path.join(self.tmp, "state"), exist_ok=True)
        res = wa.process([rec(30_000_000)], CFG, paths(self.tmp), LABELS, dry_run=True, now=NOW_DAY)
        self.assertEqual(res["n"], 1)
        self.assertIn("链上大额转账", res["text"])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "state", "walletflow-alerts.json")))

    def test_build_history(self):
        raw = os.path.join(self.tmp, "raw", "2026-11-09")
        os.makedirs(raw, exist_ok=True)
        with open(os.path.join(raw, "walletflow.jsonl"), "w", encoding="utf-8") as f:
            for i in range(3):
                f.write(json.dumps({"from": "0x1111111111111111111111111111111111111111",
                                    "to": "0x28c6c06298d514db089934071355e5743bf21d60"}) + "\n")
        hist = wa.build_history(os.path.join(self.tmp, "raw"), LABELS)
        item = hist["0x1111111111111111111111111111111111111111"]
        self.assertEqual(item["links"].get("Binance 14"), 3)


if __name__ == "__main__":
    unittest.main()
