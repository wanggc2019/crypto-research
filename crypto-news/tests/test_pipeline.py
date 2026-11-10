#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单元测试（零依赖，纯 unittest）。运行：
    .venv/bin/python -m unittest discover -s tests -v
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import common  # noqa: E402
import fetch  # noqa: E402
import score  # noqa: E402


class TestConfig(unittest.TestCase):
    """配置文件必须能被解析、结构正确。"""

    def test_all_yaml_parse(self):
        cfg_dir = os.path.join(ROOT, "config")
        files = [f for f in os.listdir(cfg_dir) if f.endswith(".yaml")]
        self.assertGreaterEqual(len(files), 7)
        for f in files:
            with self.subTest(file=f):
                data = common.load_config(f)
                self.assertIsInstance(data, dict)

    def test_watchlist_assets(self):
        wl = common.load_config("watchlist.yaml")
        self.assertEqual([a["symbol"] for a in wl["assets"]], ["BTC", "ETH", "BNB"])
        self.assertEqual([a["symbol"] for a in wl.get("paused", [])], ["SOL", "ASTER"])

    def test_sources_valid(self):
        srcs = common.load_config("sources.yaml")["sources"]
        ids = [s["id"] for s in srcs]
        self.assertEqual(len(ids), len(set(ids)), "source id 不能重复")
        allowed = {"rss", "json_api", "html_diff", "market_api", "statuspage", "warn"}
        for s in srcs:
            with self.subTest(source=s["id"]):
                self.assertTrue(s["url"].startswith("http"), "url 必须是绝对地址")
                self.assertIn(s["type"], allowed)
                self.assertIn(s["tier"], {"T0", "T1", "T2", "T3"})

    def test_thresholds_weights(self):
        t = common.load_config("thresholds.yaml")
        w = t["scoring"]["weights"]
        self.assertAlmostEqual(sum(w.values()), 1.0, places=6)
        self.assertGreater(t["scoring"]["levels"]["P0"], t["scoring"]["levels"]["P1"])
        self.assertIn("hard_triggers", t)

    def test_people_routes(self):
        for p in common.load_config("people.yaml")["people"]:
            with self.subTest(person=p["id"]):
                if p.get("verified"):
                    self.assertTrue(p.get("x"), "已验证的高管必须有 X handle")
                self.assertIn("sensitivity", p)


class TestWarnFilings(unittest.TestCase):
    """WARN 公开裁员备案解析与交易所过滤。"""

    def setUp(self):
        import datetime
        d = datetime.date.today()
        self.posted = (d - datetime.timedelta(days=2)).strftime("%m/%d/%Y")
        self.dated = (d - datetime.timedelta(days=4)).strftime("%m/%d/%Y")
        self.expected_iso = (d - datetime.timedelta(days=4)).isoformat()
        self.NY_HTML = (
            "<table><tr><th>Company Name</th><th>Region</th><th>Date Posted</th>"
            "<th>Notice Dated</th></tr>"
            "<tr><td>Coinbase, Inc.</td><td>New York City</td>"
            "<td>%s</td><td>%s</td></tr>"
            "<tr><td>Random Bakery LLC</td><td>Capital</td>"
            "<td>%s</td><td>%s</td></tr></table>" % (self.posted, self.dated, self.posted, self.dated)
        )

    def test_parse_ny_warn(self):
        rows = fetch.parse_ny_warn(self.NY_HTML.encode("utf-8"))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["company"], "Coinbase, Inc.")
        self.assertEqual(rows[0]["region"], "New York City")

    def test_warn_iso_date(self):
        self.assertEqual(fetch._warn_iso_date("12/27/2024"), "2024-12-27")
        self.assertEqual(fetch._warn_iso_date("12/16/2024 (Amended 3/17/2025)"), "2024-12-16")
        self.assertEqual(fetch._warn_iso_date("2024-12-27"), "2024-12-27")
        self.assertEqual(fetch._warn_iso_date("n/a"), "")

    def _make_xlsx(self):
        import io
        import zipfile
        strs = ["County/Parish", "Notice\nDate", "Processed\nDate", "Effective\nDate",
                "Company", "Layoff/\nClosure", "No. Of\nEmployees", "Address", "Related Industry",
                "Los Angeles County", "Coinbase, Inc.", "Layoff Permanent", "1 Main St", "54 Professional"]
        sst = ('<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">' +
               "".join("<si><t>%s</t></si>" % x for x in strs) + "</sst>")

        def cell(ref, idx, s=True, num=None):
            if s:
                return '<c r="%s" t="s"><v>%d</v></c>' % (ref, idx)
            return '<c r="%s"><v>%s</v></c>' % (ref, num)

        header = "".join(cell(chr(65 + i) + "1", i) for i in range(9))
        data = (cell("A2", 9) + cell("B2", 0, False, 46203) + cell("C2", 0, False, 46204) +
                cell("D2", 0, False, 46266) + cell("E2", 10) + cell("F2", 11) +
                cell("G2", 0, False, 100) + cell("H2", 12) + cell("I2", 13))
        sheet = ('<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                 "<sheetData><row r=\"1\">%s</row><row r=\"2\">%s</row></sheetData></worksheet>") % (header, data)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("xl/sharedStrings.xml", sst)
            z.writestr("xl/worksheets/sheet2.xml", sheet)
        return buf.getvalue()

    def test_parse_ca_warn_xlsx(self):
        rows = fetch.parse_ca_warn_xlsx(self._make_xlsx())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["company"], "Coinbase, Inc.")
        self.assertEqual(rows[0]["employees"], "100")
        self.assertEqual(rows[0]["notice_date"], fetch._excel_date(46203))

    def test_warn_items_filter_exchange_employers(self):
        src = {"id": "warn_ny", "url": "https://dol.ny.gov/warn-notices"}
        found = fetch.parse_ny_warn(self.NY_HTML)
        items = fetch._warn_items(src, {}, found, "ny")
        self.assertEqual(len(items), 1)
        self.assertIn("Coinbase", items[0]["title"])
        self.assertEqual(items[0]["published_at"], self.expected_iso)
        # 第二次同样输入 → 去重为空
        state = {items[0]["id"]: 1}
        self.assertEqual(fetch._warn_items(src, {"seen": state}, found, "ny"), [])


