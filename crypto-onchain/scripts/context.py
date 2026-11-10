#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""context.py —— 链上大额转账的"值不值得打扰"判断（价格 / 新闻 / 资金面融合）。

用户诉求（2026-11-10）：单看链上转账看不出意义，希望结合价格、新闻与市场状态
**自主判断要不要发**；没有上下文的大额转账不打扰，有意义的才推送并说明理由。

输入（均由 run.py 采集/读取后传入，纯函数、离线可测）：
- assets: {BTC: {change_24h_pct, funding_rate, ...}}（crypto-price exports）
- signals: crypto-price 导出的近期信号（可选，含 severity/rule/ts）
- news: crypto-news daily export 的 items（可选，含 severity/title/category/published_at/hard）
- cex_net_24h_bps / stablecoin_net_issue_24h_usd：本模块采集的 24h 资金面

输出：assess(record, cfg, ctx) -> {"send": bool, "score": int, "reasons": [str], "whale": bool}
"""
import datetime
import json
import os

KEYWORDS = ("hack", "exploit", "etf", "fed", "rate", "liquidation", "blackrock",
            "binance", "coinbase", "sec", "ban", "tariff", "war", "crash", "outflow",
            "被盗", "黑客", "监管", "加息", "降息", "清算", "暴跌", "崩盘")


def load_news(repo_root, date):
    """读 crypto-news 的 daily export（跨模块只读 exports/）；失败返回 []。"""
    path = os.path.join(repo_root, "crypto-news", "data", "exports", "daily-%s.json" % date)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    items = data.get("items") if isinstance(data, dict) else None
    return items if isinstance(items, list) else []


def load_price_export(repo_root):
    """读 crypto-price 的 exports/latest.json（取 signals 与 assets 兜底）；失败返回 {}。"""
    path = os.path.join(repo_root, "crypto-price", "data", "exports", "latest.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _news_ttl_hours(cfg):
    return float((cfg.get("smart") or {}).get("news_lookback_hours") or 6)


def _parse_ts(text):
    try:
        dt = datetime.datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt.timestamp()


def _recent_news(news, now, hours):
    out = []
    now_ts = now.timestamp() if hasattr(now, "timestamp") else float(now)
    for item in news or []:
        if not isinstance(item, dict):
            continue
        ts = _parse_ts(item.get("published_at"))
        if ts is None or (now_ts - ts) > hours * 3600 or ts > now_ts + 300:
            continue
        out.append(item)
    return out


def _keyword_hit(item):
    text = ("%s %s %s" % (item.get("title") or "", item.get("category") or "",
                          " ".join(item.get("hard") or []))).lower()
    hits = [k for k in KEYWORDS if k in text]
    return hits


def assess(record, cfg, ctx, now):
    """返回是否发送、上下文得分、理由（人类可读）。巨鲸直接放行。"""
    smart = cfg.get("smart") or {}
    usd = float(record.get("usd") or 0)
    whale = (usd >= float(cfg.get("whale_usd") or 100_000_000)
             or (record.get("direction") == "cex_in"
                 and usd >= float(cfg.get("whale_cex_in_usd") or 50_000_000)))
    if not smart.get("enabled", True):
        return {"send": True, "score": 0, "reasons": [], "whale": whale}
    if whale:
        return {"send": True, "score": 0, "reasons": ["巨鲸金额（≥$100M 或入所 ≥$50M）"], "whale": True}

    reasons, score = [], 0
    # 1) 价格波动：任一主流币 24h 涨跌幅 ≥ 阈值
    assets = ctx.get("assets") or {}
    stress_pct = float(smart.get("stress_move_24h_pct") or 3.0)
    worst = None
    for asset, row in assets.items():
        ch = (row or {}).get("change_24h_pct")
        try:
            ch = float(ch)
        except (TypeError, ValueError):
            continue
        if worst is None or abs(ch) > abs(worst[1]):
            worst = (asset, ch)
    if worst and abs(worst[1]) >= stress_pct:
        score += 2
        reasons.append("%s 24h %+.1f%%" % (worst[0], worst[1]))

    # 2) 价格信号（快涨快跌/破位等）
    for sig in ctx.get("signals") or []:
        if not isinstance(sig, dict):
            continue
        if str(sig.get("severity")) in ("P0", "P1"):
            score += 2 if str(sig.get("severity")) == "P0" else 1
            reasons.append("价格信号：%s" % (sig.get("rule") or sig.get("title") or "异动"))
            break

    # 3) 新闻：lookback 内 P0/P1 且命中主题关键词
    news = _recent_news(ctx.get("news") or [], now, _news_ttl_hours(cfg))
    for item in sorted(news, key=lambda x: str(x.get("published_at") or ""), reverse=True):
        sev = str(item.get("severity") or "")
        if sev not in ("P0", "P1"):
            continue
        hits = _keyword_hit(item)
        if not hits:
            continue
        score += 3 if sev == "P0" else 2
        reasons.append("新闻 %s：%s" % (sev, (item.get("title") or "")[:40]))
        break

    # 4) 资金面：CEX 净流出 / 稳定币净赎回
    try:
        cex = ctx.get("cex_net_24h_bps")
        if cex is not None and float(cex) <= -float(smart.get("cex_outflow_bps") or 100):
            score += 1
            reasons.append("CEX 24h 净流出 %.0fbps" % float(cex))
    except (TypeError, ValueError):
        pass
    try:
        st = ctx.get("stablecoin_net_issue_24h_usd")
        if st is not None and float(st) <= -float(smart.get("stablecoin_outflow_usd") or 500000000):
            score += 1
            reasons.append("稳定币 24h 净赎回 %.1f 亿" % (abs(float(st)) / 1e8))
    except (TypeError, ValueError):
        pass

    # 5) 入所 + 价格下跌：潜在卖压
    try:
        btc_ch = float((assets.get("BTC") or {}).get("change_24h_pct"))
        if record.get("direction") == "cex_in" and btc_ch <= -2.0:
            score += 1
            reasons.append("BTC 下跌中转入交易所")
    except (TypeError, ValueError):
        pass

    min_score = int(smart.get("min_score") or 2)
    return {"send": score >= min_score, "score": score, "reasons": reasons, "whale": False}
