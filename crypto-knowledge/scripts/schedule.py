# -*- coding: utf-8 -*-
"""schedule.py —— 选卡、间隔复习（SRS）与状态推进。

状态文件：data/state/progress.json
{
  "schema_version": 1,
  "created": "2026-11-10",
  "cursors": {"concept": 0, "lesson": 0},          # 新内容两条轨道的游标
  "items": {"c101": {"first_on": "...", "last_on": "...", "reps": 1, "next_due": "..."}},
  "sent": {"2026-11-10": {"13": ["c101"], "19": ["t101"], "23": ["c007"]}},
  "history": [{"date": "...", "slot": "13", "ids": ["c101"], "mode": "new"}]
}

设计取舍：
- 只有真正发出去（或显式 --force）才推进状态，避免“跑一轮没推送却消耗内容”。
- 23:00 的复习只推进“到期待复习”的条目；当天新发的两条不重复计入 SRS。
- 内容池耗尽后不空转：概念/课程都退化为间隔复习（课程优先循环实践条目）。
"""
import datetime

SCHEMA_VERSION = 1
SLOTS = ("13", "19", "21", "23")
SLOT_KIND = {"13": "concept", "19": "lesson", "21": "recap", "23": "review"}
SLOT_HOURS = {"13": 13, "19": 19, "21": 21, "23": 23}
DEFAULT_SRS_DAYS = [1, 3, 7, 14, 30, 60]


def slot_hour(slot):
    """时段对应的整点小时（用于判断是否已到点、支持错过补发）。"""
    return SLOT_HOURS.get(str(slot), 0)


def parse_day(value):
    if isinstance(value, datetime.date):
        return value
    return datetime.date.fromisoformat(str(value)[:10])


def iso_day(value):
    return parse_day(value).isoformat()


def _add_days(day_str, days):
    return (datetime.date.fromisoformat(day_str) + datetime.timedelta(days=int(days))).isoformat()


def default_state(today):
    return {
        "schema_version": SCHEMA_VERSION,
        "created": iso_day(today),
        "cursors": {"concept": 0, "lesson": 0},
        "items": {},
        "sent": {},
        "history": [],
    }


def normalize(state, today):
    """容错：损坏/旧版本状态回退为空状态，缺字段补默认值。"""
    today = iso_day(today)
    if not isinstance(state, dict) or state.get("schema_version") != SCHEMA_VERSION:
        return default_state(today)
    state.setdefault("created", today)
    state.setdefault("cursors", {"concept": 0, "lesson": 0})
    for track in ("concept", "lesson"):
        try:
            state["cursors"][track] = max(0, int(state["cursors"].get(track, 0)))
        except (TypeError, ValueError):
            state["cursors"][track] = 0
    if not isinstance(state.get("items"), dict):
        state["items"] = {}
    if not isinstance(state.get("sent"), dict):
        state["sent"] = {}
    if not isinstance(state.get("history"), list):
        state["history"] = []
    return state


def already_sent(state, date, slot):
    """该日该时段是否已发送；21:00/周报/无到期复习的 23:00 会写空列表，必须按 key 判断。"""
    return slot in ((state.get("sent") or {}).get(iso_day(date)) or {})


def day_number(state, date):
    try:
        start = parse_day(state.get("created"))
    except (TypeError, ValueError):
        start = parse_day(date)
    return max(1, (parse_day(date) - start).days + 1)


def due_items(state, items, date):
    """到期应复习的条目，按 next_due 升序。"""
    day = iso_day(date)
    out = []
    for item in items:
        rec = state["items"].get(item["id"]) or {}
        due = rec.get("next_due")
        if due and due <= day:
            out.append(item)
    out.sort(key=lambda it: (state["items"][it["id"]].get("next_due") or "", it["id"]))
    return out


def today_items(state, items, date):
    """当天 13:00 / 19:00 已发的条目（用于睡前回顾）。"""
    sent = (state.get("sent") or {}).get(iso_day(date)) or {}
    by_id = {it["id"]: it for it in items}
    out = []
    for slot in ("13", "19"):
        for item_id in sent.get(slot) or []:
            item = by_id.get(item_id)
            if item and item not in out:
                out.append(item)
    return out


def cursor_item(state, track, queue):
    idx = int(state["cursors"].get(track, 0))
    if 0 <= idx < len(queue):
        return queue[idx], "new"
    return None, None


def least_recent(state, queue):
    """队列耗尽时的兜底：挑最久没出现过的条目。"""
    def key(item):
        rec = state["items"].get(item["id"]) or {}
        return (rec.get("last_on") or "0000-00-00", item.get("order") or 0)
    return min(queue, key=key)


def pick_concept(state, concepts, date):
    if not concepts:
        return None
    item, mode = cursor_item(state, "concept", concepts)
    if item:
        return {"item": item, "mode": mode}
    due = due_items(state, concepts, date)
    if due:
        return {"item": due[0], "mode": "review"}
    return {"item": least_recent(state, concepts), "mode": "review"}


def pick_lesson(state, lessons, date):
    if not lessons:
        return None
    item, mode = cursor_item(state, "lesson", lessons)
    if item:
        return {"item": item, "mode": mode}
    due = due_items(state, lessons, date)
    if due:
        return {"item": due[0], "mode": "review"}
    practices = [it for it in lessons if it.get("type") == "practice"]
    return {"item": least_recent(state, practices or lessons), "mode": "review"}


