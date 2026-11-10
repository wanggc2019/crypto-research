#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fetch_history.py —— 拉取 BTC/ETH/BNB/SOL 日线历史（Binance 公开 K 线，免 key、零成本）。

用法：
    python3 scripts/fetch_history.py                     # 默认 4 币，2019-01-01 起；有缓存则增量更新
    python3 scripts/fetch_history.py --force             # 忽略缓存全量重拉
    python3 scripts/fetch_history.py --symbols BTC,ETH   # 只拉部分

产物：data/raw/klines/<sym>_1d.json（gitignored）。
刷新策略：缓存存在时从「最后一根 bar 的日期」开始重拉（当天未收盘的 bar 会被最新数据覆盖，
之后的新 bar 追加），避免日报/周报一直复用第一次缓存的旧数据。
"""
import argparse
import calendar
import datetime
import json
import os
import sys
import time
import urllib.request

API = "https://api.binance.com/api/v3/klines"
DEFAULT_SYMBOLS = ["BTC", "ETH", "BNB", "SOL"]
DEFAULT_START = "2019-01-01"
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(HERE, "data", "raw", "klines")


def http_json(url, tries=4):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "crypto-research/1.0"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode())
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(1.5 * (i + 1))


def fetch(symbol, start_ms):
    """分页拉全历史日线，返回 [{date, open, high, low, close, volume}]。"""
    rows, cursor = [], start_ms
    while True:
        url = f"{API}?symbol={symbol}USDT&interval=1d&limit=1000&startTime={cursor}"
        chunk = http_json(url)
        if not chunk:
            break
        rows += chunk
        if len(chunk) < 1000:
            break
        cursor = chunk[-1][0] + 86400000
        time.sleep(0.2)
    seen = {r[0]: r for r in rows}
    out = []
    for k in sorted(seen):
        r = seen[k]
        out.append({
            "date": datetime.datetime.utcfromtimestamp(r[0] / 1000).strftime("%Y-%m-%d"),
            "open": float(r[1]), "high": float(r[2]), "low": float(r[3]),
            "close": float(r[4]), "volume": float(r[5]),
        })
    return out


def _utc_ms(date_str):
    """'YYYY-MM-DD' → 该日 UTC 零点毫秒时间戳（与 Binance 日线开盘时间一致）。"""
    dt = datetime.datetime.strptime(date_str, "%Y-%m-%d")
    return int(calendar.timegm(dt.timetuple()) * 1000)


def merge_bars(old, new):
    """按 date 合并两组日线（新数据覆盖同日旧 bar），返回日期升序列表。"""
    by_date = {}
    for bar in list(old or []) + list(new or []):
        if isinstance(bar, dict) and bar.get("date"):
            by_date[bar["date"]] = bar
    return [by_date[d] for d in sorted(by_date)]


def load_cached(path):
    """读取缓存；文件缺失/损坏/结构不对一律当作无缓存（下次重建）。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            bars = json.load(f)
        if isinstance(bars, list) and all(isinstance(b, dict) for b in bars):
            return bars
    except (OSError, ValueError):
        pass
    return []


def main(argv=None):
    ap = argparse.ArgumentParser(description="拉取主流币日线历史")
    ap.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    ap.add_argument("--start", default=DEFAULT_START)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)

    os.makedirs(OUT_DIR, exist_ok=True)
    start_ms = int(datetime.datetime.strptime(args.start, "%Y-%m-%d").timestamp() * 1000)
    rc = 0
    for sym in [s.strip().upper() for s in args.symbols.split(",") if s.strip()]:
        path = os.path.join(OUT_DIR, f"{sym.lower()}_1d.json")
        cached = [] if args.force else load_cached(path)
        if cached:
            last_date = cached[-1].get("date") or ""
            begin = _utc_ms(last_date) if last_date else start_ms
            print(f"{sym}: 缓存 {len(cached)} 根（{cached[0].get('date')} → {last_date or '?'}），增量更新")
        else:
            begin = start_ms
        try:
            new_bars = fetch(sym, begin)
        except Exception as e:  # noqa: BLE001 - 单币失败继续其它币
            print(f"{sym}: 失败 {type(e).__name__}: {e}", file=sys.stderr)
            rc = 1
            continue
        bars = merge_bars(cached, new_bars)
        if not bars:
            print(f"{sym}: 拉取为空且无缓存，跳过", file=sys.stderr)
            rc = 1
            continue
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(bars, f)
        except OSError as e:
            print(f"{sym}: 写缓存失败 {e}", file=sys.stderr)
            rc = 1
            continue
        print(f"{sym}: {len(bars)} 根（{bars[0]['date']} → {bars[-1]['date']}，本次新增 {len(bars) - len(cached)}）→ {path}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
