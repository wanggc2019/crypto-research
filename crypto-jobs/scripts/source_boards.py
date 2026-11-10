#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""source_boards.py —— T1/T2 招聘站适配器（RSS / Web3.career / RemoteOK / Remotive / Jobicy）。

T1 为加密垂直站（正文/日期齐全），T2 为通用远程站（由 sources.py 再套 crypto 过滤）。
只做「原始文本 → 中间态岗位 dict」，不做角色/技能判断。
"""
import html
import json
import re
import xml.etree.ElementTree as ET

from source_util import (
    _first,
    _make_job,
    _text,
    derive_remote,
    format_salary,
    html_to_text,
    norm_employment,
)

import common

# XML 命名空间：Dublin Core（公司名）与 Yahoo MRSS（地点）
_DC_CREATOR = "{http://purl.org/dc/elements/1.1/}creator"
_MRSS_LOCATION = "{http://search.yahoo.com/mrss/}location"

# ============================== T1 · RSS（CryptoJobsList） ==============================
def _rss_tags(description_html):
    """RSS 描述开头的 "Tags: <a>…</a> • <a>…</a>" 段 → 标签列表。"""
    match = re.search(r"Tags:\s*(.*?)(?:<p\b|$)", description_html or "", re.S | re.I)
    if not match:
        return []
    tags = [_text(text) for text in re.findall(r"<a[^>]*>(.*?)</a>", match.group(1), re.S)]
    return [tag for tag in tags if tag]


def parse_rss_jobs(raw, description_max_chars=20000):
    root = ET.fromstring(raw)
    channel = root.find("channel")
    if channel is None:
        raise ValueError("RSS 缺少 channel")
    jobs = []
    for item in channel.findall("item"):
        title = _text(item.findtext("title"))
        link = (item.findtext("link") or "").strip()
        description_html = item.findtext("description") or ""
        description = html_to_text(description_html)
        company = _text(item.findtext(_DC_CREATOR))
        locations = [_text(node.text) for node in item.findall(_MRSS_LOCATION)]
        tags = _rss_tags(description_html)
        job_id = link.rstrip("/").rsplit("/", 1)[-1]
        if not job_id:
            job_id = common.sha256(title + "|" + company)[:16]
        jobs.append(
            _make_job(
                job_id=job_id,
                title=title,
                url=link,
                company=company,
                locations=locations,
                remote=derive_remote(locations, description=description),
                employment=norm_employment(" ".join(tags)),
                description_text=description,
                tags=tags[:12],
            )
        )
    return jobs


# ============================== T1 · Web3.career ==============================
def _as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _title_key(value):
    """标题归一化匹配键（去 HTML 实体、标点、空格、大小写）。"""
    text = html.unescape(_text(value)).lower()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", text)


def _web3career_jsonld(raw):
    jobs = []
    for match in re.finditer(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', raw or "", re.S | re.I):
        try:
            data = json.loads(match.group(1))
        except (TypeError, ValueError):
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("@type") == "JobPosting":
                jobs.append(item)
            for sub in item.get("@graph") or []:
                if isinstance(sub, dict) and sub.get("@type") == "JobPosting":
                    jobs.append(sub)
    return jobs


def _ld_locations(job_ld):
    locations = []

    def _addr(value):
        if isinstance(value, dict):
            country = value.get("addressCountry")
            if isinstance(country, dict):
                country = country.get("name")
            locations.extend([value.get("addressLocality"), value.get("addressRegion"), country])
        elif value:
            locations.append(value)

    for location in _as_list(job_ld.get("jobLocation")):
        if isinstance(location, dict):
            _addr(location.get("address") or location)
        else:
            locations.append(location)
    for requirement in _as_list(job_ld.get("applicantLocationRequirements")):
        locations.append(requirement.get("name") if isinstance(requirement, dict) else requirement)
    return locations


def _ld_salary(job_ld):
    salary = job_ld.get("baseSalary")
    if not isinstance(salary, dict):
        return ""
    value = salary.get("value")
    if isinstance(value, dict):
        return format_salary(
            value.get("minValue"), value.get("maxValue"),
            salary.get("currency") or "", value.get("unitText") or "",
        )
    if value is not None:
        return format_salary(value, value, salary.get("currency") or "")
    return format_salary(salary.get("minValue"), salary.get("maxValue"), salary.get("currency") or "")


def parse_web3career(raw, description_max_chars=20000):
    """解析 Web3.career 首页：列表行给 url/公司/地点/标签，JSON-LD 给正文与日期（按标题配对）。"""
    ld_by_title = {}
    for job_ld in _web3career_jsonld(raw):
        key = _title_key(job_ld.get("title"))
        if key and key not in ld_by_title:
            ld_by_title[key] = job_ld
    jobs = []
    row_re = re.compile(r"<tr\b[^>]*\bdata-jobid=(\d+)[^>]*>(.*?)</tr>", re.S | re.I)
    for match in row_re.finditer(raw or ""):
        job_id, row_html = match.group(1), match.group(2)
        title = _first(row_html, r"<h2[^>]*>(.*?)</h2>")
        if not title:
            continue
        company = _first(row_html, r"<h3[^>]*>(.*?)</h3>")
        href = _first(row_html, r"tableTurboRowClick\(event,\s*'([^']+)'") or _first(
            row_html, r'href="(/[^"]*?/\d+)"'
        )
        if href and not href.startswith("http"):
            href = "https://web3.career" + ("" if href.startswith("/") else "/") + href
        job_ld = ld_by_title.get(_title_key(title)) or {}
        description = html_to_text(job_ld.get("description") or "")
        locations = _ld_locations(job_ld)
        job_type = _text(job_ld.get("jobLocationType")).upper()
        remote = "remote" if job_type == "TELECOMMUTE" else derive_remote(locations, description=description)
        tags = [
            _text(match.group(2))
            for match in re.finditer(r'<a[^>]*href="/([a-z0-9-]+)-jobs"[^>]*>(.*?)</a>', row_html, re.S | re.I)
        ]
        jobs.append(
            _make_job(
                job_id=job_id,
                title=title,
                url=href,
                company=company,
                locations=locations,
                remote=remote,
                posted_at=job_ld.get("datePosted"),
                employment=norm_employment(job_ld.get("employmentType")),
                salary=_ld_salary(job_ld),
                description_text=description,
                tags=tags,
            )
        )
    return jobs


# ============================== T2 · RemoteOK ==============================
def parse_remoteok(raw, description_max_chars=20000):
    data = json.loads(raw)
    if isinstance(data, dict):
        data = data.get("jobs") or []
    if not isinstance(data, list):
        raise ValueError("RemoteOK 响应不是数组")
    jobs = []
    for item in data:
        if not isinstance(item, dict) or not item.get("id") or "legal" in item:
            continue  # 第一项是 ToS 声明
        description = html_to_text(item.get("description") or "")
        locations = [item.get("location")]
        jobs.append(
            _make_job(
                job_id=item.get("id"),
                title=item.get("position") or item.get("title"),
                url=item.get("url") or item.get("apply_url") or "",
                company=item.get("company") or "",
                locations=locations,
                remote="remote",
                posted_at=item.get("date"),
                salary=format_salary(item.get("salary_min"), item.get("salary_max"), "USD"),
                description_text=description,
                tags=item.get("tags") or [],
            )
        )
    return jobs


# ============================== T2 · Remotive ==============================
def parse_remotive(raw, description_max_chars=20000):
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Remotive 响应不是对象")
    jobs = []
    for item in data.get("jobs") or []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        description = html_to_text(item.get("description") or "")
        locations = [item.get("candidate_required_location")]
        tags = list(item.get("tags") or []) + [item.get("category")]
        jobs.append(
            _make_job(
                job_id=item.get("id"),
                title=item.get("title"),
                url=item.get("url") or "",
                company=item.get("company_name") or "",
                locations=locations,
                remote=derive_remote(locations, description=description, assume_remote=True),
                posted_at=item.get("publication_date"),
                employment=norm_employment(item.get("job_type")),
                salary=_text(item.get("salary"))[:160],
                description_text=description,
                tags=tags,
            )
        )
    return jobs


# ============================== T2 · Jobicy ==============================
def parse_jobicy(raw, description_max_chars=20000):
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Jobicy 响应不是对象")
    jobs = []
    for item in data.get("jobs") or []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        description = html_to_text(item.get("jobDescription") or item.get("jobExcerpt") or "")
        locations = [item.get("jobGeo")]
        job_types = [str(entry) for entry in _as_list(item.get("jobType"))]
        tags = list(_as_list(item.get("jobIndustry"))) + job_types + [_text(item.get("jobLevel"))]
        jobs.append(
            _make_job(
                job_id=item.get("id"),
                title=item.get("jobTitle") or item.get("title"),
                url=item.get("url") or "",
                company=item.get("companyName") or item.get("company") or "",
                locations=locations,
                remote=derive_remote(locations, description=description, assume_remote=True),
                posted_at=item.get("pubDate"),
                employment=norm_employment(" ".join(job_types)),
                salary=format_salary(
                    item.get("annualSalaryMin"), item.get("annualSalaryMax"),
                    item.get("salaryCurrency") or "", "year",
                ),
                description_text=description,
                tags=tags,
            )
        )
    return jobs


