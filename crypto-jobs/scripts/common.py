#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""common.py —— crypto-jobs 的薄适配层。

把共享库 libs/crypto_core 加入 sys.path（仓库根由文件位置探测，不依赖 PYTHONPATH/cwd），
并暴露本模块的固定目录与常用工具。业务逻辑不要写在这里。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)  # crypto-jobs/
_REPO_ROOT = os.path.dirname(ROOT)
_LIBS_SRC = os.path.join(_REPO_ROOT, "libs", "crypto_core", "src")
if _LIBS_SRC not in sys.path:
    sys.path.insert(0, _LIBS_SRC)

from crypto_core import config as _config  # noqa: E402
from crypto_core import env as _env  # noqa: E402
from crypto_core import http as _http  # noqa: E402
from crypto_core import paths as _paths  # noqa: E402
from crypto_core import store as _store  # noqa: E402
from crypto_core import text as _text  # noqa: E402
from crypto_core import timeutil as _timeutil  # noqa: E402
from crypto_core.notify import telegram as _tg  # noqa: E402

CONFIG_DIR = os.path.join(ROOT, "config")
DATA_DIR = os.path.join(ROOT, "data")
RAW_DIR = os.path.join(DATA_DIR, "raw")
STATE_DIR = os.path.join(DATA_DIR, "state")
REPORT_DIR = os.path.join(DATA_DIR, "reports")
EXPORT_DIR = os.path.join(DATA_DIR, "exports")
LOG_DIR = os.path.join(DATA_DIR, "logs")
FIXTURE_DIR = os.path.join(ROOT, "tests", "fixtures")

HTTP_GET = _http.get
HTTP_GET_TEXT = _http.get_text
HTTP_GET_JSON = _http.get_json
clean_text = _text.clean_text
norm_title = _text.norm_title
norm_url = _text.norm_url
sha256 = _text.sha256
contains_any = _text.contains_any
parse_date = _timeutil.parse_date
now = _timeutil.now
today_str = _timeutil.today_str
ts_iso = _timeutil.ts_iso
in_quiet_hours = _timeutil.in_quiet_hours


def ensure_dirs(*extra):
    _paths.ensure_dirs(RAW_DIR, STATE_DIR, REPORT_DIR, EXPORT_DIR, LOG_DIR, *extra)


_CONFIG = _config.ConfigCache(ROOT)


def load_config(name):
    """读 config/<name>.yaml|json；缺失返回 {}（进程内缓存）。"""
    return _CONFIG.get(name)


def load_env():
    return _env.load_env(module_dir=ROOT)


_JOBS_TOKEN_KEYS = (
    "TELEGRAM_JOBS_BOT_TOKEN",
    "TELEGRAM_JOB_BOT_TOKEN",
    "WGC_JOBS_BOT_TOKEN",
)
_JOBS_CHAT_KEYS = (
    "TELEGRAM_JOBS_CHAT_ID",
    "TELEGRAM_JOB_CHAT_ID",
)


def load_bot_env():
    """crypto-jobs 专用 Bot 凭据优先（TELEGRAM_JOBS_* 等别名），否则回退根 .env 的通用 TELEGRAM_*。"""
    info = _env.resolve_bot_env(load_env(), _JOBS_TOKEN_KEYS, _JOBS_CHAT_KEYS)
    return {k: info[k] for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")}


def safe_json_load(path, default):
    return _store.safe_json_load(path, default)


def safe_json_dump(path, obj):
    return _store.safe_json_dump(path, obj)


def append_jsonl(path, records):
    return _store.append_jsonl(path, records)


def read_jsonl(path):
    return _store.read_jsonl(path)


def job_name():
    """调度任务名（launchd Label / 手动运行标记），统一由 crypto_core.notify.telegram.job_label 解析。"""
    return _tg.job_label("crypto-jobs")
