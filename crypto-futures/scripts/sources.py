#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sources.py —— Binance USD-M 公开接口取数（联网 / fixtures 离线回放两种模式）。

- 联网走 crypto_core.http；fixtures 模式完全不联网，按 API 参数（startTime/endTime/limit）切片本地原始响应，
  文件格式见 backtest.py 文件头；
- 失败分两类：``skip``（限频 403/418/429、超时、断网——等下一轮，不算失败）与 ``error``（解析失败等真实错误）；
- 任何单接口失败都不抛出，记入 ``errors`` 由 run.py 汇总退出码；
- 只采用**已收盘**的 5m 周期（时间戳是周期起点；未收盘的点值还会变，且各节点返回不一致）。
"""
import gzip
import json
import os
import time
import urllib.error
import urllib.parse

import series as S

import common  # noqa: F401  (注入 libs 路径)
from crypto_core import http
from crypto_core.market import binance

PARSERS = {"G": binance.parse_long_short_ratio, "T": binance.parse_long_short_ratio,
           "K": binance.parse_taker_ratio, "OI": binance.parse_open_interest_hist}
SKIP_HTTP = {403, 418, 429, 451, 502, 503, 504}
MAX_AGE_S = 30 * 86400   # 接口只给最近 30 天


class FetchError(Exception):
    def __init__(self, kind, message, code=None):
        super().__init__(message)
        self.kind = kind      # skip / error
        self.code = code      # Binance 业务错误码（如 -1130）


def read_raw(data_dir, symbol, endpoint):
    """读 fixtures 原始响应（.json 或 .json.gz）；不存在返回 None。"""
    for name, opener in ((symbol + "_" + endpoint + ".json.gz", gzip.open),
                         (symbol + "_" + endpoint + ".json", open)):
        path = os.path.join(data_dir, name)
        if os.path.exists(path):
            with opener(path, "rt", encoding="utf-8") as f:
                return json.load(f)
    return None


def _slice(rows, params, now):
    """按 API 语义切片：有 startTime 取 [start, end] 内前 limit 条；否则取 end 之前最后 limit 条。"""
    limit = int(params.get("limit", 30))
    end_ms = int(params.get("endTime", now * 1000))
    start_ms = params.get("startTime")
    if start_ms is not None and int(start_ms) < (now - MAX_AGE_S) * 1000:
        raise FetchError("error", "parameter 'startTime' is invalid.", code=-1130)
    rows = [r for r in rows if r.get("timestamp", 0) <= end_ms]
    if start_ms is not None:
        return [r for r in rows if r["timestamp"] >= int(start_ms)][:limit]
    return rows[-limit:]


class Fetcher:
    def __init__(self, app_cfg, fixture_dir=None, now=None, sleep=time.sleep):
        self.cfg = (app_cfg or {}).get("binance") or {}
        self.base = self.cfg.get("base", "https://fapi.binance.com")
        self.fixture_dir = fixture_dir
        self.now = int(now if now is not None else time.time())
        self.sleep = sleep
        self.requests = 0
        self.errors = []
        self._fixture_cache = {}

    # ------------------------------------------------------------------ 原始请求
    def _get(self, path, params):
        self.requests += 1
        if self.fixture_dir:
            return self._fixture(path, params)
        url = self.base + path + ("?" + urllib.parse.urlencode(params) if params else "")
        try:
            return http.get_json(url, timeout=float(self.cfg.get("timeout_seconds", 8)))
        except urllib.error.HTTPError as e:
            raise _http_error(e) from None
        except ValueError as e:
            raise FetchError("error", "响应不是 JSON：%s" % e) from None
        except Exception as e:  # noqa: BLE001 - 超时/断网/DNS：等下一轮
            raise FetchError("skip", "%s: %s" % (type(e).__name__, e)) from None

    def _fixture(self, path, params):
        endpoint = path.rsplit("/", 1)[-1]
        key = (params.get("symbol", "ALL"), endpoint)   # fundingInfo 不带 symbol → ALL_fundingInfo.json
        if key not in self._fixture_cache:
            self._fixture_cache[key] = read_raw(self.fixture_dir, key[0], endpoint)
        raw = self._fixture_cache[key]
        if raw is None:
            raise FetchError("skip", "fixture 缺失：%s_%s" % key)
        if isinstance(raw, dict) or endpoint == "fundingInfo":   # 快照类接口原样返回，不按时间切片
            return raw
        return _slice(raw, params, self.now)

    def _note(self, asset, endpoint, err):
        self.errors.append({"asset": asset, "endpoint": endpoint, "kind": err.kind, "error": str(err)[:200]})

    # ------------------------------------------------------------------ 业务接口
    def recent(self, asset, symbol, metric):
        """最近 recent_limit 个 5m 点 → {ts: tuple}；失败记 errors 返回 None。"""
        params = {"symbol": symbol, "period": self.cfg.get("period", "5m"),
                  "limit": int(self.cfg.get("recent_limit", 12))}
        try:
            raw = self._get("/futures/data/" + S.ENDPOINTS[metric], params)
        except FetchError as e:
            self._note(asset, S.ENDPOINTS[metric], e)
            return None
        return self._closed(S.rows_to_points(metric, PARSERS[metric](raw)))

    def _closed(self, points):
        """只保留已收盘的 5m 周期：接口时间戳是周期起点，ts + 5m > now 的点还在变化（且各节点不一致）。"""
        return {ts: v for ts, v in points.items() if ts + S.STEP <= self.now}

    def history(self, asset, symbol, metric, start_ts, end_ts):
        """分页取 [start_ts, end_ts] 的 5m 点；遇到 -1130 原样抛给调用方决定是否缩短窗口。"""
        page = int(self.cfg.get("page_limit", 500))
        out, t = {}, int(start_ts)
        while t <= end_ts:
            stop = min(t + (page - 1) * S.STEP, int(end_ts))
            params = {"symbol": symbol, "period": self.cfg.get("period", "5m"), "limit": page,
                      "startTime": t * 1000, "endTime": stop * 1000}
            raw = self._get("/futures/data/" + S.ENDPOINTS[metric], params)
            out.update(self._closed(S.rows_to_points(metric, PARSERS[metric](raw))))
            t = stop + S.STEP
            if t <= end_ts and not self.fixture_dir:
                self.sleep(float(self.cfg.get("pause_seconds", 0.15)))
        return out

    def funding(self, asset, symbol):
        """当前资金费率（premiumIndex.lastFundingRate）；失败返回 None。"""
        try:
            raw = self._get("/fapi/v1/premiumIndex", {"symbol": symbol})
        except FetchError as e:
            self._note(asset, "premiumIndex", e)
            return None
        parsed = binance.parse_funding(raw)
        if parsed is None:
            self._note(asset, "premiumIndex", FetchError("error", "parse_failed"))
        return parsed

    def open_interest(self, asset, symbol):
        """实时持仓量（/fapi/v1/openInterest，M2 oi_spike 用）；失败返回 None。"""
        try:
            raw = self._get("/fapi/v1/openInterest", {"symbol": symbol})
        except FetchError as e:
            self._note(asset, "openInterest", e)
            return None
        parsed = binance.parse_open_interest(raw)
        if parsed is None:
            self._note(asset, "openInterest", FetchError("error", "parse_failed"))
        return parsed

    def funding_info(self):
        """各合约资金费率结算间隔 {symbol: 小时}（只列出被调整过的合约）；失败返回 None。"""
        try:
            raw = self._get("/fapi/v1/fundingInfo", {})
        except FetchError as e:
            self._note("*", "fundingInfo", e)
            return None
        parsed = binance.parse_funding_info(raw)
        if parsed is None:
            self._note("*", "fundingInfo", FetchError("error", "parse_failed"))
        return parsed


def _http_error(e):
    code = None
    try:
        body = json.loads(e.read().decode("utf-8", "ignore"))
        code = body.get("code") if isinstance(body, dict) else None
    except (OSError, ValueError):
        pass
    kind = "skip" if e.code in SKIP_HTTP else "error"
    return FetchError(kind, "HTTP %s%s" % (e.code, " code=%s" % code if code is not None else ""), code=code)
