#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sources.py —— crypto-jobs 采集层（招聘源 → 归一化岗位字段），只读不落盘。

对外契约（Lead 的 main.py 直接调用，保持稳定）：
    fetch_all(sources_config=None, limit=None, only=None, fixtures_dir=None, fetch_http=None)
        -> (jobs: list[dict], health: list[dict])
    - sources_config: 源配置 dict；None = 读 config/sources.yaml；也可传配置名（如 "sources.yaml"）
    - limit: 单源最多返回多少条；None/0 = 不限制（此时回落到 app.yaml fetch.max_jobs_per_source）
    - only: 只跑这些 source_id（list 或 "a,b" 逗号串）；None = 全部 enabled
    - fixtures_dir: 离线回放目录（含 manifest.json）；此时禁止任何网络请求，未覆盖的源跳过
    - fetch_http: 可注入的 {(url) -> bytes/text}，测试打桩用（优先级高于真实网络）

每个 job dict 的键（缺值用 ""/[]）：
    source_id, source_tier, company, company_slug, job_id, title, url,
    locations(list[str]), remote(remote/hybrid/onsite/""), posted_at(ISO), updated_at(ISO),
    employment, salary(str), description_text(clean 后且 <= app.yaml fetch.description_max_chars),
    tags(list[str])

health 每项：{"source_id", "tier", "ok", "count", "ms", "error"}

用法：
    python3 scripts/sources.py --dry-run --json --limit 3
    python3 scripts/sources.py --dry-run --json --only gh_coinbase,as_kraken
    python3 scripts/sources.py --fixtures tests/fixtures/sources --json   # 离线回放，零网络
    python3 scripts/sources.py --list

