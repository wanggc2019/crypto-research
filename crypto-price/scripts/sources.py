#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sources.py —— crypto-price 的行情数据层（多源取价 + 降级 + fixtures 回放）。

对外契约见 docs/crypto-price/design.md §5::

    fetch_snapshot(watchlist, app_cfg, fixture_dir=None, now_ts=None) -> dict

设计原则：
- 三源（Binance / OKX / Coinbase）各自独立抓取，任何单源失败只进 ``errors``，绝不抛出；
- 交易所原始响应统一交给 ``crypto_core.market.*`` 解析（价格 float / 时间 epoch 秒 / K 线旧→新）；
- ``fixture_dir`` 非空时**完全不联网**，按 ``<fixture>/<source>/<kind>_<ASSET>.json`` 回放原始响应；
  此时若未显式给 ``now_ts``，快照时间取 fixture 数据内最大时间戳，保证离线回放可重复；
- 返回结构是 price/snapshot/v1 的行情部分，供 indicators / rules / delivery 消费。
"""
import datetime
import os
import time
from concurrent.futures import ThreadPoolExecutor

import common  # noqa: F401 - 由它把仓库内 libs/crypto_core/src 加进 sys.path
from crypto_core import http
from crypto_core.market import binance, coinbase, okx

# K 线默认抓取根数：volume_spike 需要 5+55 根已完成 1m K 线，留约一倍余量。
KLINE_LIMIT = 120
# 三源价差超过该值（bps）时 agree=False（与 rules.yaml consensus.max_spread_bps_p1 默认值一致）。
MAX_SPREAD_BPS = 50.0
HTTP_TIMEOUT = 6      # 单源默认超时（秒）；可在 config/app.yaml 按源覆盖 timeout_seconds
HTTP_RETRIES = 0      # 不重试：一个源挂掉时不等待，下一轮（60s 后）再自动重试

SOURCE_ORDER = ("binance", "okx", "coinbase")
DEFAULT_BASES = {
    "binance_spot": "https://api.binance.com",
    "binance_futures": "https://fapi.binance.com",
    "okx": "https://www.okx.com",
    "coinbase": "https://api.exchange.coinbase.com",
}


# ---------------------------------------------------------------- 配置/小工具
def _as_dict(value):
    return value if isinstance(value, dict) else {}


def _source_cfg(app_cfg, name):
    """取 app_cfg.sources.<name>；缺省返回 {}（按默认端点、默认启用）。"""
    return _as_dict(_as_dict(_as_dict(app_cfg).get("sources")).get(name))


def _source_enabled(app_cfg, name):
    return bool(_source_cfg(app_cfg, name).get("enabled", True))


DEFAULT_PRIMARY = ("binance", "okx", "coinbase")


def _source_tier(app_cfg, name):
    """数据源等级：primary（主源，top 大所） / secondary（对比校验）。"""
    tier = _source_cfg(app_cfg, name).get("tier")
    if tier in ("primary", "secondary"):
        return tier
    return "primary" if name in DEFAULT_PRIMARY else "secondary"


def _base(cfg, key, default):
    return str(cfg.get(key) or default).rstrip("/")


def _assets(watchlist):
    """把 watchlist.assets 归一化成 [(asset, asset_cfg), ...]（保持配置顺序）。"""
    assets = _as_dict(_as_dict(watchlist).get("assets"))
    return [(str(symbol), cfg) for symbol, cfg in assets.items() if isinstance(cfg, dict)]


def _symbol(asset_cfg, source):
    return str(asset_cfg.get(source) or "").strip()


def _ts_iso(ts):
    return datetime.datetime.fromtimestamp(int(ts)).isoformat(timespec="seconds")


def _float_or_none(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _median(values):
    values = sorted(values)
    n = len(values)
    if n == 0:
        return None
    mid = n // 2
    if n % 2:
        return float(values[mid])
    return (float(values[mid - 1]) + float(values[mid])) / 2.0


def _effective_ts(now_ts, fixture_dir, records):
    """快照时间：显式 now_ts 优先；fixture 回放缺省用数据内最大时间戳（保证确定性）。"""
    if now_ts is not None:
        return int(now_ts)
    if not fixture_dir:
        return int(time.time())
    candidates = []
    for rec in records:
        for key in ("binance", "okx", "coinbase", "funding", "oi"):
            parsed = rec.get(key)
            if isinstance(parsed, dict) and parsed.get("ts") is not None:
                candidates.append(int(parsed["ts"]))
        rows = rec["candles"]["rows"]
        if rows:
            candidates.append(int(rows[-1]["close_time"]))
    return max(candidates) if candidates else int(time.time())


# ---------------------------------------------------------------- 抓取
class _Fetcher:
    """统一抓取入口：fixture 模式读原始 JSON 文件，联网模式走 crypto_core.http。

    所有失败都只记录到 ``errors``（不抛出），保证单源失败降级可继续。
    """

    def __init__(self, fixture_dir=None, timeouts=None):
        self.fixture_dir = fixture_dir
        self.errors = []
        self.timeouts = timeouts or {}
        self.failed_sources = set()   # 本轮已失败的源：后续同源请求直接跳过，不再等超时

    def raw(self, source, kind, asset, url, optional=False):
        """返回原始响应（bytes / dict / list）；失败返回 None 并记录 error。

        optional=True 用于「有则更好、没有也能降级」的补充请求（如 Coinbase ticker 时间戳）：
        失败不把该源标记为不可用，也不污染 errors。
        """
        if self.fixture_dir:
            return self._read_fixture(source, kind, asset, optional=optional)
        if source in self.failed_sources:
            return None  # 本轮该源已经失败过：直接跳过，不重复等超时
        try:
            timeout = float(self.timeouts.get(source, HTTP_TIMEOUT) or HTTP_TIMEOUT)
            return http.get_json(url, timeout=timeout, retries=HTTP_RETRIES)
        except Exception as e:  # noqa: BLE001 - 单源失败不能拖垮整轮
            if not optional:
                self.failed_sources.add(source)
                self.error(source, asset, "%s: %s" % (type(e).__name__, str(e)[:160]))
            return None

    def error(self, source, asset, message):
        self.errors.append({"source": source, "asset": asset, "error": str(message)})

    def _read_fixture(self, source, kind, asset, optional=False):
        name = "%s_%s.json" % (kind, asset)
        path = os.path.join(self.fixture_dir, source, name)
        try:
            with open(path, "rb") as f:
                return f.read()
        except OSError:
            if not optional:
                self.error(source, asset, "missing_fixture:%s/%s" % (source, name))
            return None


# ---------------------------------------------------------------- 各源抓取
def _binance_ticker(fetcher, asset, symbol, cfg):
    base = _base(cfg, "spot_base", DEFAULT_BASES["binance_spot"])
    raw = fetcher.raw("binance", "ticker24h", asset, base + "/api/v3/ticker/24hr?symbol=" + symbol)
    if raw is None:
        return None
    rows = binance.parse_tickers24h(raw)
    if not rows:
        fetcher.error("binance", asset, "parse_failed")
        return None
    return rows[0]


def _binance_klines(fetcher, asset, symbol, cfg):
    base = _base(cfg, "spot_base", DEFAULT_BASES["binance_spot"])
    url = base + "/api/v3/klines?symbol=" + symbol + "&interval=1m&limit=" + str(KLINE_LIMIT)
    raw = fetcher.raw("binance", "klines", asset, url)
    if raw is None:
        return None
    rows = binance.parse_klines(raw)
    if not rows:
        fetcher.error("binance", asset, "parse_failed")
        return None
    return rows


def _okx_ticker(fetcher, asset, symbol, cfg):
    base = _base(cfg, "base", DEFAULT_BASES["okx"])
    raw = fetcher.raw("okx", "ticker", asset, base + "/api/v5/market/ticker?instId=" + symbol)
    if raw is None:
        return None
    parsed = okx.parse_ticker(raw)
    if parsed is None:
        fetcher.error("okx", asset, "parse_failed")
    return parsed


def _okx_candles(fetcher, asset, symbol, cfg):
    base = _base(cfg, "base", DEFAULT_BASES["okx"])
    url = base + "/api/v5/market/candles?instId=" + symbol + "&bar=1m&limit=" + str(KLINE_LIMIT)
    raw = fetcher.raw("okx", "candles", asset, url)
    if raw is None:
        return None
    rows = okx.parse_candles(raw)
    if not rows:
        fetcher.error("okx", asset, "parse_failed")
        return None
    return rows


def _coinbase_stats(fetcher, asset, symbol, cfg):
    base = _base(cfg, "base", DEFAULT_BASES["coinbase"])
    raw = fetcher.raw("coinbase", "stats", asset, base + "/products/" + symbol + "/stats")
    if raw is None:
        return None
    parsed = coinbase.parse_stats(raw, symbol=asset)
    if parsed is None:
        fetcher.error("coinbase", asset, "parse_failed")
        return None
    # Coinbase /stats 不带时间戳；额外取 /ticker（带 time）判断报价是否滞后。
    ticker_raw = fetcher.raw("coinbase", "ticker", asset,
                             base + "/products/" + symbol + "/ticker", optional=True)
    ticker = coinbase.parse_ticker(ticker_raw, symbol=asset) if ticker_raw is not None else None
    if ticker:
        parsed["last"] = ticker["last"]
        parsed["ts"] = ticker.get("ts")
        parsed["bid"] = ticker.get("bid")
        parsed["ask"] = ticker.get("ask")
        if parsed.get("open"):
            parsed["change_pct"] = (ticker["last"] - parsed["open"]) / parsed["open"] * 100.0
    return parsed


def _binance_funding(fetcher, asset, symbol, cfg):
    base = _base(cfg, "futures_base", DEFAULT_BASES["binance_futures"])
    raw = fetcher.raw("binance", "premiumIndex", asset, base + "/fapi/v1/premiumIndex?symbol=" + symbol)
    if raw is None:
        return None
    parsed = binance.parse_funding(raw)
    if parsed is None:
        fetcher.error("binance", asset, "parse_failed")
    return parsed


def _binance_open_interest(fetcher, asset, symbol, cfg):
    base = _base(cfg, "futures_base", DEFAULT_BASES["binance_futures"])
    raw = fetcher.raw("binance", "openInterest", asset, base + "/fapi/v1/openInterest?symbol=" + symbol)
    if raw is None:
        return None
    parsed = binance.parse_open_interest(raw)
    if parsed is None:
        fetcher.error("binance", asset, "parse_failed")
    return parsed


def _collect_candles(fetcher, asset, asset_cfg, enabled, cfgs):
    """K 线：Binance 主源 → OKX 回退（Coinbase 无公开 K 线，契约未要求）。"""
    errors = []
    for source in ("binance", "okx"):
        if source not in enabled:
            continue
        symbol = _symbol(asset_cfg, source)
        if not symbol:
            fetcher.error(source, asset, "missing_symbol")
            errors.append({"source": source, "error": "missing_symbol"})
            continue
        if source == "binance":
            rows = _binance_klines(fetcher, asset, symbol, cfgs["binance"])
        else:
            rows = _okx_candles(fetcher, asset, symbol, cfgs["okx"])
        if rows:
            return {"source": source, "rows": rows, "errors": errors}
        errors.append({"source": source, "error": "fetch_failed"})
    return {"source": None, "rows": [], "errors": errors}


def _fetch_asset(fetcher, asset, asset_cfg, enabled, cfgs, deriv_flags):
    """抓取单个资产的全部行情（失败只记录 error，返回解析结果或 None）。"""
    rec = {"binance": None, "okx": None, "coinbase": None, "funding": None, "oi": None}

    if "binance" in enabled:
        symbol = _symbol(asset_cfg, "binance")
        if symbol:
            rec["binance"] = _binance_ticker(fetcher, asset, symbol, cfgs["binance"])
        else:
            fetcher.error("binance", asset, "missing_symbol")
    if "okx" in enabled:
        symbol = _symbol(asset_cfg, "okx")
        if symbol:
            rec["okx"] = _okx_ticker(fetcher, asset, symbol, cfgs["okx"])
        else:
            fetcher.error("okx", asset, "missing_symbol")
    if "coinbase" in enabled:
        symbol = _symbol(asset_cfg, "coinbase")
        if symbol:
            rec["coinbase"] = _coinbase_stats(fetcher, asset, symbol, cfgs["coinbase"])
        else:
            fetcher.error("coinbase", asset, "missing_symbol")

    rec["candles"] = _collect_candles(fetcher, asset, asset_cfg, enabled, cfgs)

    binance_symbol = _symbol(asset_cfg, "binance")
    deriv_enabled, want_funding, want_oi = deriv_flags
    if deriv_enabled and binance_symbol:
        if want_funding:
            rec["funding"] = _binance_funding(fetcher, asset, binance_symbol, cfgs["binance"])
        if want_oi:
            rec["oi"] = _binance_open_interest(fetcher, asset, binance_symbol, cfgs["binance"])
    return rec


# ---------------------------------------------------------------- 结构组装
def _price_entry(parsed, fallback_ts):
    """单源报价：price 为最新价，ts 缺失时用本轮快照时间兜底。"""
    price = _float_or_none(parsed.get("last"))
    if price is None:
        return None
    return {"price": price, "ts": _int_or_none(parsed.get("ts")) or int(fallback_ts)}


def _stats_row(parsed):
    return {
        "open": _float_or_none(parsed.get("open")),
        "high": _float_or_none(parsed.get("high")),
        "low": _float_or_none(parsed.get("low")),
        "change_pct": _float_or_none(parsed.get("change_pct")),
        "volume": _float_or_none(parsed.get("volume")),
    }


def _quote(source_prices, primary_names, min_primary=2, outlier_bps=40.0, max_spread_bps=50.0):
    """主源中位数 + 价差；secondary 只保留作对比校验，不参与定价。

    - 3 个主源时：若某个源偏离中位数 > outlier_bps，且其余源价差 <= max_spread_bps，
      则剔除该离群源，用剩余源继续定价（避免一个滞后源把整轮价格规则全拦住）。
    - price：有效主源中位数；主源全挂时用 secondary 兜底展示。
    - n_sources：剔除离群源后的有效主源数。
    - excluded_sources / raw_spread_bps：被剔除的源与剔除前的原始价差。
    """
    primary_entries = {name: entry for name, entry in source_prices.items() if name in primary_names}
    secondary_entries = {name: entry for name, entry in source_prices.items() if name not in primary_names}
    primary_prices = [entry["price"] for entry in primary_entries.values()
                      if entry and entry.get("price") is not None]
    raw_spread_bps = None
    excluded_sources = []
    if len(primary_prices) >= 3:
        base_price = _median(primary_prices)
        deviations = {name: abs(entry["price"] / base_price - 1.0) * 10000.0
                      for name, entry in primary_entries.items() if entry.get("price")}
        if deviations and base_price:
            raw_spread_bps = round((max(primary_prices) - min(primary_prices)) / base_price * 10000.0, 2)
            worst_name, worst_dev = max(deviations.items(), key=lambda item: item[1])
            remaining = [price for name, entry in primary_entries.items()
                         for price in [entry.get("price")] if name != worst_name and price]
            remaining_spread = None
            if len(remaining) >= 2:
                remaining_spread = (max(remaining) - min(remaining)) / _median(remaining) * 10000.0
            if (worst_dev > outlier_bps and remaining_spread is not None
                    and remaining_spread <= max_spread_bps):
                excluded_sources.append(worst_name)
                primary_entries = {name: entry for name, entry in primary_entries.items()
                                   if name != worst_name}
                primary_prices = [entry["price"] for entry in primary_entries.values()
                                  if entry and entry.get("price") is not None]
    secondary_prices = [entry["price"] for entry in secondary_entries.values()
                        if entry and entry.get("price") is not None]
    n_sources = len(primary_prices)
    n_secondary = len(secondary_prices)
    fallback_used = False
    if primary_prices:
        price = _median(primary_prices)
    elif secondary_prices:
        price = _median(secondary_prices)  # 仅用于展示/兜底，数据质量会被判定为降级
        fallback_used = True
    else:
        return {"price": None, "sources": {}, "spread_bps": None, "n_sources": 0,
                "n_secondary": 0, "agree": False, "primary_missing": list(primary_names),
                "excluded_sources": [], "raw_spread_bps": None,
                "fallback_used": False, "tier": "primary"}
    spread_bps = None
    agree = False
    if n_sources >= 2 and price:
        spread_bps = round((max(primary_prices) - min(primary_prices)) / price * 10000.0, 2)
        agree = spread_bps <= max_spread_bps
    missing = [name for name in primary_names if name not in source_prices]
    return {
        "price": round(price, 8),
        "sources": source_prices,
        "spread_bps": spread_bps,
        "n_sources": n_sources,
        "n_secondary": n_secondary,
        "agree": agree,
        "primary_missing": missing,
        "excluded_sources": excluded_sources,
        "raw_spread_bps": raw_spread_bps,
        "fallback_used": fallback_used,
        "min_primary_sources": int(min_primary),
    }


def _stats_block(source_stats):
    """stats 主源按 binance → okx → coinbase 优先级，同时保留各源明细。"""
    primary = next(iter(source_stats))
    row = dict(source_stats[primary])
    row["source"] = primary
    row["sources"] = source_stats
    return row


# ---------------------------------------------------------------- 对外入口
def fetch_snapshot(watchlist, app_cfg, fixture_dir=None, now_ts=None):
    """抓取一轮行情快照（契约见 docs/crypto-price/design.md §5）。

    :param watchlist: watchlist.yaml 解析结果（用其中的 assets 及三所交易对）
    :param app_cfg: app.yaml 解析结果（sources 端点 / enabled，derivatives 开关）
    :param fixture_dir: 非空时完全离线，按 §9.2 命名读原始响应；缺文件记 error 继续
    :param now_ts: 快照时间（epoch 秒）；缺省取当前时间；fixture 回放缺省取数据内最大时间戳
    :return: dict（ts/ts_epoch/quotes/stats/candles/funding/oi/errors/sources_ok/sources_total）
    """
    assets = _assets(watchlist)
    enabled = [name for name in SOURCE_ORDER if _source_enabled(app_cfg, name)]
    cfgs = {
        "binance": _source_cfg(app_cfg, "binance"),
        "okx": _source_cfg(app_cfg, "okx"),
        "coinbase": _source_cfg(app_cfg, "coinbase"),
    }
    quality_cfg = _as_dict(_as_dict(app_cfg).get("quality"))
    configured_primary = quality_cfg.get("primary_sources") or list(DEFAULT_PRIMARY)
    primary_names = [name for name in SOURCE_ORDER if name in enabled and name in configured_primary]
    secondary_names = [name for name in enabled if name not in primary_names]
    min_primary = int(quality_cfg.get("min_primary_sources") or 1)
    outlier_bps = float(quality_cfg.get("outlier_bps") or 40.0)
    max_spread_bps = float(quality_cfg.get("max_spread_bps") or 50.0)
    timeouts = {
        name: (cfgs[name] or {}).get("timeout_seconds") or HTTP_TIMEOUT
        for name in SOURCE_ORDER if name in cfgs
    }
    fetcher = _Fetcher(fixture_dir, timeouts=timeouts)
    deriv_cfg = _as_dict(_as_dict(app_cfg).get("derivatives"))
    deriv_flags = (
        bool(deriv_cfg.get("enabled", True)),
        bool(deriv_cfg.get("funding", True)),
        bool(deriv_cfg.get("open_interest", True)),
    )

    # 三个资产并行抓取：单资产内部仍是顺序请求，但 Coinbase 等单源超时不会串行拖 3 倍。
    def _one(item):
        asset, asset_cfg = item
        return _fetch_asset(fetcher, asset, asset_cfg, enabled, cfgs, deriv_flags)

    with ThreadPoolExecutor(max_workers=max(1, min(4, len(assets)))) as pool:
        records = list(pool.map(_one, assets))

    ts = _effective_ts(now_ts, fixture_dir, records)
    quotes, stats, candles, funding, oi = {}, {}, {}, {}, {}
    sources_ok = 0
    primary_ok_count = 0
    primary_prices_count = {name: 0 for name in primary_names}
    for (asset, _asset_cfg), rec in zip(assets, records):
        source_prices, source_stats = {}, {}
        for name in SOURCE_ORDER:
            parsed = rec.get(name)
            if name not in enabled or not parsed:
                continue
            entry = _price_entry(parsed, ts)
            if entry is None:
                continue
            entry["tier"] = _source_tier(app_cfg, name)
            source_prices[name] = entry
            if name in primary_names:
                source_stats[name] = _stats_row(parsed)
                primary_prices_count[name] = primary_prices_count.get(name, 0) + 1
            sources_ok += 1

        quotes[asset] = _quote(source_prices, primary_names, min_primary,
                               outlier_bps=outlier_bps, max_spread_bps=max_spread_bps)
        primary_ok_count += quotes[asset]["n_sources"]
        if source_stats:
            stats[asset] = _stats_block(source_stats)
        candles[asset] = rec["candles"]
        if rec.get("funding"):
            funding[asset] = {
                "rate": rec["funding"]["rate"],
                "mark_price": _float_or_none(rec["funding"].get("mark_price")),
                "ts": _int_or_none(rec["funding"].get("ts")) or ts,
            }
        if rec.get("oi"):
            oi[asset] = {"oi": rec["oi"]["oi"], "ts": _int_or_none(rec["oi"].get("ts")) or ts}

    source_health = {}
    for name in primary_names:
        ok_assets = int(primary_prices_count.get(name, 0))
        source_health[name] = {
            "ok": ok_assets == len(assets),
            "assets": ok_assets,
            "expected": len(assets),
        }
    primary_available = [name for name in primary_names if source_health[name]["ok"]]
    primary_unavailable = [name for name in primary_names if not source_health[name]["ok"]]

    return {
        "ts": _ts_iso(ts),
        "ts_epoch": ts,
        "quotes": quotes,
        "stats": stats,
        "candles": candles,
        "funding": funding,
        "oi": oi,
        "errors": fetcher.errors,
        "sources_ok": sources_ok,
        "sources_total": len(assets) * len(enabled),
        "primary_sources": primary_names,
        "secondary_sources": secondary_names,
        "source_health": source_health,
        "primary_available": primary_available,
        "primary_unavailable": primary_unavailable,
        "primary_ok": primary_ok_count,
        "primary_total": len(assets) * len(primary_names),
        "min_primary_sources": min_primary,
    }
