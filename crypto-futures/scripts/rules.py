#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rules.py —— 5 条规则的判定（纯函数）。线上 run.py 与离线 backtest.py 共用这一份代码。

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
}
ORDER = ("crowding_build", "smart_divergence", "retail_shift", "taker_burst", "extreme_level")
SEVERITY_RANK = {"P0": 0, "P1": 1, "P2": 2}


def _rule_cfg(cfg, name):
    rc = ((cfg or {}).get("rules") or {}).get(name) or {}
    return rc if rc.get("enabled", True) else None


def _hit(name, rc, direction, magnitude, metrics):
    return {"rule": name, "severity": rc.get("severity", "P1"), "direction": direction,
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


RULES = {
    "crowding_build": crowding_build,
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
