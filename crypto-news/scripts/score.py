#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""score.py —— 过滤 / 去重聚类 / 分类 / 评分 / 出本地报告

用法：
    python3 scripts/score.py              # 处理今天的数据
    python3 scripts/score.py --date 2026-11-08

输入：data/raw/<date>/items.jsonl
输出：data/reports/<date>.md          # 本地报告（人看）
      data/state/alerts-<date>.json   # P0/P1 告警（后续交给 notify.py）
"""
import datetime
import hashlib
import json
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (  # noqa: E402
    DATA_DIR,
    RAW_DIR,
    REPORT_DIR,
    STATE_DIR,
    ensure_dirs,
    load_config,
    safe_json_dump,
    today_str,
    ts_iso,
)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from crypto_core import events as _events  # noqa: E402
from crypto_core.text import cn_summary  # noqa: E402
from fetch import extract_amount_usd  # noqa: E402

ASCII_ALIAS = re.compile(r"^[A-Za-z0-9 .\-]+$")


# ---------------------------------------------------------------- 匹配
def build_asset_patterns():
    wl = load_config("watchlist.yaml")
    ent = load_config("entities.yaml")
    assets = {}
    for a in wl.get("assets", []):
        sym = a["symbol"]
        words = [sym] + (a.get("aliases") or [])
        words += (ent.get("assets", {}).get(sym, {}) or {}).get("related", [])
        rx = []
        for w in words:
            w = (w or "").strip()
            if not w:
                continue
            if ASCII_ALIAS.match(w):
                rx.append(r"(?<![A-Za-z0-9])" + re.escape(w) + r"(?![A-Za-z0-9])")
            else:
                rx.append(re.escape(w))
        assets[sym] = re.compile("|".join(rx), re.I) if rx else None
    # 稳定币
    sc = []
    for x in ent.get("stablecoins", []) or []:
        sc.append(x.get("symbol", ""))
        sc += x.get("aliases", []) or []
    sc_rx = re.compile("|".join(re.escape(s) for s in sc if s), re.I) if sc else None
    return assets, sc_rx



# ---------------------------------------------------------------- 美股/宏观（美股大跌、Fed/CPI、加密相关股票）
_MACRO_MARKET = [
    "nasdaq", "s&p", "s&p 500", "dow", "wall street", "stock market", "stocks", "equities",
    "selloff", "sell-off", "rout", "plunge", "crash", "circuit breaker", "vix",
    "treasury", "yields", "10-year", "fed", "fomc", "powell", "cpi", "inflation",
    "jobs report", "nonfarm", "nfp", "rate cut", "rate hike", "tariff", "recession",
    "risk-off", "big tech", "tech selloff", "tech stocks",
]
_MACRO_CRYPTO = [
    "bitcoin", "btc", "ethereum", "eth", "crypto", "binance", "coinbase", "microstrategy",
    "mstr", "etf", "ibit", "fbtc", "stablecoin", "digital asset", "crypto stocks",
]
_MACRO_EXTREME = [
    "selloff", "sell-off", "plunge", "rout", "crash", "circuit breaker", "vix",
    "fomc", "cpi", "rate cut", "rate hike", "tariff", "record high", "record low",
    "all-time high", "all-time low", "recession",
]


def is_macro_news(text):
    """美股/宏观新闻：只保留「市场级」事件（不是个股日常新闻）。"""
    t = (text or "").lower()
    market = any(k in t for k in _MACRO_MARKET)
    crypto = any(k in t for k in _MACRO_CRYPTO)
    extreme = any(k in t for k in _MACRO_EXTREME)
    if market and (crypto or extreme):
        return True
    # 加密相关股票/ETF 的公司级新闻也保留
    if crypto and any(k in t for k in ("microstrategy", "mstr", "coinbase", "crypto stocks", "ibit", "fbtc", "etf")):
        return True
    return False


# ---------------------------------------------------------------- 组织动向（员工/裁员/高管离职线索）
def build_labor_matchers():
    """返回 (关键词正则, 雇主正则)；两者同时命中才算组织动向线索。"""
    ent = load_config("entities.yaml")
    keywords = (ent.get("event_keywords") or {}).get("industry_labor") or []
    employers = []
    for name, meta in (ent.get("employers") or {}).items():
        employers.append(name)
        employers += (meta or {}).get("aliases") or []
    kw_rx = re.compile("|".join(re.escape(k) for k in keywords if k), re.I) if keywords else None
    emp_rx = re.compile("|".join(re.escape(e) for e in employers if e), re.I) if employers else None
    return kw_rx, emp_rx


# ---------------------------------------------------------------- 分类
def classify(text, keywords):
    t = (text or "").lower()
    best, best_hits = None, 0
    for cat, words in keywords.items():
        hits = sum(1 for w in words if w and w.lower() in t)
        if hits > best_hits:
            best, best_hits = cat, hits
    return best, best_hits


# ---------------------------------------------------------------- 去重 / 聚类
def simhash(text):
    t = re.sub(r"\s+", "", text or "")
    if not t:
        return 0
    grams = [t[i:i + 3] for i in range(max(0, len(t) - 2))] or [t]
    v = [0] * 64
    for g in grams:
        h = int(hashlib.md5(g.encode("utf-8", "ignore")).hexdigest()[:16], 16)
        for i in range(64):
            v[i] += 1 if (h >> i) & 1 else -1
    out = 0
    for i in range(64):
        if v[i] > 0:
            out |= (1 << i)
    return out


def hamming(a, b):
    return bin(a ^ b).count("1")


STOP = set("""the a an and or but of to in on for with from after before says said will would
could should has have had is are was were be been being this that these those its their his her
you your we our they them he she it as at by into over under about more most less least new
news report reports update updates price prices market markets crypto bitcoin ethereum ethereum
""".split())

EVENT_TOKENS = set("""hack exploit stolen drained breach attack 被盗 攻击 etf approve approval reject
delist delisting halt halted suspend suspended pause paused withdrawal withdrawals deposit deposits
unlock unlocke unlock burn buyback upgrade fork mainnet depeg liquidation lawsuit sue settlement
sec cftc fed fomc investigation probe arrest resign listing list binance coinbase okx bybit kraken
tether usdt usdc stablecoin reserve insolvent bankruptcy""".split())


def tokens_of(text):
    text = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff ]+", " ", (text or "")).lower()
    return {t for t in text.split() if len(t) >= 3 and t not in STOP}


def cluster_items(items, max_dist=3):
    """同一事件聚类：同一天 + (Jaccard>=0.3 或 2 个以上事件词重合 或 simhash 很近)。
    返回 clusters: list[list[item]]"""
    clusters = []
    for it in items:
        toks = tokens_of(it.get("title", "") + " " + (it.get("body", "") or "")[:200])
        it["_toks"] = toks
        day = (it.get("published_at") or "")[:10]
        placed = False
        for c in clusters:
            c_toks = c[0]["_toks"]
            c_day = (c[0].get("published_at") or "")[:10]
            union = toks | c_toks
            jac = len(toks & c_toks) / len(union) if union else 0
            shared_ev = len((toks & c_toks) & EVENT_TOKENS)
            same_day = (not day) or (not c_day) or day == c_day
            if (same_day and (jac >= 0.3 or shared_ev >= 2)) or shared_ev >= 2 or hamming(it["_sh"], c[0]["_sh"]) <= max_dist:
                c.append(it)
                placed = True
                break
        if not placed:
            clusters.append([it])
    return clusters


# ---------------------------------------------------------------- 评分
def parse_ts(s):
    if not s:
        return None
    try:
        return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def decay_hours(cat, thresholds):
    m = {"protocol_security": "security", "exchange_custody": "exchange",
         "stablecoin_depeg": "exchange", "regulatory": "regulatory",
         "macro": "macro", "capital_flow": "market", "market_structure": "market"}
    key = m.get(cat, "security")
    return thresholds["scoring"]["decay_hours"].get(key, 24)


def magnitude(it, text, thresholds):
    # 行情类：脱锚 bps / 资金费率直接从字段里来
    m = re.search(r"偏离锚定\s*([\d.]+)\s*bps", it.get("title", ""))
    if m:
        bps = float(m.group(1))
        return min(100.0, 25 * bps / 100), {"depeg_bps": bps}
    # 文本里的金额
    usd = extract_amount_usd(text)
    if usd and usd > 0:
        val = 25 * math.log10(usd / 1e4)
        return max(0.0, min(100.0, val)), {"usd": usd}
    return 40.0, {}


def score_cluster(cluster, thresholds, cfg_patterns):
    pri = {"T0": 0, "T1": 1, "T2": 2, "T3": 3}
    cluster = sorted(cluster, key=lambda x: pri.get(x.get("tier", "T3"), 9))
    head = cluster[0]
    text = head.get("title", "") + " " + (head.get("body", "") or "")
    cat, hits = classify(text, load_config("entities.yaml").get("event_keywords", {}))
    if not cat:
        cat = "other"
    if any(item.get("_macro") for item in cluster) and cat in ("other", "market_structure"):
        cat = "macro"
    # 注意：不强制覆盖分类——若本身是安全/监管类（如"被盗后 CFO 离职"），
    # 保留其高优先级分类，避免把重大安全事件降级成组织动向。
    s = thresholds["scoring"]
    tier = head.get("tier", "T3")
    S = {"T0": 100, "T1": 80, "T2": 85, "T3": 60}.get(tier, 60)
    T = s["event_base"].get(cat, 15)
    M, meta = magnitude(head, text, thresholds)
    if cat == "macro":
        pct = re.search(r"([\d.]+)\s*%", head.get("title", "") or text)
        if pct:
            try:
                M = max(M, min(100.0, float(pct.group(1)) * 20))
            except ValueError:
                pass
    k = len({x.get("source_id") for x in cluster})
    C = 100 * (1 - 0.5 ** (max(k, 1) - 1))
    if tier == "T0":
        C = 100.0  # 官方一手本身就是确认，不再按“多源数量”惩罚
    raw = s["weights"]["source"] * S + s["weights"]["event_type"] * T + \
        s["weights"]["magnitude"] * M + s["weights"]["confirmation"] * C
    pub = parse_ts(head.get("published_at"))
    age = 0.0
    if pub:
        age = max(0.0, (datetime.datetime.now(pub.tzinfo) - pub).total_seconds() / 3600)
    D = 2 ** (-age / decay_hours(cat, thresholds))
    score = max(0, min(100, raw * D))
    out = {
        "module": "crypto-news",
        "kind": head.get("kind"),
        "cn_summary": cn_summary(head.get("title", "")),
        "social": bool(head.get("_social")),
        "score": round(score, 1), "raw": round(raw, 1), "category": cat,
        "hits": hits, "components": {"S": S, "T": T, "M": round(M, 1), "C": round(C, 1), "D": round(D, 3)},
        "meta": meta, "sources": sorted({x.get("source_id") for x in cluster}),
        "titles": [x.get("title") for x in cluster][:4],
        "url": head.get("url", ""), "published_at": head.get("published_at", ""),
        "assets": sorted({a for x in cluster for a in x.get("_assets", [])}),
        "count": len({x.get("source_id") for x in cluster}),
        "source_tiers": {x.get("source_id"): x.get("tier", "T3") for x in cluster},
    }
    points = extract_points(head.get("title", ""), head.get("body", "") or "")
    if points:
        out["points"] = points
    return out


# ---------------------------------------------------------------- 硬条件
def hard_triggers(text, meta, assets, people):
    t = (text or "").lower()
    hit = []
    if re.search(r"脱锚|depeg|de-peg|脱钩", t):
        hit.append("stablecoin_depeg")
    if re.search(r"(暂停|halt|suspend|pause).{0,8}(提币|提现|充值|withdraw)", t) or re.search(r"withdrawals?\s+(halted|suspended|paused)", t):
        hit.append("exchange_withdrawal")
    if re.search(r"\bhack(ed|s)?\b|\bexploit(ed)?\b|被盗|攻击|\bdrained\b", t) and meta.get("usd", 0) >= 1e7:
        hit.append("hack_large")
    if re.search(r"\betf\b", t) and re.search(r"approv|reject|批准|否决|正式通过", t):
        hit.append("etf_resolution")
    if re.search(r"\bsec\b|\bcftc\b|\bdoj\b|起诉|lawsuit|\bsue[ds]?\b|settlement|和解", t) and re.search(r"binance|coinbase|okx|bybit|kraken|币安|欧易", t):
        hit.append("sec_head_exchange")
    if re.search(r"\bfomc\b|美联储|\bfed\b", t) and re.search(r"surprise|意外|紧急降息|紧急加息|unexpected|emergency|between meetings|off-cycle", t):
        hit.append("fomc_surprise")
    if re.search(r"清算|liquidation", t) and meta.get("usd", 0) >= 5e8:
        hit.append("liquidation_cascade")
    for p in people:
        for alias in [p.get("x", ""), p.get("name", "")]:
            if alias and alias.lower() in t:
                if re.search(r"起诉|lawsuit|调查|investigat|辞职|resign|被捕|arrest", t):
                    hit.append("executive_legal")
    return sorted(set(hit))


# ---------------------------------------------------------------- 摘要/精选类文章的要点拆解
DIGEST_RX = re.compile(r"周精选|每日精选|精选|周报|日报|早报|晚报|速览|盘点|汇总|要闻|Top\s*\d+|top\s*\d+", re.I)
_POINT_KW = [
    ("被盗", 4), ("被黑", 4), ("黑客", 3), ("攻击", 2), ("暂停提币", 4), ("暂停提现", 4),
    ("脱锚", 4), ("破产", 4), ("清算", 3), ("起诉", 3), ("监管", 2), ("ETF", 2),
    ("大跌", 2), ("暴跌", 2), ("暴跌", 2), ("增持", 1), ("解锁", 2), ("下架", 2),
    ("停止", 1), ("通过", 1), ("法案", 2), ("升级", 1),
]


def _clean_point(text):
    """去掉栏目名前缀（如「吴说周精选：」）与尾部「与新闻 Top10」之类。"""
    t = (text or "").strip()
    t = re.sub(r"^[^：:]{1,14}[：:]\s*", "", t)          # 栏目名前缀
    t = re.sub(r"(?:与|及|和)?\s*(?:新闻|热门文章)?\s*Top\s*\d+.*$", "", t, flags=re.I).strip(" ，,。")
    return t


def _split_points(title, body):
    """把「标题里的顿号分句」和「正文里的编号条目」都拆成候选要点。"""
    cands = []
    # 标题：A、B、C（精选类标题常见结构）
    for seg in re.split(r"[、|｜;；]", title or ""):
        seg = _clean_point(seg)
        if 8 <= len(seg) <= 120:
            cands.append(seg)
    # 正文：1.xxx / 1、xxx / ①xxx / 多行
    text = (body or "").replace("\r", "")
    marks = [m.start() for m in re.finditer(r"(?:^|\s|。)(\d{1,2}[.、．)）]|[①②③④⑤⑥⑦⑧⑨⑩])", text)]
    chunks = []
    for i, start in enumerate(marks):
        end = marks[i + 1] if i + 1 < len(marks) else len(text)
        chunk = _clean_point(re.sub(r"^[\s。]*(\d{1,2}[.、．)）]|[①②③④⑤⑥⑦⑧⑨⑩])\s*", "", text[start:end]))
        if 8 <= len(chunk) <= 200:
            chunks.append(chunk)
    cands.extend(chunks[:10])
    # 去重 + 过滤
    out, seen = [], set()
    for c in cands:
        key = c[:30]
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def extract_points(title, body):
    """精选/日报类文章 → 按重要性排序的要点列表（最多 5 条）；非精选类返回 []。"""
    if not DIGEST_RX.search(title or "") and len(_split_points("", body)) < 3:
        return []
    cands = _split_points(title, body)
    if len(cands) < 2:
        return []

    def weight(p):
        w = sum(wt for kw, wt in _POINT_KW if kw.lower() in p.lower())
        if re.search(r"[$¥]|\d+\s*(亿|万|billion|million|B\b|M\b)", p, re.I):
            w += 1
        return w

    ranked = sorted(enumerate(cands), key=lambda x: (-weight(x[1]), x[0]))
    return [c for _, c in ranked[:5]]


# ---------------------------------------------------------------- 分级（硬条件也要过确认/时效/衰减三道门）
TIER_RANK = {"T0": 0, "T1": 1, "T2": 2, "T3": 3}


def independent_count(s):
    """独立源数：T3（聚合/搬运）不计入确认度，避免标题党被当多源确认。"""
    return sum(1 for t in (s.get("source_tiers") or {}).values() if t != "T3")


def grade_scored(scored, thresholds):
    """统一分级。

    规则（P0 硬条件不再是“命中就推”）：
    - 已确认（T0 单源 或 ≥2 个非 T3 独立源）+ 48h 内 + 衰减 D≥0.1 → P0
    - 已确认但超 48h（≤96h）→ P1；超 96h → 按分数归档（P2/ARCHIVE, backfill）
    - 未确认：只有近期且分数 ≥ P1 才 P1；否则按分数 P2/ARCHIVE
    """
    lv = thresholds["scoring"]["levels"]
    hard_cfg = thresholds["scoring"].get("hard", {})
    window = hard_cfg.get("window_hours", 48)
    stale_window = hard_cfg.get("stale_window_hours", 96)
    min_decay = hard_cfg.get("min_decay", 0.1)
    for s in scored:
        tiers = s.get("source_tiers") or {}
        best = min(tiers.values(), key=lambda t: TIER_RANK.get(t, 9)) if tiers else "T3"
        indep = independent_count(s)
        s["independent_sources"] = indep
        s["best_tier"] = best
        s["confirmed"] = indep >= 2 or best == "T0"
        s["unconfirmed"] = bool(s.get("hard")) and not s["confirmed"]
        age = s.get("age_hours")
        fresh = age is None or age <= window
        d = (s.get("components") or {}).get("D", 1.0)
        if s.get("hard"):
            if s["confirmed"] and fresh and d >= min_decay:
                s["level"] = "P0"
            elif s["confirmed"] and (age is None or age <= stale_window):
                s["level"] = "P1"
            elif best in ("T0", "T1", "T2") and fresh and d >= min_decay:
                # 单一可信源（T0/T1/T2）的近期硬事件：推到 P1 并标「未确认」，避免漏掉真新闻
                s["level"] = "P1"
            elif fresh and s["score"] >= lv["P1"] and best != "T3":
                # T3（聚合/Google News）永远只作线索，不能靠分数升 P1
                s["level"] = "P1"
            elif s["score"] >= lv["P2"]:
                s["level"] = "P2"
                s["backfill"] = True
            else:
                s["level"] = "ARCHIVE"
                s["backfill"] = True
        else:
            if s["score"] >= lv["P0"]:
                s["level"] = "P0"
            elif s["score"] >= lv["P1"]:
                s["level"] = "P1"
            elif s["score"] >= lv["P2"]:
                s["level"] = "P2"
            else:
                s["level"] = "ARCHIVE"
        # 美股/宏观：无 BTC/ETH/BNB 关联最高 P2；有关联最高 P1（不 P0）
        if s.get("category") == "macro":
            if not s.get("assets"):
                if s["level"] in ("P0", "P1"):
                    s["level"] = "P2"
                    s["backfill"] = True
            elif s["level"] == "P0":
                s["level"] = "P1"
        # T3（聚合/Google News/社区）永远只作线索：即使分数高也不高于 P2
        if s.get("best_tier") == "T3" and s["level"] in ("P0", "P1"):
            s["level"] = "P2"
            s["backfill"] = True
        # 页面更新类（html_diff 无正文）：最多 P2，避免“CSS 变了”被推成 P1
        if s.get("kind") == "page_update" and s["level"] in ("P0", "P1"):
            s["level"] = "P2"
            s["backfill"] = True
        # 组织动向（裁员/重组/高管离职）：只做“降级封顶”，不无条件升级
        # P0 → 已确认 P1 / 未确认 P2；P1 → 未确认降 P2；P2/ARCHIVE 保持
        if s.get("category") == "industry_labor":
            if s["level"] in ("P0", "P1"):
                s["level"] = "P1" if s.get("confirmed") else "P2"
    return scored


# ---------------------------------------------------------------- 主流程
def main():
    ensure_dirs()
    args = sys.argv[1:]
    date = today_str()
    for i, a in enumerate(args):
        if a == "--date" and i + 1 < len(args):
            date = args[i + 1]

    raw_path = os.path.join(RAW_DIR, date, "items.jsonl")
    if not os.path.exists(raw_path):
        print("没有找到 %s，请先跑 fetch.py" % raw_path)
        return

    thresholds = load_config("thresholds.yaml")
    max_age_days = thresholds.get("scoring", {}).get("max_age_days", 7)
    people = load_config("people.yaml").get("people", [])
    patterns, stable_rx = build_asset_patterns()
    labor_kw_rx, labor_emp_rx = build_labor_matchers()
    wl = load_config("watchlist.yaml")
    watch = [a["symbol"] for a in wl.get("assets", [])]

    items, seen_url = [], set()
    total = 0
    for line in open(raw_path, "r", encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        total += 1
        try:
            it = json.loads(line)
        except Exception:
            continue
        text = it.get("title", "") + " " + (it.get("body", "") or "")
        # 去重
        key = (it.get("url") or "") + "|" + (it.get("title") or "")
        if key in seen_url:
            continue
        seen_url.add(key)
        # 促销/营销活动直接丢弃
        promo = load_config("rules.yaml").get("promo", {})
        low_title = (it.get("title") or "").lower()
        if any(k in low_title for k in promo.get("keywords", [])) and not any(
                k in low_title for k in promo.get("exempt_keywords", [])):
            continue
        # 匹配标的
        assets = [sym for sym in watch if patterns.get(sym) and patterns[sym].search(text)]
        systemic = bool(stable_rx and stable_rx.search(text))
        # 组织动向（裁员/重组/高管离职）：即使没提 BTC/ETH/BNB 也保留，作为 P2 线索
        labor = bool(labor_kw_rx and labor_emp_rx and labor_kw_rx.search(text) and labor_emp_rx.search(text))
        macro = is_macro_news(text)
        if not assets and not systemic and not it.get("asset_symbols") and not labor and not macro:
            continue
        pub = parse_ts(it.get("published_at"))
        if pub and (datetime.datetime.now(pub.tzinfo) - pub).total_seconds() > max_age_days * 86400:
            continue  # 太旧的 RSS 历史条目直接丢
        it["_assets"] = assets if assets else it.get("asset_symbols", [])
        it["_systemic"] = systemic
        it["_labor"] = labor
        it["_macro"] = macro
        it["_sh"] = simhash(it.get("title", ""))
        items.append(it)

    def _clean_md(line):
        line = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", line)
        line = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", line)
        line = re.sub(r"\s+", " ", line).strip(" -*>\t")
        return line

    def social_snippets(it):
        raw_lines = [x.strip() for x in (it.get("body") or "").split("\n") if x.strip()]
        keep = []
        for raw in raw_lines:
            ln = _clean_md(raw)
            low = ln.lower()
            if len(ln) < 40:
                continue
            if re.search(r"pbs\.twimg\.com|twimg|abs\.twimg", low):
                continue
            if low.startswith(("title:", "url source:", "markdown content", "##", "image")):
                continue
            score = 0
            if any(p.search(ln) for p in patterns.values() if p):
                score += 2
            if re.search(r"listing|上币|delist|下架|regulat|监管|\bsec\b|etf|hack|被盗|burn|销毁|unlock|解锁|tvl|reserve|储备|withdraw|bnb|btc|eth", low):
                score += 1
            if score >= 2:
                keep.append((score, ln[:300]))
        keep.sort(key=lambda x: -x[0])
        return [x[1] for x in keep[:6]]

    news_items, social_hits, social_quiet = [], [], []
    for it in items:
        if it.get("kind") == "social":
            sn = social_snippets(it)
            if not sn:
                social_quiet.append(it)
                continue
            it["title"] = "%s 新帖：%s" % (it.get("handle") or it.get("source_name"), sn[0][:60])
            it["body"] = "\n".join(sn)
            it["_social"] = True
            social_hits.append(it)
            continue
        news_items.append(it)

    clusters = cluster_items(news_items)
    # 社交媒体每条单独成簇，绝不跨人合并
    clusters += [[it] for it in social_hits]
    scored = [score_cluster(c, thresholds, patterns) for c in clusters]

    for s in scored:
        s["hard"] = hard_triggers(" ".join(s["titles"]), s["meta"], s["assets"], people)
        s["backfill"] = False
        age_h = None
        pub = parse_ts(s.get("published_at"))
        if pub:
            age_h = max(0.0, (datetime.datetime.now(pub.tzinfo) - pub).total_seconds() / 3600)
        s["age_hours"] = round(age_h, 1) if age_h is not None else None
    grade_scored(scored, thresholds)
    scored.sort(key=lambda x: (-len(x["hard"]), -x["score"]))

    counts = {"P0": 0, "P1": 0, "P2": 0, "ARCHIVE": 0}
    for s in scored:
        counts[s["level"]] += 1

    # ---------- 报告 ----------
    lines = []
    lines.append("# 情报日报 %s" % date)
    lines.append("")
    lines.append("> 自动生成 · 零成本采集 · 本地日报（推送由 notify.py 负责）")
    lines.append("")
    lines.append("## 概览")
    lines.append("")
    lines.append("| 指标 | 数值 |")
    lines.append("|---|---|")
    lines.append("| 原始条目 | %d |" % total)
    lines.append("| 去重后匹配三标的 | %d |" % len(items))
    lines.append("| 事件簇 | %d |" % len(clusters))
    lines.append("| P0 | %d |" % counts["P0"])
    lines.append("| P1 | %d |" % counts["P1"])
    lines.append("| P2 | %d |" % counts["P2"])
    lines.append("| 归档 | %d |" % counts["ARCHIVE"])
    lines.append("")
    lines.append("> 说明：P0/P1 按评分与硬条件判定（含高管动态）；本报告为本地留档，Telegram 推送见 notify.py。"
                 "首次运行会回填最近 7 天，之后只处理增量，故部分条目看起来是\"旧闻\"。")
    lines.append("")

    for lvl in ("P0", "P1"):
        arr = [s for s in scored if s["level"] == lvl and not s.get("social")]
        lines.append("## %s（%d 条）" % (lvl, len(arr)))
        lines.append("")
        if not arr:
            lines.append("_今日无_")
            lines.append("")
            continue
        for s in arr:
            tags = " ".join(s["assets"]) or ("稳定币/系统性" if "stablecoin" in s["category"] else "—")
            lines.append("### [%s 分] %s" % (s["score"], s["titles"][0][:90]))
            lines.append("")
            conf = ("已确认（%d 独立源）" % s["independent_sources"]
                    if s.get("confirmed") else "未确认（低可信源）")
            lines.append("- 类别：%s · 标的：%s · 来源：%s（%s）" %
                         (_events.category_cn(s["category"]), tags, conf, ", ".join(s["sources"])))
            lines.append("- 时间：%s" % (s["published_at"] or "未知"))
            if s.get("cn_summary"):
                lines.append("- **中文解读**：%s" % s["cn_summary"])
            lines.append("- 评分：S=%s T=%s M=%s C=%s D=%s" % (
                s["components"]["S"], s["components"]["T"], s["components"]["M"],
                s["components"]["C"], s["components"]["D"]))
            if s["hard"]:
                lines.append("- **硬条件命中**：%s" % "、".join(_events.hard_cn(h) for h in s["hard"]))
            if s.get("points"):
                lines.append("- **要点**：")
                for i, pt in enumerate(s["points"], 1):
                    lines.append("  %d. %s" % (i, pt[:120]))
            if s["url"]:
                lines.append("- 链接：%s" % s["url"])
            lines.append("")
        lines.append("")

    lines.append("## 高管动态（%d 个账号有新内容）" % len(social_hits))
    lines.append("")
    if not social_hits:
        lines.append("_无_")
    for it in social_hits:
        lvl_s = next((x["level"] for x in scored if x.get("social") and x.get("sources") and
                      it.get("source_id") in x["sources"]), "—")
        lines.append("- **%s**（%s）：%s" % (it.get("handle") or it.get("source_name"), lvl_s, it.get("title", "")))
        for ln in (it.get("body") or "").split("\n")[:3]:
            if ln.strip():
                lines.append("  - %s" % ln.strip()[:160])
    lines.append("")

    lines.append("## P2 摘要（%d 条，仅列前 15）" % counts["P2"])
    lines.append("")
    for s in [x for x in scored if x["level"] == "P2" and not x.get("social")][:15]:
        mark = "（历史回填）" if s.get("backfill") else ""
        cn = ("〔%s〕" % s["cn_summary"][:50]) if s.get("cn_summary") else ""
        lines.append("- [%s] %s（%s）%s%s" % (s["score"], s["titles"][0][:80],
                                            ", ".join(s["assets"]) or "—", mark, cn))
    lines.append("")

    out_md = os.path.join(REPORT_DIR, "%s.md" % date)
    with open(out_md, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    alerts = {
        "date": date, "generated_at": ts_iso(),
        "counts": counts,
        "items": [s for s in scored if s["level"] in ("P0", "P1")],
    }
    safe_json_dump(os.path.join(STATE_DIR, "alerts-%s.json" % date), alerts)

    # 跨模块只读契约：当日 P0/P1/P2 摘要（crypto-analyst 消费；只增字段不改语义）
    export_dir = os.path.join(DATA_DIR, "exports")
    os.makedirs(export_dir, exist_ok=True)
    export_items = [
        {
            "severity": s["level"],
            "title": (s.get("titles") or [""])[0],
            "category": s.get("category"),
            "assets": s.get("assets") or [],
            "score": s.get("score"),
            "url": s.get("url"),
            "published_at": s.get("published_at"),
            "hard": s.get("hard") or [],
        }
        for s in scored if s["level"] in ("P0", "P1", "P2")
    ]
    safe_json_dump(os.path.join(export_dir, "daily-%s.json" % date), {
        "schema": "news/daily/v1",
        "source_module": "crypto-news",
        "date": date,
        "generated_at": ts_iso(),
        "counts": counts,
        "items": export_items[:100],
    })

    print("评分完成：原始 %d → 匹配 %d → 事件簇 %d" % (total, len(items), len(clusters)))
    print("P0=%d  P1=%d  P2=%d  归档=%d" % (counts["P0"], counts["P1"], counts["P2"], counts["ARCHIVE"]))
    print("报告：%s" % out_md)


if __name__ == "__main__":
    main()
