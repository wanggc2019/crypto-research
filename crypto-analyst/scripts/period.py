#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""period.py —— 季报/年报（多月聚合，复用月报逻辑）。"""
import argparse
import datetime
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "scripts")); sys.path.insert(0, os.path.join(REPO, "libs", "crypto_core", "src"))
import core
import daily_brief  # noqa: E402


def month_list(end_date, months):
    d = datetime.date.fromisoformat(end_date).replace(day=1)
    out = []
    for i in range(months):
        m = (d - datetime.timedelta(days=1)).replace(day=1) if i else d
        out.append(m.strftime("%Y-%m"))
        d = m
    return sorted(out)

def _weekday(date_str):
    import datetime
    return "周" + "一二三四五六日"[datetime.date.fromisoformat(date_str).weekday()]


def label_for(end_date, months):
    d = datetime.date.fromisoformat(end_date)
    if months == 12:
        return "%dY" % (d.year - 1)
    q = (d.month - 1) // 3 + 1
    return "%dQ%d（上季）" % (d.year, q - 1 if q > 1 else 4)

def build(cfg, end_date, months, base_dir=HERE):
    months_list = month_list(end_date, months)
    data = core.load_all(base_dir, cfg.get("watchlist") or core.SYMS)
    rows = []
    for sym, bars in data.items():
        w = [b for b in bars if b["date"][:7] in months_list]
        if not w: continue
        o, c = w[0]["open"], w[-1]["close"]
        rows.append((sym, {"open": o, "close": c, "high": max(b["high"] for b in w),
                           "low": min(b["low"] for b in w), "chg": (c / o - 1) * 100 if o else None}))
    hist = []
    for m in months_list:
        h = daily_brief.read_json(os.path.join(HERE, "data", "history", "%s.json" % m)) or []
        hist += h
    label = label_for(end_date, months)
    L = ["# crypto-analyst %s（%s ~ %s，%s）" % ("年报" if months == 12 else "季报", months_list[0], months_list[-1], _weekday(end_date)), "",
         "> 数据：Binance 日线 + 事件归档｜研究用途，不构成投资建议", "",
         "## 一、区间行情", "", "| 币 | 区间开 | 区间收 | 涨跌 | 高 | 低 |", "|---|---:|---:|---:|---:|---:|"]
    for sym, w in rows:
        L.append("| %s | %s | %s | %s%% | %s | %s |" % (sym, f"{w['open']:,.2f}", f"{w['close']:,.2f}",
                 f"{w['chg']:+.1f}" if w['chg'] is not None else "n/a", f"{w['high']:,.2f}", f"{w['low']:,.2f}"))
    L += ["", "## 二、区间事件（归档 %d 条）" % len(hist), ""]
    for r in hist[:15]:
        L.append("- [%s] %s" % (r.get("source"), (r.get("title") or "")[:100]))
    if not hist: L.append("- 无归档事件")
    L += ["", "> ⚠️ 研究/方法验证，不构成投资建议。", ""]
    return label, "\n".join(L)

def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("--date", default=None); ap.add_argument("--months", type=int, default=3)
    ap.add_argument("--dry-run", action="store_true"); ap.add_argument("--notify", action="store_true")
    args = ap.parse_args(argv)
    date_str = args.date or datetime.datetime.now().strftime("%Y-%m-%d")
    label, md = build(daily_brief.load_config(HERE), date_str, args.months)
    if args.dry_run: print(md); return 0
    os.makedirs(os.path.join(HERE, "data", "reports"), exist_ok=True)
    path = os.path.join(HERE, "data", "reports", "%s-period.md" % label)
    open(path, "w").write(md); print(path)
    if args.notify:
        import subprocess
        r = subprocess.run([sys.executable, os.path.join(HERE, "scripts", "send_text.py"), path])
        if r.returncode != 0:
            return r.returncode
    return 0

if __name__ == "__main__":
    sys.exit(main())
