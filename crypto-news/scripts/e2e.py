#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""e2e.py —— crypto-news 离线端到端回放（make gate / CI 使用）。

用 tests/fixtures/news_items.jsonl 在临时 data 目录跑一遍 score.py，断言：
- 报告 / alerts / exports 都生成，exports 符合 news/daily/v1；
- 同 URL 重复条目被去重、促销条目被丢弃；
- score.py 按真实时钟丢弃 >7 天的条目，所以回放时把 published_at 改写为「现在 - N 小时」；
- 不读写生产 data/（通过 CRYPTO_NEWS_DATA_DIR 指向临时目录），不联网、不推送。
"""
import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                                "libs", "crypto_core", "src"))
from crypto_core import contracts  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "news_items.jsonl")


def _require(ok, message):
    if not ok:
        print("❌ crypto-news e2e: %s" % message)
        sys.exit(1)


def main():
    data_dir = tempfile.mkdtemp(prefix="news-e2e-")
    try:
        now = datetime.datetime.now().astimezone()
        date = now.strftime("%Y-%m-%d")
        raw_dir = os.path.join(data_dir, "raw", date)
        os.makedirs(raw_dir)
        with open(FIXTURE, encoding="utf-8") as src, \
                open(os.path.join(raw_dir, "items.jsonl"), "w", encoding="utf-8") as dst:
            for i, line in enumerate(x for x in src if x.strip()):
                item = json.loads(line)
                item["published_at"] = (now - datetime.timedelta(hours=i + 1)).isoformat(timespec="seconds")
                dst.write(json.dumps(item, ensure_ascii=False) + "\n")
        env = dict(os.environ, CRYPTO_NEWS_DATA_DIR=data_dir)
        proc = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "score.py"), "--date", date],
                              cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
        _require(proc.returncode == 0, "score.py rc=%s\n%s" % (proc.returncode, proc.stderr[-2000:]))

        report = os.path.join(data_dir, "reports", "%s.md" % date)
        alerts_path = os.path.join(data_dir, "state", "alerts-%s.json" % date)
        export_path = os.path.join(data_dir, "exports", "daily-%s.json" % date)
        for path in (report, alerts_path, export_path):
            _require(os.path.isfile(path), "缺少输出 %s" % os.path.relpath(path, data_dir))

        with open(export_path, encoding="utf-8") as f:
            export = json.load(f)
        problems = contracts.validate("news/daily/v1", export)
        _require(not problems, "exports 不符合契约 news/daily/v1：%s" % problems)
        _require(export.get("date") == date, "exports date 不对")
        _require(isinstance(export.get("items"), list), "exports items 不是 list")
        counts = export.get("counts") or {}
        _require(sum(counts.get(k, 0) for k in ("P0", "P1", "P2")) >= 1, "没有任何事件被分级：%r" % counts)

        titles = [it.get("title", "") for it in export["items"]]
        _require(not any("tournament" in t for t in titles), "促销条目未被丢弃")
        hack = [it for it in export["items"] if it.get("url") == "https://example.com/defi-hack"]
        _require(len(hack) == 1, "同 URL 重复条目未去重或被漏掉：%d 条" % len(hack))
        _require(hack[0].get("severity") in ("P0", "P1"), "$45M 被盗事件应为 P0/P1，实际 %s" % hack[0].get("severity"))
        print("✅ crypto-news e2e：counts=%s" % json.dumps(counts, ensure_ascii=False))
        return 0
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
