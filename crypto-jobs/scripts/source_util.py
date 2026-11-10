#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""source_util.py —— 招聘源解析共用的小工具（纯函数，不联网、不落盘）。

被 source_ats.py（Greenhouse / Lever / Ashby）与 source_boards.py（RSS / Web3.career /
RemoteOK / Remotive / Jobicy）复用；字段归一约定见 source_adapters.py。
"""
import datetime
import html
import re

import common

_DATE_FORMATS = (
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S.%f%z",
    "%Y-%m-%d %H:%M:%S %z",
    "%Y-%m-%d %H:%M:%S%z",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
    "%d %b %Y",
)

_REMOTE_RE = re.compile(r"(remote|distributed|anywhere|work from home|wfh|telecommute|work-from-home)")
_ONSITE_RE = re.compile(r"(on-?site|in[- ]office|office[- ]based)")

_EMPLOYMENT_PATTERNS = (
    (re.compile(r"\bintern(?:ship)?s?\b"), "Internship"),
    (re.compile(r"\bpart[- ]?time\b"), "Part-time"),
    (re.compile(r"\bcontract(?:or|ing)?\b|\bfreelance\b"), "Contract"),
    (re.compile(r"\btemp(?:orary)?\b"), "Temporary"),
    (re.compile(r"\bfull[- ]?time\b|\bpermanent\b"), "Full-time"),
)

_CURRENCY_SYMBOL = {"USD": "$", "EUR": "€", "GBP": "£", "CAD": "C$", "AUD": "A$", "SGD": "S$"}


def _text(value):
    """clean_text 的别名；非字符串先转字符串（None → ""）。"""
    if value is None:
        return ""
    return common.clean_text(value if isinstance(value, str) else str(value))


def html_to_text(raw):
    """HTML → 纯文本。

    Greenhouse 的 content 是「二次转义」的 HTML（&lt;p&gt;），所以先反转义再清洗；
    其他源多为一次性 HTML 或纯文本，同一套处理也成立（重复清洗是无害的）。
    """
    if not raw:
        return ""
    text = html.unescape(str(raw))
    text = common.clean_text(text)
    return common.clean_text(html.unescape(text))


def to_iso(value):
    """任意日期/epoch → 带时区 ISO 字符串；无法解析返回 ""。"""
    if value is None or value == "":
        return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        ts = float(value)
        if ts > 1e12:  # 毫秒时间戳（Lever createdAt 等）
            ts /= 1000.0
        try:
            stamp = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc)
            return stamp.isoformat(timespec="seconds")
        except (OverflowError, OSError, ValueError):
            return ""
    raw = str(value).strip()
    parsed = common.parse_date(raw)
    if parsed:
        return parsed
    for fmt in _DATE_FORMATS:
        try:
            return datetime.datetime.strptime(raw, fmt).astimezone().isoformat(timespec="seconds")
        except ValueError:
            continue
    return ""


def _dedupe(values):
    """清洗 + 去重（保持顺序），过滤空值。"""
    out = []
    for value in values or []:
        text = _text(value)
        if text and text not in out:
            out.append(text)
    return out


def derive_remote(locations, explicit="", description="", assume_remote=False):
    """远程属性归一：remote / hybrid / onsite / ""。

    - explicit（ATS 的 workplaceType 等）优先；
    - hybrid 看地点与 JD 前 800 字；
    - assume_remote=True 用于「本身就是远程招聘站」的 T2 源（地点只是地域限制）；
    - 地点存在但无远程线索 → onsite；地点为空且 JD 无远程表述 → ""。
    """
    exp = _text(explicit).lower().replace(" ", "")
    if exp in ("remote", "hybrid", "onsite"):
        return exp
    if "remote" in exp:
        return "remote"
    loc_text = " ".join(_dedupe(locations)).lower()
    desc_hint = (description or "")[:800].lower()
    if "hybrid" in loc_text or "hybrid" in desc_hint:
        return "hybrid"
    if _REMOTE_RE.search(loc_text):
        return "remote"
    if assume_remote:
        return "remote"
    if not loc_text:
        return "remote" if _REMOTE_RE.search(desc_hint) else ""
    if _ONSITE_RE.search(loc_text):
        return "onsite"
    if _REMOTE_RE.search(desc_hint):
        return "remote"
    return "onsite"


def norm_employment(raw):
    """雇佣类型归一（Full-time / Part-time / Contract / Internship / Temporary / ""）。"""
    low = _text(raw).lower().replace("_", "-")
    if not low:
        return ""
    for pattern, value in _EMPLOYMENT_PATTERNS:
        if pattern.search(low):
            return value
    return ""


def _to_number(value):
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return None


def _fmt_number(value):
    if value is None or value != value:  # None / NaN
        return ""
    rounded = round(value)
    if abs(value - rounded) < 1e-9:
        return "%d" % int(rounded)
    return "%.1f" % value


def _interval_suffix(interval):
    low = _text(interval).lower()
    if not low:
        return ""
    if "hour" in low:
        return "/hour"
    if "month" in low:
        return "/month"
    if "year" in low or "annual" in low:
        return "/year"
    if "day" in low:
        return "/day"
    return ""


def format_salary(min_value=None, max_value=None, currency="", interval=""):
    """薪酬 → 可读字符串（缺失返回 ""）。0 视为未设置（部分 API 用 0 占位）。"""
    low, high = _to_number(min_value), _to_number(max_value)
    if low == 0:
        low = None
    if high == 0:
        high = None
    if low is None and high is None:
        return ""
    cur = _text(currency).upper()
    symbol = _CURRENCY_SYMBOL.get(cur, "")

    def _one(number):
        if number is None:
            return ""
        return "%s%s" % (symbol, _fmt_number(number))

    if low is not None and high is not None:
        body = "%s - %s" % (_one(low), _one(high))
    else:
        body = _one(low) or _one(high)
    parts = [body]
    if cur and not symbol:
        parts.append(cur)
    suffix = _interval_suffix(interval)
    if suffix:
        parts.append(suffix)
    return " ".join(part for part in parts if part)


def slugify_company(name):
    """公司名 → slug（小写、非字母数字转 -）。"""
    slug = re.sub(r"[^0-9a-z]+", "-", _text(name).lower()).strip("-")
    return slug


def _make_job(
    job_id="",
    title="",
    url="",
    company="",
    company_slug="",
    locations=None,
    remote="",
    posted_at="",
    updated_at="",
    employment="",
    salary="",
    description_text="",
    tags=None,
):
    """组装中间态岗位 dict（字段与 sources.py 契约一致，未裁剪正文）。"""
    return {
        "job_id": str(job_id or ""),
        "title": _text(title),
        "url": (url or "").strip(),
        "company": _text(company),
        "company_slug": _text(company_slug) or slugify_company(company),
        "locations": _dedupe(locations),
        "remote": remote if remote in ("remote", "hybrid", "onsite") else "",
        "posted_at": to_iso(posted_at),
        "updated_at": to_iso(updated_at),
        "employment": employment,
        "salary": salary or "",
        "description_text": description_text,
        "tags": _dedupe(tags),
    }


def _first(text, pattern):
    """取第一个匹配组并清洗成文本。"""
    match = re.search(pattern, text or "", re.S | re.I)
    return _text(match.group(1)) if match else ""


