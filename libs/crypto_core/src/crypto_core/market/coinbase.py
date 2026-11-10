# -*- coding: utf-8 -*-
"""Coinbase Exchange 公开行情接口的解析（免 API key）。

约定见 docs/crypto-price/design.md 的接口契约：
价格 float、时间 epoch 秒 int；解析失败返回 None，不抛异常。
"""
import datetime
import json


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


def _iso_to_s(value):
    """ISO8601（如 2026-11-08T03:40:00.123456Z）-> epoch 秒 int；失败返回 None。"""
    if not value:
        return None
    text = str(value).strip()
    try:
        dt = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return int(dt.timestamp())


def parse_stats(raw, symbol=""):
    """解析 /products/{id}/stats，返回 {symbol,last,open,high,low,volume,change_pct,ts}。

    stats 接口不返回时间戳，ts 置 None（调用方可用本轮采集时间补齐）。
    change_pct 由 last 与 24h open 换算；缺 last 视为解析失败。
    """
    d = _loads(raw)
    if not isinstance(d, dict):
        return None
    last = _float(d.get("last"))
    if last is None:
        return None
    open_ = _float(d.get("open"))
    change_pct = (last - open_) / open_ * 100.0 if open_ else None
    return {
        "symbol": symbol,
        "last": last,
        "open": open_,
        "high": _float(d.get("high")),
        "low": _float(d.get("low")),
        "volume": _float(d.get("volume")),
        "change_pct": change_pct,
        "ts": None,
    }


def parse_ticker(raw, symbol=""):
    """解析 /products/{id}/ticker，返回 {symbol,last,bid,ask,volume,ts}；失败返回 None。"""
    d = _loads(raw)
    if not isinstance(d, dict):
        return None
    last = _float(d.get("price"))
    if last is None:
        return None
    return {
        "symbol": symbol,
        "last": last,
        "bid": _float(d.get("bid")),
        "ask": _float(d.get("ask")),
        "volume": _float(d.get("volume")),
        "ts": _iso_to_s(d.get("time")),
    }
