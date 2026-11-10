# ADR 0003：单根 .venv + 零安装共享库

- 状态：已采纳（2026-11-08）
- 决策：仓库根 `.venv` 为唯一开发环境（只装 ruff/PyYAML）；`libs/crypto_core` 不做 pip 包，通过加 `sys.path`（模块入口探测仓库根）或 `PYTHONPATH` 使用。
- 理由：零网络、零安装；系统 python3 也能跑；避免每模块重建 venv 与版本漂移。
- 退路：未来某模块需要锁不同依赖版本时，该模块可自带 venv，其余模块不受影响。
