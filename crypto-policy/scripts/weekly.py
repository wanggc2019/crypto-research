#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""weekly.py —— 政策周报：汇总最近 7 天进入法规库的条目。

用法：
    python3 scripts/weekly.py                    # 写 data/reports/weekly-<date>.md
    python3 scripts/weekly.py --notify           # 同时推送到 Telegram
    python3 scripts/weekly.py --dry-run          # 只打印
"""
import argparse
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import brief  # noqa: E402
import index as policy_index  # noqa: E402

import notify  # noqa: E402
from common import REPORT_DIR, ensure_dirs, ts_iso  # noqa: E402


def build_markdown(days=7):
    idx = policy_index.load()
    today = datetime.date.today()
    cutoff = (today - datetime.timedelta(days=days)).isoformat()
    rows = [p for p in idx.get("policies", []) if (p.get("last_seen") or "")[:10] >= cutoff]
    rows.sort(key=lambda p: (p.get("level") or "P9", p.get("last_seen") or ""), reverse=False)
    lines = [
        "# crypto-policy 政策周报 %s" % today.isoformat(),
        "",
        "> 窗口：最近 %d 天｜法规库共 %d 条｜本周新增/更新 %d 条" % (days, len(idx.get("policies", [])), len(rows)),
        "",
    ]
    if not rows:
        lines.append("本周无新增相关政策/法规/案例。")
    for p in rows:
        lines.append("## [%s] %s" % (p.get("level") or "P2", p.get("title") or ""))
        lines.append("")
        lines.append("- 类型：%s｜文号：%s｜状态：%s" % (p.get("doc_type"), p.get("doc_no") or "—", p.get("status")))
        lines.append("- 发布/施行：%s / %s" % (p.get("published_at") or "—", p.get("effective_at") or "—"))
        if p.get("supersedes"):
            lines.append("- 废止：%s" % "、".join(p["supersedes"]))
        if p.get("url"):
            lines.append("- 原文：%s" % p["url"])
        lines.append("")
    return "\n".join(lines), len(rows)


def main(argv=None):
    parser = argparse.ArgumentParser(description="crypto-policy 政策周报")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--notify", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--always", action="store_true", help="窗口内无变化也发（默认静默）")
    args = parser.parse_args(argv)

    ensure_dirs()
    md, count = build_markdown(args.days)
    if args.dry_run:
        print(md)
        return 0
    if count == 0 and not args.always:
        print("本周无变化，静默（--always 可强制发）")
        return 0
    path = os.path.join(REPORT_DIR, "weekly-%s.md" % ts_iso()[:10])
    with open(path, "w", encoding="utf-8") as f:
        f.write(md)
    print("✅ 周报已写入 %s" % path)
    if args.notify:
        text = "📅【crypto-policy】<b>政策周报</b>\n\n" + brief.esc(md[:3500])
        ok, err = notify.send(text)
        if not ok:
            print("❌ 推送失败：%s" % err)
            return 1
        print("✅ 周报已推送")
    return 0


if __name__ == "__main__":
    sys.exit(main())
