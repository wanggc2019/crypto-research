#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""source_ats.py —— T0 官方 ATS 适配器（Greenhouse / Lever / Ashby）。

只做「原始 JSON → 中间态岗位 dict」，字段收口在 source_adapters.parse_source，
网络与降级由 sources.py 负责。薪酬/远程/雇佣类型尽量取官方结构化字段。
"""
import json
import re

from source_util import (
    _make_job,
    _text,
    _to_number,
    derive_remote,
    format_salary,
    html_to_text,
    norm_employment,
)


# ============================== T0 · Greenhouse ==============================
def _greenhouse_salary(item):
    """Greenhouse 薪酬：优先 pay_input_ranges（分），其次 metadata 里的薪酬字段。"""
    for entry in item.get("pay_input_ranges") or []:
        if not isinstance(entry, dict):
            continue
        low = _to_number(entry.get("min_cents"))
        high = _to_number(entry.get("max_cents"))
        if low or high:
            return format_salary(
                (low / 100.0) if low is not None else None,
                (high / 100.0) if high is not None else None,
                entry.get("currency_type") or "",
                entry.get("interval") or "",
            )
    for entry in item.get("metadata") or []:
        if not isinstance(entry, dict):
            continue
        name = _text(entry.get("name")).lower()
        if not any(key in name for key in ("salary", "pay range", "compensation", "pay transparency")):
            continue
        value = entry.get("value")
        if isinstance(value, dict):
            return format_salary(
                value.get("min_value"), value.get("max_value"),
                value.get("currency_type") or "", value.get("unit") or "",
            )
        text = _text(value)
        if re.search(r"\d", text):
            return text[:160]
    return ""


def parse_greenhouse(raw, description_max_chars=20000):
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Greenhouse 响应不是对象")
    jobs = []
    for item in data.get("jobs") or []:
        if not isinstance(item, dict):
            continue
        description = html_to_text(item.get("content") or "")
        location = item.get("location") or {}
        offices = [o.get("name") for o in item.get("offices") or [] if isinstance(o, dict)]
        locations = [_text(location.get("name"))] + offices
        departments = [d.get("name") for d in item.get("departments") or [] if isinstance(d, dict)]
        jobs.append(
            _make_job(
                job_id=item.get("id"),
                title=item.get("title"),
                url=item.get("absolute_url") or "",
                company=item.get("company_name") or "",
                locations=locations,
                remote=derive_remote(locations, description=description),
                posted_at=item.get("first_published") or item.get("updated_at"),
                updated_at=item.get("updated_at"),
                employment=norm_employment(item.get("title")),
                salary=_greenhouse_salary(item),
                description_text=description,
                tags=departments,
            )
        )
    return jobs


# ============================== T0 · Lever ==============================
def parse_lever(raw, description_max_chars=20000):
    data = json.loads(raw)
    if isinstance(data, dict):  # 容错：个别代理返回 {"postings": [...]}
        data = data.get("postings") or []
    if not isinstance(data, list):
        raise ValueError("Lever 响应不是数组")
    jobs = []
    for item in data:
        if not isinstance(item, dict):
            continue
        categories = item.get("categories") or {}
        locations = [categories.get("location")] + list(categories.get("allLocations") or [])
        description = html_to_text(
            item.get("descriptionPlain") or item.get("description") or item.get("descriptionBody") or ""
        )
        salary_range = item.get("salaryRange") or {}
        jobs.append(
            _make_job(
                job_id=item.get("id"),
                title=item.get("text") or item.get("opening"),
                url=item.get("hostedUrl") or item.get("applyUrl") or "",
                locations=locations,
                remote=derive_remote(locations, explicit=item.get("workplaceType") or "", description=description),
                posted_at=item.get("createdAt"),
                employment=norm_employment(categories.get("commitment")),
                salary=format_salary(
                    salary_range.get("min"), salary_range.get("max"),
                    salary_range.get("currency") or "", salary_range.get("interval") or "",
                ),
                description_text=description,
                tags=[categories.get("team"), categories.get("department"), categories.get("commitment")],
            )
        )
    return jobs


# ============================== T0 · Ashby ==============================
def _ashby_locations(item):
    locations = [item.get("location")]
    for entry in item.get("secondaryLocations") or []:
        if isinstance(entry, dict):
            locations.append(entry.get("location") or entry.get("name"))
        else:
            locations.append(entry)
    return locations


def _ashby_salary(item):
    comp = item.get("compensation") or {}
    for entry in comp.get("summaryComponents") or []:
        if not isinstance(entry, dict):
            continue
        if _text(entry.get("compensationType")).lower() != "salary":
            continue
        salary = format_salary(
            entry.get("minValue"), entry.get("maxValue"),
            entry.get("currencyCode") or "", entry.get("interval") or "",
        )
        if salary:
            return salary
    summary = _text(comp.get("compensationTierSummary") or comp.get("scrapeableCompensationSalarySummary"))
    return summary[:200]


def parse_ashby(raw, description_max_chars=20000):
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Ashby 响应不是对象")
    jobs = []
    for item in data.get("jobs") or []:
        if not isinstance(item, dict):
            continue
        description = html_to_text(item.get("descriptionPlain") or item.get("descriptionHtml") or "")
        locations = _ashby_locations(item)
        remote = derive_remote(locations, explicit=item.get("workplaceType") or "", description=description)
        if not remote and item.get("isRemote"):
            remote = "remote"
        jobs.append(
            _make_job(
                job_id=item.get("id"),
                title=item.get("title"),
                url=item.get("jobUrl") or item.get("applyUrl") or "",
                locations=locations,
                remote=remote,
                posted_at=item.get("publishedAt"),
                employment=norm_employment(item.get("employmentType")),
                salary=_ashby_salary(item),
                description_text=description,
                tags=[item.get("department"), item.get("team")],
            )
        )
    return jobs


