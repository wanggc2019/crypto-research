#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""holdings_collect.py —— 已知地址持仓的采集/缓存/告警/CLI（挂进 crypto-onchain 现有周期）。

定位：只监控 `config/holdings-watchlist.yaml` 里**人工填写**的已知地址余额；不做地址发现/归属识别。
数据来源（全部免 key、$0）：
- BTC：`https://blockstream.info/api/address/<addr>`（失败回退 `https://mempool.space/api/address/<addr>`）；
- ETH：公共 JSON-RPC `eth_getBalance`（按配置顺序回退）；
- ERC20：同 RPC `eth_call` → `balanceOf(address)`（selector `0x70a08231`），按 decimals 换算；
- USD：优先 `crypto-price/data/exports/latest.json`（跨模块只读 exports 契约），缺失时 Binance 公开价，
  都取不到只报币数量（`usd=null`）。

成本/降级：缓存 TTL（`app.holdings.cache_ttl_seconds`，默认 30 分钟）内只读 `data/state/holdings.json`，
不打接口；单地址失败只影响该地址（error + 保留上次余额标 stale），不影响其它地址与整轮 rc；
只有变化 ≥ `threshold_pct` 才产生事件（默认 P2 只进日报，可升级 P1/P0 即时推送）。

CLI：
    python3 scripts/holdings_collect.py                     # 跑一轮（刷新缓存/写历史，不推送）
    python3 scripts/holdings_collect.py --dry-run --json    # 只预览，不写任何文件
    python3 scripts/holdings_collect.py --status            # 读缓存状态（不联网）
    python3 scripts/holdings_collect.py --fixtures DIR      # 离线回放（读 DIR/holdings.json）
