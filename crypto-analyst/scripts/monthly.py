#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""monthly.py —— 月报（价格月统计 + 事件归档 + 场景 + 复盘）。"""
import argparse
import datetime
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "scripts")); sys.path.insert(0, os.path.join(REPO, "libs", "crypto_core", "src"))
import core
import daily_brief


def _weekday(date_str):
    import datetime
    return "周" + "一二三四五六日"[datetime.date.fromisoformat(date_str).weekday()]


def prev_month(date_str):
    d = datetime.date.fromisoformat(date_str)
    first = d.replace(day=1)
    end = first - datetime.timedelta(days=1)
    return end.strftime("%Y-%m"), end.strftime("%Y-%m-%d")

def month_bars(bars, month):
    return [b for b in bars if b["date"][:7] == month]

def build(cfg, date_str, base_dir=HERE, repo_root=REPO):
    month, end = prev_month(date_str)
    data = core.load_all(base_dir, cfg.get("watchlist") or core.SYMS)
    rows = []
    for sym, bars in data.items():
        w = month_bars(bars, month)
        if not w: continue
        o, c = w[0]["open"], w[-1]["close"]
        rows.append((sym, {"open": o, "close": c, "high": max(b["high"] for b in w),
                           "low": min(b["low"] for b in w), "chg": (c / o - 1) * 100 if o else None}))
    hist = daily_brief.read_json(os.path.join(HERE, "data", "history", "%s.json" % month)) or []
    btc = core.state_row("BTC", data["BTC"]) if "BTC" in data else None
    horizon, sc_rows = daily_brief.scenario_table(cfg, btc, data.get("BTC"))
    L = ["# crypto-analyst 月报 %s" % month, "",
         "> 数据：Binance 日线 + 事件归档（data/history/%s.json）｜研究用途，不构成投资建议" % month, "",
         "## 一、月度行情", "", "| 币 | 月开 | 月收 | 月涨跌 | 月高 | 月低 |", "|---|---:|---:|---:|---:|---:|"]
    for sym, w in rows:
        L.append("| %s | %s | %s | %s%% | %s | %s |" % (sym, f"{w['open']:,.2f}", f"{w['close']:,.2f}",
                 f"{w['chg']:+.1f}" if w['chg'] is not None else "n/a", f"{w['high']:,.2f}", f"{w['low']:,.2f}"))
    L += ["", "## 二、本月事件（归档 %d 条）" % len(hist), ""]
    if hist:
        for r in hist[:10]:
            L.append("- [%s] %s %s" % (r.get("source"), r.get("severity") or "", (r.get("title") or "")[:100]))
    else:
        L.append("- 无归档事件")
    L += ["", "## 三、当前场景（BTC，约 %d 天）" % horizon, "", "| 场景 | 概率 | 关键位 |", "|---|---:|---|"]
    for name, prob, level_txt, _p, _d in sc_rows:
        L.append("| %s | %d%% | %s |" % (name, round(prob * 100), level_txt))
    L += ["", "> ⚠️ 研究/方法验证，不构成投资建议。", ""]
    md = "\n".join(L)
    tg = ("📊【crypto-analyst】月报 %s（%s）\n" % (month, _weekday(date_str)) +
          "｜".join("%s %s（月 %s）" % (s, f"{w['close']:,.0f}", f"{w['chg']:+.1f}%" if w['chg'] is not None else "n/a") for s, w in rows) +
          "\n事件归档：%d 条\n" % len(hist) +
          "BTC 场景：" + "｜".join("%s %d%%→%s" % (n, round(p * 100), l) for n, p, l, _a, _b in sc_rows) +
          "\n⚠️ 研究/方法验证，不构成投资建议\n🏷 任务：crypto-analyst.monthly（手动运行）")
    return month, md, tg[:4096]

def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None); ap.add_argument("--dry-run", action="store_true"); ap.add_argument("--notify", action="store_true")
    args = ap.parse_args(argv)
    date_str = args.date or datetime.datetime.now().strftime("%Y-%m-%d")
    month, md, tg = build(daily_brief.load_config(HERE), date_str)
    if args.dry_run:
        print(md); print("\n--- Telegram ---\n" + tg); return 0
    os.makedirs(os.path.join(HERE, "data", "reports"), exist_ok=True)
    path = os.path.join(HERE, "data", "reports", "%s-monthly.md" % month)
    open(path, "w").write(md); print(path)
    if args.notify:
        from crypto_core import env as envlib
        from crypto_core.notify import check_credentials, send_message
        token, chat, problems = check_credentials(envlib.load_env())
        if problems: print("凭据不完整", file=sys.stderr); return 2
        ok, err = send_message(token, chat, tg)
        if not ok: print("发送失败：%s" % err, file=sys.stderr); return 1
        print("已发送（%d 字符）" % len(tg))
    return 0

if __name__ == "__main__":
    sys.exit(main())
