#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""outputs.py —— 本轮产物：跨模块导出（futures/snapshot/v1）、healthcheck 用的 last_run.json、原始记录。"""
import json
import os

import indicators as ind
import series as S

import common

METRIC_CN = {"G": "账户多空比", "T": "大户持仓比", "K": "主动买卖量", "OI": "持仓量", "F": "资金费率",
             "LOI": "实时持仓量"}


def _asset_snapshot(series, funding, stale, now):
    ts = S.latest_ts(series, "G")
    taker = ind.taker_15m(series, S.latest_ts(series, "K")) if S.latest_ts(series, "K") else None
    oi_ts = S.latest_ts(series, "OI")
    return {
        "ts": ts,
        "account_ratio": ind.value(series, "G", ts) if ts else None,
        "account_long_share": ind.value(series, "G", ts, 1) if ts else None,
        "account_ratio_change_24h_pct": ind.chg(series, "G", ts, ind.DAY) if ts else None,
        "top_position_ratio": ind.value(series, "T", ts) if ts else None,
        "top_position_ratio_change_24h_pct": ind.chg(series, "T", ts, ind.DAY) if ts else None,
        "taker_ratio_15m": taker[0] if taker else None,
        "oi_usd": ind.value(series, "OI", oi_ts, 1) if oi_ts else None,
        "oi_change_4h_pct": ind.chg(series, "OI", oi_ts, 4 * ind.HOUR, idx=1) if oi_ts else None,
        "price": ind.price(series, oi_ts) if oi_ts else None,
        "price_change_24h_pct": ind.price_change(series, oi_ts, ind.DAY) if oi_ts else None,
        "funding_rate": (funding or {}).get("rate"),
        "stale": sorted(m for m in stale if m in S.METRICS),   # RT 不进导出，保持 futures/snapshot/v1 不变
    }


def build_export(all_series, fundings, stale, records, now):
    today = common.local_date(now)
    counts = {}
    for r in records:
        if r.get("source") == "live" and common.local_date(r["ts"]) == today:
            counts[r["severity"]] = counts.get(r["severity"], 0) + 1
    return {
        "schema": "futures/snapshot/v1",   # 字面量：契约登记检查按它找生产方
        "generated_at": common.ts_iso(),
        "source": "binance_usdm",
        "scope": "Binance USD-M 单所 · 5m 数据",
        "assets": {a: _asset_snapshot(s, fundings.get(a), stale.get(a) or set(), now)
                   for a, s in sorted(all_series.items())},
        "today_signals": counts,
    }


def endpoints_down(all_series, funding_ok, now, app, oi_ok=None):
    """{asset: [指标中文名]}：最新数据点早于 (过期阈值 + 失效阈值)，或资金费率 / 实时 OI 超过失效阈值没取到。

    oi_ok 为 None（旧调用方）时不检查实时 OI。
    """
    hist = (app or {}).get("history") or {}
    down_s = float(((app or {}).get("health") or {}).get("endpoint_down_min", 60)) * 60
    limit = now - float(hist.get("stale_min", 20)) * 60 - down_s
    out = {}
    for asset, s in sorted(all_series.items()):
        bad = [METRIC_CN[m] for m in S.METRICS if (S.latest_ts(s, m) or 0) < limit]
        if now - float(funding_ok.get(asset) or 0) > down_s:
            bad.append(METRIC_CN["F"])
        if oi_ok is not None and now - float(oi_ok.get(asset) or 0) > down_s:
            bad.append(METRIC_CN["LOI"])
        if bad:
            out[asset] = bad
    return out


def last_run(now, requests, errors, down, cards):
    """healthcheck 读取：primary_ok=False 时 FAIL 并用 detail 作告警正文（按模块冷却）。"""
    detail = ""
    if down:
        detail = "接口超过 1 小时无新数据，相关规则已停用：" + "；".join(
            "%s %s" % (a, "、".join(m)) for a, m in down.items())
    return {"ts": common.ts_iso(), "epoch": now, "primary_ok": not down, "primary_complete": not down,
            "primary_missing": down, "detail": detail, "requests": requests,
            "errors": errors[:20], "cards": len(cards)}


def append_jsonl(path, rows):
    if not rows:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
