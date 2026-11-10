# -*- coding: utf-8 -*-
"""render.py —— 把课程条目渲染成 Telegram HTML 卡片（v2：每条 10–15 分钟）。

约定（与仓库其它模块一致）：
- 标题带【模块名】+「🏷 任务：<launchd Label>」，方便区分来源；
- 所有课程文本先 html.escape，避免 < > & 破坏消息；
- 链接只显示「延伸阅读」文字，不暴露长 URL；
- 单条消息控制在 message.max_chars 内。
"""
import datetime
import html

from common import esc

SEP = "━━━━━━━━━━━━"
SLOT_TITLES = {"13": "概念课 · 13:00", "19": "理论 / 实践 · 19:00", "21": "今日回顾与总结 · 21:00", "23": "睡前复盘课 · 23:00"}
TYPE_EMOJI = {"concept": "🔑", "theory": "📖", "practice": "🛠"}



def header(emoji, label, ctx):
    lines = [
        "%s<b>【%s】</b>%s · 第 %d 天" % (emoji, ctx["brand"], label, ctx["day_no"]),
        "🏷 任务：%s" % esc(ctx["job_label"]),
        "<i>阶段 %d · %s</i>" % (ctx["stage"], esc(ctx.get("stage_title") or "加密基础知识")),
        SEP,
    ]
    return "\n".join(lines)


def quiz_items(item):
    """统一 quiz 形态：兼容旧版 {q,a} 与新版 [{q,a}, ...]。"""
    raw = item.get("quiz")
    if isinstance(raw, dict):
        return [raw] if raw else []
    if isinstance(raw, list):
        return [q for q in raw if isinstance(q, dict)]
    return []


def _title(item):
    text = "%s <b>%s</b>" % (TYPE_EMOJI.get(item.get("type"), "🔑"), esc(item["title"]))
    if item.get("en"):
        text += " <i>(%s)</i>" % esc(item["en"])
    return text


def _sections(item):
    out = []
    for sec in item.get("sections") or []:
        head = esc(str(sec.get("h") or "").strip())
        body = esc(str(sec.get("p") or "").strip())
        out.append("▸ <b>%s</b>\n%s" % (head, body))
    return "\n\n".join(out)


def _quiz_block(item):
    quizzes = quiz_items(item)
    if not quizzes:
        return []
    lines = ["🤔 <b>自测</b>（%d 题 · 答案 23:00 复盘课揭晓）" % len(quizzes)]
    for idx, quiz in enumerate(quizzes, 1):
        lines.append("%d. %s" % (idx, esc(quiz.get("q") or "")))
    return ["\n".join(lines)]


def _sources(item):
    urls = [str(u) for u in (item.get("sources") or []) if str(u).startswith("http")]
    if not urls:
        return []
    return ['📖 <a href="%s">延伸阅读</a>' % html.escape(urls[0], quote=True)]


def _time_plan(item):
    """按 minutes 动态拆分 10–15 分钟的学习路径，保证四段加起来等于总时长。"""
    minutes = int(item.get("minutes") or 12)
    kind = item.get("type")
    if kind == "practice":
        plan = "读目标 2′ · 动手 %d′ · 验收 2′ · 记录 1′" % max(5, minutes - 5)
    elif kind == "theory":
        plan = "读 %d′ · 拆例子 3′ · 自测 2′ · 笔记 1′" % max(6, minutes - 6)
    else:
        plan = "精读 %d′ · 例子 2′ · 自测 2′ · 记 1 句 1′" % max(5, minutes - 5)
    return "<i>⏱ 约 %d 分钟：%s</i>" % (minutes, plan)


def render_concept(item, ctx):
    lines = [header("🔑", SLOT_TITLES["13"], ctx), "", _title(item), "", _sections(item)]
    quiz = _quiz_block(item)
    if quiz:
        lines.extend(["", quiz[0]])
    lines.extend(_sources(item))
    lines.extend(["", _time_plan(item)])
    return "\n".join(lines)


def render_lesson(item, ctx):
    emoji = TYPE_EMOJI.get(item.get("type"), "📖")
    lines = [header(emoji, SLOT_TITLES["19"], ctx), "", _title(item), "", _sections(item)]
    quiz = _quiz_block(item)
    if quiz:
        lines.extend(["", quiz[0]])
    lines.extend(_sources(item))
    lines.extend(["", _time_plan(item)])
    return "\n".join(lines)


def _gist(item, limit=42):
    sections = item.get("sections") or []
    text = str((sections[0] or {}).get("p") or "") if sections else ""
    text = text.strip().replace("\n", " ")
    return text[:limit] + ("…" if len(text) > limit else "")


