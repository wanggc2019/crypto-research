# docs/crypto-onchain · 链上/交易所"卖压归因"（M2 归因引擎 + walletflow 与事件驱动告警）

> 状态：**M2 已落地（2026-11-09）**：在 M1 的 4 类免费信号采集 + 影子落盘之上，新增**归因引擎**（4 类卖压类别，每类固定 `evidence` / `counter_evidence` / `unknown` 三段）、**跨轮 OI 历史**（`data/state/oi-history.json`）与**跨模块导出契约** `onchain/pressure/v1`（`data/exports/onchain-pressure.json`）；**每日影跑摘要**（21:00 推一条"今天观察到什么"的汇总；2026-11-10 起为**链上情报分析**：净流/对倒噪声过滤/单向大额，**逐笔告警默认关闭（B 模式：仅单向未知 ≥$500M 或 1h 交易所净流入异常才即时提醒，≤2 条/天）**）与 **150 项离线单测**（indicators 18 / signals 22 / attribution 15 / digest 7 / walletflow 12 / walletflow_alerts 23 / context 9 / bot_env 4 / e2e 5）；并落地 **M3 附加能力「链上大额转账监控」（walletflow，公共 RPC 免 key）**，其**事件驱动 Telegram 告警已实现（**默认关闭**，改由每日摘要输出链上情报分析）**（每轮新增 ≥$10M、仅 P0 推送、全天即时（P1 不推送，仍落盘/进摘要）、常规日限额、特殊期解除日限额、明确标注的“推测”备注；**2026-11-10 消息增强**：代币数量/区块/地址标签/背景/交易链接，标签可来自内置清单或 Etherscan 地址页公开 name tag，仅 ETH、缓存 30 天、dry-run 不联网）。**4 类卖压候选仍只落盘不推送**：其 Telegram 推送、卡片渲染、去重冷却/静默/日限额、feedback 与校准回算属 M3 规划，2 周影子校准达标后才启动。
> 文档五件套：需求 ✅ · 设计 ✅ · 业务架构 ✅ · 技术架构 ✅（均在本目录内，walletflow + 事件驱动告警已同步）。
> 关联：[根 README 模块约定](../../README.md) · [架构设计 §5.3](../architecture.md) · [约定](../conventions.md) · [crypto-price 需求](../crypto-price/requirement.md)

## 文档

| 文件 | 内容 |
|---|---|
| [requirement.md](requirement.md) | **需求评估正文**：能/不能看到"谁"、免费源能力矩阵、卖压代理信号、归因规则与置信度、walletflow 阈值与事件驱动告警、误报控制、边界、验收、成本、里程碑 |
| [design.md](design.md) | 设计：数据源与端点、指标/信号公式、**归因引擎判定顺序与三段输出**、**OI 历史**、**链上大额转账（walletflow）解码/阈值/游标/降级**、**事件驱动告警（分级/全天即时/常规日限额/特殊期解除限额/聚合/推测/消息字段与地址标签补全）**、**exports `onchain/pressure/v1` 字段表**、候选结构、影子产物、失败模式、测试方案 |
| [architecture.md](architecture.md) | 业务架构图（Mermaid）：业务能力、数据流（含归因层 + OI 历史 + walletflow 记录/告警 + exports）、候选信号生命周期、与 price/news/analyst 的关系 |
| [technical.md](technical.md) | 模块技术架构：代码结构（含 `attribution.py`、`walletflow.py`、`walletflow_alerts.py`）、组件职责、一次运行调用链、数据/状态文件（含游标/告警状态/地址标签缓存）、退出码、测试 |

## 一句话

CEX 匿名撮合 + 链上假名 + 实体标签付费 ⇒ **"谁在卖"没有可靠答案**；本模块只做可检验的**卖压类别归因**：多头被强平（被动卖盘）/ 空头加仓 / 主动砸盘 / 增量资金撤离 / 去杠杆（反证）。每条结论固定输出"证据 + 反证条件 + 不可知说明"，**只回答类别，不回答谁**。另有 **M3 附加能力 walletflow**：公共 RPC 观测 ETH 主网 + BSC 的 USDT/USDC 大额转账（记录阈值 ETH ≥$10M / BSC ≥$5M，默认排除 mint/burn），只回答"链上发生了什么大额转账"，**不回答"地址属于谁"**；每轮新增 ≥$10M 即发事件驱动 Telegram 告警（仅 P0 推送、全天即时（P1 不推送，仍落盘/进摘要）、常规日限额、特殊期解除日限额），方向 `unknown` 可带**明确标注的“推测”**备注（不做实体断言）；**2026-11-10 消息增强**：每条含代币数量/区块/地址标签/背景/Etherscan·BscScan 交易链接，标签来自内置清单或 Etherscan 地址页公开 name tag（仅 ETH、缓存 30 天、dry-run 不联网）。

