#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""brief.py —— 把分类记录渲染成 Telegram 卡片 / Markdown 速报。

速报只陈述「文件事实」（机关/文号/日期/主题/原文要点），不做法律解读；
解读由后续 DSH 任务基于 data/docs 原文生成，并标注「AI 草稿」。
"""
import html
import re

from common import clean_text, load_config

MAX_LEN = 4096
_LEAD_WORDS = ("禁止", "不得", "一律", "应当", "要求", "停止", "取缔", "严厉打击", "依法")


def esc(s):
    return html.escape(str(s or ""), quote=False)


def pick_points(body, topics, limit=4):
    """从正文里挑「含主题词/规范性措辞」的句子作为要点。"""
    body = body or ""
    topics = topics or []
    parts = re.split(r"[。；\n]", body)
    scored, seen = [], set()
    for raw in parts:
        s = clean_text(raw)
        if len(s) < 12 or len(s) > 160 or s in seen:
            continue
        score = 0
        for t in topics:
            if t and t.lower() in s.lower():
                score += 2
        for w in _LEAD_WORDS:
            if w in s:
                score += 1
        if score <= 0:
            continue
        seen.add(s)
        scored.append((score, s))
    scored.sort(key=lambda x: -x[0])
    out = []
    for _, s in scored[:limit]:
        out.append(s if len(s) <= 120 else s[:117] + "…")
    return out


def _short_title(title, limit=120):
    title = title or ""
    return title if len(title) <= limit else title[: limit - 1] + "…"


def render_card(rec, ai_state="pending"):
    level = rec.get("level") or "P2"
    icon = "🚨" if level == "P0" else ("⚠️" if level == "P1" else "📄")
    lines = [
        "%s【crypto-policy】<b>%s · %s</b>" % (icon, esc(level), esc(rec.get("doc_type_cn") or "文件")),
        "<b>%s</b>" % esc(_short_title(rec.get("title") or "")),
        "",
        "<b>机关</b>：%s" % esc(rec.get("issuer") or "—"),
        "<b>地区</b>：%s" % esc(rec.get("jurisdiction") or "CN"),
        "<b>文号</b>：%s" % esc(rec.get("doc_no") or "—"),
        "<b>发布</b>：%s｜<b>施行</b>：%s" % (esc(rec.get("published_at") or "—"), esc(rec.get("effective_at") or "—")),
    ]
    topics = rec.get("topics") or []
    if topics:
        lines.append("<b>主题</b>：%s" % esc(" · ".join(topics[:6])))
    points = rec.get("points") or pick_points(rec.get("body") or "", topics)
    if points:
        lines.append("")
        lines.append("<b>原文要点</b>：")
        for i, p in enumerate(points[:5], 1):
            lines.append("%d. %s" % (i, esc(p)))
    lines.append("")
    if ai_state == "pending":
        lines.append("🧠 解读：待生成（AI 草稿将另行推送，非法律意见）")
    elif ai_state == "done":
        lines.append("🧠 解读：已生成（AI 草稿，非法律意见）")
    if rec.get("url"):
        lines.append('🔗 <a href="%s">查看原文</a>' % html.escape(rec["url"], quote=True))
    text = "\n".join(lines)
    if len(text) > MAX_LEN:
        text = text[: MAX_LEN - 1] + "…"
    return text


def render_digest(records, title="政策速报"):
    """P1 批量汇总：一条消息列多条。"""
    records = records or []
    lines = ["📬【crypto-policy】<b>%s（%d 条）</b>" % (esc(title), len(records)), ""]
    for i, rec in enumerate(records[:10], 1):
        line = "%d. [%s·%s] %s" % (
            i, esc(rec.get("level") or "P1"), esc(rec.get("jurisdiction") or "CN"), esc(rec.get("title") or ""))
        if rec.get("doc_no"):
            line += "（%s）" % esc(rec["doc_no"])
        lines.append(line)
        if rec.get("url"):
            lines.append("   🔗 %s" % html.escape(rec["url"], quote=True))
    lines.append("")
    lines.append("说明：机器筛选速报，未含法律解读；需要深读的会另行生成 AI 草稿。")
    text = "\n".join(lines)
    return text[: MAX_LEN - 1] + "…" if len(text) > MAX_LEN else text


def render_interpret_card(rec, draft):
    """解读草稿卡片：结论 + 要点 + 影响 + 免责声明。"""
    level = rec.get("level") or "P2"
    lines = [
        "🖋【crypto-policy】<b>%s · 解读草稿</b>" % esc(level),
        "<b>%s</b>" % esc(_short_title(rec.get("title") or "")),
        "",
        "<b>地区</b>：%s｜<b>文号</b>：%s｜<b>施行</b>：%s" % (
            esc(rec.get("jurisdiction") or "CN"), esc(rec.get("doc_no") or "—"), esc(rec.get("effective_at") or "—")),
        "",
        esc(draft.get("summary") or ""),
        "",
    ]
    points = draft.get("key_points") or []
    if points:
        lines.append("<b>要点</b>：")
        for i, p in enumerate(points[:5], 1):
            lines.append("%d. %s" % (i, esc(p)))
        lines.append("")
    changes = draft.get("changes") or []
    if changes:
        lines.append("<b>与旧规关系</b>：")
        for c in changes[:3]:
            lines.append("• %s" % esc(c))
        lines.append("")
    impact = draft.get("impact") or []
    if impact:
        lines.append("<b>影响</b>：")
        for x in impact[:4]:
            lines.append("• %s" % esc(x))
        lines.append("")
    lines.append("⚠️ %s草稿，非法律意见；以官方原文为准。" % esc(draft.get("engine_label") or "AI"))
    if rec.get("url"):
        lines.append('🔗 <a href="%s">查看原文</a>' % html.escape(rec["url"], quote=True))
    text = "\n".join(lines)
    return text[: MAX_LEN - 1] + "…" if len(text) > MAX_LEN else text


def sample_card():
    rec = {
        "level": "P0",
        "doc_type_cn": "规范性文件",
        "title": "（测试）关于进一步防范和处置虚拟货币等相关风险的通知",
        "issuer": "中国人民银行等八部门",
        "doc_no": "银发〔2026〕42号",
        "published_at": "2026-02-06",
        "effective_at": "2026-02-06",
        "topics": ["虚拟货币", "稳定币"],
        "body": "虚拟货币相关业务活动属于非法金融活动，一律严格禁止。境外单位和个人不得以任何形式非法向境内主体提供虚拟货币相关服务。",
        "url": "https://www.pbc.gov.cn/",
    }
    return render_card(rec)


def quiet_span():
    return (load_config("app.yaml").get("delivery") or {}).get("quiet_hours", "22:30-07:30")
