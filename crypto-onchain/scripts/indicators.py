#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""indicators.py —— 纯指标计算（不做阈值判断、不联网、不写盘）。

输入全部是 sources.py 归一化后的结构或配置里的静态值，输出可直接落盘/进卡片。
约定：金额一律等值 USD（float），比例 0–1（float），缺失一律 None 而不是 0，
      避免"缺数据"被误读成"指标为 0"。
"""
import math

DIRECTION_EPS_PCT = 0.1  # 24h 涨跌幅绝对值 < 0.1% 视为 flat


def _f(value):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return v


def direction_of(change_pct, eps=DIRECTION_EPS_PCT):
    """24h 涨跌幅 → up / down / flat / unknown。"""
    v = _f(change_pct)
    if v is None:
        return "unknown"
    if v > eps:
        return "up"
    if v < -eps:
        return "down"
    return "flat"


def oi_change_pct(points, now_ts, window_minutes=15, tolerance_minutes=10):
    """从跨轮 OI 历史 [[ts, oi], ...] 计算 ``window_minutes`` 前的 OI 变化百分比。

    - 取目标时刻（now - window）之前最近的一个历史点；与目标的距离超过
      ``tolerance_minutes`` 则视为历史不足，返回 None（不硬编一个假变化）；
    - 当前 OI 取列表中 ts 最大的点；样本 < 2 返回 None；
    - 返回百分比（-3.5 表示 15 分钟 OI 下降 3.5%）。
    """
    rows = []
    for item in points or []:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            ts, oi = _f(item[0]), _f(item[1])
        elif isinstance(item, dict):
            ts, oi = _f(item.get("ts")), _f(item.get("oi"))
        else:
            continue
        if ts is None or oi is None or oi <= 0:
            continue
        rows.append((int(ts), oi))
    if len(rows) < 2:
        return None
    rows.sort(key=lambda r: r[0])
    target = int(now_ts) - int(window_minutes) * 60
    tolerance = int(tolerance_minutes) * 60
    past = None
    for ts, oi in rows:
        if ts <= target:
            past = (ts, oi)
        else:
            break
    if past is None or target - past[0] > tolerance:
        return None
    current_ts, current_oi = rows[-1]
    if current_ts < target:
        return None  # 当前点比目标还早：时间轴不对
    return (current_oi - past[1]) / past[1] * 100.0


def taker_stats(trades, now_ts, window_seconds, large_trade_usd=0.0):
    """逐笔主动买卖统计（窗口内）。

    返回字段：
      n_trades / quote_volume_usd / sell_quote_usd / buy_quote_usd / sell_ratio /
      large_sell_count / large_sell_usd / large_buy_count /
      price_first / price_last / window_change_pct / coverage_seconds /
      first_ts / last_ts
    sell_ratio = 主动卖成交额 / 总成交额；样本为空时全为 None/0（quote_volume_usd 为 0）。
    """
    now_ts = int(now_ts)
    cutoff = now_ts - int(window_seconds)
    rows = [t for t in (trades or []) if isinstance(t, dict) and _f(t.get("ts")) is not None
            and cutoff <= int(t["ts"]) <= now_ts + 120]
    rows.sort(key=lambda t: int(t["ts"]))
    quote = sum(_f(t.get("quote_qty")) or 0.0 for t in rows)
    sell = sum((_f(t.get("quote_qty")) or 0.0) for t in rows if t.get("taker_side") == "sell")
    buy = sum((_f(t.get("quote_qty")) or 0.0) for t in rows if t.get("taker_side") == "buy")
    big = max(_f(large_trade_usd) or 0.0, 0.0)
    large_sells = [t for t in rows if t.get("taker_side") == "sell" and (_f(t.get("quote_qty")) or 0) >= big]
    large_buys = [t for t in rows if t.get("taker_side") == "buy" and (_f(t.get("quote_qty")) or 0) >= big]
    price_first = _f(rows[0].get("price")) if rows else None
    price_last = _f(rows[-1].get("price")) if rows else None
    change_pct = None
    if price_first and price_last and price_first > 0:
        change_pct = (price_last - price_first) / price_first * 100.0
    first_ts = int(rows[0]["ts"]) if rows else None
    last_ts = int(rows[-1]["ts"]) if rows else None
    return {
        "window_seconds": int(window_seconds),
        "n_trades": len(rows),
        "quote_volume_usd": quote,
        "sell_quote_usd": sell,
        "buy_quote_usd": buy,
        "sell_ratio": (sell / quote) if quote > 0 else None,
        "large_sell_count": len(large_sells),
        "large_sell_usd": sum(_f(t.get("quote_qty")) or 0.0 for t in large_sells),
        "large_buy_count": len(large_buys),
        "price_first": price_first,
        "price_last": price_last,
        "window_change_pct": change_pct,
        "coverage_seconds": (last_ts - first_ts) if (first_ts is not None and last_ts is not None) else 0,
        "first_ts": first_ts,
        "last_ts": last_ts,
    }


def cex_flow_metrics(rows, watch_names, min_tvl_usd=0):
    """交易所储备净流（DefiLlama /cexs，全币种聚合 USD）。

    返回 {"exchanges": [...], "n", "tvl_usd", "net_24h_usd", "net_24h_bps,
          "net_1w_usd", "net_1w_bps", "net_1m_usd", "eligible_tvl_usd"}
    bps = 净流 / 该交易所 TVL × 10000；TVL 缺失或 < min_tvl_usd 的交易所不计入合计。
    """
    names = {str(n).strip().lower() for n in (watch_names or []) if str(n).strip()}
    min_tvl = _f(min_tvl_usd) or 0.0
    exchanges = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "")
        if names and name.lower() not in names:
            continue
        tvl = _f(row.get("tvl"))
        net24 = _f(row.get("inflows_24h"))
        net1w = _f(row.get("inflows_1w"))
        net1m = _f(row.get("inflows_1m"))
        bps24 = (net24 / tvl * 10000.0) if (tvl and net24 is not None and tvl > 0) else None
        bps1w = (net1w / tvl * 10000.0) if (tvl and net1w is not None and tvl > 0) else None
        exchanges.append({
            "name": name,
            "slug": row.get("slug"),
            "tvl_usd": tvl,
            "net_24h_usd": net24,
            "net_24h_bps": bps24,
            "net_1w_usd": net1w,
            "net_1w_bps": bps1w,
            "net_1m_usd": net1m,
            "eligible": bool(tvl and tvl >= min_tvl),
        })
    elig = [e for e in exchanges if e["eligible"]]
    tvl_sum = sum(e["tvl_usd"] or 0.0 for e in elig)
    net24_sum = sum(e["net_24h_usd"] or 0.0 for e in elig)
    net1w_sum = sum(e["net_1w_usd"] or 0.0 for e in elig)
    net1m_sum = sum(e["net_1m_usd"] or 0.0 for e in elig)
    return {
        "exchanges": exchanges,
        "n": len(exchanges),
        "n_eligible": len(elig),
        "tvl_usd": tvl_sum,
        "net_24h_usd": net24_sum,
        "net_24h_bps": (net24_sum / tvl_sum * 10000.0) if tvl_sum > 0 else None,
        "net_1w_usd": net1w_sum,
        "net_1w_bps": (net1w_sum / tvl_sum * 10000.0) if tvl_sum > 0 else None,
        "net_1m_usd": net1m_sum,
    }


def stablecoin_metrics(rows, price_snapshot=None):
    """稳定币净增发（circulating - circulatingPrevDay）。

    返回 {"n", "circulating_usd", "net_issue_24h_usd", "net_issue_1w_usd",
          "symbols": [{symbol, circulating, net_24h, net_1w}]}
    """
    symbols = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        symbols.append({
            "symbol": row.get("symbol"),
            "circulating": _f(row.get("circulating")),
            "net_24h": _f(row.get("net_issue_24h")),
            "net_1w": _f(row.get("net_issue_1w")),
        })
    circ = sum(s["circulating"] or 0.0 for s in symbols)
    net24 = sum(s["net_24h"] or 0.0 for s in symbols if s["net_24h"] is not None)
    net1w = sum(s["net_1w"] or 0.0 for s in symbols if s["net_1w"] is not None)
    return {
        "n": len(symbols),
        "circulating_usd": circ,
        "net_issue_24h_usd": net24,
        "net_issue_1w_usd": net1w,
        "symbols": symbols,
    }


def liquidation_metrics(events, contract_value, price, now_ts, window_minutes=15):
    """清算统计（仅 OKX 口径）。

    events 来自 market.okx.parse_liquidations（size=合约张数），
    USD = size × contract_value（每张面值，币本位）× price。
    long_usd = posSide=long 被爆（被动卖出）；short_usd = posSide=short 被爆（被动买入）。
    返回 coverage_minutes（返回样本自身覆盖时长，用于防止拿 30 秒样本下结论）。
    """
    cv = _f(contract_value)
    px = _f(price)
    rows = [e for e in (events or []) if isinstance(e, dict) and _f(e.get("ts")) is not None]
    rows.sort(key=lambda e: int(e["ts"]))
    coverage_minutes = None
    if len(rows) >= 2:
        coverage_minutes = max(0.0, (int(rows[-1]["ts"]) - int(rows[0]["ts"])) / 60.0)
    cutoff = int(now_ts) - int(window_minutes) * 60
    window_rows = [e for e in rows if int(e["ts"]) >= cutoff]
    long_usd = short_usd = 0.0
    usd_known = bool(cv and px)
    for e in window_rows:
        notional = None
        if usd_known:
            notional = (_f(e.get("size")) or 0.0) * cv * px
        side = str(e.get("pos_side") or "")
        if side == "long":
            long_usd += notional or 0.0
        elif side == "short":
            short_usd += notional or 0.0
    total = long_usd + short_usd
    return {
        "window_minutes": int(window_minutes),
        "n": len(window_rows),
        "n_total": len(rows),
        "total_usd": total,
        "long_usd": long_usd,
        "short_usd": short_usd,
        "long_ratio": (long_usd / total) if total > 0 else None,
        "coverage_minutes": coverage_minutes,
        "usd_known": usd_known,
        "first_ts": int(rows[0]["ts"]) if rows else None,
        "last_ts": int(rows[-1]["ts"]) if rows else None,
    }
