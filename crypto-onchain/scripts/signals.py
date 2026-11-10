#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""signals.py —— 阈值判定：把指标变成"候选信号"（M1 只落盘，不推送）。

设计约束（对应 docs/crypto-onchain/requirement.md §5–§6）：
- 单信号单阈值不直接下结论：每个候选都带 aligned（与价格方向是否一致）；
- 方向不一致或未知时，severity 仍记录（供影子期评估阈值），但 would_alert=False；
- 输出中文 note 只描述"类别 + 证据"，**禁止任何身份推断**（"某机构/某巨鲸"）。
"""
from indicators import direction_of

SEVERITY_ORDER = {"P0": 0, "P1": 1}


def _candidate(signal, scope, severity, asset=None, exchange=None, value=None,
               threshold=None, direction=None, aligned=None, note="", extra=None):
    return {
        "signal": signal,
        "scope": scope,
        "asset": asset,
        "exchange": exchange,
        "severity": severity,
        "value": value,
        "threshold": threshold,
        "direction": direction,
        "aligned": aligned,
        "would_alert": bool(severity) and aligned is not False,
        "note": note,
        "extra": extra or {},
    }


def eval_taker(asset, stats, rules, price_direction):
    """逐笔主动卖压：占比 + 大单笔数 + 样本量三条件同时成立才出候选。"""
    r = (rules or {}).get("taker") or {}
    ratio = stats.get("sell_ratio")
    volume = stats.get("quote_volume_usd") or 0.0
    n_big = stats.get("large_sell_count") or 0
    if ratio is None:
        return None
    min_vol = r.get("min_window_quote_usd") or 0
    if isinstance(min_vol, dict):  # 支持按资产分层
        min_vol = min_vol.get(asset) or 0
    if volume < min_vol:
        return None
    p1, p0 = float(r.get("p1_ratio") or 0.58), float(r.get("p0_ratio") or 0.65)
    n1, n0 = int(r.get("p1_min_large_sells") or 3), int(r.get("p0_min_large_sells") or 6)
    severity = None
    if ratio >= p0 and n_big >= n0:
        severity = "P0"
    elif ratio >= p1 and n_big >= n1:
        severity = "P1"
    if not severity:
        return None
    aligned = None
    if price_direction != "unknown" and r.get("require_price_direction", True) is not False:
        aligned = price_direction == "down"
    note = "5 分钟主动卖占比 %.1f%%（阈值 %.0f%%），大额卖单 %d 笔；价格 24h 方向 %s" % (
        ratio * 100.0, p1 * 100.0, n_big, price_direction,
    )
    return _candidate(
        "taker_sell_pressure", "asset", severity, asset=asset, value=ratio,
        threshold={"p1_ratio": p1, "p0_ratio": p0, "p1_large": n1, "p0_large": n0},
        direction="down", aligned=aligned, note=note,
        extra={
            "sell_quote_usd": stats.get("sell_quote_usd"),
            "quote_volume_usd": volume,
            "large_sell_count": n_big,
            "large_sell_usd": stats.get("large_sell_usd"),
            "window_change_pct": stats.get("window_change_pct"),
        },
    )


def eval_cex_flow(flows, rules, price_direction):
    """交易所储备净流：全币种聚合 USD，只能作"资金撤离"背景，不能指认币种/主体。"""
    r = (rules or {}).get("cex_flow") or {}
    bps = flows.get("net_24h_bps")
    if bps is None or not flows.get("n_eligible"):
        return None
    p1 = float(r.get("p1_net_24h_bps_tvl") or 50)
    p0 = float(r.get("p0_net_24h_bps_tvl") or 150)
    w1 = float(r.get("p1_net_1w_bps_tvl") or 400)
    severity = None
    if bps <= -p0:
        severity = "P0"
    elif bps <= -p1:
        severity = "P1"
    elif (flows.get("net_1w_bps") or 0) <= -w1:
        severity = "P1"
    if not severity:
        return None
    aligned = None
    if price_direction != "unknown" and r.get("require_price_direction", True) is not False:
        aligned = price_direction == "down"
    note = "关注交易所 24h 储备净流 %+.1fbps TVL（约 %+.2f 亿美元，全币种聚合）；价格 24h 方向 %s" % (
        bps, (flows.get("net_24h_usd") or 0) / 1e8, price_direction,
    )
    return _candidate(
        "cex_reserve_outflow", "cex", severity, direction="down", aligned=aligned, note=note,
        value=bps, threshold={"p1_bps": p1, "p0_bps": p0, "p1_1w_bps": w1},
        extra={
            "net_24h_usd": flows.get("net_24h_usd"),
            "net_1w_usd": flows.get("net_1w_usd"),
            "tvl_usd": flows.get("tvl_usd"),
            "exchanges": [e.get("name") for e in (flows.get("exchanges") or []) if e.get("eligible")],
        },
    )


def eval_liquidation(asset, liq, rules, price_direction):
    """清算：哪边被爆 + 金额；OKX 单源口径，覆盖不足则不出候选。"""
    r = (rules or {}).get("liquidations") or {}
    coverage = liq.get("coverage_minutes")
    # coverage=None 表示样本 < 2 条，无法判断是否为"瀑布"，按样本不足处理
    if coverage is None or coverage < float(r.get("min_coverage_minutes") or 5):
        return None
    if not liq.get("usd_known"):
        return None
    total = liq.get("total_usd") or 0.0
    p1 = float(((r.get("p1_usd") or {}).get(asset)) or 0)
    p0 = float(((r.get("p0_usd") or {}).get(asset)) or 0)
    if not p1:
        return None
    severity = None
    if total >= p0 and p0 > 0:
        severity = "P0"
    elif total >= p1:
        severity = "P1"
    if not severity:
        return None
    long_ratio = liq.get("long_ratio")
    bias = float(r.get("long_bias_ratio") or 0.6)
    if long_ratio is not None and long_ratio >= bias:
        category = "多头被强平（被动卖盘）"
        implied = "down"
    elif long_ratio is not None:
        category = "空头被强平（被动买盘）"
        implied = "up"
    else:
        category = "多空混合强平"
        implied = "unknown"
    aligned = None
    if price_direction != "unknown" and implied != "unknown":
        aligned = price_direction == implied
    note = "OKX 15 分钟清算 %s：总额约 %.0f 万美元，其中%s；价格 24h 方向 %s" % (
        asset, total / 1e4, category, price_direction,
    )
    return _candidate(
        "liquidation_cascade", "asset", severity, asset=asset, direction=implied,
        aligned=aligned, note=note, value=total,
        threshold={"p1_usd": p1, "p0_usd": p0, "long_bias_ratio": bias},
        extra={
            "long_usd": liq.get("long_usd"), "short_usd": liq.get("short_usd"),
            "long_ratio": long_ratio, "coverage_minutes": coverage,
            "category": category, "venue": "OKX",
        },
    )


def eval_stablecoin(stable, rules, btc_change_pct):
    """稳定币净增发：24h 净赎回（负值）说明增量资金在撤。"""
    r = (rules or {}).get("stablecoin") or {}
    net = stable.get("net_issue_24h_usd")
    if net is None or not stable.get("n"):
        return None
    p1 = float(r.get("p1_net_issue_24h_usd") or -500000000)
    p0 = float(r.get("p0_net_issue_24h_usd") or -1500000000)
    btc_drop = float(r.get("p0_requires_btc_drop_pct") or 3.0)
    if net > p1:  # 净增发（正）或未达阈值
        return None
    severity = "P1"
    btc = None
    try:
        btc = float(btc_change_pct)
    except (TypeError, ValueError):
        btc = None
    if net <= p0 and btc is not None and btc <= -btc_drop:
        severity = "P0"
    direction = direction_of(btc)
    aligned = None if direction == "unknown" else direction == "down"
    note = "关注稳定币 24h 净赎回 %.1f 亿美元（%s）；BTC 24h %+.2f%%" % (
        -net / 1e8, "、".join(s.get("symbol") or "" for s in (stable.get("symbols") or [])),
        btc if btc is not None else 0.0,
    )
    return _candidate(
        "stablecoin_outflow", "market", severity, direction="down", aligned=aligned,
        value=net, threshold={"p1_net_issue_24h_usd": p1, "p0_net_issue_24h_usd": p0},
        note=note,
        extra={
            "circulating_usd": stable.get("circulating_usd"),
            "net_issue_1w_usd": stable.get("net_issue_1w_usd"),
            "symbols": stable.get("symbols"),
        },
    )


def build_candidates(asset_metrics, cex_flows, stablecoins, rules, directions):
    """汇总所有候选信号。asset_metrics: {asset: {"taker_5m":..., "liq_15m":...}}"""
    out = []
    btc_dir = (directions or {}).get("BTC", "unknown")
    for asset, metrics in (asset_metrics or {}).items():
        direction = (directions or {}).get(asset, "unknown")
        c = eval_taker(asset, metrics.get("taker_5m") or {}, rules, direction)
        if c:
            out.append(c)
        c = eval_liquidation(asset, metrics.get("liq_15m") or {}, rules, direction)
        if c:
            out.append(c)
    c = eval_cex_flow(cex_flows or {}, rules, btc_dir)
    if c:
        out.append(c)
    c = eval_stablecoin(stablecoins or {}, rules, (directions or {}).get("BTC"))
    if c:
        out.append(c)
    out.sort(key=lambda x: (SEVERITY_ORDER.get(x.get("severity"), 9), x.get("signal") or ""))
    return out
