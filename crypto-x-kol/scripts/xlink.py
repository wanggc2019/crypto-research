#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""xlink.py —— 给告警推文补「原帖链接」（按需抓 X 主页 HTML 解析 /status/<id>）。

为什么需要：r.jina.ai 的 Markdown 模式不暴露每条推文的 status id，只有 `@handle` 主页链接可用；
告警需要指向**具体推文**。做法：只对真正要推送的 P0/P1（少量）额外抓一次 HTML（`X-Respond-With: html`），
用 `<article>` 块里的 `/status/<id>` + 正文文本匹配还原 tweet URL。失败则回退主页链接、不阻塞推送。

纯解析函数可离线测试；网络只在 attach() 里发生。
"""
import difflib
import html as html_lib
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import load_config  # noqa: E402
from crypto_core import http as _http  # noqa: E402

ARTICLE_RE = re.compile(r"<article\b[\s\S]*?</article>", re.I)
STATUS_RE = re.compile(r"/status/(\d+)")
TAG_RE = re.compile(r"<[^>]+>")
URL_RE = re.compile(r"https?://\S+")
NORM_RE = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff]+")


def norm(text):
    """匹配用归一化：去标签/URL/标点/空白，小写。"""
    s = TAG_RE.sub(" ", text or "")
    s = html_lib.unescape(s)
    s = URL_RE.sub(" ", s)
    return NORM_RE.sub("", s).lower()


def parse_articles(page):
    """从 X 主页 HTML 提取 [{tweet_id, text}]；没有 article 时返回 []。"""
    out = []
    for block in ARTICLE_RE.findall(page or ""):
        m = STATUS_RE.search(block)
        if not m:
            continue
        out.append({"tweet_id": m.group(1), "text": norm(block)})
    return out


def match_tweet(post_text, articles):
    """返回匹配到的 tweet_id（找不到返回 None）。"""
    target = norm(post_text)
    if not target or not articles:
        return None
    for size in (60, 40, 20, 12):
        snippet = target[:size]
        for art in articles:
            if snippet and snippet in art["text"]:
                return art["tweet_id"]
    best, best_ratio = None, 0.0
    head = target[:120]
    for art in articles:
        ratio = difflib.SequenceMatcher(None, head, art["text"][:200]).ratio()
        if ratio > best_ratio:
            best, best_ratio = art, ratio
    return best["tweet_id"] if best and best_ratio >= 0.72 else None


def fetch_html(handle, cfg, log=print):
    fetch = (cfg or {}).get("fetch") or {}
    url = "%shttps://x.com/%s" % (fetch.get("jina_prefix") or "https://r.jina.ai/", handle)
    return _http.get_text(
        url,
        timeout=float(fetch.get("tweet_link_timeout_seconds") or 60),
        headers={"X-Respond-With": "html"},
        retries=int(fetch.get("tweet_link_retries") or 1),
        backoff=2.0,
    )


def attach(posts, cfg=None, log=print):
    """给 posts 原地补 tweet_url（同 handle 只抓一次 HTML）；失败静默回退主页。"""
    fetch = (cfg or {}).get("fetch") or {}
    if not fetch.get("tweet_link", True):
        return posts
    by_handle = {}
    for post in posts:
        by_handle.setdefault(post.get("handle"), []).append(post)
    for handle, group in by_handle.items():
        try:
            articles = parse_articles(fetch_html(handle, cfg, log=log))
        except Exception as exc:  # noqa: BLE001 - 找链接失败不影响告警
            log("  [xlink] @%s 取 HTML 失败，回退主页链接：%s" % (handle, str(exc)[:80]))
            continue
        if not articles:
            log("  [xlink] @%s HTML 里没有 article/status" % handle)
            continue
        for post in group:
            tweet_id = match_tweet(post.get("text"), articles)
            if tweet_id:
                post["tweet_url"] = "https://x.com/%s/status/%s" % (handle, tweet_id)
    return posts


def main(argv=None):  # pragma: no cover - 手工验证
    import argparse
    import json

    parser = argparse.ArgumentParser(description="给一条推文找原帖链接（手工验证）")
    parser.add_argument("handle")
    parser.add_argument("text")
    args = parser.parse_args(argv)
    cfg = load_config("app.yaml") or {}
    articles = parse_articles(fetch_html(args.handle, cfg))
    print(json.dumps({"articles": len(articles),
                      "tweet_id": match_tweet(args.text, articles)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
