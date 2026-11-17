#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""reference.py —— 触发记录与「历史参照」统计（纯函数 + 简单落盘）。

docs §3.4：
- 统计同一规则、3 个资产合并的历史推送；只用**早于当前且已满 24h** 的触发（排除当前这次与结果未出的）；
- 数据来源：上线后的本地触发记录 + 上线前用 30 天缓存按同一规则代码回放出的触发（只预热一次）；
- 样本 < 3 显示「样本不足（N 次）」。这一行是提醒，不是预测。
"""
import statistics

import indicators as ind

KEEP_DAYS = 365


def _key(rec):
    return (int(rec["ts"]), rec["asset"], rec["rule"])


REALTIME_RULES = ("funding_extreme", "oi_spike")


def _price_at(series, ts, rule):
    """触发时的合约价（OI 美元价值 / 张数，与之后 24h 结果同口径）。

    M1 规则保持「恰好 ts」；实时规则落在尚未收盘的 5m 桶：取 20 分钟内最新的已收盘 OI 点，
    仍取不到才退回 RT 标记价（口径略有差别，但比没有结果好）。
    """
    if rule not in REALTIME_RULES:
        return ind.price(series, ts)
    p = ind.price(series, ind.latest_at(series, "OI", ts))
    return p if p is not None else ind.value(series, "RT", ts, 1)


def add_cards(records, cards, all_series, source="live"):
    """把推送卡片里的每条命中记成一条触发记录（同 ts+资产+规则去重），返回新增条数。"""
    seen = {_key(r) for r in records}
    added = 0
    for card in cards:
        s = all_series.get(card["asset"]) or {}
        for hit in card["hits"]:
            rec = {"ts": int(card["ts"]), "asset": card["asset"], "rule": hit["rule"],
                   "severity": hit["severity"], "direction": hit["direction"],
                   "magnitude": hit["magnitude"], "price": _price_at(s, card["ts"], hit["rule"]),
                   "fwd_pct": None, "source": source}
            if _key(rec) in seen:
                continue
            seen.add(_key(rec))
            records.append(rec)
            added += 1
    records.sort(key=lambda r: (r["ts"], r["asset"], r["rule"]))
    return added


def fill_outcomes(records, all_series, horizon_h=24):
    """为还没有结果的记录补「之后 horizon_h 小时的合约价涨跌」，返回补上的条数。"""
    filled = 0
    for rec in records:
        if rec.get("fwd_pct") is not None or rec.get("price") is None:
            continue
        later = ind.price(all_series.get(rec["asset"]) or {}, int(rec["ts"]) + int(horizon_h * 3600))
        change = ind.pct_change(later, rec["price"])
        if change is not None:
            rec["fwd_pct"] = round(change, 4)
            filled += 1
    return filled


def prune(records, now, keep_days=KEEP_DAYS):
    records[:] = [r for r in records if now - int(r["ts"]) <= keep_days * 86400]


def stats(records, rule, now, lookback_days=30, horizon_h=24, min_samples=3):
    """{"n", "up", "down", "median", "enough"}；只统计 ts+horizon ≤ now 且已有结果的同规则记录。"""
    horizon = horizon_h * 3600
    outs = [r["fwd_pct"] for r in records
            if r["rule"] == rule and r.get("fwd_pct") is not None
            and int(r["ts"]) + horizon <= now and now - int(r["ts"]) <= lookback_days * 86400]
    return {"n": len(outs), "up": sum(1 for x in outs if x > 0), "down": sum(1 for x in outs if x < 0),
            "median": statistics.median(outs) if outs else None, "enough": len(outs) >= min_samples,
            "lookback_days": lookback_days}
