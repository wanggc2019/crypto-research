#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""walletflow.py —— 链上大额转账监控（ERC20 Transfer 日志，公共 RPC，免费）。

不是"巨鲸身份识别"：本模块只回答"哪条链上发生了什么大额转账"，不回答"地址属于谁"。
身份/机构标签属付费能力（Arkham/Nansen）；地址标签在 `config/app.yaml` 的 `walletflow.labels`（内置已验证交易所地址），
数据层只按标签标注方向（入所/出所/内部），未命中一律 unknown；告警层的"推测"备注在 walletflow_alerts.py，明确标注、不当结论。

数据来源（免 key）：
  - 任意 EVM 公共 RPC 的 eth_getLogs（默认 ethereum-rpc.publicnode.com）
  - 扫 USDT/USDC 的 Transfer 事件（topic0 = keccak("Transfer(address,address,uint256)")）
  - 按 USD 阈值过滤（稳定币按 1.0 折算），只保留近期窗口内的转账

成本/限流：公共 RPC 无 key，按链增量扫描（ETH ≤120 块/轮、BSC ≤900 块/轮，超限自动二分）；
失败不阻塞主流程（快照里记录 walletflow.error，下一轮重试）。
"""
import json
import os
import urllib.request

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
BLOCK_SECONDS = 12  # 以太坊主网平均出块
ZERO_ADDR = "0x" + "0" * 40


# ---------------------------------------------------------------- RPC
def rpc_call(url, method, params, timeout=25):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json", "User-Agent": "crypto-research/1.0"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8", "ignore"))
    if not isinstance(data, dict):
        raise ValueError("rpc 响应不是对象")
    if data.get("error"):
        raise RuntimeError("rpc error: %s" % str(data["error"])[:160])
    return data.get("result")


def latest_block(rpc_url, timeout=25):
    result = rpc_call(rpc_url, "eth_blockNumber", [], timeout=timeout)
    return int(str(result), 16)


def block_timestamps(rpc_url, blocks, timeout=25):
    """批量取多个区块时间戳（一次 JSON-RPC batch，减少公共 RPC 往返）。

    批量失败时退化为逐块调用；对空列表直接返回 {}。
    """
    blocks = [int(b) for b in blocks or []]
    if not blocks:
        return {}
    payload = [
        {"jsonrpc": "2.0", "id": i, "method": "eth_getBlockByNumber",
         "params": [hex(bn), False]}
        for i, bn in enumerate(blocks)
    ]
    try:
        req = urllib.request.Request(
            rpc_url, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "User-Agent": "crypto-research/1.0"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8", "ignore"))
        if not isinstance(data, list):
            raise ValueError("batch 响应不是列表")
        out = {}
        for item in data:
            if not isinstance(item, dict):
                continue
            try:
                idx = int(item.get("id"))
                result = item.get("result") or {}
                out[blocks[idx]] = int(str(result.get("timestamp")), 16)
            except (TypeError, ValueError, IndexError):
                continue
        if out:
            return out
    except Exception:  # noqa: BLE001 - 批量失败退化单查
        pass
    return {bn: block_timestamp(rpc_url, bn, timeout=timeout) for bn in blocks}


def block_timestamp(rpc_url, block_number, timeout=25):
    result = rpc_call(rpc_url, "eth_getBlockByNumber", [hex(int(block_number)), False], timeout=timeout)
    if not isinstance(result, dict):
        return None
    try:
        return int(str(result.get("timestamp")), 16)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- 解码/筛选
def _topic_addr(topic):
    """32 字节 topic → 0x 地址（后 20 字节）。"""
    h = str(topic or "").lower().replace("0x", "")
    if len(h) < 40:
        return None
    return "0x" + h[-40:]


def _hex_int(value):
    try:
        return int(str(value), 16)
    except (TypeError, ValueError):
        return None


def decode_transfer(log, token, min_usd):
    """单条 Transfer 日志 → 转账记录；不满足阈值/字段非法返回 None。

    token: {"symbol", "address", "decimals", "price"(可选，默认 1.0)}
    """
    if not isinstance(log, dict):
        return None
    topics = log.get("topics") or []
    if len(topics) < 3:
        return None
    if str(topics[0]).lower() != TRANSFER_TOPIC:
        return None
    value = _hex_int(log.get("data"))
    decimals = int(token.get("decimals") or 18)
    if value is None:
        return None
    amount = value / float(10 ** decimals)
    price = float(token.get("price") if token.get("price") is not None else 1.0)
    usd = amount * price
    if usd < float(min_usd or 0):
        return None
    block = _hex_int(log.get("blockNumber"))
    frm, to = _topic_addr(topics[1]), _topic_addr(topics[2])
    if frm == ZERO_ADDR and to != ZERO_ADDR:
        kind = "mint"          # 稳定币增发（Circle/Tether），不是钱包间转账
    elif to == ZERO_ADDR and frm != ZERO_ADDR:
        kind = "burn"          # 稳定币销毁
    else:
        kind = "transfer"
    return {
        "chain": str(token.get("chain") or "ethereum"),
        "symbol": str(token.get("symbol") or ""),
        "token": str(token.get("address") or ""),
        "amount": amount,
        "usd": usd,
        "kind": kind,
        "from": frm,
        "to": to,
        "tx": str(log.get("transactionHash") or ""),
        "log_index": _hex_int(log.get("logIndex")),
        "block": block,
    }


def _label_of(labels, address):
    if not labels or not address:
        return None
    hit = labels.get(address) or labels.get(str(address).lower()) or labels.get(str(address).upper())
    if isinstance(hit, dict):
        return hit
    if isinstance(hit, str):
        return {"name": hit, "type": "unknown"}
    return None


def classify_direction(record, labels):
    """按标签判断方向：入所 / 出所 / 未知。没有标签时全部 unknown（不猜）。"""
    from_label = _label_of(labels, record.get("from"))
    to_label = _label_of(labels, record.get("to"))
    direction = "unknown"
    counterpart = None
    from_cex = bool(from_label and str(from_label.get("type")) == "cex")
    to_cex = bool(to_label and str(to_label.get("type")) == "cex")
    if from_cex and to_cex:
        direction = "cex_internal"  # 交易所之间/内部划转，不构成单向卖压
        counterpart = to_label or from_label
    elif to_cex:
        direction = "cex_in"        # 转入交易所（潜在卖压）
        counterpart = to_label
    elif from_cex:
        direction = "cex_out"       # 从交易所转出（提币/自托管）
        counterpart = from_label
    return direction, counterpart


def select_and_classify(logs, token, min_usd, labels=None, seen=None, include_mint_burn=False):
    """批量日志 → 阈值过滤 + 去重 + 方向标注。seen 为 {(tx, log_index)} 集合。

    默认排除 mint/burn（稳定币增发/销毁占大额转账的大头，不是钱包行为）；
    需要时把 include_mint_burn 置 True（kind 字段会保留 mint/burn/transfer）。
    """
    out, seen = [], seen or set()
    for log in logs or []:
        rec = decode_transfer(log, token, min_usd=min_usd)
        if not rec:
            continue
        if not include_mint_burn and rec.get("kind") != "transfer":
            continue
        key = (rec.get("tx"), rec.get("log_index"))
        if key in seen:
            continue
        seen.add(key)
        direction, label = classify_direction(rec, labels)
        rec["direction"] = direction
        if label:
            rec["counterparty"] = {"name": label.get("name"), "type": label.get("type")}
        out.append(rec)
    return out


# ---------------------------------------------------------------- 采集
def _rpc_url(cfg):
    return str(cfg.get("rpc_url") or "https://ethereum-rpc.publicnode.com").rstrip("/")


def pending_range(cursor_block, latest, init_blocks, max_blocks):
    """计算本轮扫描区间 (from_block, to_block, skipped_blocks)。"""
    if latest is None:
        return None, None, 0
    start = int(cursor_block) + 1 if cursor_block else int(latest) - int(init_blocks) + 1
    start = max(0, start)
    skipped = 0
    if start > int(latest):
        return None, None, 0
    if int(latest) - start + 1 > int(max_blocks):
        skipped = int(latest) - start + 1 - int(max_blocks)
        start = int(latest) - int(max_blocks) + 1
    return start, int(latest), skipped


def _is_range_too_large(err):
    msg = str(err).lower()
    return ("max results" in msg) or ("query exceeds" in msg) or ("too many results" in msg)


def scan_logs(rpc_url, token_address, from_block, to_block, chunk_blocks=30, timeout=25,
              min_chunk=25, max_calls=80):
    """分块 eth_getLogs；命中节点 "max results/range too large" 时二分重试。

    公共 RPC 对单次返回条数有上限（BSC 实测 ~20k logs），固定块跨度可能超限，
    所以遇到该错误就把区间对半拆，直到成功或 < min_chunk。返回 (logs, calls)。
    """
    logs, calls = [], 0

    def fetch(start, end):
        nonlocal calls
        if calls >= max_calls:
            return
        try:
            calls += 1
            result = rpc_call(rpc_url, "eth_getLogs", [{
                "fromBlock": hex(start), "toBlock": hex(end),
                "address": token_address, "topics": [TRANSFER_TOPIC],
            }], timeout=timeout)
            if isinstance(result, list):
                logs.extend(result)
        except Exception as e:  # noqa: BLE001 - 仅对"范围过大"二分，其它错误上抛
            if not _is_range_too_large(e) or end - start < min_chunk:
                raise
            mid = (start + end) // 2
            fetch(start, mid)
            fetch(mid + 1, end)

    start = int(from_block)
    while start <= int(to_block) and calls < max_calls:
        end = min(start + int(chunk_blocks) - 1, int(to_block))
        fetch(start, end)
        start = end + 1
    return logs, calls


def fetch_transfers(cfg, cursors):
    """按配置逐链扫描一轮。cursors 为 {chain: block}。返回 (records, meta, errors)。

    meta = {"chains": [{name, latest_block, from_block, to_block, skipped_blocks,
             scanned_blocks, rpc_calls, n_large, usd_total, error}], "n_large", "usd_total"}
    单链失败只影响该链（error 记录、游标不前进），其它链照常。
    """
    timeout = int(cfg.get("timeout_seconds") or 25)
    min_usd_global = float(cfg.get("min_usd") or 0)
    labels = cfg.get("labels") or {}
    include_mint_burn = bool(cfg.get("include_mint_burn"))
    cursors = cursors or {}
    records, chain_metas = [], []
    for chain in cfg.get("chains") or []:
        name = str(chain.get("name") or "ethereum")
        meta = {"name": name, "latest_block": None, "from_block": None, "to_block": None,
                "skipped_blocks": 0, "scanned_blocks": 0, "rpc_calls": 0,
                "n_large": 0, "usd_total": 0.0, "error": None}
        rpc_url = str(chain.get("rpc_url") or "").rstrip("/")
        try:
            if not rpc_url:
                raise ValueError("缺少 rpc_url")
            latest = latest_block(rpc_url, timeout=timeout)
            meta["latest_block"] = latest
            start, end, skipped = pending_range(
                cursors.get(name), latest,
                int(chain.get("scan_blocks_init") or 120),
                int(chain.get("max_blocks_per_round") or 120),
            )
            meta.update({"from_block": start, "to_block": end, "skipped_blocks": skipped})
            if start is None:
                chain_metas.append(meta)
                continue
            meta["scanned_blocks"] = end - start + 1
            min_usd = float(chain.get("min_usd") if chain.get("min_usd") is not None else min_usd_global)
            seen = set()
            chain_records = []
            for token in chain.get("tokens") or []:
                if not token.get("address"):
                    continue
                logs, calls = scan_logs(
                    rpc_url, token["address"], start, end,
                    int(chain.get("chunk_blocks") or 30), timeout,
                )
                meta["rpc_calls"] += calls
                rows = select_and_classify(
                    logs, dict(token, chain=name), min_usd, labels, seen,
                    include_mint_burn=include_mint_burn,
                )
                chain_records.extend(rows)
            # 大额转账极少：逐块取准确时间戳（每链最多 20 个块）
            blocks = sorted({r["block"] for r in chain_records if r.get("block") is not None})[:20]
            ts_map = block_timestamps(rpc_url, blocks, timeout=timeout)
            meta["rpc_calls"] += 1 if blocks else 0
            for rec in chain_records:
                rec["ts"] = ts_map.get(rec.get("block"))
            meta["n_large"] = len(chain_records)
            meta["usd_total"] = sum(float(r.get("usd") or 0) for r in chain_records)
            records.extend(chain_records)
        except Exception as e:  # noqa: BLE001 - 公共 RPC 失败只降级该链
            meta["error"] = "%s: %s" % (type(e).__name__, str(e)[:160])
        chain_metas.append(meta)
    records.sort(key=lambda r: (r.get("ts") or 0, r.get("block") or 0, r.get("log_index") or 0), reverse=True)
    meta_all = {
        "chains": chain_metas,
        "n_large": len(records),
        "usd_total": sum(float(r.get("usd") or 0) for r in records),
    }
    errors = [{"chain": m["name"], "error": m["error"]} for m in chain_metas if m.get("error")]
    return records, meta_all, (errors or None)


# ---------------------------------------------------------------- fixtures / 标签
def load_fixture(fixtures_dir, name="walletflow_logs.json"):
    path = os.path.join(str(fixtures_dir), name)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def fixture_records(fixtures_dir, cfg):
    """离线回放：每条链一个 fixture（walletflow_logs_<chain>.json），走与线上同一解码/筛选逻辑。"""
    records, seen = [], set()
    min_usd_global = float(cfg.get("min_usd") or 0)
    for chain in cfg.get("chains") or []:
        name = str(chain.get("name") or "ethereum")
        logs = load_fixture(fixtures_dir, "walletflow_logs_%s.json" % name)
        if not logs:
            continue
        min_usd = float(chain.get("min_usd") if chain.get("min_usd") is not None else min_usd_global)
        for token in chain.get("tokens") or []:
            rows = select_and_classify(
                [lg for lg in logs if str(lg.get("address", "")).lower() == str(token.get("address", "")).lower()],
                dict(token, chain=name), min_usd, cfg.get("labels") or {}, seen,
                include_mint_burn=bool(cfg.get("include_mint_burn")),
            )
            records.extend(rows)
    records.sort(key=lambda r: (r.get("block") or 0, r.get("log_index") or 0), reverse=True)
    return records


def summarize(records, meta, errors=None):
    """快照/exports 用的 walletflow 摘要；chains 里保留每链的扫描范围与错误。"""
    chains = []
    for m in (meta or {}).get("chains") or []:
        chains.append({
            "name": m.get("name"),
            "latest_block": m.get("latest_block"),
            "from_block": m.get("from_block"),
            "to_block": m.get("to_block"),
            "scanned_blocks": m.get("scanned_blocks") or 0,
            "skipped_blocks": m.get("skipped_blocks") or 0,
            "rpc_calls": m.get("rpc_calls") or 0,
            "n_large": m.get("n_large") or 0,
            "usd_total": m.get("usd_total") or 0.0,
            "error": m.get("error"),
        })
    return {
        "enabled": True,
        "source": "eth_getLogs/public-rpc",
        "chains": chains,
        "n_large": len(records or []),
        "usd_total": sum(float(r.get("usd") or 0) for r in records or []),
        "recent": (records or [])[:5],
        "errors": errors or [],
    }
