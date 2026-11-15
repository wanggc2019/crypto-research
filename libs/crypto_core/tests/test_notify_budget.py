# -*- coding: utf-8 -*-
"""全仓推送台账（notify.ledger）与全局预算（notify.budget）。全部写临时目录、不联网。"""
import datetime
import os
import tempfile
import unittest
from unittest import mock

from crypto_core.notify import budget, ledger, telegram

NOW = datetime.datetime(2026, 11, 16, 12, 0).timestamp()


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"CRYPTO_NOTIFY_LEDGER_DIR": self.tmp.name, "CRYPTO_JOB_LABEL": ""})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def signal(self, module, severity="P1", assets=(), minutes_ago=0):
        ledger.record({"module": module, "kind": "signal", "severity": severity, "assets": list(assets)},
                      ok=True, text="x", now=NOW - minutes_ago * 60)

    def cfg(self, **kw):
        c = dict(budget.DEFAULTS)
        c.update(kw)
        return c


class LedgerTest(_Base):
    def test_record_and_read(self):
        e = ledger.record({"module": "crypto-news", "kind": "signal", "severity": "P1", "assets": ["btc", "BTC"]},
                          ok=True, text="<b>【crypto-news】标题</b>\n正文", now=NOW)
        self.assertEqual(e["assets"], ["BTC"])
        self.assertEqual(e["preview"], "【crypto-news】标题")
        rows = ledger.read("2026-11-16")
        self.assertEqual(len(rows), 1)
        self.assertTrue(ledger.delivered(rows[0]))
        self.assertEqual(ledger.read("2026-11-15"), [])

    def test_infer_module(self):
        with mock.patch.dict(os.environ, {"CRYPTO_JOB_LABEL": "com.crypto-research.crypto-jobs.alert"}):
            self.assertEqual(ledger.infer_module(""), "crypto-jobs")
        with mock.patch.dict(os.environ, {"CRYPTO_JOB_LABEL": "com.crypto-research.watchdog"}):
            self.assertEqual(ledger.infer_module(""), "watchdog")
        self.assertEqual(ledger.infer_module("🔴【crypto-price】BTC 急跌"), "crypto-price")
        self.assertEqual(ledger.infer_module("hello"), "unknown")
        self.assertEqual(ledger.record({"kind": "weird"}, ok=True, text="x", now=NOW)["kind"], "other")

    def test_send_message_records_without_breaking(self):
        with mock.patch.object(telegram, "_send_message_raw", return_value=(True, "")):
            ok, err = telegram.send_message("t", "c", "【crypto-analyst】日报", meta={"kind": "report"})
        self.assertTrue(ok)
        rows = ledger.read(datetime.date.today().isoformat())
        self.assertEqual((rows[-1]["module"], rows[-1]["kind"]), ("crypto-analyst", "report"))
        with mock.patch.object(telegram, "_send_message_raw", return_value=(True, "")), \
                mock.patch.object(ledger, "record", side_effect=RuntimeError("disk")):
            self.assertEqual(telegram.send_message("t", "c", "x"), (True, ""), "台账出错也不能影响发送")


