#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""send_brief.py —— 把当日行情简报压缩成一条 Telegram 消息（HTML，全部数字来自真实数据）。

用法：
    python3 scripts/send_brief.py --dry-run        # 只打印，不发送（默认行为）
    python3 scripts/send_brief.py --notify         # 真正发送到 Telegram
    python3 scripts/send_brief.py --notify --date 2026-11-08

数据来源（全部只读，消息里每个数字都可回溯）：
    data/raw/klines/*.json                     Binance 公开日线（2019→今，本地缓存）
    crypto-price/data/exports/latest.json      三源现货快照（现价/24h/源健康）
    crypto-news/data/exports/daily-<date>.json P0/P1 事件
    data/raw/macro/<date>.json                 DVOL / DXY / US10Y（可选）

合规：研究/方法验证定位，不构成投资建议，不提供交易指引。
"""
import argparse
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "scripts"))
sys.path.insert(0, os.path.join(REPO, "libs", "crypto_core", "src"))

import core  # noqa: E402
import daily_brief  # noqa: E402

MAX_LEN = 4096


def _weekday(date_str):
    import datetime
    return "周" + "一二三四五六日"[datetime.date.fromisoformat(date_str).weekday()]


def _esc(value):
    import html
    return html.escape("" if value is None else str(value), quote=False)


def _job_label(module="crypto-analyst"):
    """调度任务名：优先 CRYPTO_JOB_LABEL（launchd plist 注入；launchd 会把 XPC 覆盖成 0），
    其次 XPC_SERVICE_NAME，最后回退「<module>（手动运行）」。"""
    label = os.environ.get("CRYPTO_JOB_LABEL") or ""
    if label:
        return label
    job = os.environ.get("XPC_SERVICE_NAME") or ""
    if job and job != "0":
        return job
    return "%s（手动运行）" % module


def _fmt(x, digits=0):
    return "n/a" if x is None else f"{x:,.{digits}f}"


def _pct(x, digits=1):
    return "n/a" if x is None else f"{x:+.{digits}f}%"


def impact_of(cfg, category):
    """事件类别 → 潜在影响标签（配置优先，缺省回退）。"""
    mapping = ((cfg.get("events") or {}).get("impact") or {})
    return mapping.get(category) or (cfg.get("events") or {}).get("default") or "情绪"


def confirmation_of(event):
    """P0（按 crypto-news 规则=已确认）标"已确认"，其余标"单源待确认"。"""
    return "已确认" if event.get("severity") == "P0" else "单源待确认"


def event_line(cfg, event, max_len=48):
    """消息里的事件行：事件（潜在影响：X｜确认状态）：标题。"""
    title = (event.get("title") or "").strip()[:max_len]
    for sep in ("，", "；", "。"):
        if sep in title:
            title = title.split(sep)[0]
    return "事件（潜在影响：%s｜%s）：%s" % (
        impact_of(cfg, event.get("category")), confirmation_of(event), title)


def _build_short(cfg, date_str, base_dir=HERE, repo_root=REPO):
    """精炼版：默认发送。只保留 一句话 / 四币现价 / 关键位 / 三个场景 / 失效 / 1 条事件。"""
    watchlist = cfg.get("watchlist") or core.SYMS
    data = core.load_all(base_dir, watchlist)
    if not data:
        raise SystemExit(f"缺少 K 线数据：{core.klines_dir(base_dir)}，先跑 scripts/fetch_history.py")
    rows = [core.state_row(s, b) for s, b in data.items()]
    btc = next((r for r in rows if r["sym"] == "BTC"), None)
    events = daily_brief.load_events(repo_root, date_str)

    above = [r for r in rows if (r["vs_ma200_pct"] or 0) > 0]
    below50 = [r for r in rows if (r["vs_ma50_pct"] or 0) < 0]
    vol = [r["vol_ratio_30_90"] for r in rows if r["vol_ratio_30_90"]]
    if len(above) == len(rows) and len(below50) == len(rows):
        one = "长期趋势未破、短期缩量回调，不是崩盘"
    elif len(above) == len(rows):
        one = "长期趋势未破，短期回调"
    elif not above:
        one = "长期趋势已破，防守"
    else:
        one = "趋势分化，等确认"

    def _px(v):
        return "%.2f" % v if v < 10 else ("%.1f" % v if v < 1000 else "{:,.0f}".format(v))

    L = ["📊【crypto-analyst】日报 %s（%s）" % (_esc(date_str), _esc(_weekday(date_str))), ""]
    L.append("结论：%s" % _esc(one))
    if btc:
        L.append("依据（200 日均线=长期趋势，50 日均线=短期趋势）：")
        def _side(x):
            return "上方" if x >= 0 else "下方"

        L.append("· 现价 %s，在长期趋势线 %s %s" % (
            _fmt(btc["last"], 2), _fmt(btc["ma200"]),
            ("%s（%s）" % (_side(btc["vs_ma200_pct"]), _pct(btc["vs_ma200_pct"])))))
        L.append("· 在短期趋势线 %s %s" % (
            _fmt(btc["ma50"]),
            ("%s（%s）" % (_side(btc["vs_ma50_pct"]), _pct(btc["vs_ma50_pct"])))))
        if vol:
            vol_txt = "缩量" if max(vol) < 0.95 else "正常"
            L.append("· 成交量是近 3 个月均值的 %d%%~%d%%（%s）" % (
                min(vol) * 100, max(vol) * 100, vol_txt))
    L.append("行情：%s" % "｜".join(
        "%s %s" % (_esc(r["sym"]), _px(r["last"])) for r in rows))
    if btc:
        L.append("距历史最高 %s" % _pct(btc["dd_from_ath_pct"]))
    L.append("")
    horizon, sc_rows = daily_brief.scenario_table(cfg, btc, data.get("BTC"))
    L.append("BTC 场景（未来约 %d 天；ETH/BNB/SOL 随 BTC 波动，不单独设场景）" % horizon)
    for label, prob, level_txt, pct_txt, desc in sc_rows:
        level_txt = level_txt.replace("（MA50）", "").replace("（MA200）", "")
        L.append("· %s：%d%% → %s（%s）" % (_esc(label), round(prob * 100), _esc(level_txt), _esc(pct_txt)))
    if btc:
        L.append("失效：收盘跌破长期趋势线 %s → 按场景2处理" % _fmt(btc["ma200"]))
    if events:
        L.append("")
        L.append(_esc(event_line(cfg, events[0])))
    L.append("")
    L.append("⚠️ 研究/方法验证，不构成投资建议")
    L.append("🏷 任务：%s" % _job_label())
    return "\n".join(L), {"rows": rows, "events": events, "style": "short"}


def build_message(cfg, date_str, base_dir=HERE, repo_root=REPO, style="short"):
    """style=short（默认，精炼版）或 full（详细版）。"""
    if style == "full":
        return _build_full(cfg, date_str, base_dir, repo_root)
    return _build_short(cfg, date_str, base_dir, repo_root)


def _build_full(cfg, date_str, base_dir=HERE, repo_root=REPO):
    """返回 (text, meta)；text 为 Telegram HTML。"""
    watchlist = cfg.get("watchlist") or core.SYMS
    data = core.load_all(base_dir, watchlist)
    if not data:
        raise SystemExit(f"缺少 K 线数据：{core.klines_dir(base_dir)}，先跑 scripts/fetch_history.py")

    rows = [core.state_row(s, b) for s, b in data.items()]
    btc = next((r for r in rows if r["sym"] == "BTC"), None)
    snap = daily_brief.load_price_snapshot(repo_root)
    events = daily_brief.load_events(repo_root, date_str)

    # 一句话状态
    above = [r for r in rows if (r["vs_ma200_pct"] or 0) > 0]
    below = [r for r in rows if (r["vs_ma50_pct"] or 0) < 0]
    vol = [r["vol_ratio_30_90"] for r in rows if r["vol_ratio_30_90"]]
    coins = "/".join(r["sym"] for r in rows)
    coin_label = coins
    state = "%s：%d/%d 在 MA200（200日均线·长期趋势）上方（%s~%s）；%d/%d 在 MA50（50日均线·中短期趋势）下方（%s~%s）" % (
        coin_label, len(above), len(rows),
        _pct(min((r["vs_ma200_pct"] for r in above), default=None)),
        _pct(max((r["vs_ma200_pct"] for r in above), default=None)),
        len(below), len(rows),
        _pct(min((r["vs_ma50_pct"] for r in below), default=None)),
        _pct(max((r["vs_ma50_pct"] for r in below), default=None)),
    )
    if vol:
        state += "；量能（30日/90日均量比）%s" % ("%.2f–%.2f" % (min(vol), max(vol)))

    L = ["📊【crypto-analyst】每日简报 %s" % _esc(date_str), ""]
    L.append("状态：%s" % _esc(state))
    if btc:
        L.append("BTC 结构：距 ATH（历史最高价）%s｜距 52 周低 %s｜距 90 日高 %s" % (
            _pct(btc["dd_from_ath_pct"]), _pct(btc["vs_52w_low_pct"]), _pct(btc["vs_90d_high_pct"])))
    L.append("")

    L.append("行情（Binance 日线，收盘 %s）" % _esc(date_str))
    for r in rows:
        line = "· %s %s｜MA200（200日均线）%s｜MA50（50日均线）%s｜1年 %s" % (
            _esc(r["sym"]), _fmt(r["last"], 2), _pct(r["vs_ma200_pct"]),
            _pct(r["vs_ma50_pct"]), _pct(r["ret_1y_pct"]))
        chg = None
        if snap and (snap.get("assets") or {}).get(r["sym"]):
            chg = snap["assets"][r["sym"]].get("change_24h_pct")
        if chg is not None:
            line += "｜24h %s" % _pct(float(chg))
        L.append(line)
    if snap:
        sh = snap.get("source_health") or {}
        L.append("· 现货快照：%s，源健康 %s/%s" % (
            _esc(snap.get("generated_at", "n/a")), sh.get("ok", "?"), sh.get("total", "?")))
    L.append("")

    horizon, sc_rows = daily_brief.scenario_table(cfg, btc, data.get("BTC"))
    L.append("场景预测（未来约 %d 天，概率和 100%%）" % horizon)
    for label, prob, level_txt, pct_txt, desc in sc_rows:
        L.append("· %s：%d%% 概率 → %s（%s）" % (_esc(label), round(prob * 100), _esc(level_txt), _esc(pct_txt)))
    inv = (cfg.get("scenarios") or {}).get("invalidation")
    if inv:
        L.append("失效条件：%s" % _esc(inv))
    L.append("")

    L.append("事件（P0/P1，来源 crypto-news %s）" % _esc(date_str))
    if events:
        for e in events[:5]:
            L.append("· %s %s" % (_esc(e.get("severity")), _esc((e.get("title") or "")[:90])))
    else:
        L.append("· 无 P0/P1 事件（或导出缺失）")
    L.append("")

    macro = daily_brief.load_macro(HERE, date_str)
    if macro and (macro.get("series") or {}):
        parts = []
        names = {"dvol": "DVOL（BTC期权隐含波动率）", "dxy": "DXY（美元指数）",
                 "us10y": "US10Y（美债10年期收益率）"}
        for key in ("dvol", "dxy", "us10y"):
            v = (macro.get("series") or {}).get(key)
            if v:
                parts.append("%s %s" % (_esc(names.get(key, key)), v.get("value")))
        if parts:
            L.append("宏观：%s（%s）" % ("｜".join(parts), _esc(macro.get("date", ""))))
    L.append("")
    L.append("—— 数据：Binance 公开日线 2019→今 + crypto-price 快照 + crypto-news 导出")
    L.append("⚠️ 研究/方法验证，不构成投资建议，不提供交易指引")
    L.append("🏷 任务：%s" % _esc(_job_label()))

    text = "\n".join(L)
    return text, {"rows": rows, "events": events, "snapshot": bool(snap)}


def main(argv=None):
    ap = argparse.ArgumentParser(description="发送当日行情简报（Telegram）")
    ap.add_argument("--date", default=None, help="YYYY-MM-DD（默认今天）")
    ap.add_argument("--notify", action="store_true", help="真正发送（默认只打印）")
    ap.add_argument("--dry-run", action="store_true", help="只打印（默认行为，显式写法）")
    ap.add_argument("--full", action="store_true", help="发送详细版（默认精炼版）")
    ap.add_argument("--short", action="store_true", help="精炼版（默认）")
    ap.add_argument("--json", action="store_true", help="打印元信息 JSON")
    args = ap.parse_args(argv)

    date_str = args.date or datetime.datetime.now().strftime("%Y-%m-%d")
    cfg = daily_brief.load_config(HERE)
    text, meta = build_message(cfg, date_str, style="full" if args.full else "short")

    if len(text) > MAX_LEN:
        text = text[: MAX_LEN - 20] + "\n…（已截断）"
    print(text)
    if args.json:
        print(json.dumps(meta, ensure_ascii=False, default=str))
    if not args.notify:
        print("\n（未加 --notify，仅打印；加 --notify 才会发送）")
        return 0

    from crypto_core import env as envlib
    from crypto_core.notify import check_credentials, send_message
    token, chat, problems = check_credentials(envlib.load_env())
    if problems:
        print("❌ Telegram 凭据不完整：%s" % "；".join(problems), file=sys.stderr)
        return 2
    ok, err = send_message(token, chat, text)
    if not ok and "parse entities" in (err or ""):
        import html
        plain = html.unescape(text.replace("<b>", "").replace("</b>", ""))
        ok, err = send_message(token, chat, plain)
        if ok:
            print("⚠️ HTML 解析失败，已改用纯文本发送成功", file=sys.stderr)
    if not ok:
        print("❌ 发送失败：%s" % err, file=sys.stderr)
        return 1
    print("\n✅ 已发送到 Telegram（%d 字符）" % len(text))
    return 0


if __name__ == "__main__":
    sys.exit(main())
