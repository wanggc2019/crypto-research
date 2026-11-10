#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""holdings.py —— 已知地址持仓监控的**纯逻辑层**（离线可测，不联网、不写盘）。

包含：BTC/ETH/ERC20 余额解析、配置条目规范化与地址标签来源校验、变化百分比、缓存 TTL 判定、
价格解析、阈值事件与告警文案渲染。网络取数/缓存落盘/CLI 在 `holdings_collect.py`。

边界（与需求一致）：
- 只处理**人工写进 `config/holdings-watchlist.yaml` 的已知地址**；不做地址发现/归属推断；
- 地址标签只能来自配置 `label` + `source_note`（人工标注来源）；缺 `source_note` 的条目会被拒绝；
- 未核实地址必须标注「示例/未核实」，禁止冒充真实机构。
"""
from crypto_core.notify import esc  # noqa: F401

ERC20_BALANCE_SELECTOR = "0x70a08231"
DEFAULT_THRESHOLD_PCT = 5.0
VALID_CHAINS = ("btc", "eth", "erc20")
SATOSHI = 100000000



# ---------------------------------------------------------------- 纯解析（离线可测）
def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_btc_address(payload):
    """blockstream/mempool 的 `/address/<addr>` 响应 → 余额（satoshi 差值）。

    余额 = chain_stats.funded_txo_sum − chain_stats.spent_txo_sum（单位 satoshi）；
    mempool_stats 不并入（未确认余额单独存在，避免把待确认出入账算成持仓变化）。
    """
    if not isinstance(payload, dict):
        return {"ok": False, "balance": None, "error": "响应不是对象"}
    stats = payload.get("chain_stats")
    if not isinstance(stats, dict):
        return {"ok": False, "balance": None, "error": "缺少 chain_stats"}
    funded = _as_int(stats.get("funded_txo_sum"))
    spent = _as_int(stats.get("spent_txo_sum"))
    if funded is None or spent is None:
        return {"ok": False, "balance": None, "error": "funded/spent 不是整数"}
    sats = funded - spent
    if sats < 0:
        return {"ok": False, "balance": None, "error": "余额为负（数据异常）"}
    return {
        "ok": True,
        "balance": sats / float(SATOSHI),
        "balance_sat": sats,
        "tx_count": _as_int(stats.get("tx_count")) or 0,
        "error": None,
    }


def parse_hex_quantity(value):
    """十六进制数量（`0x...`）→ int；非法返回 None。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text.lower().startswith("0x"):
        return None
    try:
        return int(text, 16)
    except ValueError:
        return None


def parse_eth_balance(result_hex):
    """`eth_getBalance` 的 result（wei，hex）→ ETH。"""
    wei = parse_hex_quantity(result_hex)
    if wei is None:
        return {"ok": False, "balance": None, "error": "余额不是十六进制数量"}
    return {"ok": True, "balance": wei / float(10**18), "wei": wei, "error": None}


def erc20_balance_of_data(address):
    """构造 `balanceOf(address)` 的 eth_call data；地址非法返回 None。"""
    h = str(address or "").strip().lower()
    if h.startswith("0x"):
        h = h[2:]
    if len(h) != 40 or any(c not in "0123456789abcdef" for c in h):
        return None
    return ERC20_BALANCE_SELECTOR + h.rjust(64, "0")


def parse_erc20_balance(result_hex, decimals):
    """`eth_call` 的 result（uint256 hex）→ 代币数量（按 decimals 换算）。"""
    raw = parse_hex_quantity(result_hex)
    dec = _as_int(decimals)
    if raw is None:
        return {"ok": False, "balance": None, "error": "balanceOf 返回不是十六进制数量"}
    if dec is None or dec < 0 or dec > 36:
        return {"ok": False, "balance": None, "error": "decimals 非法：%r" % (decimals,)}
    return {"ok": True, "balance": raw / float(10**dec), "raw": raw, "error": None}


def compute_change_pct(prev, cur):
    """相对上一次实际采集的变化百分比；prev 缺失/为 0 时返回 None（无法定义变化率）。"""
    prev_f, cur_f = _as_float(prev), _as_float(cur)
    if prev_f is None or cur_f is None or prev_f == 0:
        return None
    return (cur_f - prev_f) / abs(prev_f) * 100.0


