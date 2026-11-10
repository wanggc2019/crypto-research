# ADR 0002：共享能力下沉到 libs/crypto_core

- 状态：已采纳（2026-11-08）
- 决策：模块只向下依赖共享库；共享库不依赖模块、不做业务决策；模块之间零 import。
- 抽取原则：第二个使用者出现才抽（Rule of Two）；抽能力不抽策略；抽取必须带测试。
- 影响：P0/P1 已抽出 paths/env/config/timeutil/store/text/events/dedupe/notify；http/sources/market 等 P2 再抽。
