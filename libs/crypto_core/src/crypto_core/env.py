# -*- coding: utf-8 -*-
"""凭据/环境变量：唯一来源是仓库根 `.env`。

模块内如果放了 `.env`，优先用模块内的（便于单模块临时覆盖），
否则回退到仓库根 `.env`；两者都没有时返回空 dict，不报错。
"""
import os
from pathlib import Path

from . import paths


def find_env_file(module_dir=None):
    """按「模块内 .env → 仓库根 .env」顺序找凭据文件。"""
    if module_dir:
        candidate = Path(module_dir) / ".env"
        if candidate.is_file():
            return candidate
    return paths.repo_root() / ".env"


def load_env(path=None, module_dir=None):
    """读 .env 为 dict；不存在返回 {}。"""
    p = Path(path) if path else find_env_file(module_dir)
    out = {}
    if not p.is_file():
        return out
    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def save_env_value(key, value, path=None, module_dir=None):
    """原地更新 .env 中某个 key（不存在则追加），保留其他行与注释。"""
    p = Path(path) if path else find_env_file(module_dir)
    lines = []
    if p.is_file():
        lines = p.read_text(encoding="utf-8").splitlines()
    hit = False
    for i, line in enumerate(lines):
        if line.strip().startswith(key + "="):
            lines[i] = "%s=%s" % (key, value)
            hit = True
    if not hit:
        lines.append("%s=%s" % (key, value))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def get(key, default=None):
    """读当前进程环境（供部署时覆盖 .env，如 DATA_DIR / CHANNEL_TYPE）。"""
    return os.environ.get(key, default)
