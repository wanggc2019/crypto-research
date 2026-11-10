#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""crypto-jobs 采集层单测（全部离线，不联网、不写 data/）。

覆盖：
- 源注册表结构（启用源数量 / T0 数量 / 字段与类型合法性 / 禁用的反爬站点）
- 每种适配器解析（Greenhouse / Lever / Ashby / RSS / Web3.career / RemoteOK / Remotive / Jobicy）
- 对外 job 契约字段完整性与类型
- T2 通用站 crypto 关键词 + 公司白名单过滤（含词边界反例）
- 单源失败降级（health 记错、不抛整轮）
- fixtures 离线回放（零网络、manifest 映射、未覆盖源跳过）
- health 结构、limit / only 语义、正文截断
"""
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
FIXTURES = os.path.join(ROOT, "tests", "fixtures", "sources")
sys.path.insert(0, SCRIPTS)

import source_adapters  # noqa: E402
import sources  # noqa: E402

import common  # noqa: E402

GH_JOB = {
    "id": 101,
    "title": "Senior Site Reliability Engineer",
    "absolute_url": "https://example.com/jobs/101",
    "company_name": "Acme",
    "location": {"name": "Remote - USA"},
    "offices": [{"name": "US Remote"}],
    "departments": [{"name": "Infrastructure"}],
    "content": "&lt;p&gt;We run &lt;b&gt;Kubernetes&lt;/b&gt; and Terraform.&lt;/p&gt;",
    "first_published": "2026-10-01T10:00:00-04:00",
    "updated_at": "2026-10-02T11:00:00-04:00",
}
GH_PAYLOAD = json.dumps({"jobs": [GH_JOB], "meta": {"total": 1}})

REMOTIVE_PAYLOAD = json.dumps(
    {
        "job-count": 1,
        "jobs": [
            {
                "id": 9001,
                "url": "https://remotive.com/jobs/9001",
                "title": "Platform Engineer",
                "company_name": "Ledger",
                "tags": ["kubernetes", "blockchain"],
                "category": "Software Development",
                "job_type": "full_time",
                "publication_date": "2026-10-03T12:00:00",
                "candidate_required_location": "Worldwide",
                "salary": "$90k - $120k",
                "description": "<p>Build validator infrastructure.</p>",
            }
        ],
    }
)

JOBICY_PAYLOAD = json.dumps(
    {
        "jobCount": 1,
        "jobs": [
            {
                "id": 7001,
                "url": "https://jobicy.com/jobs/7001",
                "jobTitle": "Cloud AI Engineer",
                "companyName": "Binance",
                "jobIndustry": ["Software Engineering"],
                "jobType": ["Full-Time"],
                "jobGeo": "USA",
                "jobLevel": "Senior",
                "jobDescription": "<p>Kubernetes and GPU clusters for blockchain data.</p>",
                "pubDate": "2026-10-04T08:00:00+00:00",
                "annualSalaryMin": 150000,
                "annualSalaryMax": 200000,
                "salaryCurrency": "USD",
            }
        ],
    }
)

REMOTEOK_PAYLOAD = json.dumps(
    [
        {"legal": "API Terms of Service", "last_updated": 1},
        {"id": "1", "position": "Blockchain SRE", "company": "Acme", "tags": ["sre", "crypto"],
         "description": "<p>Kubernetes and validators</p>", "url": "https://remoteok.com/1",
         "date": "2026-10-05T00:00:00+00:00", "salary_min": 0, "salary_max": 0},
        {"id": "2", "position": "Marketing Lead", "company": "Binance", "tags": ["marketing"],
         "description": "<p>Growth campaigns</p>", "url": "https://remoteok.com/2",
         "date": "2026-10-05T00:00:00+00:00"},
        {"id": "3", "position": "Senior Accountant", "company": "Acme Widgets", "tags": ["finance"],
         "description": "<p>Monthly close</p>", "url": "https://remoteok.com/3",
         "date": "2026-10-05T00:00:00+00:00"},
    ]
)


def _stub(payloads):
    """url → payload 的注入函数；未登记的 URL 抛错（模拟网络失败）。"""
    def _fetch(url):
        for key, payload in payloads.items():
            if key in url:
                return payload
        raise RuntimeError("network is down: %s" % url)
    return _fetch


def _load_fixture(filename):
    with open(os.path.join(FIXTURES, filename), "r", encoding="utf-8", errors="ignore") as handle:
        return handle.read()


class TestSourceRegistry(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = common.load_config("sources.yaml")
        cls.app = common.load_config("app.yaml")
        cls.enabled = [s for s in cls.cfg.get("sources") or [] if s.get("enabled", True) is not False]

    def test_schema_and_counts(self):
        self.assertEqual(self.cfg.get("schema_version"), 1)
        self.assertGreaterEqual(len(self.enabled), 15, "启用源必须 ≥15")
        t0 = [s for s in self.enabled if s.get("tier") == "T0"]
        self.assertGreaterEqual(len(t0), 10, "T0 源必须 ≥10")
        ids = [s.get("id") for s in self.enabled]
        self.assertEqual(len(ids), len(set(ids)), "source_id 不能重复")

    def test_each_source_is_valid(self):
        for source in self.enabled:
            sid = source.get("id")
            self.assertTrue(sid, "源缺少 id")
            self.assertIn(source.get("tier"), ("T0", "T1", "T2"), sid)
            self.assertIn(source.get("type"), source_adapters.ADAPTERS, sid)
            self.assertTrue(str(source.get("url") or "").startswith("https://"), "%s 的 url 必须是 https" % sid)
            self.assertTrue(source.get("note"), "%s 缺少 note/备注" % sid)
            self.assertTrue(source.get("tos"), "%s 缺少 tos/使用条款备注" % sid)
            if source.get("crypto_only"):
                self.assertEqual(source.get("tier"), "T2", "%s: 只有 T2 通用站需要 crypto_only" % sid)

    def test_banned_sites_absent(self):
        banned = ("linkedin.com", "indeed.com", "glassdoor.com", "ziprecruiter.com")
        for source in self.enabled:
            url = str(source.get("url") or "").lower()
            for domain in banned:
                self.assertNotIn(domain, url, "禁止抓取 %s（%s）" % (domain, source.get("id")))

    def test_t2_sources_have_keywords(self):
        keywords = sources._crypto_keywords(self.cfg, self.app)
        self.assertIn("crypto", keywords)
        self.assertIn("blockchain", keywords)
        t2 = [s for s in self.enabled if s.get("tier") == "T2"]
        self.assertTrue(t2, "至少要有 1 个 T2 源")
        self.assertLessEqual(len(t2), 3, "T2 源最多 3 个（任务契约）")

    def test_companies_whitelist(self):
        whitelist = sources._company_whitelist(self.cfg)
        self.assertGreaterEqual(len(whitelist), 50)
        for name in ("coinbase", "binance", "kraken", "fireblocks"):
            self.assertIn(name, whitelist)


class TestAdapters(unittest.TestCase):
    def test_greenhouse_fixture(self):
        source = {"id": "gh_coinbase", "type": "greenhouse", "tier": "T0", "company": "Coinbase"}
        jobs = source_adapters.parse_source(source, _load_fixture("greenhouse_coinbase.json"))
        self.assertEqual(len(jobs), 5)
        first = jobs[0]
        self.assertTrue(first["title"])
        self.assertTrue(first["url"].startswith("http"))
        self.assertGreater(len(first["description_text"]), 200, "JD 正文应被清洗出来")
        self.assertNotIn("<", first["description_text"], "HTML 标签必须清掉")
        self.assertTrue(first["locations"])
        self.assertIn(first["remote"], ("remote", "hybrid", "onsite", ""))

    def test_lever_fixture(self):
        source = {"id": "lv_anchorage", "type": "lever", "tier": "T0", "company": "Anchorage Digital"}
        jobs = source_adapters.parse_source(source, _load_fixture("lever_anchorage.json"))
        self.assertEqual(len(jobs), 5)
        for job in jobs:
            self.assertTrue(job["job_id"])
            self.assertTrue(job["url"].startswith("https://jobs.lever.co/"))
            self.assertTrue(job["description_text"])
            self.assertEqual(job["remote"], "remote")  # 该 board 全部 workplaceType=remote

    def test_ashby_fixture(self):
        source = {"id": "as_kraken", "type": "ashby", "tier": "T0", "company": "Kraken"}
        jobs = source_adapters.parse_source(source, _load_fixture("ashby_kraken.json"))
        self.assertEqual(len(jobs), 5)
        for job in jobs:
            self.assertTrue(job["job_id"])
            self.assertTrue(job["url"].startswith("https://jobs.ashbyhq.com/"))
            self.assertIn(job["remote"], ("remote", "hybrid", "onsite", ""))
        self.assertTrue(any(job["salary"] for job in jobs), "Ashby 带薪酬字段，应至少解析出一条")

    def test_rss_fixture(self):
        source = {"id": "cjl_rss", "type": "rss_jobs", "tier": "T1", "company": "CryptoJobsList"}
        jobs = source_adapters.parse_source(source, _load_fixture("cryptojobslist.rss.xml"))
        self.assertEqual(len(jobs), 5)
        for job in jobs:
            self.assertTrue(job["title"])
            self.assertTrue(job["url"].startswith("https://cryptojobslist.com/jobs/"))
            self.assertGreater(len(job["description_text"]), 500)
            self.assertTrue(job["company"])

    def test_web3career_fixture(self):
        source = {"id": "web3career", "type": "web3career", "tier": "T1", "company": "Web3.career"}
        jobs = source_adapters.parse_source(source, _load_fixture("web3career.html"))
        self.assertEqual(len(jobs), 4)
        for job in jobs:
            self.assertTrue(job["title"])
            self.assertTrue(job["url"].startswith("https://web3.career/"))
            self.assertGreater(len(job["description_text"]), 100, "JSON-LD 正文应按标题配对成功")
            self.assertTrue(job["posted_at"], "JSON-LD datePosted 应解析为 ISO")

    def test_remoteok_fixture(self):
        source = {"id": "remoteok", "type": "remoteok", "tier": "T2", "company": "RemoteOK"}
        jobs = source_adapters.parse_source(source, _load_fixture("remoteok.json"))
        self.assertEqual(len(jobs), 8, "ToS 声明项必须被跳过")
        self.assertTrue(all(job["remote"] == "remote" for job in jobs))

    def test_remotive_inline(self):
        jobs = source_adapters.parse_source({"id": "remotive", "type": "remotive"}, REMOTIVE_PAYLOAD)
        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job["company"], "Ledger")
        self.assertEqual(job["employment"], "Full-time")
        self.assertEqual(job["remote"], "remote")
        self.assertEqual(job["salary"], "$90k - $120k")

    def test_jobicy_inline(self):
        jobs = source_adapters.parse_source({"id": "jobicy", "type": "jobicy"}, JOBICY_PAYLOAD)
        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job["company"], "Binance")
        self.assertEqual(job["employment"], "Full-time")
        self.assertEqual(job["salary"], "$150000 - $200000 /year")
        self.assertEqual(job["remote"], "remote")

    def test_unknown_type_raises(self):
        with self.assertRaises(ValueError):
            source_adapters.parse_source({"id": "x", "type": "nope"}, "{}")

    def test_invalid_payload_raises(self):
        with self.assertRaises((ValueError, json.JSONDecodeError)):
            source_adapters.parse_source({"id": "x", "type": "greenhouse"}, "not json")
        with self.assertRaises(Exception):
            source_adapters.parse_source({"id": "x", "type": "rss_jobs"}, "<not-xml")


class TestHelpers(unittest.TestCase):
    def test_derive_remote(self):
        cases = [
            (["Remote - USA"], "", "", False, "remote"),
            (["Hybrid - London"], "", "", False, "hybrid"),
            (["New York, NY"], "", "", False, "onsite"),
            (["New York, NY"], "", "This is a fully remote role", False, "remote"),
            ([], "", "no hint", False, ""),
            (["Worldwide"], "", "", True, "remote"),
            (["Tokyo"], "hybrid", "", False, "hybrid"),
            (["Tokyo"], "onsite", "", True, "onsite"),
        ]
        for locations, explicit, description, assume, expected in cases:
            self.assertEqual(
                source_adapters.derive_remote(locations, explicit, description, assume_remote=assume),
                expected, "%s/%s" % (locations, explicit),
            )

    def test_employment_and_salary(self):
        self.assertEqual(source_adapters.norm_employment("FullTime"), "Full-time")
        self.assertEqual(source_adapters.norm_employment("Full-Time - Remote"), "Full-time")
        self.assertEqual(source_adapters.norm_employment("Part-Time"), "Part-time")
        self.assertEqual(source_adapters.norm_employment("Contractor"), "Contract")
        self.assertEqual(source_adapters.norm_employment(""), "")
        self.assertEqual(source_adapters.norm_employment("employment_required"), "")
        self.assertEqual(source_adapters.format_salary(120000, 150000, "USD", "1 YEAR"), "$120000 - $150000 /year")
        self.assertEqual(source_adapters.format_salary(0, 0, "USD"), "")
        self.assertEqual(source_adapters.format_salary(None, None), "")

    def test_company_slug(self):
        self.assertEqual(source_adapters.slugify_company("TRM Labs"), "trm-labs")
        self.assertEqual(source_adapters.slugify_company("Crypto.com"), "crypto-com")


class TestCryptoFilter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = common.load_config("sources.yaml")
        cls.app = common.load_config("app.yaml")
        cls.keywords = sources._crypto_keywords(cls.cfg, cls.app)
        cls.whitelist = sources._company_whitelist(cls.cfg)

    def _job(self, title, company="Acme", tags=None, description=""):
        return {"title": title, "company": company, "tags": tags or [], "description_text": description}

    def test_keyword_hits(self):
        self.assertTrue(sources.is_crypto_job(self._job("Blockchain SRE"), self.keywords, self.whitelist))
        self.assertTrue(sources.is_crypto_job(self._job("SRE", tags=["ethereum"]), self.keywords, self.whitelist))
        self.assertTrue(sources.is_crypto_job(
            self._job("Platform Engineer", description="Join our validator team"), self.keywords, self.whitelist))

    def test_company_whitelist(self):
        self.assertTrue(sources.is_crypto_job(self._job("Marketing Lead", company="Binance"),
                                             self.keywords, self.whitelist))
        self.assertFalse(sources.is_crypto_job(self._job("Accountant", company="Acme Widgets"),
                                               self.keywords, self.whitelist))

    def test_word_boundaries(self):
        # defi 不应命中 definitive/define；trade 不应命中 trademark；go 类短词不在此列
        self.assertFalse(sources.is_crypto_job(self._job("Definitive Guide to Hiring"), self.keywords, self.whitelist))
        self.assertFalse(sources.is_crypto_job(self._job("Trademark Paralegal"), self.keywords, self.whitelist))
        self.assertTrue(sources.is_crypto_job(self._job("DeFi Risk Analyst"), self.keywords, self.whitelist))

    def test_circleci_not_circle(self):
        self.assertFalse(sources.is_crypto_job(self._job("SRE", company="CircleCI"), self.keywords, self.whitelist))

    def test_t2_end_to_end_filter(self):
        cfg = {
            "schema_version": 1,
            "sources": [
                {"id": "remoteok", "tier": "T2", "type": "remoteok", "url": "https://remoteok.com/api",
                 "crypto_only": True},
            ],
        }
        jobs, health = sources.fetch_all(sources_config=cfg, fetch_http=_stub({"remoteok": REMOTEOK_PAYLOAD}))
        self.assertEqual(health[0]["count"], 2)  # 命中 crypto 关键词 + Binance 白名单
        titles = sorted(job["title"] for job in jobs)
        self.assertEqual(titles, ["Blockchain SRE", "Marketing Lead"])


class TestFetchAll(unittest.TestCase):
    def test_contract_fields_on_fixtures(self):
        jobs, health = sources.fetch_all(fixtures_dir=FIXTURES)
        self.assertGreater(len(jobs), 0)
        for job in jobs:
            self.assertEqual(set(job.keys()), set(sources.JOB_KEYS))
            self.assertIn(job["source_tier"], ("T0", "T1", "T2"))
            self.assertIsInstance(job["locations"], list)
            self.assertIsInstance(job["tags"], list)
            self.assertIsInstance(job["description_text"], str)
            self.assertIsInstance(job["salary"], str)
            self.assertIn(job["remote"], ("remote", "hybrid", "onsite", ""))
            self.assertTrue(job["job_id"])
            self.assertTrue(job["url"].startswith("http"))
        self.assertGreaterEqual(len([h for h in health if h["ok"]]), 6)

    def test_health_structure(self):
        _, health = sources.fetch_all(fixtures_dir=FIXTURES)
        for item in health:
            self.assertEqual(set(item.keys()), {"source_id", "tier", "ok", "count", "ms", "error"})
            self.assertIsInstance(item["ok"], bool)
            self.assertIsInstance(item["count"], int)
            self.assertIsInstance(item["ms"], int)
            self.assertIsInstance(item["error"], str)

    def test_fixtures_replay_is_offline(self):
        """fixtures 模式下网络函数必须完全不被调用（注入的 http 也优先被 fixtures 覆盖）。"""
        original = common.HTTP_GET_TEXT

        def _boom(*args, **kwargs):
            raise AssertionError("fixtures 回放禁止联网：%r" % (args,))

        def _stub_boom(url):
            raise AssertionError("fixtures 回放禁止调用 fetch_http：%s" % url)

        common.HTTP_GET_TEXT = _boom
        try:
            jobs, health = sources.fetch_all(fixtures_dir=FIXTURES, fetch_http=_stub_boom)
        finally:
            common.HTTP_GET_TEXT = original
        self.assertGreater(len(jobs), 0)
        self.assertTrue(all(item["ok"] for item in health))

    def test_fixtures_skip_uncovered_sources(self):
        _, health = sources.fetch_all(fixtures_dir=FIXTURES)
        self.assertEqual(len(health), 6, "manifest 只覆盖 6 个源，其余应跳过而不是记失败")
        self.assertEqual({item["source_id"] for item in health},
                         {"gh_coinbase", "lv_anchorage", "as_kraken", "cjl_rss", "web3career", "remoteok"})

    def test_manifest_maps_sources(self):
        with open(os.path.join(FIXTURES, "manifest.json"), "r", encoding="utf-8") as handle:
            manifest = json.load(handle)
        self.assertEqual(manifest.get("schema_version"), 1)
        entries = manifest.get("sources") or {}
        self.assertEqual(len(entries), 6)
        for source_id, entry in entries.items():
            filename = entry["file"] if isinstance(entry, dict) else entry
            self.assertTrue(os.path.isfile(os.path.join(FIXTURES, filename)), source_id)
            self.assertLessEqual(os.path.getsize(os.path.join(FIXTURES, filename)), 200 * 1024,
                                 "%s fixture 必须 <200KB" % source_id)

    def test_limit_and_only(self):
        jobs, health = sources.fetch_all(fixtures_dir=FIXTURES, limit=2, only="gh_coinbase,cjl_rss")
        self.assertEqual([item["source_id"] for item in health], ["gh_coinbase", "cjl_rss"])
        self.assertTrue(all(item["count"] <= 2 for item in health))
        self.assertLessEqual(len(jobs), 4)

    def test_only_missing_source(self):
        jobs, health = sources.fetch_all(fixtures_dir=FIXTURES, only="does_not_exist")
        self.assertEqual(jobs, [])
        self.assertEqual(health, [])

    def test_failure_degrades(self):
        cfg = {
            "schema_version": 1,
            "sources": [
                {"id": "good", "tier": "T0", "type": "greenhouse", "url": "https://example.com/good"},
                {"id": "bad", "tier": "T0", "type": "greenhouse", "url": "https://example.com/bad"},
            ],
        }
        jobs, health = sources.fetch_all(sources_config=cfg, fetch_http=_stub({"example.com/good": GH_PAYLOAD}))
        self.assertEqual(len(health), 2)
        by_id = {item["source_id"]: item for item in health}
        self.assertTrue(by_id["good"]["ok"])
        self.assertEqual(by_id["good"]["count"], 1)
        self.assertFalse(by_id["bad"]["ok"])
        self.assertEqual(by_id["bad"]["count"], 0)
        self.assertIn("network is down", by_id["bad"]["error"])
        self.assertEqual(len(jobs), 1, "单源失败不影响其他源产出")

    def test_bad_payload_degrades(self):
        cfg = {
            "schema_version": 1,
            "sources": [{"id": "broken", "tier": "T0", "type": "greenhouse", "url": "https://example.com/x"}],
        }
        jobs, health = sources.fetch_all(sources_config=cfg, fetch_http=lambda url: "not json")
        self.assertEqual(jobs, [])
        self.assertFalse(health[0]["ok"])
        self.assertTrue(health[0]["error"])

    def test_description_truncation(self):
        source = {"id": "trunc", "tier": "T0", "type": "greenhouse"}
        payload = json.dumps({"jobs": [dict(GH_JOB, content="x" * 30000)]})
        parsed = source_adapters.parse_source(source, payload)
        job = sources._finalize(parsed[0], source, 123)
        self.assertEqual(len(job["description_text"]), 123)
        jobs = sources.run_source(source, payload, description_max_chars=64)
        self.assertLessEqual(len(jobs[0]["description_text"]), 64)

    def test_fetch_http_accepts_bytes(self):
        cfg = {"schema_version": 1, "sources": [
            {"id": "gh", "tier": "T0", "type": "greenhouse", "url": "https://example.com/gh"}]}
        jobs, health = sources.fetch_all(sources_config=cfg, fetch_http=lambda url: GH_PAYLOAD.encode("utf-8"))
        self.assertTrue(health[0]["ok"])
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["remote"], "remote")
        self.assertEqual(jobs[0]["company"], "Acme")


if __name__ == "__main__":
    unittest.main(verbosity=2)
