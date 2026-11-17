#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""engine.py —— 逐点评估 + 告警治理（冷却 / 加剧 / 同点合并 / P0 日上限）。

线上 run.py 每轮调用一次 step()；离线 backtest.py 按 5m 逐点调用同一个 step()，
所以两边在同一份数据上的推送结果完全一致（docs §6 验收 2）。

治理一律按**数据时间戳**（不是墙钟）计时，保证可回放：
- 每个资产每条规则一个游标：只评估游标之后、已就绪（输入都有数据）且不晚于 now 的 5m 点；
- 每轮最多回看 catchup 秒，不对很旧的数据补发告警；
- 同一资产同一数据点命中多条规则只出一张卡（级别最高者为主）。
"""
import rules as R

import common

STEP = 300


def _ready_ts(series, rule, stale):
    """该规则所有输入都有数据的最新时间戳；任一输入过期返回 None。"""
    latest = []
    for m in R.REQUIRES[rule]:
        if m in stale:
            return None
        bucket = series.get(m) or {}
        if not bucket:
            return None
        latest.append(max(bucket))
    return min(latest)


def _pending(series, rule, cursor, now, stale, catchup_s):
    ready = _ready_ts(series, rule, stale)
    if ready is None:
        return []
    end = int(min(ready, now))
    start = int(max((cursor or 0) + STEP, now - catchup_s))
    start = start - start % STEP + (STEP if start % STEP else 0)
    return list(range(start, end + 1, STEP))


def _cooldown_s(cfg, rule):
    gov = (cfg or {}).get("governance") or {}
    rc = ((cfg or {}).get("rules") or {}).get(rule) or {}
    return float(rc.get("cooldown_hours", gov.get("cooldown_hours", 4))) * 3600


def _admit(hit, asset, ts, state, cfg):
    """治理判定：返回 None（不推送）或 {"escalated": bool}；通过时更新 state。"""
    gov = (cfg or {}).get("governance") or {}
    key = "%s|%s|%s" % (hit["rule"], asset, hit["direction"])
    prev = state["last"].get(key)
    escalated = False
    if prev and ts - prev[0] < _cooldown_s(cfg, hit["rule"]):
        big = hit["magnitude"] >= float(gov.get("escalate_multiple", 1.5)) * float(prev[1])
        gap = ts - prev[0] >= float(gov.get("escalate_min_gap_hours", 1)) * 3600
        if not (big and gap):
            return None
        escalated = True
    if hit["severity"] == "P0":
        day_key = "%s|%s" % (asset, common.local_date(ts))
        if state["p0"].get(day_key, 0) >= int(gov.get("p0_daily_cap", 2)):
            return None
        state["p0"][day_key] = state["p0"].get(day_key, 0) + 1
    state["last"][key] = [ts, hit["magnitude"]]
    return {"escalated": escalated}


def new_state():
    return {"cursor": {}, "last": {}, "p0": {}}


def _collect(series, asset, state, cfg, now, stale, catchup_s, keys):
    """按规则游标收集各数据点的命中：{ts: [hit, ...]}，并推进游标。"""
    by_ts = {}
    cursors = state["cursor"].setdefault(asset, {})
    for rule in R.ORDER:
        if not R._rule_cfg(cfg, rule):
            continue
        todo = _pending(series, rule, cursors.get(rule), now, stale, catchup_s)
        for ts in todo:
            hit = R.RULES[rule](series, ts, cfg, keys)
            if hit:
                by_ts.setdefault(ts, []).append(hit)
        if todo:
            cursors[rule] = todo[-1]
    return by_ts


def _card(asset, ts, admitted):
    admitted.sort(key=lambda h: (R.SEVERITY_RANK.get(h["severity"], 9), R.ORDER.index(h["rule"])))
    main = admitted[0]
    return {"asset": asset, "ts": ts, "severity": main["severity"], "rule": main["rule"],
            "direction": main["direction"], "escalated": bool(main.get("escalated")),
            "hits": admitted, "also": [h["rule"] for h in admitted[1:]]}


def step(all_series, state, cfg, now, stale=None, catchup_s=3600, keys=None):
    """评估所有资产，返回 (cards, p2_hits)。cards 按 (ts, asset) 排序；state 原地更新。"""
    stale = stale or {}
    cards, p2 = [], []
    for asset in sorted(all_series):
        by_ts = _collect(all_series[asset], asset, state, cfg, now, stale.get(asset) or set(),
                         catchup_s, (keys or {}).get(asset))
        for ts in sorted(by_ts):
            admitted = []
            for hit in by_ts[ts]:
                if hit["severity"] == "P2":
                    p2.append(dict(hit, asset=asset, ts=ts))
                    continue
                verdict = _admit(hit, asset, ts, state, cfg)
                if verdict is not None:
                    admitted.append(dict(hit, **verdict))
            if admitted:
                cards.append(_card(asset, ts, admitted))
    cards.sort(key=lambda c: (c["ts"], c["asset"]))
    _prune_state(state, now, cfg)
    return cards, p2


def _prune_state(state, now, cfg=None):
    """冷却记录保留到「最长冷却时长」之后（至少 1 天），P0 计数只留昨天和今天。"""
    longest = max([_cooldown_s(cfg, rule) for rule in R.ORDER] + [86400])
    for key in [k for k, v in state["last"].items() if now - v[0] > longest]:
        del state["last"][key]
    yesterday = common.local_date(now - 86400)
    for key in [k for k in state["p0"] if k.split("|", 1)[1] < yesterday]:
        del state["p0"][key]