设计要点：
- 严格串行 + 每源单次请求 + 超时 20s + 失败重试 1 次；单源失败只记 health.error，绝不抛整轮；
- 本层不做角色/技能判断（那是 normalize.py / skills.py 的职责），T2 通用站只做 crypto 语境过滤；
- 不写 data/、不写任何文件、不推送。
"""
import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import source_adapters  # noqa: E402

import common  # noqa: E402

TIERS = ("T0", "T1", "T2")
REMOTE_VALUES = ("remote", "hybrid", "onsite")
DEFAULT_TIMEOUT = 20
DEFAULT_RETRIES = 1
DEFAULT_DESCRIPTION_MAX = 20000

JOB_KEYS = (
    "source_id",
    "source_tier",
    "company",
    "company_slug",
    "job_id",
    "title",
    "url",
    "locations",
    "remote",
    "posted_at",
    "updated_at",
    "employment",
    "salary",
    "description_text",
    "tags",
)


def _int_option(*values, **kwargs):
    """按顺序取第一个可转 int 的值；全失败用 default。"""
    default = kwargs.get("default", 0)
    for value in values:
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return default


def _sources_from(sources_config):
    """返回 (配置 dict, 启用源 list)。"""
    if sources_config is None:
        cfg = common.load_config("sources.yaml") or {}
    elif isinstance(sources_config, str):
        cfg = common.load_config(sources_config) or {}
    else:
        cfg = sources_config or {}
    sources = [s for s in cfg.get("sources") or [] if isinstance(s, dict)]
    enabled = [s for s in sources if s.get("enabled", True) is not False]
    return cfg, enabled


def _parse_only(only):
    if not only:
        return None
    if isinstance(only, str):
        items = only.split(",")
    else:
        items = list(only)
    wanted = {str(item).strip() for item in items if str(item).strip()}
    return wanted or None


def _effective_limit(limit, app, defaults):
    """limit 显式 > 0 时优先；None 时回落到 app.yaml fetch.max_jobs_per_source；0 = 不限制。"""
    if limit not in (None, ""):
        cap = _int_option(limit, default=0)
        return cap if cap > 0 else 0
    fetch_cfg = app.get("fetch") or {}
    cap = _int_option(fetch_cfg.get("max_jobs_per_source"), defaults.get("max_jobs_per_source"), default=0)
    return cap if cap > 0 else 0


def _load_manifest(fixtures_dir):
    """读 fixtures manifest.json → {source_id: 文件名}。兼容 {"sources": {...}} 与平铺两种写法。"""
    path = os.path.join(fixtures_dir, "manifest.json")
    if not os.path.isfile(path):
        raise FileNotFoundError("fixtures 目录缺少 manifest.json：%s" % path)
    with open(path, "r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    if not isinstance(manifest, dict):
        raise ValueError("fixtures manifest.json 不是对象：%s" % path)
    entries = manifest.get("sources") if isinstance(manifest.get("sources"), dict) else manifest
    out = {}
    for source_id, value in (entries or {}).items():
        if source_id in ("schema_version", "note", "generated_at"):
            continue
        if isinstance(value, str):
            filename = value
        elif isinstance(value, dict):
            filename = value.get("file") or value.get("filename")
        else:
            continue
        if filename:
            out[str(source_id)] = str(filename)
    if not out:
        raise ValueError("fixtures manifest.json 没有任何源映射：%s" % path)
    return out


def _read_text(value):
    """把注入函数/文件的返回值统一成 str。"""
    if value is None:
        raise ValueError("响应为空")
    if isinstance(value, bytes):
        return value.decode("utf-8", "ignore")
    if not isinstance(value, str):
        raise ValueError("响应类型不支持：%s" % type(value).__name__)
    return value


class _Fetcher:
    """网页/文件/打桩三种取数方式；fixtures 模式下绝不触网。"""

    def __init__(self, timeout=DEFAULT_TIMEOUT, retries=DEFAULT_RETRIES, fetch_http=None, fixtures_dir=None):
        self.timeout = timeout
        self.retries = retries
        self.fetch_http = fetch_http
        self.fixtures_dir = fixtures_dir
        self.fixture_map = _load_manifest(fixtures_dir) if fixtures_dir else {}

    def covered(self, source_id):
        return str(source_id) in self.fixture_map

    def get(self, source):
        source_id = str(source.get("id") or "")
        url = str(source.get("url") or "").strip()
        if self.fixtures_dir:
            filename = self.fixture_map.get(source_id)
            if not filename:
                raise LookupError("fixtures 缺少源 %s" % source_id)
            path = os.path.join(self.fixtures_dir, filename)
            if not os.path.isfile(path):
                raise FileNotFoundError("fixtures 文件不存在：%s" % path)
            with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                return handle.read()
        if self.fetch_http is not None:
            return _read_text(self.fetch_http(url))
        if not url:
            raise ValueError("源缺少 url：%s" % source_id)
        # 真实网络：crypto_core 统一 UA / 超时 / 重试 / 308 跟随
        return _read_text(common.HTTP_GET_TEXT(url, timeout=self.timeout, retries=self.retries))


def _crypto_keywords(cfg, app):
    words = list((app.get("filters") or {}).get("crypto_keywords") or [])
    words += list(cfg.get("t2_keywords") or [])
    out = []
    for word in words:
        text = str(word or "").strip().lower()
        if text and text not in out:
            out.append(text)
    return out


def _merge_keywords(base, extra):
    """全局 crypto 关键词 + 源级 keywords（去重、按序）。"""
    merged = list(base)
    for word in extra or []:
        text = str(word or "").strip().lower()
        if text and text not in merged:
            merged.append(text)
    return merged


def _company_whitelist(cfg):
    entries = cfg.get("company_whitelist")
    if not entries:
        entries = (common.load_config("companies.yaml") or {}).get("crypto_companies") or []
    return [str(item or "").strip().lower() for item in entries if str(item or "").strip()]


def _company_hit(company, whitelist):
    """公司名按词边界命中白名单（避免 CircleCI 命中 Circle）。"""
    low = (company or "").lower()
    for name in whitelist:
        if re.search(r"(?<![0-9a-z])" + re.escape(name) + r"(?![0-9a-z])", low):
            return True
    return False


_KEYWORD_RX_CACHE = {}


def _keyword_rx(word):
    """关键词 → 词边界正则（允许英文复数 s），避免 defi 命中 definitive、trade 命中 trademark。"""
    word = str(word or "").strip().lower()
    if not word:
        return None
    cached = _KEYWORD_RX_CACHE.get(word)
    if cached is None:
        escaped = re.escape(word)
        left = r"(?<![0-9a-z])" if re.match(r"^[0-9a-z]", word) else ""
        right = r"s?(?![0-9a-z])" if re.search(r"[0-9a-z]$", word) else ""
        cached = re.compile(left + escaped + right)
        _KEYWORD_RX_CACHE[word] = cached
    return cached


def is_crypto_job(job, keywords, whitelist):
    """T2 crypto 语境判断：标题/公司/标签/正文前 4000 字命中关键词，或公司命中白名单。"""
    haystack = " ".join(
        [
            job.get("title") or "",
            job.get("company") or "",
            " ".join(job.get("tags") or []),
            (job.get("description_text") or "")[:4000],
        ]
    ).lower()
    for word in keywords:
        pattern = _keyword_rx(word)
        if pattern is not None and pattern.search(haystack):
            return True
    return _company_hit(job.get("company") or "", whitelist)


def _finalize(item, source, description_max_chars):
    """中间态 → 对外契约 job dict。"""
    title = str(item.get("title") or "").strip()
    job_id = str(item.get("job_id") or "").strip()
    url = str(item.get("url") or "").strip()
    company = str(item.get("company") or "").strip() or str(source.get("company") or "")
    if not job_id:
        basis = "|".join([str(source.get("id") or ""), url, title])
        job_id = common.sha256(basis)[:16]
    remote = str(item.get("remote") or "").strip().lower()
    if remote not in REMOTE_VALUES:
        remote = ""
    description = str(item.get("description_text") or "")[:description_max_chars]
    return {
        "source_id": str(source.get("id") or ""),
        "source_tier": str(source.get("tier") or "T2"),
        "company": company,
        "company_slug": str(item.get("company_slug") or "").strip() or source_adapters.slugify_company(company),
        "job_id": job_id,
        "title": title,
        "url": url,
        "locations": [str(x).strip() for x in item.get("locations") or [] if str(x).strip()],
        "remote": remote,
        "posted_at": str(item.get("posted_at") or ""),
        "updated_at": str(item.get("updated_at") or ""),
        "employment": str(item.get("employment") or ""),
        "salary": str(item.get("salary") or ""),
        "description_text": description,
        "tags": [str(x).strip() for x in item.get("tags") or [] if str(x).strip()],
    }


def run_source(source, raw, description_max_chars=DEFAULT_DESCRIPTION_MAX, crypto_keywords=(), company_whitelist=()):
    """单源原始响应 → 契约 job list（解析 + 兜底 + T2 crypto 过滤 + 源内去重）。"""
    parsed = source_adapters.parse_source(source, raw, description_max_chars=description_max_chars)
    jobs, seen = [], set()
    for item in parsed:
        job = _finalize(item, source, description_max_chars)
        if not job["title"]:
            continue
        if job["job_id"] in seen:
            continue
        seen.add(job["job_id"])
        if source.get("crypto_only") and not is_crypto_job(job, crypto_keywords, company_whitelist):
            continue
        jobs.append(job)
    return jobs


def _fetch_all(sources_config=None, limit=None, only=None, fixtures_dir=None, fetch_http=None, on_progress=None):
    cfg, sources = _sources_from(sources_config)
    app = common.load_config("app.yaml") or {}
    defaults = cfg.get("defaults") or {}
    fetch_cfg = app.get("fetch") or {}
    timeout = _int_option(fetch_cfg.get("timeout_seconds"), defaults.get("timeout_seconds"), default=DEFAULT_TIMEOUT)
    retries = _int_option(fetch_cfg.get("retries"), defaults.get("retries"), default=DEFAULT_RETRIES)
    max_chars = _int_option(
        fetch_cfg.get("description_max_chars"),
        defaults.get("description_max_chars"),
        default=DEFAULT_DESCRIPTION_MAX,
    )
    cap = _effective_limit(limit, app, defaults)
    wanted = _parse_only(only)
    selected = [source for source in sources if not wanted or str(source.get("id")) in wanted]
    base_keywords = _crypto_keywords(cfg, app)
    whitelist = _company_whitelist(cfg)
    fetcher = _Fetcher(timeout=timeout, retries=retries, fetch_http=fetch_http, fixtures_dir=fixtures_dir)

    jobs, health = [], []
    for source in selected:
        source_id = str(source.get("id") or "")
        tier = str(source.get("tier") or "T2")
        if fetcher.fixtures_dir and not fetcher.covered(source_id):
            continue  # 离线回放：manifest 未覆盖的源直接跳过（不算失败）
        if on_progress:
            on_progress("→ %s" % source_id)
        keywords = _merge_keywords(base_keywords, source.get("keywords"))
        started = time.monotonic()
        try:
            raw = fetcher.get(source)
            source_jobs = run_source(
                source, raw,
                description_max_chars=max_chars,
                crypto_keywords=keywords,
                company_whitelist=whitelist,
            )
            if cap:
                source_jobs = source_jobs[:cap]
            ok, error = True, ""
        except Exception as exc:  # noqa: BLE001 - 单源失败只降级，不拖垮整轮
            source_jobs, ok = [], False
            error = ("%s: %s" % (type(exc).__name__, exc))[:300]
        elapsed_ms = int((time.monotonic() - started) * 1000)
        jobs.extend(source_jobs)
        health.append(
            {"source_id": source_id, "tier": tier, "ok": ok, "count": len(source_jobs), "ms": elapsed_ms, "error": error}
        )
    return jobs, health


def fetch_all(sources_config=None, limit=None, only=None, fixtures_dir=None, fetch_http=None):
    """采集全部（或指定）源，返回 (jobs, health)。契约见模块 docstring。"""
    return _fetch_all(
        sources_config=sources_config,
        limit=limit,
        only=only,
        fixtures_dir=fixtures_dir,
        fetch_http=fetch_http,
    )


def _print_table(result):
    print(
        "采集完成：%d 源 / 成功 %d / 失败 %d / 岗位 %d 条（%d ms）"
        % (
            result["sources_total"],
            result["sources_ok"],
            result["sources_failed"],
            result["total_jobs"],
            result["total_ms"],
        )
    )
    for item in result["sources"]:
        flag = "ok  " if item["ok"] else "FAIL"
        line = "  %s %-14s %-3s count=%-4d %5dms" % (
            flag, item["source_id"], item["tier"], item["count"], item["ms"],
        )
        if item["error"]:
            line += "  ! %s" % item["error"]
        print(line)
    if result["skipped_sources"]:
        print("  （fixtures 未覆盖、已跳过 %d 个源）" % result["skipped_sources"])


def main(argv=None):
    parser = argparse.ArgumentParser(description="crypto-jobs 招聘源采集（只读，不写 data/）")
    parser.add_argument("--dry-run", action="store_true", help="只打印不落盘（本层无副作用，保持统一 CLI）")
    parser.add_argument("--fixtures", help="离线回放目录（含 manifest.json），禁止联网")
    parser.add_argument("--only", help="只跑指定 source_id（逗号分隔）")
    parser.add_argument("--limit", type=int, help="单源最多返回条数（0 = 用 app.yaml fetch.max_jobs_per_source）")
    parser.add_argument("--json", action="store_true", help="机器可读输出（stdout 仅 JSON）")
    parser.add_argument("--list", action="store_true", help="列出启用源后退出")
    args = parser.parse_args(argv)

    _, sources = _sources_from(None)
    if not sources:
        print("[crypto-jobs] 没有启用的招聘源，请检查 config/sources.yaml", file=sys.stderr)
        return 2
    if args.list:
        for source in sources:
            print("%-14s %-3s %-10s %s" % (
                source.get("id"), source.get("tier"), source.get("type"), source.get("url")))
        print("共 %d 个启用源" % len(sources), file=sys.stderr)
        return 0

    skipped = 0
    if args.fixtures:
        covered = set(_load_manifest(args.fixtures))
        skipped = len([source for source in sources if str(source.get("id")) not in covered])

    def _progress(message):
        if not args.json:
            print(message, file=sys.stderr)

    started = time.monotonic()
    jobs, health = _fetch_all(
        limit=args.limit,
        only=args.only,
        fixtures_dir=args.fixtures,
        on_progress=_progress,
    )
    total_ms = int((time.monotonic() - started) * 1000)
    ok_count = len([item for item in health if item["ok"]])
    failed = [item for item in health if not item["ok"]]
    result = {
        "fixtures": bool(args.fixtures),
        "limit": args.limit,
        "total_jobs": len(jobs),
        "sources_total": len(health),
        "sources_ok": ok_count,
        "sources_failed": len(failed),
        "skipped_sources": skipped,
        "total_ms": total_ms,
        "sources": health,
        "errors": ["%s: %s" % (item["source_id"], item["error"]) for item in failed],
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=1))
    else:
        _print_table(result)

    if not health or ok_count == 0:
        return 3  # 网络全挂 / 全部源不可用
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