def is_fresh(state, now_ts, ttl_seconds):
    """缓存是否在 TTL 内；状态缺失/时间非法/ttl<=0 一律视为过期。"""
    updated = _as_int((state or {}).get("updated_ts"))
    ttl = _as_int(ttl_seconds)
    if updated is None or ttl is None or ttl <= 0:
        return False
    age = int(now_ts) - updated
    return 0 <= age < ttl


def price_for(entry, price_ctx):
    """条目价格：固定 price_usd → crypto-price exports → None（只报数量）。"""
    fixed = _as_float(entry.get("price_usd"))
    if fixed is not None and fixed > 0:
        return fixed
    asset = entry.get("price_asset") or (entry.get("token") or {}).get("symbol") or entry.get("chain", "").upper()
    row = (((price_ctx or {}).get("assets") or {}).get(str(asset).upper())) or {}
    price = _as_float(row.get("price"))
    return price if price is not None and price > 0 else None


def _symbol_of(entry):
    if entry.get("chain") == "erc20":
        return str((entry.get("token") or {}).get("symbol") or "ERC20")
    return entry.get("chain", "").upper()


def _valid_hex_address(value):
    h = str(value or "").strip().lower()
    if h.startswith("0x"):
        h = h[2:]
    return len(h) == 40 and all(c in "0123456789abcdef" for c in h)


def entry_key(entry):
    """状态/历史里的稳定键：btc 地址大小写敏感，EVM 地址统一小写。"""
    address = str(entry.get("address") or "")
    if entry.get("chain") != "btc":
        address = address.lower()
    return "%s:%s" % (entry.get("chain"), address)


def normalize_entries(watchlist):
    """配置 → 规范化条目 + problems。缺 label/source_note 的条目直接跳过（防编造归属）。"""
    rows = (watchlist or {}).get("entries")
    if rows is None:
        return [], []
    if not isinstance(rows, list):
        return [], ["entries 必须是列表"]
    entries, problems, seen = [], [], set()
    for idx, raw in enumerate(rows, 1):
        entry, problem = _normalize_entry(raw)
        if problem:
            problems.append("entries[%d]: %s" % (idx, problem))
            continue
        key = entry_key(entry)
        if key in seen:
            problems.append("entries[%d]: 重复地址 %s（已忽略）" % (idx, key))
            continue
        seen.add(key)
        entries.append(entry)
    return entries, problems


def _normalize_entry(raw):
    if not isinstance(raw, dict):
        return None, "不是对象"
    chain = str(raw.get("chain") or "").strip().lower()
    if chain not in VALID_CHAINS:
        return None, "chain 必须是 btc/eth/erc20"
    address = str(raw.get("address") or "").strip()
    if not address:
        return None, "缺少 address"
    if chain != "btc" and not _valid_hex_address(address):
        return None, "EVM 地址格式非法（应为 0x + 40 位十六进制）"
    if chain == "btc" and not (14 <= len(address) <= 100):
        return None, "BTC 地址长度异常"
    label = str(raw.get("label") or "").strip()
    if not label:
        return None, "缺少 label（人工填写的名称，未核实要标注）"
    source_note = str(raw.get("source_note") or "").strip()
    if not source_note:
        return None, "缺少 source_note（标签来源/确认方式；禁止编造归属）"
    threshold = _as_float(raw.get("threshold_pct"))
    if threshold is None or threshold <= 0:
        threshold = DEFAULT_THRESHOLD_PCT
    severity = str(raw.get("severity") or "").strip().upper() or None
    if severity and severity not in ("P0", "P1", "P2"):
        return None, "severity 必须是 P0/P1/P2"
    token, problem = _normalize_token(raw, chain)
    if problem:
        return None, problem
    price_usd = _as_float(raw.get("price_usd"))
    if price_usd is not None and price_usd < 0:
        price_usd = None
    return {
        "chain": chain,
        "address": address,
        "label": label,
        "source_note": source_note,
        "threshold_pct": threshold,
        "severity": severity,
        "token": token,
        "price_usd": price_usd,
        "price_asset": str(raw.get("price_asset") or "").strip().upper() or None,
    }, None


