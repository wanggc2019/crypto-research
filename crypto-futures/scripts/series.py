#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""series.py —— 每个资产的 5m 序列（内存结构 + 落盘），纯数据操作，不联网。

内存结构：``{"G": {ts: (ratio, long_share)}, "T": {...}, "K": {ts: (buy_vol, sell_vol)}, "OI": {ts: (oi, oi_usd)}}``
- ``G`` 账户多空比（globalLongShortAccountRatio）；``T`` 大户持仓多空比（topLongShortPositionRatio）；
- ``K`` 合约主动买卖量（takerlongshortRatio）；``OI`` 持仓量历史（openInterestHist）；
- ``ts`` 为 epoch 秒（5m 对齐），同一 ts 只保留一份（后到覆盖先到）。
"""
import json

METRICS = ("G", "T", "K", "OI")
ENDPOINTS = {
    "G": "globalLongShortAccountRatio",
    "T": "topLongShortPositionRatio",
    "K": "takerlongshortRatio",
    "OI": "openInterestHist",
}
STEP = 300   # 5m


def empty():
    return {m: {} for m in METRICS}


def rows_to_points(metric, rows):
    """把 crypto_core.market.binance 的解析结果转成 {ts: tuple}。"""
    out = {}
    for r in rows or []:
        ts = r.get("ts")
        if ts is None:
            continue
        if metric in ("G", "T"):
            out[int(ts)] = (r["ratio"], r["long_share"])
        elif metric == "K":
            out[int(ts)] = (r["buy_vol"], r["sell_vol"])
        else:
            out[int(ts)] = (r["oi"], r.get("oi_usd"))
    return out


def merge(series, metric, points):
    """并入新点，返回新增点数（已有 ts 覆盖但不计新增）。"""
    bucket = series.setdefault(metric, {})
    added = sum(1 for ts in points if ts not in bucket)
    bucket.update(points)
    return added


def prune(series, keep_from_ts):
    for bucket in series.values():
        for ts in [t for t in bucket if t < keep_from_ts]:
            del bucket[ts]


def latest_ts(series, metric):
    bucket = series.get(metric) or {}
    return max(bucket) if bucket else None


def earliest_ts(series, metric):
    bucket = series.get(metric) or {}
    return min(bucket) if bucket else None


def count_between(series, metric, start_ts, end_ts):
    """[start_ts, end_ts) 内的点数。"""
    return sum(1 for ts in (series.get(metric) or {}) if start_ts <= ts < end_ts)


def to_json(series):
    return {m: {str(ts): list(v) for ts, v in sorted((series.get(m) or {}).items())} for m in METRICS}


def from_json(data):
    out = empty()
    if not isinstance(data, dict):
        return out
    for m in METRICS:
        for ts, v in (data.get(m) or {}).items():
            try:
                out[m][int(ts)] = tuple(v)
            except (TypeError, ValueError):
                continue
    return out


def load(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return from_json(json.load(f))
    except (OSError, ValueError):
        return empty()


def dump(path, series):
    """紧凑写盘（不缩进；7 天 × 4 指标 × 2016 点约 0.3 MB/资产），先写临时文件再原子替换。"""
    import os
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(to_json(series), f, separators=(",", ":"))
    os.replace(tmp, path)
