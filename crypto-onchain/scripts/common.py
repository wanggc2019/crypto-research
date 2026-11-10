#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""common.py —— crypto-onchain 的薄适配层。

通用能力（路径 / .env / YAML / JSON 原子写 / 文本 / 时间 / HTTP）已下沉到共享库
`libs/crypto_core`；这里只负责：
1. 把共享库加入 sys.path（仓库根由文件位置向上探测，不依赖 PYTHONPATH / cwd）；
2. 暴露本模块的固定目录常量与配置加载；
3. 提供日志/状态路径/跨模块只读读取等少量编排工具。

约定：本文件不 import 任何业务模块（sources/indicators/run），保持薄。
"""
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)  # crypto-onchain/
_REPO_ROOT = os.path.dirname(ROOT)
_LIBS_SRC = os.path.join(_REPO_ROOT, "libs", "crypto_core", "src")
if _LIBS_SRC not in sys.path:
    sys.path.insert(0, _LIBS_SRC)

from crypto_core import config as _config  # noqa: E402
from crypto_core import env as _env  # noqa: E402
from crypto_core import paths as _paths  # noqa: E402
from crypto_core import store as _store  # noqa: E402
from crypto_core import timeutil as _timeutil  # noqa: E402

CONFIG_DIR = os.path.join(ROOT, "config")
DATA_DIR = os.path.join(ROOT, "data")
REPO_ROOT = _REPO_ROOT

CONFIG_FILES = ("watchlist.yaml", "rules.yaml", "app.yaml")
_CFG_CACHE = {}


# ---------------------------------------------------------------- 目录
def data_paths(base=None):
    """返回数据目录字典；base 非空时把 data/ 整体重定向（测试/回放用）。"""
    root = os.path.abspath(base) if base else DATA_DIR
    return {
        "root": root,
        "raw": os.path.join(root, "raw"),
        "state": os.path.join(root, "state"),
        "reports": os.path.join(root, "reports"),
        "exports": os.path.join(root, "exports"),
        "logs": os.path.join(root, "logs"),
    }


def ensure_dirs(base=None):
    paths = data_paths(base)
    _paths.ensure_dirs(*(v for k, v in paths.items() if k != "root"))
    return paths


# ---------------------------------------------------------------- 配置
def load_config(name):
    if name in _CFG_CACHE:
        return _CFG_CACHE[name]
    data = _config.load_yaml(os.path.join(CONFIG_DIR, name))
    if not isinstance(data, dict):
        data = {}
    _CFG_CACHE[name] = data
    return data


def load_all_configs():
    return {
        "watchlist": load_config("watchlist.yaml"),
        "rules": load_config("rules.yaml"),
        "app": load_config("app.yaml"),
    }


def schema_problem(cfg, expected=1):
    return _config.check_schema_version(cfg, expected)


# ---------------------------------------------------------------- 时间/存储
def now():
    return _timeutil.now()


def today_str(dt=None):
    return _timeutil.today_str(dt)


def ts_iso(dt=None):
    return _timeutil.ts_iso(dt)


def epoch(dt=None):
    return int((dt or now()).timestamp())


def safe_json_load(path, default):
    return _store.safe_json_load(path, default)


def safe_json_dump(path, obj):
    return _store.safe_json_dump(path, obj)


def append_jsonl(path, records):
    return _store.append_jsonl(path, records)


def read_jsonl(path):
    return _store.read_jsonl(path)


def load_env():
    """凭据从仓库根 .env 读（模块内 .env 可覆盖）。M1 无凭据需求，仅为后续推送预留。"""
    return _env.load_env(module_dir=ROOT)


# 本模块专用 Bot（独立 Bot（.env 配置））：各类别名按顺序取第一个非空
_ONCHAIN_TOKEN_KEYS = (
    "TELEGRAM_ONCHAIN_BOT_TOKEN",
    "TELEGRAM_CHAIN_BOT_TOKEN",
)
_ONCHAIN_CHAT_KEYS = (
    "TELEGRAM_ONCHAIN_CHAT_ID",
    "TELEGRAM_CHAIN_CHAT_ID",
)


def _first_value(raw, keys):
    for key in keys:
        value = raw.get(key)
        if value:
            return value
    return ""


def load_bot_env():
    """crypto-onchain 专用 Bot 凭据优先（独立 Bot（.env 配置）），否则回退根 .env 的通用 TELEGRAM_*。

    这样链上监控/告警可以单独用 独立 Bot（.env 配置） 发送，不影响其他模块。
    """
    raw = load_env()
    return {
        "TELEGRAM_BOT_TOKEN": _first_value(raw, _ONCHAIN_TOKEN_KEYS)
        or raw.get("TELEGRAM_BOT_TOKEN", ""),
        "TELEGRAM_CHAT_ID": _first_value(raw, _ONCHAIN_CHAT_KEYS)
        or raw.get("TELEGRAM_CHAT_ID", ""),
    }


# ---------------------------------------------------------------- 时区
def apply_timezone(name):
    """让进程本地时间跟随配置时区（默认 本地时区）。"""
    if not name:
        return None
    os.environ["TZ"] = str(name)
    try:
        time.tzset()
    except AttributeError:  # Windows 无 tzset
        pass
    return str(name)


# ---------------------------------------------------------------- 日志
def log_line(msg, paths, filename="onchain.log"):
    os.makedirs(paths["logs"], exist_ok=True)
    line = "%s %s\n" % (now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        with open(os.path.join(paths["logs"], filename), "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass
    return line.strip()


# ---------------------------------------------------------------- 跨模块只读
def load_price_context(app_cfg, now_ts=None):
    """读 crypto-price 的 exports 契约（唯一允许的跨模块读取方式）。

    返回 {"ok": bool, "fresh": bool, "generated_at": str|None, "assets": {...}, "error": str|None}
    缺失/损坏/过期都不抛异常——本模块降级运行，不阻塞影子采集。
    """
    src = (app_cfg.get("sources") or {}).get("price_exports") or {}
    rel = src.get("path") or "../crypto-price/data/exports/latest.json"
    path = rel if os.path.isabs(rel) else os.path.normpath(os.path.join(ROOT, rel))
    max_age = int(src.get("max_age_seconds") or 900)
    data = safe_json_load(path, None)
    if not isinstance(data, dict):
        return {"ok": False, "fresh": False, "generated_at": None, "assets": {}, "error": "price export 缺失或损坏"}
    assets = {}
    for asset, row in (data.get("assets") or {}).items():
        if not isinstance(row, dict):
            continue
        assets[str(asset).upper()] = {
            "price": row.get("price"),
            "change_24h_pct": row.get("change_24h_pct"),
            "volume_24h": row.get("volume_24h"),
            "funding_rate": row.get("funding_rate"),
            "oi": row.get("oi"),
            "sources": row.get("sources") or {},
        }
    fresh = False
    generated_at = data.get("generated_at")
    try:
        gen_ts = int(time.mktime(time.strptime(str(generated_at)[:19], "%Y-%m-%dT%H:%M:%S")))
        fresh = (now_ts or epoch()) - gen_ts <= max_age
    except (TypeError, ValueError, OverflowError):
        fresh = False
    return {
        "ok": bool(assets),
        "fresh": fresh,
        "generated_at": generated_at,
        "assets": assets,
        "error": None if assets else "price export 无 assets",
    }


def price_of(price_ctx, asset):
    row = (price_ctx.get("assets") or {}).get(asset) or {}
    price = row.get("price")
    try:
        return float(price)
    except (TypeError, ValueError):
        return None


def write_json(path, obj):
    """确保父目录存在后原子写。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return safe_json_dump(path, obj)


def read_raw_json(path, default=None):
    """宽松读 JSON（fixtures/状态文件用）。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError, UnicodeDecodeError):
        return default
