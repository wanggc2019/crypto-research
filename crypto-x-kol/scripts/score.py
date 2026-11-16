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


# kind → (取值表, 键, 默认分, 理由模板（None=不写理由；{label} 替换为主题名）, 是否可作为主题)
_KIND_RULES = {
    "critical": ("weights", "critical", 30, "安全/监管类：{label}", True),
    "strong": ("weights", "strong", 16, "{label}", True),
    "action": ("weights", "action", 8, "操作观点", False),
    "breaking": ("weights", "breaking", 10, "突发词", False),
    "rumor": ("penalties", "rumor", -10, None, False),
    "promo": ("penalties", "promo", -25, None, False),
    "gm": ("penalties", "gm", -18, None, False),
}


def _score_topics(text, rules, cfg):
    """按主题词组累加分数；返回 {score, reasons, matched, kinds(命中的 kind 集合), topic_key, topic_label}。"""
    tables = {"weights": cfg["weights"], "penalties": cfg["penalties"]}
    res = {"score": 0, "reasons": [], "matched": [], "kinds": set(), "topic_key": None, "topic_label": None}
    for key, group in _topic_groups(rules):
        hits = _hits(text, group.get("keywords"))
        if not hits:
            continue
        kind = str(group.get("kind") or "strong")
        label = group.get("label") or key
        res["matched"].extend(hits[:3])
        rule = _KIND_RULES.get(kind)
        if not rule:
            continue
        table, wkey, default, reason, sets_topic = rule
        res["kinds"].add(kind)
        res["score"] += int(tables[table].get(wkey) or default)
        if reason:
            res["reasons"].append(reason.format(label=label))
        if sets_topic and res["topic_key"] is None:
            res["topic_key"], res["topic_label"] = key, label
    return res


def _is_novel(post, topic_key, history, rules):
    """同一账号在历史窗口内没提过该主题。"""
    handle = (post.get("handle") or "").lower()
    for other in history:
        if other.get("id") == post.get("id"):
            continue
        if (other.get("handle") or "").lower() != handle:
            continue
        if topic_of(other, rules)[0] == topic_key:
            return False
    return True


def _grade(score, thresholds, critical_hit, consensus):
    if score >= int(thresholds.get("P0") or 45) and (critical_hit or consensus >= 2):
        return "P0"
    if score >= int(thresholds.get("P1") or 28):
        return "P1"
    if score >= int(thresholds.get("P2") or 10):
        return "P2"
    return "MUTE"


def evaluate_post(post, rules, history=None, now=None):
    """给单条推文打分并分级；返回补全 score/level/topic/reasons 的 dict。"""
    cfg = policy(rules)
    text = _text(post)
    weights = cfg["weights"]
    tier = str(post.get("tier") or "T2")
    score = int((weights.get("tier") or {}).get(tier, (weights.get("tier") or {}).get("T2", 10)))

    topics = _score_topics(text, rules, cfg)
    score += topics["score"]
    reasons = topics["reasons"]
    topic_key, topic_label = topics["topic_key"], topics["topic_label"]
    if topics["kinds"] & {"promo", "gm"}:
        reasons.append("推广/日常内容（降权）")
    if "rumor" in topics["kinds"]:
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

    if topic_key and history is not None and _is_novel(post, topic_key, history, rules):
        score += int(weights.get("novelty") or 8)
        reasons.append("%d 天内首次提该主题" % int(cfg.get("novelty_days") or 7))

    score = max(0, min(100, score))
    level = _grade(score, cfg["thresholds"], "critical" in topics["kinds"], consensus)

    out = dict(post)
    out.update({
        "score": score,
        "level": level,
        "level_cn": LEVEL_CN.get(level, level),
        "topic": topic_key,
        "topic_label": topic_label,
        "topic_hits": topics["matched"][:5],
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
