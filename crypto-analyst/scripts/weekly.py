#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""weekly.py —— 行情分析周报（crypto-analyst 唯一周度出口，吸收原 crypto-report 的数据汇总）。

用法：
    python3 scripts/weekly.py                    # 生成 data/reports/<YYYY>-Www-weekly.md
    python3 scripts/weekly.py --dry-run          # 只打印
    python3 scripts/weekly.py --notify           # 生成并推送 Telegram（精炼版）
    python3 scripts/weekly.py --fixtures tests/fixtures --dry-run   # 离线回放（CI）

数据来源（只读）：
    data/raw/klines/*.json                      Binance 公开日线（周开/收/高/低 + 均线状态）
    crypto-news/data/exports/daily-*.json       本周 P0/P1 事件
    crypto-price/data/exports/latest.json       三源健康（可选）
    data/raw/macro/<date>.json                  DVOL / DXY（可选）
    records/judgments.jsonl                  打分状态
"""
import argparse
import datetime
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "scripts"))
sys.path.insert(0, os.path.join(REPO, "libs", "crypto_core", "src"))

import core  # noqa: E402
import daily_brief  # noqa: E402

import score as score_mod  # noqa: E402

MAX_LEN = 4096


def _weekday(date_str):
    import datetime
    return "周" + "一二三四五六日"[datetime.date.fromisoformat(date_str).weekday()]


def _esc(value):
    import html
    return html.escape("" if value is None else str(value), quote=False)


def _pct(x, digits=1):
    return "n/a" if x is None else f"{x:+.{digits}f}%"


def week_label(date_str):
    d = datetime.date.fromisoformat(date_str)
    iso = d.isocalendar()
    return "%d-W%02d" % (iso[0], iso[1])


def week_dates(date_str, days=7):
    d = datetime.date.fromisoformat(date_str)
    return [(d - datetime.timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]


def weekly_bar(bars, days=7):
    """最近 N 天日线聚合：周开/周收/周高/周低/周涨跌/周均量。"""
    w = bars[-days:]
    if not w:
        return None
    o = w[0]["open"]
    c = w[-1]["close"]
    return {
        "open": o, "close": c,
        "high": max(b["high"] for b in w),
        "low": min(b["low"] for b in w),
        "change_pct": (c / o - 1) * 100 if o else None,
        "vol_avg": sum(b["volume"] for b in w) / len(w),
        "days": len(w),
    }


def load_week_events(repo_root, dates):
    """本周 crypto-news 导出里的 P0/P1，按标题去重（保留最高级别）。"""
    seen = {}
    for d in dates:
        data = daily_brief.read_json(os.path.join(
            repo_root, "crypto-news", "data", "exports", "daily-%s.json" % d))
        if not data:
            continue
        for item in (data.get("items") or []):
            if item.get("severity") not in ("P0", "P1"):
                continue
            key = item.get("title") or ""
            rank = {"P0": 0, "P1": 1}
            old = seen.get(key)
            if old is None or rank.get(item["severity"], 9) < rank.get(old.get("severity"), 9):
                seen[key] = item
    return sorted(seen.values(), key=lambda x: ({"P0": 0, "P1": 1}.get(x.get("severity"), 9),))


def load_policy(repo_root, dates):
    """本周政策/监管条目（crypto-policy exports）。"""
    items = []
    for d in dates:
        data = daily_brief.read_json(os.path.join(
            repo_root, "crypto-policy", "data", "exports", "policy-%s.json" % d))
        if not data:
            continue
        for it in (data.get("items") or []):
            items.append(it)
    return items


def load_kol(repo_root, dates):
    """本周 KOL 条目（crypto-kol exports），按 handle+text 去重。"""
    seen = {}
    for d in dates:
        data = daily_brief.read_json(os.path.join(
            repo_root, "crypto-kol", "data", "exports", "daily-%s.json" % d))
        if not data:
            continue
        for it in (data.get("items") or []):
            key = "%s|%s" % (it.get("handle"), (it.get("text") or "")[:60])
            seen[key] = it
    return list(seen.values())


def load_week_signals(repo_root, dates):
    """本周 crypto-price 导出里的 P0/P1 价格异动，去重后返回。"""
    seen = {}
    for d in dates:
        data = daily_brief.read_json(os.path.join(
            repo_root, "crypto-price", "data", "exports", "daily-%s.json" % d))
        if not data:
            continue
        for sig in (data.get("signals") or []):
            if sig.get("severity") not in ("P0", "P1"):
                continue
            if sig.get("rule") == "data_quality":  # 运行/数据质量异常不进周报（属运维噪音）
                continue
            key = sig.get("dedupe_key") or sig.get("title") or ""
            old = seen.get(key)
            rank = {"P0": 0, "P1": 1}
            if old is None or rank.get(sig["severity"], 9) < rank.get(old.get("severity"), 9):
                seen[key] = sig
    return sorted(seen.values(), key=lambda x: ({"P0": 0, "P1": 1}.get(x.get("severity"), 9),))


def news_counts(repo_root, dates):
    """本周 crypto-news 的 P0/P1/P2 计数（按天累加）。"""
    out = {"P0": 0, "P1": 0, "P2": 0}
    for d in dates:
        data = daily_brief.read_json(os.path.join(
            repo_root, "crypto-news", "data", "exports", "daily-%s.json" % d))
        if not data:
            continue
        for sev, n in (data.get("counts") or {}).items():
            if sev in out and isinstance(n, int):
                out[sev] += n
    return out


def export_coverage(repo_root, dates):
    """本周 price/news 导出覆盖天数（数据源健康度的替代指标）。"""
    def days(module):
        n = 0
        for d in dates:
            if os.path.exists(os.path.join(repo_root, module, "data", "exports", "daily-%s.json" % d)):
                n += 1
        return n
    return {"price": days("crypto-price"), "news": days("crypto-news")}


def build(cfg, date_str, base_dir=HERE, repo_root=REPO):
    """返回 (label, markdown, telegram_text)。"""
    watchlist = cfg.get("watchlist") or core.SYMS
    data = core.load_all(base_dir, watchlist)
    if not data:
        raise SystemExit(f"缺少 K 线数据：{core.klines_dir(base_dir)}，先跑 scripts/fetch_history.py")
    dates = week_dates(date_str)
    label = week_label(date_str)
    rows = [(sym, weekly_bar(bars)) for sym, bars in data.items()]
    rows = [(s, r) for s, r in rows if r]
    states = {sym: core.state_row(sym, bars) for sym, bars in data.items()}
    btc = states.get("BTC")
    events = load_week_events(repo_root, dates)
    signals = load_week_signals(repo_root, dates)
    ncounts = news_counts(repo_root, dates)
    policies = load_policy(repo_root, dates)
    kols = load_kol(repo_root, dates)
    cov = export_coverage(repo_root, dates)
    snap = daily_brief.load_price_snapshot(repo_root)
    horizon, sc_rows = daily_brief.scenario_table(cfg, btc, data.get("BTC"))
    macro = daily_brief.load_macro(base_dir, date_str)
    entries = score_mod.load_log(os.path.join(HERE, "records", "judgments.jsonl"))

    # ---------- Markdown ----------
    L = ["# crypto-analyst 周报 %s" % _esc(label), ""]
    L.append("> 窗口：最近 7 天（%s ~ %s）｜只读 Binance 日线 + price/news exports｜研究用途，不构成投资建议"
             % (_esc(dates[0]), _esc(dates[-1])))
    L.append("")
    L.append("## 一、行情（周）")
    L.append("")
    L.append("| 币 | 周开 | 周收 | 周涨跌 | 周高 | 周低 | 周均量 |")
    L.append("|---|---:|---:|---:|---:|---:|---:|")
    for sym, w in rows:
        L.append("| %s | %s | %s | %s | %s | %s | %s |" % (
            sym, _fmt(w["open"]), _fmt(w["close"]), _pct(w["change_pct"]),
            _fmt(w["high"]), _fmt(w["low"]), _fmt(w["vol_avg"], 0)))
    L.append("")
    L.append("## 二、位置（日线状态）")
    L.append("")
    L.append("| 币 | vs MA200（长期） | vs MA50（短期） | 1年 | 距ATH |")
    L.append("|---|---:|---:|---:|---:|")
    for sym, r in states.items():
        L.append("| %s | %s | %s | %s | %s |" % (
            sym, _pct(r["vs_ma200_pct"]), _pct(r["vs_ma50_pct"]),
            _pct(r["ret_1y_pct"]), _pct(r["dd_from_ath_pct"])))
    L.append("")
    if btc:
        L.append("## 三、BTC 结构")
        L.append("")
        L.append("- 历史最高（ATH）**%s**（%s），现价距其 **%s**" % (
            _fmt(btc["ath"], 0), btc["ath_date"], _pct(btc["dd_from_ath_pct"])))
        L.append("- 52 周区间 **%s – %s**，现价位于 **%.1f%%** 分位" % (
            _fmt(btc["lo52"], 0), _fmt(btc["hi52"], 0), btc["pos_52w_pct"] or 0))
        L.append("")
    L.append("## 四、本周事件（P0/P1）")
    L.append("")
    L.append("**价格异动（crypto-price）**")
    L.append("")
    if signals:
        for sig in signals[:10]:
            L.append("- **%s** %s %s（%s）" % (
                sig.get("severity"), sig.get("asset") or "", sig.get("title") or "", sig.get("rule") or ""))
    else:
        L.append("- 无 P0/P1 价格异动")
    L.append("")
    L.append("**消息面（crypto-news）**：P0 %d 条 · P1 %d 条 · P2 %d 条" % (
        ncounts["P0"], ncounts["P1"], ncounts["P2"]))
    L.append("")
    if events:
        for e in events[:10]:
            L.append("- **%s**（%s）%s" % (e.get("severity"), "/".join(e.get("assets") or []) or "—",
                                          e.get("title") or ""))
    else:
        L.append("- 无 P0/P1 事件（或 crypto-news 导出缺失）")
    L.append("")
    L.append("**政策面（crypto-policy）**")
    L.append("")
    if policies:
        for it in policies[:5]:
            L.append("- %s（%s）" % (it.get("title") or it.get("doc_type_cn") or "政策文件", it.get("level") or "—"))
    else:
        L.append("- 本周无政策条目")
    L.append("")
    L.append("**KOL 动态（crypto-kol）**：本周 %d 条" % len(kols))
    for it in kols[:3]:
        L.append("- %s（%s）：%s" % (it.get("name") or it.get("handle"), it.get("tier") or "—",
                                    (it.get("text") or "")[:60]))
    L.append("")
    L.append("## 五、BTC 场景（未来约 %d 天；ETH/BNB/SOL 随 BTC 波动）" % horizon)
    L.append("")
    L.append("| 场景 | 概率 | 关键位 | 距现价 | 描述 |")
    L.append("|---|---:|---:|---:|---|")
    for name, prob, level_txt, pct_txt, desc in sc_rows:
        L.append("| %s | %d%% | %s | %s | %s |" % (name, round(prob * 100), level_txt, pct_txt, desc))
    inv = (cfg.get("scenarios") or {}).get("invalidation")
    if inv:
        L.append("")
        L.append("**失效条件**：%s" % inv)
    L.append("")
    L.append("## 六、数据与复盘")
    L.append("")
    L.append("- 导出覆盖：price %d/7 天，news %d/7 天" % (cov["price"], cov["news"]))
    if snap:
        sh = snap.get("source_health") or {}
        L.append("- 现货快照：%s，源健康 %s/%s" % (snap.get("generated_at", "n/a"), sh.get("ok", "?"), sh.get("total", "?")))
    if macro and (macro.get("series") or {}):
        parts = []
        for key in ("dvol", "dxy", "us10y"):
            v = (macro.get("series") or {}).get(key)
            if v:
                parts.append("%s %s" % (v.get("label", key), v.get("value")))
        L.append("- 宏观：%s" % "｜".join(parts))
    due = [e for e in entries if (e.get("score_at") or e.get("review_by")) and not e.get("scored")]
    if due:
        L.append("- 待打分条目：%s" % "、".join("`%s`(%s)" % (e.get("id"), e.get("score_at") or e.get("review_by")) for e in due[:5]))
    L.append("")
    L.append("> ⚠️ 研究/方法验证，不构成投资建议，不提供交易指引。")
    L.append("")

    # ---------- Telegram（精炼） ----------
    T = ["📊【crypto-analyst】周报 %s（%s~%s，%s）" % (_esc(label), dates[0][5:], dates[-1][5:], _esc(_weekday(date_str))), ""]
    T.append("行情：" + "｜".join("%s %s（周 %s）" % (_esc(s), _fmt(w["close"]), _pct(w["change_pct"]))
                                  for s, w in rows))
    if btc:
        above = sum(1 for r in states.values() if (r["vs_ma200_pct"] or 0) > 0)
        below = sum(1 for r in states.values() if (r["vs_ma50_pct"] or 0) < 0)
        T.append("位置：%d/%d 在长期均线上方、%d/%d 在短期均线下方；BTC 距历史最高 %s" % (
            above, len(states), below, len(states), _pct(btc["dd_from_ath_pct"])))
    T.append("BTC 场景（约 %d 天）：" % horizon + "｜".join(
        "%s %d%%→%s" % (name, round(prob * 100), level_txt) for name, prob, level_txt, _p, _d in sc_rows))
    if inv:
        T.append("失效：%s" % inv)
    if signals:
        T.append("本周价格异动：%d 条（P0 %d）" % (
            len(signals), sum(1 for x in signals if x.get("severity") == "P0")))
    if policies:
        T.append("本周政策：%d 条" % len(policies))
    if kols:
        T.append("KOL 动态：%d 条" % len(kols))
    def _short(title, n=42):
        t = (title or "").strip()[:n]
        for sep in ("，", "；", "。"):
            if sep in t:
                t = t.split(sep)[0]
        return t

    if events:
        T.append("本周事件：" + "；".join(
            "%s %s" % (_esc(e.get("severity")), _esc(_short(e.get("title")))) for e in events[:2]))
    T.append("")
    T.append("⚠️ 研究/方法验证，不构成投资建议")
    T.append("🏷 任务：%s" % _esc(_job_label()))
    telegram = "\n".join(T)
    return label, "\n".join(L), telegram[:MAX_LEN]


def _fmt(x, digits=2):
    return "n/a" if x is None else f"{x:,.{digits}f}"


def _job_label(module="crypto-analyst.weekly"):
    label = os.environ.get("CRYPTO_JOB_LABEL") or ""
    if label:
        return label
    job = os.environ.get("XPC_SERVICE_NAME") or ""
    if job and job != "0":
        return job
    return "%s（手动运行）" % module


def main(argv=None):
    ap = argparse.ArgumentParser(description="行情分析周报")
    ap.add_argument("--date", default=None, help="YYYY-MM-DD（默认今天）")
    ap.add_argument("--fixtures", default=None, help="离线回放根目录")
    ap.add_argument("--data-dir", default=None, help="输出目录（默认 <module>/data/reports）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--notify", action="store_true", help="推送 Telegram")
    args = ap.parse_args(argv)

    base_dir = os.path.abspath(args.fixtures) if args.fixtures else HERE
    date_str = args.date or datetime.datetime.now().strftime("%Y-%m-%d")
    cfg = daily_brief.load_config(HERE)
    try:
        label, md, tg = build(cfg, date_str, base_dir, REPO if not args.fixtures else base_dir)
    except SystemExit as e:
        print(e)
        return 1

    if args.dry_run:
        print(md)
        print("\n--- Telegram ---\n" + tg)
        return 0

    out_dir = args.data_dir or os.path.join(base_dir, "data", "reports")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "%s-weekly.md" % label)
    with open(path, "w") as f:
        f.write(md)
    print(path)

    if not args.notify:
        return 0
    from crypto_core import env as envlib
    from crypto_core.notify import check_credentials, send_message
    token, chat, problems = check_credentials(envlib.load_env())
    if problems:
        print("❌ Telegram 凭据不完整：%s" % "；".join(problems), file=sys.stderr)
        return 2
    ok, err = send_message(token, chat, tg)
    if not ok:
        print("❌ 周报发送失败：%s" % err, file=sys.stderr)
        return 1
    print("✅ 周报已发送到 Telegram（%d 字符）" % len(tg))
    return 0


if __name__ == "__main__":
    sys.exit(main())
