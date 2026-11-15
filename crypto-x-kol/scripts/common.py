#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""common.py —— crypto-x-kol 的薄适配层（路径 / 配置 / 日志）。

通用能力（YAML / .env / JSON 原子写 / 文本 / 时间）都在 `libs/crypto_core`；
本文件只做三件事：把共享库加入 sys.path、暴露本模块目录、保留少量便捷函数。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)  # crypto-x-kol/
_REPO_ROOT = os.path.dirname(ROOT)
REPO_ROOT = _REPO_ROOT
_LIBS_SRC = os.path.join(_REPO_ROOT, "libs", "crypto_core", "src")
if _LIBS_SRC not in sys.path:
    sys.path.insert(0, _LIBS_SRC)

from crypto_core import config as _config  # noqa: E402
from crypto_core import env as _env  # noqa: E402
from crypto_core import paths as _paths  # noqa: E402
from crypto_core import store as _store  # noqa: E402
from crypto_core import text as _text  # noqa: E402
from crypto_core import timeutil as _timeutil  # noqa: E402
from crypto_core.notify import telegram as _tg  # noqa: E402

CONFIG_DIR = os.path.join(ROOT, "config")
DATA_DIR = os.path.join(ROOT, "data")


_CONFIG = _config.ConfigCache(ROOT)


def load_config(name):
    """读 config/<name>（.yaml/.yml/.json 自动识别；PyYAML 缺失时共享库回退 ruby）；进程内缓存，非 dict 视为 {}。"""
    return _CONFIG.get(name)


def data_paths(base=None):
    """数据目录布局；base 用于测试/回放的临时 data-dir。"""
    return _paths.data_layout(base or DATA_DIR)


def ensure_dirs(base=None):
    paths = data_paths(base)
    _paths.ensure_dirs(paths["raw"], paths["state"], paths["reports"], paths["logs"], paths["exports"])
    return paths


def load_env():
    return _env.load_env(module_dir=ROOT)


def now():
    return _timeutil.now()


def today_str(dt=None):
    return _timeutil.today_str(dt)


def ts_iso(dt=None):
    return _timeutil.ts_iso(dt)


def sha256(s):
    return _text.sha256(s)


def clean_text(s):
    return _text.clean_text(s)


def norm_title(s, limit=400):
    return _text.norm_title(s, limit)


def safe_json_load(path, default):
    return _store.safe_json_load(path, default)


def safe_json_dump(path, obj):
    return _store.safe_json_dump(path, obj)


def read_jsonl(path):
    return _store.read_jsonl(path)


def append_jsonl(path, records):
    return _store.append_jsonl(path, records)


def log_line(msg, paths=None, name="kol.log"):
    """运行日志（collect.sh 之外的可读流水，失败不抛）。"""
    paths = paths or data_paths()
    _store.append_log_line(os.path.join(paths["logs"], name), msg, ts=now().strftime("%Y-%m-%d %H:%M:%S"))


def job_name(module="crypto-x-kol"):
    """调度任务名（launchd Label / 手动运行标记），统一由 crypto_core.notify.telegram.job_label 解析。"""
    return _tg.job_label(module)
