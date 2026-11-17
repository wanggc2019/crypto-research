#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""backtest.py —— 用历史 5m 数据离线回放告警规则（与线上共用 rules.py / engine.py）。

数据目录格式（与 tests/fixtures 相同）：``<SYMBOL>_<endpoint>.json`` 或 ``.json.gz``，
内容为 Binance ``/futures/data/<endpoint>`` 的原始 JSON 列表（例如 ``BTCUSDT_globalLongShortAccountRatio.json.gz``）。

M2 两条实时规则（funding_extreme / oi_spike）没有历史接口，回测时由历史数据推出 RT 序列（``attach_rt``）：
- 实时 OI / 标记价 ← ``openInterestHist`` 的 5m 张数与 ``美元价值 / 张数``（线上用实时值，见 requirement-m2 §4.3）；
- 资金费率 ← 可选的 ``<SYMBOL>_fundingRate.json``（``/fapi/v1/fundingRate`` 结算记录），**只放在结算时刻所在的 5m 桶**，
  所以回测数的是「越过阈值的结算次数」，会低估线上实时值的触发（requirement-m2 §5 已知风险）。

用法：
    python3 scripts/backtest.py --data <dir>            # 打印推送卡片与各规则次数
    python3 scripts/backtest.py --data <dir> --json     # 机器可读
    python3 scripts/backtest.py --cache data/state      # 直接用线上每日刷新的 30 天缓存（cache30d-*.json）
    python3 scripts/backtest.py --cache data/state --data <dir>   # 同上，资金费率结算记录取自 <dir>
"""
import argparse
import json
import os
import sys

import engine
import series as S
from sources import PARSERS, read_raw

import common


def load_series(data_dir, assets):
    """{asset: series}；assets 为 {"BTC": "BTCUSDT", ...}。"""
    out = {}
    for asset, symbol in assets.items():
        s = S.empty()
        for metric, endpoint in S.ENDPOINTS.items():
            raw = read_raw(data_dir, symbol, endpoint)
            S.merge(s, metric, S.rows_to_points(metric, PARSERS[metric](raw)))
        out[asset] = s
    return out


def load_cache(state_dir, assets):
    """线上 run.py 每日写的 cache30d-<ASSET>.json → {asset: series}。"""
    return {a: S.load(os.path.join(state_dir, "cache30d-%s.json" % a)) for a in assets}


def attach_rt(all_series, assets, data_dir=None):
    """由 OI 历史（+ 可选资金费率结算记录）推出 RT 序列，原地写入各资产 series。"""
    for asset, symbol in assets.items():
        s = all_series[asset]
        pts = {}
        for ts, (oi, usd) in (s.get("OI") or {}).items():
            pts[ts] = [None, usd / oi if oi and usd is not None else None, oi, None]
        for row in _funding_rows(data_dir, symbol):
            ts = S.bucket(row["ts"])
            pts.setdefault(ts, [None, None, None, None])[0] = row["rate"]
        S.merge(s, S.RT, {ts: tuple(v) for ts, v in pts.items()})
    return all_series


def _funding_rows(data_dir, symbol):
    raw = read_raw(data_dir, symbol, "fundingRate") if data_dir else None
    rows = []
    for item in raw if isinstance(raw, list) else []:
        try:
            rows.append({"ts": int(item["fundingTime"]) // 1000, "rate": float(item["fundingRate"])})
        except (KeyError, TypeError, ValueError):
            continue
    return rows


def sorted_keys(all_series):
    return {a: {m: sorted(s.get(m) or {}) for m in S.METRICS} for a, s in all_series.items()}


def replay(all_series, cfg, start=None, end=None, warmup_s=86400):
    """从 start（默认：最早数据 + 24h 预热）到 end 逐 5m 调用 engine.step，返回 (cards, p2_hits, days)。"""
    starts = [S.earliest_ts(s, "G") for s in all_series.values() if S.earliest_ts(s, "G")]
    ends = [S.latest_ts(s, "G") for s in all_series.values() if S.latest_ts(s, "G")]
    if not starts:
        return [], [], 0.0
    start = start if start is not None else min(starts) + warmup_s
    end = end if end is not None else max(ends)
    state = engine.new_state()
    keys = sorted_keys(all_series)
    cards, p2 = [], []
    for now in range(start - start % S.STEP, end + 1, S.STEP):
        c, p = engine.step(all_series, state, cfg, now, catchup_s=S.STEP, keys=keys)
        cards.extend(c)
        p2.extend(p)
    return cards, p2, (end - start) / 86400.0


def summarize(cards, p2, days):
    by_rule, by_asset, by_sev = {}, {}, {}
    for c in cards:
        by_asset[c["asset"]] = by_asset.get(c["asset"], 0) + 1
        by_sev[c["severity"]] = by_sev.get(c["severity"], 0) + 1
        for h in c["hits"]:
            by_rule[h["rule"]] = by_rule.get(h["rule"], 0) + 1
    return {"days": round(days, 2), "cards": len(cards),
            "per_day": round(len(cards) / days, 2) if days else None,
            "by_rule": by_rule, "by_asset": by_asset, "by_severity": by_sev, "p2_hits": len(p2)}


def main(argv=None):
    parser = argparse.ArgumentParser(description="crypto-futures 规则离线回放")
    parser.add_argument("--data", help="原始 5m 数据目录（见文件头说明）")
    parser.add_argument("--cache", help="线上 30 天缓存目录（含 cache30d-<ASSET>.json，通常是 data/state）")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    app = common.load_config("app.yaml")
    cfg = common.load_config("rules.yaml")
    assets = app.get("assets") or {}
    if not args.data and not args.cache:
        parser.error("需要 --data 或 --cache")
    all_series = load_cache(args.cache, assets) if args.cache else load_series(args.data, assets)
    if not any(S.latest_ts(s, "G") for s in all_series.values()):
        print("❌ 没有读到任何 5m 数据（检查 --cache / --data 路径），不能当作「零触发」", file=sys.stderr)
        return 2
    attach_rt(all_series, assets, args.data)
    cards, p2, days = replay(all_series, cfg)
    summary = summarize(cards, p2, days)
    if args.json:
        print(json.dumps({"summary": summary, "cards": cards}, ensure_ascii=False, indent=1))
        return 0
    for c in cards:
        print("%s %s %s %s%s%s" % (common.fmt_local(c["ts"]), c["asset"], c["severity"], c["rule"],
                                   "（加剧）" if c["escalated"] else "",
                                   " + " + ",".join(c["also"]) if c["also"] else ""))
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
