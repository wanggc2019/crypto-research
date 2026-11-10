#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""notify 模板与凭据校验单测（不联网）。"""
import unittest
from unittest import mock

from crypto_core.notify import MAX_LEN, telegram, templates

OK_TOKEN = "123456789:" + "A" * 35


def make_alert(level="P0"):
    return {
        "level": level,
        "category": "exchange_custody",
        "titles": ["Binance <suspends> withdrawals"],
        "assets": ["BNB", "BTC"],
        "count": 2,
        "age_hours": 3.5,
        "score": 88.0,
        "components": {"S": 100, "T": 90, "M": 80, "C": 50, "D": 0.9},
        "hard": ["exchange_withdrawal"],
        "url": "https://example.com/a?x=1&y=2",
    }


class TestTemplates(unittest.TestCase):
    def test_p0_card(self):
        text = templates.format_alert(make_alert("P0"))
        self.assertIn("🚨", text)
        self.assertIn("P0", text)
        self.assertIn("88.0", text)
        self.assertIn("BNB", text)
        self.assertIn("&lt;suspends&gt;", text)
        self.assertIn("&amp;y=2", text)

    def test_p1_card_and_length(self):
        text = templates.format_alert(make_alert("P1"))
        self.assertIn("⚠️", text)
        self.assertLessEqual(len(text), MAX_LEN)

    def test_module_badge_and_job(self):
        text = templates.format_alert({
            "severity": "P1", "category": "price", "title": "BTC 急跌",
            "module": "crypto-price", "job": "com.crypto-research.crypto-price",
        })
        self.assertIn("【crypto-price】", text)
        self.assertIn("任务：com.crypto-research.crypto-price", text)

    def test_accepts_new_alert_shape(self):
        text = templates.format_alert(
            {
                "severity": "P0",
                "category": "price",
                "title": "BTC 急跌",
                "assets": ["BTC"],
                "urls": ["https://example.com"],
                "score": 80,
            }
        )
        self.assertIn("P0", text)
        self.assertIn("BTC 急跌", text)


class TestCredentials(unittest.TestCase):
    def test_ok(self):
        _, _, problems = telegram.check_credentials(
            {"TELEGRAM_BOT_TOKEN": OK_TOKEN, "TELEGRAM_CHAT_ID": "123"}
        )
        self.assertEqual(problems, [])

    def test_bad(self):
        _, _, problems = telegram.check_credentials(
            {"TELEGRAM_BOT_TOKEN": "你的token", "TELEGRAM_CHAT_ID": "你的chat_id"}
        )
        self.assertEqual(len(problems), 2)

    def test_negative_chat_id_allowed(self):
        _, _, problems = telegram.check_credentials(
            {"TELEGRAM_BOT_TOKEN": OK_TOKEN, "TELEGRAM_CHAT_ID": "-100123456"}
        )
        self.assertEqual(problems, [])




class TestDigestPoints(unittest.TestCase):
    def test_points_are_numbered(self):
        text = templates.format_alert(
            {
                "level": "P1",
                "category": "protocol_security",
                "title": "吴说周精选",
                "assets": ["BTC"],
                "count": 1,
                "points": ["币安 63 亿美金被盗", "香港加密报税框架", "美国中期选举大跌"],
            }
        )
        self.assertIn("<b>要点</b>：", text)
        self.assertIn("1. 币安 63 亿美金被盗", text)
        self.assertIn("3. 美国中期选举大跌", text)
        self.assertNotIn("•", text)


class TestChineseAndTrust(unittest.TestCase):
    def _alert(self, **kw):
        base = {
            "level": "P0",
            "category": "protocol_security",
            "titles": ["Binance hacked for $6.3B"],
            "assets": ["BNB", "BTC"],
            "count": 2,
            "confirmed": False,
            "score": 35.0,
            "age_hours": 29.9,
            "hard": ["hack_large"],
            "url": "https://news.google.com/rss/articles/abc",
        }
        base.update(kw)
        return base

    def test_chinese_vocab_and_trust_hint(self):
        text = templates.format_alert(self._alert())
        self.assertIn("协议安全", text)      # 类别中文化
        self.assertIn("大额被盗", text)      # 硬条件中文化
        self.assertIn("未确认", text)        # 低可信源显式标注
        self.assertIn("聚合站", text)        # Google News 跳转提示
        self.assertIn("<a href=", text)      # 链接文字化

    def test_confirmed_alert_has_no_unconfirmed_mark(self):
        text = templates.format_alert(self._alert(confirmed=True))
        self.assertNotIn("未确认", text)
        self.assertIn("已确认", text)

    def test_no_component_details(self):
        text = templates.format_alert(self._alert())
        self.assertNotIn("S=", text)
        self.assertNotIn("D=", text)


class TestSendMessage(unittest.TestCase):
    """send_message 的 payload 组装（mock tg_call，不联网）。"""

    def _payload(self, **kwargs):
        with mock.patch.object(telegram, "tg_call", return_value={"ok": True}) as m:
            ok, err = telegram.send_message("token", "123", "<b>x</b>", **kwargs)
        self.assertTrue(ok)
        self.assertEqual(err, "")
        return m.call_args[0][2]

    def test_default_not_silent(self):
        payload = self._payload()
        self.assertNotIn("disable_notification", payload)
        self.assertEqual(payload["parse_mode"], "HTML")

    def test_silent_flag(self):
        self.assertTrue(self._payload(silent=True)["disable_notification"])

    def test_footer_has_time_no_location(self):
        text = self._payload()["text"]
        self.assertIn("🕒 发送时间", text)
        self.assertNotRegex(text, r"Asia|UTC|本地时区|\+\d")


class TestSendDocument(unittest.TestCase):
    """sendDocument 的 multipart 组装（mock urlopen，不联网）。"""

    def test_build_multipart(self):
        body, ctype = telegram.build_multipart(
            {"chat_id": "123"}, "document", "report.md", b"# hi", boundary="BOUND")
        self.assertTrue(ctype.endswith("boundary=BOUND"))
        self.assertIn(b'name="chat_id"', body)
        self.assertIn(b'filename="report.md"', body)
        self.assertIn(b"# hi", body)
        self.assertIn(b"--BOUND--", body)

    def test_send_document_ok(self):
        import os
        import tempfile

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"ok": true}'

        with tempfile.NamedTemporaryFile(suffix=".md", delete=False) as handle:
            handle.write(b"# report")
            path = handle.name
        try:
            with mock.patch.object(telegram.urllib.request, "urlopen",
                                   return_value=_Resp()) as m:
                ok, err = telegram.send_document("token", "123", path, caption="cap")
            self.assertTrue(ok)
            self.assertEqual(err, "")
            req = m.call_args[0][0]
            self.assertIn("sendDocument", req.full_url)
        finally:
            os.remove(path)

    def test_send_document_missing_file(self):
        ok, err = telegram.send_document("token", "123", "/no/such/report.md")
        self.assertFalse(ok)
        self.assertIn("读取文件失败", err)


if __name__ == "__main__":
    unittest.main(verbosity=2)
