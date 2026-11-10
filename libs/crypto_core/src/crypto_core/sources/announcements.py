# -*- coding: utf-8 -*-
"""交易所公告解析：Binance(CMS JSON) / OKX(v5) / Bybit(v5)。

只做结构解析，不判断「该不该留」——白名单/上币币种过滤由模块做。
"""
import datetime
import json

from .. import text, timeutil


def _ts_to_iso(ts):
    if not ts:
        return timeutil.ts_iso()
    try:
        return datetime.datetime.fromtimestamp(int(ts) / 1000).astimezone().isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError):
        return timeutil.ts_iso()


def parse_binance(raw):
    """Binance CMS 公告，返回 [{title,url,published_at,catalog_id}]。"""
    d = json.loads(raw.decode("utf-8"))
    out = []
    for cat in (d.get("data", {}) or {}).get("catalogs", []) or []:
        cid = cat.get("catalogId")
        for a in cat.get("articles", []) or []:
            title = text.clean_text(a.get("title", ""))
            code = a.get("code", "")
            url = text.norm_url("https://www.binance.com/en/support/announcement/%s" % code)
            out.append(
                {
                    "title": title,
                    "url": url,
                    "published_at": _ts_to_iso(a.get("releaseDate")),
                    "catalog_id": cid,
                }
            )
    return out


def parse_okx(raw):
    """OKX v5 support/announcements，返回 [{title,url,published_at}]。"""
    d = json.loads(raw.decode("utf-8"))
    out = []
    for group in d.get("data", []) or []:
        for det in group.get("details", []) or []:
            title = text.clean_text(det.get("title", ""))
            out.append(
                {
                    "title": title,
                    "url": text.norm_url(det.get("url", "")),
                    "published_at": _ts_to_iso(det.get("publishDate")),
                }
            )
    return out


def parse_bybit(raw):
    """Bybit v5 announcements，返回 [{title,url,body,published_at}]。"""
    d = json.loads(raw.decode("utf-8"))
    out = []
    for a in (d.get("result", {}) or {}).get("list", []) or []:
        out.append(
            {
                "title": text.clean_text(a.get("title", "")),
                "url": text.norm_url(a.get("url", "")),
                "body": text.clean_text(a.get("description", ""))[:2000],
                "published_at": _ts_to_iso(a.get("publishTime") or a.get("dateTimestamp")),
            }
        )
    return out
