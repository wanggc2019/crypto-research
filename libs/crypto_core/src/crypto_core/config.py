# -*- coding: utf-8 -*-
"""配置加载：YAML（PyYAML 优先，缺失时用系统 Ruby 兜底）+ JSON。

约定：模块配置放 `<module>/config/<name>.yaml|json`，建议带 `schema_version`。
"""
import json
import subprocess
from pathlib import Path


def load_yaml(path):
    """读 YAML 为 dict；文件不存在返回 {}，解析失败抛出带路径的异常。"""
    p = Path(path)
    if not p.is_file():
        return {}
    try:
        import yaml  # noqa: F401

        with open(p, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        # 零依赖兜底：macOS 自带 ruby + psych
        out = subprocess.check_output(
            ["ruby", "-ryaml", "-rjson", "-e", "print YAML.load_file(ARGV[0]).to_json", str(p)],
            stderr=subprocess.DEVNULL,
        )
        return json.loads(out.decode("utf-8")) or {}


def load_json(path, default=None):
    p = Path(path)
    if not p.is_file():
        return {} if default is None else default
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def load_config(module_dir, name):
    """读模块配置：<module_dir>/config/<name>（.yaml/.yml/.json 自动识别）。"""
    base = Path(module_dir) / "config"
    for suffix in (".yaml", ".yml", ".json"):
        candidate = base / (name if name.endswith(suffix) else name + suffix)
        if candidate.is_file():
            if candidate.suffix == ".json":
                return load_json(candidate)
            return load_yaml(candidate)
    return {}


def check_schema_version(data, expected=1):
    """校验配置的 schema_version 字段；缺失视为不通过，返回问题描述或 None。"""
    if not isinstance(data, dict):
        return "配置不是 dict"
    if data.get("schema_version") != expected:
        return "schema_version 期望 %s，实际 %r" % (expected, data.get("schema_version"))
    return None