def _retrieval(item, max_questions=2):
    """一条内容的检索题（问题 + 折叠答案）。"""
    out = []
    for quiz in quiz_items(item)[:max_questions]:
        out.append("%s\n<tg-spoiler>答案：%s</tg-spoiler>"
                   % (esc(quiz.get("q") or ""), esc(quiz.get("a") or "—")))
    return out


def _first_sentence(text, limit=52):
    text = str(text or "").strip().replace("\n", " ")
    for sep in ("。", "；", ";", "！", "？"):
        if sep in text:
            text = text.split(sep, 1)[0]
            break
    return text[:limit] + ("…" if len(text) > limit else "")


def _takeaways(item, limit=2):
    """从 sections 里提取 2 条关键要点（跳过“一句话/为什么重要”，避免与 gist 重复）。"""
    out = []
    for sec in item.get("sections") or []:
        head = str(sec.get("h") or "").strip()
        if head in ("一句话", "为什么重要"):
            continue
        out.append("<b>%s</b>：%s" % (esc(head), esc(_first_sentence(sec.get("p")))))
        if len(out) >= limit:
            break
    return out


def render_recap(ctx, today, stats, preview_items):
    """21:00 今日回顾与总结：把当天两节课串起来，给要点、进度、明日预告。"""
    lines = [header("🌆", SLOT_TITLES["21"], ctx), ""]

    lines.append("📚 <b>今日课程</b>")
    if today:
        for idx, item in enumerate(today, 1):
            lines.append("%d. <b>%s</b> —— %s" % (idx, esc(item["title"]), esc(_gist(item))))
    else:
        lines.append("<i>今天的课程还没发出（可能错过了白天两班）。</i>")

    if today:
        lines.extend(["", "🧾 <b>关键要点</b>"])
        for item in today:
            for takeaway in _takeaways(item):
                lines.append("· %s" % takeaway)

    lines.extend(["", "📊 <b>今日进度</b>"])
    lines.append("· 第 %d 天 · 已学 %d/%d 条 · 待复习 %d 条"
                 % (stats["day_no"], stats["learned"], stats["total"], stats["due"]))
    lines.append("· 连续学习 %d 天" % stats["streak"])

    lines.extend(["", "🧠 <b>一句话总结</b>"])
    if len(today) >= 2:
        lines.append("用一句话把「%s」和「%s」连起来，写进 learning/notes/ 的本周笔记。"
                     % (esc(today[0]["title"]), esc(today[1]["title"])))
    elif today:
        lines.append("用一句话写下「%s」解决了什么问题，写进 learning/notes/。" % esc(today[0]["title"]))
    else:
        lines.append("用一句话写下今天记住的一个概念。")

    nxt = [it for it in (preview_items or {}).values() if it]
    if nxt:
        lines.extend(["", "🌅 <b>明日预告</b>"])
        for item in nxt:
            label = "概念" if item.get("type") == "concept" else "课程"
            lines.append("· %s：%s" % (label, esc(item["title"])))

    lines.extend(["", "<i>⏱ 约 8 分钟：回顾 4′ · 总结 3′ · 预告 1′</i>"])
    return "\n".join(lines)


WEEKDAY_CN = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def _weekday_cn(day_iso):
    return WEEKDAY_CN[datetime.date.fromisoformat(day_iso).isoweekday() - 1]


