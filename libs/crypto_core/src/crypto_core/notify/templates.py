# -*- coding: utf-8 -*-
"""消息卡片模板：把告警渲染成 Telegram HTML 文本。

展示约定（见 docs/conventions.md §5）：
- 类别 / 硬条件 / 影响 / 动作参考 统一由 events 里的中文词表翻译
- 不展示 S/T/M/C/D 内部评分明细，只保留总分
- 链接用「查看原文」文字链接，避免一长串 URL
- 未确认（低可信源）事件显式标注，避免误导
"""
import html
from urllib.parse import urlparse

from .. import events as ev
from .. import timeutil
from ..text import cn_summary as _cn_summary
from .telegram import job_label

MAX_LEN = 4096


def esc(s):
    return html.escape(str(s or ""), quote=False)


def _abs_time(published_at):
    """ISO 时间 → "MM-DD HH:MM"；解析失败返回空串。"""
    if not published_at:
        return ""
    try:
        return timeutil.fmt_local(published_at, "%m-%d %H:%M")
    except (ValueError, TypeError):
        return ""


def _truncate(text):
    if len(text) <= MAX_LEN:
        return text
    cut = text[: MAX_LEN - 1]
    # 不切断 HTML 标签：回退到最后一个完整的 '>'
    if cut.rfind("<") > cut.rfind(">"):
        cut = cut[: cut.rfind("<")]
    return cut + "…"


def format_alert(a):
    """渲染 P0/P1 告警卡片；a 可以是 dict（既有格式）或 events.Alert。"""
    if not isinstance(a, dict):
        a = a.to_dict()
    level = a.get("level") or a.get("severity") or "P1"
    category = a.get("category")
    title = a.get("title") or ((a.get("titles") or [""])[0])
    icon = "🚨" if level == "P0" else "⚠️"

    module = a.get("module") or ""
    job = a.get("job") or (job_label(module) if module else "")
    if module:
        lines = ["%s【%s】<b>%s · %s</b>" % (icon, esc(module), level, ev.category_cn(category))]
    else:
        lines = ["%s <b>%s · %s</b>" % (icon, level, ev.category_cn(category))]
    lines.append("<b>%s</b>" % esc(title))
    cn = a.get("cn_summary") or _cn_summary(title)
    if cn:
        lines.append("🈯 <b>中文解读</b>：%s" % esc(cn))
    lines.append("")
    points = a.get("points") or []
    if points:
        lines.append("<b>要点</b>：")
        for i, p in enumerate(points[:5], 1):
            lines.append("%d. %s" % (i, esc(p)))
        lines.append("")

    assets = " · ".join(a.get("assets") or []) or "—"
    lines.append("<b>标的</b>：%s" % esc(assets))

    url = a.get("url") or ((a.get("urls") or [""])[0])
    count = a.get("count") or 0
    confirmed = a.get("confirmed")
    indep = a.get("independent_sources")
    if confirmed is True:
        src_txt = "已确认（%d 个独立源）" % (indep or count) if (indep or count) else "已确认"
    elif confirmed is False:
        best = a.get("best_tier")
        n = indep if indep else (count or 0)
        extra = "（共 %d 家）" % count if count and indep and count > indep else ""
        if best in ("T0", "T1", "T2"):
            src_txt = "%d 家独立源%s · <b>未确认</b>（单源可信但未交叉确认，建议核对官方）" % (n, extra)
        elif best == "T3":
            src_txt = "%d 家独立源%s · <b>未确认</b>（仅聚合/搬运站，建议核对官方）" % (n, extra)
        else:
            src_txt = ("%d 家独立源%s · <b>未确认</b>（建议核对官方）" % (n, extra)) if n else "未确认（建议核对官方）"
    else:
        src_txt = "%d 家" % count if count else "—"
    if "news.google." in urlparse(url or "").netloc.lower():
        src_txt += "（聚合站跳转）"
    lines.append("<b>来源</b>：%s" % src_txt)

    for d in (a.get("detail") or [])[:3]:
        lines.append("• %s" % esc(d))

    lines.append("<b>影响</b>：%s" % ev.impact_cn(category))
    lines.append("<b>动作参考</b>：%s（不构成投资建议）" % ev.action_cn(category))

    hard = a.get("hard") or []
    if hard:
        lines.append("<b>触发</b>：%s" % "、".join(ev.hard_cn(h) for h in hard))

    meta = []
    if a.get("score") is not None:
        meta.append("评分 %.1f" % float(a["score"]))
    if a.get("age_hours") is not None:
        meta.append("发布 %.1f 小时前" % float(a["age_hours"]))
    abs_t = _abs_time(a.get("published_at"))
    if abs_t:
        meta.append(abs_t)
    if meta:
        lines.append("<b>时间/分（）</b>：%s" % " · ".join(meta))

    if url:
        lines.append('🔗 <a href="%s">查看原文</a>' % html.escape(url, quote=True))
    if job:
        lines.append("🏷 任务：%s" % esc(job))

    return _truncate("\n".join(lines))