class TestLaborLeads(unittest.TestCase):
    """组织动向（裁员/重组/高管离职）：只作 P2 线索，确认后最高 P1。"""

    def test_labor_sources_configured(self):
        src = common.load_config("sources.yaml")
        ids = {x.get("id") for x in src.get("sources", [])}
        self.assertIn("gnews_layoffs_en", ids)
        self.assertIn("gnews_layoffs_cn", ids)
        for x in src.get("sources", []):
            if x.get("id", "").startswith("gnews_layoffs"):
                self.assertEqual(x.get("tier"), "T3")
                self.assertTrue(x.get("verified"))

    def test_labor_keywords_configured(self):
        ent = common.load_config("entities.yaml")
        kws = (ent.get("event_keywords") or {}).get("industry_labor") or []
        for k in ("裁员", "layoff", "restructuring"):
            self.assertIn(k, kws)
        self.assertIn("Binance", (ent.get("employers") or {}))


class TestExtractAmount(unittest.TestCase):
    def test_usd_suffixes(self):
        cases = {
            "$6.3 billion": 6.3e9,
            "$124M": 124e6,
            "$2.5 million": 2.5e6,
            "63 亿美元": 6.3e9,
            "5000万美元": 5e7,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                got = fetch.extract_amount_usd(text)
                self.assertIsNotNone(got, text)
                self.assertAlmostEqual(got, expected, delta=expected * 0.01)

    def test_no_amount(self):
        self.assertIsNone(fetch.extract_amount_usd("no numbers here at all"))


class TestClassifyAndHardTriggers(unittest.TestCase):
    def setUp(self):
        self.keywords = common.load_config("entities.yaml")["event_keywords"]

    def test_classify_security(self):
        cat, _ = score.classify("Binance hacked, funds stolen and drained", self.keywords)
        self.assertEqual(cat, "protocol_security")

    def test_classify_regulatory(self):
        cat, _ = score.classify("SEC files lawsuit against exchange, settlement talks", self.keywords)
        self.assertEqual(cat, "regulatory")

    def test_hack_requires_amount(self):
        big = score.hard_triggers("Binance hack $6.3 billion stolen", {"usd": 6.3e9}, ["BTC"], [])
        self.assertIn("hack_large", big)
        small = score.hard_triggers("a small hack happened", {"usd": 0}, ["BTC"], [])
        self.assertNotIn("hack_large", small)

    def test_sec_word_boundary(self):
        # "security" 不能误触发 sec_head_exchange
        hits = score.hard_triggers("security audit completed for binance", {}, ["BNB"], [])
        self.assertNotIn("sec_head_exchange", hits)
        real = score.hard_triggers("SEC sues binance over violations", {}, ["BNB"], [])
        self.assertIn("sec_head_exchange", real)

    def test_fomc_needs_surprise(self):
        self.assertIn("fomc_surprise", score.hard_triggers("Fed surprise emergency rate cut", {}, ["BTC"], []))
        self.assertNotIn("fomc_surprise", score.hard_triggers("Fed expected to cut rates", {}, ["BTC"], []))

    def test_stablecoin_depeg(self):
        self.assertIn("stablecoin_depeg", score.hard_triggers("USDT 脱锚 300bps", {}, ["BTC"], []))

    def test_executive_legal(self):
        people = [{"name": "CZ", "x": "cz_binance"}]
        self.assertIn("executive_legal", score.hard_triggers("CZ 被起诉", {}, ["BNB"], people))
        self.assertNotIn("executive_legal", score.hard_triggers("CZ posts about BNB", {}, ["BNB"], people))


class TestClustering(unittest.TestCase):
    def _item(self, title, body="", source="s1", tier="T1"):
        return {
            "title": title, "body": body, "source_id": source, "tier": tier,
            "url": "https://example.com/" + source,
            "published_at": common.ts_iso(), "kind": "news",
            "_sh": score.simhash(title),
        }

    def test_same_event_merges(self):
        items = [
            self._item("Binance hackers swapped stolen tokens for bitcoin", "binance hackers stolen tokens"),
            self._item("Binance hackers swapped stolen tokens for bitcoin", "binance hackers stolen tokens"),
        ]
        self.assertEqual(len(score.cluster_items(items)), 1)

    def test_different_events_not_merged(self):
        items = [
            self._item("Ethereum Fusaka upgrade lands today", "ethereum upgrade fusaka"),
            self._item("SEC approves bitcoin ETF", "sec approves bitcoin etf"),
        ]
        self.assertGreaterEqual(len(score.cluster_items(items)), 2)


class TestScoring(unittest.TestCase):
    def _item(self, title, tier="T0", source="binance_ann", body=""):
        return {
            "title": title, "body": body, "tier": tier, "source_id": source,
            "url": "https://example.com", "published_at": common.ts_iso(), "kind": "announcement",
            "_sh": score.simhash(title), "_assets": ["BNB"],
        }

    def test_official_single_source_high_confirmation(self):
        t = common.load_config("thresholds.yaml")
        cluster = [self._item("Binance will list BTC and ETH pairs")]
        s = score.score_cluster(cluster, t, None)
        self.assertEqual(s["components"]["C"], 100.0)
        self.assertGreater(s["score"], 50)

    def test_count_distinct_sources(self):
        t = common.load_config("thresholds.yaml")
        cluster = [self._item("Binance suspends withdrawals", source="binance_ann"),
                   self._item("Binance suspends withdrawals", source="binance_ann")]
        s = score.score_cluster(cluster, t, None)
        self.assertEqual(s["count"], 1, "同源重复不应虚增确认度")



class TestDigestPoints(unittest.TestCase):
    TITLE = "美国中期选举结果引发市场大跌、香港通过 Crypto 报税框架、币安披露 63 亿美金被盗原因与新闻 Top10"

    def test_split_title_and_rank_by_importance(self):
        pts = score.extract_points(self.TITLE, "")
        self.assertEqual(len(pts), 3)
        self.assertIn("被盗", pts[0])          # 重要事件排第一
        self.assertIn("大跌", pts[1])
        self.assertIn("报税", pts[2])

    def test_numbered_body_points(self):
        body = "1. 币安恢复提现，链上净流出 36 亿美元\n2. SEC 起诉某交易所\n3. BTC 现货 ETF 单日净流入 5 亿"
        pts = score.extract_points("每日精选", body)
        self.assertGreaterEqual(len(pts), 2)
        self.assertTrue(any("SEC" in p or "ETF" in p for p in pts))

    def test_non_digest_returns_empty(self):
        self.assertEqual(score.extract_points("Binance pauses withdrawals", "short body"), [])


class TestPageUpdateFetch(unittest.TestCase):
    """html_diff 只取可见文本；CSS/JS/JSON-LD 垃圾页面变化直接忽略。"""

    def _collect(self, html):
        orig = fetch.http_get
        fetch.http_get = lambda url, **kw: html.encode("utf-8")
        try:
            return fetch.collect_html({"id": "x", "name": "X", "url": "https://x.test"}, {})
        finally:
            fetch.http_get = orig

    def test_junk_page_skipped(self):
        junk = "<html><style>--chakra-ring:1;var(--x:y)</style><script>function(){};</script></html>"
        items, h = self._collect(junk)
        self.assertEqual(items, [])
        self.assertTrue(h)

    def test_meaningful_page_kept_without_tags(self):
        page = "<html><script>var x=1</script><body>" + ("BNB Chain announces network upgrade. " * 20) + "</body></html>"
        items, h = self._collect(page)
        self.assertEqual(len(items), 1)
        self.assertNotIn("<", items[0]["body"])
        self.assertIn("BNB Chain announces", items[0]["body"])
        self.assertEqual(items[0]["kind"], "page_update")


class TestUSMacro(unittest.TestCase):
    """美股/宏观新闻：只保留市场级事件；无币种关联最高 P2。"""

    def _thresholds(self):
        return {
            "scoring": {
                "levels": {"P0": 78, "P1": 58, "P2": 35},
                "hard": {"window_hours": 48, "stale_window_hours": 96, "min_decay": 0.1},
            }
        }

    def _scored(self, **kw):
        base = {"score": 90.0, "hard": [], "source_tiers": {}, "age_hours": 2.0,
                "components": {"D": 0.9}, "backfill": False, "level": None}
        base.update(kw)
        return base

    def test_macro_matches_market_wide_events(self):
        self.assertTrue(score.is_macro_news("Nasdaq plunges 3% as tech selloff deepens"))
        self.assertTrue(score.is_macro_news("Nvidia-led tech selloff weighs on crypto majors"))
        self.assertTrue(score.is_macro_news("Fed cuts rates as CPI cools"))

    def test_macro_ignores_single_stock_daily(self):
        self.assertFalse(score.is_macro_news("Apple unveils new iPhone at annual event"))
        self.assertFalse(score.is_macro_news("Tesla recalls 1,000 cars"))

    def test_macro_without_asset_capped_p2(self):
        s = self._scored(category="macro", score=90.0, source_tiers={"theblock": "T1"})
        score.grade_scored([s], self._thresholds())
        self.assertEqual(s["level"], "P2")

    def test_macro_with_asset_max_p1(self):
        s = self._scored(category="macro", score=90.0, source_tiers={"theblock": "T1"}, assets=["BTC"])
        score.grade_scored([s], self._thresholds())
        self.assertEqual(s["level"], "P1")

    def test_macro_headline_gets_cn_summary(self):
        out = score.cn_summary("Bitcoin loses 50-week moving average as 24-hour crypto long liquidations top $1 billion")
        self.assertIn("跌破 50 周均线", out)
        self.assertIn("10 亿美元", out)


class TestGrading(unittest.TestCase):
    """硬条件升级必须过「确认 + 时效 + 衰减」三道门（防标题党/旧闻抬成 P0）。"""

    def _thresholds(self):
        return {
            "scoring": {
                "levels": {"P0": 78, "P1": 58, "P2": 35},
                "hard": {"window_hours": 48, "stale_window_hours": 96, "min_decay": 0.1},
            }
        }

    def _scored(self, **kw):
        base = {
            "score": 90.0,
            "hard": [],
            "source_tiers": {},
            "age_hours": 2.0,
            "components": {"D": 0.9},
            "backfill": False,
            "level": None,
        }
        base.update(kw)
        return base

    def test_official_single_source_is_p0(self):
        s = self._scored(hard=["hack_large"], source_tiers={"binance_ann": "T0"})
        score.grade_scored([s], self._thresholds())
        self.assertTrue(s["confirmed"])
        self.assertEqual(s["level"], "P0")

    def test_two_independent_media_is_p0(self):
        s = self._scored(hard=["hack_large"], source_tiers={"theblock": "T1", "coindesk": "T1"})
        score.grade_scored([s], self._thresholds())
        self.assertTrue(s["confirmed"])
        self.assertEqual(s["level"], "P0")

    def test_aggregator_only_not_p0(self):
        # 真实案例：Google News 聚合站的标题党 + hack_large 硬条件，原来会被抬成 P0
        s = self._scored(hard=["hack_large"], source_tiers={"gnews_btc_eth": "T3"}, score=35.0, age_hours=29.9)
        score.grade_scored([s], self._thresholds())
        self.assertFalse(s["confirmed"])
        self.assertEqual(s["level"], "P2")
        self.assertTrue(s["backfill"])

    def test_t3_hard_high_score_capped_to_p2(self):
        # 聚合站高分硬事件（如"币安 CFO 离职"被 T3 转述）不能靠分数升 P1
        s = self._scored(hard=["executive_legal"], source_tiers={"gnews_layoffs_cn": "T3"},
                         score=90.0, age_hours=5.0, components={"D": 0.8})
        score.grade_scored([s], self._thresholds())
        self.assertFalse(s["confirmed"])
        self.assertEqual(s["level"], "P2")

    def test_single_t1_fresh_is_p1_unconfirmed(self):
        s = self._scored(hard=["hack_large"], source_tiers={"coindesk": "T1"}, score=40.0, age_hours=10.0,
                         components={"D": 0.75})
        score.grade_scored([s], self._thresholds())
        self.assertFalse(s["confirmed"])
        self.assertTrue(s["unconfirmed"])
        self.assertEqual(s["level"], "P1")

    def test_page_update_capped_to_p2(self):
        s = self._scored(kind="page_update", score=90.0, source_tiers={"binance_ann": "T0"})
        score.grade_scored([s], self._thresholds())
        self.assertEqual(s["level"], "P2")

    def test_labor_unconfirmed_capped_to_p2(self):
        s = self._scored(category="industry_labor", hard=[], source_tiers={"coindesk": "T1"}, score=90.0)
        score.grade_scored([s], self._thresholds())
        self.assertEqual(s["level"], "P2")

    def test_labor_confirmed_capped_to_p1(self):
        s = self._scored(category="industry_labor", hard=["executive_legal"],
                         source_tiers={"theblock": "T1", "coindesk": "T1"}, score=95.0)
        score.grade_scored([s], self._thresholds())
        self.assertTrue(s["confirmed"])
        self.assertEqual(s["level"], "P1")

    def test_stale_confirmed_downgrades_to_p1(self):
        s = self._scored(hard=["hack_large"], source_tiers={"binance_ann": "T0"}, age_hours=60.0, components={"D": 0.2})
        score.grade_scored([s], self._thresholds())
        self.assertEqual(s["level"], "P1")

    def test_low_decay_blocks_p0(self):
        s = self._scored(hard=["liquidation_cascade"], source_tiers={"theblock": "T1", "coindesk": "T1"},
                         components={"D": 0.05})
        score.grade_scored([s], self._thresholds())
        self.assertEqual(s["level"], "P1")

    def test_stale_low_score_archived(self):
        # 真实案例：50 小时前的旧清算新闻 + 硬条件，评分 0.2，原来会被推成 P1
        s = self._scored(hard=["liquidation_cascade"], source_tiers={"theblock": "T1"}, score=0.2,
                         age_hours=50.6, components={"D": 0.003})
        score.grade_scored([s], self._thresholds())
        self.assertNotIn(s["level"], ("P0", "P1"))

    def test_no_hard_uses_score(self):
        tiers = {"theblock": "T1"}
        hi = self._scored(score=80.0, source_tiers=tiers)
        mid = self._scored(score=60.0, source_tiers=tiers)
        low = self._scored(score=30.0, source_tiers=tiers)
        score.grade_scored([hi, mid, low], self._thresholds())
        self.assertEqual(hi["level"], "P0")
        self.assertEqual(mid["level"], "P1")
        self.assertEqual(low["level"], "ARCHIVE")

    def test_t3_high_score_capped_p2(self):
        s = self._scored(score=90.0, source_tiers={"gnews_btc_eth": "T3"})
        score.grade_scored([s], self._thresholds())
        self.assertEqual(s["level"], "P2")

    def test_independent_count_excludes_t3(self):
        s = self._scored(source_tiers={"a": "T1", "b": "T3", "c": "T2"})
        self.assertEqual(score.independent_count(s), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
