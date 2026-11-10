#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""classify.py —— 规则门（G1）：文档类型 / 主题 / 文号 / 施行日 / 重要性。

全部是确定性规则，不调用 AI。目标是「召回优先」：
- 标题命中主题 → 进候选；
- 正文命中但标题没命中 → 降级归档（P2），不推送；
- 中央 T0 源的法规/司法解释即使标题没命中，也保留进索引供人工复查。
"""
import re

from common import clean_text, load_config


def _rules():
    return load_config("rules.yaml")


def _app():
    return load_config("app.yaml")


def detect_doc_type(title, source):
    """按 rules.yaml 顺序识别文档类型；识别不出用源的 default_doc_type。"""
    text = title or ""
    rules = (_rules().get("doc_types") or {})
    order = [
        "judicial_interpretation",
        "law",
        "admin_regulation",
        "dept_rule",
        "normative_doc",
        "case",
        "interpretation",
    ]
    for doc_type in order:
        for pattern in rules.get(doc_type) or []:
            if re.search(pattern, text, re.I):
                return doc_type
    return source.get("default_doc_type") or "news"


def doc_type_cn(doc_type):
    return ((_rules().get("doc_type_cn") or {}).get(doc_type)) or doc_type or "其他"


def extract_doc_no(text):
    pattern = _rules().get("doc_no_re")
    if not pattern:
        return ""
    m = re.search(pattern, text or "")
    return re.sub(r"\s+", "", m.group(1)) if m else ""


def extract_effective(text, published_at):
    """返回 YYYY-MM-DD；支持「自X年X月X日起施行」和「自发布之日起施行」。"""
    body = text or ""
    pattern = _rules().get("effective_re")
    if pattern:
        m = re.search(pattern, body)
        if m:
            return "%s-%02d-%02d" % (m.group(1), int(m.group(2)), int(m.group(3)))
    if re.search(r"自(发布|印发|公布)之日起(施行|实施|执行)", body):
        return published_at or ""
    return ""


def topics_hit(text, topics=None):
    topics = topics if topics is not None else (_app().get("topics") or [])
    low = (text or "").lower()
    out = []
    for t in topics:
        if not t:
            continue
        token = str(t).lower()
        if re.match(r"^[a-z0-9 ]+$", token):
            if re.search(r"\b" + re.escape(token) + r"\b", low):
                out.append(t)
        elif token in low:
            out.append(t)
    return out


def _high_topics():
    return _rules().get("high_topics") or []


def guess_issuer(title, source_name):
    """标题里「……关于/公告/令……」之前的部分通常是发文机关；识别不出退回源名。"""
    title = clean_text(title)
    for kw in ("关于", "公告[〔\\[［]", "令[〔\\[［]", "印发", "发布", "公布"):
        m = re.match(r"^(.{4,80}?)(?=%s)" % kw, title)
        if m:
            return m.group(1).strip()
    if re.search(r"(中国人民银行|证监会|外汇局|最高人民法院|最高人民检察院)", title) and len(title) < 60:
        return title
    return source_name or ""


def detect_foreign_doc_type(title):
    """国外监管源：规则制定 / 执法 / 指引 / 讲话 / 新闻稿。"""
    rules = _rules().get("foreign_doc_types") or {}
    for doc_type in ("rulemaking", "enforcement", "guidance", "speech"):
        for pattern in rules.get(doc_type) or []:
            if re.search(pattern, title or "", re.I):
                return doc_type
    return "press_release"


def level_for(doc_type, title, title_topics, body_topics, jurisdiction="CN"):
    """P0 立即 / P1 当日 / P2 归档 / None 不相关。"""
    if jurisdiction and jurisdiction != "CN":
        # 国外源：只按标题命中，避免通用新闻流把无关内容带进来
        if not title_topics:
            return None
        if doc_type in ("rulemaking", "final_rule"):
            return "P0"
        if doc_type in ("proposed_rule", "enforcement", "guidance", "speech"):
            return "P1"
        return "P2"
    if not title_topics and not body_topics:
        return None
    if doc_type in ("law", "admin_regulation", "judicial_interpretation"):
        return "P0"
    if doc_type in ("dept_rule", "normative_doc"):
        high = [h for h in _high_topics() if h in (title or "")]
        if high:
            return "P0"
        return "P1" if title_topics else "P2"
    if doc_type == "case":
        return "P1"
    if doc_type == "interpretation":
        return "P2"
    return "P2" if title_topics else None


def build_record(item):
    """采集条目 → 分类后的记录（保持 item 原字段）。"""
    title = clean_text(item.get("title") or "")
    body = item.get("body") or ""
    topics_all = _app().get("topics") or []
    title_topics = topics_hit(title, topics_all)
    body_topics = topics_hit(body[:4000], topics_all)
    jurisdiction = item.get("jurisdiction") or "CN"
    doc_type = item.get("doc_type_hint") or (
        detect_foreign_doc_type(title) if jurisdiction != "CN" else detect_doc_type(title, item)
    )
    level = level_for(doc_type, title, title_topics, body_topics, jurisdiction)

    rec = dict(item)
    rec.update(
        {
            "title": title,
            "jurisdiction": jurisdiction,
            "doc_type": doc_type,
            "doc_type_cn": doc_type_cn(doc_type),
            "doc_no": item.get("doc_no_hint") or extract_doc_no(title + "\n" + body[:3000]),
            "issuer": item.get("issuer_hint") or guess_issuer(title, item.get("source_name")),
            "effective_at": item.get("effective_hint") or extract_effective(body, item.get("published_at")),
            "topics": title_topics or body_topics,
            "title_topics": title_topics,
            "level": level,
        }
    )
    return rec


def is_relevant(rec):
    """标题命中主题才进库；P0 允许正文命中（如洗钱司法解释标题没写「虚拟资产」）。"""
    if not rec.get("level"):
        return False
    if rec.get("jurisdiction") and rec["jurisdiction"] != "CN":
        return bool(rec.get("title_topics"))  # 国外源必须有标题级命中
    if rec.get("title_topics"):
        return True
    return rec.get("level") == "P0"


def supersedes_of(rec):
    pattern = _rules().get("supersede_re")
    if not pattern:
        return []
    found = re.findall(pattern, rec.get("body") or "")
    out = []
    for m in found:
        if isinstance(m, tuple):
            out.extend([x for x in m if x])
        elif m:
            out.append(m)
    return [re.sub(r"\s+", "", x) for x in out]
