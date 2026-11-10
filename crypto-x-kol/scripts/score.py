#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""score.py —— KOL 推文价值评分（决定「要不要发告警」）。

为什么需要：30 个 KOL 一天几十条推文，全部推送会变成噪音源。本模块用**页面里能拿到的信号**
（作者层级、主题关键词、资产、是否突发、是否推广/传闻、跨 KOL 同题、是否首次提该主题）
算一个 0–100 的价值分，只把高分（P0/P1）推送 Telegram，其余仍进本地日报。

注意：X 匿名页面没有点赞/转发数、没有发帖时间，所以评分**不含互动量**；
「跨 KOL 同题」是最接近「热度」的替代信号。

评分（默认权重在 config/rules.yaml 的 alert 段，可调）：
    score = 作者层级 + Σ主题权重 + 资产关注 + 引用 + 跨KOL同题 + 首次提该主题
            - 推广/日常/传闻扣分
    分级：P0 ≥45 且（安全/监管类 或 ≥2 位 KOL 同题）；P1 ≥28；P2 ≥10；其余 MUTE（只进日报）

纯函数、无网络、无副作用，便于离线测试。
"""
import datetime
import re

LEVEL_ORDER = {"P0": 0, "P1": 1, "P2": 2, "MUTE": 3}
LEVEL_CN = {"P0": "P0 需立即看", "P1": "P1 值得看", "P2": "P2 仅日报", "MUTE": "MUTE 归档"}

DEFAULT_POLICY = {
    "weights": {
        "tier": {"T1": 18, "T2": 10},
        "critical": 30,
        "strong": 16,
        "action": 8,
        "breaking": 10,
        "asset": 5,
        "quote": 2,
        "consensus_per_other": 10,
        "consensus_cap": 20,
        "novelty": 8,
    },
    "penalties": {"promo": -25, "gm": -18, "rumor": -10},
    "thresholds": {"P0": 45, "P1": 28, "P2": 10},
    "consensus_window_hours": 4,
    "novelty_days": 7,
    "p0_pierce_quiet": True,
    "caps": {"per_day": 8, "max_per_message": 5, "p0_bypass_cap": True},
}


def policy(rules):
    """从 rules.yaml 读出 alert 策略，深合并默认值。"""
    raw = (rules or {}).get("alert") or {}
    merged = {}
    for key, value in DEFAULT_POLICY.items():
        if isinstance(value, dict):
            merged[key] = dict(value)
            merged[key].update(raw.get(key) or {})
        else:
            merged[key] = raw.get(key, value)
    return merged


def _text(post):
    return ("%s %s" % (post.get("text") or "", post.get("quote") or "")).lower()


def _match_term(term, text):
    """英文词按边界匹配（避免 SEC 命中 second），中文按子串。"""
    term = str(term).strip().lower()
    if not term:
        return False
    if re.fullmatch(r"[a-z0-9][a-z0-9 .:_-]*", term):
        return re.search(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(term), text) is not None
    return term in text


def _hits(text, terms):
    return [str(t) for t in (terms or []) if _match_term(t, text)]


def _topic_groups(rules):
    return list(((rules or {}).get("topics") or {}).items())


def _parse_ts(value):
    try:
        dt = datetime.datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


def topic_of(post, rules):
    """返回 (topic_key, topic_label, kind)。优先级：critical > strong；否则用资产。"""
    text = _text(post)
    best = (None, None, None)
    for key, group in _topic_groups(rules):
        kind = str(group.get("kind") or "strong")
        if kind not in ("critical", "strong"):
            continue
        if _hits(text, group.get("keywords")) and best[2] != "critical":
            best = (key, group.get("label") or key, kind)
            if kind == "critical":
                break
    return best


def _assets_in(text, rules):
    return [str(a) for a in ((rules or {}).get("assets") or []) if _match_term(a, text)]


def _consensus(post, topic_key, history, rules, cfg, now):
    """同主题的其他 KOL 数（窗口内、去重 handle、不含自己）。"""
    if not topic_key or not history:
        return 0
    window = datetime.timedelta(hours=float(cfg.get("consensus_window_hours") or 4))
    handles = set()
    for other in history:
        if other.get("id") == post.get("id"):
            continue
        if (other.get("handle") or "").lower() == (post.get("handle") or "").lower():
            continue
        ts = _parse_ts(other.get("first_seen"))
        if now and ts and (now - ts) > window:
            continue
        if topic_of(other, rules)[0] == topic_key:
            handles.add((other.get("handle") or "").lower())
    return len(handles)


def evaluate_post(post, rules, history=None, now=None):
    """给单条推文打分并分级；返回补全 score/level/topic/reasons 的 dict。"""
    cfg = policy(rules)
    text = _text(post)
    weights, penalties = cfg["weights"], cfg["penalties"]
    tier = str(post.get("tier") or "T2")
    score = int((weights.get("tier") or {}).get(tier, (weights.get("tier") or {}).get("T2", 10)))
    reasons, matched = [], []
    critical_hit = promo_hit = gm_hit = rumor_hit = False
    topic_key = topic_label = None

    for key, group in _topic_groups(rules):
        hits = _hits(text, group.get("keywords"))
        if not hits:
            continue
        kind = str(group.get("kind") or "strong")
        label = group.get("label") or key
        matched.extend(hits[:3])
        if kind == "critical":
            critical_hit = True
            score += int(weights.get("critical") or 30)
            reasons.append("安全/监管类：" + label)
            if topic_key is None:
                topic_key, topic_label = key, label
        elif kind == "strong":
            score += int(weights.get("strong") or 16)
            reasons.append(label)
            if topic_key is None:
                topic_key, topic_label = key, label
        elif kind == "action":
            score += int(weights.get("action") or 8)
            reasons.append("操作观点")
        elif kind == "breaking":
            score += int(weights.get("breaking") or 10)
            reasons.append("突发词")
        elif kind == "rumor":
            rumor_hit = True
            score += int(penalties.get("rumor") or -10)
        elif kind == "promo":
            promo_hit = True
            score += int(penalties.get("promo") or -25)
        elif kind == "gm":
            gm_hit = True
            score += int(penalties.get("gm") or -18)

    if promo_hit or gm_hit:
        reasons.append("推广/日常内容（降权）")
    if rumor_hit:
        reasons.append("传闻（降权）")

    assets = _assets_in(text, rules)
    if assets:
        score += int(weights.get("asset") or 5)
        reasons.append("关注资产：" + "/".join(assets[:2]))

    if post.get("quote"):
        score += int(weights.get("quote") or 2)

    consensus = _consensus(post, topic_key, history or [], rules, cfg,
                           _parse_ts(post.get("first_seen")) or now)
    if consensus:
        score += min(consensus * int(weights.get("consensus_per_other") or 10),
                     int(weights.get("consensus_cap") or 20))
        reasons.append("%d 位 KOL 同题（%s）" % (consensus, topic_label or topic_key))

    if topic_key and history is not None:
        novel = True
        for other in history:
            if other.get("id") == post.get("id"):
                continue
            if (other.get("handle") or "").lower() != (post.get("handle") or "").lower():
                continue
            if topic_of(other, rules)[0] == topic_key:
                novel = False
                break
        if novel:
            score += int(weights.get("novelty") or 8)
            reasons.append("%d 天内首次提该主题" % int(cfg.get("novelty_days") or 7))

    score = max(0, min(100, score))
    thresholds = cfg["thresholds"]
    if score >= int(thresholds.get("P0") or 45) and (critical_hit or consensus >= 2):
        level = "P0"
    elif score >= int(thresholds.get("P1") or 28):
        level = "P1"
    elif score >= int(thresholds.get("P2") or 10):
        level = "P2"
    else:
        level = "MUTE"

    out = dict(post)
    out.update({
        "score": score,
        "level": level,
        "level_cn": LEVEL_CN.get(level, level),
        "topic": topic_key,
        "topic_label": topic_label,
        "topic_hits": matched[:5],
        "consensus": consensus,
        "reasons": reasons,
        "keywords": _hits(text, ((rules.get("high_signal") or {}).get("keywords") or [])),
    })
    return out


def evaluate(posts, rules, history=None, now=None):
    """批量评分（history 用于同题/新颖度），按「级别 → 分数」降序返回。"""
    out = [evaluate_post(p, rules, history, now) for p in posts]
    out.sort(key=lambda p: (LEVEL_ORDER.get(p["level"], 9), -p["score"], p.get("first_seen") or ""))
    return out
