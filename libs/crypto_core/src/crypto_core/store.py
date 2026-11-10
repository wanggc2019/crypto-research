# -*- coding: utf-8 -*-
"""存储原语：JSON 原子读写、JSONL 追加、目录创建。

所有写操作先写临时文件再 os.replace，避免断电/中断留下半截文件。
"""
import json
import os
from pathlib import Path


def ensure_parent(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def safe_json_load(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def safe_json_dump(path, obj, indent=1):
    ensure_parent(path)
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=indent)
    os.replace(tmp, path)


def append_jsonl(path, records):
    """追加记录到 JSONL。records 可以是 dict 或 dict 的可迭代对象。"""
    ensure_parent(path)
    if isinstance(records, dict):
        records = [records]
    with open(path, "a", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def read_jsonl(path):
    """逐行读 JSONL，坏行跳过（返回 list[dict]）。"""
    out = []
    if not Path(path).is_file():
        return out
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out