class BudgetDecisionTest(_Base):
    def test_under_cap_sends_cleanly(self):
        self.signal("crypto-news")
        d = budget.decide("crypto-price", "P1", ["BTC"], now=NOW, cfg=self.cfg())
        self.assertEqual((d["action"], d["would_suppress"], d["today_signals"]), ("send", False, 1))

    def test_daily_cap_observe_vs_enforce(self):
        for _ in range(3):
            self.signal("crypto-news")
        obs = budget.decide("crypto-policy", "P1", now=NOW, cfg=self.cfg(daily_signal_cap=3))
        self.assertEqual((obs["action"], obs["would_suppress"]), ("send", True))
        self.assertIn("daily_cap:3/3", obs["reasons"])
        enf = budget.decide("crypto-policy", "P1", now=NOW, cfg=self.cfg(daily_signal_cap=3, mode="enforce"))
        self.assertEqual(enf["action"], "suppress")

    def test_p0_always_passes_and_is_not_counted(self):
        for _ in range(3):
            self.signal("crypto-news")
        self.signal("crypto-onchain", severity="P0")
        d = budget.decide("crypto-price", "P0", ["BTC"], now=NOW, cfg=self.cfg(daily_signal_cap=1, mode="enforce"))
        self.assertEqual((d["action"], d["reasons"], d["today_signals"]), ("send", [], 3))

    def test_correlation_other_module_same_asset_in_window(self):
        self.signal("crypto-price", assets=["BTC"], minutes_ago=10)
        self.signal("crypto-news", assets=["ETH"], minutes_ago=5)
        self.signal("crypto-x-kol", assets=["BTC"], minutes_ago=45)       # 窗口外
        d = budget.decide("crypto-x-kol", "P1", ["btc", "macro"], now=NOW, cfg=self.cfg(mode="enforce"))
        self.assertEqual([r["module"] for r in d["related"]], ["crypto-price"])
        self.assertEqual((d["action"], d["reasons"]), ("suppress", ["correlated:crypto-price"]))
        same = budget.decide("crypto-price", "P1", ["BTC"], now=NOW, cfg=self.cfg(mode="enforce"))
        self.assertEqual(same["related"], [], "同模块自己的连续信号不算跨模块重复")

    def test_off_mode(self):
        for _ in range(20):
            self.signal("crypto-news", assets=["BTC"])
        d = budget.decide("crypto-price", "P1", ["BTC"], now=NOW, cfg=self.cfg(mode="off"))
        self.assertEqual((d["action"], d["reasons"]), ("send", []))

    def test_config_defaults_and_bad_mode(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "b.yaml")
            with open(p, "w") as f:
                f.write("mode: yolo\ndaily_signal_cap: 4\nunknown: 1\n")
            with mock.patch.object(budget, "_CONFIG_PATH", p):
                cfg = budget.load_config()
            self.assertEqual((cfg["mode"], cfg["daily_signal_cap"]), ("observe", 4))
            self.assertNotIn("unknown", cfg)
            with mock.patch.object(budget, "_CONFIG_PATH", os.path.join(d, "missing.yaml")):
                self.assertEqual(budget.load_config(), budget.DEFAULTS)

    def test_watch_assets(self):
        self.assertEqual(budget.watch_assets(["btc", "Macro", None, "ETH", "BTC", "sol"]), ["BTC", "ETH"])


class SendSignalTest(_Base):
    def test_enforce_suppresses_without_sending(self):
        for _ in range(2):
            self.signal("crypto-news")
        sender = mock.Mock(return_value=(True, ""))
        with mock.patch.object(budget, "load_config", return_value=self.cfg(mode="enforce", daily_signal_cap=2)):
            ok, err = budget.send_signal("t", "c", "【crypto-policy】x", "crypto-policy", "P1", now=NOW, sender=sender)
        self.assertTrue(ok)
        self.assertTrue(err.startswith("suppressed: daily_cap"))
        sender.assert_not_called()
        last = ledger.read("2026-11-16")[-1]
        self.assertEqual((last["decision"]["action"], last["ok"]), ("suppress", False))
        self.assertFalse(ledger.delivered(last))

    def test_observe_sends_and_records_once(self):
        sender = mock.Mock(return_value=(True, ""))
        with mock.patch.object(budget, "load_config", return_value=self.cfg()):
            ok, err = budget.send_signal("t", "c", "x", "crypto-price", "P1", assets=["BTC"], key="k",
                                         now=NOW, sender=sender, sender_args=(False,))
        self.assertEqual((ok, err), (True, ""))
        sender.assert_called_once_with("t", "c", "x", False)
        rows = ledger.read("2026-11-16")
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["kind"], rows[0]["module"], rows[0]["assets"]), ("signal", "crypto-price", ["BTC"]))

    def test_real_sender_path_records_once(self):
        with mock.patch.object(budget, "load_config", return_value=self.cfg()), \
                mock.patch.object(telegram, "_send_message_raw", return_value=(True, "")):
            budget.send_signal("t", "c", "x", "crypto-news", "P1", sender=telegram.send_message)
        rows = ledger.read(datetime.date.today().isoformat())
        self.assertEqual([r["kind"] for r in rows], ["signal"], "真实 send_message 只记一条（带 signal 元信息）")

    def test_budget_error_never_blocks(self):
        sender = mock.Mock(return_value=(True, ""))
        with mock.patch.object(budget, "decide", side_effect=ValueError("boom")):
            ok, _ = budget.send_signal("t", "c", "x", "crypto-news", "P1", sender=sender)
        self.assertTrue(ok)
        sender.assert_called_once()


if __name__ == "__main__":
    unittest.main()
