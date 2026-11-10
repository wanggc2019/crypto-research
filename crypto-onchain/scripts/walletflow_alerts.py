#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""walletflow_alerts.py —— 链上大额转账的**事件驱动告警**（Telegram）。

设计目标（设计约束）：
- 事件驱动：每轮新出现的大额转账达到阈值就发，不等日报；
- 不刷屏：常规 ≤10 条消息/天；当天大额笔数激增时按笔数扩容（有硬上限）；
- 分级：P0（≥$50M，或转入交易所且 ≥$25M）；其余 P1。默认全天即时（quiet_hours 空）；
- 允许"猜"：方向 unknown 时可在括号里标注**推测**（如"疑似与 Binance 关联的地址"），
  猜不到就不写；推测永远带"疑似/推测"字样，不作为结论。

状态文件：data/state/walletflow-alerts.json
  {"date": "...", "messages": n, "sent": [{key,severity,usd,ts}],
   "suppressed": [{key,severity,usd,reason}], "pending": [...], "last_sent_at": "..."}
"""
import datetime
import os
import re
import socket
import time
import urllib.error
import urllib.request

import context as market_context

import common
from crypto_core import timeutil
from crypto_core.notify import check_credentials, esc, job_label, send_message

STATE_FILE = "state/walletflow-alerts.json"
TAG_CACHE = "state/address-tags.json"
PENDING_MAX = 20
ETHERSCAN_TAG_RE = re.compile(r'\n\t([A-Za-z0-9][A-Za-z0-9 .,:&_()/\-]{1,60})\s*\|\s*Address:')


# ---------------------------------------------------------------- 状态
def _state_path(paths):
    return os.path.join(paths["root"], STATE_FILE)


def load_state(paths):
    data = common.safe_json_load(_state_path(paths), {})
    return data if isinstance(data, dict) else {}


def save_state(paths, state):
    try:
        common.write_json(_state_path(paths), state)
    except OSError:
        pass


def _reset_daily(state, today):
    if str(state.get("date") or "") != today:
        state = {"date": today, "messages": 0, "sent": [], "suppressed": [], "pending": []}
    state.setdefault("sent", [])
    state.setdefault("suppressed", [])
    state.setdefault("pending", [])
    state.setdefault("messages", 0)
    return state


def _load_tag_cache(paths):
    data = common.safe_json_load(os.path.join(paths["root"], TAG_CACHE), {})
    return data if isinstance(data, dict) else {}


def _save_tag_cache(paths, cache):
    try:
        common.write_json(os.path.join(paths["root"], TAG_CACHE), cache)
    except OSError:
        pass


def parse_etherscan_tag(html):
    m = ETHERSCAN_TAG_RE.search(html or "")
    return m.group(1).strip() if m else ""


def fetch_etherscan_tag(addr, timeout=6):
    """取 Etherscan 地址页 name tag（只对 ETH 地址；失败返回空串，绝不抛）。"""
    url = "https://etherscan.io/address/%s" % addr
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/124.0"})
    try:
        html = urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "ignore")
    except (urllib.error.URLError, socket.timeout, OSError, ValueError):
        return ""
    return parse_etherscan_tag(html)


def enrich_tags(records, paths, cfg):
    """给告警里出现的未知地址补 Etherscan 标签（带 30 天缓存；best-effort）。"""
    enrich = cfg.get("enrich") or {}
    if not enrich.get("etherscan_tags", True):
        return {}
    ttl = int(enrich.get("cache_days") or 30) * 86400
    timeout = int(enrich.get("timeout_seconds") or 6)
    cache = _load_tag_cache(paths)
    now = int(time.time())
    out, dirty = {}, False
    for rec in records:
        for side in ("from", "to"):
            addr = str(rec.get(side) or "").lower()
            if not addr or set(addr) == {"0"}:
                continue
            item = cache.get(addr) or {}
            if item.get("ts") and now - int(item["ts"]) < ttl:
                if item.get("tag"):
                    out[addr] = item["tag"]
                continue
            tag = fetch_etherscan_tag(addr, timeout=timeout) if str(rec.get("chain")) == "ethereum" else ""
            cache[addr] = {"tag": tag, "ts": now}
            dirty = True
            if tag:
                out[addr] = tag
    if dirty:
        _save_tag_cache(paths, cache)
    return out


def _key(rec):
    return "%s:%s" % (rec.get("tx"), rec.get("log_index"))


# ---------------------------------------------------------------- 历史（用于"猜"）
def build_history(raw_dir, labels, days=7):
    """从最近 raw/<date>/walletflow.jsonl 统计地址与已知交易所的关联次数、出现频次。

    返回 {addr: {"links": {exchange_name: count}, "count": n}}；只用于低置信"推测"。
    """
    hist = {}
    root = str(raw_dir or "")
    if not os.path.isdir(root):
        return hist
    names = sorted(os.listdir(root), reverse=True)[:int(days)]
    for name in names:
        path = os.path.join(root, name, "walletflow.jsonl")
        for row in common.read_jsonl(path):
            frm, to = str(row.get("from") or "").lower(), str(row.get("to") or "").lower()
            item_f = hist.setdefault(frm, {"links": {}, "count": 0}) if frm else None
            item_t = hist.setdefault(to, {"links": {}, "count": 0}) if to else None
            if item_f:
                item_f["count"] += 1
            if item_t:
                item_t["count"] += 1
            lf, lt = labels.get(frm) or {}, labels.get(to) or {}
            if item_f and str(lt.get("type")) == "cex":
                item_f["links"][lt.get("name")] = item_f["links"].get(lt.get("name"), 0) + 1
            if item_t and str(lf.get("type")) == "cex":
                item_t["links"][lf.get("name")] = item_t["links"].get(lf.get("name"), 0) + 1
    return hist


def guess_counterparty(record, labels, history):
    """低置信推测（只用于 unknown 的括号备注）；猜不到返回 None。"""
    if not history:
        return None
    sides = [str(record.get("from") or "").lower(), str(record.get("to") or "").lower()]
    for addr in sides:
        if not addr or addr in labels or set(addr) == {"0"}:
            continue
        item = history.get(addr) or {}
        links = item.get("links") or {}
        if links:
            name, count = max(links.items(), key=lambda kv: kv[1])
            if count >= 3:
                return "疑似与 %s 关联的地址" % name
        if item.get("count", 0) >= 5:
            return "疑似做市商/高频机构地址"
    return None


# ---------------------------------------------------------------- 分级/限额/静默
def is_whale(record, cfg):
    """巨鲸级转账：单笔 ≥whale_usd，或转入交易所 ≥whale_cex_in_usd（不受常规 10 条限制）。"""
    usd = float(record.get("usd") or 0)
    if usd >= float(cfg.get("whale_usd") or 100_000_000):
        return True
    return record.get("direction") == "cex_in" and usd >= float(cfg.get("whale_cex_in_usd") or 50_000_000)


def severity_of(record, cfg):
    usd = float(record.get("usd") or 0)
    if usd >= float(cfg.get("p0_usd") or 50000000):
        return "P0"
    if record.get("direction") == "cex_in" and usd >= float(cfg.get("p0_cex_in_usd") or 25000000):
        return "P0"
    return "P1"


def daily_cap(cfg, today_large_count):
    """常规 daily_cap；当天大额笔数超过 daily_cap×ratio 后按笔数扩容，硬上限 burst_cap。"""
    base = int(cfg.get("daily_cap") or 10)
    burst = int(cfg.get("burst_cap") or 30)
    ratio = float(cfg.get("burst_trigger_ratio") or 2)
    trigger = int(base * ratio)
    n = int(today_large_count or 0)
    if n <= trigger:
        return base
    return min(burst, base + (n - trigger) // 2)


def special_mode(cfg, today_p0, context=None):
    """特殊时期判定：命中任一条件 → 解除日限额（仍受去重/单轮上限约束）。

    计数条件用「当日 P0 笔数」而不是全部大额笔数：普通活跃日 ≥$10M 可达数百笔，
    按全部笔数会把普通日误判为特殊期、导致限额被解除（2026-11-10 实测 32 条）。
    """
    sp = cfg.get("special") or {}
    if not sp.get("enabled", True):
        return False
    if sp.get("manual"):
        return True
    if int(today_p0 or 0) >= int(sp.get("p0_transfers_today") or 300):
        return True
    ctx = context or {}
    try:
        btc = ctx.get("btc_change_24h")
        if btc is not None and abs(float(btc)) >= float(sp.get("btc_move_24h_pct") or 6.0):
            return True
        cex = ctx.get("cex_net_24h_bps")
        if cex is not None and float(cex) <= -float(sp.get("cex_outflow_bps") or 150):
            return True
        st = ctx.get("stablecoin_net_issue_24h_usd")
        if st is not None and float(st) <= float(sp.get("stablecoin_outflow_usd") or -1500000000):
            return True
    except (TypeError, ValueError):
        return False
    return False


def _parse_hours(span):
    try:
        a, b = str(span or "22:00-07:00").split("-", 1)
        ah, am = [int(x) for x in a.split(":", 1)]
        bh, bm = [int(x) for x in b.split(":", 1)]
        return (ah * 60 + am, bh * 60 + bm)
    except (TypeError, ValueError):
        return (22 * 60, 7 * 60)


def in_quiet_hours(now, span):
    if not span or str(span).strip().lower() in ("off", "none", "0"):
        return False
    start, end = _parse_hours(span)
    cur = now.hour * 60 + now.minute
    if start <= end:
        return start <= cur < end
    return cur >= start or cur < end


# ---------------------------------------------------------------- 选择/渲染
def _fmt_usd(value):
    v = float(value or 0)
    if abs(v) >= 1e6:
        return "%.2fM" % (v / 1e6)
    return "%.0fK" % (v / 1e3)


ENTITY_TYPE_CN = {"cex": "交易所", "mm": "做市商", "issuer": "发行方"}
CEX_TAG_HINTS = ("binance", "coinbase", "okx", "kraken", "bybit", "bitfinex", "deribit",
                 "gate", "kucoin", "huobi", "htx", "gemini", "crypto.com", "bitstamp",
                 "upbit", "bithumb")


def _entity(addr, labels, tags):
    """地址 → 人话实体："交易所 Binance 14" / "做市商 Jump Trading" / 浏览器标签 / "未知"。"""
    addr_l = str(addr or "").lower()
    label = labels.get(addr_l) or {}
    if label.get("name"):
        typ = ENTITY_TYPE_CN.get(str(label.get("type")), "")
        return ("%s %s" % (typ, label["name"])).strip()
    tag = str(tags.get(addr_l) or "").strip()
    if tag:
        low = tag.lower()
        if any(k in low for k in CEX_TAG_HINTS):
            return "交易所 %s" % tag
        if "circle" in low or "tether" in low:
            return "发行方 %s" % tag
        return tag
    return "未知"


def _short(addr):
    a = str(addr or "")
    return (a[:6] + "…" + a[-4:]) if len(a) > 12 else a


def _label_of(labels, addr):
    return labels.get(str(addr or "").lower()) or {}


def _group_records(records):
    """同一对 from→to（同币种）的多笔合并成一行，避免同一交易对手刷屏。"""
    groups, index = [], {}
    for rec in records:
        key = (str(rec.get("from") or "").lower(), str(rec.get("to") or "").lower(),
               str(rec.get("symbol") or ""))
        if key in index:
            g = groups[index[key]]
            g["count"] += 1
            g["usd"] += float(rec.get("usd") or 0)
            if rec.get("direction") != g["direction"]:
                g["direction"] = "mixed"
        else:
            index[key] = len(groups)
            groups.append({"rec": rec, "count": 1, "usd": float(rec.get("usd") or 0),
                           "direction": rec.get("direction") or "unknown"})
    return groups


def _fmt_amount(rec):
    try:
        amount = float(rec.get("amount") or 0)
        usd = float(rec.get("usd") or 0)
        if amount > 0 and usd > 0:
            return "{:,.2f} {}".format(amount, str(rec.get("symbol") or ""))
        return "%s" % str(rec.get("symbol") or "")
    except (TypeError, ValueError):
        return str(rec.get("symbol") or "")


def _tx_url(rec):
    tx = str(rec.get("tx") or "")
    if not tx:
        return ""
    base = "https://bscscan.com/tx/" if rec.get("chain") == "bsc" else "https://etherscan.io/tx/"
    return base + tx


def render_message(records, cfg, labels, history, now, special=False, tags=None, today=None, tx_legs=None):
    tags = tags or {}
    tx_legs = tx_legs or {}
    today = today or {}
    lines = ["<b>【crypto-onchain】链上大额转账</b>",
             "%s · 本轮 %d 笔%s" % (timeutil.fmt_local(now, "%m-%d %H:%M"), len(records),
                                   " · ⚠ 特殊期：日限额已解除" if special else "")]
    for g in _group_records(records):
        rec = g["rec"]
        sev = severity_of(rec, cfg)
        icon = "🔴" if sev == "P0" else "🟡"
        lines.append("")
        amount = "<b>$%s</b>" % _fmt_usd(g["usd"])
        if g["count"] > 1:
            amount += "（%d 笔）" % g["count"]
        whale_tag = " · 🐋 巨鲸" if rec.get("_whale") else ""
        lines.append("%s <b>%s</b> %s %s · %s%s" % (
            icon, sev, esc(str(rec.get("symbol") or "")), amount,
            esc(str(rec.get("chain") or "").upper() or "ETH"), whale_tag))
        lines.append("代币数量：%s ｜ 区块：%s" % (
            esc(_fmt_amount(rec)), esc(str(rec.get("block") or "?"))))
        lines.append("%s → %s" % (esc(_short(rec.get("from"))), esc(_short(rec.get("to")))))
        # 地址标签（内置 + 浏览器 name tag）
        def _tag(addr):
            addr_l = str(addr or "").lower()
            lbl = labels.get(addr_l) or {}
            if lbl.get("name"):
                return str(lbl["name"])
            return tags.get(addr_l) or ""

        from_tag, to_tag = _tag(rec.get("from")), _tag(rec.get("to"))
        if from_tag or to_tag:
            parts = []
            if from_tag:
                parts.append("from: %s" % from_tag)
            if to_tag:
                parts.append("to: %s" % to_tag)
            lines.append("地址标签：%s" % esc(" / ".join(parts)))
        else:
            lines.append("地址标签：双方均无公开标签")
        direction = str(g.get("direction") or "unknown")
        if direction == "cex_in":
            desc = "cex_in · 转入交易所"
        elif direction == "cex_out":
            desc = "cex_out · 转出交易所"
        elif direction == "cex_internal":
            desc = "cex_internal · 交易所内部划转"
        else:
            desc = "unknown · 无法判断买卖方向"
        # 流向与方向合并成一行：未知 → 未知（unknown · 无法判断买卖方向）
        flow_line = "流向：%s → %s（%s）" % (
            esc(_entity(rec.get("from"), labels, tags)),
            esc(_entity(rec.get("to"), labels, tags)),
            esc(desc),
        )
        if direction in ("unknown", "") and cfg.get("guess", True):
            gs = guess_counterparty(rec, labels, history)
            if gs:
                flow_line += "（推测：%s）" % esc(gs)
        lines.append(flow_line)
        # 背景：今日累计 + 该地址 7 天出现次数 + 同 tx 多腿
        bg = []
        if today.get("n"):
            pct = (float(rec.get("usd") or 0) / float(today.get("usd") or 0) * 100.0) if today.get("usd") else 0
            bg.append("今日第 %d 笔大额（累计 %d 笔 / $%s，占 %.1f%%）" % (
                today.get("n"), today.get("n"), _fmt_usd(today.get("usd")), pct))
        freq = (history.get(str(rec.get("from") or "").lower()) or {}).get("count", 0)
        freq2 = (history.get(str(rec.get("to") or "").lower()) or {}).get("count", 0)
        if freq or freq2:
            bg.append("近 7 天 from 出现 %d 次 / to 出现 %d 次" % (freq, freq2))
        legs = tx_legs.get(str(rec.get("tx") or ""), 1)
        if legs > 1:
            bg.append("同一 tx 共 %d 腿，仅报最大一腿" % legs)
        if bg:
            lines.append("背景：%s" % esc("；".join(bg)))
        why = rec.get("_why") or []
        if why:
            lines.append("推送理由：%s" % esc("；".join(why[:3])))
        url = _tx_url(rec)
        if url:
            lines.append("交易：%s" % esc(url))
    lines.append("")
    lines.append("🏷 任务：%s" % job_label("crypto-onchain"))
    return "\n".join(lines)


def select_alerts(records, cfg, state, now, labels, history, today_large_count, context=None,
                  today_p0=0):
    """从本轮记录中挑选要发的告警；返回 (to_send, suppressed, state)。

    - 只处理 ≥ alerts.min_usd 且未出现过的记录；
    - pending（上轮发送失败）优先重试，最多 PENDING_MAX 条；
    - P0 受 burst_cap 限制；P1 受动态 daily_cap 限制；夜间 P1 抑制；
    - 单条消息最多 max_per_round 笔，其余记 suppressed（reason=round_limit）。
    """
    today = now.strftime("%Y-%m-%d")
    state = _reset_daily(dict(state or {}), today)
    sent_keys = {str(x.get("key")) for x in state["sent"]}
    sup_keys = {str(x.get("key")) for x in state["suppressed"]}
    candidates = []
    for rec in list(state.get("pending") or []) + list(records or []):
        key = _key(rec)
        if not key or key in sent_keys or key in sup_keys:
            continue
        if float(rec.get("usd") or 0) < float(cfg.get("min_usd") or 0):
            continue
        candidates.append(rec)
    # 同一 tx 可能有多腿（如原子换手：A→B 与 B→A 各一条），告警只保留最大一腿，避免重复刷屏
    source, by_tx, candidates = candidates, {}, []
    for rec in source:
        key_tx = str(rec.get("tx") or "")
        if not key_tx:
            candidates.append(rec)
            continue
        prev = by_tx.get(key_tx)
        if prev is None or float(rec.get("usd") or 0) > float(prev.get("usd") or 0):
            by_tx[key_tx] = rec
    candidates.extend(by_tx.values())
    candidates.sort(key=lambda r: float(r.get("usd") or 0), reverse=True)

    special = special_mode(cfg, today_p0, context)
    only_p0 = bool(cfg.get("only_p0"))
    if special:
        # 特殊期解除常规 10 条，但仍有硬上限（默认 30），避免极端日刷屏
        cap = hard = int((cfg.get("special") or {}).get("max_messages") or 30)
    elif only_p0:
        # 只推 P0 时仍遵守「常规 ≤10 条消息/天」；特殊期才解除（设计约束）
        cap = hard = int(cfg.get("daily_cap") or 10)
    else:
        cap = daily_cap(cfg, today_large_count)
        hard = int(cfg.get("burst_cap") or 30)
    max_round = int(cfg.get("max_per_round") or 5)
    quiet = in_quiet_hours(now, cfg.get("quiet_hours") or "")
    to_send, suppressed = [], []
    for rec in candidates:
        sev = severity_of(rec, cfg)
        whale = is_whale(rec, cfg)
        # 智能判断：没有价格/新闻/资金面上下文的大额转账不打扰（巨鲸始终发）
        verdict = market_context.assess(rec, cfg, context or {}, now)
        if not verdict.get("send"):
            suppressed.append({"key": _key(rec), "severity": sev, "usd": rec.get("usd"),
                               "reason": "low_context", "score": verdict.get("score") or 0})
            sup_keys.add(_key(rec))
            continue
        rec = dict(rec)
        rec["_why"] = verdict.get("reasons") or []
        rec["_whale"] = bool(verdict.get("whale") or whale)
        if only_p0 and sev != "P0":
            suppressed.append({"key": _key(rec), "severity": sev,
                               "usd": rec.get("usd"), "reason": "only_p0"})
            sup_keys.add(_key(rec))
            continue
        if quiet and sev != "P0":
            suppressed.append({"key": _key(rec), "severity": sev,
                               "usd": rec.get("usd"), "reason": "quiet_hours"})
            sup_keys.add(_key(rec))
            continue
        if special:
            limit = cap
        elif only_p0:
            # 普通 P0 用 daily_cap；巨鲸额外保留 whale_daily_cap 条，避免发满后漏掉大额
            limit = int(cfg.get("daily_cap") or 10) + (int(cfg.get("whale_daily_cap") or 5) if rec.get("_whale") else 0)
        else:
            limit = hard if sev == "P0" else cap
        capped = limit is not None and state["messages"] >= limit
        if capped or len(to_send) >= max_round:
            suppressed.append({"key": _key(rec), "severity": sev, "usd": rec.get("usd"),
                               "reason": "daily_cap" if capped else "round_limit"})
            sup_keys.add(_key(rec))
            continue
        to_send.append(rec)
    state["suppressed"].extend(suppressed)
    return to_send, suppressed, state


def _hourly_net_flows(paths, days=8):
    """按 (date,hour) 汇总 交易所净流入（cex_in - cex_out，USD）。"""
    out = {}
    root = paths.get("raw") or ""
    names = sorted(os.listdir(root))[-days:] if os.path.isdir(root) else []
    for name in names:
        path = os.path.join(root, name, "walletflow.jsonl")
        for r in common.read_jsonl(path):
            ts = r.get("ts")
            try:
                hour = int(datetime.datetime.fromtimestamp(int(ts)).hour)
            except (TypeError, ValueError, OSError):
                continue
            key = (name, hour)
            net = out.get(key, 0.0)
            usd = float(r.get("usd") or 0)
            if r.get("direction") == "cex_in":
                net += usd
            elif r.get("direction") == "cex_out":
                net -= usd
            out[key] = net
    return out


def flow_anomaly(paths, cfg, now):
    """最近完整 1 小时交易所净流入是否 > 7 日同时段均值 × ratio，且超过 min_usd。"""
    rare = cfg.get("rare") or {}
    ratio = float(rare.get("flow_anomaly_ratio") or 3)
    min_usd = float(rare.get("flow_min_usd") or 100_000_000)
    today = now.strftime("%Y-%m-%d")
    cur_hour = now.hour - 1 if now.hour > 0 else 23
    prev_day = (now - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    flows = _hourly_net_flows(paths, days=8)
    latest = flows.get((today, cur_hour)) or flows.get((prev_day, cur_hour)) or 0.0
    history = [v for (d, h), v in flows.items()
               if h == cur_hour and d != today and d != prev_day and v > 0]
    if not history:
        return None
    baseline = sum(history) / len(history)
    if latest <= 0 or latest < min_usd or baseline <= 0 or latest < baseline * ratio:
        return None
    return {"net_usd": latest, "baseline_usd": baseline, "ratio": latest / baseline,
            "hour": cur_hour}


def _rare_process(records, cfg, paths, labels, dry_run, now):
    """B 模式：只发两类极少数异常——单向未知 ≥rare.min_usd；1h 交易所净流入异常。"""
    rare = cfg.get("rare") or {}
    min_usd = float(rare.get("min_usd") or 500_000_000)
    daily_cap = int(rare.get("daily_cap") or 2)
    state = _reset_daily(load_state(paths), now.strftime("%Y-%m-%d"))
    sent_keys = {str(x.get("key")) for x in state.get("sent") or []}
    anomaly = flow_anomaly(paths, cfg, now)
    picks, seen_tx = [], set()
    for rec in sorted(records or [], key=lambda r: -float(r.get("usd") or 0)):
        if str(rec.get("direction")) != "unknown":
            continue
        if float(rec.get("usd") or 0) < min_usd:
            continue
        key = _key(rec)
        tx = str(rec.get("tx") or "")
        if key in sent_keys or tx in seen_tx:
            continue
        seen_tx.add(tx)
        picks.append(rec)
        if len(picks) >= 2:
            break
    if not anomaly and not picks:
        return {"sent": False, "n": 0, "text": "", "error": None, "suppressed": 0}
    if int(state.get("messages") or 0) >= daily_cap:
        return {"sent": False, "n": 0, "text": "", "error": None, "suppressed": len(picks)}

    lines = ["<b>🚨【crypto-onchain】链上异常</b>", now.strftime("%m-%d %H:%M")]
    if anomaly:
        lines.append("")
        lines.append("· 交易所 1 小时净流入 <b>$%.0fM</b>（7 日同时段均值 $%.0fM，%.1f 倍）"
                     % (anomaly["net_usd"] / 1e6, anomaly["baseline_usd"] / 1e6, anomaly["ratio"]))
    for rec in picks:
        lines.append("")
        url = _tx_url(rec) or ""
        lines.append("· 单向未知 <b>$%.1fM</b> %s（%s）" % (
            float(rec.get("usd") or 0) / 1e6, str(rec.get("symbol") or ""),
            esc(str(rec.get("chain") or "").upper())))
        lines.append("  %s → %s" % (esc(_short(rec.get("from"))), esc(_short(rec.get("to")))))
        if url:
            lines.append("  %s" % esc(url))
    lines.append("")
    lines.append("仅重大异常才即时提醒；日常分析见每晚 21:00 链上情报。")
    text = "\n".join(lines)
    if dry_run:
        return {"sent": False, "n": len(picks) + (1 if anomaly else 0), "text": text,
                "error": None, "suppressed": 0}
    env = common.load_bot_env()
    token, chat, problems = check_credentials(env)
    if problems:
        return {"sent": False, "n": 0, "text": text,
                "error": "凭据不完整：%s" % "；".join(problems), "suppressed": 0}
    ok, err = send_message(token, chat, text, disable_preview=True)
    if not ok:
        return {"sent": False, "n": 0, "text": text, "error": err, "suppressed": 0}
    for rec in picks:
        state.setdefault("sent", []).append({"key": _key(rec), "severity": "rare",
                                             "usd": rec.get("usd"), "ts": now.isoformat(timespec="seconds")})
    state["messages"] = int(state.get("messages") or 0) + 1
    state["last_sent_at"] = now.isoformat(timespec="seconds")
    save_state(paths, state)
    return {"sent": True, "n": len(picks) + (1 if anomaly else 0), "text": text,
            "error": None, "suppressed": 0}


def process(records, cfg, paths, labels, dry_run=False, now=None, context=None):
    """端到端：读状态 → 选告警 → 发送/预览 → 写状态。

    返回 {"sent": bool, "n": int, "text": str, "error": str|None, "suppressed": int}
    """
    now = now or common.now()
    if cfg.get("rare_only"):
        return _rare_process(records, cfg, paths, labels, dry_run, now)
    state = load_state(paths)
    today = now.strftime("%Y-%m-%d")
    # 今日大额笔数（用于扩容日限额）：从 raw 今日明细统计（与记录阈值一致）
    raw_today = os.path.join(paths["raw"], today, "walletflow.jsonl")
    _rows_today = common.read_jsonl(raw_today)
    today_large = len(_rows_today)
    p0_usd = float(cfg.get("p0_usd") or 50000000)
    p0_cex_in = float(cfg.get("p0_cex_in_usd") or 25000000)
    today_p0 = sum(
        1 for r in _rows_today
        if float(r.get("usd") or 0) >= p0_usd
        or (r.get("direction") == "cex_in" and float(r.get("usd") or 0) >= p0_cex_in)
    )
    history = build_history(paths["raw"], labels)
    to_send, suppressed, state = select_alerts(
        records, cfg, state, now, labels, history, today_large, context=context,
        today_p0=today_p0,
    )
    special = special_mode(cfg, today_p0, context)
    if not to_send:
        state["pending"] = []
        if not dry_run:
            save_state(paths, state)
        return {"sent": False, "n": 0, "text": "", "error": None, "suppressed": len(suppressed)}
    # dry-run 预览不联网（避免测试/回放访问 Etherscan）；真实发送才补浏览器标签
    tags = {} if dry_run else enrich_tags(to_send, paths, cfg)
    today_usd = sum(float(r.get("usd") or 0) for r in common.read_jsonl(raw_today))
    today_ctx = {"n": today_large, "usd": today_usd}
    tx_legs = {}
    for r in records or []:
        k = str(r.get("tx") or "")
        if k:
            tx_legs[k] = tx_legs.get(k, 0) + 1
    text = render_message(to_send, cfg, labels, history, now, special=special,
                          tags=tags, today=today_ctx, tx_legs=tx_legs)
    if dry_run:
        return {"sent": False, "n": len(to_send), "text": text, "error": None,
                "suppressed": len(suppressed)}
    env = common.load_bot_env()
    token, chat, problems = check_credentials(env)
    if problems:
        state["pending"] = (state.get("pending") or []) + to_send
        state["pending"] = state["pending"][:PENDING_MAX]
        save_state(paths, state)
        return {"sent": False, "n": len(to_send), "text": text,
                "error": "凭据不完整：%s" % "；".join(problems), "suppressed": len(suppressed)}
    ok, err = send_message(token, chat, text, disable_preview=True)
    if not ok:
        state["pending"] = (state.get("pending") or []) + to_send
        state["pending"] = state["pending"][:PENDING_MAX]
        save_state(paths, state)
        return {"sent": False, "n": len(to_send), "text": text, "error": err,
                "suppressed": len(suppressed)}
    for rec in to_send:
        state["sent"].append({"key": _key(rec), "severity": severity_of(rec, cfg),
                              "usd": rec.get("usd"), "ts": now.strftime("%Y-%m-%dT%H:%M:%S")})
    state["messages"] = int(state.get("messages") or 0) + 1
    state["last_sent_at"] = now.strftime("%Y-%m-%dT%H:%M:%S")
    state["pending"] = []
    save_state(paths, state)
    return {"sent": True, "n": len(to_send), "text": text, "error": None,
            "suppressed": len(suppressed)}
