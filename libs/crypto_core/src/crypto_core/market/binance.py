# -*- coding: utf-8 -*-
"""Binance 公开行情接口的解析（免 API key）。

约定（见 docs/crypto-price/design.md 的接口契约）：
- 价格一律 float，时间一律 epoch 秒 int（接口返回的毫秒在这里换算）；
- K 线一律按时间旧→新排序；
- 解析失败返回 None（单条）或 []（列表），不向上抛异常。
"""
import json

# 24h ticker 字段映射：输出名 -> Binance 原始字段名（字段顺序即输出顺序）。
_TICKER_FIELDS = (
    ("last", "lastPrice"),
    ("open", "openPrice"),
    ("high", "highPrice"),
    ("low", "lowPrice"),
    ("volume", "volume"),
    ("quote_volume", "quoteVolume"),
    ("change_pct", "priceChangePercent"),
)


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


def _int(value):
    """转 int；None/非法字符串返回 None。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _ms_to_s(value):
    """毫秒时间戳 -> epoch 秒 int；非法/缺失返回 None。"""
    f = _float(value)
    if f is None or f < 0:
        return None
    return int(f) // 1000


def parse_tickers24h(raw):
    """解析 /api/v3/ticker/24hr，返回 list[dict]。

    带 symbol 查询时接口返回单个 dict，这里统一包成 list（无 symbol 参数时本就是 list）。
    输出字段：symbol,last,open,high,low,volume,quote_volume,change_pct,ts。
    """
    data = _loads(raw)
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return []
    out = []
    for item in data:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "")
        row = {"symbol": symbol}
        for key, src in _TICKER_FIELDS:
            row[key] = _float(item.get(src))
        row["ts"] = _ms_to_s(item.get("closeTime"))
        if not symbol or row["last"] is None:
            continue
        out.append(row)
    return out


def parse_klines(raw):
    """解析 /api/v3/klines，返回 list[dict]，按 open_time 旧→新排序。

    输出字段：open_time,open,high,low,close,volume,close_time（均为秒）。
    单根 K 线格式：[openTime, open, high, low, close, volume, closeTime, ...]。
    """
    data = _loads(raw)
    if not isinstance(data, list):
        return []
    out = []
    for item in data:
        if not isinstance(item, (list, tuple)) or len(item) < 7:
            continue
        open_time = _ms_to_s(item[0])
        close_time = _ms_to_s(item[6])
        open_, high, low, close, volume = (
            _float(item[1]),
            _float(item[2]),
            _float(item[3]),
            _float(item[4]),
            _float(item[5]),
        )
        if open_time is None or close_time is None:
            continue
        if None in (open_, high, low, close, volume):
            continue
        out.append(
            {
                "open_time": open_time,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
                "close_time": close_time,
            }
        )
    out.sort(key=lambda r: r["open_time"])
    return out


def parse_open_interest(raw):
    """解析 /fapi/v1/openInterest，返回 {symbol, oi, ts}；解析失败返回 None。"""
    data = _loads(raw)
    if not isinstance(data, dict):
        return None
    oi = _float(data.get("openInterest"))
    if oi is None:
        return None
    return {"symbol": str(data.get("symbol") or ""), "oi": oi, "ts": _ms_to_s(data.get("time"))}


def parse_open_interest_hist(raw):
    """解析 /futures/data/openInterestHist，返回 [{symbol, oi, ts}]（旧→新排序）。

    该端点免 key，官方给 5m/15m/1h 等周期的 OI 历史；解析失败返回 []。
    """
    data = _loads(raw)
    if not isinstance(data, list):
        return []
    out = []
    for row in data:
        if not isinstance(row, dict):
            continue
        oi = _float(row.get("sumOpenInterest"))
        ts = _ms_to_s(row.get("timestamp"))
        if oi is None or ts is None or oi <= 0:
            continue
        out.append({"symbol": str(row.get("symbol") or ""), "oi": oi, "ts": ts})
    out.sort(key=lambda r: r["ts"])
    return out


def parse_funding(raw):
    """解析 fapi premiumIndex / fundingRate，返回 {symbol, rate, mark_price, ts}。

    rate 为小数（0.0001 = 0.01%）；兼容旧字段（只增 ts）；解析失败返回 None。
    """
    data = _loads(raw)
    if not isinstance(data, dict):
        return None
    rate = _float(data.get("lastFundingRate"))
    if rate is None:
        return None
    return {
        "symbol": data.get("symbol", ""),
        "rate": rate,
        "mark_price": data.get("markPrice"),
        "ts": _ms_to_s(data.get("time")),
    }


def parse_agg_trades(raw):
    """解析 /api/v3/aggTrades 或 /fapi/v1/aggTrades，返回 list[dict]。

    输入为逐笔成交 list，字段 a=成交 ID、p=价格、q=数量、T=毫秒时间、m=买方是否为 maker。
    输出字段：id,price,qty,quote_qty,ts,taker_side（quote_qty = price * qty）。
    taker_side：m=True 表示主动方是卖方（"sell"），为 False 表示主动方是买方（"buy"）；
    单条缺 ID/价格/数量/时间或非法则跳过，顶层非 list 返回 []。
    """
    data = _loads(raw)
    if not isinstance(data, list):
        return []
    out = []
    for item in data:
        if not isinstance(item, dict):
            continue
        trade_id = _int(item.get("a"))
        price = _float(item.get("p"))
        qty = _float(item.get("q"))
        ts = _ms_to_s(item.get("T"))
        if trade_id is None or price is None or qty is None or ts is None:
            continue
        out.append(
            {
                "id": trade_id,
                "price": price,
                "qty": qty,
                "quote_qty": price * qty,
                "ts": ts,
                "taker_side": "sell" if bool(item.get("m")) else "buy",
            }
        )
    return out
