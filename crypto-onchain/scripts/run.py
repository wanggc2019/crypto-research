#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run.py —— crypto-onchain 主入口（M2 归因引擎：采集 + 归因 + 影子落盘，不推送）。

用法：
    python3 scripts/run.py                      # 跑一轮，写影子快照/候选（不推送）
    python3 scripts/run.py --dry-run --json     # 只预览，不写任何文件
    python3 scripts/run.py --fixtures DIR       # 离线回放（配合 --data-dir 临时目录）
    python3 scripts/run.py --status [--json]    # 查看最近影子快照/候选数量（不联网）

退出码（与仓库约定一致）：0 成功 / 1 部分源失败但有产出 / 2 配置错误 / 3 主要数据源全部不可用。

边界（写进卡片/日志，防止过度解读）：
- 本模块永远不输出"谁在卖"的身份结论，只输出类别（主动卖盘 / 储备流出 / 强平 / 稳定币赎回）；
- 交易所净流是 DefiLlama 全币种聚合 USD，不是分币种；清算仅 OKX 口径。
"""
import argparse
import json
import os
import sys
import time

import common  # isort: skip  # 必须最先导入：common 负责把 libs/crypto_core 加入 sys.path

import attribution  # noqa: E402
import context as market_context  # noqa: E402
import holdings_collect  # noqa: E402
import indicators  # noqa: E402
import signals  # noqa: E402
import sources  # noqa: E402
import walletflow  # noqa: E402
import walletflow_alerts  # noqa: E402


# ---------------------------------------------------------------- 快照组装
def _asset_snapshot(trades_meta, liq_events, liq_meta, price_row, rules, now_ts, errors):
    """单资产：5m/15m 主动卖统计 + 15m 清算统计 + 行情背景。"""
    taker_rules = (rules.get("taker") or {})
    large_usd = ((taker_rules.get("large_trade_usd") or {}).get(liq_meta["asset"])) or 0
    t5 = indicators.taker_stats(trades_meta["trades"], now_ts, 300, large_trade_usd=large_usd)
    t15 = indicators.taker_stats(trades_meta["trades"], now_ts, 900, large_trade_usd=large_usd)
    price = None
    if price_row:
        try:
            price = float(price_row.get("price"))
        except (TypeError, ValueError):
            price = None
    if price is None:
        price = t5.get("price_last")
    liq = indicators.liquidation_metrics(
        liq_events, liq_meta["contract_value"], price, now_ts,
        window_minutes=(rules.get("liquidations") or {}).get("window_minutes") or 15,
    )
    if trades_meta.get("error"):
        errors.append("%s trades: %s" % (liq_meta["asset"], trades_meta["error"]))
    if liq_meta.get("error"):
        errors.append("%s liquidations: %s" % (liq_meta["asset"], liq_meta["error"]))
    return {
        "price": price,
        "change_24h_pct": (price_row or {}).get("change_24h_pct"),
        "direction": indicators.direction_of((price_row or {}).get("change_24h_pct")),
        "oi": (price_row or {}).get("oi"),
        "funding_rate": (price_row or {}).get("funding_rate"),
        "taker_5m": t5,
        "taker_15m": t15,
        "liq_15m": liq,
        "sources": {
            "trades_pages": trades_meta.get("pages"),
            "trades_truncated": trades_meta.get("truncated"),
            "trades_coverage_seconds": trades_meta.get("coverage_seconds"),
            "liq_events": liq.get("n_total"),
            "liq_coverage_minutes": liq.get("coverage_minutes"),
        },
    }


def _price_context_offline(fixtures_dir, now_ts):
    """fixtures/price_export.json 走与线上同一条降级路径（schema 相同）。"""
    data = sources.load_fixture(fixtures_dir, "price_export.json")
    if not isinstance(data, dict):
        return {"ok": False, "fresh": False, "generated_at": None, "assets": {},
                "error": "缺少 fixture price_export.json"}
    assets = {}
    for asset, row in (data.get("assets") or {}).items():
        if isinstance(row, dict):
            assets[str(asset).upper()] = {
                "price": row.get("price"), "change_24h_pct": row.get("change_24h_pct"),
                "volume_24h": row.get("volume_24h"), "sources": row.get("sources") or {},
                "funding_rate": row.get("funding_rate"), "oi": row.get("oi"),
            }
    return {"ok": bool(assets), "fresh": True, "generated_at": data.get("generated_at"),
            "assets": assets, "error": None if assets else "fixture price_export 无 assets"}


def _collect(cfg, paths, fixtures_dir, now_ts=None):
    """采集 + 指标。返回 (assets, cex_flows, stablecoins, price_ctx, now_ts, errors)。"""
    app = cfg["app"]
    rules = cfg["rules"]
    watchlist = cfg["watchlist"]
    src = app.get("sources") or {}
    binance_on = bool((src.get("binance") or {}).get("enabled", True))
    okx_on = bool((src.get("okx") or {}).get("enabled", True))
    llama_on = bool((src.get("defillama") or {}).get("enabled", True))
    errors = []

    # 0) 时钟：联网模式先定 now；fixtures 模式等成交数据回来后用最新成交时间当"现在"
    if not fixtures_dir:
        now_ts = int(now_ts or time.time())

    # 1) 逐笔成交（fixtures 时先取到数据再定时钟）
    trade_meta = {}
    assets_cfg = watchlist.get("assets") or {}
    for asset, row in assets_cfg.items():
        if not binance_on:
            trade_meta[asset] = {"trades": [], "pages": 0, "weight": 0, "truncated": False,
                                 "coverage_seconds": 0, "first_ts": None, "last_ts": None, "error": None}
        elif fixtures_dir:
            trade_meta[asset] = sources.load_fixture_trades(fixtures_dir, row.get("binance"))
        else:
            trade_meta[asset] = sources.fetch_binance_trades(
                (src.get("binance") or {}).get("base"),
                row.get("binance"),
                now_ts,
                window_seconds=(src.get("binance") or {}).get("window_seconds") or 900,
                limit=(src.get("binance") or {}).get("request_limit") or 1000,
                max_pages=(src.get("binance") or {}).get("max_pages") or 12,
                timeout=(src.get("binance") or {}).get("timeout_seconds") or 8,
            )

    # 离线回放：用数据里最新的成交时间当"现在"，否则窗口过滤会把样本全丢掉
    if fixtures_dir:
        latest = [m.get("last_ts") for m in trade_meta.values() if m.get("last_ts")]
        now_ts = max(latest) if latest else int(time.time())
    else:
        now_ts = int(now_ts or time.time())

    # 2) 行情背景（跨模块只读；缺失/过期 → 降级，不阻塞）
    if fixtures_dir:
        price_ctx = _price_context_offline(fixtures_dir, now_ts)
    else:
        price_ctx = common.load_price_context(app, now_ts=now_ts)
    if not price_ctx.get("ok"):
        errors.append("price_context: %s" % (price_ctx.get("error") or "不可用"))

    # 2.5) 官方 OI 历史（优先；失败时归因自动降级用本地历史）
    oi_hist = {}
    oi_cfg = (src.get("binance") or {}).get("oi_hist") or {}
    for asset, row in assets_cfg.items():
        if not binance_on:
            oi_hist[asset] = []
        elif fixtures_dir:
            rows, _err = sources.load_fixture_oi_history(fixtures_dir, row.get("binance"))
            oi_hist[asset] = rows
        else:
            rows, _err = sources.fetch_oi_history(
                (src.get("binance") or {}).get("futures_base"),
                row.get("binance"),
                period=oi_cfg.get("period") or "15m",
                limit=oi_cfg.get("limit") or 3,
                timeout=(src.get("binance") or {}).get("timeout_seconds") or 8,
            )
            oi_hist[asset] = rows

    # 3) CEX 储备净流 + 稳定币（免费、免 key）
    llama = src.get("defillama") or {}
    if not llama_on:
        cex_rows, cex_err, st_rows, st_err = [], None, [], None
    elif fixtures_dir:
        cex_rows, cex_err = sources.load_fixture_cex_flows(fixtures_dir)
        st_rows, st_err = sources.load_fixture_stablecoins(
            fixtures_dir, (rules.get("stablecoin") or {}).get("symbols"),
        )
    else:
        cex_rows, cex_err = sources.fetch_cex_flows(
            llama.get("cexs_url"), timeout=llama.get("timeout_seconds") or 12,
        )
        st_rows, st_err = sources.fetch_stablecoins(
            llama.get("stablecoins_url"),
            (rules.get("stablecoin") or {}).get("symbols"),
            timeout=llama.get("timeout_seconds") or 12,
        )
    if cex_err:
        errors.append("cex_flows: %s" % cex_err)
    if st_err:
        errors.append("stablecoins: %s" % st_err)

    # 4) 清算（仅 OKX 口径）
    liq_events = {}
    for asset, row in assets_cfg.items():
        if not okx_on:
            liq_events[asset] = {"events": [], "error": None}
        elif fixtures_dir:
            events, err = sources.load_fixture_liquidations(fixtures_dir, row.get("okx_uly"))
        else:
            events, err = sources.fetch_okx_liquidations(
                (src.get("okx") or {}).get("base"), row.get("okx_uly"),
                limit=(src.get("okx") or {}).get("liquidation_limit") or 100,
                timeout=(src.get("okx") or {}).get("timeout_seconds") or 8,
            )
        liq_events[asset] = {"events": events, "error": None if events else err}

    # 5) 指标
    cex_flows = indicators.cex_flow_metrics(
        cex_rows, watchlist.get("cex_watch"),
        min_tvl_usd=(rules.get("cex_flow") or {}).get("min_tvl_usd") or 0,
    )
    stablecoins = indicators.stablecoin_metrics(st_rows)
    assets = {}
    if not price_ctx.get("fresh") and price_ctx.get("ok"):
        errors.append("price_context: 快照过期（%s），方向门控降级为 unknown" % price_ctx.get("generated_at"))
    for asset, row in assets_cfg.items():
        assets[asset] = _asset_snapshot(
            trade_meta[asset], liq_events[asset]["events"],
            {
                "asset": asset,
                "contract_value": row.get("contract_value"),
                "error": liq_events[asset]["error"],
            },
            (price_ctx.get("assets") or {}).get(asset), rules, now_ts, errors,
        )
        if not price_ctx.get("fresh"):
            assets[asset]["direction"] = "unknown"
    return assets, cex_flows, stablecoins, price_ctx, now_ts, errors, oi_hist


# ---------------------------------------------------------------- 链上大额转账状态
WALLETFLOW_CURSOR = "state/walletflow-cursor.json"


def _load_walletflow_cursor(paths):
    """返回 {chain: block}；兼容旧格式 {"block": n}（视为 ethereum）。"""
    data = common.safe_json_load(os.path.join(paths["root"], WALLETFLOW_CURSOR), {})
    if not isinstance(data, dict):
        return {}
    if isinstance(data.get("chains"), dict):
        out = {}
        for chain, block in data["chains"].items():
            try:
                if int(block) > 0:
                    out[str(chain)] = int(block)
            except (TypeError, ValueError):
                continue
        return out
    try:
        legacy = int(data.get("block") or 0)
    except (TypeError, ValueError):
        legacy = 0
    return {"ethereum": legacy} if legacy > 0 else {}


def _advance_walletflow_cursor(paths, meta, cursors):
    """每条链单独前进（有 error 的链不前进）；无进展则不动文件。"""
    chains = dict(cursors or {})
    changed = False
    for m in (meta or {}).get("chains") or []:
        if m.get("error"):
            continue
        block = m.get("to_block") or m.get("latest_block")
        if not block:
            continue
        chains[str(m.get("name"))] = int(block)
        changed = True
    if not changed:
        return
    try:
        common.write_json(os.path.join(paths["root"], WALLETFLOW_CURSOR),
                          {"chains": chains, "updated_at": common.ts_iso()})
    except OSError:
        pass


def _log_walletflow(paths, records):
    if not records:
        return
    try:
        common.append_jsonl(
            os.path.join(paths["raw"], common.today_str(), "walletflow.jsonl"),
            [{k: r.get(k) for k in ("ts", "chain", "block", "symbol", "amount", "usd", "from", "to",
                                    "tx", "log_index", "direction", "counterparty")} for r in records],
        )
    except OSError:
        pass


# ---------------------------------------------------------------- OI 历史（跨轮状态）
OI_HISTORY_FILE = "state/oi-history.json"


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _load_oi_history(paths):
    """读 {asset: [[ts, oi], ...]}；坏数据一律当空。"""
    data = common.safe_json_load(os.path.join(paths["root"], OI_HISTORY_FILE), {})
    return data if isinstance(data, dict) else {}


def _oi_changes(assets, paths, now_ts, dry_run, rules, oi_hist=None):
    """算 15m OI 变化：官方 openInterestHist 优先，本地跨轮历史兜底。

    官方历史不受 Mac 休眠造成的采样断点影响（每 15m 一个点，容差 20 分钟）；
    本地历史同时继续累积，作为官方端点失败时的降级来源与交叉校验。
    返回 (changes, sources)：{asset: pct|None}, {asset: "binance_hist"|"local"|None}。
    """
    cfg = (rules or {}).get("attribution") or {}
    window = int(cfg.get("oi_window_minutes") or 15)
    keep_hours = int(cfg.get("oi_history_hours") or 24)
    cutoff = int(now_ts) - keep_hours * 3600
    history = _load_oi_history(paths)
    changes, sources_map, new_history = {}, {}, {}
    hist_by_asset = oi_hist or {}
    for asset, row in (assets or {}).items():
        official = hist_by_asset.get(asset) or []
        change, source = None, None
        if len(official) >= 2:
            change = indicators.oi_change_pct(official, now_ts, window_minutes=window, tolerance_minutes=20)
            source = "binance_hist" if change is not None else None
        points = []
        for item in history.get(asset) or []:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                ts, val = _num(item[0]), _num(item[1])
                if ts is not None and val is not None and int(ts) >= cutoff:
                    points.append([int(ts), val])
        oi_now = _num((row or {}).get("oi"))
        if oi_now is not None:
            points.append([int(now_ts), oi_now])
        if change is None:
            change = indicators.oi_change_pct(points, now_ts, window_minutes=window)
            source = "local" if change is not None else None
        changes[asset] = change
        sources_map[asset] = source
        new_history[asset] = points[-400:]  # 24h × 5min ≈ 288 点，留余量
    if not dry_run:
        try:
            common.write_json(os.path.join(paths["root"], OI_HISTORY_FILE), new_history)
        except OSError:
            pass
    return changes, sources_map


# ---------------------------------------------------------------- 跨模块导出契约
def _export_payload(snapshot):
    """`onchain/pressure/v1`：给 crypto-analyst 等下游只读消费（只增字段）。"""
    atts = (snapshot.get("attribution") or {})
    assets = {}
    for asset, row in (snapshot.get("assets") or {}).items():
        att = (atts.get("assets") or {}).get(asset)
        assets[asset] = {
            "price": row.get("price"),
            "change_24h_pct": row.get("change_24h_pct"),
            "taker_5m_sell_ratio": (row.get("taker_5m") or {}).get("sell_ratio"),
            "taker_5m_quote_usd": (row.get("taker_5m") or {}).get("quote_volume_usd"),
            "liq_15m_usd": (row.get("liq_15m") or {}).get("total_usd"),
            "liq_15m_long_ratio": (row.get("liq_15m") or {}).get("long_ratio"),
            "oi_change_15m_pct": row.get("oi_change_15m_pct"),
            "oi_source": row.get("oi_source"),
            "funding_rate": row.get("funding_rate"),
            "attribution": att,
        }
    cex = snapshot.get("cex_flows") or {}
    st = snapshot.get("stablecoins") or {}
    return {
        "schema": "onchain/pressure/v1",
        "generated_at": snapshot.get("generated_at"),
        "source_module": "crypto-onchain",
        "mode": snapshot.get("mode"),
        "assets": assets,
        "market": {
            "cex_net_24h_bps": cex.get("net_24h_bps"),
            "cex_net_24h_usd": cex.get("net_24h_usd"),
            "stablecoin_net_issue_24h_usd": st.get("net_issue_24h_usd"),
            "btc_direction": (((snapshot.get("assets") or {}).get("BTC") or {}).get("direction")),
            "attribution": (atts.get("market")),
        },
        "candidates": {
            "n": len(snapshot.get("candidates") or []),
            "by_severity": {
                s: len([c for c in (snapshot.get("candidates") or []) if c.get("severity") == s])
                for s in ("P0", "P1")
            },
        },
        "walletflow": snapshot.get("walletflow"),
        "caveats": [
            "CEX 净流是 DefiLlama 全币种聚合 USD，不能归到单一币种",
            "清算仅 OKX 口径，不代表全市场",
            "本契约只回答卖压类别，不含卖方身份",
        ],
    }


def _write_exports(cfg, paths, snapshot):
    app_cfg = cfg.get("app") or {}
    rel = (app_cfg.get("exports") or {}).get("file") or "exports/onchain-pressure.json"
    path = os.path.join(paths["root"], rel)
    common.write_json(path, _export_payload(snapshot))
    return path


# ---------------------------------------------------------------- 归因日志（每日摘要用）
def _log_attributions(paths, attributions, assets):
    """把本轮非空归因追加到 raw/<date>/attributions.jsonl，供每日影跑摘要统计。"""
    rows = []
    now_iso = common.ts_iso()
    for asset, att in (attributions.get("assets") or {}).items():
        if not att:
            continue
        row = assets.get(asset) or {}
        rows.append({
            "generated_at": now_iso,
            "asset": asset,
            "category": att.get("category"),
            "category_code": att.get("category_code"),
            "confidence": att.get("confidence"),
            "price": row.get("price"),
            "change_24h_pct": row.get("change_24h_pct"),
            "taker_5m_sell_ratio": (row.get("taker_5m") or {}).get("sell_ratio"),
            "oi_change_15m_pct": row.get("oi_change_15m_pct"),
            "oi_source": row.get("oi_source"),
            "evidence": att.get("evidence"),
        })
    market = attributions.get("market")
    if market:
        rows.append({
            "generated_at": now_iso,
            "asset": None,
            "scope": "market",
            "category": market.get("category"),
            "category_code": market.get("category_code"),
            "confidence": market.get("confidence"),
            "cex_net_24h_bps": (market.get("evidence") or [None])[0],
            "evidence": market.get("evidence"),
        })
    if rows:
        try:
            common.append_jsonl(
                os.path.join(paths["raw"], common.today_str(), "attributions.jsonl"), rows,
            )
        except OSError:
            pass


# ---------------------------------------------------------------- 影子产物
def _candidate_records(candidates, now_ts, assets, cex_flows):
    out = []
    for c in candidates:
        rec = {
            "generated_at": common.ts_iso(),
            "now_ts": now_ts,
            "signal": c.get("signal"),
            "scope": c.get("scope"),
            "asset": c.get("asset"),
            "exchange": c.get("exchange"),
            "severity": c.get("severity"),
            "would_alert": c.get("would_alert"),
            "aligned": c.get("aligned"),
            "direction": c.get("direction"),
            "value": c.get("value"),
            "threshold": c.get("threshold"),
            "note": c.get("note"),
            "extra": c.get("extra"),
            "price_context": {
                a: {"price": (assets.get(a) or {}).get("price"),
                    "change_24h_pct": (assets.get(a) or {}).get("change_24h_pct")}
                for a in ("BTC", "ETH", "BNB")
            },
            "cex_net_24h_bps": (cex_flows or {}).get("net_24h_bps"),
            "attribution": c.get("attribution"),
        }
        out.append(rec)
    return out


def _write_shadow(cfg, paths, snapshot, candidate_records):
    shadow = cfg["app"].get("shadow") or {}
    if not shadow.get("enabled", True):
        return {}
    snap_path = os.path.join(paths["root"], shadow.get("snapshot_file") or "state/shadow-latest.json")
    cand_path = os.path.join(paths["root"], shadow.get("candidates_file") or "state/shadow-candidates.jsonl")
    common.write_json(snap_path, snapshot)
    if candidate_records:
        common.append_jsonl(cand_path, candidate_records)
    return {"snapshot": snap_path, "candidates": cand_path, "n_candidates": len(candidate_records)}


# ---------------------------------------------------------------- 状态
def _attribution_brief(att):
    att = att or {}
    parts = []
    for asset, a in (att.get("assets") or {}).items():
        if a:
            parts.append("%s=%s(%s)" % (asset, a.get("category"), a.get("confidence")))
    if att.get("market"):
        parts.append("市场=%s(%s)" % (att["market"].get("category"), att["market"].get("confidence")))
    return "；".join(parts) if parts else "无（价格未下跌或数据不足）"


def _status(cfg, paths):
    shadow = cfg["app"].get("shadow") or {}
    snap_path = os.path.join(paths["root"], shadow.get("snapshot_file") or "state/shadow-latest.json")
    cand_path = os.path.join(paths["root"], shadow.get("candidates_file") or "state/shadow-candidates.jsonl")
    snap = common.safe_json_load(snap_path, None)
    records = common.read_jsonl(cand_path)
    today = common.today_str()
    today_recs = [r for r in records if str(r.get("generated_at") or "").startswith(today)]
    return {
        "snapshot": snap_path,
        "snapshot_ok": bool(snap),
        "generated_at": (snap or {}).get("generated_at"),
        "candidates_file": cand_path,
        "candidates_total": len(records),
        "candidates_today": len(today_recs),
        "candidates_today_severity": {
            s: len([r for r in today_recs if r.get("severity") == s]) for s in ("P0", "P1")
        },
        "last_candidates": records[-3:],
        "attribution": (snap or {}).get("attribution"),
        "walletflow": (snap or {}).get("walletflow"),
        "holdings": (snap or {}).get("holdings"),
        "exports": os.path.join(
            paths["root"],
            ((cfg.get("app") or {}).get("exports") or {}).get("file") or "exports/onchain-pressure.json",
        ),
    }


# ---------------------------------------------------------------- 主流程
def run_once(cfg, paths, fixtures_dir=None, dry_run=False):
    t0 = time.time()
    assets, cex_flows, stablecoins, price_ctx, now_ts, errors, oi_hist = _collect(cfg, paths, fixtures_dir)
    repo_root = os.path.dirname(common.ROOT)
    directions = {a: (assets.get(a) or {}).get("direction", "unknown") for a in assets}
    candidates = signals.build_candidates(
        {a: {"taker_5m": (assets[a] or {}).get("taker_5m"),
             "liq_15m": (assets[a] or {}).get("liq_15m")} for a in assets},
        cex_flows, stablecoins, cfg["rules"], directions,
    )
    # M2 归因引擎：需要跨轮 OI 历史（15m 变化）+ 资金费率（来自 crypto-price exports）
    # 链上大额转账（免费公共 RPC；辅助信号，失败不阻塞主流程）
    walletflow_meta = {"enabled": False}
    wf_records = []
    wf_cfg = (cfg["app"] or {}).get("walletflow") or {}
    if wf_cfg.get("enabled"):
        cursor = _load_walletflow_cursor(paths)
        if fixtures_dir:
            wf_records = walletflow.fixture_records(fixtures_dir, wf_cfg)
            fixture_chains = []
            for _chain in wf_cfg.get("chains") or []:
                _name = _chain.get("name")
                _rows = [r for r in wf_records if r.get("chain") == _name]
                fixture_chains.append({
                    "name": _name, "latest_block": None, "from_block": None, "to_block": None,
                    "scanned_blocks": 0, "skipped_blocks": 0, "rpc_calls": 0,
                    "n_large": len(_rows), "usd_total": sum(float(r.get("usd") or 0) for r in _rows),
                    "error": None,
                })
            wf_meta = {
                "chains": fixture_chains,
                "n_large": len(wf_records),
                "usd_total": sum(float(r.get("usd") or 0) for r in wf_records),
            }
            wf_error = None
        else:
            wf_records, wf_meta, wf_error = walletflow.fetch_transfers(wf_cfg, cursor)
        walletflow_meta = walletflow.summarize(wf_records, wf_meta, wf_error)
        if not dry_run and not wf_error:
            _advance_walletflow_cursor(paths, wf_meta, cursor)
            _log_walletflow(paths, wf_records)
        # 事件驱动告警（fixtures 离线回放不发送；dry-run 只生成预览）
        alerts_cfg = wf_cfg.get("alerts") or {}
        if alerts_cfg.get("enabled", True) and not fixtures_dir:
            try:
                wf_alert = walletflow_alerts.process(
                    wf_records, alerts_cfg, paths, wf_cfg.get("labels") or {},
                    dry_run=dry_run, now=common.now(),
                    context={
                        "btc_change_24h": (assets.get("BTC") or {}).get("change_24h_pct"),
                        "cex_net_24h_bps": cex_flows.get("net_24h_bps"),
                        "stablecoin_net_issue_24h_usd": stablecoins.get("net_issue_24h_usd"),
                        # 智能判断：价格（含信号）+ 新闻 + 资金面
                        "assets": price_ctx.get("assets") or {},
                        "signals": (market_context.load_price_export(repo_root).get("signals") or []),
                        "news": market_context.load_news(repo_root, common.today_str()),
                    },
                )
            except Exception as e:  # noqa: BLE001 - 告警失败不拖垮采集
                wf_alert = {"sent": False, "n": 0, "error": "%s: %s" % (type(e).__name__, str(e)[:120]),
                            "suppressed": 0, "text": ""}
            walletflow_meta["alerts"] = {
                "sent_round": wf_alert.get("n") if wf_alert.get("sent") else 0,
                "suppressed": wf_alert.get("suppressed") or 0,
                "error": wf_alert.get("error"),
            }
            if dry_run and wf_alert.get("text"):
                walletflow_meta["alerts_preview"] = wf_alert["text"]
    # 已知地址持仓（免费公共接口；TTL 内只读缓存；单地址失败不影响整轮 rc）
    holdings_meta = {"enabled": False}
    if ((cfg["app"] or {}).get("holdings") or {}).get("enabled", True):
        try:
            holdings_meta = holdings_collect.collect(
                cfg, paths, price_ctx, now=common.now(),
                fixtures_dir=fixtures_dir, dry_run=dry_run,
            )
        except Exception as e:  # noqa: BLE001 - 持仓监控失败不拖垮主流程
            holdings_meta = {"enabled": True, "entries": [], "changes": [], "errors": [],
                             "error": "%s: %s" % (type(e).__name__, str(e)[:120])}
    oi_changes, oi_sources = _oi_changes(assets, paths, now_ts, dry_run, cfg["rules"], oi_hist=oi_hist)
    for asset, row in assets.items():
        row["oi_change_15m_pct"] = oi_changes.get(asset)
        row["oi_source"] = oi_sources.get(asset)
    attributions = attribution.build_attributions(
        assets, cex_flows, stablecoins, cfg["rules"], directions, oi_changes=oi_changes,
    )
    for c in candidates:
        if c.get("scope") == "asset":
            att = (attributions.get("assets") or {}).get(c.get("asset"))
        else:
            att = attributions.get("market")
        if att:
            c["attribution"] = {
                "category": att.get("category"),
                "category_code": att.get("category_code"),
                "confidence": att.get("confidence"),
            }
    snapshot = {
        "schema": "onchain/shadow/v1",
        "generated_at": common.ts_iso(),
        "source_module": "crypto-onchain",
        "mode": "shadow",
        "now_ts": now_ts,
        "assets": assets,
        "cex_flows": cex_flows,
        "stablecoins": stablecoins,
        "price_context": price_ctx,
        "candidates": candidates,
        "attribution": attributions,
        "walletflow": walletflow_meta,
        "holdings": holdings_meta,
        "source_health": {
            "ok": sum(1 for a in assets if (assets[a] or {}).get("taker_5m", {}).get("n_trades")),
            "total": len(assets),
            "cex_ok": bool(cex_flows.get("n_eligible")),
            "stablecoin_ok": bool(stablecoins.get("n")),
            "errors": errors,
        },
        "errors": errors,
    }
    candidate_records = _candidate_records(candidates, now_ts, assets, cex_flows)
    written = {}
    if not dry_run:
        written = _write_shadow(cfg, paths, snapshot, candidate_records)
        written["exports"] = _write_exports(cfg, paths, snapshot)
        _log_attributions(paths, attributions, assets)
        att_n = sum(1 for a in (attributions.get("assets") or {}).values() if a)
        att_n += 1 if attributions.get("market") else 0
        common.log_line(
            "shadow: assets=%d cex_n=%d stable_n=%d candidates=%d attributions=%d walletflow=%d holdings=%d errors=%d"
            % (len(assets), cex_flows.get("n_eligible") or 0, stablecoins.get("n") or 0,
               len(candidates), att_n, walletflow_meta.get("n_large") or 0,
               holdings_meta.get("n") or 0, len(errors)),
            paths, filename=(cfg["app"].get("logging") or {}).get("file") or "onchain.log",
        )
    # 退出码判定：主要源全挂 → 3；部分失败 → 1
    main_ok = sum(1 for a in assets if (assets[a] or {}).get("taker_5m", {}).get("n_trades"))
    if not main_ok and not cex_flows.get("n_eligible") and not stablecoins.get("n"):
        rc = 3
    elif errors:
        rc = 1
    else:
        rc = 0
    return snapshot, candidate_records, written, rc, round(time.time() - t0, 2)


def main(argv=None):
    parser = argparse.ArgumentParser(description="crypto-onchain 影子采集 + 归因（M2：只落盘，不推送）")
    parser.add_argument("--dry-run", action="store_true", help="不写任何文件，只打印/输出 JSON")
    parser.add_argument("--json", action="store_true", help="机器可读输出")
    parser.add_argument("--fixtures", default=None, help="离线回放目录（tests/fixtures）")
    parser.add_argument("--data-dir", default=None, help="data/ 重定向（测试/回放隔离）")
    parser.add_argument("--status", action="store_true", help="查看最近影子快照/候选（不联网）")
    args = parser.parse_args(argv)

    cfg = common.load_all_configs()
    common.apply_timezone((cfg["app"] or {}).get("timezone"))
    mode = str((cfg["app"] or {}).get("mode") or "shadow")
    if mode != "shadow":
        print("❌ 当前只实现 shadow 模式（当前 mode=%r）；live 推送属 M3" % mode, file=sys.stderr)
        return 2
    for name in common.CONFIG_FILES:
        problem = common.schema_problem(cfg[name[:-5]], 1)
        if problem:
            print("❌ 配置错误：%s %s" % (name, problem), file=sys.stderr)
            return 2
    # 只读模式（--dry-run/--status）不创建目录；写模式也由 store 层按需建父目录
    paths = common.data_paths(args.data_dir)
    if args.status:
        status = _status(cfg, paths)
        print(json.dumps(status, ensure_ascii=False, indent=1) if args.json else
              "影子快照: %s\n候选累计: %d（今日 %d：P0 %d / P1 %d）\n归因: %s"
              % (status.get("generated_at") or "暂无", status["candidates_total"],
                 status["candidates_today"], status["candidates_today_severity"]["P0"],
                 status["candidates_today_severity"]["P1"],
                 _attribution_brief(status.get("attribution"))))
        return 0 if status["snapshot_ok"] else 1

    snapshot, candidate_records, written, rc, elapsed = run_once(
        cfg, paths, fixtures_dir=args.fixtures, dry_run=args.dry_run,
    )
    snapshot["elapsed_seconds"] = elapsed
    if args.json:
        print(json.dumps(snapshot, ensure_ascii=False, indent=1))
    else:
        print("crypto-onchain 影子轮次 | %s | rc=%d | %.2fs" % (snapshot["generated_at"], rc, elapsed))
        for asset, row in snapshot["assets"].items():
            t5 = row.get("taker_5m") or {}
            liq = row.get("liq_15m") or {}
            print("  %-4s price=%s 24h=%s 主动卖占比=%s 5m成交额=%.0f 大单卖=%d 15m清算=%.0f万"
                  % (asset, row.get("price"), row.get("change_24h_pct"),
                     ("%.1f%%" % (t5["sell_ratio"] * 100)) if t5.get("sell_ratio") is not None else "—",
                     t5.get("quote_volume_usd") or 0, t5.get("large_sell_count") or 0,
                     (liq.get("total_usd") or 0) / 1e4))
        cex = snapshot.get("cex_flows") or {}
        st = snapshot.get("stablecoins") or {}
        print("  CEX 24h 净流: %.1fbps TVL（%.2f 亿美元，%d 家）"
              % (cex.get("net_24h_bps") or 0, (cex.get("net_24h_usd") or 0) / 1e8, cex.get("n_eligible") or 0))
        print("  稳定币 24h 净增发: %.2f 亿美元（%d 个币种）"
              % ((st.get("net_issue_24h_usd") or 0) / 1e8, st.get("n") or 0))
        wf = snapshot.get("walletflow") or {}
        if wf.get("enabled"):
            chain_bits = []
            for c in wf.get("chains") or []:
                chain_bits.append("%s 块 %s-%s%s" % (
                    c.get("name"), c.get("from_block"), c.get("to_block"),
                    "" if not c.get("error") else "(⚠ %s)" % c["error"]))
            _alerts = wf.get("alerts") or {}
            if _alerts.get("sent_round") or _alerts.get("error"):
                print("  链上告警: 本轮发送 %s 条%s" % (
                    _alerts.get("sent_round") or 0,
                    "" if not _alerts.get("error") else "（失败：%s）" % _alerts["error"]))
            if (wf.get("alerts_preview") or "").strip():
                print("  [dry-run] 告警预览:")
                for line in wf["alerts_preview"].splitlines():
                    print("    " + line)
            print("  链上大额转账: %d 笔（≥阈值，USDT/USDC）｜%s"
                  % (wf.get("n_large") or 0, "；".join(chain_bits) or "未扫描"))
            for r in (wf.get("recent") or [])[:3]:
                print("    %s %s $%.2fM %s → %s" % (
                    r.get("symbol"), r.get("tx", "")[:10],
                    (r.get("usd") or 0) / 1e6, str(r.get("from"))[:8], str(r.get("to"))[:8]))
        hold = snapshot.get("holdings") or {}
        if hold.get("enabled"):
            bits = ["%d 个地址%s" % (hold.get("n") or 0, "（缓存）" if hold.get("cached") else "")]
            if hold.get("usd_total") is not None:
                _usd = float(hold["usd_total"])
                bits.append("合计 %s" % (("≈$%.2fM" % (_usd / 1e6)) if abs(_usd) >= 1e6 else ("≈$%.2f" % _usd)))
            bits.append("变化事件 %d 条" % len(hold.get("changes") or []))
            if hold.get("n_errors"):
                bits.append("取数失败 %d 个" % hold["n_errors"])
            print("  已知地址持仓: %s" % "｜".join(bits))
            for event in hold.get("changes") or []:
                print("    [%s] %s %s %+.2f%%（阈值 ≥%.0f%%）" % (
                    event.get("severity"), event.get("label"), event.get("symbol"),
                    event.get("change_pct") or 0, event.get("threshold_pct") or 0))
            if (hold.get("alerts_preview") or "").strip():
                print("  [dry-run] 持仓告警预览:")
                for line in hold["alerts_preview"].splitlines():
                    print("    " + line)
        print("  候选信号: %d 条%s" % (len(candidate_records),
              "" if not written else " → 已写 %s" % (written.get("snapshot") or "")))
        for c in candidate_records:
            print("    [%s] %-22s %s | %s" % (c.get("severity"), c.get("signal"),
                                              c.get("asset") or c.get("scope"), c.get("note")))
        att = snapshot.get("attribution") or {}
        for asset, a in (att.get("assets") or {}).items():
            if a:
                print("    归因[%s] %s（%s）" % (asset, a.get("category"), a.get("confidence")))
        if att.get("market"):
            print("    归因[市场] %s（%s）" % (att["market"].get("category"), att["market"].get("confidence")))
        if written.get("exports"):
            print("  导出契约: %s" % written["exports"])
        if snapshot.get("errors"):
            print("  ⚠ 降级: %s" % "; ".join(snapshot["errors"]))
    return rc


if __name__ == "__main__":
    sys.exit(main())
