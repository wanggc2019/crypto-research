# -*- coding: utf-8 -*-
"""Telegram 公开预览页（t.me/s/<channel>）与通用页面变更检测。"""
import re

from .. import text, timeutil


def parse_telegram_preview(raw, channel):
    """解析 t.me/s/<channel>，返回 [{key,title,url,body,published_at}]。"""
    page = raw.decode("utf-8", "ignore")
    posts = re.findall(r'data-post="([^"]+)"', page)
    blocks = re.findall(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', page, re.S)
    out = []
    seen = set()
    for i, post in enumerate(posts):
        if post in seen:
            continue
        seen.add(post)
        msg = text.clean_text(blocks[i]) if i < len(blocks) else ""
        if not msg:
            continue
        out.append(
            {
                "key": post,
                "title": msg[:120],
                "url": "https://t.me/%s/%s" % (channel, post.split("/")[-1]),
                "body": msg[:2000],
                "published_at": timeutil.ts_iso(),
            }
        )
    return out


def fingerprint(raw):
    """页面内容指纹：返回 (解码文本, sha256)。用于无 RSS 站点的变更检测。"""
    page = raw.decode("utf-8", "ignore")
    return page, text.sha256(page)


def plain_text(raw):
    """页面 → 纯文本（去标签/空白压缩），适合正文入库。"""
    return text.clean_text(raw.decode("utf-8", "ignore"))
