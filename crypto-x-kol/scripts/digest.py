#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""digest.py —— KOL 推文评分、日报与「只推高价值」的 Telegram 告警。

用法：
    python3 scripts/digest.py                 # 评分 + 写日报，不推送（安全默认）
    python3 scripts/digest.py --notify        # 评分 + 日报 + 只推 P0/P1（去重/静默/每日上限）
    python3 scripts/digest.py --test          # 发一条通道测试消息
    python3 scripts/digest.py --dry-run       # 只打印，不写文件/不推送
    python3 scripts/digest.py --date 2026-11-08 --data-dir DIR --json   # 回放/测试

策略（见 scripts/score.py 与 config/rules.yaml）：
- 所有新帖都进日报（data/reports/<date>.md），带 score/level/理由；
- Telegram 只推 P0/P1：P0=安全/监管类或 ≥2 位 KOL 同题的高分推文；P1=值得看；
- 其余 P2/MUTE 只进日报；每天最多推送 alert.caps.per_day 条；静默时段只放行 P0（可配）。
"""
import argparse
import datetime
import html
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analyze as analyzer  # noqa: E402
import xlink  # noqa: E402

import score as scoring  # noqa: E402
from common import (  # noqa: E402
    ensure_dirs,
    job_name,
    load_config,
    load_env,
    now,
    read_jsonl,
    safe_json_dump,
    safe_json_load,
    today_str,
    ts_iso,
)
from crypto_core.notify import check_credentials, send_message  # noqa: E402
from crypto_core.timeutil import in_quiet_hours  # noqa: E402

MAX_LEN = 4096
ALERT_LEVELS = ("P0", "P1")


# ---------------------------------------------------------------- 数据装载
def load_history(paths, date, days):
    """读最近 N 天的 raw（含当天）作为「同题/新颖度」上下文；坏文件跳过。"""
    try:
        today = datetime.date.fromisoformat(str(date))
    except ValueError:
        today = now().date()
    rows = []
    for offset in range(max(1, int(days))):
        day = (today - datetime.timedelta(days=offset)).isoformat()
        rows.extend(read_jsonl(os.path.join(paths["raw"], day, "posts.jsonl")))
    return rows


def account_meta():
    """handle → {name, role}；用于给旧 raw 条目补齐作者信息。"""
    cfg = load_config("kol.yaml") or {}
    out = {}
    for item in cfg.get("accounts") or []:
        handle = str(item.get("handle") or "").strip().lstrip("@").lower()
        if handle:
            out[handle] = {
                "name": str(item.get("name") or handle),
                "role": str(item.get("role") or ""),
            }
    return out


def apply_meta(posts, meta):
    for post in posts:
        info = meta.get((post.get("handle") or "").lower()) or {}
        if info.get("name") and not post.get("name"):
            post["name"] = info["name"]
        if info.get("role") and not post.get("role"):
            post["role"] = info["role"]
    return posts


def author_line(post):
    """'Michael Saylor · Strategy 执行主席'；缺字段则尽量拼。"""
    parts = [str(post.get("name") or "").strip(), str(post.get("role") or "").strip()]
    return " · ".join([p for p in parts if p])


def enrich(posts, rules, history=None, now_dt=None):
    """兼容旧接口：批量评分（无 history 时也能算基础分）。"""
    return scoring.evaluate(posts, rules, history=history, now=now_dt)


# ---------------------------------------------------------------- 报告 / 消息（纯函数）
def _counts(posts):
    counts = {level: 0 for level in ("P0", "P1", "P2", "MUTE")}
    for post in posts:
        counts[post.get("level") or "MUTE"] = counts.get(post.get("level") or "MUTE", 0) + 1
    return counts


def fmt_clock(post):
    raw = str(post.get("first_seen") or "")
    return raw[11:16] if len(raw) >= 16 else "--:--"


def one_line(text, limit):
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: max(0, limit - 1)] + "…"


def _level_badge(post):
    return "%s · %d分" % (post.get("level") or "MUTE", int(post.get("score") or 0))


def _reason_text(post):
    reasons = post.get("reasons") or []
    return "、".join(reasons[:4]) if reasons else "—"


def build_report(posts, date, rules, generated_at, cache=None):
    cache = cache or {}
    counts = _counts(posts)
    alerts = [p for p in posts if p.get("level") in ALERT_LEVELS]
    lines = [
        "# KOL 推文日报 %s" % date,
        "",
        "> 生成时间：%s｜新帖 %d 条｜告警 %d 条（P0 %d / P1 %d）｜仅日报 %d 条" % (
            generated_at, len(posts), len(alerts), counts.get("P0", 0), counts.get("P1", 0),
            counts.get("P2", 0) + counts.get("MUTE", 0)),
        "> 外文推文已由 DeepSeek 翻成中文并给出一句话总结；原文保留在 `data/raw/<date>/posts.jsonl`。",
        "",
        "## 🔔 需关注（P0/P1，%d 条）" % len(alerts),
        "",
    ]
    if alerts:
        for post in alerts:
            text, summary, _, translated = analyzer.localize(post, cache)
            prefix = "[翻译] " if translated else ""
            author = author_line(post)
            lines.append("- **%s｜@%s%s** `%s` %s" % (
                _level_badge(post), post["handle"],
                ("（%s）" % author) if author else "",
                fmt_clock(post), prefix + one_line(text, 320)))
            if summary:
                lines.append("  - 一句话：%s" % one_line(summary, 200))
            lines.append("  - 理由：%s" % _reason_text(post))
            if post.get("topic_hits"):
                lines.append("  - 命中：%s" % "、".join(post["topic_hits"][:5]))
    else:
        lines.append("_今日无达到告警阈值的推文_")
    lines += ["", "## 📋 全部新帖（%d 条）" % len(posts), ""]
    groups = {}
    for post in posts:
        groups.setdefault(post["handle"], []).append(post)
    for handle in sorted(groups, key=lambda h: (groups[h][0].get("tier") != "T1", h.lower())):
        items = groups[handle]
        author = author_line(items[0])
        lines.append("### @%s（%s）" % (handle, author or items[0].get("name") or handle))
        for post in items:
            text, summary, _, translated = analyzer.localize(post, cache)
            prefix = "[翻译] " if translated else ""
            lines.append("- `%s` **%s** %s" % (
                fmt_clock(post), _level_badge(post), prefix + one_line(text, 500)))
            if summary:
                lines.append("  - 一句话：%s" % one_line(summary, 200))
            if post.get("reasons"):
                lines.append("  - %s" % _reason_text(post))
            lines.append("  - 主页：https://x.com/%s" % handle)
        lines.append("")
    lines += [
        "> 说明：X 匿名页面不提供发帖时间与点赞/转发数，时间统一是「首次发现时间」；"
        "评分只看作者层级/主题/资产/同题/新颖度，不含互动量。",
        "> 本报告由固定规则生成，不构成投资建议。",
        "",
    ]
    return "\n".join(lines)


def build_alert_message(alerts, rules, text_chars, max_per_message, report_path, cache=None):
    """只包含 P0/P1 的 Telegram HTML 文本（外文已翻成中文 + 一句话总结）。"""
    cache = cache or {}
    shown = alerts[:max_per_message]
    counts = _counts(alerts)
    lines = ["🔔【crypto-x-kol】高价值推文 %d 条（P0 %d · P1 %d）" % (
        len(alerts), counts.get("P0", 0), counts.get("P1", 0))]
    for post in shown:
        handle = post["handle"]
        icon = "🔴" if post.get("level") == "P0" else "🟡"
        lines.append("")
        author = author_line(post)
        suffix = ("（%s）" % html.escape(author, quote=False)) if author else ""
        url = post.get("tweet_url") or ("https://x.com/%s" % handle)
        lines.append("%s <a href=\"%s\">@%s</a>%s · %s · <b>%s</b>" % (
            icon, html.escape(url, quote=True), html.escape(handle), suffix,
            fmt_clock(post), html.escape(_level_badge(post), quote=False)))
        text, summary, _, translated = analyzer.localize(post, cache)
        prefix = "[翻译] " if translated else ""
        lines.append(html.escape(prefix + one_line(text, text_chars), quote=False))
        if summary:
            lines.append("🧠 %s" % html.escape(one_line(summary, 200), quote=False))
        details = _reason_text(post)
        if details and details != "—":
            lines.append("（%s）" % html.escape(details, quote=False))
        if post.get("tweet_url"):
            lines.append('🔗 <a href="%s">原帖</a>' % html.escape(post["tweet_url"], quote=True))
    rest = len(alerts) - len(shown)
    if rest > 0:
        lines.append("")
        lines.append("另有 %d 条高分推文见日报（每日上限 %s 条）。" % (rest, "见配置"))
    lines.append("")
    lines.append("完整日报：%s" % html.escape(report_path, quote=False))
    lines.append("🏷 任务：%s" % html.escape(job_name(), quote=False))
    text = "\n".join(lines)
    if len(text) > MAX_LEN:
        text = text[: MAX_LEN - 1] + "…"
    return text


# ---------------------------------------------------------------- 推送状态
def pushed_path(paths, date):
    return os.path.join(paths["state"], "pushed-%s.json" % date)


def load_pushed(paths, date):
    """返回 {post_id: {"decision": alert|digest, "ts": ...}}；兼容旧版字符串值。"""
    data = safe_json_load(pushed_path(paths, date), {}) or {}
    raw = data.get("ids") or {}
    out = {}
    for key, value in raw.items():
        if isinstance(value, dict):
            out[key] = value
        else:  # 旧格式：直接存时间戳，视为已处理
            out[key] = {"decision": "digest", "ts": str(value)}
    return out


def save_pushed(paths, date, decisions):
    safe_json_dump(pushed_path(paths, date), {"date": date, "ids": decisions})


# ---------------------------------------------------------------- 翻译/总结缓存
def analysis_cache_path(paths, date):
    return os.path.join(paths["state"], "analysis-%s.json" % date)


def load_analysis_cache(paths, date):
    data = safe_json_load(analysis_cache_path(paths, date), {}) or {}
    items = data.get("items")
    return items if isinstance(items, dict) else {}


def save_analysis_cache(paths, date, items, model=""):
    safe_json_dump(analysis_cache_path(paths, date),
                   {"date": date, "model": model, "items": items})


def pending_posts(posts, pushed):
    return [p for p in posts if p["id"] not in pushed]


def mark(pushed, posts, decision):
    stamp = ts_iso()
    for post in posts:
        pushed[post["id"]] = {
            "decision": decision,
            "ts": stamp,
            "score": post.get("score"),
            "level": post.get("level"),
        }


def mark_after_send(pushed, pending, chosen, quiet):
    """发送后更新状态：chosen → alert；静默期只把非告警标 digest，其余 P1 留待次日。

    返回静默期「留待次日」的告警条数（非静默恒为 0）。
    """
    chosen_ids = {p["id"] for p in chosen}
    mark(pushed, chosen, "alert")
    if quiet:
        held = [p for p in pending if p.get("level") in ALERT_LEVELS and p["id"] not in chosen_ids]
        non_alerts = [p for p in pending if p.get("level") not in ALERT_LEVELS]
        if non_alerts:
            mark(pushed, non_alerts, "digest")
        return len(held)
    rest = [p for p in pending if p["id"] not in chosen_ids]
    if rest:
        mark(pushed, rest, "digest")
    return 0


def sent_alerts_today(pushed):
    return sum(1 for item in pushed.values() if (item or {}).get("decision") == "alert")


def select_alerts(candidates, sent_today, caps):
    """按每日上限/单条上限挑出本轮要推的告警：P0 可绕过每日上限，但受单条上限约束。"""
    per_day = int(caps.get("per_day") or 8)
    max_msg = int(caps.get("max_per_message") or 5)
    chosen = []
    for post in candidates:
        if len(chosen) >= max_msg:
            break
        if post.get("level") == "P0" and caps.get("p0_bypass_cap", True):
            chosen.append(post)
        elif sent_today + len(chosen) < per_day:
            chosen.append(post)
    return chosen


# ---------------------------------------------------------------- 主流程
def run(args):
    cfg = load_config("app.yaml") or {}
    rules = load_config("rules.yaml") or {}
    digest_cfg = cfg.get("digest") or {}
    policy = scoring.policy(rules)
    caps = policy.get("caps") or {}
    paths = ensure_dirs(args.data_dir)
    date = args.date or today_str()
    now_dt = now()

    raw_posts = apply_meta(read_jsonl(os.path.join(paths["raw"], date, "posts.jsonl")), account_meta())
    history = load_history(paths, date, int(policy.get("novelty_days") or 7) + 1)
    posts = scoring.evaluate(raw_posts, rules, history=history, now=now_dt)
    counts = _counts(posts)

    if args.test:
        env = load_env()
        token, chat, problems = check_credentials(env)
        if problems:
            print("❌ Telegram 凭据不完整：%s" % "；".join(problems))
            return 2
        accounts = [a for a in (load_config("kol.yaml").get("accounts") or [])
                    if a.get("enabled", True)]
        analysis_on = bool(analyzer.analysis_config(cfg).get("enabled"))
        text = ("✅【crypto-x-kol】<b>通道测试</b>\n"
                "监控账号：%d 个（每 %s 秒抓一次）｜抓取：r.jina.ai（免 key）\n"
                "告警：价值分 P0≥%s / P1≥%s，每日最多 %s 条\n"
                "中文：DeepSeek %s｜原文见日报\n🏷 任务：%s" % (
                    len(accounts), (cfg.get("poll_seconds") or 300),
                    policy["thresholds"].get("P0"), policy["thresholds"].get("P1"),
                    caps.get("per_day"), "翻译+总结" if analysis_on else "未启用",
                    job_name()))
        if args.dry_run:
            print("[dry-run] 测试消息：\n" + text)
            return 0
        ok, err = send_message(token, chat, text)
        print("测试消息%s%s" % ("发送成功 ✅" if ok else "发送失败 ❌", (" | " + err) if err else ""))
        return 0 if ok else 2

    # 翻译 + 一句话总结（DeepSeek；dry-run / --no-analyze 跳过；无 key 自动降级）
    analysis_cache = load_analysis_cache(paths, date)
    analysis_cfg = analyzer.analysis_config(cfg)
    if posts and not args.dry_run and not args.no_analyze:
        todo = [p for p in posts if str(p["id"]) not in analysis_cache]
        if todo:
            before = len(analysis_cache)
            analysis_cache = analyzer.analyze(
                todo, analysis_cfg, env=load_env(), cache=analysis_cache, log=print)
            if len(analysis_cache) > before:
                save_analysis_cache(paths, date, analysis_cache,
                                    model=str(analysis_cfg.get("model") or ""))
                print("翻译/总结：新分析 %d 条（缓存共 %d 条）" % (
                    len(analysis_cache) - before, len(analysis_cache)))
    elif args.no_analyze:
        print("（--no-analyze：跳过翻译/总结，显示原文）")
    elif args.dry_run:
        print("（dry-run：跳过翻译/总结，不调用 DeepSeek）")

    report_path = os.path.join(paths["reports"], "%s.md" % date)
    markdown = build_report(posts, date, rules, ts_iso(), cache=analysis_cache) if posts else ""
    if markdown and not args.dry_run:
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(markdown)

    pushed = load_pushed(paths, date)
    pending = pending_posts(posts, pushed)
    alerts = [p for p in pending if p.get("level") in ALERT_LEVELS]
    summary = {
        "date": date, "posts": len(posts), "pending": len(pending),
        "alerts": len(alerts), "p0": counts.get("P0", 0), "p1": counts.get("P1", 0),
        "p2": counts.get("P2", 0), "mute": counts.get("MUTE", 0),
        "analyzed": len(analysis_cache), "report": report_path,
    }

    if not posts:
        print("今日暂无新帖（%s）" % date)
        if args.json:
            print(json.dumps(summary, ensure_ascii=False))
        return 0

    # 非 --notify：只写日报 + 打印「如果推送会发什么」
    if not args.notify:
        print("今日新帖 %d 条（P0 %d / P1 %d / P2 %d / MUTE %d），待告警 %d 条；报告：%s" % (
            len(posts), counts.get("P0", 0), counts.get("P1", 0),
            counts.get("P2", 0), counts.get("MUTE", 0), len(alerts), report_path))
        quiet = bool(digest_cfg.get("quiet_hold", True)) and in_quiet_hours(
            now_dt=now_dt, span=digest_cfg.get("quiet_hours") or "22:00-07:00")
        if alerts and args.dry_run:
            print("\n--- 本次会推送的告警预览%s ---" % ("（静默时段：仅 P0）" if quiet else ""))
            preview = alerts
            if quiet:
                preview = [p for p in preview if p.get("level") == "P0"] \
                    if policy.get("p0_pierce_quiet") else []
            if preview:
                print(build_alert_message(preview, rules,
                                          int(digest_cfg.get("text_chars") or 140),
                                          int(caps.get("max_per_message") or 5),
                                          report_path, cache=analysis_cache))
            else:
                print("（静默时段：本轮无 P0，P1 留待次日）")
        elif alerts:
            print("（未加 --notify，仅写日报；%d 条达到告警阈值）" % len(alerts))
        if args.json:
            print(json.dumps(summary, ensure_ascii=False))
        return 0

    # --notify：只处理「达到 P0/P1 且未推送」的告警；P2/MUTE 直接标记为已进日报
    if not alerts:
        mark(pushed, pending, "digest")
        if not args.dry_run:
            save_pushed(paths, date, pushed)
        if pending:
            print("本轮待处理 %d 条均未达到告警阈值（今日共 %d 条），已进日报；报告：%s" % (
                len(pending), len(posts), report_path))
        else:
            print("今日新帖已全部处理完毕（无待推送）；报告：%s" % report_path)
        if args.json:
            print(json.dumps(summary, ensure_ascii=False))
        return 0

    quiet = bool(digest_cfg.get("quiet_hold", True)) and in_quiet_hours(
        now_dt=now_dt, span=digest_cfg.get("quiet_hours") or "22:00-07:00")
    candidates = alerts
    if quiet:
        candidates = [p for p in alerts if p.get("level") == "P0"] if policy.get("p0_pierce_quiet") else []
        if not candidates:
            print("静默时段（%s），%d 条 P1 留待次日；报告：%s" % (
                digest_cfg.get("quiet_hours") or "22:00-07:00", len(alerts), report_path))
            if args.json:
                print(json.dumps(summary, ensure_ascii=False))
            return 0

    per_day = int(caps.get("per_day") or 8)
    max_msg = int(caps.get("max_per_message") or 5)
    sent_today = sent_alerts_today(pushed)
    chosen = select_alerts(candidates, sent_today, caps)
    if not chosen:
        print("今日告警已达上限（%d 条），%d 条高分推文只进日报；报告：%s" % (
            per_day, len(alerts), report_path))
        mark(pushed, pending, "digest")
        if not args.dry_run:
            save_pushed(paths, date, pushed)
        if args.json:
            print(json.dumps(summary, ensure_ascii=False))
        return 0

    # 给待推送告警补「原帖链接」（按需抓 HTML；失败回退主页）
    if not args.dry_run:
        xlink.attach(chosen, cfg, log=print)

    message = build_alert_message(chosen, rules,
                                  int(digest_cfg.get("text_chars") or 140),
                                  max_msg, report_path, cache=analysis_cache)
    if args.dry_run:
        print(message)
        return 0

    env = load_env()
    token, chat, problems = check_credentials(env)
    if problems:
        print("❌ Telegram 凭据不完整：%s" % "；".join(problems))
        return 2
    ok, err = send_message(token, chat, message)  # 保留 Telegram 链接预览（原帖卡片）
    if not ok:
        print("❌ 推送失败：%s" % err)
        return 1

    held = mark_after_send(pushed, pending, chosen, quiet)
    save_pushed(paths, date, pushed)
    if quiet:
        print("✅ 已推送 %d 条告警（P0 %d / P1 %d）；%d 条 P1 留待静默结束；报告：%s" % (
            len(chosen), sum(1 for p in chosen if p["level"] == "P0"),
            sum(1 for p in chosen if p["level"] == "P1"), held, report_path))
    else:
        print("✅ 已推送 %d 条告警（P0 %d / P1 %d）；其余 %d 条进日报；报告：%s" % (
            len(chosen), sum(1 for p in chosen if p["level"] == "P0"),
            sum(1 for p in chosen if p["level"] == "P1"),
            max(0, len(pending) - len(chosen)), report_path))
    if args.json:
        summary["sent"] = len({p["id"] for p in chosen})
        print(json.dumps(summary, ensure_ascii=False))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="KOL 推文评分 + 日报 + 高价值告警")
    parser.add_argument("--notify", action="store_true", help="只推送 P0/P1（默认只写日报）")
    parser.add_argument("--test", action="store_true", help="发一条通道测试消息")
    parser.add_argument("--dry-run", action="store_true", help="只打印，不写文件/不推送（不调用翻译 API）")
    parser.add_argument("--no-analyze", action="store_true", help="跳过 DeepSeek 翻译/总结（报告显示原文）")
    parser.add_argument("--date", help="处理日期（默认今天）")
    parser.add_argument("--data-dir", help="数据目录覆盖（测试/回放）")
    parser.add_argument("--json", action="store_true", help="机器可读摘要")
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
