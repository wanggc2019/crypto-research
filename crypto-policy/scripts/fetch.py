#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fetch.py —— 采集层（列表 → 新条目 → 详情正文），只落盘，不推送。

用法：
    python3 scripts/fetch.py --dry-run
    python3 scripts/fetch.py --fixtures tests/fixtures   # 离线回放（不联网）
    python3 scripts/fetch.py --only pbc_tiaofasi
    python3 scripts/fetch.py --json

输出：
    data/raw/<date>/items.jsonl     归一化条目（追加）
    data/docs/<id>.json             详情正文 + 元数据
    data/state/<source>.json        已见 id / 基线 / 最近检查
    data/logs/fetch-<date>.json     本轮统计

约定：
- 首次运行（state 里 initialized=false）只做基线，不产生推送；
- 单源失败不拖垮整轮：记录 errors，继续跑其他源；
- 正文只保留前 body_max_chars 个字符，避免 data 膨胀。
"""
import argparse
import datetime
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from html.parser import HTMLParser  # noqa: E402

from common import (  # noqa: E402
    DOC_DIR,
    LOG_DIR,
    RAW_DIR,
    STATE_DIR,
    append_jsonl,
    clean_text,
    ensure_dirs,
    load_config,
    norm_url,
    safe_json_dump,
    safe_json_load,
    sha256,
    today_str,
    ts_iso,
)

try:  # 延迟到 runtime，fixtures 模式不联网
    from crypto_core import http as _http
    from crypto_core.sources import rss as _rss
except ImportError:  # pragma: no cover - common 已把 libs 放进 sys.path
    _http = None
    _rss = None

_ARTICLE_HINT = re.compile(r"(\d{4}[-年/.]\d{1,2}[-月/.]\d{1,2})")


class _ListParser(HTMLParser):
    """收集列表页里的 <a href>，保留标题与 title 属性。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self._cur = None

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        d = dict(attrs)
        href = (d.get("href") or "").strip()
        if not href:
            return
        self._cur = {"href": href, "title_attr": clean_text(d.get("title", "")), "text": []}

    def handle_data(self, data):
        if self._cur is not None:
            self._cur["text"].append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._cur is not None:
            text = clean_text("".join(self._cur["text"]))
            title = self._cur["title_attr"] or text
            if title:
                self.links.append({"href": self._cur["href"], "title": title})
            self._cur = None


