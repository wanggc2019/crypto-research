#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core.py —— crypto-analyst 的分析核心（纯函数 + 只读本地 K 线缓存，stdlib）。

设计原则：
  1. 所有数字来自数据文件，函数可单测（不联网、不读时钟）；
  2. 每个统计都返回样本量，重叠样本必须能被调用方看见；
  3. 不产出「点位预测」，只产出可观测状态与条件位。

数据：data/raw/klines/<sym>_1d.json，由 scripts/fetch_history.py 生成。
"""
import json
import math
import os
import statistics as st

SYMS = ["BTC", "ETH", "BNB", "SOL"]


# ---------- 数据加载 ----------

def klines_dir(base_dir):
    return os.path.join(base_dir, "data", "raw", "klines")


def load_bars(sym, base_dir):
    """读取单个币种日线；缺失时抛 FileNotFoundError（调用方给可读提示）。"""
    path = os.path.join(klines_dir(base_dir), f"{sym.lower()}_1d.json")
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    with open(path) as f:
        return json.load(f)


def load_all(base_dir, syms=None):
    return {s: load_bars(s, base_dir) for s in (syms or SYMS) if os.path.exists(
        os.path.join(klines_dir(base_dir), f"{s.lower()}_1d.json"))}


# ---------- 基础统计 ----------

def sma(vals, n):
    return sum(vals[-n:]) / n if len(vals) >= n else None


def rets(vals):
    return [vals[i] / vals[i - 1] - 1 for i in range(1, len(vals))]


def stdev(xs):
    return st.stdev(xs) if len(xs) >= 2 else 0.0


def ann_vol(vals, n, periods=365):
    r = rets(vals)[-n:] if len(vals) > n else rets(vals)
    return stdev(r) * math.sqrt(periods) * 100


def max_drawdown(equity):
    """返回 (最大回撤, 峰值索引, 谷值索引)；回撤为负数。"""
    peak, dd, pk, tr = equity[0], 0.0, 0, 0
    for i, v in enumerate(equity):
        if v > peak:
            peak, pk = v, i
        cur = v / peak - 1
        if cur < dd:
            dd, tr = cur, i
    return dd, pk, tr


def pct(x, digits=1, sign=True):
    if x is None:
        return "n/a"
    return f"{x:+.{digits}f}%" if sign else f"{x:.{digits}f}%"


def money(x):
    return f"${x:,.0f}"


# ---------- 状态 ----------

def state_row(sym, bars):
    """单个币种的当前状态（全部可观测）。"""
    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    last = closes[-1]
    ma50, ma200 = sma(closes, 50), sma(closes, 200)
    ath = max(highs)
    ath_i = max(range(len(bars)), key=lambda i: highs[i])
    w52 = bars[-365:] if len(bars) >= 365 else bars
    hi52 = max(b["high"] for b in w52)
    lo52 = min(b["low"] for b in w52)
    w90 = bars[-90:]
    hi90 = max(b["high"] for b in w90)
    v30 = sum(b["volume"] for b in bars[-30:]) / min(30, len(bars))
    v90 = sum(b["volume"] for b in bars[-90:]) / min(90, len(bars))
    row = {
        "sym": sym,
        "last": last,
        "ma50": ma50,
        "ma200": ma200,
        "vs_ma50_pct": (last / ma50 - 1) * 100 if ma50 else None,
        "vs_ma200_pct": (last / ma200 - 1) * 100 if ma200 else None,
        "ath": ath,
        "ath_date": bars[ath_i]["date"],
        "dd_from_ath_pct": (last / ath - 1) * 100,
        "hi52": hi52,
        "lo52": lo52,
        "pos_52w_pct": (last - lo52) / (hi52 - lo52) * 100 if hi52 > lo52 else None,
        "vs_52w_low_pct": (last / lo52 - 1) * 100,
        "hi90": hi90,
        "vs_90d_high_pct": (last / hi90 - 1) * 100,
        "vol_ratio_30_90": v30 / v90 if v90 else None,
    }
    for n, key in ((30, "ret_30d_pct"), (90, "ret_90d_pct"), (365, "ret_1y_pct")):
        row[key] = (closes[-1] / closes[-1 - n] - 1) * 100 if len(closes) > n else None
    row["rv30_pct"] = ann_vol(closes, 30)
    row["rv90_pct"] = ann_vol(closes, 90)
    return row


def basket(px_by_sym):
    """等权组合（日再平衡）的净值序列与日期。"""
    common = sorted(set.intersection(*[set(px_by_sym[s]) for s in px_by_sym]))
    nav = [sum(px_by_sym[s][d] for s in px_by_sym) / len(px_by_sym) for d in common]
    return common, nav


def equal_weight_stats(px_by_sym):
    common, nav = basket(px_by_sym)
    eq = [1.0]
    for r in rets(nav):
        eq.append(eq[-1] * (1 + r))
    yrs = len(nav) / 365.25
    dd, pk, tr = max_drawdown(eq)
    i_ath = max(range(len(eq)), key=lambda i: eq[i])
    return {
        "start": common[0],
        "end": common[-1],
        "years": yrs,
        "cagr_pct": (eq[-1] ** (1 / yrs) - 1) * 100 if yrs > 0 else None,
        "vol_ann_pct": stdev(rets(nav)) * math.sqrt(365) * 100,
        "maxdd_pct": dd * 100,
        "dd_peak": common[pk],
        "dd_trough": common[tr],
        "dd_from_ath_pct": (eq[-1] / eq[i_ath] - 1) * 100,
        "ret_30d_pct": (eq[-1] / eq[-31] - 1) * 100 if len(eq) > 31 else None,
        "ret_90d_pct": (eq[-1] / eq[-91] - 1) * 100 if len(eq) > 91 else None,
        "ret_1y_pct": (eq[-1] / eq[-366] - 1) * 100 if len(eq) > 366 else None,
    }


def correlation(px_by_sym, days=365):
    syms = list(px_by_sym)
    common = sorted(set.intersection(*[set(px_by_sym[s]) for s in syms]))[-days - 1:]
    rmap = {s: rets([px_by_sym[s][d] for d in common]) for s in syms}
    out = {}
    for i, a in enumerate(syms):
        for b in syms[i + 1:]:
            x, y = rmap[a], rmap[b]
            mx, my = sum(x) / len(x), sum(y) / len(y)
            cov = sum((x[j] - mx) * (y[j] - my) for j in range(len(x)))
            sx = math.sqrt(sum((v - mx) ** 2 for v in x))
            sy = math.sqrt(sum((v - my) ** 2 for v in y))
            out[f"{a}-{b}"] = cov / (sx * sy) if sx and sy else 0.0
    return out


# ---------- 条件统计（历史类比） ----------

def ma200_zone_stats(bars, lo_pct=0.0, hi_pct=5.0, horizon=365, gap_days=10):
    """价格处于 MA200 上方 lo_pct%~hi_pct% 区间时，之后 horizon 天的表现。

    返回日级样本（重叠，不能当概率）与时段级样本（相互独立，但可能被单一行情段主导），
    以及时间段列表，调用方必须同时展示两者（本次研究里两者结论相反，是重要方法论教训）。
    """
    closes = [b["close"] for b in bars]
    dates = [b["date"] for b in bars]
    qty = len(closes)

    def fwd(i, h):
        return (closes[i + h] / closes[i] - 1) * 100 if i + h < qty else None

    zone = []
    for i in range(200, qty):
        m = sma(closes[:i + 1], 200)
        if m is None:
            continue
        p = (closes[i] / m - 1) * 100
        if lo_pct <= p <= hi_pct:
            zone.append(i)

    day = [fwd(i, horizon) for i in zone if fwd(i, horizon) is not None]
    episodes, cur = [], []
    for i in zone:
        if cur and i - cur[-1] <= gap_days:
            cur.append(i)
        else:
            if cur:
                episodes.append(cur)
            cur = [i]
    if cur:
        episodes.append(cur)

    ep_rows = []
    for ep in episodes:
        i = ep[-1]
        ep_rows.append({
            "start": dates[ep[0]],
            "end": dates[i],
            "days": len(ep),
            "fwd": fwd(i, horizon),
        })
    ep_vals = [r["fwd"] for r in ep_rows if r["fwd"] is not None]
    return {
        "zone_pct": [lo_pct, hi_pct],
        "horizon": horizon,
        "day": {
            "n": len(day),
            "median": st.median(day) if day else None,
            "pct_positive": 100 * sum(v > 0 for v in day) / len(day) if day else None,
        },
        "episode": {
            "n": len(ep_vals),
            "median": st.median(ep_vals) if ep_vals else None,
            "pct_positive": 100 * sum(v > 0 for v in ep_vals) / len(ep_vals) if ep_vals else None,
        },
        "episodes": ep_rows,
    }


# ---------- 情景 ----------

def scenario_levels(state):
    """从状态推导三个可观测位（不预测，只给条件位）。"""
    return {
        "support_ma200": state["ma200"],
        "resistance_ma50": state["ma50"],
        "high_90d": state["hi90"],
        "low_52w": state["lo52"],
    }


def band_of_price(px, bands):
    """把价格映射到情景区间；bands = {band_id: [lo, hi]}，hi 可为 None 表示无上限。"""
    for band_id, (lo, hi) in bands.items():
        if px >= lo and (hi is None or px < hi):
            return band_id
    return "other"


def brier(probs, realized_band):
    """多分类 Brier 分数（越低越好）；probs = {band: p}。"""
    total = 0.0
    for band, p in probs.items():
        total += (p - (1.0 if band == realized_band else 0.0)) ** 2
    return round(total, 4)
