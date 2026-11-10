# ADR 0006：解析层离线 fixtures 测试

- 状态：已采纳（2026-11-08）
- 决策：抓取/解析类代码的单测使用内联样本（RSS XML、Binance/OKX/Bybit JSON、Telegram 预览页、资金费率、稳定币），不打网络；CI 完全不联网。
- 理由：外部源会限流/改版/抖动，联网测试不可复现；解析逻辑才是我们自己的代码，用固定样本能稳定回归。
- 影响：`libs/crypto_core/tests/test_sources_market.py` 覆盖全部解析器；端到端抓取只在本地可选冒烟（`verify.sh --with-network`）。
