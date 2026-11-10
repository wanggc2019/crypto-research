#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""crypto-jobs 流水线离线单测（不联网、不写生产 data/）。

覆盖：岗位归一化/角色资历/远程、跨源去重、快照差分、索引窗口、指标口径、周报/日报渲染。
"""
import contextlib
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import metrics  # noqa: E402
import normalize  # noqa: E402
import report  # noqa: E402
import snapshot  # noqa: E402

import common  # noqa: E402

ROLES_CFG = {
    "exclude_keywords": ["engineering manager", "director", "marketing"],
    "roles": [
        {"id": "sre", "keywords": ["site reliability", "sre", "reliability engineer"]},
        {"id": "mlops", "keywords": ["mlops", "ml platform", "model serving"]},
        {"id": "devops", "keywords": ["devops", "ci/cd"]},
        {"id": "platform", "keywords": ["platform engineer", "infrastructure"]},
    ],
    "seniority": [
        {"id": "principal", "keywords": ["principal"]},
        {"id": "staff", "keywords": ["staff"]},
        {"id": "senior", "keywords": ["senior", "sr."]},
        {"id": "junior", "keywords": ["junior"]},
        {"id": "mid", "keywords": ["ii"]},
    ],
}

TAXONOMY = {
    "schema_version": 1,
    "categories": [{"id": "container", "label": "容器编排"}, {"id": "observability", "label": "可观测性"},
                   {"id": "cloud", "label": "云平台"}, {"id": "blockchain", "label": "链上"}],
    "drilldown": {"kubernetes": ["prometheus"]},
    "skills": [
        {"id": "kubernetes", "label": "Kubernetes", "category": "container", "aliases": ["kubernetes", "k8s"]},
        {"id": "prometheus", "label": "Prometheus", "category": "observability", "aliases": ["prometheus"]},
        {"id": "terraform", "label": "Terraform", "category": "cloud", "aliases": ["terraform"]},
        {"id": "validator-ops", "label": "验证者运维", "category": "blockchain", "aliases": ["validator operations"]},
    ],
}

CFG = {
    "schema_version": 1,
    "filters": {"target_roles": ["devops", "sre", "mlops", "platform"], "min_skills": 1},
    "report": {"min_skill_count": 1, "top_skills": 25, "rising_delta": 2, "max_notable_jobs": 10},
}


def fake_extract(text, taxonomy=None):
    text_l = (text or "").lower()
    found = {}
    for skill in TAXONOMY["skills"]:
        for alias in skill["aliases"]:
            if alias in text_l:
                found[skill["id"]] = found.get(skill["id"], 0) + 1
                break
    by_cat = {}
    for sid in found:
        cat = next(s["category"] for s in TAXONOMY["skills"] if s["id"] == sid)
        by_cat.setdefault(cat, []).append(sid)
    return {"skills": sorted(found), "required": sorted(found), "counts": found, "by_category": by_cat}


def raw_job(source_id="greenhouse:acme", tier="T0", job_id="1", title="Senior Site Reliability Engineer",
            company="Acme", company_slug="acme", locations=("Remote - USA",), description="", url=None):
    return {
        "source_id": source_id, "source_tier": tier, "company": company, "company_slug": company_slug,
        "job_id": job_id, "title": title, "url": url or "https://example.com/jobs/%s" % job_id,
        "locations": list(locations), "remote": "", "posted_at": "", "updated_at": "",
        "employment": "full-time", "salary": None, "description_text": description,
        "tags": [],
    }


def nj(key="src:1", company="Acme", company_slug="acme", title="SRE", role="sre", tier="T0",
       skills=("kubernetes",), required=(), description_hash="h1", remote="remote",
       locations=("Remote",), url="https://x", source_id="src"):
    return {
        "key": key, "source_id": source_id, "source_tier": tier, "company": company,
        "company_slug": company_slug, "job_id": key.split(":")[-1], "title": title, "role": role,
        "role_matched": "", "seniority": "senior", "remote": remote, "locations": list(locations),
        "url": url, "posted_at": "", "updated_at": "", "employment": "full-time", "salary": None,
        "description_hash": description_hash, "description_len": 100,
        "skills": list(skills), "required_skills": list(required), "skill_counts": {}, "tags": [],
    }


class TestNormalize(unittest.TestCase):
    def test_role_classification(self):
        cases = {
            "Senior Site Reliability Engineer": "sre",
            "DevOps Engineer": "devops",
            "ML Platform Engineer": "mlops",
            "Infrastructure Engineer": "platform",
            "Engineering Manager": "excluded",
            "Marketing Lead": "excluded",
            "Accountant": "other",
        }
        for title, expected in cases.items():
            self.assertEqual(normalize.match_role(title, ROLES_CFG)[0], expected, title)

    def test_seniority(self):
        cases = {"Principal SRE": "principal", "Staff DevOps Engineer": "staff",
                 "Senior SRE": "senior", "Sr. Platform Engineer": "senior",
                 "Junior DevOps": "junior", "SRE II": "mid", "SRE": "unknown"}
        for title, expected in cases.items():
            self.assertEqual(normalize.seniority_of(title, ROLES_CFG), expected, title)

    def test_remote(self):
        self.assertEqual(normalize.remote_of(["Remote - USA"]), "remote")
        self.assertEqual(normalize.remote_of(["Hybrid - London"]), "hybrid")
        self.assertEqual(normalize.remote_of(["New York, NY"]), "onsite")
        self.assertEqual(normalize.remote_of(["New York, NY"], "This is a fully remote role"), "remote")
        self.assertEqual(normalize.remote_of([], "no hint"), "")

    def test_normalize_job_fields_and_key(self):
        raw = raw_job(description="We run Kubernetes and Terraform on AWS. Prometheus for metrics.",
                      locations=("Remote - EU",))
        job = normalize.normalize_job(raw, CFG, ROLES_CFG, TAXONOMY, fake_extract)
        self.assertEqual(job["role"], "sre")
        self.assertEqual(job["seniority"], "senior")
        self.assertEqual(job["remote"], "remote")
        self.assertEqual(job["key"], "greenhouse:acme:1")
        self.assertEqual(job["skills"], ["kubernetes", "prometheus", "terraform"])
        self.assertEqual(job["required_skills"], ["kubernetes", "prometheus", "terraform"])
        self.assertTrue(job["description_hash"])
        # 同输入两次结果一致（快照可复现）
        self.assertEqual(job, normalize.normalize_job(raw, CFG, ROLES_CFG, TAXONOMY, fake_extract))

    def test_normalize_filters_and_dedupe(self):
        raws = [
            raw_job(job_id="1", title="SRE", description="kubernetes"),
            raw_job(job_id="2", title="Accountant", description="kubernetes"),
            raw_job(job_id="3", title="SRE", description="no skills here"),
            raw_job(source_id="aggregator", tier="T2", job_id="9", title="SRE",
                    description="kubernetes", company="Acme"),
        ]
        jobs, stats = normalize.normalize_jobs(raws, CFG, ROLES_CFG, TAXONOMY, fake_extract)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["source_tier"], "T0")  # T0 优先于 T2
        self.assertEqual(stats["fetched"], 4)
        self.assertEqual(stats["skipped_role"], 1)
        self.assertEqual(stats["skipped_no_skills"], 1)
        self.assertEqual(stats["duplicates"], 1)

    def test_job_key_fallback(self):
        raw = raw_job(job_id="")
        key = normalize.job_key(raw)
        self.assertTrue(key.startswith("greenhouse:acme:h:"))
        self.assertEqual(key, normalize.job_key(raw))


class TestSnapshot(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cjobs-snap-")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_save_load_and_previous(self):
        snap8 = snapshot.build_snapshot("2026-11-08", [nj("a:1")], [{"source_id": "a", "ok": True}], {})
        snap9 = snapshot.build_snapshot("2026-11-09", [nj("a:1")], [{"source_id": "a", "ok": True}], {})
        snapshot.save_snapshot(self.tmp, snap8)
        snapshot.save_snapshot(self.tmp, snap9)
        self.assertEqual(snapshot.load_snapshot(self.tmp, "2026-11-08")["date"], "2026-11-08")
        day, prev = snapshot.load_previous(self.tmp, "2026-11-09")
        self.assertEqual(day.isoformat(), "2026-11-08")
        self.assertIsNotNone(prev)
        self.assertEqual(snapshot.load_on_or_before(self.tmp, "2026-11-09")[0].isoformat(), "2026-11-09")
        self.assertEqual(snapshot.load_previous(self.tmp, "2026-11-08")[0], None)

    def test_diff_new_closed_changed_health_guard(self):
        prev = [nj("a:1", description_hash="h1"), nj("a:2", description_hash="h1", source_id="a"),
                nj("b:3", description_hash="h1", source_id="b")]
        cur = [nj("a:1", description_hash="h2"), nj("a:4", source_id="a")]
        health = [{"source_id": "a", "ok": True}, {"source_id": "b", "ok": False}]
        diff = snapshot.diff_jobs(prev, cur, health)
        self.assertEqual(diff["new_keys"], ["a:4"])
        self.assertEqual(diff["closed_keys"], ["a:2"])  # b:3 的源不健康 → 不算关闭
        self.assertEqual([c["key"] for c in diff["changed"]], ["a:1"])
        self.assertIn("description", diff["changed"][0]["fields"])

    def test_index_and_window(self):
        index = snapshot.update_index(snapshot.load_index(self.tmp),
                                      [nj("a:1"), nj("a:2")], "2026-11-01")
        snapshot.update_index(index, [nj("a:3")], "2026-11-06")
        snapshot.update_index(index, [nj("a:1")], "2026-11-08")
        self.assertEqual(index["jobs"]["a:1"]["first_seen"], "2026-11-01")
        self.assertEqual(index["jobs"]["a:1"]["last_seen"], "2026-11-08")
        self.assertEqual(snapshot.index_new_jobs(index, "2026-11-08", days=7), {"a:3"})
        # 首次发现时间不会被后续出现覆盖
        snapshot.update_index(index, [nj("a:3")], "2026-11-09")
        self.assertEqual(index["jobs"]["a:3"]["first_seen"], "2026-11-06")
        snapshot.save_index(self.tmp, index)
        self.assertEqual(snapshot.load_index(self.tmp)["schema"], "jobs-index/v1")


    def test_push_log_dedupe(self):
        self.assertFalse(snapshot.was_pushed(self.tmp, "weekly", "2026-11-09"))
        snapshot.mark_pushed(self.tmp, "weekly", "2026-11-09", True, "ok")
        self.assertTrue(snapshot.was_pushed(self.tmp, "weekly", "2026-11-09"))
        self.assertFalse(snapshot.was_pushed(self.tmp, "weekly", "2026-11-16"))
        self.assertFalse(snapshot.was_pushed(self.tmp, "daily", "2026-11-09"))
        log = snapshot.load_push_log(self.tmp)
        self.assertEqual(log["schema"], "push-log/v1")
        self.assertTrue(log["sent"]["weekly:2026-11-09"]["ok"])

    def test_prune_snapshots(self):
        for day in ("2026-01-01", "2026-11-01", "2026-11-08"):
            snapshot.save_snapshot(self.tmp, snapshot.build_snapshot(day, [], [], {}))
        removed = snapshot.prune_snapshots(self.tmp, keep_days=30, today="2026-11-08")
        self.assertEqual(removed, 1)
        self.assertEqual([d.isoformat() for d in snapshot.list_snapshot_dates(self.tmp)],
                         ["2026-11-01", "2026-11-08"])


class TestMetrics(unittest.TestCase):
    def _snap(self, date, jobs):
        return snapshot.build_snapshot(date, jobs, [{"source_id": "src", "ok": True}], {})

    def test_skill_stats(self):
        jobs = [nj("a:1", skills=("kubernetes", "prometheus"), required=("kubernetes",)),
                nj("a:2", skills=("kubernetes",))]
        stats = metrics.skill_stats(jobs)
        self.assertEqual(stats["counts"]["kubernetes"], 2)
        self.assertEqual(stats["required"]["kubernetes"], 1)

    def test_build_report_data_baseline_and_delta(self):
        base = self._snap("2026-11-01", [nj("a:1", skills=("kubernetes",)),
                                         nj("a:2", skills=("kubernetes", "terraform"))])
        cur = self._snap("2026-11-08", [nj("a:1", skills=("kubernetes",)),
                                        nj("a:3", source_id="src", skills=("terraform", "prometheus")),
                                        nj("a:4", source_id="src", skills=("prometheus",))])
        index = {"schema": "jobs-index/v1", "jobs": {"a:3": {"first_seen": "2026-11-06"},
                                                     "a:1": {"first_seen": "2026-10-01"}}}
        data = metrics.build_report_data(cur, base, index, CFG, TAXONOMY, date="2026-11-08",
                                         period_days=7,
                                         history_snapshots=[(snapshot._as_date("2026-11-01"), base),
                                                            (snapshot._as_date("2026-11-08"), cur)])
        ov = data["overview"]
        self.assertEqual(ov["jobs"], 3)
        self.assertEqual(ov["new"], 1)
        self.assertEqual(ov["closed"], 1)
        top = {r["skill"]: r for r in data["top_skills"]}
        self.assertEqual(top["kubernetes"]["delta"], -1)
        self.assertEqual(top["terraform"]["delta"], 0)
        self.assertEqual(top["prometheus"]["count"], 2)
        self.assertIn("skill_history", data)
        self.assertEqual(len(data["skill_history"]["series"][0]["counts"]), 2)

    def test_build_report_data_first_baseline(self):
        cur = self._snap("2026-11-08", [nj("a:1")])
        data = metrics.build_report_data(cur, None, {"jobs": {}}, CFG, TAXONOMY, date="2026-11-08")
        self.assertTrue(data["period"]["first_baseline"])
        self.assertEqual(data["overview"]["new"], 0)
        self.assertEqual(data["overview"]["closed"], 0)


    def test_skill_children_drilldown(self):
        cur = self._snap("2026-11-08", [
            nj("a:1", skills=("kubernetes", "prometheus")),
            nj("a:2", skills=("kubernetes",)),
        ])
        data = metrics.build_report_data(cur, None, {"jobs": {}}, CFG, TAXONOMY,
                                         date="2026-11-08")
        kube = next(r for r in data["top_skills"] if r["skill"] == "kubernetes")
        self.assertEqual([c["skill"] for c in kube.get("children") or []], ["prometheus"])
        role = next(r for r in data["by_role"] if r["role"] == "sre")
        role_kube = next(r for r in role["top_skills"] if r["skill"] == "kubernetes")
        self.assertEqual([c["skill"] for c in role_kube.get("children") or []], ["prometheus"])

    def test_company_rows(self):
        jobs = [
            nj("a:1", company="Acme", company_slug="acme", role="sre",
               skills=("kubernetes", "prometheus"), remote="remote"),
            nj("a:2", company="Acme", company_slug="acme", role="backend",
               skills=("kubernetes", "terraform"), remote="onsite"),
            nj("a:3", company="Beta", company_slug="beta", role="frontend",
               skills=("prometheus",)),
        ]
        rows = metrics.company_rows(jobs, {"a:1"}, metrics.taxonomy_maps(TAXONOMY))
        self.assertEqual([r["company_slug"] for r in rows], ["acme", "beta"])
        acme = rows[0]
        self.assertEqual(acme["jobs"], 2)
        self.assertEqual(acme["new_jobs"], 1)
        self.assertEqual(acme["remote"], 1)
        self.assertEqual(acme["remote_share"], 50.0)
        self.assertEqual({r["role"] for r in acme["roles"]}, {"sre", "backend"})
        self.assertEqual(acme["top_skills"][0]["skill"], "kubernetes")
        self.assertTrue(acme["sample_titles"])


class TestReport(unittest.TestCase):
    def _data(self):
        cur = snapshot.build_snapshot("2026-11-08", [
            nj("a:1", role="sre", skills=("kubernetes", "prometheus"), required=("kubernetes",)),
            nj("a:2", role="devops", skills=("kubernetes", "terraform"), company="Beta",
               company_slug="beta"),
        ], [{"source_id": "src", "ok": True}], {})
        return metrics.build_report_data(
            cur, None, {"jobs": {}}, CFG, TAXONOMY, date="2026-11-08",
            exchanges={"cex": [{"slug": "acme", "name": "Acme"}],
                       "dex": [{"slug": "beta", "name": "Beta"}]})

    def test_weekly_markdown(self):
        md = report.render_weekly(self._data(), CFG)
        for needle in ("技能需求 Top", "市场概览", "分角色技能需求", "方法与局限", "Kubernetes", "Beta"):
            self.assertIn(needle, md)


    def test_exchange_dynamics_section(self):
        md = report.render_weekly(self._data(), CFG)
        for needle in ("## 7. 交易所动态（CEX / DEX）", "CEX vs DEX 对比", "招聘节奏"):
            self.assertIn(needle, md)

    def test_daily_markdown(self):
        md = report.render_daily(self._data(), CFG)
        self.assertIn("技术岗位日报", md)
        self.assertIn("Kubernetes", md)

    def test_telegram_digest_length(self):
        data = self._data()
        data["notable_new_jobs"] = [nj("a:%d" % i, title="Long title " * 10, company="Company " * 6)
                                    for i in range(20)]
        text = report.render_telegram(data)
        self.assertLessEqual(len(text), 3800)
        self.assertNotIn("\n   ", text)  # 公司信息必须合并成单行
        self.assertIn("crypto-jobs", text)
        self.assertIn("技能提及 Top 10", text)



    def test_weekly_digest_has_core_analysis(self):
        text = report.render_telegram(self._data())
        for needle in ("核心结论", "市场结构", "技能需求", "结论"):
            self.assertIn(needle, text)

    def test_daily_telegram_digest(self):
        data = self._data()
        text = report.render_daily_telegram(data)
        for needle in ("技术岗位日报", "在招", "交易所（CEX / DEX）", "CEX 1 家", "DEX 1 家",
                       "Acme（CEX）", "Beta（DEX）", "技能提及 Top 10", "招聘公司 Top 5", "Prometheus（1）"):
            self.assertIn(needle, text)
        self.assertLessEqual(len(text), 3800)
        self.assertNotIn("\n   ", text)  # 公司信息必须合并成单行

    def test_empty_snapshot_safe(self):
        cur = snapshot.build_snapshot("2026-11-08", [], [], {})
        data = metrics.build_report_data(cur, None, {"jobs": {}}, CFG, TAXONOMY, date="2026-11-08")
        md = report.render_weekly(data, CFG)
        self.assertIn("本周没有达到阈值的技能", md)

    def test_company_stats_sections(self):
        data = self._data()
        self.assertTrue(data.get("companies"))
        acme = next(c for c in data["companies"] if c["company_slug"] == "acme")
        self.assertEqual(acme["jobs"], 1)
        self.assertTrue(acme["top_skills"])
        md = report.render_weekly(data, CFG)
        for needle in ("## 5. 公司招聘统计", "## 6. 公司画像", "类型", "CEX", "DEX",
                       "角色构成（Top3）", "代表岗位"):
            self.assertIn(needle, md)
        daily = report.render_daily(data, CFG)
        self.assertIn("公司招聘 Top 10", daily)
        self.assertIn("Acme", daily)


class TestNotify(unittest.TestCase):
    def test_dry_run_prints(self):
        import notify
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ok, err = notify.send("<b>hello</b>", dry_run=True)
        self.assertTrue(ok)
        self.assertEqual(err, "")
        output = buf.getvalue()
        self.assertIn("hello", output)
        self.assertIn("🏷", output)


class TestDevRoleScope(unittest.TestCase):
    """开发岗位纳入统计：用真实 roles.yaml / app.yaml 校验分类边界。"""

    @classmethod
    def setUpClass(cls):
        cls.roles = common.load_config("roles.yaml")
        cls.app = common.load_config("app.yaml")

    def test_target_scope(self):
        target = set(self.roles["target_roles"])
        for role in ("devops", "sre", "mlops", "platform", "software", "backend", "frontend",
                     "fullstack", "mobile", "blockchain", "trading", "data", "ml", "embedded"):
            self.assertIn(role, target)
        self.assertNotIn("security", target)  # 安全/测试默认不计入开发样本
        self.assertEqual(target, set(self.app["filters"]["target_roles"]))

    def test_dev_title_classification(self):
        cases = {
            "Senior/Staff Engineer, Liquidity Platform": "trading",
            "Backend Engineer (Java) - KYC Tech": "backend",
            "Staff Software Engineer, Frontend (Consumer)": "frontend",
            "Full Stack Engineer (Web, Frontend Oriented)": "fullstack",
            "Software Engineer, Mobile (Android)": "mobile",
            "Blockchain Development Engineer (Java)": "blockchain",
            "Backend Engineer (Java) - Web3 Wallet": "backend",
            "Senior Frontend Engineer - Web3 Wallet": "frontend",
            "Mobile Engineer (Web3)": "mobile",
            "Staff Machine Learning Engineer": "ml",
            "Senior Data Engineer": "data",
            "Staff Engineer, Middleware": "platform",
            "Senior/Staff Engineer, AI Agent Development": "ml",
            "Senior/Staff Engineer, Growth": "software",
            "Staff Software Engineer, Security Platform": "software",
            "Staff QA Engineer": "excluded",
            "Engineering Manager": "excluded",
            "Principal Risk Control Architect": "other",
        }
        for title, expected in cases.items():
            self.assertEqual(normalize.match_role(title, self.roles)[0], expected, title)

    def test_send_weekly_sends_digest_and_document(self):
        import notify

        calls = []
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as handle:
            handle.write("# report")
            path = handle.name

        def fake_send(text, dry_run=False):
            calls.append("digest")
            return True, ""

        def fake_document(token, chat, doc_path, caption="", silent=False):
            calls.append("document")
            return True, ""

        try:
            with mock.patch.object(notify, "send", side_effect=fake_send), \
                    mock.patch.object(notify, "load_bot_env", return_value={}), \
                    mock.patch.object(notify, "check_credentials", return_value=("token", "123", [])), \
                    mock.patch.object(notify, "send_document", side_effect=fake_document):
                data = {"date": "2026-11-09", "overview": {"jobs": 1, "companies": 1}, "top_skills": []}
                ok, err = notify.send_weekly(data, report_path=path)
            self.assertTrue(ok)
            self.assertEqual(err, "")
            self.assertEqual(calls, ["digest", "document"])
        finally:
            os.remove(path)

    def test_send_weekly_dry_run_skips_document(self):
        import notify
        calls = []
        with mock.patch.object(notify, "send", side_effect=lambda text, dry_run=False: (calls.append("digest"), (True, ""))[1]), \
                mock.patch.object(notify, "send_document", side_effect=lambda *a, **k: (calls.append("document"), (True, ""))[1]):
            ok, err = notify.send_weekly({"date": "2026-11-09", "overview": {}}, dry_run=True)
        self.assertTrue(ok)
        self.assertEqual(calls, ["digest"])


class TestWeeklyDue(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cjobs-weekly-")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_weekly_due_by_weekday(self):
        import main
        cfg = {"delivery": {"weekly": {"weekday": 1}}}
        self.assertTrue(main.weekly_due(cfg, "2026-11-09"))   # 周一
        self.assertFalse(main.weekly_due(cfg, "2026-11-10"))  # 周二
        self.assertTrue(main.weekly_due(cfg, "2026-11-16"))   # 下周一
        self.assertFalse(main.weekly_due({}, "2026-11-10"))   # 缺配置默认周一


    def test_weekly_baseline_fallback(self):

        import main
        # 只有 11-09 快照，查询 11-15：7 天前（11-08）没有快照 → 回退到最早旧快照 11-09
        snapshot.save_snapshot(self.tmp, snapshot.build_snapshot("2026-11-09", [], [], {}))
        base_date, base = main.weekly_baseline(self.tmp, "2026-11-15")
        self.assertEqual(str(base_date), "2026-11-09")
        self.assertIsNotNone(base)
        # 有 7 天前快照时优先用它
        snapshot.save_snapshot(self.tmp, snapshot.build_snapshot("2026-11-08", [], [], {}))
        base_date, base = main.weekly_baseline(self.tmp, "2026-11-15")
        self.assertEqual(str(base_date), "2026-11-08")
        self.assertIsNotNone(base)


class TestExchangeAnalysis(unittest.TestCase):
    def _mk(self, slug, index, days_ago, title="Software Engineer", employment="full-time"):
        import datetime
        job = dict(nj("%s:%d" % (slug, index), company=slug.upper(), company_slug=slug, title=title))
        day = datetime.date(2026, 11, 9) - datetime.timedelta(days=days_ago)
        job["posted_at"] = day.isoformat() + "T00:00:00+04:00"
        job["employment"] = employment
        return job

    def test_cex_dex_and_rhythm(self):
        maps = metrics.taxonomy_maps(TAXONOMY)
        jobs = [self._mk("okx", i, 90) for i in range(16)]
        jobs += [self._mk("okx", 100 + i, 5, title="Backend Engineer") for i in range(4)]
        jobs += [self._mk("uniswap-labs", i, 70, title="Smart Contract Engineer") for i in range(4)]
        data = metrics.exchange_analysis(
            jobs,
            {"cex": [{"slug": "okx", "name": "OKX"}],
             "dex": [{"slug": "uniswap-labs", "name": "Uniswap Labs"}]},
            maps, date="2026-11-09")
        cex = data["cex"][0]
        self.assertEqual(cex["jobs"], 20)
        self.assertIn("长期大量招聘", cex["tags"])
        self.assertIn("近期放量", cex["tags"])
        dex = data["dex"][0]
        self.assertEqual(dex["jobs"], 4)
        self.assertEqual(dex["contract_jobs"], 0)  # Smart Contract 不算合同岗
        self.assertEqual(data["comparison"]["cex"]["jobs"], 20)
        self.assertEqual(data["comparison"]["dex"]["jobs"], 4)
        self.assertEqual(data["coverage"]["dex"]["configured"], 1)

    def test_contract_detection(self):
        self.assertFalse(metrics.is_contract_job(dict(nj("x:1", title="Smart Contract Engineer"))))
        self.assertTrue(metrics.is_contract_job(dict(
            nj("x:2", title="Backend Engineer"), employment="Contract")))

    def test_job_direction(self):
        self.assertEqual(metrics.job_direction(dict(nj("x:1", title="Mobile Engineer"))), "移动端")
        self.assertEqual(metrics.job_direction(dict(nj("x:2", title="Trading System Engineer"))), "交易/撮合")
        self.assertEqual(metrics.job_direction(dict(nj("x:3", title="AI Agent Engineer"))), "AI/ML")
        self.assertEqual(metrics.job_direction(dict(nj("x:4", title="Custody Engineer"))), "钱包/托管/安全")


class TestDailyPushPolicy(unittest.TestCase):
    def test_should_send_daily(self):
        import main
        cfg = {"delivery": {"daily_digest": True, "daily_min_new_jobs": 0}}
        self.assertTrue(main.should_send_daily(cfg, 0))
        self.assertFalse(main.should_send_daily({"delivery": {"daily_digest": False}}, 10))
        self.assertTrue(main.should_send_daily({"delivery": {"daily_digest": False}}, 10, manual=True))
        cfg_threshold = {"delivery": {"daily_digest": True, "daily_min_new_jobs": 5}}
        self.assertFalse(main.should_send_daily(cfg_threshold, 4))
        self.assertTrue(main.should_send_daily(cfg_threshold, 5))


class TestJobAlerts(unittest.TestCase):
    def test_select_new_jobs(self):
        import main
        jobs = [{"key": "a"}, {"key": "b"}]
        self.assertEqual([j["key"] for j in main.select_new_jobs(jobs, {"a"})], ["b"])
        self.assertEqual(main.select_new_jobs(jobs, set()), jobs)

    def test_render_job_alerts(self):
        jobs = [{
            "key": "acme:1", "company": "Acme", "company_slug": "acme",
            "title": "Senior Site Reliability Engineer", "role": "sre",
        }]
        text = report.render_job_alerts(jobs, {"acme": "CEX"}, now_text="2026-11-10 10:00")
        for needle in ("新岗位速报", "Acme（CEX）", "Senior Site Reliability Engineer", "SRE / 可靠性"):
            self.assertIn(needle, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
