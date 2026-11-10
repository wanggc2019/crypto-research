#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fetch.py —— 抓取 KOL 的 X 主页（r.jina.ai 代理），解析推文、去重、落盘。

为什么不用官方 X API：2026 年起官方 API 纯按量付费（$0.005/条读），30 个账号的日常监控
成本明显高于 r.jina.ai 匿名代理（20 RPM、免费、不消耗 token）。详见 docs/crypto-x-kol/design.md。

用法：
    python3 scripts/fetch.py                    # 抓全部 enabled 账号（首次只建基线，不产出新帖）
    python3 scripts/fetch.py --only saylor,woonomic
    python3 scripts/fetch.py --limit 5
    python3 scripts/fetch.py --dry-run          # 只打印，不写 raw/state
    python3 scripts/fetch.py --fixtures DIR     # 离线回放（DIR/x.com/<handle>.md 或 DIR/<handle>.md）
    python3 scripts/fetch.py --data-dir DIR     # 数据目录覆盖（测试用）

输出：
    data/raw/<date>/posts.jsonl     # 新帖（追加写，可回放）
    data/state/kol-<handle>.json    # 已见指纹 / 基线标记 / 最近检查时间 / 最近错误
    data/logs/fetch-<date>.json     # 本轮统计
"""
import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (  # noqa: E402
    ensure_dirs,
    load_config,
    load_env,
    log_line,
    norm_title,
    safe_json_dump,
    safe_json_load,
    sha256,
    today_str,
    ts_iso,
)
from crypto_core import http as _http  # noqa: E402

MAX_SEEN = 2000
LOGIN_MARKERS = ("Log in or sign up for X", "Sign up for X")
MISSING_MARKERS = ("This account doesn’t exist", "This account doesn't exist", "Account not found")
# 网页/代理层面的限制（不是账号或解析问题）：等下一轮即可，不当失败、不告警。
WEB_RESTRICTION_MARKERS = (
    "命中 X 登录墙",
    "AbuseAlleviationError",
    "Anonymous access to domain",
    "blocked until",
    "HTTP Error 403",
    "HTTP Error 429",
    "Too Many Requests",
)
FOOTER_MARKERS = (
    "## Log in or sign up for X",
    "## What’s happening",
    "## What's happening",
    "## New to X?",
    "© 20",
)
POSTS_HEADING_RE = re.compile(r"(?m)^## .+? posts\s*$")
# 主页某些渲染会带 "Wu Blockchain @WuBlockchain 1h " 前缀（相对时间，每次抓取都会变），
# 不剥掉会导致同一推文指纹变化 → 重复告警。
AGE_PREFIX_RE = re.compile(
    r"^(?:[^\n]{0,80}?\s+)?@[A-Za-z0-9_]{1,15}\s+"
    r"(?:\d+\s*(?:s|m|h|d|w)|now|just now|刚刚|片刻前)\s+",
    re.I)
# 主页渲染尾部会带 "Show more 23 24 149 20K"（点赞/回复/转发/浏览计数，每次抓取都会变），
# 必须剥掉，否则同一推文会被当成新推文重复告警。
UI_TAIL_RE = re.compile(r"\s*(?:Show more|Show replies|Show \d+ repl(?:y|ies))\b.*$", re.I)
POST_SEP_RE = re.compile(r"(?m)^\* {1,3}")
AVATAR_RE = re.compile(
    r"\[!\[Image \d+: [^\]]*\]\((https://pbs\.twimg\.com/profile_images/[^)]+)\)\]"
    r"\((https://x\.com/([A-Za-z0-9_]+))\)"
)
IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
VIDEO_RE = re.compile(r"\[Video \d+\]\([^)]*\)")
LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")


class ParseError(Exception):
    """页面结构不符合预期（登录墙 / 改版 / 空页）。"""


# ---------------------------------------------------------------- 解析（纯函数，离线可测）
def posts_section(page):
    """取「## <名字>'s posts」之后、页脚之前的内容；找不到抛 ParseError。"""
    m = POSTS_HEADING_RE.search(page or "")
    if not m:
        raise ParseError("找不到 posts 区（可能是登录墙或页面改版）")
    body = page[m.end():]
    cuts = [body.find(marker) for marker in FOOTER_MARKERS if body.find(marker) >= 0]
    if cuts:
        body = body[:min(cuts)]
    return body