def _pick_spread(items, count):
    """从列表里均匀取 count 条（用于周报要点/自测，不随机，保证可复现）。"""
    if not items or count <= 0:
        return []
    step = max(1, len(items) // count)
    picked = items[::step][:count]
    # 不足时补尾部
    for item in items:
        if len(picked) >= count:
            break
        if item not in picked:
            picked.append(item)
    return picked


def render_weekly(ctx, week_log, stats, active_days, stages, preview_items):
    """周日 21:00：本周回顾与总结（10 分钟）。"""
    lines = [header("🗓️", "本周回顾与总结 · 周日 21:00", ctx), ""]

    rows = [row for row in week_log if row["items"]]
    total_items = sum(len(row["items"]) for row in rows)
    lines.append("📚 <b>本周课程</b>（%d/7 天 · %d 节）" % (active_days, total_items))
    if rows:
        for row in rows:
            titles = " / ".join(esc(it["title"]) for it in row["items"])
            lines.append("· %s：%s" % (_weekday_cn(row["date"]), titles))
    else:
        lines.append("<i>本周还没有学习记录。从下周一开始：每天 3 节 + 21:00 回顾总结。</i>")

    flat, seen = [], set()
    for row in rows:
        for item in row["items"]:
            if item["id"] not in seen:
                seen.add(item["id"])
                flat.append(item)
    picks = _pick_spread(flat, 3)
    if picks:
        lines.extend(["", "🧾 <b>本周关键要点</b>"])
        for item in picks:
            takeaway = _takeaways(item, 1)
            if takeaway:
                lines.append("· <b>%s</b>：%s" % (esc(item["title"]), takeaway[0]))
            else:
                lines.append("· <b>%s</b>：%s" % (esc(item["title"]), esc(_gist(item))))

    lines.extend(["", "📊 <b>本周数据</b>"])
    lines.append("· 学习 %d/7 天 · 连续 %d 天 · 已学 %d/%d 条 · 待复习 %d 条"
                 % (active_days, stats["streak"], stats["learned"], stats["total"], stats["due"]))
    parts = ["阶段 %d %d/%d" % (stage, data["learned"], data["total"]) for stage, data in stages.items()]
    if parts:
        lines.append("· " + " · ".join(parts))

    if picks:
        lines.extend(["", "🎯 <b>本周自测</b>（3 题 · 点开看答案）"])
        for idx, item in enumerate(picks, 1):
            quiz = (quiz_items(item) or [{}])[0]
            lines.append("Q%d：%s\n<tg-spoiler>答案：%s</tg-spoiler>"
                         % (idx, esc(quiz.get("q") or item["title"]), esc(quiz.get("a") or "—")))

    nxt = [it for it in (preview_items or {}).values() if it]
    if nxt:
        lines.extend(["", "🌅 <b>下周预告</b>"])
        for item in nxt:
            label = "概念" if item.get("type") == "concept" else "课程"
            lines.append("· %s：%s" % (label, esc(item["title"])))

    lines.extend(["", "<i>⏱ 约 10 分钟：回顾 4′ · 自测 3′ · 计划 2′ · 预告 1′</i>"])
    return "\n".join(lines)


def render_review(ctx, today, due, preview_items):
    lines = [header("🌙", SLOT_TITLES["23"], ctx), ""]

    lines.append("📌 <b>今日复盘</b>（约 5 分钟）")
    if today:
        for item in today:
            lines.append("▸ <b>%s</b> —— %s" % (esc(item["title"]), esc(_gist(item))))
            for idx, block in enumerate(_retrieval(item), 1):
                lines.append("%d. %s" % (idx, block))
    else:
        lines.append("<i>今天没有新内容（可能错过了白天两班）。</i>")

    if due:
        lines.extend(["", "🔁 <b>间隔复习</b>（约 4 分钟）"])
        for item in due:
            blocks = _retrieval(item, 1)
            lines.append("▸ <b>%s</b>" % esc(item["title"]))
            for block in blocks:
                lines.append("· %s" % block)

    lines.extend(["", "🧠 <b>综合思考</b>（约 2 分钟）"])
    if len(today) >= 2:
        first, second = today[0], today[1]
        lines.append("用 3 句话回答：「%s」和「%s」之间是什么关系？一个解决什么问题，"
                     "另一个依赖它什么？" % (esc(first["title"]), esc(second["title"])))
    elif today:
        lines.append("用 3 句话复述「%s」：它解决什么问题、代价是什么、什么情况下不适用？"
                     % esc(today[0]["title"]))
    else:
        lines.append("用 3 句话写下你今天记住的一个概念，以及它不适用的一种场景。")
    if due:
        lines.append("<i>写完这 3 句，今天才算真的学过。</i>")
    else:
        lines.append("<i>今天没有到期复习：把这道题写满 5 句，或多花 3 分钟回看今天的课程。</i>")

    nxt = [it for it in (preview_items or {}).values() if it]
    if nxt:
        lines.extend(["", "🌅 <b>明日预告</b>"])
        for item in nxt:
            label = "概念" if item.get("type") == "concept" else "课程"
            lines.append("· %s：%s" % (label, esc(item["title"])))

    if due:
        lines.extend(["", "<i>⏱ 约 12 分钟：复盘 5′ · 间隔 4′ · 思考 2′ · 预告 1′</i>"])
    else:
        lines.extend(["", "<i>⏱ 约 10 分钟：复盘 5′ · 深度思考 4′ · 预告 1′</i>"])
    return "\n".join(lines)


def truncate(text, max_chars=3800):
    """按 Telegram 上限截断；尽量不切断 HTML 标签。"""
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    if cut.rfind("<") > cut.rfind(">"):
        cut = cut[: cut.rfind("<")]
    return cut.rstrip() + "\n…"
