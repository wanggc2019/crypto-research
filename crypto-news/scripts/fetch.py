#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fetch.py —— 采集层（采集 + 归一化；只落盘，推送由 notify.py 负责）

用法：
    python3 scripts/fetch.py            # 抓全部 verified 源
    python3 scripts/fetch.py --only binance_ann,sec_press
    python3 scripts/fetch.py --limit 5  # 只跑前 5 个源（调试用）

输出：
    data/raw/YYYY-MM-DD/items.jsonl     # 归一化后的条目（追加写、去重）
    data/state/<source>.json            # 每个源的状态（hash / 已见 id / 上次检查）
    data/logs/fetch-YYYY-MM-DD.json     # 本次运行统计
"""
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
import zipfile
from io import BytesIO

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (  # noqa: E402
    LOG_DIR,
    RAW_DIR,
    STATE_DIR,
    ensure_dirs,
    load_config,
    safe_json_dump,
    safe_json_load,
    sha256,
    today_str,
    ts_iso,
)
from crypto_core import http as _http  # noqa: E402
from crypto_core import text as _textlib  # noqa: E402
from crypto_core import timeutil as _timeutil  # noqa: E402
from crypto_core.market import binance as _binance  # noqa: E402
from crypto_core.market import defillama as _defillama  # noqa: E402
from crypto_core.sources import announcements as _ann  # noqa: E402
from crypto_core.sources import rss as _rss  # noqa: E402
from crypto_core.sources import telegram as _tg  # noqa: E402

UA = _http.UA_DEFAULT


# ---------------------------------------------------------------- 基础工具
# ---------------------------------------------------------------- 兼容别名
# 通用实现已下沉到 libs/crypto_core：http / sources.rss / sources.telegram
http_get = _http.get
http_get_bytes = _http.get_bytes
parse_rss = _rss.parse_feed
parse_tme = _tg.parse_telegram_preview
parse_date = _timeutil.parse_date


def extract_amount_usd(text):
    """从文本里粗提取金额（$12M / 12 million / 1.2b / 5000万美元）。"""
    t = text or ""
    m = re.search(r"(?:US)?\$\s*([\d,.]+)\s*(million|billion|m|b|k)?", t, re.I)
    if not m:
        m = re.search(r"([\d,.]+)\s*(million|billion)\s*(?:dollars|usd)", t, re.I)
    if not m:
        m = re.search(r"([\d,.]+)\s*(万|亿)\s*(?:美元|美金|USD)", t)
        if m:
            n = float(m.group(1).replace(",", ""))
            unit = m.group(2)
            return n * (1e8 if unit == "亿" else 1e4)
        return None
    n = float(m.group(1).replace(",", ""))
    mult = {"m": 1e6, "million": 1e6, "b": 1e9, "billion": 1e9, "k": 1e3,
            "": 1}.get((m.group(2) or "").lower(), 1)
    return n * mult


def is_original_post(md, handle):
    """jina 抓的 X 主页 markdown 太脏，这里只做粗判：是否有该账号的原创文本。"""
    return handle.lower() in (md or "").lower()


# ---------------------------------------------------------------- 采集器
def collect_rss(src, state):
    raw = http_get(src["url"], timeout=20, headers=src.get("headers"))
    items = parse_rss(raw)
    out = []
    for it in items:
        url = it["url"]
        key = sha256(src["id"] + "|" + (url or it["title"]))
        if key in state.get("seen", {}):
            continue
        it.update({"id": key, "kind": "news"})
        out.append(it)
    return out


def collect_binance(src, state):
    raw = http_get(src["url"], timeout=20)
    keep = set(src.get("filter", {}).get("keep_catalogs", []))
    drop = set(src.get("filter", {}).get("drop_catalogs", []))
    watch = [a["symbol"] for a in load_config("watchlist.yaml").get("assets", [])]
    out = []
    for a in _ann.parse_binance(raw):
        cid = a.get("catalog_id")
        if cid in drop or (keep and cid not in keep):
            continue
        title = a["title"]
        # 上币类（catalog 48）：只保留 watchlist 标的
        if cid == 48 and watch and not any(w.lower() in title.lower() for w in watch):
            continue
        key = sha256(src["id"] + "|" + a["url"])
        if key in state.get("seen", {}):
            continue
        out.append({"id": key, "kind": "announcement", "title": title, "url": a["url"],
                    "body": title, "published_at": a["published_at"], "catalog_id": cid})
    return out


def collect_okx(src, state):
    raw = http_get(src["url"], timeout=20)
    out = []
    for a in _ann.parse_okx(raw):
        key = sha256(src["id"] + "|" + (a["url"] or a["title"]))
        if key in state.get("seen", {}):
            continue
        out.append({"id": key, "kind": "announcement", "title": a["title"], "url": a["url"],
                    "body": a["title"], "published_at": a["published_at"]})
    return out


def collect_bybit(src, state):
    raw = http_get(src["url"], timeout=20)
    out = []
    for a in _ann.parse_bybit(raw):
        key = sha256(src["id"] + "|" + (a["url"] or a["title"]))
        if key in state.get("seen", {}):
            continue
        out.append({"id": key, "kind": "announcement", "title": a["title"], "url": a["url"],
                    "body": a["body"], "published_at": a["published_at"]})
    return out


# ---------------------------------------------------------------- WARN 裁员备案（美国州政府公开数据）
_WARN_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def _warn_employer_rx():
    """交易所雇主名/别名正则（只保留跟 BTC/ETH/BNB 相关的公司）。"""
    ent = load_config("entities.yaml")
    names = []
    for name, meta in (ent.get("employers") or {}).items():
        names.append(name)
        names += (meta or {}).get("aliases") or []
    return re.compile("|".join(re.escape(n) for n in names if n), re.I) if names else None


def parse_ny_warn(html):
    """解析纽约州 DOL WARN 页面表格 → [{company, region, posted, dated}]"""
    if isinstance(html, bytes):
        html = html.decode("utf-8", "ignore")
    m = re.search(r"<table.*?</table>", html or "", re.S | re.I)
    if not m:
        return []
    rows = re.findall(r"<tr.*?</tr>", m.group(0), re.S | re.I)
    out = []
    for row in rows:
        cells = [re.sub(r"<[^>]+>", " ", c).strip() for c in
                 re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S | re.I)]
        cells = [re.sub(r"\s+", " ", c) for c in cells]
        if len(cells) < 4 or cells[0].lower().startswith("company"):
            continue
        out.append({"company": cells[0], "region": cells[1], "posted": cells[2], "dated": cells[3]})
    return out


def _excel_date(value):
    """Excel 序列日期 → ISO（1899-12-30 起）。非法返回空串。"""
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        return ""
    if n <= 0:
        return ""
    import datetime as _dt
    return (_dt.date(1899, 12, 30) + _dt.timedelta(days=n)).isoformat()


def parse_ca_warn_xlsx(raw):
    """解析加州 EDD WARN 报告 xlsx（Detailed WARN Report sheet）→ [{company, county, layoff, employees, notice_date, effective_date}]"""
    try:
        z = zipfile.ZipFile(BytesIO(raw))
    except (zipfile.BadZipFile, TypeError):
        return []
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        root = ET.fromstring(z.read("xl/sharedStrings.xml"))
        for si in root.findall("m:si", _WARN_NS):
            shared.append("".join(t.text or "" for t in si.iter(
                "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t")))
    # 找到含 "Company" 表头的 sheet（通常是 Detailed WARN Report）
    target = None
    for name in z.namelist():
        if not name.startswith("xl/worksheets/sheet"):
            continue
        root = ET.fromstring(z.read(name))
        rows = root.findall(".//m:row", _WARN_NS)
        for row in rows[:5]:
            texts = []
            for c in row.findall("m:c", _WARN_NS):
                v = c.find("m:v", _WARN_NS)
                val = v.text if v is not None else ""
                if c.get("t") == "s" and val != "":
                    val = shared[int(val)]
                texts.append(val)
            if "Company" in texts and any("Employees" in t for t in texts):
                target, header = name, texts
                break
        if target:
            break
    if not target:
        return []
    root = ET.fromstring(z.read(target))
    out = []
    for row in root.findall(".//m:row", _WARN_NS):
        vals = []
        for c in row.findall("m:c", _WARN_NS):
            v = c.find("m:v", _WARN_NS)
            val = v.text if v is not None else ""
            if c.get("t") == "s" and val != "":
                val = shared[int(val)]
            vals.append(val)
        if len(vals) < len(header) or vals[:1] == ["County/Parish"]:
            continue
        rec = {str(k).replace("\r", ""): v for k, v in zip(header, vals)}
        out.append({
            "county": rec.get("County/Parish", ""),
            "company": rec.get("Company", ""),
            "layoff": rec.get("Layoff/\nClosure", "") or rec.get("Layoff/Closure", ""),
            "employees": rec.get("No. Of\nEmployees", "") or rec.get("No. Of Employees", ""),
            "notice_date": _excel_date(rec.get("Notice\nDate")),
            "effective_date": _excel_date(rec.get("Effective \nDate")),
        })
    return out


def _warn_iso_date(text):
    """M/D/YYYY 或 ISO → ISO；解析不了返回空串。"""
    text = (text or "").strip()
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if m:
        import datetime as _dt
        try:
            return _dt.date(int(m.group(3)), int(m.group(1)), int(m.group(2))).isoformat()
        except ValueError:
            return ""
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    return m.group(0) if m else ""


def _warn_items(src, state, found, fmt):
    """把 WARN 记录过滤为交易所相关条目（只保留最近 30 天公告）。"""
    import datetime as _dt
    cutoff = (_dt.date.today() - _dt.timedelta(days=30)).isoformat()
    emp_rx = _warn_employer_rx()
    if emp_rx is None:
        return []
    out = []
    for rec in found:
        company = rec.get("company", "")
        if not emp_rx.search(company):
            continue
        if fmt == "ny":
            published = _warn_iso_date(rec.get("dated") or rec.get("posted"))
            if not published:
                continue
            title = "WARN 裁员备案：%s（%s，公告 %s）" % (company, rec.get("region", ""), published)
            body = "纽约州 WARN：%s 在 %s 提交裁员/关厂备案（日期 %s）。" % (
                company, rec.get("region", ""), published)
        else:
            published = rec.get("notice_date") or rec.get("effective_date")
            if not published:
                continue
            employees = str(rec.get("employees", "")).replace(".0", "")
            title = "WARN 裁员备案：%s %s %s 人（%s，生效 %s）" % (
                company, rec.get("layoff", ""), employees,
                rec.get("county", ""), rec.get("effective_date", ""))
            body = "加州 EDD WARN：%s 在 %s 备案 %s，影响 %s 人，生效 %s。" % (
                company, rec.get("county", ""), rec.get("layoff", ""),
                employees, rec.get("effective_date", ""))
        if published and published < cutoff:
            continue  # 历史备案不重复入库
        key = sha256(src["id"] + "|" + company + "|" + str(published))
        if key in state.get("seen", {}):
            continue
        out.append({"id": key, "kind": "news", "title": title, "url": src["url"],
                    "body": body, "published_at": published})
    return out


def collect_warn(src, state):
    fmt = (src.get("warn_format") or "ny").lower()
    if fmt == "ca":
        raw = http_get_bytes(src["url"], timeout=40)
        return _warn_items(src, state, parse_ca_warn_xlsx(raw), "ca")
    html = http_get(src["url"], timeout=25)
    return _warn_items(src, state, parse_ny_warn(html), "ny")


def collect_market(src, state):
    """行情/链上快照。只在触发阈值时产出一条 item。"""
    raw = http_get(src["url"], timeout=20)
    sid = src["id"]
    out = []
    if sid == "defillama_stablecoins":
        warn_bps = 50
        for a in _defillama.parse_stablecoins(raw):
            if a["dev_bps"] < warn_bps:
                continue
            key = sha256("%s|%s|%s" % (sid, a["symbol"], today_str()))
            if key in state.get("seen", {}):
                continue
            out.append({
                "id": key, "kind": "market",
                "title": "稳定币 %s 偏离锚定 %.0f bps" % (a["symbol"], a["dev_bps"]),
                "url": "https://defillama.com/stablecoins",
                "body": "price=%s dev=%.1fbps" % (a["price"], a["dev_bps"]),
                "published_at": ts_iso(), "asset_symbols": [a["symbol"]],
            })
    elif sid.startswith("binance_funding"):
        parsed = _binance.parse_funding(raw)
        if not parsed or abs(parsed["rate"]) < 0.0005:
            return out
        r = parsed["rate"]
        sym = parsed["symbol"]
        key = sha256("%s|%s|%s|%s" % (sid, sym, round(r, 6), today_str()))
        if key in state.get("seen", {}):
            return out
        out.append({
            "id": key, "kind": "market",
            "title": "%s 资金费率异动 %+.4f%%" % (sym, r * 100),
            "url": "https://www.binance.com/en/futures/%s" % sym,
            "body": "funding_rate=%.6f mark=%s" % (r, parsed["mark_price"]),
            "published_at": ts_iso(), "asset_symbols": [sym.replace("USDT", "")],
        })
    return out


def collect_jina(src, state):
    """r.jina.ai 代理页面：X 主页 / Farside 表格。用内容 hash 做变更检测。"""
    text, h = _tg.fingerprint(http_get(src["url"], timeout=45))
    kind = "social" if "x.com" in src["url"] else "etf_flow"
    out = []
    if h != state.get("hash"):
        handle = ""
        if "x.com/" in src["url"]:
            handle = src["url"].rsplit("x.com/", 1)[-1].strip("/")
        out.append({
            "id": sha256(src["id"] + "|" + h),
            "kind": kind,
            "title": ("%s 主页更新" % (handle or src["name"])),
            "url": src["url"].replace("https://r.jina.ai/", ""),
            "body": text[:4000],
            "published_at": ts_iso(),
            "handle": handle,
        })
    return out, h


def collect_html(src, state):
    """通用网页变更检测（无 RSS 的站点，如 BNB Chain 博客/文档索引）。

    只对「可见文本」做指纹与入库；CSS/JS/JSON-LD 等垃圾页面变化直接忽略。
    """
    raw = http_get(src["url"], timeout=25)
    visible = _textlib.html_to_text(raw)
    h = sha256(visible)
    out = []
    if h != state.get("hash") and not _textlib.looks_like_junk_page(visible):
        out.append({
            "id": sha256(src["id"] + "|" + h),
            "kind": "page_update",
            "title": "%s 页面更新" % src.get("name", src["id"]),
            "url": src["url"],
            "body": visible[:4000],
            "published_at": ts_iso(),
        })
    return out, h


def collect_tme(src, state):
    raw = http_get(src["url"], timeout=20)
    channel = src["url"].rsplit("/s/", 1)[-1]
    out = []
    for it in _tg.parse_telegram_preview(raw, channel):
        key = sha256(src["id"] + "|" + it["key"])
        if key in state.get("seen", {}):
            continue
        it["id"] = key
        it["kind"] = "announcement"
        out.append(it)
    return out


# ---------------------------------------------------------------- 主流程
HANDLERS = {
    "binance_ann": collect_binance,
    "okx_ann": collect_okx,
    "bybit_ann": collect_bybit,
}


def main():
    ensure_dirs()
    args = sys.argv[1:]
    only = None
    limit = None
    for i, a in enumerate(args):
        if a == "--only" and i + 1 < len(args):
            only = set(args[i + 1].split(","))
        if a == "--limit" and i + 1 < len(args):
            limit = int(args[i + 1])

    sources = load_config("sources.yaml").get("sources", [])
    sources = [s for s in sources if s.get("verified")]
    if only:
        sources = [s for s in sources if s["id"] in only]
    if limit:
        sources = sources[:limit]

    date = today_str()
    out_path = os.path.join(RAW_DIR, date, "items.jsonl")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    global_seen_path = os.path.join(STATE_DIR, "seen.json")
    global_seen = safe_json_load(global_seen_path, {})
    if global_seen.get("date") != date:
        global_seen = {"date": date, "ids": {}}

    stats = {"date": date, "started_at": ts_iso(), "sources": [], "errors": []}
    total_new = 0

    for src in sources:
        sid = src["id"]
        st_path = os.path.join(STATE_DIR, sid + ".json")
        state = safe_json_load(st_path, {})
        state.setdefault("seen", {})
        t0 = time.time()
        new_items, new_hash = [], None
        try:
            h = HANDLERS.get(sid)
            if h:
                new_items = h(src, state)
            elif src["type"] == "rss":
                new_items = collect_rss(src, state)
            elif src["type"] == "market_api":
                new_items = collect_market(src, state)
            elif src["type"] == "warn":
                new_items = collect_warn(src, state)
            elif src["type"] == "html_diff" and "t.me/s/" in src["url"]:
                new_items = collect_tme(src, state)
            elif src["type"] == "html_diff" and "r.jina.ai" in src["url"]:
                new_items, new_hash = collect_jina(src, state)
            elif src["type"] == "html_diff":
                new_items, new_hash = collect_html(src, state)
            else:
                stats["sources"].append({"id": sid, "status": "skipped", "type": src["type"]})
                continue
            ok = True
        except Exception as e:  # noqa
            ok = False
            stats["errors"].append({"id": sid, "error": "%s: %s" % (type(e).__name__, e)})

        elapsed = round(time.time() - t0, 2)
        stats["sources"].append({"id": sid, "ok": ok, "new": len(new_items), "seconds": elapsed})

        # 落盘 + 状态
        if new_items:
            with open(out_path, "a", encoding="utf-8") as f:
                for it in new_items:
                    rec = dict(it)
                    rec.update({"source_id": sid, "source_name": src.get("name", sid),
                                "tier": src.get("tier", "T3"), "fetched_at": ts_iso()})
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    state["seen"][it["id"]] = 1
                    global_seen["ids"][it["id"]] = 1
                    total_new += 1
        if new_hash:
            state["hash"] = new_hash
        # 控制 seen 规模
        if len(state["seen"]) > 3000:
            state["seen"] = dict(list(state["seen"].items())[-1000:])
        state["last_checked"] = ts_iso()
        safe_json_dump(st_path, state)
        print("  %-22s %-4s new=%-3d %5.2fs" % (sid, "OK" if ok else "ERR", len(new_items), elapsed))

    # 全局去重表
    if len(global_seen["ids"]) > 20000:
        global_seen["ids"] = dict(list(global_seen["ids"].items())[-5000:])
    safe_json_dump(global_seen_path, global_seen)

    stats["finished_at"] = ts_iso()
    stats["total_new"] = total_new
    safe_json_dump(os.path.join(LOG_DIR, "fetch-%s.json" % date), stats)

    ok_n = sum(1 for s in stats["sources"] if s.get("ok"))
    print("\n采集完成：%d 个源，成功 %d，失败 %d，新增 %d 条"
          % (len(sources), ok_n, len(stats["errors"]), total_new))
    if stats["errors"]:
        print("失败明细：")
        for e in stats["errors"]:
            print("  - %(id)s: %(error)s" % e)


if __name__ == "__main__":
    main()
