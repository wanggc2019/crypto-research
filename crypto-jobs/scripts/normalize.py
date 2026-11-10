#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""normalize.py —— 把各招聘源的原始岗位归一化为 `job/v1` 记录。

分层原则：
- sources.py 只负责「抓 + 字段归一」，不判断岗位角色；
- 本文件负责角色/资历/远程/去重等语义，技能抽取委托 skills.py；
- 纯函数优先，配置从 roles.yaml / app.yaml 传入，便于离线测试与复现。
"""
import re

import skills

import common

TIER_RANK = {"T0": 0, "T1": 1, "T2": 2}
DEFAULT_TARGET_ROLES = (
    "devops", "sre", "mlops", "platform", "software", "backend", "frontend", "fullstack",
    "mobile", "blockchain", "trading", "data", "ml", "embedded",
)


def _kw_re(kw):
    """关键词匹配：字母数字边界，避免 sre 命中 "sres" 之类；保留 ci/cd 等符号。"""
    kw = str(kw or "").strip().lower()
    if not kw:
        return None
    esc = re.escape(kw)
    left = r"(?<![a-z0-9])" if re.match(r"^[a-z0-9]", kw) else ""
    right = r"(?![a-z0-9])" if re.search(r"[a-z0-9]$", kw) else ""
    return re.compile(left + esc + right)


def has_kw(text, kw):
    rx = _kw_re(kw)
    return bool(rx and rx.search(text or ""))


def has_any_kw(text, keywords):
    return any(has_kw(text, kw) for kw in keywords or [])


def match_role(title, roles_cfg):
    """返回 (role_id, matched_keyword)。标题命中 exclude_keywords 时 role='excluded'。"""
    title_l = (title or "").lower()
    roles_cfg = roles_cfg or {}
    for kw in roles_cfg.get("exclude_keywords") or []:
        if has_kw(title_l, kw):
            return "excluded", kw
    for role in roles_cfg.get("roles") or []:
        for kw in role.get("keywords") or []:
            if has_kw(title_l, kw):
                return role.get("id") or "other", kw
    return "other", ""


def seniority_of(title, roles_cfg):
    """从标题解析资历；未知返回 'unknown'。"""
    title_l = (title or "").lower()
    for item in (roles_cfg or {}).get("seniority") or []:
        for kw in item.get("keywords") or []:
            if has_kw(title_l, kw):
                return item.get("id") or "unknown"
    return "unknown"


def remote_of(locations=None, description=""):
    """远程属性：优先看地点字段，其次看正文里的明确表述。"""
    loc = " ".join([str(x) for x in (locations or [])]).lower()
    blob = (loc + " " + (description or "")[:600]).lower()
    if any(k in loc for k in ("remote", "distributed", "anywhere", "work from home", "wfh")):
        return "remote"
    if "hybrid" in loc:
        return "hybrid"
    if any(k in blob for k in ("fully remote", "remote-first", "remote first", "remote position",
                               "100% remote", "work from home", "wfh")):
        return "remote"
    if loc.strip():
        return "onsite"
    return ""


def job_key(raw):
    """稳定主键：source_id + job_id；缺 id 时退化为 url/title 哈希。"""
    source_id = str(raw.get("source_id") or "")
    job_id = str(raw.get("job_id") or "")
    if job_id:
        return "%s:%s" % (source_id, job_id)
    basis = "|".join([source_id, str(raw.get("url") or ""), str(raw.get("title") or "")])
    return "%s:h:%s" % (source_id, common.sha256(basis)[:16])


def _clean_locations(raw):
    out = []
    for item in raw.get("locations") or []:
        text = common.clean_text(item)
        if text and text not in out:
            out.append(text)
    return out


def normalize_job(raw, cfg=None, roles_cfg=None, taxonomy=None, extract_fn=None):
    """单条原始岗位 → 归一化 job dict（不做目标角色过滤，只打标）。"""
    cfg = cfg or {}
    title = common.clean_text(raw.get("title") or "")
    description = raw.get("description_text") or ""
    role, matched = match_role(title, roles_cfg)
    extractor = extract_fn or skills.extract
    skill_res = extractor(description, taxonomy) if description else {
        "skills": [], "required": [], "counts": {}, "by_category": {}
    }
    locations = _clean_locations(raw)
    return {
        "key": job_key(raw),
        "source_id": raw.get("source_id") or "",
        "source_tier": raw.get("source_tier") or "T2",
        "company": raw.get("company") or "",
        "company_slug": raw.get("company_slug") or "",
        "job_id": str(raw.get("job_id") or ""),
        "title": title,
        "role": role,
        "role_matched": matched,
        "seniority": seniority_of(title, roles_cfg),
        "remote": remote_of(locations, description),
        "locations": locations,
        "url": raw.get("url") or "",
        "posted_at": raw.get("posted_at") or "",
        "updated_at": raw.get("updated_at") or "",
        "employment": raw.get("employment") or "",
        "salary": raw.get("salary") or None,
        "description_hash": common.sha256(description)[:16] if description else "",
        "description_len": len(description or ""),
        "skills": list(skill_res.get("skills") or []),
        "required_skills": list(skill_res.get("required") or []),
        "skill_counts": dict(skill_res.get("counts") or {}),
        "tags": list(raw.get("tags") or []),
    }


def dedupe_jobs(jobs):
    """跨源去重：同公司 + 同标题 + 同地点只保留来源等级最高的一条（T0 > T1 > T2）。

    返回 (kept, dropped)。同一等级/同一来源的多条（不同 job_id）视为不同岗位保留。
    """
    groups = {}
    for job in jobs:
        loc = "|".join(common.norm_title(x) for x in (job.get("locations") or [])[:1])
        group_key = (
            (job.get("company_slug") or job.get("company") or "").lower(),
            common.norm_title(job.get("title") or ""),
            loc,
        )
        groups.setdefault(group_key, []).append(job)

    kept, dropped = [], 0
    for _group, items in groups.items():
        best = min(TIER_RANK.get(j.get("source_tier"), 9) for j in items)
        best_items = [j for j in items if TIER_RANK.get(j.get("source_tier"), 9) == best]
        # 同源多条不同 job_id 保留（可能是不同团队同标题岗位）；同源同 id 只留一条
        seen = set()
        for job in best_items:
            if job["key"] in seen:
                dropped += 1
                continue
            seen.add(job["key"])
            kept.append(job)
        dropped += len(items) - len(best_items)
    return kept, dropped


def normalize_jobs(raw_jobs, cfg=None, roles_cfg=None, taxonomy=None, extract_fn=None):
    """批量归一化 + 目标角色/技能过滤 + 跨源去重。

    返回 (jobs, stats)。jobs 已按 key 排序，保证快照可复现。
    """
    cfg = cfg or {}
    filters = cfg.get("filters") or {}
    target_roles = set(filters.get("target_roles") or DEFAULT_TARGET_ROLES)
    min_skills = int(filters.get("min_skills") or 0)

    stats = {
        "fetched": len(raw_jobs or []),
        "normalized": 0,
        "target": 0,
        "skipped_role": 0,
        "skipped_excluded": 0,
        "skipped_no_skills": 0,
        "duplicates": 0,
        "with_required_skills": 0,
    }
    normalized = []
    for raw in raw_jobs or []:
        try:
            job = normalize_job(raw, cfg, roles_cfg, taxonomy, extract_fn)
        except Exception:  # noqa: BLE001 - 单条坏数据不拖垮整轮
            stats["skipped_role"] += 1
            continue
        stats["normalized"] += 1
        if job["role"] == "excluded":
            stats["skipped_excluded"] += 1
            continue
        if job["role"] not in target_roles:
            stats["skipped_role"] += 1
            continue
        if min_skills and len(job["skills"]) < min_skills:
            stats["skipped_no_skills"] += 1
            continue
        if job["required_skills"]:
            stats["with_required_skills"] += 1
        normalized.append(job)

    kept, dropped = dedupe_jobs(normalized)
    stats["duplicates"] = dropped
    stats["target"] = len(kept)
    kept.sort(key=lambda j: j["key"])
    return kept, stats


def summarize_sources(health):
    """源健康汇总，给报告/日志用。"""
    health = health or []
    ok = [h for h in health if h.get("ok")]
    fail = [h for h in health if not h.get("ok")]
    by_tier = {}
    for item in health:
        tier = item.get("tier") or "?"
        bucket = by_tier.setdefault(tier, {"ok": 0, "fail": 0, "jobs": 0})
        if item.get("ok"):
            bucket["ok"] += 1
            bucket["jobs"] += int(item.get("count") or 0)
        else:
            bucket["fail"] += 1
    return {
        "total": len(health),
        "ok": len(ok),
        "fail": len(fail),
        "by_tier": by_tier,
        "errors": [{"source_id": h.get("source_id"), "error": h.get("error")} for h in fail][:20],
    }
