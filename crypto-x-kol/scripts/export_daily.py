#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""export_daily.py —— 把当日 KOL 原始条目导出为跨模块只读契约（kol/daily/v1）。

用法：python3 scripts/export_daily.py [--date YYYY-MM-DD]
产物：data/exports/daily-<date>.json
只读 data/raw/<date>/posts.jsonl，不改任何状态。
"""
import argparse
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEEP = ("id", "kind", "handle", "name", "tier", "tags", "text", "quote", "has_media", "url", "score", "severity")


def main(argv=None):
    ap = argparse.ArgumentParser(description="导出当日 KOL 条目")
    ap.add_argument("--date", default=None)
    ap.add_argument("--data-dir", default=os.path.join(HERE, "data"))
    args = ap.parse_args(argv)
    day = args.date or datetime.datetime.now().strftime("%Y-%m-%d")
    raw = os.path.join(args.data_dir, "raw", day, "posts.jsonl")
    out_dir = os.path.join(args.data_dir, "exports")
    os.makedirs(out_dir, exist_ok=True)
    items = []
    if os.path.exists(raw):
        for line in open(raw, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                post = json.loads(line)
            except ValueError:
                continue
            items.append({k: post[k] for k in KEEP if k in post})
    payload = {"schema": "kol/daily/v1", "date": day,
               "generated_at": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
               "items": items}
    path = os.path.join(out_dir, "daily-%s.json" % day)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print("%s（%d 条）" % (path, len(items)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
