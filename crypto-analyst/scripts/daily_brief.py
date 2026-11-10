#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""daily_brief.py —— 生成每日行情简报（本地 Markdown，离线可跑）。

用法：
    python3 scripts/daily_brief.py                 # 写 data/reports/<today>-daily-brief.md
    python3 scripts/daily_brief.py --dry-run       # 只打印，不写文件
    python3 scripts/daily_brief.py --date 2026-11-08
    python3 scripts/daily_brief.py --fixtures tests/fixtures --dry-run   # 离线回放（CI 用）

数据来源（全部只读）：
  - data/raw/klines/*.json                     （fetch_history.py 生成）
  - crypto-price/data/exports/latest.json      （可选）
  - crypto-news/data/exports/daily-<date>.json （可选）
  - data/raw/macro/<date>.json                 （可选，macro.py 生成）
  - records/judgments.jsonl                 （打分到期提醒）
"""
import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core  # noqa: E402

import score as score_mod  # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(HERE)

DISCLAIMER = ("本报告为公开数据的统计分析与方法研究，不构成投资建议；"
              "不提供任何交易、出入金或账户操作指引。")

DEFAULT_CONFIG = {
    "watchlist": ["BTC", "ETH", "BNB", "SOL"],
    "scenarios": {
        "horizon_days": 90,
        "set": {
            "A": {"label": "场景1", "prob": 0.50, "desc": "守住 MA200，修复至 MA50（动态位）", "anchor": "ma50",
                  "band_px": [81000, 87400]},
            "B": {"label": "场景2", "prob": 0.35, "desc": "日收盘跌破 MA200，下探 68,000–70,000", "band_px": [68000, 70000]},
            "C": {"label": "场景3", "prob": 0.15, "desc": "突破 90 日高点，挑战 90,000+", "band_px": [90000, 1000000]},
        },
        "invalidation": "BTC 日收盘跌破 MA200 视为场景1/场景3 失效",
    },
    "macro": {"enabled": True},
    "compliance": {"disclaimer": DISCLAIMER},
}


def load_config(base_dir):
    path = os.path.join(base_dir, "config", "analyst.yaml")
    if not os.path.exists(path):
        return DEFAULT_CONFIG
    try:
        import yaml  # 可选依赖；没有就退回默认配置
        with open(path) as f:
            cfg = yaml.safe_load(f) or {}
        cfg.setdefault("watchlist", DEFAULT_CONFIG["watchlist"])
        cfg.setdefault("scenarios", DEFAULT_CONFIG["scenarios"])
        return cfg
    except Exception as e:  # 缺 PyYAML / YAML 语法错：降级但不静默
        print(f"警告：配置读取失败（{type(e).__name__}），使用内置默认配置", file=sys.stderr)
        return DEFAULT_CONFIG


def read_json(path):
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (ValueError, OSError):
        return None


def load_events(repo_root, date_str):
    data = read_json(os.path.join(repo_root, "crypto-news", "data", "exports", f"daily-{date_str}.json"))
    if not data:
        return []
    items = data.get("items") or []
    return [i for i in items if i.get("severity") in ("P0", "P1")]


def load_price_snapshot(repo_root):
    return read_json(os.path.join(repo_root, "crypto-price", "data", "exports", "latest.json"))


def load_macro(base_dir, date_str):
    data = read_json(os.path.join(base_dir, "data", "raw", "macro", f"{date_str}.json"))
    if data:
        return data
    # 退而求其次：目录里最新的一个
    d = os.path.join(base_dir, "data", "raw", "macro")
    if not os.path.isdir(d):
        return None
    files = sorted(f for f in os.listdir(d) if f.endswith(".json"))
    return read_json(os.path.join(d, files[-1])) if files else None


def one_liner(rows, btc):
    above = [r for r in rows if (r["vs_ma200_pct"] or 0) > 0]
    below50 = [r for r in rows if (r["vs_ma50_pct"] or 0) < 0]
    above_rng = f"{(min(r['vs_ma200_pct'] for r in above)):+.1f}%~{(max(r['vs_ma200_pct'] for r in above)):+.1f}%" if above else "n/a"
    below_rng = f"{(min(r['vs_ma50_pct'] for r in below50)):+.1f}%~{(max(r['vs_ma50_pct'] for r in below50)):+.1f}%" if below50 else "n/a"
    vol = [r["vol_ratio_30_90"] for r in rows if r["vol_ratio_30_90"]]
    vol_rng = f"{min(vol):.2f}–{max(vol):.2f}" if vol else "n/a"
    coins = "/".join(r["sym"] for r in rows)
    parts = [
        f"{coins}：{len(above)}/{len(rows)} 在 MA200（200 日均线·长期趋势）上方（{above_rng}）",
        f"{len(below50)}/{len(rows)} 在 MA50（50 日均线·中短期趋势）下方（{below_rng}）",
        f"量能（30 日/90 日均量比）{vol_rng}",
    ]
    if btc:
        parts.append(f"BTC 距 52 周低 {(btc['vs_52w_low_pct'] or 0):+.1f}%、距 90 日高 {(btc['vs_90d_high_pct'] or 0):+.1f}%")
    return "；".join(parts) + "。"


def scenario_table(cfg, btc_row, btc_bars):
    sc = cfg.get("scenarios") or {}
    horizon = sc.get("horizon_days", 90)
    rows = []
    for sid, s in (sc.get("set") or {}).items():
        anchor = s.get("anchor")
        if anchor == "ma50" and btc_row.get("ma50"):
            level = btc_row["ma50"]
            level_txt = f"{level:,.0f}（MA50）"
            pct = (level / btc_row["last"] - 1) * 100
            pct_txt = core.pct(pct)
        else:
            band = s.get("band_px") or []
            if len(band) == 2 and band[1] and band[1] < 1_000_000:
                level_txt = f"{band[0]:,}–{band[1]:,}"
                pct_txt = f"{core.pct((band[0] / btc_row['last'] - 1) * 100)} ~ {core.pct((band[1] / btc_row['last'] - 1) * 100)}"
            elif band:
                level_txt = f"{band[0]:,}+"
                pct_txt = f"{core.pct((band[0] / btc_row['last'] - 1) * 100)} 以上"
            else:
                level_txt, pct_txt = "n/a", "n/a"
        rows.append((s.get("label") or sid, s.get("prob", 0), level_txt, pct_txt, s.get("desc", "")))
    return horizon, rows


def macro_section(base_dir, date_str):
    m = load_macro(base_dir, date_str)
    if not m:
        return []
    lines = ["## 宏观与波动（可选）", "",
             "| 指标 | 最新 | 日期 |", "|---|---:|---|"]
    for k, v in (m.get("series") or {}).items():
        lines.append(f"| {v.get('label', k)} | {v.get('value')} | {v.get('date', 'n/a')} |")
    lines.append("")
    return lines


def build(cfg, base_dir, date_str):
    watchlist = cfg.get("watchlist") or core.SYMS
    data = core.load_all(base_dir, watchlist)
    if not data:
        raise SystemExit(f"缺少 K 线数据：{core.klines_dir(base_dir)}，先跑 scripts/fetch_history.py")

    rows = [core.state_row(s, b) for s, b in data.items()]
    btc = next((r for r in rows if r["sym"] == "BTC"), None)
    px = {s: {b["date"]: b["close"] for b in v} for s, v in data.items()}
    bs = core.equal_weight_stats(px) if len(px) >= 2 else None
    snap = load_price_snapshot(REPO)
    events = load_events(REPO, date_str)
    horizon, sc_rows = scenario_table(cfg, btc, data.get("BTC"))
    disclaimer = (cfg.get("compliance") or {}).get("disclaimer", DISCLAIMER)

    L = []
    L.append(f"# 每日行情简报 {date_str}")
    L.append("")
    L.append(f"> ⚠️ {disclaimer}")
    L.append("")
    L.append("> 数据来源：Binance 公开日线（2019→今）+ `crypto-price`/`crypto-news` 只读导出。"
             "生成脚本：`crypto-analyst/scripts/daily_brief.py`（可离线复跑）。")
    L.append("")
    L.append("## 一、一句话状态")
    L.append("")
    L.append(one_liner(rows, btc))
    L.append("")
    L.append("## 二、四币状态")
    L.append("")
    L.append("| 币 | 现价 | vs MA50（50日均线） | vs MA200（200日均线） | 1年 | 距ATH（历史最高） | 52w位置 | 距90日高 | 量30/90（量能比） |")
    L.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for r in rows:
        L.append(f"| {r['sym']} | {r['last']:,.2f} | {core.pct(r['vs_ma50_pct'])} | "
                 f"{core.pct(r['vs_ma200_pct'])} | {core.pct(r['ret_1y_pct'])} | "
                 f"{core.pct(r['dd_from_ath_pct'])} | {core.pct(r['pos_52w_pct'], 1, False)} | "
                 f"{core.pct(r['vs_90d_high_pct'])} | {r['vol_ratio_30_90']:.2f} |")
    L.append("")

    if btc:
        L.append("## 三、BTC 结构")
        L.append("")
        L.append(f"- ATH 历史最高价 **{btc['ath']:,.0f}**（{btc['ath_date']}），现价距其 **{core.pct(btc['dd_from_ath_pct'])}**")
        L.append(f"- 52 周区间 **{btc['lo52']:,.0f} – {btc['hi52']:,.0f}**，现价位于 **{core.pct(btc['pos_52w_pct'], 1, False)}** 分位，距低点 **{core.pct(btc['vs_52w_low_pct'])}**")
        L.append(f"- 90 日高点 **{btc['hi90']:,.0f}**，现价距其 **{core.pct(btc['vs_90d_high_pct'])}**")
        L.append(f"- 30 日年化波动 **{btc['rv30_pct']:.1f}%**，90 日 **{btc['rv90_pct']:.1f}%**")
        L.append("")

    L.append("## 四、事件（P0/P1）")
    L.append("")
    if events:
        for e in events[:8]:
            assets = "/".join(e.get("assets") or []) or "—"
            L.append(f"- **{e.get('severity')}**（{assets}）{e.get('title', '')[:120]}")
    else:
        L.append("- 无 P0/P1 事件（或未读到 crypto-news 导出）。")
    L.append("")

    L.append(f"## 五、BTC 场景预测（未来约 {horizon} 天，概率和 100%；ETH/BNB/SOL 随 BTC 波动）")
    L.append("")
    L.append("| 场景 | 概率 | 关键位 | 距现价 | 描述 |")
    L.append("|---|---:|---:|---:|---|")
    for sid, prob, level_txt, pct_txt, desc in sc_rows:
        L.append(f"| {sid} | {prob:.0%} | {level_txt} | {pct_txt} | {desc} |")
    L.append("")
    inv = (cfg.get("scenarios") or {}).get("invalidation")
    if inv:
        L.append(f"**失效条件**：{inv}")
        L.append("")

    review = (cfg.get("review") or {})
    L.append("## 六、待打分（复盘记录）")
    L.append("")
    entries = score_mod.load_log(os.path.join(HERE, "records", "judgments.jsonl"))
    rows = []
    for e in entries:
        if e.get("scored"):
            continue
        kind = "情景打分" if e.get("score_at") else ("方向复核" if e.get("review_by") else None)
        due = e.get("score_at") or e.get("review_by")
        if not kind or not due:
            continue
        rows.append((due, e.get("id"), kind, "已到期（跑 score.py）" if due <= date_str else "待到期"))
    if rows:
        for due, eid, kind, status in sorted(rows)[:8]:
            L.append(f"- `{eid}` **{due}**（{kind}，{status}）")
    else:
        L.append("- 无待打分条目。")
    if review:
        L.append("")
        L.append(f"> 打分口径：方向 {review.get('direction_days', 7)} 天、情景 Brier {review.get('scenario_days', 30)} 天；"
                 f"`python3 crypto-analyst/scripts/score.py --dry-run` 可预览。")
    L.append("")

    L.extend(macro_section(base_dir, date_str))

    if bs:
        L.append("## 七、等权组合参考（仅统计，不做组合建议）")
        L.append("")
        L.append(f"- 区间 {bs['start']} → {bs['end']}（{bs['years']:.1f} 年）：CAGR {bs['cagr_pct']:.1f}%，"
                 f"年化波动 {bs['vol_ann_pct']:.1f}%，历史最大回撤 {bs['maxdd_pct']:.1f}%")
        L.append(f"- 距历史高点 {core.pct(bs['dd_from_ath_pct'])}；近 30/90/365 日 {core.pct(bs['ret_30d_pct'])} / "
                 f"{core.pct(bs['ret_90d_pct'])} / {core.pct(bs['ret_1y_pct'])}")
        L.append("")

    L.append("## 数据与口径")
    L.append("")
    L.append(f"- 生成时间：{datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}")
    if snap:
        sh = snap.get("source_health") or {}
        L.append(f"- crypto-price 快照：{snap.get('generated_at', 'n/a')}，源健康 {sh.get('ok', '?')}/{sh.get('total', '?')}")
    L.append("- K 线缓存：" + ", ".join(f"{s}({len(b)} 根)" for s, b in data.items()))
    L.append("")
    L.append(f"> ⚠️ {disclaimer}")
    L.append("")
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description="生成每日行情简报")
    ap.add_argument("--date", default=None, help="日期 YYYY-MM-DD（默认今天）")
    ap.add_argument("--fixtures", default=None, help="离线回放根目录（覆盖仓库根）")
    ap.add_argument("--dry-run", action="store_true", help="只打印不写文件")
    ap.add_argument("--out-dir", default=None)
    args = ap.parse_args(argv)

    base_dir = os.path.abspath(args.fixtures) if args.fixtures else HERE
    date_str = args.date or datetime.datetime.now().strftime("%Y-%m-%d")
    cfg = load_config(base_dir if not args.fixtures else HERE)
    text = build(cfg, base_dir, date_str)

    if args.dry_run:
        print(text)
        return 0
    out_dir = args.out_dir or os.path.join(base_dir, "data", "reports")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{date_str}-daily-brief.md")
    with open(path, "w") as f:
        f.write(text)
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
