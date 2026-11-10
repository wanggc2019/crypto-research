#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""common.py —— crypto-news 的薄适配层。

通用能力（路径 / .env / YAML / JSON 原子写 / 文本 / 时间）已下沉到共享库
`libs/crypto_core`；这里只负责：
1. 把共享库加入 sys.path（仓库根由文件位置向上探测，不依赖 PYTHONPATH / cwd）；
2. 暴露本模块的固定目录常量（config/ data/ raw/ state/ reports/ logs/）；
3. 保持既有函数名，避免业务代码大改。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)  # crypto-news/
_REPO_ROOT = os.path.dirname(ROOT)
_LIBS_SRC = os.path.join(_REPO_ROOT, "libs", "crypto_core", "src")
if _LIBS_SRC not in sys.path:
    sys.path.insert(0, _LIBS_SRC)

from crypto_core import config as _config  # noqa: E402
from crypto_core import paths as _paths  # noqa: E402
from crypto_core import store as _store  # noqa: E402
from crypto_core import text as _text  # noqa: E402
from crypto_core import timeutil as _timeutil  # noqa: E402

CONFIG_DIR = os.path.join(ROOT, "config")
DATA_DIR = os.path.join(ROOT, "data")
RAW_DIR = os.path.join(DATA_DIR, "raw")
STATE_DIR = os.path.join(DATA_DIR, "state")
REPORT_DIR = os.path.join(DATA_DIR, "reports")
LOG_DIR = os.path.join(DATA_DIR, "logs")

_CFG_CACHE = {}


def ensure_dirs():
    _paths.ensure_dirs(RAW_DIR, STATE_DIR, REPORT_DIR, LOG_DIR)


def load_config(name):
    """读 config/<name>（YAML；PyYAML 缺失时用系统 Ruby 兜底）。"""
    if name in _CFG_CACHE:
        return _CFG_CACHE[name]
    data = _config.load_yaml(os.path.join(CONFIG_DIR, name))
    _CFG_CACHE[name] = data
    return data


def now():
    return _timeutil.now()


def today_str():
    return _timeutil.today_str()


def ts_iso(dt=None):
    return _timeutil.ts_iso(dt)


def sha256(s):
    return _text.sha256(s)


def clean_text(s):
    return _text.clean_text(s)


def norm_url(u):
    return _text.norm_url(u)


def contains_any(text, words):
    return _text.contains_any(text, words)


def safe_json_load(path, default):
    return _store.safe_json_load(path, default)


def safe_json_dump(path, obj):
    return _store.safe_json_dump(path, obj)
