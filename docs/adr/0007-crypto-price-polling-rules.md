# ADR 0007：crypto-price 用 REST 轮询 + 多源核验 + 规则引擎

- 状态：已采纳（2026-11-08）
- 背景：用户需要 BTC/ETH/BNB 价格剧烈波动时实时告警，零成本、可在 Mac 上跑，还要避免单交易所数据失真与刷屏。
- 决策：
  1. **REST 轮询而非 WebSocket**：Python 3.9 标准库没有 WebSocket 客户端，引入第三方库违反"零依赖/零安装"约定；60 秒轮询 + 1m K 线回看足够覆盖"剧烈波动"（1m/5m/15m/1h 窗口），不追求毫秒级。
  2. **多源中位数 + 价差核验**：Binance 主源（K 线/24h 统计最全）、OKX 第二源、Coinbase 第三源；单源失败自动降级。价差 >50bps 抑制价格类告警，>100bps 视为数据不可用——宁可漏报一次，也不基于失真价格发错误信号。
  3. **规则引擎而非 ML/预测**：8 类可解释规则（急涨急跌/24h 波动/新极值/放量/资金费率/OI/关键位/数据质量），阈值全部放 `config/rules.yaml`；可回放、可单测、用户可自己调。
  4. **launchd StartInterval=60 而非 KeepAlive 常驻**：短生命周期进程不会在 Mac 休眠后被系统挂起成"假活"；唤醒后 launchd 自动补跑；单实例锁防止重叠。常驻需求可在 VPS 用 `--loop`。
  5. **送达治理复用 libs**：alert/v1 事件模型、Telegram 发送、静默/去重能力复用 `crypto_core`；价格专属的分级/冷却/早报/卡片留在模块内，不进 libs。
- 影响：
  - 新增 libs 行情解析器 `market/{okx,coinbase}.py` 与 Binance 24h/K 线/OI 解析器（带内联样本单测），未来 `crypto-onchain`/`crypto-analyst` 可复用。
  - `notify.send_message` 增加可选 `silent=True`（静默测试消息），向后兼容。
  - 新增模块 `crypto-price/`，独立配置/测试/launchd Label/CI job；与 `crypto-news` 零 import、零耦合。
- 代价/取舍：
  - 60 秒采样可能漏掉瞬时插针（用 1m K 线窗口缓解；可把 `poll_seconds` 调到 30）。
  - Mac 合盖时不会监控（launchd 唤醒补跑）；7×24 需要 VPS（Runbook 已给后路）。
  - 不做清算数据（Binance 强平接口需要鉴权/付费），用资金费率 + OI + 放量作为杠杆拥挤的代理指标。
