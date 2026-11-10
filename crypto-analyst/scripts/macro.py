#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""macro.py —— 宏观/波动数据（零成本公开接口），写入 data/raw/macro/<date>.json。

数据源（全部免 key）：
  - Deribit DVOL（BTC 期权隐含波动率指数）
  - FRED DGS10（美国 10 年期国债收益率）
  - Yahoo Finance DX-Y.NYB（美元指数）

用法：
    python3 scripts/macro.py              # 抓取并落盘
    python3 scripts/macro.py --dry-run    # 只打印
    python3 scripts/macro.py --json       # 机器可读

离线时：单源失败不影响其他源；全部失败退出码 1，数据文件不写。
"""
import argparse
import concurrent.futures
import datetime
import json
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UA = {"User-Agent": "Mozilla/5.0 (crypto-research/1.0)"}


def http(url, timeout=6, tries=1):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode()
        except Exception as e:  # 网络/HTTP 错误都降级
            last = e
            if i < tries - 1:
                time.sleep(1.0)
    raise last


def fetch_dvol(currency="BTC"):
    start = int((datetime.datetime.utcnow() - datetime.timedelta(days=3)).timestamp() * 1000)
    end = int(time.time() * 1000)
    url = ("https://www.deribit.com/api/v2/public/get_volatility_index_data"
           f"?currency={currency}&start_timestamp={start}&end_timestamp={end}&resolution=1D")
    rows = json.loads(http(url))["result"]["data"]
    ts, _o, _h, _l, close = rows[-1]
    d = datetime.datetime.utcfromtimestamp(ts / 1000).strftime("%Y-%m-%d")
    return {"label": "DVOL（BTC 隐含波动率）", "value": round(close, 2), "date": d}


def fetch_fred(series_id, label, days=10):
    cosd = (datetime.datetime.utcnow() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}&cosd={cosd}"
    lines = [ln for ln in http(url).strip().splitlines() if ln and not ln.startswith("observation_date")]
    for ln in reversed(lines):
        parts = ln.split(",")
        if len(parts) >= 2 and parts[1] not in ("", "."):
            return {"label": label, "value": float(parts[1]), "date": parts[0]}
    raise ValueError("FRED 无有效观测值")


def fetch_yahoo(symbol, label):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1mo&interval=1d"
    data = json.loads(http(url))["chart"]["result"][0]
    closes = data["indicators"]["quote"][0].get("close") or []
    stamps = data.get("timestamp") or []
    for i in range(len(closes) - 1, -1, -1):
        if closes[i] is not None:
            d = datetime.datetime.utcfromtimestamp(stamps[i]).strftime("%Y-%m-%d")
            return {"label": label, "value": round(float(closes[i]), 3), "date": d}
    raise ValueError("Yahoo 无有效收盘价")


def collect(deadline_seconds=8):
    """并发抓取，总耗时受 deadline 约束；单源失败不影响其他源。"""
    series, errors = {}, {}
    tasks = {
        "dvol": lambda: fetch_dvol("BTC"),
        "us10y": lambda: fetch_fred("DGS10", "美国 10Y 国债收益率"),
        "dxy": lambda: fetch_yahoo("DX-Y.NYB", "美元指数 DXY"),
    }
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(tasks)) as ex:
        futures = {k: ex.submit(fn) for k, fn in tasks.items()}
        done, not_done = concurrent.futures.wait(futures.values(), timeout=deadline_seconds)
        for key, fut in futures.items():
            if fut in not_done:
                errors[key] = "timeout（超过总时限）"
                continue
            try:
                series[key] = fut.result()
            except Exception as e:  # 单源降级
                errors[key] = f"{type(e).__name__}: {e}"
    return series, errors


def main(argv=None):
    ap = argparse.ArgumentParser(description="抓取宏观/波动数据（零成本公开源）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    series, errors = collect()
    date_str = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    payload = {"schema": "macro/v1", "date": date_str, "series": series, "errors": errors}

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
    else:
        print(f"宏观数据 {date_str}")
        for k, v in series.items():
            print(f"  {v['label']}: {v['value']}（{v['date']}）")
        for k, e in errors.items():
            print(f"  ✗ {k}: {e}", file=sys.stderr)

    if not series:
        print("全部数据源失败（离线？）", file=sys.stderr)
        return 1
    if args.dry_run:
        return 0
    out_dir = os.path.join(HERE, "data", "raw", "macro")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{date_str}.json")
    with open(path, "w") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
