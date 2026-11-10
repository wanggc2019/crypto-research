# -*- coding: utf-8 -*-
"""content.py —— 课程内容加载、校验与分轨（v2：每条 10–15 分钟）。

内容文件：config/curriculum/stage{1..4}.yaml（格式见该目录 README.md）。
对外只暴露：load_all / split_tracks / validate / validate_or_raise / inventory / quiz_items。
"""
import os
import re

import common

VALID_TYPES = ("concept", "theory", "practice")
ID_RE = re.compile(r"^([ctp])(\d{3})$")
URL_RE = re.compile(r"^https?://[^\s]+$")

SECTIONS_MIN, SECTIONS_MAX = 4, 6
SECTION_HEAD_MAX = 10
SECTION_BODY_MIN, SECTION_BODY_MAX = 50, 420
BODY_TOTAL_MIN, BODY_TOTAL_MAX = 800, 1800
TITLE_MAX = 24
QUIZ_MIN, QUIZ_MAX = 1, 3
QUIZ_Q_MIN, QUIZ_Q_MAX = 8, 60
QUIZ_A_MIN, QUIZ_A_MAX = 20, 200
MINUTES_RANGE = (10, 15)
SOURCES_MAX = 2
QUIZ_REQUIRED = {"concept": 2, "theory": 2, "practice": 1}


def _items_of(data):
    if not isinstance(data, dict):
        return []
    items = data.get("items")
    return items if isinstance(items, list) else []


def load_all(content_dir=None):
    """加载 content_dir 下所有 stage*.yaml，按 (stage, 轨道, order) 排序返回。"""
    content_dir = content_dir or common.CURRICULUM_DIR
    items = []
    if not os.path.isdir(content_dir):
        return items
    for name in sorted(os.listdir(content_dir)):
        if not name.startswith("stage") or not name.endswith((".yaml", ".yml")):
            continue
        data = common.core_config.load_yaml(os.path.join(content_dir, name))
        for item in _items_of(data):
            if isinstance(item, dict):
                item = dict(item)
                item.setdefault("_file", name)
                item.setdefault("_stage_title", data.get("title") or "")
                item.setdefault("_weeks", data.get("weeks") or "")
                items.append(item)
    items.sort(key=lambda it: (_int(it.get("stage"), 0), 0 if it.get("type") == "concept" else 1,
                               _int(it.get("order"), 0), str(it.get("id"))))
    return items


def split_tracks(items):
    """拆成 (concepts, lessons)；lessons 保持 stage/order 顺序。"""
    concepts = [it for it in items if it.get("type") == "concept"]
    lessons = [it for it in items if it.get("type") in ("theory", "practice")]
    return concepts, lessons


def _int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _text_len(value):
    return len(str(value or "").strip())


def quiz_items(item):
    """统一 quiz 形态：兼容旧版 {q,a} 与新版 [{q,a}, ...]。"""
    raw = item.get("quiz")
    if isinstance(raw, dict):
        return [raw] if raw else []
    if isinstance(raw, list):
        return [q for q in raw if isinstance(q, dict)]
    return []


