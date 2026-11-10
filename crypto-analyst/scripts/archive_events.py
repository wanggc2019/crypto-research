#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""archive_events.py —— 每日把跨模块事件归档到月度历史（供月报/季报/年报使用）。

背景：exports 仅保留 7 天、raw 90 天，长周期报告需要自己的归档。
用法：python3 scripts/archive_events.py [--date YYYY-MM-DD]
产物：data/history/<YYYY-MM>.json（按月一份，永久保留，去重）
"""
import argparse
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "scripts"))
import daily_brief  # noqa: E402


def load(path):
    if os.path.exists(path):
        try:
            return json.load(open(path, encoding="utf-8"))
        except ValueError:
            return []
    return []


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None)
    args = ap.parse_args(argv)
    today = args.date or datetime.datetime.now().strftime("%Y-%m-%d")
    month = today[:7]
    out = os.path.join(HERE, "data", "history", "%s.json" % month)
    records = load(out)
    seen = {r.get("key") for r in records}
    added = 0
    sources = [
        ("news", "crypto-news", "daily-%s.json", "P0/P1"),
        ("policy", "crypto-policy", "policy-%s.json", "policy"),
        ("kol", "crypto-kol", "daily-%s.json", "kol"),
    ]
    for src, module, pattern, kind in sources:
        data = daily_brief.read_json(os.path.join(REPO, module, "data", "exports", pattern % today))
        if not data:
            continue
        for it in (data.get("items") or []):
            if src == "news" and it.get("severity") not in ("P0", "P1"):
                continue
            title = it.get("title") or it.get("text") or ""
            key = "%s|%s|%s" % (src, it.get("handle") or "", title[:80])
            if key in seen:
                continue
            seen.add(key)
            records.append({"date": today, "source": src, "kind": kind, "key": key,
                            "severity": it.get("severity"), "title": title[:200],
                            "assets": it.get("assets") or [], "url": it.get("url")})
            added += 1
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=1)
    print("%s：新增 %d 条（累计 %d）" % (out, added, len(records)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