## 当前结论（详见 requirement.md）

- **免费可行**：DefiLlama `/cexs` 交易所净流（全币种聚合 USD）、Binance 现货 `aggTrades` 主动卖占比与大单、OKX 清算 REST、DefiLlama 稳定币净增发；`crypto-price` exports 提供价格/24h 涨跌/**资金费率/OI**（M2 起读取，旧字段不变）。
- **必须付费**：实体地址标签（Arkham ≥$100/mo）、分币种交易所余额（CryptoQuant ≥$99/mo）、全市场清算（CoinGlass ≥$29/mo）、Glassnode API（≥$584/mo）→ 默认全关。
- **M2 已交付**：归因引擎（4 类 + 三段输出，资产级 + 市场级）、OI 历史（24h / 400 点上限）、`onchain/pressure/v1` 导出（**目前无下游消费**，crypto-analyst 规划中）、150 项离线单测；**每日影跑摘要**（21:00，只汇总不告警）已由 `collect.sh` 接入；healthcheck / 单实例锁 / 每日 prune 同样已接入。
- **M3 附加能力（记录 + 告警已上线）**：`walletflow` 用公共 RPC `eth_getLogs` 扫 ETH 主网 + BSC 的 USDT/USDC 大额转账（ETH ≥$10M / BSC ≥$5M），免 key、$0；默认排除 mint/burn；游标 `data/state/walletflow-cursor.json`（`{chains: {ethereum, bsc}}`）+ ETH 单轮增量 ≤120 块 / BSC ≤900 块；快照/exports/每日摘要均含 `walletflow`（只增字段），明细落 `data/raw/<date>/walletflow.jsonl`；**不识别地址归属/实体**，手工 `walletflow.labels` 仅标方向（`cex_in`/`cex_out`）；RPC 失败只记 `walletflow.error`、不阻塞、不改 rc。
- **链上大额转账告警（已上线，2026-11-09 口径调整）**：每轮新增达到 `alerts.min_usd=$10M` 即发一条聚合 Telegram；P0 = 单笔 ≥$50M 或转入交易所且 ≥$25M。**`quiet_hours` 默认空 `""` = 全天即时**，仅 P0 立即发送；P1 不推送（仍落盘/进摘要）（送达即时，由接收端自行处理）；该配置仍可临时配回时段，非空时仅 P1 抑制、P0 穿透。P1 不推送；常规 ≤10 条消息/天（特殊期解除）；**巨鲸保护**：单笔 ≥$100M 或入所 ≥$50M 额外最多 5 条/天（标「🐋 巨鲸」）；**特殊期解除日限额**（`special.*` 命中任一：当日 P0 笔数 ≥600、|BTC 24h 涨跌幅| ≥6%、CEX 24h 净流出 ≥150bps、稳定币 24h 净赎回 ≥15 亿美元，或 `manual=true` 手工强制）→ 解除常规 10 条但仍有特殊期硬上限 30 条/天，仍保留去重与单轮 5 笔，消息标注「⚠ 特殊期：日限额已解除」。发送成功才计入状态，失败进 `pending` 下轮重试，`--dry-run` 只预览不写状态。同一 tx 多腿只保留 USD 最大一腿；**允许方向 `unknown` 时带“推测”字样的低置信备注（如“疑似与 Binance 14 关联的地址”），不做实体断言**；BSC 无免费标签源，Etherscan key 未使用。**2026-11-10 消息增强**：每条聚合行含数量（千分位）/区块/from→to/地址标签/方向/背景/交易链接；地址标签渲染时优先内置 `walletflow.labels`，Etherscan 地址页公开 name tag 作为补充（仅 ETH、缓存 `data/state/address-tags.json` 30 天、失败忽略），仍只作展示、不做实体断言；`--dry-run` 预览不联网。
- **M3 规划（仅 4 类卖压候选）**：2 周影子校准（精确率 ≥60%、日均候选 ≤3）→ 达标后 Telegram 推送/卡片、去重冷却/静默/日限额、feedback 与校准回算；未达标降级为日报摘要。
