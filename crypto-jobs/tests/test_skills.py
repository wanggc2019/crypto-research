#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""skills.py 技能抽取层单测（全部离线，不联网、不依赖 LLM）。"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import skills  # noqa: E402

FIXTURE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "jobs")
TAX = skills.load()


def read_fixture(name):
    with open(os.path.join(FIXTURE_DIR, name), "r", encoding="utf-8") as handle:
        return handle.read()


def found(text):
    return set(skills.extract(text, TAX)["skills"])


def required(text):
    return set(skills.extract(text, TAX)["required"])


class TestTaxonomy(unittest.TestCase):
    def test_schema_version(self):
        self.assertEqual(TAX["schema_version"], 1)

    def test_categories(self):
        categories = TAX["categories"]
        self.assertGreaterEqual(len(categories), 10)
        cat_ids = [item["id"] for item in categories]
        self.assertEqual(len(cat_ids), len(set(cat_ids)))
        for item in categories:
            self.assertTrue(item["label"], item["id"])

    def test_skills_structure(self):
        skill_list = TAX["skills"]
        self.assertGreaterEqual(len(skill_list), 120)
        ids = [item["id"] for item in skill_list]
        self.assertEqual(len(ids), len(set(ids)))
        cat_ids = {item["id"] for item in TAX["categories"]}
        for item in skill_list:
            self.assertRegex(item["id"], r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
            self.assertTrue(item["label"], item["id"])
            self.assertIn(item["category"], cat_ids, item["id"])
            self.assertTrue(item["aliases"], item["id"])
            for alias in item["aliases"]:
                self.assertIsInstance(alias, str)
                self.assertTrue(alias.strip(), item["id"])

    def test_required_skill_coverage(self):
        # 任务书要求的覆盖点：按 id 存在性校验（字段名与 skills.yaml 对齐）。
        must_have = [
            # DevOps / 平台
            "kubernetes", "docker", "helm", "terraform", "pulumi", "ansible", "gitops", "argocd",
            "cicd", "github-actions", "gitlab-ci", "jenkins", "linux", "dns", "load-balancing",
            "nginx", "envoy",
            # SRE / 可观测
            "prometheus", "grafana", "datadog", "opentelemetry", "sentry", "slo-sli", "on-call",
            "incident-management", "chaos-engineering", "capacity-planning", "disaster-recovery",
            "multi-region",
            # MLOps / AI Infra
            "mlflow", "kubeflow", "ray", "sagemaker", "vertex-ai", "feature-store", "model-serving",
            "vllm", "triton", "cuda", "gpu", "distributed-training", "llm", "rag", "vector-database",
            "fine-tuning", "rlhf", "huggingface",
            # 数据
            "sql", "postgresql", "kafka", "flink", "spark", "clickhouse", "redis", "elasticsearch",
            "airflow", "dbt",
            # 语言
            "python", "go", "rust", "typescript", "javascript", "java", "cpp", "bash", "solidity",
            "move",
            # 加密行业特有
            "ethereum", "evm", "solana", "bitcoin", "cosmos", "substrate", "node-operations",
            "validator", "staking", "rpc-node", "archive-node", "indexer", "subgraph", "mev", "l2",
            "rollup", "bridge", "oracle", "chainlink", "custody", "hsm", "fireblocks", "kms",
            "cold-wallet", "matching-engine", "low-latency", "market-data", "consensus-mechanism",
            "p2p", "mempool",
            # 安全合规
            "soc2", "iso27001", "iam", "vault", "threat-modeling", "penetration-testing", "siem",
            "zero-trust", "appsec", "cloud-security",
        ]
        missing = [skill_id for skill_id in must_have if skill_id not in set(skills.ids(TAX))]
        self.assertEqual(missing, [], "技能分类法缺少: %s" % missing)

    def test_drilldown_ids(self):
        drilldown = TAX.get("drilldown") or {}
        self.assertIn("ai", drilldown)
        skill_ids = set(skills.ids(TAX))
        for parent, children in drilldown.items():
            self.assertIn(parent, skill_ids)
            self.assertTrue(children)
            for child in children:
                self.assertIn(child, skill_ids)

    def test_each_skill_detected_by_label_or_first_alias(self):
        misses = []
        for item in TAX["skills"]:
            candidates = [item["label"], item["aliases"][0]]
            if not any(item["id"] in found(candidate) for candidate in candidates):
                misses.append(item["id"])
        self.assertEqual(misses, [], "以下技能的 label/第一个 alias 无法命中自身: %s" % misses)


class TestExtractContract(unittest.TestCase):
    def test_result_shape(self):
        result = skills.extract("Requirements:\n- Kubernetes\n- Python\n", TAX)
        self.assertEqual(set(result), {"skills", "required", "counts", "by_category"})
        self.assertIn("kubernetes", result["skills"])
        self.assertIn("kubernetes", result["required"])
        self.assertEqual(result["counts"]["kubernetes"], 1)
        self.assertIn("kubernetes", result["by_category"]["container"])

    def test_deterministic(self):
        text = read_fixture("crypto_sre.txt")
        self.assertEqual(skills.extract(text, TAX), skills.extract(text, TAX))

    def test_empty_input(self):
        empty = {"skills": [], "required": [], "counts": {}, "by_category": {}}
        self.assertEqual(skills.extract("", TAX), empty)
        self.assertEqual(skills.extract(None, TAX), empty)

    def test_ids_and_lookup(self):
        self.assertEqual(len(skills.ids(TAX)), len(TAX["skills"]))
        self.assertEqual(skills.ids(TAX), [item["id"] for item in TAX["skills"]])
        self.assertEqual(skills.category_of("kubernetes"), "container")
        self.assertEqual(skills.label_of("kubernetes"), "Kubernetes")
        with self.assertRaises(KeyError):
            skills.category_of("no-such-skill")

    def test_skills_unique_and_counts_consistent(self):
        result = skills.extract(read_fixture("crypto_mlops.txt"), TAX)
        self.assertEqual(len(result["skills"]), len(set(result["skills"])))
        self.assertEqual(set(result["skills"]), set(result["counts"]))
        self.assertTrue(set(result["required"]) <= set(result["skills"]))
        for skill_id in result["skills"]:
            self.assertGreaterEqual(result["counts"][skill_id], 1)
            self.assertIn(skills.category_of(skill_id), result["by_category"])

    def test_load_custom_path(self):
        # load() 显式路径与默认路径结果一致（同一份文件）
        tax = skills.load(skills.DEFAULT_TAXONOMY_PATH)
        self.assertEqual(skills.ids(tax), skills.ids(TAX))

    def test_load_missing_path(self):
        with self.assertRaises(FileNotFoundError):
            skills.load(os.path.join(FIXTURE_DIR, "does-not-exist.yaml"))


class TestAmbiguity(unittest.TestCase):
    """短词/歧义词不能误伤（任务书点名的 go/rust/r/c/ml/ai/k8s 等）。"""

    def test_go_not_verb(self):
        self.assertNotIn("go", found("go to market strategy"))
        self.assertNotIn("go", found("we have a go-to-market plan"))
        self.assertNotIn("go", found("go live next quarter"))
        self.assertIn("go", found("Python, Go, Rust"))
        self.assertIn("go", found("Go developer building backend services"))
        self.assertIn("go", found("golang and rustlang experience"))

    def test_rust_not_object(self):
        self.assertNotIn("rust", found("rust prevention and rust-proof coating"))
        self.assertIn("rust", found("Rust developer"))
        self.assertIn("rust", found("Rust, Solana, Move"))

    def test_r_statistical_language(self):
        self.assertIn("r-lang", found("Python, R, SQL"))
        self.assertIn("r-lang", found("R programming and RStudio"))
        self.assertNotIn("r-lang", found("R&D team"))
        self.assertNotIn("r-lang", found("R&R for this role"))
        self.assertNotIn("r-lang", found("R and R responsibilities"))
        self.assertNotIn("r-lang", found("R-squared value of the model"))

    def test_c_vs_cpp_and_executive(self):
        self.assertIn("c", found("Python, C, C++"))
        self.assertIn("cpp", found("C++ and Rust"))
        self.assertNotIn("c", found("C-suite executives at C-level"))
        self.assertNotIn("c", found("gRPC and C# services"))

    def test_move_verb(self):
        self.assertIn("move", found("Solidity, Move, Rust"))
        self.assertIn("move", found("Move language smart contracts"))
        self.assertNotIn("move", found("please move fast and move to production"))

    def test_ray_xray(self):
        self.assertIn("ray", found("Ray cluster with Ray Serve"))
        self.assertNotIn("ray", found("X-ray imaging"))

    def test_spark_verb(self):
        self.assertIn("spark", found("Apache Spark jobs"))
        self.assertNotIn("spark", found("join us and spark innovation"))

    def test_l2_vs_cache(self):
        self.assertIn("l2", found("Layer 2 rollups and L2"))
        self.assertNotIn("l2", found("L2 cache and L2 data"))

    def test_ml_and_ai(self):
        self.assertIn("machine-learning", found("ML pipeline with AI/ML"))
        self.assertIn("ai", found("AI/ML platform"))
        self.assertNotIn("machine-learning", found("HTML and CSS only"))
        self.assertNotIn("ai", found("OpenAI and Airtable integrations"))
        self.assertIn("mlops", found("MLOps platform"))

    def test_bridge_and_consensus_context(self):
        self.assertIn("bridge", found("cross-chain bridge monitoring"))
        self.assertNotIn("bridge", found("bridge the gap between teams"))
        self.assertIn("consensus-mechanism", found("consensus client and consensus layer"))
        self.assertNotIn("consensus-mechanism", found("drive consensus among stakeholders"))

    def test_oracle_vs_database(self):
        self.assertIn("oracle", found("Chainlink oracle network"))
        self.assertNotIn("oracle", found("Oracle database administration"))

    def test_k8s(self):
        self.assertIn("kubernetes", found("k8s and kubectl"))
        self.assertIn("kubernetes", found("容器编排平台"))


class TestRequiredSections(unittest.TestCase):
    def test_english_heading_variants(self):
        headings = [
            "Requirements",
            "Qualifications",
            "Minimum Qualifications",
            "Basic Qualifications",
            "What you'll need",
            "You have",
            "Must have",
            "Who you are",
            "Your background",
            "Skills and experience",
        ]
        for heading in headings:
            text = "%s:\n- Kubernetes\n- Python\n" % heading
            self.assertTrue({"kubernetes", "python"} <= required(text), heading)

    def test_nice_headings_are_not_required(self):
        nice = ["Preferred Qualifications", "Nice to have", "Bonus points", "Desired skills", "加分项", "优先条件"]
        for heading in nice:
            text = "Requirements:\n- Python\n\n%s:\n- Rust\n" % heading
            self.assertIn("python", required(text), heading)
            self.assertNotIn("rust", required(text), heading)

    def test_chinese_heading_variants(self):
        headings = ["任职要求", "岗位要求", "必备条件", "任职资格"]
        for heading in headings:
            text = "%s：\n- 容器编排\n- 可观测性\n- 节点运维\n" % heading
            self.assertTrue({"kubernetes", "observability", "node-operations"} <= required(text), heading)

    def test_no_heading_means_no_required(self):
        text = "We use Kubernetes, Python and Terraform every day."
        result = skills.extract(text, TAX)
        self.assertTrue(result["skills"])
        self.assertEqual(result["required"], [])

    def test_responsibilities_before_requirements(self):
        text = (
            "Responsibilities:\n- Run Kubernetes clusters\n\n"
            "Requirements:\n- Python\n- Terraform\n"
        )
        self.assertNotIn("kubernetes", required(text))
        self.assertTrue({"python", "terraform"} <= required(text))

    def test_section_ends_at_blank_block(self):
        text = (
            "Requirements:\n- Kubernetes\n\n"
            "Our stack also includes Rust and Terraform.\n\n"
            "Nice to have:\n- Python\n"
        )
        self.assertIn("kubernetes", required(text))
        self.assertNotIn("terraform", required(text))
        self.assertNotIn("rust", required(text))
        self.assertNotIn("python", required(text))

    def test_flattened_single_line(self):
        text = (
            "Requirements: 4+ years SRE with Kubernetes, Python and Terraform. "
            "Nice to have: Rust and Solana. Benefits: great office."
        )
        got = required(text)
        self.assertTrue({"kubernetes", "python", "terraform", "sre"} <= got)
        self.assertNotIn("rust", got)
        self.assertNotIn("solana", got)

    def test_markdown_headings(self):
        text = (
            "## Requirements\n\n- Kubernetes\n- Go\n\n"
            "## Nice to have\n\n- Rust\n"
        )
        got = required(text)
        self.assertTrue({"kubernetes", "go"} <= got)
        self.assertNotIn("rust", got)

    def test_uppercase_and_plural_headings(self):
        text = "WHAT YOU BRING - Kubernetes and Python. NICE TO HAVES - Rust and Solana."
        got = required(text)
        self.assertTrue({"kubernetes", "python"} <= got)
        self.assertFalse({"rust", "solana"} & got)

    def test_html_headings(self):
        text = (
            "<h2>Requirements</h2><ul><li>Kubernetes</li><li>Go</li></ul>"
            "<h2>Nice to have</h2><ul><li>Rust</li></ul>"
        )
        got = required(text)
        self.assertTrue({"kubernetes", "go"} <= got)
        self.assertNotIn("rust", got)


class TestHtmlAndUrl(unittest.TestCase):
    def test_urls_do_not_create_matches(self):
        text = "Docs: https://kubernetes.io/docs (see the Python client)"
        got = found(text)
        self.assertIn("python", got)
        self.assertNotIn("kubernetes", got)

    def test_html_urls_and_tags_do_not_create_matches(self):
        text = '<p>See <a href="https://golang.org/doc">the docs</a> for our <strong>Linux</strong> fleet.</p>'
        got = found(text)
        self.assertIn("linux", got)
        self.assertNotIn("go", got)

    def test_plain_words_inside_other_words(self):
        self.assertNotIn("machine-learning", found("HTML and CSS"))  # HTML 里的 ml
        self.assertNotIn("rust", found("trust and security"))  # trust 里的 rust
        self.assertIn("redis", found("Redis Cluster"))
        self.assertNotIn("redis", found("clustering and sharding"))

    def test_hyphenated_spellings(self):
        self.assertTrue({"iac", "terraform"} <= found("infrastructure-as-code with Terraform"))
        self.assertTrue({"feature-store", "model-serving"} <= found("feature-store, model-serving"))
        self.assertTrue({"low-latency", "market-data"} <= found("low-latency market-data feeds"))
        self.assertIn("multi-region", found("multi-region failover"))
        self.assertNotIn("go", found("go-to-market strategy"))


class TestFixtureJDs(unittest.TestCase):
    def test_flattened_fixtures_match_line_mode(self):
        # 线上 sources 层用 clean_text 把 JD 压成单行，required 结果应与带换行的原文一致。
        name = "crypto_devops.txt"
        text = read_fixture(name)
        flat = " ".join(text.split())
        self.assertEqual(
            set(skills.extract(text, TAX)["required"]),
            set(skills.extract(flat, TAX)["required"]),
            name,
        )
        # "Rust is a plus - ..." 这类正文不能被当成段落标题误切
        plus_text = "Requirements - Python and SQL; Go or Rust is a plus - Nice to have - Solidity"
        got = required(plus_text)
        self.assertTrue({"python", "go", "rust"} <= got)
        self.assertNotIn("solidity", got)

    def test_crypto_sre_fixture(self):
        text = read_fixture("crypto_sre.txt")
        result = skills.extract(text, TAX)
        got_required = set(result["required"])
        got_skills = set(result["skills"])
        # 硬性要求段里的关键技能
        for skill_id in [
            "sre", "kubernetes", "docker", "linux", "tcp-ip", "dns", "load-balancing", "nginx",
            "terraform", "ansible", "aws", "prometheus", "grafana", "opentelemetry", "on-call",
            "incident-management", "postmortem", "python", "go", "ethereum", "validator", "staking",
            "archive-node",
        ]:
            self.assertIn(skill_id, got_required, "crypto_sre required 缺少 %s" % skill_id)
        # nice-to-have 里才出现的技能：抽得到但不算 required
        for skill_id in ["rust", "solana", "mev", "low-latency", "hsm", "fireblocks", "custody"]:
            self.assertIn(skill_id, got_skills, "crypto_sre skills 缺少 %s" % skill_id)
            self.assertNotIn(skill_id, got_required, "crypto_sre nice-to-have 误入 required: %s" % skill_id)
        # 公司介绍里的干扰词（go-to-market / R&D / C-suite）不误伤
        self.assertEqual(result["counts"]["go"], 1)
        self.assertNotIn("c", got_skills)
        self.assertNotIn("r-lang", got_skills)
        # 岗位职责里的技能不能被算成硬性要求
        for skill_id in ["rpc-node", "execution-client", "beacon-chain", "market-data", "matching-engine"]:
            self.assertIn(skill_id, got_skills)
            self.assertNotIn(skill_id, got_required)

    def test_crypto_devops_fixture(self):
        text = read_fixture("crypto_devops.txt")
        result = skills.extract(text, TAX)
        got_required = set(result["required"])
        got_skills = set(result["skills"])
        for skill_id in [
            "devops", "platform-engineering", "kubernetes", "helm", "terraform", "ansible", "iac",
            "aws", "gcp", "linux", "prometheus", "grafana", "opentelemetry", "observability",
            "ethereum", "node-operations", "archive-node", "beacon-chain", "execution-client",
            "vault", "iam", "kms", "soc2", "iso27001", "kyc-aml",
        ]:
            self.assertIn(skill_id, got_required, "crypto_devops required 缺少 %s" % skill_id)
        # 加分项/职责段里的技能：有命中但不算 required
        for skill_id in ["staking", "matching-engine", "low-latency", "rust", "go", "indexer"]:
            self.assertIn(skill_id, got_skills, "crypto_devops skills 缺少 %s" % skill_id)
            self.assertNotIn(skill_id, got_required, "crypto_devops 误入 required: %s" % skill_id)
        # 中文职责段落技能命中
        self.assertTrue({"kubernetes", "observability", "node-operations"} <= got_skills)

    def test_crypto_mlops_fixture(self):
        text = read_fixture("crypto_mlops.txt")
        result = skills.extract(text, TAX)
        got_required = set(result["required"])
        got_skills = set(result["skills"])
        for skill_id in [
            "mlops", "python", "sql", "mlflow", "kubeflow", "model-serving", "cuda", "gpu",
            "distributed-training", "llm", "rag", "fine-tuning", "rlhf", "feature-store",
            "vector-database", "airflow", "kafka", "clickhouse", "postgresql", "kubernetes",
            "docker", "terraform", "aws", "prometheus", "grafana", "slo-sli",
        ]:
            self.assertIn(skill_id, got_required, "crypto_mlops required 缺少 %s" % skill_id)
        for skill_id in ["solidity", "smart-contracts", "ethereum", "mev", "market-data", "hsm", "custody"]:
            self.assertIn(skill_id, got_skills, "crypto_mlops skills 缺少 %s" % skill_id)
            self.assertNotIn(skill_id, got_required, "crypto_mlops nice-to-have 误入 required: %s" % skill_id)
        # 职责段技能不应算 required
        for skill_id in ["pytorch", "huggingface", "github-actions", "dbt", "vllm", "triton"]:
            self.assertIn(skill_id, got_skills)
            self.assertNotIn(skill_id, got_required)


class TestDevSkills(unittest.TestCase):
    """开发岗位技能（2026-11-09 纳入开发岗位后新增的 Web/移动/链上开发技能）。"""

    def test_web_backend_stack(self):
        got = found("React, Node.js, GraphQL, Kafka, PostgreSQL, Spring Boot, microservices")
        for sid in ("react", "nodejs", "graphql", "kafka", "postgresql", "spring", "microservices"):
            self.assertIn(sid, got)

    def test_js_filename_not_javascript(self):
        self.assertNotIn("javascript", found("Node.js and Next.js and web3.js"))
        self.assertNotIn("javascript", found("React.js and Vue.js"))
        self.assertIn("javascript", found("JavaScript and TypeScript"))

    def test_chain_dev_tools(self):
        got = found("Hardhat, Foundry, ethers.js, viem, wagmi, Solidity, dApp")
        for sid in ("hardhat", "foundry", "ethersjs", "viem", "wagmi", "solidity", "dapp"):
            self.assertIn(sid, got)

    def test_mobile_stack(self):
        got = found("Swift and Kotlin for iOS and Android, React Native")
        for sid in ("swift", "kotlin", "ios", "android", "react-native"):
            self.assertIn(sid, got)


if __name__ == "__main__":
    unittest.main(verbosity=2)
