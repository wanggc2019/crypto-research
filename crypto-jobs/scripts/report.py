#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""report.py —— 把 `skill-report/v1` 数据渲染成 Markdown 周报/日报 + Telegram 摘要。

纯渲染，不读盘、不联网；输入输出都可离线测试。
"""
import html

ROLE_LABELS = {
    "devops": "DevOps / 交付",
    "sre": "SRE / 可靠性",
    "mlops": "MLOps / AI 基础设施",
    "platform": "平台 / 基础设施",
    "software": "软件开发（通用）",
    "backend": "后端开发",
    "frontend": "前端开发",
    "fullstack": "全栈开发",
    "mobile": "移动端开发",
    "blockchain": "区块链 / 智能合约",
    "trading": "交易系统开发",
    "data": "数据工程",
    "ml": "AI / ML 工程",
    "embedded": "嵌入式",
    "security": "安全",
}

# 公司统计/摘要里用的「明确职能」短标签：区分开发/运维，避免「移动端」「平台」这种歧义
ROLE_SHORT = {
    "devops": "DevOps/交付",
    "sre": "SRE/可靠性",
    "mlops": "MLOps/AI 基础设施",
    "platform": "平台/基础设施",
    "software": "软件开发（通用）",
    "backend": "后端开发",
    "frontend": "前端开发",
    "fullstack": "全栈开发",
    "mobile": "移动端开发",
    "blockchain": "区块链开发",
    "trading": "交易系统开发",
    "data": "数据工程",
    "ml": "AI/ML 工程",
    "embedded": "嵌入式开发",
    "security": "安全",
}


def _pct(value):
    return "%.1f%%" % float(value or 0)


def _delta(n):
    if n is None:
        return "—"
    n = int(n or 0)
    if n > 0:
        return "+%d" % n
    return str(n)


def _cell(text, limit=60):
    text = str(text or "").replace("|", "／").replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _table(headers, rows):
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in rows:
        out.append("| " + " | ".join(str(x) for x in row) + " |")
    return "\n".join(out)


def _skill_line(row):
    return "**%s** %d（%s）" % (row["label"], row["count"], _delta(row.get("delta")))


def render_weekly(data, cfg=None):
    cfg = cfg or {}
    ov = data.get("overview") or {}
    period = data.get("period") or {}
    title = "加密行业技术岗位周报（DevOps / SRE / MLOps / 开发）· %s" % data.get("date")
    lines = [
        "# %s" % title,
        "",
        "> 统计窗口：%s ~ %s（%s 天）｜在招岗位 **%d**｜覆盖 **%d** 家公司｜数据源健康 %d/%d" % (
            period.get("start"), period.get("end"), period.get("days"),
            ov.get("jobs", 0), ov.get("companies", 0),
            ov.get("sources_ok", 0), ov.get("sources_total", 0)),
        "> 自动生成：`crypto-jobs`（规则抽取，样本见文末方法说明）",
        "",
        "## 1. 市场概览",
        "",
        "- 在招目标岗位：**%d**（DevOps / SRE / MLOps / 平台 + 开发岗位）" % ov.get("jobs", 0),
        "- 本周新增：**%d**；关闭：**%d**；JD 有变更：**%d**" % (
            ov.get("new", 0), ov.get("closed", 0), ov.get("changed", 0)),
        "- 远程岗位占比：**%s**（%d 个）；资历分布：%s" % (
            _pct(ov.get("remote_share")), ov.get("remote", 0),
            "、".join("%s %d" % (s.get("seniority"), s.get("jobs"))
                     for s in (ov.get("seniority") or [])[:5]) or "—"),
        "- 观测到技能：**%d** 个（词典规模 %s 个）" % (
            ov.get("skills_observed", 0), data.get("taxonomy_size", "?")),
    ]
    if period.get("first_baseline"):
        lines.append("- ⚠️ 首次建立基线：本次不计算「新增」，下周开始有环比。")
    lines += ["", "## 2. 技能需求 Top %d" % len(data.get("top_skills") or []), ""]
    rows = [[i + 1, _cell(r["label"]), _cell(r.get("category_label"), 20), r["count"],
             _pct(100.0 * r["count"] / (ov.get("jobs") or 1)), r.get("required", 0), _delta(r.get("delta"))]
            for i, r in enumerate(data.get("top_skills") or [])]
    if rows:
        lines.append(_table(["#", "技能", "类别", "提及岗位", "占比", "JD 硬性要求", "周环比"], rows))
    else:
        lines.append("_本周没有达到阈值的技能。_")
    broad = [r for r in (data.get("top_skills") or [])
             if r.get("children") or r.get("related")][:10]
    if broad:
        lines += ["", "### 技能拆解与常见搭配（提及岗位数）", ""]
        for row in broad:
            lines.append("- **%s**（%d）%s" % (
                _cell(row.get("label"), 24), row.get("count", 0), _children_text(row, 6)))

    lines += ["", "## 3. 趋势：升温 / 降温 / 新出现", ""]
    rising = data.get("rising") or []
    falling = data.get("falling") or []
    new_skills = data.get("new_skills") or []
    if period.get("first_baseline"):
        lines.append("- 首次建立基线：本周先看技能存量分布，下周开始有升温/降温对比。")
        rising, falling, new_skills = [], [], []
    lines.append("- **升温**：" + ("、".join(_skill_line(r) for r in rising[:8]) if rising else "无"))
    lines.append("- **降温**：" + ("、".join("**%s** %d（%s）" % (r["label"], r["count"], _delta(r.get("delta")))
                                    for r in falling[:8]) if falling else "无"))
    lines.append("- **新出现**：" + ("、".join("**%s** %d" % (r["label"], r["count"])
                                    for r in new_skills[:8]) if new_skills else "无"))

    lines += ["", "## 4. 分角色技能需求", ""]
    for role in data.get("by_role") or []:
        top = role.get("top_skills") or []
        lines.append("### %s（%d 个岗位）" % (role.get("label") or ROLE_LABELS.get(role.get("role"), role.get("role")),
                                          role.get("jobs", 0)))
        lines.append("")
        if top:
            lines.append("、".join("%s %d" % (_cell(r["label"], 30), r["count"]) for r in top))
        else:
            lines.append("_样本不足_")
        lines.append("")

    lines += ["## 5. 公司招聘统计", ""]
    companies = data.get("companies") or []
    type_map = _exchange_type_map(data)
    if companies:
        rows = []
        for company in companies:
            roles = "、".join(
                "%s %d" % (ROLE_SHORT.get(r.get("role"), r.get("role") or "—"), r.get("jobs", 0))
                for r in (company.get("roles") or [])[:3]) or "—"
            skills = "、".join(
                "%s %d" % (_cell(s.get("label"), 18), s.get("count", 0))
                for s in (company.get("top_skills") or [])[:4]) or "—"
            rows.append([_cell(company.get("company"), 30),
                         type_map.get(company.get("company_slug"), "—"),
                         company.get("jobs", 0),
                         company.get("new_jobs", 0), _pct(company.get("remote_share")),
                         roles, skills])
        lines.append(_table(["公司", "类型", "在招", "本周新增", "远程占比", "角色构成（Top3）", "技能提及（Top4）"], rows))
    else:
        lines.append("_无数据_")

    profiles = companies[: int((cfg or {}).get("report", {}).get("company_profiles") or 10)]
    if profiles:
        lines += ["", "## 6. 公司画像（招聘量 Top %d）" % len(profiles), ""]
        for company in profiles:
            lines.append("### %s%s（%d 个岗位｜新增 %d｜远程 %s）" % (
                _cell(company.get("company"), 40),
                _company_type_suffix(company, type_map),
                company.get("jobs", 0),
                company.get("new_jobs", 0), _pct(company.get("remote_share"))))
            lines.append("")
            roles = "、".join(
                "%s %d" % (ROLE_LABELS.get(r.get("role"), r.get("role") or "—"), r.get("jobs", 0))
                for r in (company.get("roles") or [])[:6]) or "—"
            skills = "、".join(
                "%s %d" % (_cell(s.get("label"), 24), s.get("count", 0))
                for s in (company.get("top_skills") or [])[:8]) or "—"
            lines.append("- **角色**：%s" % roles)
            lines.append("- **技能**：%s" % skills)
            titles = [t for t in (company.get("sample_titles") or []) if t]
            if titles:
                lines.append("- **代表岗位**：%s" % "；".join(_cell(t, 64) for t in titles[:3]))
            lines.append("")

    exchange = data.get("exchange_dynamics") or {}
    cex_rows = exchange.get("cex") or []
    dex_rows = exchange.get("dex") or []
    if cex_rows or dex_rows:
        coverage = exchange.get("coverage") or {}

        def _exchange_table(rows):
            table = []
            for row in rows:
                roles = "、".join(
                    "%s %d" % (ROLE_SHORT.get(r.get("role"), r.get("role") or "—"), r.get("jobs", 0))
                    for r in (row.get("roles") or [])[:2]) or "—"
                skills = "、".join(
                    "%s %d" % (_cell(s.get("label"), 16), s.get("count", 0))
                    for s in (row.get("top_skills") or [])[:3]) or "—"
                table.append([_cell(row.get("name"), 20), row.get("jobs", 0),
                              _pct(row.get("remote_share")), _pct(row.get("staff_principal_share")),
                              row.get("recent14", 0), row.get("evergreen", 0), roles, skills,
                              "、".join(row.get("tags") or [])])
            return _table(["交易所", "岗位", "远程", "高级岗", "近14天", ">60天",
                           "角色Top2", "技能Top3", "招聘节奏"], table)

        cex_jobs = sum(r.get("jobs", 0) for r in cex_rows)
        dex_jobs = sum(r.get("jobs", 0) for r in dex_rows)
        cex_cov = coverage.get("cex") or {}
        dex_cov = coverage.get("dex") or {}
        lines += ["", "## 7. 交易所动态（CEX / DEX）", ""]
        lines.append("### CEX 中心化交易所（%d 家 / %d 个岗位）" % (len(cex_rows), cex_jobs))
        lines.append("")
        lines.append(_exchange_table(cex_rows) if cex_rows else "_无 CEX 样本_")
        lines.append("")
        lines.append("### DEX 去中心化交易所（%d 家 / %d 个岗位）" % (len(dex_rows), dex_jobs))
        lines.append("")
        if dex_rows:
            lines.append(_exchange_table(dex_rows))
        else:
            lines.append("_本轮样本中没有 DEX 公司岗位。_")
        lines.append("")
        lines.append("> 覆盖：CEX 配置 %d 家 / 命中 %d 家；DEX 配置 %d 家 / 命中 %d 家；"
                     "未命中的公司暂无公开 ATS 或目标岗位。" % (
                         cex_cov.get("configured", 0), cex_cov.get("matched", 0),
                         dex_cov.get("configured", 0), dex_cov.get("matched", 0)))

        comp = exchange.get("comparison") or {}
        cex = comp.get("cex") or {}
        dex = comp.get("dex") or {}

        def _roles(group):
            return "、".join("%s %d" % (ROLE_SHORT.get(r.get("role"), r.get("role") or "—"), r.get("jobs", 0))
                             for r in (group.get("top_roles") or [])[:4]) or "—"

        def _skills(group):
            return "、".join("%s %d" % (_cell(s.get("label"), 16), s.get("count", 0))
                             for s in (group.get("top_skills") or [])[:5]) or "—"

        lines += ["", "### CEX vs DEX 对比", ""]
        lines.append(_table(["指标", "CEX", "DEX"], [
            ["岗位数", cex.get("jobs", 0), dex.get("jobs", 0)],
            ["公司数", cex.get("companies", 0), dex.get("companies", 0)],
            ["远程占比", _pct(cex.get("remote_share")), _pct(dex.get("remote_share"))],
            ["高级岗（staff+principal）占比", _pct(cex.get("staff_principal_share")),
             _pct(dex.get("staff_principal_share"))],
            ["近 14 天新岗", cex.get("recent14", 0), dex.get("recent14", 0)],
            ["超 60 天老岗", cex.get("evergreen", 0), dex.get("evergreen", 0)],
            ["合同/急招岗", cex.get("contract_jobs", 0), dex.get("contract_jobs", 0)],
            ["主力角色", _roles(cex), _roles(dex)],
            ["Top 技能", _skills(cex), _skills(dex)],
        ]))
        lines.append("")
        if dex_rows and dex.get("jobs", 0) < 10:
            lines.append("> ⚠️ DEX 样本仅 %d 个岗位，对比仅供参考；后续接入 dYdX / Curve / Balancer 等源后再做趋势结论。"
                         % dex.get("jobs", 0))
        lines.append("")

    lines += ["", "## 8. 本周值得看的新岗位", ""]
    notable = data.get("notable_new_jobs") or []
    if notable:
        for job in notable:
            remote = "｜远程" if job.get("remote") == "remote" else ""
            lines.append("- **%s**｜%s（%s%s）%s" % (
                _cell(job.get("company"), 30), _cell(job.get("title"), 70),
                ROLE_LABELS.get(job.get("role"), job.get("role") or "—"),
                remote, "｜[链接](%s)" % job.get("url") if job.get("url") else ""))
    else:
        lines.append("_本周无新增岗位。_")

    history = data.get("skill_history")
    show_history = bool(history and history.get("series") and len(history.get("dates") or []) >= 2)
    if show_history:
        lines += ["", "## 9. 近 %d 周技能趋势（提及岗位数）" % len(history.get("dates") or []), ""]
        dates = history["dates"]
        rows = []
        for item in history["series"]:
            rows.append([_cell(item["label"], 24)] + list(item.get("counts") or []))
        lines.append(_table(["技能"] + [d[5:] for d in dates], rows))

    lines += [
        "",
        "## %d. 方法与局限" % (10 if show_history else 9),
        "",
        "- **样本**：只覆盖本模块配置的公开招聘源（`config/sources.yaml`，当前 %d 个源健康），"
        "不是全市场普查；结论用于看相对变化，不宜当绝对存量。" % ov.get("sources_ok", 0),
        "- **岗位口径**：DevOps / SRE / MLOps / 平台 + 开发岗位（后端/前端/全栈/移动/区块链/"
        "数据/ML/嵌入式/交易系统/通用软件）；安全/测试默认不计入，可在 `config/roles.yaml` 调整。",
        "- **公司口径**：按 `company_slug` 汇总（同公司跨源已去重）；「角色构成」取该司岗位角色 Top3，"
        "「技能提及」取 Top4；完整机器可读数据见 `data/exports/company-stats.json`。",
        "- **提示**：钱包/dApp/托管等词在部分 JD 里来自公司/产品介绍而非技能要求，"
        "请以「JD 硬性要求」列交叉验证；公司口径同理。",
        "- **交易所口径**：CEX / DEX 名单见 `config/exchanges.yaml`；招聘节奏用 `posted_at`（≤14 天=近期、"
        ">60 天=老岗）与合同/临时岗识别，单日快照只是代理指标，2–4 周后可结合 first_seen/last_seen 硬化。",
        "- **技能口径**：`config/skills.yaml` 中的 %s 个技能，按 JD 正文规则匹配；"
        "「提及岗位数」= 提到该技能的岗位数（同岗位多次只算 1），分母是在招目标岗位数。"
        % data.get("taxonomy_size", "?"),
        "- **周环比**：对比 %s 的快照；「新增」按首次发现日期计算，首次建库不产生新增。"
        % (period.get("baseline_date") or "上一份"),
        "- **关闭判定**：仅当岗位所属源当轮抓取健康时才计入，避免把源故障当成岗位下线。",
        "- **合规**：只使用公司官方 ATS 公开接口 / 招聘站公开页面（遵守 robots.txt 与 ToS），"
        "不抓 LinkedIn / Indeed / Glassdoor。",
        "- **复现**：`cd crypto-jobs && ./scripts/run.sh --weekly`；数据在 `data/`（不入库）。",
        "",
    ]
    if data.get("notes", {}).get("source_errors"):
        lines += ["<details><summary>本轮失败源（不影响其余数据）</summary>", ""]
        for err in data["notes"]["source_errors"]:
            lines.append("- `%s`：%s" % (err.get("source_id"), _cell(err.get("error"), 120)))
        lines += ["", "</details>", ""]
    return "\n".join(lines)


def render_daily(data, cfg=None):
    """简版日报（默认只落本地，不推送）。"""
    ov = data.get("overview") or {}
    lines = [
        "# 技术岗位日报 · %s" % data.get("date"),
        "",
        "> 在招 **%d** 个岗位｜新增 **%d**｜关闭 **%d**｜远程 %s" % (
            ov.get("jobs", 0), ov.get("new", 0), ov.get("closed", 0), _pct(ov.get("remote_share"))),
        "",
        "## 今日技能提及 Top 10",
        "",
    ]
    rows = [[i + 1, _cell(r["label"]), r["count"], r.get("required", 0), _delta(r.get("delta"))]
            for i, r in enumerate((data.get("top_skills") or [])[:10])]
    lines.append(_table(["#", "技能", "提及岗位", "硬性要求", "较上轮"], rows) if rows else "_无数据_")
    broad = [r for r in (data.get("top_skills") or [])
             if r.get("children") or r.get("related")][:8]
    if broad:
        lines += ["", "### 技能拆解与常见搭配", ""]
        for row in broad:
            lines.append("- **%s**（%d）%s" % (
                _cell(row.get("label"), 24), row.get("count", 0), _children_text(row, 4)))
    companies = data.get("companies") or []
    if companies:
        lines += ["", "## 公司招聘 Top 10", ""]
        type_map = _exchange_type_map(data)
        company_table = []
        for company in companies[:10]:
            roles = "、".join(
                "%s %d" % (ROLE_SHORT.get(r.get("role"), r.get("role") or "—"), r.get("jobs", 0))
                for r in (company.get("roles") or [])[:2]) or "—"
            company_table.append([_cell(company.get("company"), 30),
                                  type_map.get(company.get("company_slug"), "—"),
                                  company.get("jobs", 0),
                                  company.get("new_jobs", 0), _pct(company.get("remote_share")), roles])
        lines.append(_table(["公司", "类型", "在招", "新增", "远程", "主要角色"], company_table))
    new_jobs = data.get("notable_new_jobs") or []
    if new_jobs:
        lines += ["", "## 新增岗位", ""]
        for job in new_jobs:
            lines.append("- %s｜%s" % (_cell(job.get("company"), 30), _cell(job.get("title"), 70)))
    lines += ["", "> 完整周报见 `data/reports/<date>-weekly.md`。", ""]
    return "\n".join(lines)


def _children_text(row, limit=3):
    """技能行的展示明细：宽口径用子技能拆解，其余用「常搭配」技能（共现）。"""
    children = row.get("children") or []
    if children:
        return "｜" + "、".join("%s（%d）" % (c.get("label"), c.get("count", 0))
                               for c in children[:limit])
    related = row.get("related") or []
    if related:
        return "｜常搭配：" + "、".join("%s（%d）" % (c.get("label"), c.get("count", 0))
                                      for c in related[:limit])
    return ""


def _role_jobs(data, role):
    for row in data.get("by_role") or []:
        if row.get("role") == role:
            return int(row.get("jobs") or 0)
    return 0


def _skill_count(data, sid):
    return int((data.get("skill_counts") or {}).get(sid) or 0)


def _req_ratio(data, sid):
    count = _skill_count(data, sid)
    required = int((data.get("skill_required") or {}).get(sid) or 0)
    return round(100.0 * required / count) if count else 0


def _pct1(n, d):
    return round(100.0 * n / d, 1) if d else 0.0


def render_core_analysis(data, max_chars=2200):
    """周报消息里的「核心结论」：3-5 段话，概括市场/交易所/技能/结论。"""
    ov = data.get("overview") or {}
    total = int(ov.get("jobs") or 0)
    if not total:
        return []
    dynamics = data.get("exchange_dynamics") or {}
    comparison = dynamics.get("comparison") or {}
    cex = comparison.get("cex") or {}
    dex = comparison.get("dex") or {}
    cex_rows = dynamics.get("cex") or []
    senior_jobs = sum(int(item.get("jobs") or 0) for item in ov.get("seniority") or []
                      if item.get("seniority") in ("senior", "staff", "principal", "lead"))
    dev_roles = ("software", "backend", "frontend", "fullstack", "mobile",
                 "blockchain", "data", "ml", "trading", "embedded")
    dev_jobs = sum(_role_jobs(data, role) for role in dev_roles)
    cex_share = _pct1(int(cex.get("jobs") or 0), total)
    remote_share = ov.get("remote_share") or 0
    senior_share = _pct1(senior_jobs, total)
    dev_share = _pct1(dev_jobs, total)

    top_cex = "、".join("%s %d" % (row.get("name"), row.get("jobs", 0)) for row in cex_rows[:5])
    tag_groups = {}
    for row in cex_rows:
        for tag in row.get("tags") or []:
            tag_groups.setdefault(tag, []).append(row.get("name"))
    rhythm = "；".join("%s：%s" % (tag, "/".join(names[:4])) for tag, names in tag_groups.items())

    ai = _skill_count(data, "ai")
    llm = _skill_count(data, "llm")
    ml = _skill_count(data, "machine-learning")
    mlops_jobs = _role_jobs(data, "mlops")
    vllm = _skill_count(data, "vllm")
    mlflow = _skill_count(data, "mlflow")
    go_req, rust_req = _req_ratio(data, "go"), _req_ratio(data, "rust")
    ts_req, py_req = _req_ratio(data, "typescript"), _req_ratio(data, "python")
    hsm_req = _req_ratio(data, "hsm")
    mpc_req = _req_ratio(data, "mpc")
    validator_req = max(_req_ratio(data, "validator"), _req_ratio(data, "node-operations"))
    evm_req = _req_ratio(data, "evm")
    solana_req = _req_ratio(data, "solana")
    low_latency_req = _req_ratio(data, "low-latency")

    paragraphs = [
        "<b>核心结论</b>",
        "① 市场结构：在招 %d 个岗位 / %d 家公司；CEX %d 家占 %.1f%%（%d 岗），DEX %d 家 %d 岗；"
        "远程 %.1f%%，senior+ 占 %.1f%%，开发类岗位约 %.1f%%。" % (
            total, ov.get("companies", 0), len(cex_rows), cex_share,
            int(cex.get("jobs") or 0), len(dynamics.get("dex") or []),
            int(dex.get("jobs") or 0), remote_share, senior_share, dev_share),
        "② 交易所动态：头部为 %s；节奏看 %s。方向集中在交易/移动/平台/AI，"
        "Coinbase 远程优先、亚洲交易所 onsite 为主。" % (top_cex or "—", rhythm or "—"),
        "③ 技能需求：AI 提及 %d、LLM %d、机器学习 %d，但 MLOps 专职岗仅 %d 个（vLLM %d、MLflow %d），"
        "AI 更偏平台与应用；语言侧 Go 硬性率 %d%%、Rust %d%%、TypeScript %d%%、Python %d%%；"
        "加密专有硬性需求集中在 HSM %d%%、MPC %d%%、validator/节点 %d%%、EVM %d%%、Solana %d%%、低延迟 %d%%。" % (
            ai, llm, ml, mlops_jobs, vllm, mlflow, go_req, rust_req, ts_req, py_req,
            hsm_req, mpc_req, validator_req, evm_req, solana_req, low_latency_req),
        "④ 结论：交易所是主力（占 %.0f%% 岗位），通用底座（Go/Python/Java + K8s/AWS/可观测性）"
        "叠加加密专有（EVM/Solana/validator/HSM/MPC/低延迟）的复合型候选人最稀缺；"
        "求职按 CEX 远程政策与垂直赛道（安全/托管/基础设施）分层；DEX 样本仅 %d 岗，暂作观察。" % (
            cex_share, int(dex.get("jobs") or 0)),
    ]
    text = "\n\n".join(paragraphs)
    if len(text) > max_chars:
        # 保留标题 + 前三段，第四段截断
        text = text[:max_chars - 1].rsplit("\n", 1)[0] + "\n…"
    return text.split("\n\n")


def _exchange_type_map(data):
    """slug -> 类型（CEX/DEX 优先，其余来自 config/exchanges.yaml 的 company_types）。"""
    mapping = dict(data.get("company_types") or {})
    dynamics = data.get("exchange_dynamics") or {}
    for group, label in (("cex", "CEX"), ("dex", "DEX")):
        for row in dynamics.get(group) or []:
            if row.get("slug"):
                mapping[row["slug"]] = label
    return mapping


def _company_type_suffix(company, type_map):
    kind = (type_map or {}).get(company.get("company_slug"))
    return "（%s）" % kind if kind else ""


_RHYTHM_SHORT = {
    "长期大量招聘": "长期",
    "近期放量": "放量",
    "短期急招/合同补位": "急招",
    "慢性难招/常青岗": "难招",
    "常规补招": "常规",
}


def _exchange_brief(data, max_items=5):
    """Telegram 摘要里的 CEX/DEX 速览（让读者一眼分清类型）。"""
    dynamics = data.get("exchange_dynamics") or {}
    cex = dynamics.get("cex") or []
    dex = dynamics.get("dex") or []
    if not cex and not dex:
        return []

    def _items(rows):
        parts = []
        for row in rows[:max_items]:
            tags = row.get("tags") or []
            tag = _RHYTHM_SHORT.get(tags[0], tags[0]) if tags else ""
            parts.append("%s %d%s" % (
                _esc(_cell(row.get("name"), 18)), row.get("jobs", 0),
                ("（%s）" % tag) if tag else ""))
        if len(rows) > max_items:
            parts.append("等 %d 家" % (len(rows) - max_items))
        return "、".join(parts)

    lines = ["", "<b>交易所（CEX / DEX）</b>"]
    if cex:
        lines.append("CEX %d 家 / %d 岗：%s" % (
            len(cex), sum(r.get("jobs", 0) for r in cex), _items(cex)))
    if dex:
        lines.append("DEX %d 家 / %d 岗：%s" % (
            len(dex), sum(r.get("jobs", 0) for r in dex), _items(dex)))
    return lines


def _company_roles_text(company, limit=3):
    """公司信息合并成一段：角色 岗位数（代表 title）。"""
    role_titles = company.get("role_titles") or {}
    parts = []
    for role_item in (company.get("roles") or [])[:limit]:
        role = role_item.get("role") or ""
        label = ROLE_SHORT.get(role, role or "—")
        titles = [t for t in (role_titles.get(role) or []) if t]
        if titles:
            parts.append("%s %d（%s）" % (label, role_item.get("jobs", 0), _cell(titles[0], 46)))
        else:
            parts.append("%s %d" % (label, role_item.get("jobs", 0)))
    return "｜" + "、".join(parts) if parts else ""


def _esc(text):
    return html.escape(str(text or ""), quote=False)


def render_telegram(data, max_chars=3800):
    """Telegram HTML 摘要：控制长度，先丢"新岗位"再截断。"""
    ov = data.get("overview") or {}
    period = data.get("period") or {}
    start, end = period.get("start", ""), period.get("end", "")
    head = "<b>【crypto-jobs】加密技术岗位周报</b>\n%s ~ %s" % (start, end)
    body = [
        "在招 <b>%d</b> 个岗位 / %d 家公司" % (ov.get("jobs", 0), ov.get("companies", 0)),
        "本周新增 <b>%d</b>｜关闭 %d｜远程 %s" % (ov.get("new", 0), ov.get("closed", 0),
                                            _pct(ov.get("remote_share"))),
        "数据源健康 %d/%d" % (ov.get("sources_ok", 0), ov.get("sources_total", 0)),
        "",
        "<b>技能提及 Top 10</b>",
    ]
    analysis = render_core_analysis(data)
    if analysis:
        pos = body.index("<b>技能提及 Top 10</b>")
        body[pos:pos] = analysis + [""]
    brief = _exchange_brief(data)
    if brief:
        pos = body.index("<b>技能提及 Top 10</b>")
        body[pos:pos] = brief + [""]
    companies = data.get("companies") or []
    if companies:
        type_map = _exchange_type_map(data)
        pos = body.index("<b>技能提及 Top 10</b>")
        block = ["<b>招聘公司 Top 5</b>"]
        for company in companies[:5]:
            block.append("• %s%s — %d 个岗位（新增 %d）%s" % (
                _esc(_cell(company.get("company"), 24)),
                _esc(_company_type_suffix(company, type_map)),
                company.get("jobs", 0), company.get("new_jobs", 0),
                _esc(_company_roles_text(company, 2))))
        block.append("")
        body[pos:pos] = block
    for i, row in enumerate((data.get("top_skills") or [])[:10]):
        body.append("%d. %s — %d（%s）%s" % (
            i + 1, _esc(row.get("label")), row.get("count", 0), _delta(row.get("delta")),
            _esc(_children_text(row, 3))))
    rising = data.get("rising") or []
    if rising:
        body += ["", "<b>升温</b>：" + "、".join("%s %s" % (_esc(r.get("label")), _delta(r.get("delta")))
                                             for r in rising[:6])]
    notable = data.get("notable_new_jobs") or []
    if notable:
        body += ["", "<b>新岗位</b>"]
        for job in notable[:6]:
            body.append("• %s｜%s" % (_esc(_cell(job.get("company"), 24)), _esc(_cell(job.get("title"), 48))))
    text = head + "\n" + "\n".join(body)
    while len(text) > max_chars and notable:
        notable = notable[:-1]
        idx = text.find("\n<b>新岗位</b>")
        if idx >= 0:
            text = text[:idx]
        else:
            break
    while len(text) > max_chars and ("\n\n<b>升温" in text):
        text = text[: text.find("\n\n<b>升温")]
    if len(text) > max_chars:
        text = text[: max_chars - 1].rsplit("\n", 1)[0] + "\n…"
    return text


def render_daily_telegram(data, max_chars=3800):
    """日报 Telegram HTML 摘要（手动 --daily-digest 或 daily_digest 开启时使用）。"""
    ov = data.get("overview") or {}
    period = data.get("period") or {}
    lines = [
        "<b>【crypto-jobs】技术岗位日报</b>",
        _esc(str(period.get("end") or data.get("date") or "")),
        "在招 <b>%d</b> 个岗位 / %d 家公司" % (ov.get("jobs", 0), ov.get("companies", 0)),
        "新增 %d｜关闭 %d｜远程 %s" % (ov.get("new", 0), ov.get("closed", 0),
                                   _pct(ov.get("remote_share"))),
    ]
    if period.get("first_baseline"):
        lines.append("首次建立基线：暂无环比，下期开始对比")
    lines += _exchange_brief(data)
    lines += ["", "<b>技能提及 Top 10</b>"]
    for i, row in enumerate((data.get("top_skills") or [])[:10]):
        lines.append("%d. %s — %d（%s）%s" % (
            i + 1, _esc(row.get("label")), row.get("count", 0), _delta(row.get("delta")),
            _esc(_children_text(row, 3))))
    companies = data.get("companies") or []
    if companies:
        type_map = _exchange_type_map(data)
        lines += ["", "<b>招聘公司 Top 5</b>"]
        for company in companies[:5]:
            lines.append("• %s%s — %d 个岗位（新增 %d）%s" % (
                _esc(_cell(company.get("company"), 24)),
                _esc(_company_type_suffix(company, type_map)),
                company.get("jobs", 0), company.get("new_jobs", 0),
                _esc(_company_roles_text(company, 2))))
    new_jobs = data.get("notable_new_jobs") or []
    if new_jobs:
        lines += ["", "<b>新增岗位</b>"]
        for job in new_jobs[:5]:
            lines.append("• %s｜%s" % (_esc(_cell(job.get("company"), 24)),
                                       _esc(_cell(job.get("title"), 48))))
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[: max_chars - 1].rsplit("\n", 1)[0] + "\n…"
    return text


def render_job_alerts(jobs, type_map=None, max_items=8, now_text=""):
    """新岗位速报：事件触发时推送的短消息（列出具体 title）。"""
    jobs = jobs or []
    type_map = type_map or {}
    lines = [
        "<b>【crypto-jobs】新岗位速报</b>",
        "%s｜新增 <b>%d</b> 个目标岗位" % (now_text or "", len(jobs)),
        "",
    ]
    for job in jobs[:max_items]:
        company = _esc(_cell(job.get("company"), 24))
        kind = type_map.get(job.get("company_slug"))
        suffix = "（%s）" % kind if kind else ""
        lines.append("• %s%s｜%s（%s）" % (
            company, _esc(suffix), _esc(_cell(job.get("title"), 52)),
            _esc(ROLE_LABELS.get(job.get("role"), job.get("role") or "—"))))
    if len(jobs) > max_items:
        lines.append("…另有 %d 个新岗位" % (len(jobs) - max_items))
    lines += ["", "详细分析见每天 18:00 日报 / 周日 10:00 周报。"]
    return "\n".join(lines)


def render_test_card():
    return ("<b>【crypto-jobs】通道测试</b>\n"
            "crypto-jobs 已就绪：岗位采集 → 技能抽取 → 快照差分 → 周报。\n"
            "这是一条静默测试消息。")