def strip_markdown(fragment):
    """去掉图片/视频/头像/链接标记，只保留可读文本。"""
    s = fragment or ""
    s = VIDEO_RE.sub("", s)
    s = IMAGE_RE.sub("", s)
    s = AVATAR_RE.sub("", s)
    s = LINK_RE.sub(lambda m: m.group(1), s)  # 保留链接可见文字（如 hashtag/域名）
    s = re.sub(r"(?m)^\s*Quote\s*$", "", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def strip_profile_prefix(text):
    """去掉 "Name @handle 1h " 这类相对时间前缀（时间会变，不能进指纹）。"""
    t = (text or "").strip()
    m = AGE_PREFIX_RE.match(t)
    return t[m.end():].strip() if m else t


def is_web_restriction(err):
    """错误是否属于「网页/代理限制」：等下一轮重试即可，不触发告警。"""
    text = str(err or "")
    return any(marker.lower() in text.lower() for marker in WEB_RESTRICTION_MARKERS)


def strip_ui_tail(text):
    """去掉 X 页面的 Show more/Show replies 及后面的互动计数。"""
    return UI_TAIL_RE.sub("", (text or "")).strip()


def parse_page(page, handle):
    """把 r.jina.ai 抓到的 X 主页 Markdown 解析成推文列表。

    结构（实测）：
        *   Pinned
        *   [![Image ..](头像)](https://x.com/<handle>) 正文 [图片]
        *   [![Image ..](头像)](https://x.com/<handle>) 正文 [![Image ..](引用作者头像)](...)
            引用正文

    返回 [{handle, text, quote, has_media, fingerprint}]；置顶/转推/纯图帖丢弃。
    """
    text = page or ""
    section = posts_section(text)
    out = []
    for chunk in POST_SEP_RE.split(section):
        if not chunk.strip():
            continue
        head = chunk.strip()
        if re.match(r"(?i)^(pinned|reposted)\b", head):
            continue  # 置顶帖（往往是旧帖）与转推都不算「新发言」
        avatars = list(AVATAR_RE.finditer(chunk))
        if avatars and avatars[0].group(3).lower() != handle.lower():
            continue  # 页面里混入的他人内容（转推）
        main_end = avatars[1].start() if len(avatars) > 1 else len(chunk)
        main_raw = chunk[avatars[0].end():main_end] if avatars else chunk
        quote_raw = chunk[avatars[1].start():] if len(avatars) > 1 else ""
        body = strip_ui_tail(strip_profile_prefix(strip_markdown(main_raw)))
        quote = strip_ui_tail(strip_profile_prefix(strip_markdown(quote_raw)))
        if not body:
            continue  # 纯图/纯视频帖没有可读文本
        fingerprint = sha256("%s|%s|%s" % (
            handle.lower(), norm_title(body), norm_title(quote)))[:24]
        text_key = sha256("%s|%s" % (handle.lower(), norm_title(body)))[:24]
        # 正文前 120 字符的稳定头：对长文/尾部计数差异更鲁棒
        head_key = sha256("%s|%s" % (handle.lower(), norm_title(body, 120)))[:24]
        out.append({
            "handle": handle,
            "text": body,
            "quote": quote,
            "has_media": bool(IMAGE_RE.search(chunk) or VIDEO_RE.search(chunk)),
            "fingerprint": fingerprint,
            "text_key": text_key,
            "head_key": head_key,
        })
    if not out:
        if any(m in text for m in LOGIN_MARKERS):
            raise ParseError("命中 X 登录墙（匿名访问被拦截）")
        if any(m in text for m in MISSING_MARKERS):
            raise ParseError("账号不存在（handle 可能写错）")
    return out


# ---------------------------------------------------------------- 抓取与状态
def load_accounts(only=None):
    cfg = load_config("kol.yaml") or {}
    defaults = cfg.get("defaults") or {}
    accounts = []
    for item in cfg.get("accounts") or []:
        handle = str(item.get("handle") or "").strip().lstrip("@")
        if not handle:
            continue
        rec = dict(defaults)
        rec.update(item)
        rec["handle"] = handle
        rec.setdefault("enabled", True)
        rec.setdefault("tier", "T2")
        if only:
            # 显式 --only 时连 disabled 账号也跑（调试/离线回放用）
            if handle.lower() not in only and str(rec.get("id", "")).lower() not in only:
                continue
        elif not rec.get("enabled"):
            continue
        accounts.append(rec)
    return accounts


def request_headers(cfg, env):
    """r.jina.ai 请求头：默认空（匿名免费）；可选带 JINA_API_KEY。"""
    fetch = (cfg.get("fetch") or {})
    headers = dict(fetch.get("request_headers") or {})
    if fetch.get("use_api_key"):
        key = (env or {}).get("JINA_API_KEY", "").strip()
        if key:
            headers["Authorization"] = "Bearer %s" % key
        else:
            print("  [warn] app.yaml 开了 use_api_key 但 .env 里没有 JINA_API_KEY，回退匿名模式")
    return headers


def _fixture_path(fixtures, handle):
    for rel in ("x.com/%s.md" % handle, "%s.md" % handle, "x.com/%s.txt" % handle, "%s.txt" % handle):
        path = os.path.join(fixtures, rel)
        if os.path.isfile(path):
            return path
    return None


def load_page(handle, cfg, headers, fixtures=None):
    """取主页 Markdown；fixtures 优先（离线回放，不联网）。"""
    if fixtures:
        path = _fixture_path(fixtures, handle)
        if not path:
            raise ParseError("fixtures 里没有 %s 的页面" % handle)
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    fetch = cfg.get("fetch") or {}
    url = "%shttps://x.com/%s" % (fetch.get("jina_prefix") or "https://r.jina.ai/", handle)
    return _http.get_text(
        url,
        timeout=float(fetch.get("timeout_seconds") or 30),
        headers=headers or None,
        retries=int(fetch.get("retries") or 0),
        backoff=float(fetch.get("backoff_seconds") or 2),
    )


def _mark_seen(state, posts):
    seen = state.setdefault("seen", {})
    seen_texts = state.setdefault("seen_texts", {})
    seen_heads = state.setdefault("seen_heads", {})
    stamp = ts_iso()
    for post in posts:
        seen[post["fingerprint"]] = stamp
        if post.get("text_key"):
            seen_texts[post["text_key"]] = stamp
        if post.get("head_key"):
            seen_heads[post["head_key"]] = stamp
    if len(seen) > MAX_SEEN:
        keep = sorted(seen.items(), key=lambda kv: kv[1])[-MAX_SEEN // 2:]
        state["seen"] = dict(keep)
    if len(seen_texts) > MAX_SEEN:
        keep = sorted(seen_texts.items(), key=lambda kv: kv[1])[-MAX_SEEN // 2:]
        state["seen_texts"] = dict(keep)
    if len(seen_heads) > MAX_SEEN:
        keep = sorted(seen_heads.items(), key=lambda kv: kv[1])[-MAX_SEEN // 2:]
        state["seen_heads"] = dict(keep)


def diff_posts(state, parsed):
    """返回 (新帖, 是否首轮基线)。首轮只建基线，避免把主页历史帖当新帖刷屏。

    双保险：指纹（正文+引用）或稳定文本 key 任一命中都视为旧帖；
    text_key 专门兜住「相对时间前缀变化」这类指纹抖动。
    """
    if not state.get("initialized"):
        return [], True
    seen = state.get("seen") or {}
    seen_texts = state.get("seen_texts") or {}
    seen_heads = state.get("seen_heads") or {}
    fresh = [p for p in parsed
             if p["fingerprint"] not in seen
             and p.get("text_key") not in seen_texts
             and p.get("head_key") not in seen_heads]
    return fresh, False


def build_item(post, account, date):
    """归一化落盘记录（cross-day 可回放）。"""
    first_seen = ts_iso()
    item_id = sha256("%s|%s" % (account["handle"].lower(), post["fingerprint"]))[:24]
    return {
        "id": item_id,
        "kind": "kol_post",
        "date": date,
        "handle": account["handle"],
        "name": account.get("name") or account["handle"],
        "role": account.get("role") or "",
        "tier": account.get("tier") or "T2",
        "tags": account.get("tags") or [],
        "text": post["text"],
        "quote": post["quote"],
        "has_media": post["has_media"],
        "fingerprint": post["fingerprint"],
        "text_key": post.get("text_key") or "",
        "head_key": post.get("head_key") or "",
        "url": "https://x.com/%s" % account["handle"],
        "first_seen": first_seen,
        "fetched_at": first_seen,
    }


# ---------------------------------------------------------------- 主流程
def run(args):
    cfg = load_config("app.yaml") or {}
    paths = ensure_dirs(args.data_dir)
    date = args.date or today_str()
    accounts = load_accounts(only=args.only)
    if args.limit:
        accounts = accounts[:args.limit]
    if not accounts:
        print("没有 enabled 的 KOL 账号（检查 config/kol.yaml）")
        return 0

    headers = request_headers(cfg, load_env())
    fetch_cfg = cfg.get("fetch") or {}
    interval = 0.0 if args.fixtures else float(fetch_cfg.get("min_interval_seconds") or 0)
    page_retries = 0 if args.fixtures else int(fetch_cfg.get("page_retries") or 0)
    retry_backoff = float(fetch_cfg.get("page_retry_backoff_seconds") or 2)
    last_request = 0.0
    out_path = os.path.join(paths["raw"], date, "posts.jsonl")
    stats = {
        "date": date, "started_at": ts_iso(),
        "accounts": [], "errors": [], "skipped": [], "total_new": 0, "baseline": 0,
    }
    total_new = 0

    previous_requests = False
    for account in accounts:
        handle = account["handle"]
        state_path = os.path.join(paths["state"], "kol-%s.json" % handle.lower())
        state = safe_json_load(state_path, {})
        started = time.time()
        fresh, baseline, parsed = [], False, []
        ok, err = False, ""
        for attempt in range(1 + page_retries):
            try:
                if interval > 0:
                    wait = interval - (time.time() - last_request)
                    if previous_requests and wait > 0:
                        time.sleep(wait)
                last_request = time.time()
                previous_requests = True
                page = load_page(handle, cfg, headers, fixtures=args.fixtures)
                parsed = parse_page(page, handle)
                fresh, baseline = diff_posts(state, parsed)
                ok, err = True, ""
                break
            except ParseError as exc:
                ok, err = False, str(exc)
            except Exception as exc:  # noqa: BLE001 - 网络/超时统一记录，继续下一个账号
                ok, err = False, "%s: %s" % (type(exc).__name__, exc)
            if attempt < page_retries:
                time.sleep(retry_backoff)

        if ok:
            if not args.dry_run:
                _mark_seen(state, parsed)
            state.update({"initialized": True, "last_checked": ts_iso(),
                          "last_ok": ts_iso(), "last_error": ""})
            if fresh and not args.dry_run:
                records = [build_item(p, account, date) for p in fresh]
                os.makedirs(os.path.dirname(out_path), exist_ok=True)
                with open(out_path, "a", encoding="utf-8") as f:
                    for rec in records:
                        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                total_new += len(fresh)
            if baseline:
                stats["baseline"] += 1
            if args.dry_run:
                fresh = []  # dry-run 不落盘，也不宣称新增
        else:
            state.update({"last_checked": ts_iso(), "last_error": err})
            if is_web_restriction(err):
                # 登录墙/403/429/代理临时封禁：等下一轮，不记失败、不告警
                state["last_skipped"] = err
                stats["skipped"].append({"handle": handle, "reason": err})
            else:
                stats["errors"].append({"handle": handle, "error": err})

        if not args.dry_run:
            safe_json_dump(state_path, state)

        elapsed = round(time.time() - started, 2)
        skipped = (not ok) and is_web_restriction(err)
        if ok:
            print("  @%-18s OK   new=%-2d visible=%-2d %5.2fs%s" % (
                handle, len(fresh), len(parsed), elapsed,
                "（基线）" if baseline and not args.dry_run else ""))
        elif skipped:
            print("  @%-18s SKIP %5.2fs  %s" % (handle, elapsed, err[:80]))
        else:
            print("  @%-18s ERR  %5.2fs  %s" % (handle, elapsed, err[:80]))
        stats["accounts"].append({
            "handle": handle, "ok": ok, "skipped": skipped, "new": len(fresh),
            "visible": len(parsed), "baseline": baseline,
            "seconds": elapsed, "error": err,
        })

    ok_n = sum(1 for a in stats["accounts"] if a["ok"])
    skip_n = len(stats["skipped"])
    stats.update({"finished_at": ts_iso(), "total_new": total_new,
                  "ok": ok_n, "fail": len(stats["errors"]), "skip": skip_n})
    if not args.dry_run:
        safe_json_dump(os.path.join(paths["logs"], "fetch-%s.json" % date), stats)
        log_line("fetch %s：账号 %d，成功 %d，失败 %d，新增 %d" % (
            date, len(accounts), ok_n, len(stats["errors"]), total_new), paths)

    print("\n采集完成：%d 个源，成功 %d，失败 %d，跳过 %d（登录墙/网页限制），新增 %d 条%s" % (
        len(accounts), ok_n, len(stats["errors"]), skip_n, total_new,
        "（首轮基线，不推送历史帖）" if stats["baseline"] else ""))
    if ok_n == 0 and not stats["errors"] and skip_n:
        print("全部源被网页限制（登录墙/HTTP 403/429/代理封禁），本轮跳过，等下一轮；不告警。")
    if stats["errors"]:
        print("失败明细：")
        for err in stats["errors"]:
            print("  - @%s: %s" % (err["handle"], err["error"][:120]))
    if args.json:
        print(json.dumps({"ok": ok_n, "fail": len(stats["errors"]), "skip": skip_n,
                          "new": total_new, "baseline": stats["baseline"]}, ensure_ascii=False))
    if stats["errors"]:
        return 3 if ok_n == 0 else 1
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="抓取 KOL 的 X 主页并解析新推文（r.jina.ai）")
    parser.add_argument("--only", help="只跑指定 handle/id，逗号分隔")
    parser.add_argument("--limit", type=int, help="只跑前 N 个账号（调试）")
    parser.add_argument("--dry-run", action="store_true", help="只打印，不写 raw/state")
    parser.add_argument("--date", help="落盘日期（默认今天；回放用）")
    parser.add_argument("--data-dir", help="数据目录覆盖（测试/回放）")
    parser.add_argument("--fixtures", help="离线回放目录（页面 Markdown，禁止联网）")
    parser.add_argument("--json", action="store_true", help="末尾输出机器可读摘要")
    args = parser.parse_args(argv)
    args.only = {s.strip().lower() for s in args.only.split(",") if s.strip()} if args.only else None
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