class _DetailParser(HTMLParser):
    """收集详情页的标题、段落、日期线索。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title_parts = []
        self.heading = ""
        self.paragraphs = []
        self._in_title = False
        self._in_h = False
        self._p = []

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        elif tag in ("h1", "h2") and not self.heading:
            self._in_h = True
        elif tag == "p":
            self._p = []

    def handle_data(self, data):
        if self._in_title:
            self.title_parts.append(data)
        elif self._in_h:
            self.heading += data
        if self._p is not None:
            self._p.append(data)

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag in ("h1", "h2"):
            self._in_h = False
        elif tag == "p":
            text = clean_text("".join(self._p))
            if len(text) >= 8:
                self.paragraphs.append(text)
            self._p = []


def _abs_url(base, href):
    from urllib.parse import urljoin

    return urljoin(base or "", href)


def _date_from_url(url):
    """从 URL 里猜发布日期：PBC /2026020619591971323/、SAFE /2026/0929/。"""
    m = re.search(r"/(20\d{2})(\d{2})(\d{2})\d{6,}/", url or "")
    if m:
        return "%s-%s-%s" % m.groups()
    m = re.search(r"/(20\d{2})/(\d{2})(\d{2})/", url or "")
    if m:
        return "%s-%s-%s" % m.groups()
    return ""


def parse_list(html_text, source):
    """列表页 → [{title,url}]，按 item_href_re 过滤并去重。"""
    parser = _ListParser()
    parser.feed(html_text or "")
    pattern = re.compile(source.get("item_href_re") or r".*")
    base = source.get("base_url") or ""
    out, seen = [], set()
    for link in parser.links:
        href = link["href"]
        if not pattern.search(href):
            continue
        url = norm_url(_abs_url(base, href))
        if url in seen:
            continue
        seen.add(url)
        out.append({"title": link["title"], "url": url, "published_at_hint": _date_from_url(url)})
    return out


def _title_from_html(html_text):
    """从 <div class="title"> 里取文章标题（法院页 <title> 只有栏目名）。"""
    m = re.search(r'<div[^>]*class="[^"]*title[^"]*"[^>]*>(.*?)</div>', html_text or "", re.S | re.I)
    if not m:
        return ""
    title = clean_text(m.group(1))
    return title if 4 <= len(title) <= 200 else ""


def _fallback_body(html_text):
    """正文兜底：从 class 含 txt/content/TRS_Editor 的 div/td 里抽文本。"""
    m = re.search(
        r'<(?:div|td|section)[^>]*class="[^"]*(?:txt|content|TRS_Editor|article)[^"]*"[^>]*>(.*?)</(?:div|td|section)>',
        html_text or "",
        re.S | re.I,
    )
    if not m:
        return ""
    txt = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", m.group(1), flags=re.S | re.I)
    txt = re.sub(r"</p>|<br\s*/?>", "\n", txt, flags=re.I)
    return clean_text(txt)


def parse_detail(html_text):
    """详情页 → {title, body, published_at}。"""
    parser = _DetailParser()
    parser.feed(html_text or "")
    title = clean_text("".join(parser.title_parts))
    # 去掉「_中国人民银行」「- 中国政府网」之类的站点后缀
    title = re.split(r"\s*[_|]\s*", title)[0].strip()
    if parser.heading and len(parser.heading) >= len(title) * 0.6:
        title = clean_text(parser.heading) or title
    div_title = _title_from_html(html_text)
    if div_title and not re.search(r"关于|通知|解释|规定|办法|公告|案例|批复|决定|意见", title):
        title = div_title
    body = "\n".join(parser.paragraphs)
    if len(parser.paragraphs) < 3:  # 部分法院页正文在 div 里，<p> 很少
        body = _fallback_body(html_text) or body
    published_at = None
    m = _ARTICLE_HINT.search(body[:2000]) or _ARTICLE_HINT.search(body[-800:]) or _ARTICLE_HINT.search(title)
    if m:
        parts = re.findall(r"\d+", m.group(1))
        if len(parts) >= 3:
            published_at = "%s-%02d-%02d" % (parts[0], int(parts[1]), int(parts[2]))
    return {"title": title, "body": body, "published_at": published_at}


def parse_list_markdown(text, source):
    """r.jina.ai 返回的 Markdown 列表 → [{title,url}]。"""
    pattern = re.compile(source.get("item_href_re") or r".*")
    out, seen = [], set()
    for m in re.finditer(r"\[([^\]]{4,200})\]\((https?://[^)\s]+)\)", text or ""):
        title = clean_text(m.group(1))
        url = norm_url(m.group(2))
        if not pattern.search(url) or url in seen:
            continue
        seen.add(url)
        out.append({"title": title, "url": url, "published_at_hint": _date_from_url(url)})
    return out


def parse_detail_markdown(text):
    """r.jina.ai 返回的 Markdown 详情 → {title, body, published_at}。"""
    title = ""
    lines = []
    for line in (text or "").splitlines():
        s = line.strip()
        if not s:
            continue
        if not title and s.startswith("# "):
            title = clean_text(s[2:])
            continue
        if s.startswith(("#", "![", "|", ">")):
            continue
        if s.startswith("[") and "](" in s:
            continue
        s = clean_text(s)
        if len(s) >= 8:
            lines.append(s)
    body = "\n".join(lines[:400])
    published_at = None
    m = _ARTICLE_HINT.search(body[:2000]) or _ARTICLE_HINT.search(title)
    if m:
        parts = re.findall(r"\d+", m.group(1))
        if len(parts) >= 3:
            published_at = "%s-%02d-%02d" % (parts[0], int(parts[1]), int(parts[2]))
    return {"title": title, "body": body, "published_at": published_at}


def _state_path(source_id):
    return os.path.join(STATE_DIR, "source-%s.json" % source_id)


def _load_source_html(source, fixtures):
    if fixtures:
        candidate = os.path.join(fixtures, "%s_list.html" % source["id"])
        if not os.path.isfile(candidate):
            raise OSError("fixtures 缺少列表页：%s" % candidate)
        with open(candidate, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    url = source["url"]
    if source.get("reader") == "jina":
        url = "https://r.jina.ai/" + url
        return _http.get_text(url, timeout=45, retries=0)
    return _http.get_text(url, timeout=int(source.get("list_timeout") or 15),
                          retries=int(source.get("list_retries") or 0))


def _load_rss(source, fixtures):
    """RSS/Atom 源 → [{title,url,published_at,summary}]。"""
    if fixtures:
        candidate = os.path.join(fixtures, "%s_feed.xml" % source["id"])
        if not os.path.isfile(candidate):
            raise OSError("fixtures 缺少 RSS：%s" % candidate)
        with open(candidate, "rb") as f:
            raw = f.read()
    else:
        raw = _http.get(source["url"], timeout=int(source.get("list_timeout") or 20),
                        retries=int(source.get("list_retries") or 0))
    parsed = _rss.parse_feed(raw)
    cap = int(source.get("max_items") or 40)
    out = []
    for it in parsed[:cap]:
        out.append(
            {
                "title": it.get("title") or "",
                "url": it.get("url") or "",
                "published_at_hint": (it.get("published_at") or "")[:10],
                "summary": it.get("body") or "",
            }
        )
    return out


def _load_federal_register(source, fixtures):
    """美国 Federal Register API → 规则/拟议规则/公告（含 doc_type_hint）。"""
    if fixtures:
        candidate = os.path.join(fixtures, "%s.json" % source["id"])
        if not os.path.isfile(candidate):
            raise OSError("fixtures 缺少 FR JSON：%s" % candidate)
        with open(candidate, "r", encoding="utf-8", errors="ignore") as f:
            data = json.load(f)
    else:
        queries = source.get("queries") or []
        if queries:
            from urllib.parse import quote

            results, seen = [], set()
            for q in queries:
                url = ("https://www.federalregister.gov/api/v1/documents.json"
                       "?per_page=15&order=newest&conditions%5Bterm%5D=" + quote(q))
                raw = _http.get(url, timeout=int(source.get("list_timeout") or 30),
                                retries=int(source.get("list_retries") or 1))
                for r in (json.loads(raw.decode("utf-8", "ignore")).get("results") or []):
                    num = r.get("document_number")
                    if num and num in seen:
                        continue
                    seen.add(num)
                    results.append(r)
            results.sort(key=lambda r: r.get("publication_date") or "", reverse=True)
            data = {"results": results}
        else:
            raw = _http.get(source["url"], timeout=int(source.get("list_timeout") or 30),
                            retries=int(source.get("list_retries") or 1))
            data = json.loads(raw.decode("utf-8", "ignore"))
    max_age = int(source.get("max_age_days") or 0)
    if max_age:
        cutoff = (datetime.date.today() - datetime.timedelta(days=max_age)).isoformat()
        data = {"results": [r for r in (data.get("results") or []) if (r.get("publication_date") or "") >= cutoff]}
    hint_map = {"Rule": "final_rule", "Proposed Rule": "proposed_rule", "Notice": "notice"}
    out = []
    for r in (data.get("results") or [])[: int(source.get("max_items") or 40)]:
        agencies = r.get("agencies") or []
        names = []
        for a in agencies[:3]:
            names.append(clean_text(a.get("name") if isinstance(a, dict) else str(a)))
        out.append(
            {
                "title": clean_text(r.get("title") or ""),
                "url": r.get("html_url") or "",
                "published_at_hint": r.get("publication_date") or "",
                "summary": clean_text(r.get("abstract") or ""),
                "doc_type_hint": hint_map.get(r.get("type") or "", "notice"),
                "doc_no_hint": r.get("document_number") or "",
                "issuer_hint": " · ".join([n for n in names if n]),
                "effective_hint": r.get("effective_on") or "",
            }
        )
    return out


def _load_detail_html(url, fixtures, source_id, item_id, reader="direct", timeout=20, retries=0):
    if fixtures:
        for name in ("%s_detail.html" % source_id, "%s_detail_%s.html" % (source_id, item_id)):
            candidate = os.path.join(fixtures, name)
            if os.path.isfile(candidate):
                with open(candidate, "r", encoding="utf-8", errors="ignore") as f:
                    return f.read()
        return ""
    if reader == "jina":
        url = "https://r.jina.ai/" + url
        timeout = max(timeout, 45)
    return _http.get_text(url, timeout=timeout, retries=retries)


def collect_source(source, app, fixtures=None, detail_budget=None, limit=None, dry_run=False):
    """采集单个源，返回 (items, errors, stats)。items 带 baseline 标记。"""
    source_id = source["id"]
    state = safe_json_load(_state_path(source_id), {"initialized": False, "seen": {}})
    baseline = not bool(state.get("initialized"))
    errors = []
    stats = {"source": source_id, "list_ok": False, "found": 0, "new": 0, "detail_ok": 0, "detail_fail": 0}

    reader = source.get("reader") or "direct"
    detail_reader = source.get("detail_reader") or reader
    src_type = source.get("type") or "list"
    try:
        if src_type == "rss":
            links = _load_rss(source, fixtures)
        elif src_type == "federal_register":
            links = _load_federal_register(source, fixtures)
        else:
            html_text = _load_source_html(source, fixtures)
            links = parse_list_markdown(html_text, source) if reader == "jina" else parse_list(html_text, source)
        stats["list_ok"] = True
        stats["found"] = len(links)
    except Exception as e:  # noqa: BLE001 - 单源失败不拖垮整轮
        errors.append("%s: %s" % (source_id, e))
        state["last_check"] = ts_iso()
        state["last_error"] = str(e)[:300]
        safe_json_dump(_state_path(source_id), state)
        return [], errors, stats

    cap = limit or int(app.get("max_new_per_source") or 20)
    fresh = []
    seen_urls = set()
    for link in links:
        if not link.get("url") or link["url"] in seen_urls:
            continue  # 同轮去重（多 query 合并可能重复）
        seen_urls.add(link["url"])
        item_id = sha256(source_id + "|" + link["url"])[:24]
        if item_id in state.get("seen", {}):
            continue
        fresh.append(
            {
                "id": item_id,
                "title": link["title"],
                "url": link["url"],
                "published_at_hint": link.get("published_at_hint") or "",
                "summary": link.get("summary") or "",
                "doc_type_hint": link.get("doc_type_hint") or "",
                "doc_no_hint": link.get("doc_no_hint") or "",
                "issuer_hint": link.get("issuer_hint") or "",
                "effective_hint": link.get("effective_hint") or "",
            }
        )
        if len(fresh) >= cap:
            break

    # 详情预算有限时，优先抓标题命中主题的条目（42 号这类文件不在列表最前面）
    topics = app.get("topics") or []

    def _title_hit(it):
        low = (it.get("title") or "").lower()
        return 0 if any(str(t).lower() in low for t in topics) else 1

    fresh.sort(key=_title_hit)

    body_max = int(app.get("body_max_chars") or 20000)
    per_source = min(int(app.get("max_detail_per_source") or 5), int(source.get("detail_limit") or 99))
    detail_timeout = int(source.get("detail_timeout") or 20)
    local_budget = [per_source if detail_budget is None else min(per_source, detail_budget[0])]
    out = []
    new_backlog = []
    for item in fresh:
        body = ""
        detail_title = ""
        raw_detail = {}
        try:
            if local_budget[0] > 0:
                if source_id.startswith("csrc"):
                    time.sleep(1.2)  # 证监会反爬：连续请求会超时
                raw = _load_detail_html(item["url"], fixtures, source_id, item["id"],
                                        reader=detail_reader, timeout=detail_timeout)
                local_budget[0] -= 1
                if detail_budget is not None:
                    detail_budget[0] -= 1
                if raw:
                    raw_detail = parse_detail_markdown(raw) if detail_reader == "jina" else parse_detail(raw)
                    detail_title = raw_detail["title"]
                    body = raw_detail["body"][:body_max]
                    stats["detail_ok"] += 1
                else:
                    stats["detail_fail"] += 1
            elif per_source > 0:
                new_backlog.append(item["id"])
        except Exception as e:  # noqa: BLE001 - 详情失败只降级，不算源失败
            stats["detail_fail"] += 1
            stats.setdefault("detail_errors", []).append("%s: %s" % (item["url"], e))

        if not body:
            body = item.get("summary") or ""  # RSS 摘要兜底

        published_at = item.get("published_at_hint") or ""
        if raw_detail.get("published_at"):
            published_at = raw_detail["published_at"]

        rec = {
            "id": item["id"],
            "source_id": source_id,
            "source_name": source.get("name", source_id),
            "tier": source.get("tier", "T2"),
            "jurisdiction": source.get("jurisdiction") or "CN",
            "title": clean_text(detail_title) or item["title"],
            "url": item["url"],
            "body": body,
            "body_hash": sha256(body) if body else "",
            "published_at": published_at,
            "doc_type_hint": item.get("doc_type_hint") or "",
            "doc_no_hint": item.get("doc_no_hint") or "",
            "issuer_hint": item.get("issuer_hint") or "",
            "effective_hint": item.get("effective_hint") or "",
            "baseline": baseline,
            "first_seen": ts_iso(),
        }
        if not dry_run:
            safe_json_dump(os.path.join(DOC_DIR, item["id"] + ".json"), rec)
            append_jsonl(os.path.join(RAW_DIR, today_str(), "items.jsonl"), rec)
            state.setdefault("seen", {})[item["id"]] = ts_iso()
        out.append(rec)
        stats["new"] += 1

    # 详情预算用完后把 id 记入 backlog，后续每轮补抓少量（避免 crypto 司法解释因标题没关键词而漏掉正文）
    # 本轮新条目只进 backlog，下一轮再补抓；否则同一轮会把同一文件写入两次
    prev_backlog = [i for i in (state.get("backlog") or []) if i not in set(new_backlog)]
    backlog = prev_backlog + new_backlog
    backfill_n = int(app.get("backfill_per_source") or 3)
    done_bf = []
    for bid in prev_backlog[:backfill_n]:
        doc = safe_json_load(os.path.join(DOC_DIR, bid + ".json"), {})
        if not doc:
            done_bf.append(bid)
            continue
        if len(doc.get("body") or "") > 200:
            done_bf.append(bid)
            continue
        try:
            if source_id.startswith("csrc"):
                time.sleep(1.2)
            raw = _load_detail_html(doc["url"], fixtures, source_id, bid,
                                    reader=detail_reader, timeout=detail_timeout)
            if raw:
                detail = parse_detail_markdown(raw) if detail_reader == "jina" else parse_detail(raw)
                doc["body"] = detail["body"][:body_max]
                doc["body_hash"] = sha256(doc["body"])
                doc["published_at"] = doc.get("published_at") or detail["published_at"] or ""
                doc["baseline"] = True  # 补抓的旧条目只入库，不推送
                if not dry_run:
                    safe_json_dump(os.path.join(DOC_DIR, bid + ".json"), doc)
                out.append(doc)
                stats["backfill"] = stats.get("backfill", 0) + 1
            done_bf.append(bid)
        except Exception:  # noqa: BLE001 - 失败留在 backlog 下轮再试
            pass
    state["backlog"] = [i for i in backlog if i not in done_bf]

    state["initialized"] = True
    state["last_check"] = ts_iso()
    state["last_error"] = ""
    if not dry_run:
        safe_json_dump(_state_path(source_id), state)
    return out, errors, stats


def run(app=None, fixtures=None, only=None, limit=None, dry_run=False):
    """采集全部（或指定）源，返回 {items, errors, stats}。"""
    ensure_dirs()
    app = app or load_config("app.yaml")
    sources = (load_config("sources.yaml") or {}).get("sources") or []
    sources = [s for s in sources if s.get("enabled", True) is not False]
    if only:
        wanted = {s.strip() for s in str(only).split(",") if s.strip()}
        sources = [s for s in sources if s["id"] in wanted]
    if fixtures:
        sources = [s for s in sources if os.path.isfile(os.path.join(fixtures, s["id"] + "_list.html"))]
        if not sources:
            return {"items": [], "errors": ["fixtures 目录里没有任何源的列表页"], "stats": []}

    detail_budget = [int(app.get("detail_fetch_limit") or 12)]
    items, errors, stats = [], [], []
    for source in sources:
        print("→ %s" % source["id"], flush=True)
        src_items, src_errors, src_stats = collect_source(
            source, app, fixtures=fixtures, detail_budget=detail_budget, limit=limit, dry_run=dry_run
        )
        items.extend(src_items)
        errors.extend(src_errors)
        stats.append(src_stats)

    log = {"ts": ts_iso(), "fixtures": bool(fixtures), "stats": stats, "errors": errors}
    if not dry_run:
        safe_json_dump(os.path.join(LOG_DIR, "fetch-%s.json" % today_str()), log)
    return {"items": items, "errors": errors, "stats": stats}


def main(argv=None):
    parser = argparse.ArgumentParser(description="crypto-policy 采集（只落盘，不推送）")
    parser.add_argument("--dry-run", action="store_true", help="只打印，不写数据")
    parser.add_argument("--fixtures", help="离线回放目录（CI 用，禁止联网）")
    parser.add_argument("--only", help="只跑指定源（逗号分隔）")
    parser.add_argument("--limit", type=int, help="单源最多处理条数")
    parser.add_argument("--json", action="store_true", help="机器可读输出")
    args = parser.parse_args(argv)

    if args.dry_run:
        sources = (load_config("sources.yaml") or {}).get("sources") or []
        print("[dry-run] %d 个源：%s" % (len(sources), ", ".join(s["id"] for s in sources)))
        for s in sources:
            print("  - %s  %s" % (s["id"], s["url"]))
        return 0

    res = run(fixtures=args.fixtures, only=args.only, limit=args.limit)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        print("采集完成：新增 %d 条，错误 %d 条" % (len(res["items"]), len(res["errors"])))
        for s in res["stats"]:
            print("  %-14s found=%-3d new=%-3d detail_ok=%-3d fail=%d"
                  % (s["source"], s["found"], s["new"], s["detail_ok"], s["detail_fail"]))
        for e in res["errors"][:5]:
            print("  ! %s" % e)
    return 1 if res["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
