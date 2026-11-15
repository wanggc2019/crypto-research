# -*- coding: utf-8 -*-
"""跨天首轮无新增（当天还没有 raw/items.jsonl）：评分与推送都应视为「0 条」正常结束（rc=0），
而不是 notify 找不到 alerts 文件返回 1（2026-11-16 00:01 线上实例）。"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATE = "2026-11-16"


class EmptyDayTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="news-empty-")
        # 只放空 .env 之外的最小环境：凭据为空也不应触发真实发送
        self.env = dict(os.environ, CRYPTO_NEWS_DATA_DIR=self.tmp.name,
                        TELEGRAM_BOT_TOKEN="", TELEGRAM_CHAT_ID="")

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, script, *args):
        return subprocess.run([sys.executable, os.path.join(ROOT, "scripts", script)] + list(args),
                              cwd=ROOT, env=self.env, capture_output=True, text=True, timeout=120)

    def test_score_writes_empty_outputs(self):
        proc = self._run("score.py", "--date", DATE)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        alerts = os.path.join(self.tmp.name, "state", "alerts-%s.json" % DATE)
        export = os.path.join(self.tmp.name, "exports", "daily-%s.json" % DATE)
        self.assertTrue(os.path.isfile(alerts), "当天无 raw 也要写 alerts（0 条）")
        with open(alerts, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["items"], [])
        with open(export, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["schema"], "news/daily/v1")
        self.assertEqual(data["items"], [])

    def test_notify_after_empty_score_is_ok(self):
        self.assertEqual(self._run("score.py", "--date", DATE).returncode, 0)
        proc = self._run("notify.py", "--dry-run", "--date", DATE)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
