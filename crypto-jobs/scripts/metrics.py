#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""metrics.py —— 岗位快照 → 技能/角色/公司/趋势指标（`skill-report/v1`）。

口径统一约定（报告里也要写）：
- 技能「提及岗位数」：同一岗位多次提到只算 1 次，分母是当前在招的目标岗位数；
- 周环比 = 当前快照 vs baseline 快照（默认 7 天前，找不到就用最早一份）；
- 「新增」用 jobs-index 的 first_seen 判定，首次建库不产生新增；
- 「关闭」需要该岗位所属源在当轮健康，避免源故障造成假关闭。
"""
import collections
import datetime

import snapshot

import common

SCHEMA = "skill-report/v1"
DEFAULT_TARGET_ROLES = (
    "devops", "sre", "mlops", "platform", "software", "backend", "frontend", "fullstack",
    "mobile", "blockchain", "trading", "data", "ml", "embedded",
)


def _pct(n, d):
    return round(100.0 * n / d, 1) if d else 0.0


def _delta(cur, base):
    return int(cur or 0) - int(base or 0)


def taxonomy_maps(taxonomy):
    """从 skills.yaml 结构生成 id→label / id→category / category→label 映射（容错缺字段）。"""
    labels, skill_category, category_labels = {}, {}, {}
    for cat in (taxonomy or {}).get("categories") or []:
        if isinstance(cat, dict) and cat.get("id"):
            category_labels[cat["id"]] = cat.get("label") or cat["id"]
    for skill in (taxonomy or {}).get("skills") or []:
        if not isinstance(skill, dict) or not skill.get("id"):
            continue
        sid = skill["id"]
        labels[sid] = skill.get("label") or sid
        skill_category[sid] = skill.get("category") or "other"
        category_labels.setdefault(skill_category[sid], skill_category[sid])
    return {
        "labels": labels,
        "skill_category": skill_category,
        "category_labels": category_labels,
    }


def skill_stats(jobs):
    counts, required = collections.Counter(), collections.Counter()
    for job in jobs or []:
        for sid in set(job.get("skills") or []):
            counts[sid] += 1
        for sid in set(job.get("required_skills") or []):
            required[sid] += 1
    return {"total_jobs": len(jobs or []), "counts": counts, "required": required}


def skill_rows(counts, base_counts, required, maps, min_count=1, limit=None):
    rows = []
    for sid, count in counts.items():
        if count < min_count:
            continue
        rows.append({
            "skill": sid,
            "label": maps["labels"].get(sid, sid),
            "category": maps["skill_category"].get(sid, "other"),
            "category_label": maps["category_labels"].get(
                maps["skill_category"].get(sid, "other"), "其他"),
            "count": int(count),
            "required": int(required.get(sid, 0)),
            "delta": _delta(count, base_counts.get(sid)),
            "base_count": int(base_counts.get(sid, 0)),
        })
    rows.sort(key=lambda r: (-r["count"], -r["delta"], r["skill"]))
    return rows[:limit] if limit else rows


def category_rows(counts, maps):
    agg = collections.Counter()
    for sid, count in counts.items():
        agg[maps["skill_category"].get(sid, "other")] += int(count)
    total = sum(agg.values()) or 1
    rows = [{
        "category": cat,
        "label": maps["category_labels"].get(cat, cat),
        "mentions": int(n),
        "share": _pct(n, total),
    } for cat, n in agg.items()]
    rows.sort(key=lambda r: (-r["mentions"], r["category"]))
    return rows


def attach_children(rows, counts, taxonomy, maps, jobs=None, limit=6, related_limit=4):
    """给技能行挂展示明细：宽口径技能挂子技能拆解，其余挂「常搭配」技能（共现）。

    只影响展示，不改变统计口径。
    """
    drilldown = (taxonomy or {}).get("drilldown") or {}
    skill_sets = [set(job.get("skills") or []) for job in (jobs or [])]
    for row in rows:
        sid = row.get("skill")
        child_ids = drilldown.get(sid) or []
        children = [{"skill": child, "label": maps["labels"].get(child, child),
                     "count": int(counts.get(child, 0))}
                    for child in child_ids if counts.get(child)]
        children.sort(key=lambda item: (-item["count"], item["skill"]))
        if children:
            row["children"] = children[:limit]
            continue
        if not jobs:
            continue
        related = collections.Counter()
        for skills_set in skill_sets:
            if sid in skills_set:
                related.update(other for other in skills_set if other != sid)
        related_rows = [{"skill": other, "label": maps["labels"].get(other, other),
                         "count": int(n)}
                        for other, n in related.items() if n >= 2 and n < counts.get(sid, 0)]
        related_rows.sort(key=lambda item: (-item["count"], item["skill"]))
        if related_rows:
            row["related"] = related_rows[:related_limit]
    return rows


def role_rows(jobs, maps, target_roles, taxonomy=None, top_n=8):
    order = list(target_roles or DEFAULT_TARGET_ROLES)
    out = []
    for role in order:
        subset = [j for j in jobs if j.get("role") == role]
        if not subset:
            continue
        counts = skill_stats(subset)["counts"]
        rows = skill_rows(counts, {}, {}, maps, min_count=1, limit=top_n)
        attach_children(rows, counts, taxonomy, maps, jobs=subset)
        out.append({"role": role, "jobs": len(subset), "top_skills": rows})
    # 报告按岗位量从多到少展示（同量按 roles 配置顺序，保证可复现）
    out.sort(key=lambda item: (-item["jobs"], order.index(item["role"])))
    return out


def company_rows(jobs, new_keys, maps, limit=None):
    """每个公司的招聘统计：岗位数/新增/远程/角色构成/Top 技能/代表岗位。

    limit=None 时返回全部公司（报告全量表 + 机器可读导出需要全量）。
    """
    buckets = {}
    for job in jobs:
        slug = job.get("company_slug") or job.get("company") or ""
        if not slug:
            continue
        bucket = buckets.setdefault(slug, {
            "company": job.get("company") or slug,
            "company_slug": slug,
            "jobs": 0,
            "new_jobs": 0,
            "remote": 0,
            "roles": collections.Counter(),
            "skills": collections.Counter(),
            "titles": [],
            "role_titles": {},
        })
        bucket["jobs"] += 1
        if job.get("key") in (new_keys or set()):
            bucket["new_jobs"] += 1
        if job.get("remote") == "remote":
            bucket["remote"] += 1
        bucket["roles"][job.get("role") or "other"] += 1
        for sid in set(job.get("skills") or []):
            bucket["skills"][sid] += 1
        title = job.get("title") or ""
        if title:
            if title not in bucket["titles"]:
                bucket["titles"].append(title)
            role_titles = bucket["role_titles"].setdefault(job.get("role") or "other", [])
            if title not in role_titles and len(role_titles) < 4:
                role_titles.append(title)

    rows = []
    for slug, bucket in buckets.items():
        total = bucket["jobs"]
        role_list = [{"role": role, "jobs": int(n)}
                     for role, n in bucket["roles"].most_common()]
        # 代表岗位：从岗位数最多的角色里各取 1 个 title，保证跨职能多样
        sample_titles = []
        for role_item in role_list:
            for title in bucket["role_titles"].get(role_item["role"], []):
                if title not in sample_titles:
                    sample_titles.append(title)
                    break
            if len(sample_titles) >= 3:
                break
        for title in bucket["titles"]:
            if len(sample_titles) >= 3:
                break
            if title not in sample_titles:
                sample_titles.append(title)
        rows.append({
            "company": bucket["company"],
            "company_slug": slug,
            "jobs": total,
            "new_jobs": bucket["new_jobs"],
            "remote": bucket["remote"],
            "remote_share": _pct(bucket["remote"], total),
            "roles": role_list,
            "top_skills": [{"skill": sid, "label": maps["labels"].get(sid, sid), "count": int(n)}
                           for sid, n in bucket["skills"].most_common(8)],
            "role_titles": {role: titles for role, titles in bucket["role_titles"].items()},
            "sample_titles": sample_titles[:3],
        })
    rows.sort(key=lambda r: (-r["jobs"], -r["new_jobs"], r["company"]))
    return rows[:limit] if limit else rows


def _seniority_rows(jobs):
    counts = collections.Counter(j.get("seniority") or "unknown" for j in jobs)
    return [{"seniority": k, "jobs": int(v)} for k, v in counts.most_common()]


def _skill_history(history_snapshots, top_ids, maps):
    """近 N 周 Top 技能的提及岗位数序列（缺周则空）。"""
    if not history_snapshots:
        return None
    dates = [str(day)[:10] for day, _snap in history_snapshots]
    series = []
    for sid in top_ids:
        counts = []
        for _day, snap in history_snapshots:
            counts.append(sum(1 for job in snap.get("jobs") or [] if sid in (job.get("skills") or [])))
        series.append({"skill": sid, "label": maps["labels"].get(sid, sid), "counts": counts})
    return {"dates": dates, "series": series}




# ---------------------------------------------------------------- 交易所 / 招聘节奏

DIRECTION_PATTERNS = [
    ("交易/撮合", ["trading", "matching engine", "exchange os", "market making", "liquidity",
                   "quant", "market data", "low latency", "trading engine"]),
    ("移动端", ["mobile", "ios", "android", "flutter", "react native"]),
    ("AI/ML", ["machine learning", "ml engineer", "ai engineer", "ai agent", "llm",
               "ai application", "ai native", "model serving", "applied ai", "ai compute"]),
    ("钱包/托管/安全", ["wallet", "custody", "hsm", "mpc", "key management", "security",
                        "compliance", "kyc", "aml", "audit"]),
    ("支付/法币", ["payment", "fiat", "card", "banking", "money", "treasury", "remittance"]),
    ("数据", ["data engineer", "data platform", "analytics", "warehouse", "data pipeline",
              "data foundations"]),
    ("链上/协议", ["blockchain", "web3", "onchain", "on-chain", "solidity", "evm", "solana",
                   "defi", "protocol", "dex"]),
    ("平台/基础设施", ["platform", "infrastructure", "middleware", "kubernetes", "cloud",
                       "compute", "sre", "reliability", "devops", "observability", "storage", "network"]),
    ("前端/全栈", ["frontend", "front end", "full stack", "fullstack"]),
    ("后端/通用", ["backend", "back-end", "software engineer", "software developer", "developer"]),
]
_CONTRACT_HINTS = ("6 month contract", "12 month contract", "1 year contract", "6-month contract",
                   "12-month contract", "fixed term", "temporary contract", "contract role")


def _parse_day(value):
    try:
        return datetime.date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def job_direction(job):
    """按 title 关键词把岗位归到业务方向（用于看交易所在做什么）。"""
    title = (job.get("title") or "").lower()
    import re
    for name, keywords in DIRECTION_PATTERNS:
        for kw in keywords:
            if re.search(r"(?<![a-z0-9])" + re.escape(kw) + r"(?![a-z0-9])", title):
                return name
    return "其他"


def is_contract_job(job):
    """短期/合同岗判断（排除 smart contract 这种技能词）。"""
    employment = (job.get("employment") or "").lower()
    if any(k in employment for k in ("contract", "temporary", "fixed term")):
        return True
    text = ((job.get("title") or "") + " " + (job.get("description_text") or "")[:800]).lower()
    text = text.replace("smart contract", "smart_contract")
    return any(hint in text for hint in _CONTRACT_HINTS)


def _job_age(job, today):
    posted = _parse_day(job.get("posted_at"))
    if not posted:
        return None
    return (today - posted).days


def _rhythm_tags(profile):
    """招聘节奏标签：长期大量 / 近期放量 / 短期急招 / 慢性难招 / 常规补招。"""
    total = profile.get("jobs") or 0
    recent = profile.get("recent14") or 0
    evergreen = profile.get("evergreen") or 0
    tags = []
    if profile.get("contract_jobs"):
        tags.append("短期急招/合同补位")
    if total >= 10 and recent / total >= 0.15:
        tags.append("近期放量")
    if total >= 15 and evergreen / total >= 0.5:
        tags.append("长期大量招聘")
    if total < 15 and evergreen / total >= 0.5:
        tags.append("慢性难招/常青岗")
    if not tags:
        tags.append("常规补招")
    return tags


def _exchange_profile(slug, name, items, today, maps):
    n = len(items)
    remote = sum(1 for job in items if job.get("remote") == "remote")
    senior = collections.Counter(job.get("seniority") for job in items)
    roles = collections.Counter(job.get("role") for job in items)
    directions = collections.Counter(job_direction(job) for job in items)
    skills = collections.Counter()
    for job in items:
        for sid in set(job.get("skills") or []):
            skills[sid] += 1
    ages = [ _job_age(job, today) for job in items ]
    known = [age for age in ages if age is not None]
    recent14 = sum(1 for age in known if age <= 14)
    evergreen = sum(1 for age in known if age > 60)
    contracts = [job for job in items if is_contract_job(job)]
    locations = {loc for job in items for loc in (job.get("locations") or [])}
    profile = {
        "slug": slug,
        "name": name,
        "jobs": n,
        "remote": remote,
        "remote_share": _pct(remote, n),
        "staff_principal": senior.get("staff", 0) + senior.get("principal", 0),
        "staff_principal_share": _pct(senior.get("staff", 0) + senior.get("principal", 0), n),
        "locations": len(locations),
        "roles": [{"role": role, "jobs": int(count)} for role, count in roles.most_common()],
        "directions": [{"name": dname, "jobs": int(count)}
                       for dname, count in directions.most_common()],
        "top_skills": [{"skill": sid, "label": maps["labels"].get(sid, sid),
                        "count": int(count)} for sid, count in skills.most_common(8)],
        "recent14": recent14,
        "mid": sum(1 for age in known if 14 < age <= 60),
        "evergreen": evergreen,
        "unknown_age": sum(1 for age in ages if age is None),
        "contract_jobs": len(contracts),
        "contract_titles": [job.get("title") or "" for job in contracts][:3],
        "sample_titles": [],
    }
    seen = set()
    for job in items:
        title = job.get("title") or ""
        if title and title not in seen:
            seen.add(title)
            profile["sample_titles"].append(title)
        if len(profile["sample_titles"]) >= 3:
            break
    profile["tags"] = _rhythm_tags(profile)
    return profile


def _group_comparison(profiles, maps):
    total = sum(profile["jobs"] for profile in profiles)
    remote = sum(profile["remote"] for profile in profiles)
    senior = sum(profile["staff_principal"] for profile in profiles)
    skills = collections.Counter()
    roles = collections.Counter()
    recent = sum(profile["recent14"] for profile in profiles)
    evergreen = sum(profile["evergreen"] for profile in profiles)
    contracts = sum(profile["contract_jobs"] for profile in profiles)
    for profile in profiles:
        for row in profile["top_skills"]:
            skills[row["skill"]] += row["count"]
        for row in profile["roles"]:
            roles[row["role"]] += row["jobs"]
    return {
        "companies": len(profiles),
        "jobs": total,
        "remote_share": _pct(remote, total),
        "staff_principal_share": _pct(senior, total),
        "recent14": recent,
        "evergreen": evergreen,
        "contract_jobs": contracts,
        "top_roles": [{"role": role, "jobs": int(count)} for role, count in roles.most_common(5)],
        "top_skills": [{"skill": sid, "label": maps["labels"].get(sid, sid),
                        "count": int(count)} for sid, count in skills.most_common(8)],
    }


def exchange_analysis(jobs, exchanges, maps, date=None):
    """CEX / DEX 招聘画像 + 招聘节奏 + 两组对比。"""
    exchanges = exchanges or {}
    today = _parse_day(date or common.today_str()) or datetime.date.today()
    by_slug = collections.defaultdict(list)
    for job in jobs or []:
        by_slug[job.get("company_slug") or ""].append(job)

    result = {"cex": [], "dex": [], "coverage": {}}
    for group in ("cex", "dex"):
        rows = []
        for entry in exchanges.get(group) or []:
            slug = entry.get("slug") or ""
            items = by_slug.get(slug) or []
            if not items:
                continue
            rows.append(_exchange_profile(slug, entry.get("name") or slug, items, today, maps))
        rows.sort(key=lambda row: -row["jobs"])
        result[group] = rows
        result["coverage"][group] = {
            "configured": len(exchanges.get(group) or []),
            "matched": len(rows),
        }
    result["comparison"] = {
        "cex": _group_comparison(result["cex"], maps),
        "dex": _group_comparison(result["dex"], maps),
    }
    return result


def build_report_data(current_snapshot, baseline_snapshot=None, index=None, cfg=None,
                      taxonomy=None, date=None, period_days=7, history_snapshots=None,
                      exchanges=None):
    """组装周报/日报共用的 `skill-report/v1` 数据。纯函数（不读盘、不联网）。"""
    cfg = cfg or {}
    report_cfg = cfg.get("report") or {}
    target_roles = (cfg.get("filters") or {}).get("target_roles") or list(DEFAULT_TARGET_ROLES)
    min_count = int(report_cfg.get("min_skill_count") or 1)
    top_n = int(report_cfg.get("top_skills") or 25)
    rising_delta = int(report_cfg.get("rising_delta") or 3)
    maps = taxonomy_maps(taxonomy)

    cur_jobs = list((current_snapshot or {}).get("jobs") or [])
    base_jobs = list((baseline_snapshot or {}).get("jobs") or [])
    snapshot_date = str(date or (current_snapshot or {}).get("date") or common.today_str())[:10]
    end = datetime.datetime.strptime(snapshot_date, "%Y-%m-%d").date()
    baseline_date = str((baseline_snapshot or {}).get("date") or "")[:10]
    if baseline_date:
        try:
            period_days = max(1, (end - datetime.datetime.strptime(baseline_date, "%Y-%m-%d").date()).days)
        except ValueError:
            baseline_date = ""
    start = (end - datetime.timedelta(days=period_days)).isoformat()

    has_baseline = baseline_snapshot is not None and bool((baseline_snapshot or {}).get("date"))
    cur_stats = skill_stats(cur_jobs)
    base_stats = skill_stats(base_jobs)
    counts, required = cur_stats["counts"], cur_stats["required"]
    rows = skill_rows(counts, base_stats["counts"], required, maps, min_count, None)
    attach_children(rows, counts, taxonomy, maps, jobs=cur_jobs)
    if not has_baseline:
        # 首次建基线：没有可比对象，环比置空（报告显示「—」），避免把存量当增量
        for row in rows:
            row["delta"] = None
            row["base_count"] = 0

    total = len(cur_jobs)
    remote = sum(1 for j in cur_jobs if j.get("remote") == "remote")

    # 新增：优先 jobs-index.first_seen（窗口内），无索引时退化为与 baseline 的集合差
    new_keys = set()
    if baseline_snapshot is None:
        # 首次建基线：不产生"新增"（否则首周会把全部在招岗位当成新增）
        new_keys = set()
    elif index and index.get("jobs"):
        new_keys = snapshot.index_new_jobs(index, snapshot_date, days=period_days)
        new_keys &= {j.get("key") for j in cur_jobs}
    else:
        new_keys = {j.get("key") for j in cur_jobs} - {j.get("key") for j in base_jobs}
    diff = snapshot.diff_jobs(base_jobs, cur_jobs, (current_snapshot or {}).get("source_health"))
    closed_keys = set(diff["closed_keys"]) if baseline_snapshot is not None else set()
    new_jobs = [j for j in cur_jobs if j.get("key") in new_keys]

    companies = {j.get("company_slug") or j.get("company") for j in cur_jobs}
    companies.discard("")
    health = (current_snapshot or {}).get("source_health") or []
    health_ok = [h for h in health if h.get("ok")]
    health_fail = [h for h in health if not h.get("ok")]

    top_rows = rows[:top_n]
    rising, falling, new_skills = [], [], []
    if has_baseline:
        rising = [r for r in rows if r["delta"] >= rising_delta and r["base_count"] >= min_count]
        rising.sort(key=lambda r: (-r["delta"], -r["count"]))
        falling = [r for r in rows if r["delta"] <= -rising_delta and r["base_count"] >= min_count]
        falling.sort(key=lambda r: (r["delta"], -r["base_count"]))
        new_skills = [r for r in rows if r["base_count"] == 0 and r["count"] >= max(2, min_count)]

    notable = sorted(new_jobs, key=lambda j: (
        {"T0": 0, "T1": 1, "T2": 2}.get(j.get("source_tier"), 9),
        j.get("company_slug") or "",
        j.get("title") or "",
    ))[: int(report_cfg.get("max_notable_jobs") or 10)]

    data = {
        "schema": SCHEMA,
        "module": "crypto-jobs",
        "generated_at": common.ts_iso(),
        "date": snapshot_date,
        "period": {"start": start, "end": snapshot_date, "days": period_days,
                   "baseline_date": baseline_date, "first_baseline": not baseline_date},
        "overview": {
            "jobs": total,
            "companies": len(companies),
            "sources_ok": len(health_ok),
            "sources_total": len(health),
            "new": len(new_keys),
            "closed": len(closed_keys),
            "changed": len(diff["changed"]),
            "remote": remote,
            "remote_share": _pct(remote, total),
            "skills_observed": len(counts),
            "seniority": _seniority_rows(cur_jobs),
            "first_baseline": baseline_snapshot is None,
        },
        "top_skills": top_rows,
        "rising": rising[:10],
        "falling": falling[:10],
        "new_skills": new_skills[:10],
        "by_role": role_rows(cur_jobs, maps, target_roles, taxonomy),
        "by_category": category_rows(counts, maps),
        "companies": company_rows(
            cur_jobs, new_keys, maps,
            limit=int(report_cfg.get("company_stats_limit") or 200)),
        "exchange_dynamics": exchange_analysis(cur_jobs, exchanges, maps, snapshot_date),
        "company_types": dict((exchanges or {}).get("company_types") or {}),
        "notable_new_jobs": notable,
        "skill_history": _skill_history(
            history_snapshots, [r["skill"] for r in top_rows[:8]], maps),
        "source_health": health,
        "taxonomy_size": len((taxonomy or {}).get("skills") or []),
        "skill_counts": dict(counts),
        "skill_required": dict(required),
        "skill_labels": {sid: maps["labels"].get(sid, sid) for sid in counts},
        "notes": {
            "method": "规则词典抽取（config/skills.yaml），非人工整理",
            "source_errors": [{"source_id": h.get("source_id"), "error": h.get("error")}
                              for h in health_fail][:10],
        },
    }
    return data
