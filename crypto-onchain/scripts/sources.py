#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sources.py —— 只负责"取数 + 交给共享库解析"，不做任何阈值判断。

原则：
- 单源失败不拖垮整轮：每个 fetch_* 返回 (data, error)，error 非空时 data 为空；
- 免费源、免 key（M1）；解析器全部在 `libs/crypto_core/market/`，模块内不复制解析逻辑；
- fixtures 模式（离线回放/单测）与联网模式返回**同一种结构**，run.py 无需分支。
"""
import json
import os

from crypto_core import http
from crypto_core.market import binance as m_binance
from crypto_core.market import defillama as m_defillama
from crypto_core.market import okx as m_okx

BINANCE_AGG_PATH = "/api/v3/aggTrades"          # 权重 4/次，limit 最大 1000（已核实）
BINANCE_OI_HIST_PATH = "/futures/data/openInterestHist"  # 官方 OI 历史（免 key，15m 周期）
OKX_LIQ_PATH = "/api/v5/public/liquidation-orders"


def _get_json(url, timeout=8, retries=0):
    """GET → (obj, error)。错误信息截断，避免日志被 URL/堆栈刷屏。"""
    try:
        raw = http.get(url, timeout=timeout, retries=retries)
        return json.loads(raw.decode("utf-8", "ignore")), None
    except Exception as e:  # noqa: BLE001 - 网络/解析/HTTP 统一降级
        return None, "%s: %s" % (type(e).__name__, str(e)[:160])


# ---------------------------------------------------------------- Binance 逐笔
def fetch_binance_trades(base, symbol, now_ts, window_seconds, limit=1000, max_pages=12, timeout=8):
    """回补最近 window_seconds 的 aggTrades（从最新一笔往回翻页，保证窗口完整）。

    返回 {"trades": [...], "pages": int, "weight": int, "truncated": bool,
          "coverage_seconds": int, "error": str|None}
    - 首页不带时间参数 → 最近 limit 笔；之后用 endTime=本批最早-1ms 向过去翻页；
    - 翻到超过窗口/空页/达到 max_pages 即停；truncated=True 表示窗口数据没取全。
    """
    base = str(base or "").rstrip("/")
    cutoff_ms = int(int(now_ts) - int(window_seconds)) * 1000
    trades, pages, weight = [], 0, 0
    next_end_ms = None
    error = None
    while pages < int(max_pages):
        url = "%s%s?symbol=%s&limit=%d" % (base, BINANCE_AGG_PATH, symbol, int(limit))
        if next_end_ms is not None:
            url += "&endTime=%d" % int(next_end_ms)
        data, err = _get_json(url, timeout=timeout)
        pages += 1
        weight += 4
        if err:
            error = err
            break
        rows = m_binance.parse_agg_trades(data)
        if not rows:
            break
        rows.sort(key=lambda t: t["ts"])
        # 只保留窗口内（含少量缓冲，防交易所时钟偏差）
        trades.extend([t for t in rows if t["ts"] * 1000 >= cutoff_ms - 1000])
        oldest_ms = rows[0]["ts"] * 1000
        if oldest_ms <= cutoff_ms:
            break
        next_end_ms = oldest_ms - 1
    # 去重（翻页边界可能重叠）并按时间升序
    uniq = {t["id"]: t for t in trades}
    trades = sorted(uniq.values(), key=lambda t: t["ts"])
    truncated = bool(pages >= int(max_pages) and trades and trades[0]["ts"] * 1000 > cutoff_ms)
    if trades:
        first_ts, last_ts = trades[0]["ts"], trades[-1]["ts"]
        coverage = max(0, last_ts - first_ts)
    else:
        first_ts = last_ts = None
        coverage = 0
    return {
        "trades": trades,
        "pages": pages,
        "weight": weight,
        "truncated": truncated,
        "coverage_seconds": coverage,
        "first_ts": first_ts,
        "last_ts": last_ts,
        "error": error,
    }


# ---------------------------------------------------------------- Binance OI 历史
def fetch_oi_history(base, symbol, period="15m", limit=3, timeout=8):
    """官方 OI 历史（免 key）→ (rows, error)；rows 为 [{symbol, oi, ts}] 旧→新。

    比本地跨轮历史更可靠：不受 Mac 休眠导致的采样断点影响。
    """
    base = str(base or "").rstrip("/")
    url = "%s%s?symbol=%s&period=%s&limit=%d" % (
        base, BINANCE_OI_HIST_PATH, symbol, period, int(limit),
    )
    data, err = _get_json(url, timeout=timeout, retries=1)
    if err:
        return [], err
    rows = m_binance.parse_open_interest_hist(data)
    return rows, None if rows else "openInterestHist 解析为空"


def load_fixture_oi_history(fixtures_dir, symbol):
    data = load_fixture(fixtures_dir, "binance_oi_hist_%s.json" % symbol)
    rows = m_binance.parse_open_interest_hist(data) if data is not None else []
    return rows, None if rows else "缺少/无法解析 fixture binance_oi_hist_%s.json" % symbol


# ---------------------------------------------------------------- DefiLlama
def fetch_cex_flows(url, timeout=12):
    """返回 (rows, error)；rows 为 market.defillama.parse_cex_flows 的输出。"""
    data, err = _get_json(url, timeout=timeout, retries=1)
    if err:
        return [], err
    rows = m_defillama.parse_cex_flows(data)
    return rows, None if rows else "cexs 解析为空"


def fetch_stablecoins(url, symbols, timeout=12):
    """返回 (rows, error)；rows 含 circulating / prev_day / net_issue_24h。"""
    data, err = _get_json(url, timeout=timeout, retries=1)
    if err:
        return [], err
    rows = m_defillama.parse_stablecoin_supply(data, symbols=symbols)
    return rows, None if rows else "stablecoins 解析为空"


# ---------------------------------------------------------------- OKX 清算
def fetch_okx_liquidations(base, uly, limit=100, timeout=8):
    """返回 (rows, error)；rows 为最近 limit 条清算（size 为合约张数，USD 换算留给指标层）。"""
    base = str(base or "").rstrip("/")
    url = "%s%s?instType=SWAP&uly=%s&state=filled&limit=%d" % (base, OKX_LIQ_PATH, uly, int(limit))
    data, err = _get_json(url, timeout=timeout, retries=1)
    if err:
        return [], err
    rows = m_okx.parse_liquidations(data)
    return rows, None if rows else "liquidation-orders 解析为空"


# ---------------------------------------------------------------- fixtures（离线）
def fixture_path(fixtures_dir, name):
    return os.path.join(str(fixtures_dir), name)


def load_fixture(fixtures_dir, name, default=None):
    path = fixture_path(fixtures_dir, name)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError, UnicodeDecodeError):
        return default


def load_fixture_trades(fixtures_dir, symbol):
    """fixtures/binance_<symbol>.json → 与 fetch_binance_trades 同结构（pages=0 表示回放）。"""
    data = load_fixture(fixtures_dir, "binance_%s.json" % symbol)
    if data is None:
        return {"trades": [], "pages": 0, "weight": 0, "truncated": False,
                "coverage_seconds": 0, "first_ts": None, "last_ts": None,
                "error": "缺少 fixture binance_%s.json" % symbol}
    rows = m_binance.parse_agg_trades(data)
    if rows:
        first_ts, last_ts = rows[0]["ts"], rows[-1]["ts"]
        coverage = max(0, last_ts - first_ts)
    else:
        first_ts = last_ts = None
        coverage = 0
    return {
        "trades": rows, "pages": 0, "weight": 0, "truncated": False,
        "coverage_seconds": coverage, "first_ts": first_ts, "last_ts": last_ts,
        "error": None if rows else "fixture 无有效成交",
    }


def load_fixture_cex_flows(fixtures_dir):
    data = load_fixture(fixtures_dir, "cexs.json")
    rows = m_defillama.parse_cex_flows(data) if data is not None else []
    return rows, None if rows else "缺少/无法解析 fixture cexs.json"


def load_fixture_stablecoins(fixtures_dir, symbols):
    data = load_fixture(fixtures_dir, "stablecoins.json")
    rows = m_defillama.parse_stablecoin_supply(data, symbols=symbols) if data is not None else []
    return rows, None if rows else "缺少/无法解析 fixture stablecoins.json"


def load_fixture_liquidations(fixtures_dir, uly):
    data = load_fixture(fixtures_dir, "okx_liq_%s.json" % uly)
    rows = m_okx.parse_liquidations(data) if data is not None else []
    return rows, None if rows else "缺少/无法解析 fixture okx_liq_%s.json" % uly
