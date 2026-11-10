# -*- coding: utf-8 -*-
"""DefiLlama 稳定币 / CEX 流量数据的解析（免 API key）。"""
import json

WATCH_STABLES = ("USDT", "USDC", "USDe", "FDUSD")


def _loads(raw):
    """把 bytes/str/dict/list 解析成 Python 对象；失败返回 None，不抛异常。"""
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
    except (TypeError, ValueError, UnicodeDecodeError):
        return None


def _float(value):
    """转 float；None/非法字符串返回 None。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pegged_usd(value):
    """从 {peggedUSD: ...} 结构取市值；缺失/非法返回 None。"""
    if not isinstance(value, dict):
        return None
    return _float(value.get("peggedUSD"))


def _diff(current, previous):
    """两数之差；任一侧缺失（None）返回 None。"""
    if current is None or previous is None:
        return None
    return current - previous


def parse_stablecoins(raw):
    """解析 stablecoins.llama.fi/stablecoins，返回 [{symbol, price, dev_bps}]。

    dev_bps = 偏离 1.0 的基点绝对值；没有可用价格或不在关注列表里的币种会被跳过。
    """
    d = json.loads(raw.decode("utf-8")) if isinstance(raw, bytes) else json.loads(raw)
    out = []
    for a in d.get("peggedAssets", []) or []:
        sym = a.get("symbol")
        if sym not in WATCH_STABLES:
            continue
        price = a.get("price")
        if not isinstance(price, (int, float)):
            continue
        out.append({"symbol": sym, "price": price, "dev_bps": abs(price - 1.0) * 10000})
    return out


def parse_cex_flows(raw):
    """解析 api.llama.fi/cexs，返回 list[dict]。

    输出字段：name,slug,tvl,inflows_24h,inflows_1w,inflows_1m
    （tvl 取源字段 currentTvl；净流为全币种聚合 USD，正=流入交易所）。
    缺 name 的条目跳过，数值缺失为 None；解析失败返回 []。
    """
    data = _loads(raw)
    if not isinstance(data, dict):
        return []
    cexs = data.get("cexs")
    if not isinstance(cexs, list):
        return []
    out = []
    for item in cexs:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        if not name:
            continue
        out.append(
            {
                "name": name,
                "slug": str(item.get("slug") or ""),
                "tvl": _float(item.get("currentTvl")),
                "inflows_24h": _float(item.get("inflows_24h")),
                "inflows_1w": _float(item.get("inflows_1w")),
                "inflows_1m": _float(item.get("inflows_1m")),
            }
        )
    return out


def parse_stablecoin_supply(raw, symbols=WATCH_STABLES):
    """解析 stablecoins.llama.fi/stablecoins?includePrices=true，返回关注币种的市值与净增发。

    输出字段：symbol,circulating,prev_day,prev_week,net_issue_24h,net_issue_1w
    （市值取 circulating.peggedUSD 等，单位 USD）。net_issue_24h = circulating - prev_day，
    net_issue_1w = circulating - prev_week；任一侧缺失则对应净增发为 None。
    解析失败返回 []。
    """
    data = _loads(raw)
    if not isinstance(data, dict):
        return []
    assets = data.get("peggedAssets")
    if not isinstance(assets, list):
        return []
    out = []
    for item in assets:
        if not isinstance(item, dict):
            continue
        symbol = item.get("symbol")
        if symbol not in symbols:
            continue
        circulating = _pegged_usd(item.get("circulating"))
        prev_day = _pegged_usd(item.get("circulatingPrevDay"))
        prev_week = _pegged_usd(item.get("circulatingPrevWeek"))
        out.append(
            {
                "symbol": symbol,
                "circulating": circulating,
                "prev_day": prev_day,
                "prev_week": prev_week,
                "net_issue_24h": _diff(circulating, prev_day),
                "net_issue_1w": _diff(circulating, prev_week),
            }
        )
    return out
