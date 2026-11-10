#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""indicators.py —— 行情指标计算（纯函数：不读配置、不联网、不写盘）。

约定（见 docs/crypto-price/design.md §5）：
- K 线旧→新排序，字段 open_time/open/high/low/close/volume/close_time，时间单位 epoch 秒；
- 只用「已完成」K 线：close_time <= now_ts；
- 任何脏数据（空列表 / None / 除零 / 负值 / 非数值）返回 None 或跳过，绝不抛异常。

调用方（rules.py）负责把 None 解释为「样本不足，本窗口/本规则跳过」。
"""
import math


def _num(value):
    """转成有限 float；非法值（None/字符串/NaN/Inf）返回 None。"""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _positive(value):
    """只接受 >0 的有限数值，其余返回 None（价格/成交量/持仓量均须为正）。"""
    num = _num(value)
    return num if num is not None and num > 0 else None


def _completed_rows(candles, now_ts):
    """筛出已完成 K 线（close_time <= now_ts），按 close_time 升序返回，不修改入参。"""
    rows = []
    for row in candles or []:
        if not isinstance(row, dict):
            continue
        close_time = _num(row.get("close_time"))
        if close_time is None:
            continue
        rows.append((close_time, row))
    rows.sort(key=lambda item: item[0])
    return [row for close_time, row in rows if close_time <= now_ts]


def pct_change(price, reference):
    """(price / reference - 1) * 100；除零、负值、非数值返回 None。"""
    current = _positive(price)
    base = _positive(reference)
    if current is None or base is None:
        return None
    return (current / base - 1.0) * 100.0


def window_moves(candles, price, now_ts, windows):
    """各时间窗口涨跌幅（百分比）。

    windows: {"m1": 60, "m5": 300, ...}（窗口长度，秒）。
    每个窗口的参照价 = close_time <= now_ts - window 的最后一根已完成 K 线收盘价；
    样本不足（找不到参照 K 线）时该窗口不出现在结果里。返回 {"m1": -0.8, ...}。
    """
    current = _positive(price)
    now = _num(now_ts)
    if current is None or now is None or not isinstance(windows, dict):
        return {}
    rows = _completed_rows(candles, now)
    if not rows:
        return {}
    out = {}
    for name, seconds in windows.items():
        span = _positive(seconds)
        if span is None:
            continue
        cutoff = now - span
        reference = None
        # rows 已按 close_time 升序；最后一个 <= cutoff 的即为最近参照。
        for row in rows:
            close_time = _num(row.get("close_time"))
            if close_time is None or close_time > cutoff:
                break
            reference = _num(row.get("close"))
        change = pct_change(current, reference)
        if change is not None:
            out[name] = change
    return out


def volume_ratio(candles, now_ts, window_min=5, baseline_min=55):
    """近端放量倍数：近 window_min 根每分钟均量 / 之前 baseline_min 根每分钟均量。

    返回 {"ratio": 4.2, "window_volume": 12.3, "baseline_avg": 2.9, "window_min": 5}，
    其中 window_volume / baseline_avg 均为「每分钟均量」。样本不足或基线为 0 时返回 None。
    """
    now = _num(now_ts)
    window = _positive(window_min)
    baseline = _positive(baseline_min)
    if now is None or window is None or baseline is None:
        return None
    window_min = int(window)
    baseline_min = int(baseline)
    if window_min <= 0 or baseline_min <= 0:
        return None
    rows = _completed_rows(candles, now)
    need = window_min + baseline_min
    if len(rows) < need:
        return None
    recent = rows[-window_min:]
    base = rows[-need:-window_min]
    recent_volumes = [_num(row.get("volume")) for row in recent]
    base_volumes = [_num(row.get("volume")) for row in base]
    if any(v is None or v < 0 for v in recent_volumes + base_volumes):
        return None
    baseline_avg = sum(base_volumes) / float(baseline_min)
    if baseline_avg <= 0:
        return None
    window_volume = sum(recent_volumes) / float(window_min)
    return {
        "ratio": window_volume / baseline_avg,
        "window_volume": window_volume,
        "baseline_avg": baseline_avg,
        "window_min": window_min,
    }


def oi_change_pct(history, now_ts, window_min=15):
    """持仓量变化百分比：最新样本 /（now_ts - window_min 分钟）前的样本 - 1。

    history: [{"ts": epoch 秒, "oi": float}, ...] 旧→新（乱序也会先排序）。
    找不到足够旧的样本、无最新样本、参照 OI <= 0 时返回 None。
    """
    now = _num(now_ts)
    window = _positive(window_min)
    if now is None or window is None:
        return None
    samples = []
    for item in history or []:
        if not isinstance(item, dict):
            continue
        ts = _num(item.get("ts"))
        oi = _positive(item.get("oi"))
        if ts is None or oi is None or ts > now:
            continue
        samples.append((ts, oi))
    if not samples:
        return None
    samples.sort(key=lambda sample: sample[0])
    cutoff = now - window * 60.0
    if samples[-1][0] <= cutoff:
        return None  # 没有「最新」样本，只有窗口外的旧数据
    reference = None
    for ts, oi in samples:
        if ts <= cutoff:
            reference = oi
        else:
            break
    if reference is None:
        return None
    return pct_change(samples[-1][1], reference)
