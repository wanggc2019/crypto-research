#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""notify_report.py —— 全仓推送台账统计（只读）：今天/最近 N 天实际打扰了几次、谁最吵、预算会拦掉多少。

用法：
    python3 tools/notify_report.py              # 今天
    python3 tools/notify_report.py --days 7     # 最近 7 天
    python3 tools/notify_report.py --json
"""
import argparse
import collections
import datetime
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "libs", "crypto_core", "src"))
from crypto_core.notify import budget, ledger  # noqa: E402


def summarize(entries):
    out = {"total_delivered": 0, "by_kind": collections.Counter(), "by_module": collections.Counter(),
           "signals": collections.Counter(), "failed": 0, "suppressed": 0, "would_suppress": 0,
           "reasons": collections.Counter(), "examples": []}
    for e in entries:
        d = e.get("decision") or {}
        if d.get("action") == "suppress":
            out["suppressed"] += 1
        elif not e.get("ok"):
            out["failed"] += 1
            continue
        else:
            out["total_delivered"] += 1
            out["by_kind"][e.get("kind") or "other"] += 1
            out["by_module"][e.get("module") or "unknown"] += 1
            if e.get("kind") == "signal":
                out["signals"][e.get("severity") or "?"] += 1
        if d.get("would_suppress"):
            out["would_suppress"] += 1
            for r in d.get("reasons") or []:
                out["reasons"][r.split(":", 1)[0]] += 1
            if len(out["examples"]) < 8:
                out["examples"].append({"ts": e.get("ts"), "module": e.get("module"), "severity": e.get("severity"),
                                        "reasons": d.get("reasons"), "preview": e.get("preview")})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="全仓推送台账统计")
    ap.add_argument("--days", type=int, default=1)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    today = datetime.date.today()
    dates = [(today - datetime.timedelta(days=i)).isoformat() for i in range(max(1, args.days))][::-1]
    entries = [e for d in dates for e in ledger.read(d)]
    s = summarize(entries)
    cfg = budget.load_config()
    if args.json:
        print(json.dumps({"dates": dates, "config": cfg, **{k: (dict(v) if isinstance(v, collections.Counter) else v)
                                                              for k, v in s.items()}}, ensure_ascii=False, indent=1))
        return 0
    days = len(dates)
    print("推送台账 %s ~ %s（%d 天）｜预算 mode=%s cap=%s/天 窗口=%smin" % (
        dates[0], dates[-1], days, cfg["mode"], cfg["daily_signal_cap"], cfg["correlation_window_min"]))
    print("送达 %d 条（日均 %.1f）｜发送失败 %d｜预算拦截 %d" % (
        s["total_delivered"], s["total_delivered"] / days, s["failed"], s["suppressed"]))
    print("按类别：%s" % (", ".join("%s %d" % kv for kv in s["by_kind"].most_common()) or "—"))
    print("按模块：%s" % (", ".join("%s %d" % kv for kv in s["by_module"].most_common()) or "—"))
    sig_total = sum(s["signals"].values())
    print("即时信号：%d 条（日均 %.1f；%s）" % (sig_total, sig_total / days,
                                         ", ".join("%s %d" % kv for kv in sorted(s["signals"].items())) or "—"))
    print("若开启 enforce 会拦截：%d 条（%s）" % (s["would_suppress"],
                                         ", ".join("%s %d" % kv for kv in s["reasons"].most_common()) or "—"))
    for ex in s["examples"]:
        print("  - %s %s %s %s｜%s" % (ex["ts"], ex["module"], ex["severity"], ",".join(ex["reasons"] or []),
                                    ex["preview"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
