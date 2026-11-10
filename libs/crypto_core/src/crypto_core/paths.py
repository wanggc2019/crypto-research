# -*- coding: utf-8 -*-
"""仓库/模块路径解析。

仓库布局固定为：

    <repo>/libs/crypto_core/src/crypto_core/paths.py   ← 本文件
    <repo>/<module>/                                   ← 需求模块（crypto-*）
"""
import os
from pathlib import Path


def repo_root():
    """仓库根目录（通过本文件位置推导，不依赖 cwd）。"""
    return Path(__file__).resolve().parents[4]


def module_root(module):
    """需求模块根目录，例如 module_root("crypto-news")。"""
    return repo_root() / module


def module_dir(module, *parts):
    """模块内的子路径，例如 module_dir("crypto-news", "data", "state")。"""
    return module_root(module).joinpath(*parts)


def ensure_dirs(*paths):
    """批量创建目录（幂等）。"""
    for p in paths:
        Path(p).mkdir(parents=True, exist_ok=True)


def find_repo_root(start=None):
    """从 start（默认本文件）向上找仓库根，找不到抛 RuntimeError。

    模块入口在无法依赖 PYTHONPATH 时可用它把自己定位到仓库：
        repo = find_repo_root(__file__)
    """
    p = Path(start or __file__).resolve()
    for parent in [p, *p.parents]:
        if (parent / "libs" / "crypto_core" / "src" / "crypto_core").is_dir():
            return parent
    raise RuntimeError("找不到仓库根（期望包含 libs/crypto_core/src/crypto_core）: %s" % start)


def repo_path(*parts):
    return repo_root().joinpath(*parts)


def rel_repo(path):
    """相对仓库根的展示路径，用于日志。"""
    try:
        return os.path.relpath(str(path), str(repo_root()))
    except ValueError:
        return str(path)
