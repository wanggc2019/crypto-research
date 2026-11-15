# -*- coding: utf-8 -*-
"""跨模块 exports 契约登记表：谁产出、放在哪、消费方依赖哪些字段。

规则（ADR-0004 / architecture §契约）：
- 模块之间只读对方 `<module>/data/exports/`；读取一律走 `read()` / `export_path()`，不手拼路径；
- 生产方只增字段不改语义；改语义就升版本号（v2）并在这里新登记；
- `required` / `item_required` 只列**消费方真正依赖**的字段，生产方测试与消费方测试都用它校验；
- 标准样例在 `contract_examples/<schema 中 / 换成 _>.json`，消费方测试用它搭假仓库。
"""
import json
import os
import sys

from . import paths

# item_container: ("list", key) → obj[key] 是 list[dict]；("map", key) → obj[key] 是 dict[str, dict]
CONTRACTS = {
    "price/snapshot/v1": {
        "module": "crypto-price", "file": "latest.json",
        "required": {"schema": str, "generated_at": str, "assets": dict, "source_health": dict},
        "item_container": ("map", "assets"), "item_required": ("price", "change_24h_pct"),
        "consumers": ("crypto-analyst", "crypto-onchain"),
    },
    "price/daily/v1": {
        "module": "crypto-price", "file": "daily-{date}.json",
        "required": {"schema": str, "date": str, "generated_at": str, "assets": dict, "signals": list},
        "item_container": ("list", "signals"), "item_required": ("severity", "rule", "title"),
        "consumers": ("crypto-analyst",),
    },
    "news/daily/v1": {
        "module": "crypto-news", "file": "daily-{date}.json",
        "required": {"schema": str, "date": str, "generated_at": str, "counts": dict, "items": list},
        "item_container": ("list", "items"), "item_required": ("severity", "title", "url"),
        "consumers": ("crypto-analyst", "crypto-onchain"),
    },
    "policy/daily/v1": {
        "module": "crypto-policy", "file": "policy-{date}.json",
        "required": {"schema": str, "date": str, "generated_at": str, "items": list},
        "item_container": ("list", "items"), "item_required": ("title", "url"),
        "consumers": ("crypto-analyst",),
    },
    "kol/daily/v1": {
        "module": "crypto-x-kol", "file": "daily-{date}.json",
        "required": {"schema": str, "date": str, "generated_at": str, "items": list},
        "item_container": ("list", "items"), "item_required": ("handle", "text", "url"),
        "consumers": ("crypto-analyst",),
    },
    "onchain/pressure/v1": {
        "module": "crypto-onchain", "file": "onchain-pressure.json",
        "required": {"schema": str, "generated_at": str, "assets": dict},
        "item_container": ("map", "assets"), "item_required": (),
        "consumers": (),
    },
}

# 只在模块内部使用、不跨模块读取的 schema（登记在此，便于「所有 schema 字面量都有归属」的检查）
INTERNAL = {"alert/v1", "onchain/shadow/v1", "macro/v1", "jobs-export/v1", "company-stats/v1", "skill-report/v1"}

EXAMPLES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "contract_examples")


def _spec(schema_id):
    if schema_id not in CONTRACTS:
        raise KeyError("未登记的契约：%s" % schema_id)
    return CONTRACTS[schema_id]


def export_path(schema_id, date=None, repo=None):
    """契约文件的绝对路径；文件名含 {date} 的必须传 date（YYYY-MM-DD）。"""
    spec = _spec(schema_id)
    name = spec["file"]
    if "{date}" in name:
        if not date:
            raise ValueError("%s 需要 date" % schema_id)
        name = name.format(date=date)
    root = str(repo) if repo else str(paths.repo_root())
    return os.path.join(root, spec["module"], "data", "exports", name)


def validate(schema_id, obj):
    """返回问题列表（空 = 符合契约）。只校验登记的必需字段与类型。"""
    spec = _spec(schema_id)
    if not isinstance(obj, dict):
        return ["顶层不是对象"]
    problems = []
    if obj.get("schema") != schema_id:
        problems.append("schema 字段为 %r，期望 %r" % (obj.get("schema"), schema_id))
    for key, typ in spec["required"].items():
        if key not in obj:
            problems.append("缺少字段 %s" % key)
        elif not isinstance(obj[key], typ):
            problems.append("字段 %s 类型为 %s，期望 %s" % (key, type(obj[key]).__name__, typ.__name__))
    kind, key = spec["item_container"]
    container = obj.get(key)
    if spec["item_required"] and isinstance(container, (list, dict)):
        items = enumerate(container) if kind == "list" else container.items()
        for i, item in items:
            if not isinstance(item, dict):
                problems.append("%s[%s] 不是对象" % (key, i))
                continue
            missing = [f for f in spec["item_required"] if f not in item]
            if missing:
                problems.append("%s[%s] 缺少 %s" % (key, i, ",".join(missing)))
    return problems


def read(schema_id, date=None, repo=None, default=None):
    """读取并按 schema 字段核对契约：文件缺失/损坏/schema 不符都返回 default（消费方降级，不抛）。

    文件缺失是常态（生产方当天还没产出），静默；文件**存在却损坏或 schema 不符**说明契约出了问题，
    往 stderr 打一行警告（进各模块 collect.log），避免长期静默丢数据。
    """
    path = export_path(schema_id, date=date, repo=repo)
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        sys.stderr.write("[contracts] ⚠️ %s 读取失败（%s）：%s\n" % (schema_id, path, e))
        return default
    found = data.get("schema") if isinstance(data, dict) else type(data).__name__
    if found != schema_id:
        sys.stderr.write("[contracts] ⚠️ %s 期望 schema=%s，实际 %r：%s\n" % (path, schema_id, found, "已忽略"))
        return default
    return data


def example(schema_id):
    """标准样例（dict），供消费方测试使用。"""
    _spec(schema_id)
    with open(os.path.join(EXAMPLES_DIR, schema_id.replace("/", "_") + ".json"), "r", encoding="utf-8") as f:
        return json.load(f)


def write_example_repo(root, date):
    """在 root 下按真实目录结构铺好全部契约样例（文件名里的 {date} 用传入日期），返回 root。"""
    for schema_id in CONTRACTS:
        data = example(schema_id)
        if "date" in data:
            data["date"] = date
        path = export_path(schema_id, date=date, repo=root)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    return root
