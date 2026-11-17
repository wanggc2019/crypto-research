# -*- coding: utf-8 -*-
"""测试公共工具：注入 scripts 路径、构造合成序列、样例时点、离线 fixtures 位置。"""
import datetime
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import series as S  # noqa: E402

import common  # noqa: E402,F401

FIXTURES = os.path.join(HERE, "fixtures", "replay")
STEP = 300
T0 = 1_793_300_000 - 1_793_300_000 % STEP   # 任意 5m 对齐的时间点


def at(iso):
    """ISO 时间（带时区）→ epoch。样例时点一律用 UTC 写，避免依赖运行机器的时区。"""
    return int(datetime.datetime.fromisoformat(iso).timestamp())


# 需求 §5.3 的三个样例时点
BNB_P0 = at("2026-10-29T21:10:00+00:00")
BTC_DIV = at("2026-10-30T13:05:00+00:00")
ETH_SHIFT = at("2026-11-05T15:15:00+00:00")


def flat_series(end, points, g=2.0, t=1.5, buy=100.0, sell=100.0, oi=1000.0, px=100.0):
    """end 之前（含 end）共 points 个 5m 点的平稳序列。"""
    s = S.empty()
    for k in range(points):
        ts = end - k * STEP
        s["G"][ts] = (g, g / (1 + g))
        s["T"][ts] = (t, t / (1 + t))
        s["K"][ts] = (buy, sell)
        s["OI"][ts] = (oi, oi * px)
    return s


def rules_cfg(**overrides):
    cfg = {
        "rules": {
            "crowding_build": {"enabled": True, "severity": "P0", "oi_change_4h_pct": 5.0,
                               "high_quantile": 0.95, "low_quantile": 0.05},
            "smart_divergence": {"enabled": True, "severity": "P1", "change_24h_pct": 15.0, "cooldown_hours": 12},
            "retail_shift": {"enabled": True, "severity": "P1", "change_1h_pct": 20.0},
            "taker_burst": {"enabled": True, "severity": "P1", "ratio": 3.5, "volume_multiple": 3.0},
            "extreme_level": {"enabled": True, "severity": "P2"},
        },
        "window": {"points_7d": 2016, "min_coverage": 0.9},
        "governance": {"cooldown_hours": 4, "escalate_multiple": 1.5, "escalate_min_gap_hours": 1,
                       "p0_daily_cap": 2},
        "reference": {"lookback_days": 30, "min_samples": 3, "horizon_hours": 24},
    }
    for name, value in overrides.items():
        cfg["rules"][name].update(value)
    return cfg
