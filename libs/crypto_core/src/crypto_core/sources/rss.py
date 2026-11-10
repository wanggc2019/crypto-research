# -*- coding: utf-8 -*-
"""RSS / Atom 解析（标准库 ElementTree）。"""
from xml.etree import ElementTree as ET

from .. import text, timeutil


def local(tag):
    return tag.split("}")[-1]


def parse_feed(raw):
    """解析 RSS/Atom，返回 [{title,url,body,published_at}]；解析失败返回 []。"""
    items = []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return items
    for el in root.iter():
        if local(el.tag) not in ("item", "entry"):
            continue
        title = link = desc = pub = ""
        for c in list(el):
            n = local(c.tag)
            if n == "title":
                title = text.clean_text("".join(c.itertext()))
            elif n == "link":
                link = (c.text or "").strip() or c.attrib.get("href", "")
            elif n in ("description", "summary", "content", "encoded"):
                desc = desc or text.clean_text("".join(c.itertext()))
            elif n in ("pubDate", "published", "updated", "date"):
                pub = (c.text or "").strip()
        if title or link:
            items.append(
                {
                    "title": title,
                    "url": text.norm_url(link),
                    "body": desc[:2000],
                    "published_at": timeutil.parse_date(pub) or timeutil.ts_iso(),
                }
            )
    return items


# 兼容旧名称
parse_rss = parse_feed
