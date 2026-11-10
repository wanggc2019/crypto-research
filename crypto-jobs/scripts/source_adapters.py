#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""source_adapters.py —— 各招聘源解析适配器的统一入口（薄门面，不联网、不落盘）。

拆分为三个子模块（单文件 <500 行，见仓库 conventions）：
    source_util.py   共用小工具：HTML→文本、日期→ISO、远程/雇佣/薪酬归一、中间态 job 组装
    source_ats.py    T0 官方 ATS：Greenhouse / Lever / Ashby
    source_boards.py T1/T2 招聘站：RSS(CryptoJobsList) / Web3.career / RemoteOK / Remotive / Jobicy

对外只暴露 parse_source(source, raw, description_max_chars) 与 ADAPTERS；
网络请求、串行/超时/重试、单源降级、fixtures 回放统一由 sources.py 负责。
"""
from source_ats import parse_ashby, parse_greenhouse, parse_lever
from source_boards import (
    parse_jobicy,
    parse_remoteok,
    parse_remotive,
    parse_rss_jobs,
    parse_web3career,
)
from source_util import (
    _text,
    derive_remote,
    format_salary,
    html_to_text,
    norm_employment,
    slugify_company,
    to_iso,
)

ADAPTERS = {
    "greenhouse": parse_greenhouse,
    "lever": parse_lever,
    "ashby": parse_ashby,
    "rss_jobs": parse_rss_jobs,
    "web3career": parse_web3career,
    "remoteok": parse_remoteok,
    "remotive": parse_remotive,
    "jobicy": parse_jobicy,
}

__all__ = [
    "ADAPTERS",
    "derive_remote",
    "format_salary",
    "html_to_text",
    "norm_employment",
    "parse_source",
    "slugify_company",
    "to_iso",
]


def parse_source(source, raw, description_max_chars=20000):
    """按 source["type"] 分发解析；未知类型抛 ValueError（由 sources.py 记为源失败）。"""
    source_type = _text(source.get("type"))
    adapter = ADAPTERS.get(source_type)
    if adapter is None:
        raise ValueError("未知源类型 %r（source_id=%s）" % (source_type, source.get("id")))
    jobs = adapter(raw, description_max_chars=description_max_chars)
    return [job for job in jobs if job.get("title")]
