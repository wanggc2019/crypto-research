# -*- coding: utf-8 -*-
"""crypto_core —— crypto-research 共享能力库

设计约束（见 docs/architecture.md）：
- 只依赖 Python 3.9 标准库（YAML 可选 PyYAML，缺失时用系统 Ruby 兜底）
- 无业务决策、无调度、无隐式全局状态；需要配置由调用方传入
- 模块通过 `libs/crypto_core/src` 加入 sys.path 后 `import crypto_core`
"""

__version__ = "0.1.0"

# 显式白名单：稳定 API 才在这里导出，避免模块依赖内部实现细节
from . import config, dedupe, env, events, paths, store, text, timeutil  # noqa: E402,F401
from .notify import check_credentials, discover_chat_id, format_alert, send_message  # noqa: E402,F401

__all__ = [
    "config",
    "dedupe",
    "env",
    "events",
    "paths",
    "store",
    "text",
    "timeutil",
    "send_message",
    "check_credentials",
    "discover_chat_id",
    "format_alert",
]