"""
import argparse
import datetime
import json
import os
import sys
import urllib.parse
import urllib.request

import common  # isort: skip  # 必须最先导入：common 负责把 libs/crypto_core 加入 sys.path

import holdings  # noqa: E402

from crypto_core import config as _config  # noqa: E402
from crypto_core import http as _http  # noqa: E402
from crypto_core.notify import check_credentials, send_message  # noqa: E402

WATCHLIST_FILE = "holdings-watchlist.yaml"
STATE_FILE = "state/holdings.json"
HISTORY_FILE = "state/holdings-history.jsonl"
ALERTS_STATE = "state/holdings-alerts.json"
DEFAULT_TTL_SECONDS = 1800
DEFAULT_BTC_PROVIDERS = ("https://blockstream.info/api", "https://mempool.space/api")
DEFAULT_ETH_RPC_URLS = (
    "https://ethereum.publicnode.com",
    "https://ethereum-rpc.publicnode.com",
    "https://cloudflare-eth.com",
)



# ---------------------------------------------------------------- 取数（网络）
def _get_json(url, timeout=10):
    raw = _http.get(url, timeout=timeout, retries=1)
    return json.loads(raw.decode("utf-8", "ignore"))


def _rpc_call(url, method, params, timeout=10):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode("utf-8")
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json", "User-Agent": "crypto-research/1.0"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8", "ignore"))
    if not isinstance(data, dict):
        raise ValueError("rpc 响应不是对象")
    if data.get("error"):
        raise RuntimeError("rpc error: %s" % str(data["error"])[:160])
    return data.get("result")


def rpc_with_fallback(rpc_urls, method, params, timeout=10):
    """按顺序尝试公共 RPC；返回 (result, url, error)。全失败时 error 汇总前两个原因。"""
    errors = []
    for url in rpc_urls or DEFAULT_ETH_RPC_URLS:
        try:
            return _rpc_call(url, method, params, timeout=timeout), url, None
        except Exception as e:  # noqa: BLE001 - 公共 RPC 失败逐个回退
            errors.append("%s: %s" % (url, str(e)[:80]))
    return None, None, "；".join(errors[:2]) or "无可用 RPC"


def fetch_btc_balance(entry, providers=None, timeout=10):
    errors = []
    for base in providers or DEFAULT_BTC_PROVIDERS:
        base = str(base or "").rstrip("/")
        if not base:
            continue
        url = "%s/address/%s" % (base, urllib.parse.quote(entry["address"], safe=""))
        try:
            payload = _get_json(url, timeout=timeout)
        except Exception as e:  # noqa: BLE001 - 接口失败回退下一个
            errors.append("%s: %s" % (base, str(e)[:80]))
            continue
        parsed = holdings.parse_btc_address(payload)
        if parsed.get("ok"):
            return dict(parsed, source=base)
        errors.append("%s: %s" % (base, parsed.get("error")))
    return {"ok": False, "balance": None, "source": None, "error": "；".join(errors[:2]) or "无可用 BTC 接口"}


def fetch_eth_balance(entry, rpc_urls=None, timeout=10):
    result, url, error = rpc_with_fallback(
        rpc_urls, "eth_getBalance", [entry["address"], "latest"], timeout=timeout,
    )
    if error:
        return {"ok": False, "balance": None, "source": None, "error": error}
    parsed = holdings.parse_eth_balance(result)
    return dict(parsed, source=url)


def fetch_erc20_balance(entry, rpc_urls=None, timeout=10):
    token = entry.get("token") or {}
    data = holdings.erc20_balance_of_data(entry["address"])
    if data is None:
        return {"ok": False, "balance": None, "source": None, "error": "持有人地址非法"}
    result, url, error = rpc_with_fallback(
        rpc_urls, "eth_call", [{"to": token.get("address"), "data": data}, "latest"], timeout=timeout,
    )
    if error:
        return {"ok": False, "balance": None, "source": None, "error": error}
    parsed = holdings.parse_erc20_balance(result, token.get("decimals"))
    return dict(parsed, source=url)


def fetch_binance_price(entry, hcfg):
    """exports 缺价时的兜底：Binance 公开价（只对 BTC/ETH/BNB；稳定币用 price_usd）。"""
    asset = entry.get("price_asset") or (entry.get("token") or {}).get("symbol") or entry.get("chain", "").upper()
    if entry.get("price_usd") is not None or str(asset).upper() not in ("BTC", "ETH", "BNB"):
        return None
    price_cfg = (hcfg or {}).get("price") or {}
    base = str(price_cfg.get("binance_base") or "https://api.binance.com").rstrip("/")
    timeout = holdings._as_int(price_cfg.get("timeout_seconds")) or 6
    try:
        data = _get_json("%s/api/v3/ticker/price?symbol=%sUSDT" % (base, str(asset).upper()), timeout=timeout)
    except Exception:  # noqa: BLE001 - 价格兜底失败只降级为只报数量
        return None
    return holdings._as_float((data or {}).get("price"))


# ---------------------------------------------------------------- 缓存/状态
def load_fixture(fixtures_dir):
    """离线回放：`<fixtures>/holdings.json` = {"<chain>:<address>": <原始响应或 {"error": "..."}>}。"""
    path = os.path.join(str(fixtures_dir), "holdings.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _balance_from_fixture(entry, fixture):
    key = holdings.entry_key(entry)
    raw = fixture.get(key) if isinstance(fixture, dict) else None
    if raw is None and isinstance(fixture, dict):
        raw = fixture.get(key.lower())
    if raw is None:
        return {"ok": False, "balance": None, "source": "fixture", "error": "fixtures 缺少 %s" % key}
    if isinstance(raw, dict) and raw.get("error"):
        return {"ok": False, "balance": None, "source": "fixture", "error": str(raw["error"])[:120]}
    if entry["chain"] == "btc":
        return dict(holdings.parse_btc_address(raw), source="fixture")
    if entry["chain"] == "eth":
        return dict(holdings.parse_eth_balance(raw), source="fixture")
    return dict(holdings.parse_erc20_balance(raw, (entry.get("token") or {}).get("decimals")), source="fixture")


def _price_for_entry(entry, price_ctx, hcfg, use_fixture):
    price = holdings.price_for(entry, price_ctx)
    if price is not None or use_fixture:
        return price
    return fetch_binance_price(entry, hcfg)


def _row_base(entry):
    """配置条目 → 行公共字段（label/source_note 以当前配置为准，地址/标签只来自配置）。"""
    return {"chain": entry["chain"], "address": entry["address"], "symbol": holdings._symbol_of(entry),
            "label": entry["label"], "source_note": entry["source_note"],
            "threshold_pct": entry["threshold_pct"], "severity": entry.get("severity")}


def _collect_one(entry, hcfg, price_ctx, fixture, prev, now, use_fixture):
    timeout = holdings._as_int(hcfg.get("timeout_seconds")) or 10
    if use_fixture:
        result = _balance_from_fixture(entry, fixture)
    elif entry["chain"] == "btc":
        result = fetch_btc_balance(entry, hcfg.get("btc_providers"), timeout=timeout)
    elif entry["chain"] == "eth":
        result = fetch_eth_balance(entry, hcfg.get("eth_rpc_urls"), timeout=timeout)
    else:
        result = fetch_erc20_balance(entry, hcfg.get("eth_rpc_urls"), timeout=timeout)
    row = dict(_row_base(entry), cached=False, stale=False, fetched_at=common.ts_iso(now),
               source=result.get("source"), balance=result.get("balance"), usd=None, price=None,
               prev_balance=(prev or {}).get("balance"), change_pct=None, error=result.get("error"))
    if not result.get("ok"):
        # 单地址失败：保留上次余额并标 stale，绝不影响其它地址
        if row["prev_balance"] is not None:
            row["balance"] = row["prev_balance"]
            row["usd"] = (prev or {}).get("usd")
            row["price"] = (prev or {}).get("price")
            row["stale"] = True
        return row
    price = _price_for_entry(entry, price_ctx, hcfg, use_fixture)
    row["price"] = price
    row["usd"] = row["balance"] * price if (price is not None and row["balance"] is not None) else None
    row["change_pct"] = holdings.compute_change_pct(row["prev_balance"], row["balance"])
    return row


def _rows_from_cache(entries, saved_entries):
    """TTL 内只读缓存：用上次实际采集的余额/变化率，label 等以当前配置为准。"""
    rows = []
    for entry in entries:
        saved = (saved_entries or {}).get(holdings.entry_key(entry))
        if not isinstance(saved, dict):
            continue
        rows.append(dict(
            _row_base(entry), cached=True, stale=bool(saved.get("error")),
            fetched_at=saved.get("fetched_at"), source=saved.get("source"), balance=saved.get("balance"),
            usd=saved.get("usd"), price=saved.get("price"), prev_balance=saved.get("prev_balance"),
            change_pct=saved.get("change_pct"), error=saved.get("error"),
        ))
    return rows


def trim_jsonl(path, max_lines):
    """把 JSONL 截断到最近 max_lines 行（有界历史）；文件不存在/过短/写失败都不抛。"""
    limit = holdings._as_int(max_lines)
    if limit is None or limit <= 0:
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return
    if len(lines) <= limit:
        return
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.writelines(lines[-limit:])
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass



def push_change_alerts(events, alerts_cfg, paths, now):
    """即时推送（仅 severity ∈ push_severities；默认 P0/P1）。返回 (消息数, error, 抑制数)。"""
    targets = holdings.select_push_events(events, alerts_cfg)
    if not targets:
        return 0, None, len(events or [])
    state = common.safe_json_load(os.path.join(paths["root"], ALERTS_STATE), {})
    if not isinstance(state, dict):
        state = {}
    today = now.strftime("%Y-%m-%d")
    if str(state.get("date") or "") != today:
        state = {"date": today, "messages": 0, "keys": [], "last_sent_at": None}
    cap = holdings._as_int(alerts_cfg.get("daily_cap")) or 3
    remaining = max(0, cap - (holdings._as_int(state.get("messages")) or 0))
    sent_keys = set(state.get("keys") or [])
    todo = [e for e in targets if e.get("key") not in sent_keys][:remaining]
    suppressed = len(events or []) - len(todo)
    if not todo:
        return 0, None, suppressed
    env = common.load_bot_env()
    token, chat, problems = check_credentials(env)
    if problems:
        return 0, "凭据不完整：%s" % "；".join(problems), suppressed
    ok, error = send_message(token, chat, holdings.render_alert(todo), disable_preview=True)
    if not ok:
        return 0, error, suppressed
    state["messages"] = (holdings._as_int(state.get("messages")) or 0) + 1
    state["keys"] = sorted(sent_keys | {str(e.get("key")) for e in todo})
    state["last_sent_at"] = common.ts_iso(now)
    try:
        common.write_json(os.path.join(paths["root"], ALERTS_STATE), state)
    except OSError:
        pass
    return 1, None, suppressed


# ---------------------------------------------------------------- 编排
def _write_state(paths, rows, now, ttl, problems):
    keys = ("chain", "address", "symbol", "balance", "usd", "price", "prev_balance",
            "change_pct", "fetched_at", "source", "error")
    entries = {holdings.entry_key(row): {k: row.get(k) for k in keys} for row in rows}
    common.write_json(os.path.join(paths["root"], STATE_FILE), {
        "schema_version": 1,
        "updated_at": common.ts_iso(now),
        "updated_ts": int(now.timestamp()),
        "cache_ttl_seconds": int(ttl),
        "entries": entries,
        "problems": problems or [],
    })


def _append_history(paths, rows, max_lines):
    path = os.path.join(paths["root"], HISTORY_FILE)
    keys = ("fetched_at", "chain", "address", "symbol", "balance", "usd",
            "change_pct", "threshold_pct", "severity", "error")
    common.append_jsonl(path, [{k: row.get(k) for k in keys} for row in rows])
    trim_jsonl(path, max_lines)


def _append_events(paths, events, now):
    if not events:
        return
    common.append_jsonl(os.path.join(paths["raw"], common.today_str(now), "holdings-events.jsonl"), events)


def collect(cfg, paths, price_ctx=None, now=None, fixtures_dir=None, dry_run=False, watchlist=None, fixture=None):
    """跑一轮持仓：TTL 内只读缓存，否则逐地址取数 → 变化事件 → 落盘/告警。

    返回快照用的 `holdings` 块；单地址失败只进 `errors`，不改变主流程 rc。
    """
    app_cfg = (cfg or {}).get("app") or {}
    hcfg = app_cfg.get("holdings") or {}
    if not hcfg.get("enabled", True):
        return {"enabled": False}
    now = now or common.now()
    now_ts = int(now.timestamp())
    if watchlist is None:
        watchlist = common.load_config(WATCHLIST_FILE)
    problem = _config.check_schema_version(watchlist, 1)
    entries, problems = holdings.normalize_entries(watchlist)
    if problem:
        problems.insert(0, problem)
    if not entries and not problems:
        problems.append("没有有效地址（检查 config/holdings-watchlist.yaml）")
    ttl = holdings._as_int(hcfg.get("cache_ttl_seconds")) or DEFAULT_TTL_SECONDS
    state = common.safe_json_load(os.path.join(paths["root"], STATE_FILE), {})
    if not isinstance(state, dict):
        state = {}
    saved_entries = state.get("entries") if isinstance(state.get("entries"), dict) else {}
    use_fixture = bool(fixtures_dir)
    if use_fixture and fixture is None:
        fixture = load_fixture(fixtures_dir)
    alerts_cfg = hcfg.get("alerts") or {}
    cached = (not use_fixture) and holdings.is_fresh(state, now_ts, ttl)
    if cached:
        rows = _rows_from_cache(entries, saved_entries)
        changes = []
    else:
        rows = [
            _collect_one(entry, hcfg, price_ctx, fixture if use_fixture else None,
                         saved_entries.get(holdings.entry_key(entry)), now, use_fixture)
            for entry in entries
        ]
        changes = holdings.build_events(rows, hcfg.get("default_severity") or "P2") if alerts_cfg.get("enabled", True) else []
    errors = [{
        "key": holdings.entry_key(row), "chain": row.get("chain"),
        "address": row.get("address"), "error": row.get("error"),
    } for row in rows if row.get("error")]
    alerts_meta = {"enabled": bool(alerts_cfg.get("enabled", True)), "sent": 0, "suppressed": 0, "error": None}
    preview = None
    if changes:
        if dry_run:
            preview = holdings.render_alert(changes)
            alerts_meta["suppressed"] = len(changes)
        elif use_fixture:
            alerts_meta["suppressed"] = len(changes)
            alerts_meta["reason"] = "fixtures 回放不发送"
        elif alerts_cfg.get("push"):
            sent, error, suppressed = push_change_alerts(changes, alerts_cfg, paths, now)
            alerts_meta.update({"sent": sent, "error": error, "suppressed": suppressed})
        else:
            alerts_meta["suppressed"] = len(changes)
    if not dry_run and not cached and entries:
        _write_state(paths, rows, now, ttl, problems)
        _append_history(paths, rows, hcfg.get("history_max_lines"))
        _append_events(paths, changes, now)
        common.log_line(
            "holdings: n=%d cached=%d changes=%d errors=%d" % (len(rows), int(cached), len(changes), len(errors)),
            paths, filename=(app_cfg.get("logging") or {}).get("file") or "onchain.log",
        )
    base_ts = (holdings._as_int(state.get("updated_ts")) or now_ts) if cached else now_ts
    usd_values = [row["usd"] for row in rows if row.get("usd") is not None]
    meta = {
        "enabled": True,
        "source": "blockstream/mempool + public-rpc + price-exports",
        "cached": cached,
        "cache_ttl_seconds": ttl,
        "fetched_at": common.ts_iso(now),
        "next_refresh_at": common.ts_iso(datetime.datetime.fromtimestamp(base_ts + ttl)),
        "n": len(rows),
        "n_errors": len(errors),
        "usd_total": round(sum(usd_values), 2) if usd_values else None,
        "price_ok": bool(usd_values),
        "entries": rows,
        "changes": changes,
        "alerts": alerts_meta,
        "problems": problems,
        "errors": errors,
    }
    if preview:
        meta["alerts_preview"] = preview
    return meta


def status(paths):
    """读缓存状态（不联网），供 `--status`。"""
    state = common.safe_json_load(os.path.join(paths["root"], STATE_FILE), {})
    if not isinstance(state, dict):
        state = {}
    entries = state.get("entries") if isinstance(state.get("entries"), dict) else {}
    return {
        "state_file": os.path.join(paths["root"], STATE_FILE),
        "updated_at": state.get("updated_at"),
        "cache_ttl_seconds": state.get("cache_ttl_seconds"),
        "n": len(entries),
        "entries": [dict({"key": key}, **(value or {})) for key, value in entries.items()],
        "problems": state.get("problems") or [],
    }


# ---------------------------------------------------------------- CLI
def _offline_price_context(fixtures_dir):
    """CLI 离线回放的行情：与 run.py 的 fixture 结构一致（只取 price）。"""
    path = os.path.join(str(fixtures_dir), "price_export.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError, UnicodeDecodeError):
        return {"ok": False, "fresh": False, "assets": {}, "error": "缺少 price_export.json"}
    assets = {}
    for asset, row in ((data or {}).get("assets") or {}).items():
        if isinstance(row, dict):
            assets[str(asset).upper()] = {"price": row.get("price")}
    return {"ok": bool(assets), "fresh": True, "assets": assets, "error": None}


def _print_summary(meta):
    if not meta.get("enabled"):
        print("持仓监控：未启用（app.holdings.enabled=false）")
        return
    print("已知地址持仓 | %s | %d 个地址（缓存=%s）" % (
        meta.get("fetched_at"), meta.get("n") or 0, "是" if meta.get("cached") else "否"))
    for row in meta.get("entries") or []:
        change = row.get("change_pct")
        print("  [%s] %s %s：%s %s（%s；%s）" % (
            str(row.get("chain") or "").upper(), row.get("label"), str(row.get("address") or "")[:10],
            holdings._fmt_amount(row.get("balance")), row.get("symbol") or "",
            holdings._fmt_usd(row.get("usd")),
            ("变化 %+.2f%%" % change) if change is not None else "无上次基线",
        ))
        if row.get("error"):
            print("    ⚠ %s%s" % (row["error"], "（显示上次余额）" if row.get("stale") else ""))
    print("  变化事件：%d 条；错误：%d 个；USD 合计：%s" % (
        len(meta.get("changes") or []), meta.get("n_errors") or 0, holdings._fmt_usd(meta.get("usd_total"))))
    for event in meta.get("changes") or []:
        print("    [%s] %s %s %+.2f%%（阈值 ≥%.0f%%）" % (
            event.get("severity"), event.get("label"), event.get("symbol"),
            event.get("change_pct") or 0, event.get("threshold_pct") or 0))
    if (meta.get("alerts_preview") or "").strip():
        print("  [dry-run] 告警预览:")
        for line in meta["alerts_preview"].splitlines():
            print("    " + line)


def main(argv=None):
    parser = argparse.ArgumentParser(description="crypto-onchain 已知地址持仓监控（BTC/ETH/ERC20）")
    parser.add_argument("--dry-run", action="store_true", help="只预览，不写任何文件、不推送")
    parser.add_argument("--json", action="store_true", help="机器可读输出")
    parser.add_argument("--status", action="store_true", help="读缓存状态（不联网）")
    parser.add_argument("--fixtures", default=None, help="离线回放目录（含 holdings.json）")
    parser.add_argument("--data-dir", default=None, help="data/ 重定向（测试/回放隔离）")
    args = parser.parse_args(argv)

    cfg = common.load_all_configs()
    common.apply_timezone((cfg.get("app") or {}).get("timezone"))
    paths = common.data_paths(args.data_dir)
    if args.status:
        data = status(paths)
        print(json.dumps(data, ensure_ascii=False, indent=1) if args.json else
              "持仓缓存：%s（%d 个地址，更新时间 %s）" % (
                  data["state_file"], data["n"], data.get("updated_at") or "暂无"))
        return 0 if data["n"] else 1
    price_ctx = _offline_price_context(args.fixtures) if args.fixtures else common.load_price_context(cfg.get("app") or {})
    meta = collect(cfg, paths, price_ctx, fixtures_dir=args.fixtures, dry_run=args.dry_run)
    if args.json:
        print(json.dumps(meta, ensure_ascii=False, indent=1))
    else:
        _print_summary(meta)
    if not meta.get("enabled"):
        return 0
    if meta.get("problems") and not meta.get("entries"):
        return 2
    if meta.get("n") and len(meta.get("errors") or []) >= meta.get("n"):
        return 3
    return 1 if meta.get("errors") else 0


if __name__ == "__main__":
    sys.exit(main())