def _validate_item(item, seen_ids):
    """返回该条目的问题列表（空列表 = 通过）。"""
    problems = []
    where = "id=%s" % (item.get("id") or "?")

    missing = [k for k in ("id", "type", "stage", "order", "title", "minutes", "sections", "quiz")
               if item.get(k) in (None, "")]
    if missing:
        return ["%s 缺少字段 %s" % (where, ",".join(missing))]

    item_id = str(item.get("id") or "")
    if not ID_RE.match(item_id):
        problems.append("%s id 不符合 c101/t201/p308 形式" % where)
    if item_id in seen_ids:
        problems.append("%s id 重复" % where)
    seen_ids.add(item_id)

    item_type = str(item.get("type"))
    if item_type not in VALID_TYPES:
        problems.append("%s type 非法：%r" % (where, item_type))

    stage = _int(item.get("stage"), 0)
    if stage not in (1, 2, 3, 4):
        problems.append("%s stage 必须是 1-4" % where)

    order = _int(item.get("order"), 0)
    if order < 1 or order > 999:
        problems.append("%s order 必须是正整数" % where)

    if _text_len(item.get("title")) > TITLE_MAX:
        problems.append("%s title 超过 %d 字" % (where, TITLE_MAX))

    minutes = _int(item.get("minutes"), 0)
    if not (MINUTES_RANGE[0] <= minutes <= MINUTES_RANGE[1]):
        problems.append("%s minutes 应在 %d-%d 分钟（v2 每条 10–15 分钟）"
                        % (where, MINUTES_RANGE[0], MINUTES_RANGE[1]))

    sections = item.get("sections")
    body_len = 0
    if not isinstance(sections, list) or not (SECTIONS_MIN <= len(sections) <= SECTIONS_MAX):
        problems.append("%s sections 必须是 %d-%d 节" % (where, SECTIONS_MIN, SECTIONS_MAX))
    else:
        for idx, sec in enumerate(sections):
            if not isinstance(sec, dict):
                problems.append("%s sections[%d] 不是 {h,p}" % (where, idx))
                continue
            head, body = str(sec.get("h") or "").strip(), str(sec.get("p") or "").strip()
            if not head or _text_len(head) > SECTION_HEAD_MAX:
                problems.append("%s sections[%d].h 为空或超过 %d 字" % (where, idx, SECTION_HEAD_MAX))
            if not (SECTION_BODY_MIN <= _text_len(body) <= SECTION_BODY_MAX):
                problems.append("%s sections[%d].p 应在 %d-%d 字"
                                % (where, idx, SECTION_BODY_MIN, SECTION_BODY_MAX))
            body_len += _text_len(body)

    quizzes = quiz_items(item)
    if not (QUIZ_MIN <= len(quizzes) <= QUIZ_MAX):
        problems.append("%s quiz 必须是 %d-%d 道题的列表" % (where, QUIZ_MIN, QUIZ_MAX))
    required = QUIZ_REQUIRED.get(item_type, 1)
    if len(quizzes) < required:
        problems.append("%s 的 %s 至少需要 %d 道自测题" % (where, item_type, required))
    for idx, quiz in enumerate(quizzes):
        q, a = str(quiz.get("q") or "").strip(), str(quiz.get("a") or "").strip()
        if not (QUIZ_Q_MIN <= _text_len(q) <= QUIZ_Q_MAX):
            problems.append("%s quiz[%d].q 应在 %d-%d 字" % (where, idx, QUIZ_Q_MIN, QUIZ_Q_MAX))
        if not (QUIZ_A_MIN <= _text_len(a) <= QUIZ_A_MAX):
            problems.append("%s quiz[%d].a 应在 %d-%d 字" % (where, idx, QUIZ_A_MIN, QUIZ_A_MAX))
        body_len += _text_len(q) + _text_len(a)

    if not (BODY_TOTAL_MIN <= body_len <= BODY_TOTAL_MAX):
        problems.append("%s 正文合计 %d 字，应在 %d-%d 字（10–15 分钟）"
                        % (where, body_len, BODY_TOTAL_MIN, BODY_TOTAL_MAX))

    sources = item.get("sources")
    if sources is not None:
        if not isinstance(sources, list) or len(sources) > SOURCES_MAX:
            problems.append("%s sources 必须是最多 %d 条的列表" % (where, SOURCES_MAX))
        else:
            for url in sources:
                if not URL_RE.match(str(url or "")):
                    problems.append("%s sources 不是合法 http(s) URL：%r" % (where, url))
    return problems


def validate(items):
    """校验条目列表，返回问题列表。"""
    problems = []
    seen_ids = set()
    for item in items:
        problems.extend(_validate_item(item, seen_ids))
    return problems


def validate_or_raise(items):
    problems = validate(items)
    if problems:
        raise ValueError("课程内容校验失败（%d 项）：\n- %s" % (len(problems), "\n- ".join(problems[:30])))
    return True


def inventory(items):
    """按阶段统计各类型数量，供 status 与测试使用。"""
    out = {}
    for item in items:
        stage = _int(item.get("stage"), 0)
        bucket = out.setdefault(stage, {"concept": 0, "theory": 0, "practice": 0, "total": 0})
        item_type = str(item.get("type"))
        if item_type in bucket:
            bucket[item_type] += 1
        bucket["total"] += 1
    return out