def _normalize_token(raw, chain):
    if chain != "erc20":
        return None, None
    token = raw.get("token")
    if not isinstance(token, dict):
        return None, "erc20 必须给 token: {address, symbol, decimals}"
    token_address = str(token.get("address") or "").strip()
    if not _valid_hex_address(token_address):
        return None, "token.address 格式非法"
    symbol = str(token.get("symbol") or "").strip().upper()
    if not symbol:
        return None, "token.symbol 不能为空"
    decimals = _as_int(token.get("decimals"))
    if decimals is None or decimals < 0 or decimals > 36:
        return None, "token.decimals 必须是 0–36 的整数"
    return {"address": token_address, "symbol": symbol, "decimals": decimals}, None


def build_events(rows, default_severity="P2"):
    """变化 ≥ threshold_pct 的条目 → 告警事件（默认 P2，可被条目 severity 升级）。"""
    events = []
    for row in rows or []:
        change = _as_float(row.get("change_pct"))
        if change is None:
            continue
        threshold = _as_float(row.get("threshold_pct")) or DEFAULT_THRESHOLD_PCT
        if abs(change) < threshold:
            continue
        severity = str(row.get("severity") or default_severity or "P2").upper()
        direction = "up" if change > 0 else "down"
        events.append({
            "ts": row.get("fetched_at"),
            "chain": row.get("chain"),
            "address": row.get("address"),
            "label": row.get("label"),
            "symbol": row.get("symbol"),
            "prev_balance": row.get("prev_balance"),
            "balance": row.get("balance"),
            "change_pct": round(change, 2),
            "threshold_pct": threshold,
            "severity": severity,
            "usd": row.get("usd"),
            "key": "%s:%s" % (entry_key(row), direction),
        })
    return events


def select_push_events(events, alerts_cfg):
    """按 push_severities 过滤出应即时推送的事件（默认 P2 不推）。"""
    push = {str(s).upper() for s in ((alerts_cfg or {}).get("push_severities") or ["P0", "P1"])}
    return [e for e in events or [] if str(e.get("severity") or "").upper() in push]



# ---------------------------------------------------------------- 告警送达（默认 P2/日报）
def _fmt_amount(value):
    number = _as_float(value)
    if number is None:
        return "—"
    text = ("%.8f" % number).rstrip("0").rstrip(".")
    integer, _, fraction = text.partition(".")
    integer = "{:,}".format(int(integer)) if integer.lstrip("-").isdigit() else integer
    return integer + ("." + fraction if fraction else "")


def _fmt_usd(value):
    number = _as_float(value)
    if number is None:
        return "价格不可得"
    if abs(number) >= 1e8:
        return "≈$%.2fM" % (number / 1e6)
    if abs(number) >= 1e4:
        return "≈$%.1fK" % (number / 1e3)
    return "≈$%.2f" % number


def render_alert(events):
    """变化事件 → Telegram HTML 文本（P2 日报用同一段文案；不含身份/归属推断）。"""
    events = list(events or [])
    lines = [
        "<b>【crypto-onchain】已知地址持仓变化</b>",
        "<i>只报余额变化；地址与标签来自人工配置，不做归属推断</i>",
        "",
    ]
    for event in events[:10]:
        change = _as_float(event.get("change_pct")) or 0.0
        arrow = "↑" if change > 0 else "↓"
        lines.append("· %s %s：%s → %s %s（%s%.2f%%，阈值 ≥%.0f%%）%s" % (
            esc(str(event.get("label") or "")),
            esc(str(event.get("address") or ""))[:16] + "…",
            _fmt_amount(event.get("prev_balance")),
            _fmt_amount(event.get("balance")),
            esc(str(event.get("symbol") or "")),
            arrow, abs(change), _as_float(event.get("threshold_pct")) or 0.0,
            _fmt_usd(event.get("usd")),
        ))
    if len(events) > 10:
        lines.append("· 其余 %d 条见快照" % (len(events) - 10))
    lines += ["", "口径：地址来自 config/holdings-watchlist.yaml；未核实标签仅作观察，不代表真实归属。"]
    return "\n".join(lines)
