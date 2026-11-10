#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""index.py —— 法规库：去重、版本、废止/替代关系、效力状态。

索引文件：data/index/policies.json
    {"schema_version": 1, "updated_at": "...", "policies": [Policy, ...]}

Policy:
    key / id / doc_no / title / issuer / doc_type / level / status /
    published_at / effective_at / topics / supersedes / superseded_by /
    version / first_seen / last_seen / url
"""
import os

from common import INDEX_DIR, safe_json_dump, safe_json_load, ts_iso

INDEX_PATH = os.path.join(INDEX_DIR, "policies.json")


def load():
    data = safe_json_load(INDEX_PATH, {"schema_version": 1, "updated_at": "", "policies": []})
    if not isinstance(data, dict):
        data = {"schema_version": 1, "updated_at": "", "policies": []}
    data.setdefault("policies", [])
    return data


def save(index):
    index["updated_at"] = ts_iso()
    safe_json_dump(INDEX_PATH, index)


def find(index, key):
    for p in index.get("policies", []):
        if p.get("key") == key:
            return p
    return None


def _key_of(rec):
    return rec.get("doc_no") or ("id:" + str(rec.get("id")))


def upsert(index, rec):
    """把分类记录并入法规库；返回 (policy, is_new, is_updated)。"""
    key = _key_of(rec)
    now = ts_iso()
    policy = find(index, key)
    is_new = policy is None
    is_updated = False

    if is_new:
        policy = {
            "key": key,
            "id": rec.get("id"),
            "doc_no": rec.get("doc_no") or "",
            "title": rec.get("title") or "",
            "issuer": rec.get("issuer") or "",
            "doc_type": rec.get("doc_type") or "other",
            "jurisdiction": rec.get("jurisdiction") or "CN",
            "level": rec.get("level"),
            "status": "effective",
            "published_at": rec.get("published_at") or "",
            "effective_at": rec.get("effective_at") or "",
            "topics": rec.get("topics") or [],
            "supersedes": rec.get("supersedes") or [],
            "superseded_by": [],
            "version": 1,
            "first_seen": now,
            "last_seen": now,
            "url": rec.get("url") or "",
        }
        index["policies"].append(policy)
    else:
        before = (policy.get("version"), policy.get("title"), policy.get("url"))
        policy["last_seen"] = now
        policy["title"] = rec.get("title") or policy.get("title")
        policy["url"] = rec.get("url") or policy.get("url")
        policy["jurisdiction"] = rec.get("jurisdiction") or policy.get("jurisdiction") or "CN"
        policy["level"] = rec.get("level") or policy.get("level")
        if rec.get("body_hash") and rec.get("body_hash") != policy.get("body_hash"):
            policy["version"] = int(policy.get("version") or 1) + 1
        policy["body_hash"] = rec.get("body_hash") or policy.get("body_hash")
        is_updated = before != (policy.get("version"), policy.get("title"), policy.get("url"))

    # 废止/替代关系：新文件写「废止……银发〔2021〕237号」→ 旧条目置 repealed
    for old_no in rec.get("supersedes") or []:
        old = find(index, old_no)
        if old and old.get("key") != key:
            old["status"] = "repealed"
            old["superseded_by"] = key
            old["last_seen"] = now

    # 施行日在未来 → 待生效
    if policy.get("effective_at") and policy.get("effective_at") > now[:10]:
        policy["status"] = "pending"
    return policy, is_new, is_updated
