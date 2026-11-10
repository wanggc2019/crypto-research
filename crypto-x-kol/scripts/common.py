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

CONFIG_DIR = os.path.join(ROOT, "config")
DATA_DIR = os.path.join(ROOT, "data")
_CFG = {}


def load_config(name):
    """读 config/<name>（YAML；缺 PyYAML 时由共享库回退 ruby）。"""
    if name not in _CFG:
        _CFG[name] = _config.load_yaml(os.path.join(CONFIG_DIR, name)) or {}
    return _CFG[name]


def data_paths(base=None):
    """数据目录布局；base 用于测试/回放的临时 data-dir。"""
    root = os.path.abspath(base) if base else DATA_DIR
    return {
        "root": root,
        "raw": os.path.join(root, "raw"),
        "state": os.path.join(root, "state"),
        "reports": os.path.join(root, "reports"),
        "logs": os.path.join(root, "logs"),
        "exports": os.path.join(root, "exports"),
    }


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
    try:
        os.makedirs(paths["logs"], exist_ok=True)
        with open(os.path.join(paths["logs"], name), "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (now().strftime("%Y-%m-%d %H:%M:%S"), msg))
    except OSError:
        pass


def job_name(module="crypto-x-kol"):
    """调度任务名：优先 CRYPTO_JOB_LABEL（launchd plist 注入；launchd 会把 XPC 覆盖成 0），
    其次 XPC_SERVICE_NAME，最后回退手动标记。"""
    label = os.environ.get("CRYPTO_JOB_LABEL") or ""
    if label:
        return label
    job = os.environ.get("XPC_SERVICE_NAME") or ""
    if not job or job == "0":
        return "%s（手动运行）" % module
    return job
