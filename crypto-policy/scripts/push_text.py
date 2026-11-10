#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""push_text.py —— 把解读草稿 Markdown 发到 Telegram（供 DSH 解读任务使用）。

用法：
    ./scripts/push_text.py data/reports/interpret-<id>.md --id <doc_id>
    ./scripts/push_text.py data/reports/interpret-<id>.md --dry-run
"""
import argparse
import html
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import notify  # noqa: E402
from common import DATA_DIR, STATE_DIR, safe_json_dump, safe_json_load, ts_iso  # noqa: E402

MAX_LEN = 4096
INTERPRETED_PATH = os.path.join(STATE_DIR, "interpreted.json")
PENDING_DIR = os.path.join(DATA_DIR, "pending")


def _title_of(md, fallback):
    for line in md.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
        if line.startswith("> "):
            continue
    return fallback


def _mark_pushed(doc_id, report_path):
    data = safe_json_load(INTERPRETED_PATH, {"items": {}})
    if not isinstance(data, dict):
        data = {"items": {}}
    data.setdefault("items", {})
    data["items"][doc_id] = {"engine": "dsh", "status": "pushed", "report": report_path, "ts": ts_iso()}
    safe_json_dump(INTERPRETED_PATH, data)
    pending = os.path.join(PENDING_DIR, doc_id + ".json")
    if os.path.isfile(pending):
        done = os.path.join(PENDING_DIR, "done")
        os.makedirs(done, exist_ok=True)
        os.replace(pending, os.path.join(done, doc_id + ".json"))


def main(argv=None):
    parser = argparse.ArgumentParser(description="推送解读草稿 Markdown 到 Telegram")
    parser.add_argument("path", help="Markdown 文件路径")
    parser.add_argument("--id", help="文档 id；提供则标记已推送并归档 pending")
    parser.add_argument("--title", help="卡片标题（默认取 Markdown 一级标题）")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--silent", action="store_true", help="静默发送（不响铃）")
    args = parser.parse_args(argv)

    with open(args.path, "r", encoding="utf-8") as f:
        md = f.read().strip()
    if not md:
        print("❌ 空文件")
        return 2

    title = args.title or _title_of(md, os.path.basename(args.path))
    body = html.escape(md, quote=False)
    text = "🖋【crypto-policy】<b>解读草稿</b>\n<b>%s</b>\n\n%s\n\n⚠️ AI 草稿，非法律意见；以官方原文为准。" % (
        html.escape(title, quote=False),
        body,
    )
    if len(text) > MAX_LEN:
        text = text[: MAX_LEN - 1] + "…"

    if args.dry_run:
        print(text)
        return 0

    ok, err = notify.send(text, silent=args.silent)
    if not ok:
        print("❌ 发送失败：%s" % err)
        return 1
    if args.id:
        _mark_pushed(args.id, os.path.abspath(args.path))
    print("✅ 解读草稿已发送（%s）" % title)
    return 0


if __name__ == "__main__":
    sys.exit(main())
