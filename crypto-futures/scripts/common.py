#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""common.py —— crypto-futures 的薄适配层。

把共享库 libs/crypto_core 加入 sys.path（仓库根由文件位置探测，不依赖 PYTHONPATH/cwd），
并暴露本模块的固定目录、配置读取与日志。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)  # crypto-futures/
_REPO_ROOT = os.path.dirname(ROOT)
_LIBS_SRC = os.path.join(_REPO_ROOT, "libs", "crypto_core", "src")
if _LIBS_SRC not in sys.path:
    sys.path.insert(0, _LIBS_SRC)

from crypto_core import config as _config  # noqa: E402
from crypto_core import env as _env  # noqa: E402
from crypto_core import paths as _paths  # noqa: E402
from crypto_core import store as _store  # noqa: E402
from crypto_core import timeutil as _timeutil  # noqa: E402

MODULE = "crypto-futures"
CONFIG_DIR = os.path.join(ROOT, "config")
DATA_DIR = os.path.join(ROOT, "data")
RAW_DIR = os.path.join(DATA_DIR, "raw")
STATE_DIR = os.path.join(DATA_DIR, "state")
REPORT_DIR = os.path.join(DATA_DIR, "reports")
LOG_DIR = os.path.join(DATA_DIR, "logs")
EXPORT_DIR = os.path.join(DATA_DIR, "exports")

_CFG_CACHE = {}


def data_paths(base=None):
    """数据目录；base 不为空时（测试 / 回放）整体换到 base 下。"""
    root = base or DATA_DIR
    return {name: os.path.join(root, name) for name in ("raw", "state", "reports", "logs", "exports")}


def ensure_dirs(paths=None):
    paths = paths or data_paths()
    _paths.ensure_dirs(*paths.values())
    return paths


def load_config(name):
    if name not in _CFG_CACHE:
        _CFG_CACHE[name] = _config.load_yaml(os.path.join(CONFIG_DIR, name)) or {}
    return _CFG_CACHE[name]


def load_env():
    return _env.load_env()


def ts_iso(dt=None):
    return _timeutil.ts_iso(dt)


def fmt_local(epoch, fmt="%m-%d %H:%M"):
    return _timeutil.fmt_local(_timeutil.to_local(_from_epoch(epoch)), fmt)


def local_date(epoch):
    return fmt_local(epoch, "%Y-%m-%d")


def _from_epoch(epoch):
    import datetime
    return datetime.datetime.fromtimestamp(float(epoch), tz=datetime.timezone.utc)


def safe_json_load(path, default):
    return _store.safe_json_load(path, default)


def safe_json_dump(path, obj):
    return _store.safe_json_dump(path, obj)


def log_line(msg, paths=None):
    """追加一行到 data/logs/futures.log（失败静默），同时打印到 stderr（进 collect.log，不污染 --json 输出）。"""
    line = "[%s] %s" % (_timeutil.ts_iso(), msg)
    print(line, file=sys.stderr)
    try:
        path = os.path.join((paths or data_paths())["logs"], "futures.log")
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
