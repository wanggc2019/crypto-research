#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rules.py —— 7 条规则的判定（纯函数）。M1 5 条看持仓结构；M2 2 条（funding_extreme / oi_spike）看实时快照 RT。线上 run.py 与离线 backtest.py 共用这一份代码。

每条规则只依赖自己的输入（REQUIRES）；输入缺失或过期时只跳过这条规则（docs §3.1）。
返回的 hit：``{"rule", "severity", "direction", "magnitude", "metrics"}``；
magnitude 用于冷却期内「加剧」判断，同一规则内可比较。
"""
import indicators as ind

REQUIRES = {
    "crowding_build": ("G", "OI"),
    "smart_divergence": ("G", "T"),
    "retail_shift": ("G",),
    "taker_burst": ("K",),
    "extreme_level": ("G",),
    "funding_extreme": ("RT",),
    "oi_spike": ("RT",),
}
# 同一数据点同级别多条命中时，ORDER 靠前者做卡片主规则：M1 规则在前，M2 迁入的两条随后
ORDER = ("crowding_build", "smart_divergence", "retail_shift", "taker_burst", "funding_extreme", "oi_spike",
         "extreme_level")
RT_REF_BACK = (15 * 60, 20 * 60)   # oi_spike 参考桶：优先 −15 分钟，缺失用 −20 分钟（docs requirement-m2 §3.2）
DEFAULT_FUNDING_HOURS = 8
SEVERITY_RANK = {"P0": 0, "P1": 1, "P2": 2}


def _rule_cfg(cfg, name):
    rc = ((cfg or {}).get("rules") or {}).get(name) or {}
    return rc if rc.get("enabled", True) else None


def _hit(name, rc, direction, magnitude, metrics, severity=None):
    return {"rule": name, "severity": severity or rc.get("severity", "P1"), "direction": direction,
            "magnitude": round(float(magnitude), 6), "metrics": metrics}


def _window_g(series, ts, cfg, keys):
    win = (cfg or {}).get("window") or {}
    points = int(win.get("points_7d", 2016))
    vals = ind.window(series, "G", ts, points, keys=(keys or {}).get("G"))
    if len(vals) < points * float(win.get("min_coverage", 0.9)):
        return None
    return vals


def crowding_build(series, ts, cfg, keys=None):
    rc = _rule_cfg(cfg, "crowding_build")
    if not rc:
        return None
    oi_chg = ind.chg(series, "OI", ts, 4 * ind.HOUR, idx=1)
    g = ind.value(series, "G", ts)
    if oi_chg is None or g is None or oi_chg < float(rc.get("oi_change_4h_pct", 5.0)):
        return None
    vals = _window_g(series, ts, cfg, keys)
    if vals is None:
        return None
    hi = ind.quantile(vals, float(rc.get("high_quantile", 0.95)))
    lo = ind.quantile(vals, float(rc.get("low_quantile", 0.05)))
    if g >= hi:
        direction = "long"
    elif g <= lo:
        direction = "short"
    else:
        return None
    return _hit("crowding_build", rc, direction, oi_chg,
                {"oi_change_4h_pct": oi_chg, "g": g, "g_p95": hi, "g_p5": lo})


def smart_divergence(series, ts, cfg, keys=None):
    rc = _rule_cfg(cfg, "smart_divergence")
    if not rc:
        return None
    a = float(rc.get("change_24h_pct", 15.0))
    dg = ind.chg(series, "G", ts, ind.DAY)
    dt = ind.chg(series, "T", ts, ind.DAY)
    if dg is None or dt is None:
        return None
    if dg >= a and dt <= -a:
        direction = "retail_long"
    elif dg <= -a and dt >= a:
        direction = "retail_short"
    else:
        return None
    return _hit("smart_divergence", rc, direction, min(abs(dg), abs(dt)),
                {"g_change_24h_pct": dg, "t_change_24h_pct": dt})


def retail_shift(series, ts, cfg, keys=None):
    rc = _rule_cfg(cfg, "retail_shift")
    if not rc:
        return None
    d1 = ind.chg(series, "G", ts, ind.HOUR)
    if d1 is None or abs(d1) < float(rc.get("change_1h_pct", 20.0)):
        return None
    return _hit("retail_shift", rc, "up" if d1 > 0 else "down", abs(d1), {"g_change_1h_pct": d1})


def taker_burst(series, ts, cfg, keys=None):
    rc = _rule_cfg(cfg, "taker_burst")
    if not rc:
        return None
    t = ind.taker_15m(series, ts)
    if t is None:
        return None
    r, buy, sell, vol = t
    limit = float(rc.get("ratio", 3.5))
    if not (r >= limit or r <= 1.0 / limit):
        return None
    base = ind.taker_volume_baseline(series, ts)
    if not base or vol < float(rc.get("volume_multiple", 3.0)) * base:
        return None
    return _hit("taker_burst", rc, "buy" if r > 1 else "sell", max(r, 1.0 / r),
                {"taker_ratio_15m": r, "buy_vol": buy, "sell_vol": sell, "volume_multiple": vol / base})


def extreme_level(series, ts, cfg, keys=None):
    rc = _rule_cfg(cfg, "extreme_level")
    if not rc:
        return None
    g = ind.value(series, "G", ts)
    if g is None:
        return None
    vals = _window_g(series, ts, cfg, keys)
    if vals is None:
        return None
    hi, lo = max(vals), min(vals)
    if g > hi:
        return _hit("extreme_level", rc, "high", g, {"g": g, "g_max_7d": hi})
    if g < lo:
        return _hit("extreme_level", rc, "low", g, {"g": g, "g_min_7d": lo})
    return None


def _tiered(value, rc, p1_key, p0_key, p1_default):
    """分级阈值：返回 "P0" / "P1" / None（未到 P1）。缺省值与 config/rules.yaml 一致。"""
    if value < float(rc.get(p1_key, p1_default)):
        return None
    p0 = rc.get(p0_key)
    return "P0" if p0 is not None and value >= float(p0) else "P1"


def funding_extreme(series, ts, cfg, keys=None):
    """当前资金费率绝对值越过阈值；正 = 多头付费（long），负 = 空头付费（short）。"""
    rc = _rule_cfg(cfg, "funding_extreme")
    if not rc:
        return None
    rate = ind.value(series, "RT", ts, 0)
    if rate is None:
        return None
    rate_pct = rate * 100.0
    severity = _tiered(abs(rate_pct), rc, "p1_abs_pct", "p0_abs_pct", 0.10)
    if severity is None:
        return None
    hours = ind.value(series, "RT", ts, 3)
    known = hours is not None and hours > 0
    hours = hours if known else DEFAULT_FUNDING_HOURS
    return _hit("funding_extreme", rc, "long" if rate > 0 else "short", abs(rate_pct),
                {"rate_pct": rate_pct, "interval_hours": hours, "interval_known": known,
                 "annualized_pct": rate_pct * (24.0 / hours) * 365,
                 "mark_price": ind.value(series, "RT", ts, 1)}, severity=severity)


def oi_spike(series, ts, cfg, keys=None):
    """实时 OI 15 分钟急变且合约标记价同向变动；OI 与标记价用同一个参考桶。"""
    rc = _rule_cfg(cfg, "oi_spike")
    if not rc:
        return None
    oi, mark = ind.value(series, "RT", ts, 2), ind.value(series, "RT", ts, 1)
    if not oi or not mark:
        return None
    for back in RT_REF_BACK:
        oi0, mark0 = ind.value(series, "RT", ts - back, 2), ind.value(series, "RT", ts - back, 1)
        if oi0 and mark0:
            break
    else:
        return None
    oi_chg, move = ind.pct_change(oi, oi0), ind.pct_change(mark, mark0)
    if oi_chg == 0 or abs(move) < float(rc.get("min_price_move_pct", 0.5)) or (oi_chg > 0) != (move > 0):
        return None
    severity = _tiered(abs(oi_chg), rc, "p1_change_pct", "p0_change_pct", 5.0)
    if severity is None:
        return None
    return _hit("oi_spike", rc, "up" if oi_chg > 0 else "down", abs(oi_chg),
                {"oi_change_pct": oi_chg, "price_change_pct": move, "window_min": back // 60,
                 "oi": oi, "oi_ref": oi0, "mark_price": mark}, severity=severity)


RULES = {
    "crowding_build": crowding_build,
    "funding_extreme": funding_extreme,
    "oi_spike": oi_spike,
    "smart_divergence": smart_divergence,
    "retail_shift": retail_shift,
    "taker_burst": taker_burst,
    "extreme_level": extreme_level,
}


def evaluate(series, ts, cfg, rules=None, keys=None):
    """在 ts 这个 5m 点上评估 rules（默认全部），返回命中的 hit 列表（按 ORDER）。"""
    out = []
    for name in ORDER:
        if rules is not None and name not in rules:
            continue
        hit = RULES[name](series, ts, cfg, keys)
        if hit:
            out.append(hit)
    return out
