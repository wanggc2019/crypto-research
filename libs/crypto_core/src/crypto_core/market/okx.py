# -*- coding: utf-8 -*-
"""OKX 公开行情接口的解析（免 API key）。

约定见 docs/crypto-price/design.md 的接口契约：
价格 float、时间 epoch 秒 int、K 线旧→新；解析失败返回 None / []，不抛异常。
"""
import json

# 1m K 线的默认毫秒数；只有一根 K 线时无法从相邻时间差推断周期。
_DEFAULT_BAR_MS = 60_000


def _loads(raw):
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
    except (TypeError, ValueError, UnicodeDecodeError):
        return None


def _float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ms_to_s(value):
    f = _float(value)
    if f is None or f < 0:
        return None
    return int(f) // 1000


def _ok_data(raw):
    """校验 OKX 的统一响应外壳（{"code": "0", "data": [...]}），返回 data list 或 None。"""
    d = _loads(raw)
    if not isinstance(d, dict):
        return None
    if str(d.get("code", "")) != "0":
        return None
    data = d.get("data")
    if not isinstance(data, list) or not data:
        return None
    return data


def parse_ticker(raw):
    """解析 /api/v5/market/ticker，返回 {symbol,last,open,high,low,volume,quote_volume,change_pct,ts}。

    现货场景：vol24h = 基础币成交量，volCcy24h = 计价币成交额；
    change_pct 由 last 与 open24h 换算（接口不直接给涨跌幅）。
    """
    data = _ok_data(raw)
    if not isinstance(data, list) or not isinstance(data[0], dict):
        return None
    t = data[0]
    last = _float(t.get("last"))
    if last is None:
        return None
    open_ = _float(t.get("open24h"))
    change_pct = (last - open_) / open_ * 100.0 if open_ else None
    return {
        "symbol": str(t.get("instId") or ""),
        "last": last,
        "open": open_,
        "high": _float(t.get("high24h")),
        "low": _float(t.get("low24h")),
        "volume": _float(t.get("vol24h")),
        "quote_volume": _float(t.get("volCcy24h")),
        "change_pct": change_pct,
        "ts": _ms_to_s(t.get("ts")),
    }


def _bar_ms(rows):
    """从相邻两根 K 线的时间差推断周期（ms）；推断不出时退回 1m。"""
    if len(rows) < 2:
        return _DEFAULT_BAR_MS
    ts0 = _float(rows[0][0])
    for row in rows[1:]:
        ts1 = _float(row[0]) if isinstance(row, (list, tuple)) and row else None
        if ts0 is not None and ts1 is not None and ts0 > ts1:
            return int(ts0 - ts1)
    return _DEFAULT_BAR_MS


def parse_candles(raw):
    """解析 /api/v5/market/candles，返回 list[dict]（旧→新）。

    输出字段：open_time,open,high,low,close,volume,quote_volume,close_time,confirm。
    接口每根为 [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]，且是**新→旧**；
    这里按 open_time 排序，close_time = open_time + bar - 1 秒（bar 由相邻时间差推断）。
    """
    data = _ok_data(raw)
    if not isinstance(data, list):
        return []
    bar_ms = _bar_ms(data)
    out = []
    for item in data:
        if not isinstance(item, (list, tuple)) or len(item) < 9:
            continue
        open_time = _ms_to_s(item[0])
        open_, high, low, close, volume = (
            _float(item[1]),
            _float(item[2]),
            _float(item[3]),
            _float(item[4]),
            _float(item[5]),
        )
        if open_time is None:
            continue
        if None in (open_, high, low, close, volume):
            continue
        confirm = 1 if str(item[8]).strip().lower() in ("1", "true") else 0
        out.append(
            {
                "open_time": open_time,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
                "quote_volume": _float(item[6]),
                "close_time": open_time + bar_ms // 1000 - 1,
                "confirm": confirm,
            }
        )
    out.sort(key=lambda r: r["open_time"])
    return out


def parse_liquidations(raw):
    """解析 /api/v5/public/liquidation-orders，返回 list[dict]（按 ts 升序）。

    每个 data 条目下的 details[] 扁平化成一行，输出字段：
    inst_id,uly,pos_side,side,price,size,ts（price 取破产价 bkPx，size 为合约张数，
    换算 USD 留给模块）。缺价格/张数/时间戳的明细跳过；解析失败返回 []。
    """
    data = _ok_data(raw)
    if not isinstance(data, list):
        return []
    out = []
    for item in data:
        if not isinstance(item, dict):
            continue
        inst_id = str(item.get("instId") or "")
        uly = str(item.get("uly") or "")
        details = item.get("details")
        if not isinstance(details, list):
            continue
        for detail in details:
            if not isinstance(detail, dict):
                continue
            price = _float(detail.get("bkPx"))
            size = _float(detail.get("sz"))
            ts = _ms_to_s(detail.get("ts"))
            if price is None or size is None or ts is None:
                continue
            out.append(
                {
                    "inst_id": inst_id,
                    "uly": uly,
                    "pos_side": str(detail.get("posSide") or ""),
                    "side": str(detail.get("side") or ""),
                    "price": price,
                    "size": size,
                    "ts": ts,
                }
            )
    out.sort(key=lambda r: r["ts"])
    return out


def parse_instruments(raw):
    """解析 /api/v5/public/instruments，返回 {inst_id: {ct_val, ct_val_ccy}}。

    ct_val 为合约面值（float；缺失/非法为 None），ct_val_ccy 为面值币种
    （如 USDT 本位永续的 "BTC"）。缺 instId 的条目跳过；解析失败返回 {}。
    """
    data = _ok_data(raw)
    if not isinstance(data, list):
        return {}
    out = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        inst_id = str(item.get("instId") or "")
        if not inst_id:
            continue
        out[inst_id] = {
            "ct_val": _float(item.get("ctVal")),
            "ct_val_ccy": str(item.get("ctValCcy") or ""),
        }
    return out
