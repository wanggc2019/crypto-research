#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""score.py —— 复盘记录复盘打分（方向命中 + 情景 Brier + 失效检查）。

用法：
    python3 scripts/score.py --dry-run              # 只看哪些到期/即将到期，不写文件
    python3 scripts/score.py                        # 对到期条目打分并回写 outcome/scored
    python3 scripts/score.py --as-of 2026-11-15      # 指定复盘日（默认今天）
    python3 scripts/score.py --json                 # 机器可读输出

打分口径：
  - 情景 Brier：对 entry["scenarios_1_3m"] 里的概率与 band_px 计算多分类 Brier（越低越好）；
  - 失效检查：条目日期到复盘日之间，若出现「BTC 日收盘 < 当日 MA200」→ invalidated=True；
  - 方向命中：**未实现自动判定**（P2）：条目目前只记录 since-entry 涨跌，不判对错。

前提：data/raw/klines/ 有 BTC 日线（fetch_history.py）。
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core  # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(HERE, "records", "judgments.jsonl")


def load_log(path=LOG):
    if not os.path.exists(path):
        return []
    return [json.loads(line) for line in open(path) if line.strip()]


def save_log(entries, path=LOG):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


def find_bar(bars, date_str):
    """返回 date_str 当日或之前最近一根日线。"""
    chosen = None
    for b in bars:
        if b["date"] <= date_str:
            chosen = b
        else:
            break
    return chosen


def invalidated_between(bars, start_date, end_date):
    """start_date..end_date 之间，是否出现收盘跌破当日 MA200。"""
    closes = [b["close"] for b in bars]
    for i, b in enumerate(bars):
        if b["date"] < start_date or b["date"] > end_date:
            continue
        if i < 199:
            continue
        ma200 = core.sma(closes[:i + 1], 200)
        if ma200 and b["close"] < ma200:
            return True, b["date"]
    return False, None


def score_entry(entry, btc_bars, as_of):
    """对一个条目打分；返回 (score_dict, reason_if_skipped)。"""
    review_by = entry.get("score_at") or entry.get("review_by")
    if not review_by:
        return None, "无 score_at/review_by"
    if review_by > as_of:
        return None, "未到期"
    if entry.get("scored"):
        return None, "已打分"
    scenarios = entry.get("scenarios_1_3m") or []
    bands = {}
    probs = {}
    for sc in scenarios:
        band = sc.get("band_px")
        if not band:
            continue
        bands[sc["id"]] = (band[0], band[1])
        probs[sc["id"]] = sc.get("prob", 0) / 100.0
    if not bands:
        return None, "无 band_px（不可打分，补场景区间后重跑）"

    entry_date = entry.get("ts", "")[:10]
    realized = find_bar(btc_bars, review_by)
    if not realized:
        return None, "K 线缺复盘日数据"
    base = find_bar(btc_bars, entry_date)
    band = core.band_of_price(realized["close"], bands)
    invalid, invalid_date = invalidated_between(btc_bars, entry_date, review_by)
    result = {
        "review_date": review_by,
        "realized_px": round(realized["close"], 2),
        "realized_band": band,
        "brier": core.brier(probs, band) if band in probs else None,
        "invalidated": invalid,
        "invalidated_on": invalid_date,
        "return_since_entry_pct": round((realized["close"] / base["close"] - 1) * 100, 2) if base else None,
    }
    return result, None


def run(as_of, dry_run=False, json_out=False, log_path=LOG, klines_base=HERE):
    entries = load_log(log_path)
    try:
        btc = core.load_bars("BTC", klines_base)
    except FileNotFoundError:
        btc = None
    results, pending = [], []
    for e in entries:
        if btc is None:
            pending.append({"id": e.get("id"), "reason": "缺 K 线"})
            continue
        res, reason = score_entry(e, btc, as_of)
        if res:
            results.append({"id": e.get("id"), **res})
            if not dry_run:
                e["outcome"] = res
                e["scored"] = True
        else:
            due = e.get("score_at") or e.get("review_by")
            if due:  # 没有复盘日期的条目（口径记录）不参与打分，也不刷屏
                pending.append({"id": e.get("id"), "review_by": due, "reason": reason})
    if not dry_run and results:
        save_log(entries, log_path)
    if json_out:
        print(json.dumps({"as_of": as_of, "scored": results, "pending": pending},
                         ensure_ascii=False, indent=1))
        return 0
    print(f"复盘日 {as_of}｜条目 {len(entries)} 条｜本次打分 {len(results)} 条")
    for r in results:
        print(f"  ✔ {r['id']}  实现价 {r['realized_px']}  落入 {r['realized_band']}  "
              f"Brier {r['brier']}  期间收益 {r['return_since_entry_pct']}%  "
              f"失效 {'是（' + str(r['invalidated_on']) + '）' if r['invalidated'] else '否'}")
    if not results:
        print("  本次没有到期的条目。")
    print("待到期/未打分：")
    for p in pending:
        print(f"  · {p['id']}  review_by={p.get('review_by')}  原因：{p['reason']}")
    if dry_run:
        print("（dry-run：未写回复盘记录）")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="复盘记录复盘打分")
    ap.add_argument("--as-of", default=None, help="复盘日 YYYY-MM-DD（默认今天）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--log", default=LOG)
    args = ap.parse_args(argv)
    as_of = args.as_of or __import__("datetime").datetime.now().strftime("%Y-%m-%d")
    return run(as_of, dry_run=args.dry_run, json_out=args.json, log_path=args.log)


if __name__ == "__main__":
    sys.exit(main())
