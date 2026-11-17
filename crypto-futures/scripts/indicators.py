#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""indicators.py —— 规则用到的派生量（纯函数，不联网、不写盘）。

约定（docs/crypto-futures/requirement.md §3.1）：
- 「N 小时前」= 时间戳**恰好**早 N 小时的那个 5m 点；取不到返回 None，不找近似点；
- 7 天窗口 = 当前点之前的 2016 个 5m 点，**不含当前点**；
- 15 分钟主动买卖比 = 最近 3 个 5m 点**买入量之和 ÷ 卖出量之和**（不对比值取平均）。
"""
import math
import statistics

STEP = 300
HOUR = 3600
DAY = 86400


def value(series, metric, ts, idx=0):
    row = (series.get(metric) or {}).get(ts)
    if row is None or idx >= len(row) or row[idx] is None:
        return None
    return float(row[idx])


def latest_at(series, metric, ts, max_lag_s=1200):
    """ts 本身有点就返回 ts；否则返回 [ts-max_lag_s, ts) 内最新的点；都没有仍返回 ts（取值时自然为 None）。"""
    bucket = series.get(metric) or {}
    if ts in bucket:
        return ts
    for back in range(STEP, int(max_lag_s) + 1, STEP):
        if ts - back in bucket:
            return ts - back
    return ts


def pct_change(cur, ref):
    if cur is None or ref is None or ref == 0:
        return None
    return (cur / ref - 1.0) * 100.0


def chg(series, metric, ts, delta_s, idx=0):
    """metric 在 ts 相对 ts-delta_s 的变化百分比；任一点缺失返回 None。"""
    return pct_change(value(series, metric, ts, idx), value(series, metric, ts - delta_s, idx))


def price(series, ts):
    """合约价 = OI 美元价值 / OI 张数；张数为 0 或缺失、美元价值缺失时返回 None。"""
    oi = value(series, "OI", ts, 0)
    usd = value(series, "OI", ts, 1)
    if not oi or usd is None:
        return None
    return usd / oi


def price_change(series, ts, delta_s):
    return pct_change(price(series, ts), price(series, ts - delta_s))


def window(series, metric, ts, points, idx=0, keys=None):
    """ts 之前（不含 ts）的 points 个 5m 点（时间范围 [ts-points*5m, ts)）里存在的值。

    keys：该指标已排序的时间戳列表（批量回放时传入可避免逐点查找）。
    """
    import bisect
    bucket = series.get(metric) or {}
    keys = keys if keys is not None else sorted(bucket)
    lo = bisect.bisect_left(keys, ts - points * STEP)
    hi = bisect.bisect_left(keys, ts)
    out = []
    for t in keys[lo:hi]:
        row = bucket[t]
        if idx < len(row) and row[idx] is not None:
            out.append(float(row[idx]))
    return out


def quantile(values, q):
    """最近秩分位数（nearest-rank）：q=0.95 → 第 ceil(0.95n) 小的值；空列表返回 None。"""
    if not values:
        return None
    s = sorted(values)
    k = min(len(s), max(1, math.ceil(q * len(s))))
    return s[k - 1]


def rank_pct(values, v):
    """v 在 values 中的位置（≤ v 的比例，0–100）；空列表返回 None。"""
    if not values or v is None:
        return None
    return 100.0 * sum(1 for x in values if x <= v) / len(values)


def taker_15m(series, ts):
    """最近 3 个 5m 点（ts, ts-5m, ts-10m）的 (R, buy, sell, volume)；缺点、买入或卖出量为 0 返回 None。

    买入量为 0 时 R=0，下游要算 1/R（幅度、卡片文案），所以和卖出量为 0 一样视为无效数据。
    """
    rows = [(series.get("K") or {}).get(ts - i * STEP) for i in range(3)]
    if any(r is None for r in rows):
        return None
    buy = sum(float(r[0]) for r in rows)
    sell = sum(float(r[1]) for r in rows)
    if buy <= 0 or sell <= 0:
        return None
    return buy / sell, buy, sell, buy + sell


def taker_volume_baseline(series, ts, hours=24, min_share=0.5):
    """ts 之前 24h 内滚动 15 分钟成交量（每 5m 一个窗口）的中位数；有效窗口不足一半返回 None。"""
    n = int(hours * HOUR / STEP)
    vols = []
    for j in range(3, n + 3):
        t = taker_15m(series, ts - j * STEP)
        if t is not None:
            vols.append(t[3])
    if len(vols) < n * min_share:
        return None
    return statistics.median(vols)