def pick_review(state, items, date, max_items=2):
    """返回 (今日已发条目, 到期待复习条目)；当天新发的条目排除在 SRS 之外。"""
    today = today_items(state, items, date)
    today_ids = {it["id"] for it in today}
    pending = [it for it in due_items(state, items, date) if it["id"] not in today_ids]
    limit = max(0, int(max_items))
    return today, pending[:limit]


def preview(state, concepts, lessons):
    """下一条新内容（用于睡前预告）。游标在发送后才推进，所以这里看到的就是“明天”。"""
    out = {}
    for track, queue in (("concept", concepts), ("lesson", lessons)):
        idx = int(state["cursors"].get(track, 0))
        out[track] = queue[idx] if 0 <= idx < len(queue) else None
    return out


def interval_days(reps, srs_days=None):
    """第 reps 次出现后，下次复习间隔（天）。"""
    days = [int(x) for x in (srs_days or DEFAULT_SRS_DAYS) if int(x) > 0] or [1]
    idx = min(max(0, int(reps) - 1), len(days) - 1)
    return days[idx]


def mark_sent(state, date, slot, plan, srs_days=None):
    """记录一次已发送：SRS、游标、当日 sent、history。plan 见 build 的返回值。"""
    day = iso_day(date)
    items = [it for it in (plan.get("items") or []) if it and it.get("id")]
    ids = [it["id"] for it in items]
    state.setdefault("sent", {}).setdefault(day, {})[slot] = ids
    state.setdefault("history", []).append(
        {"date": day, "slot": slot, "ids": ids, "mode": plan.get("mode")})
    for item in items:
        rec = state["items"].get(item["id"])
        if rec is None:
            state["items"][item["id"]] = {
                "first_on": day,
                "last_on": day,
                "reps": 1,
                "next_due": _add_days(day, interval_days(1, srs_days)),
            }
        else:
            reps = int(rec.get("reps") or 1) + 1
            rec["reps"] = reps
            rec["last_on"] = day
            rec["next_due"] = _add_days(day, interval_days(reps, srs_days))
    if plan.get("mode") == "new" and plan.get("track"):
        track = plan["track"]
        state["cursors"][track] = int(state["cursors"].get(track, 0)) + 1
    return state


def streak_days(state, date):
    """连续学习天数：从今天往前数，sent[date] 非空就算一天。"""
    day = parse_day(date)
    count = 0
    while True:
        key = (day - datetime.timedelta(days=count)).isoformat()
        if (state.get("sent") or {}).get(key):
            count += 1
        else:
            break
    return count


def recap_stats(state, items, date):
    """21:00 回顾与总结用的进度数据。"""
    return {
        "day_no": day_number(state, date),
        "learned": len(state.get("items") or {}),
        "total": len(items),
        "due": len(due_items(state, items, date)),
        "streak": streak_days(state, date),
    }


def week_range(date):
    """当前 ISO 周（周一 → 周日）的起止日期。"""
    day = parse_day(date)
    start = day - datetime.timedelta(days=day.isoweekday() - 1)
    return start, start + datetime.timedelta(days=6)


def is_weekday(date, weekday=7):
    """是否是指定 ISO 周几（7 = 周日）。"""
    return parse_day(date).isoweekday() == int(weekday)


def week_log(state, items, date, slots=("13", "19")):
    """本周每天实际发出的条目：[{date, items}, ...]，按日期升序、空天保留。"""
    start, end = week_range(date)
    by_id = {it["id"]: it for it in items}
    out = []
    day = start
    while day <= end:
        key = day.isoformat()
        sent = (state.get("sent") or {}).get(key) or {}
        picked = []
        for slot in slots:
            for item_id in sent.get(slot) or []:
                item = by_id.get(item_id)
                if item and item not in picked:
                    picked.append(item)
        out.append({"date": key, "items": picked})
        day += datetime.timedelta(days=1)
    return out


def week_active_days(state, date):
    """本周有学习记录（13 或 19 发出过）的天数。"""
    start, end = week_range(date)
    count = 0
    day = start
    while day <= end:
        sent = (state.get("sent") or {}).get(day.isoformat()) or {}
        if sent.get("13") or sent.get("19"):
            count += 1
        day += datetime.timedelta(days=1)
    return count


def stage_progress(state, items):
    """各阶段已学 / 总数，用于周报。"""
    out = {}
    learned = state.get("items") or {}
    for item in items:
        stage = int(item.get("stage") or 0)
        bucket = out.setdefault(stage, {"learned": 0, "total": 0})
        bucket["total"] += 1
        if item["id"] in learned:
            bucket["learned"] += 1
    return {stage: out[stage] for stage in sorted(out)}


def split_tracks(items):
    return ([it for it in items if it.get("type") == "concept"],
            [it for it in items if it.get("type") in ("theory", "practice")])


def progress(state, items, date):
    concepts, lessons = split_tracks(items)
    sent_today = (state.get("sent") or {}).get(iso_day(date)) or {}
    return {
        "date": iso_day(date),
        "day_no": day_number(state, date),
        "total": len(items),
        "concept_total": len(concepts),
        "lesson_total": len(lessons),
        "concept_cursor": min(int(state["cursors"].get("concept", 0)), len(concepts)),
        "lesson_cursor": min(int(state["cursors"].get("lesson", 0)), len(lessons)),
        "due": len(due_items(state, items, date)),
        "sent": {slot: bool(sent_today.get(slot)) for slot in SLOTS},
    }
