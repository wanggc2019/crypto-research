# 设计：crypto-onchain —— 交易所/链上"卖压归因"（M2 归因引擎 + walletflow 与事件驱动告警）

> 本文是 crypto-onchain 的**实现设计**：数据源与端点、指标公式、信号阈值与方向门控、**归因引擎判定顺序与置信度**、**OI 跨轮历史**、**链上大额转账监控（walletflow）与事件驱动告警**、**`onchain/pressure/v1` 导出契约**、候选结构、影子产物、失败模式、测试方案与演进边界。
> 需求与产品边界见 [requirement.md](requirement.md)；业务架构见 [architecture.md](architecture.md)；模块内技术架构见 [technical.md](technical.md)；代码入口见模块 [README.md](../../crypto-onchain/README.md)。
> 当前阶段为 **M2 影子模式（4 类卖压候选仍只落盘、不推送）**：其 Telegram 推送、卡片渲染、去重冷却/静默/日限额、feedback 与校准回算属 M3 规划；launchd 影子任务已安装（300s，`collect.sh` 含单实例锁、healthcheck、每日 prune）。**链上大额转账（walletflow）与其实时告警已上线**：公共 RPC 免 key，记录阈值 ETH $10M / BSC $5M；每轮新增 ≥$10M 发一条聚合 Telegram，仅 P0 推送、全天即时（P1 不推送，仍落盘/进摘要）（`quiet_hours` 默认空）、常规日限额可扩容、特殊期解除日限额；方向 `unknown` 可带**明确标注的“推测”**备注（不做实体断言）。**2026-11-10 告警消息增强**：每条聚合行含标题（图标+分级+币种+金额+链）、代币数量（千分位）与区块号、from→to（缩写）、地址标签（内置优先，其次抓 Etherscan 地址页公开 name tag，仅 ETH，缓存 `data/state/address-tags.json` 30 天，失败忽略，两边都无写“双方均无公开标签”）、方向、背景（今日第 N 笔/累计/占比、近 7 天 from/to 频次、同 tx 多腿提示）与 Etherscan/BscScan 交易链接；`alerts.enrich` 仅真实发送时抓取，`--dry-run` 预览不联网。

## 1. 定位与口径

### 1.1 只回答"类别"，不回答"谁"

CEX 匿名撮合只暴露成交量、链上地址是假名、实体标签是付费能力，因此"谁在卖"没有可靠答案。本模块把问题降级为**卖压类别**：多头被强平（被动卖盘）/ 空头加仓 / 主动砸盘 / 增量资金撤离 / 去杠杆（反证）。归因结论只描述类别、证据数值、时间窗与反证条件，**永不出现任何主体指认**；每条结论的 `unknown` 段固定以"无法判断卖方身份（CEX 匿名撮合、链上地址假名）"开头（`tests/test_attribution.py` 与 `tests/test_run_fixtures.py` 有身份词黑名单断言）。

### 1.2 四类信号与归因层

| # | 信号名 | 数据源 | 口径 | `scope` |
|---|---|---|---|---|
| 1 | `taker_sell_pressure` | Binance 现货 `/api/v3/aggTrades` | **单所逐笔**：`isBuyerMaker` 判 taker 方向，5 分钟窗口 | `asset` |
| 2 | `cex_reserve_outflow` | DefiLlama `api.llama.fi/cexs` | **全币种聚合 USD**，关注 CEX 合计；不能断言某个币种进出 | `cex` |
| 3 | `liquidation_cascade` | OKX `/api/v5/public/liquidation-orders` | **仅 OKX 口径**；USD = 张数 × ctVal × 价格 | `asset` |
| 4 | `stablecoin_outflow` | DefiLlama `stablecoins.llama.fi` | 关注稳定币的**合计净增发**（circulating − prevDay） | `market` |

归因层（`scripts/attribution.py`）不新增数据源，它消费上述指标 + `crypto-price` 的 `funding_rate` / `oi` + 跨轮 OI 变化，把"单信号候选"组合为四类结论（§5）。信号名曾用 `cex_netflow` / `stablecoin_issuance`，2026-11-09 统一为 `cex_reserve_outflow` / `stablecoin_outflow`；其余语义不变。

另有**辅助观测能力**：`walletflow`（链上大额转账监控，§9.6）走公共 RPC `eth_getLogs` 扫 USDT/USDC 的 Transfer 日志，只记录单笔 ≥$10M（ETH 主网）/ ≥$5M（BSC）的转账并默认排除 mint/burn；它**不是第五类卖压信号**，不参与候选/归因判定，只进快照、exports（只增字段）与每日摘要。它只回答"链上发生了什么大额转账"，**不回答"地址属于谁"**。**与告警的分工**：`walletflow.py` 负责"看见"（记录/方向/游标，落盘方向不猜）；`walletflow_alerts.py`（§9.7）负责"通知"——每轮新增 ≥`alerts.min_usd=$10M` 即发一条聚合 Telegram，允许在方向 `unknown` 时加**明确标注的“推测”**备注（基于 7 天历史关联/频次，只作线索、不做实体断言）；告警消息字段与标签补全见 §9.7.3 与 §9.7.6。

### 1.3 红线（写代码/写文案前先看）

- **不做主体推断**：不识别、不推断、不暗示个人或机构身份；只写"主动卖盘占比升高""多头被强平"等类别。
- **不做地址归属推断**：`walletflow` 的**数据记录**（快照/明细/exports）不带任何实体/归属字段；`walletflow.labels` 内置 23 个已验证地址（21 cex / 1 mm / 1 issuer）（官方清单 + Etherscan name tag 交叉核对，2026-11-09），命中后仅标注方向（`cex_in`/`cex_out`/`cex_internal`），绝不输出"某机构/某巨鲸"；Etherscan key 未使用（公共 RPC 免 key）。**告警文案**里的标签可来自内置清单，或发送时抓取 Etherscan 地址页公开 name tag（仅 ETH、页面抓取、免 key、缓存 30 天，见 §9.7.6），**只作展示**、不写入数据记录、不做实体断言。**BSC 无免费标签源**，内置标签均为 ETH 主网地址，BSC 方向基本为 `unknown`。
- **允许“推测”但必须标注（设计要求）**：仅当方向 `unknown` 时，`walletflow_alerts` 的**告警文案**可在括号内加低置信推测（如“unknown（推测：疑似与 Binance 14 关联的地址）”“疑似做市商/高频机构地址”）；依据是 7 天历史同现 ≥3 次或出现 ≥5 次；**猜不到就不写**。推测永远带“推测/疑似”字样、只作线索，**绝不当结论、不做实体/身份断言**，且不写入数据记录字段。
- **不接付费源**：实体标签（Arkham/Nansen）、分币种交易所余额（CryptoQuant）、Glassnode、CoinGlass 默认全关；不接 Binance 清算 WebSocket；不自建全链索引。
- **不重复采集**：价格/24h 涨跌/资金费率/OI 只读 `crypto-price` 的 exports；ETF 流向与鲸鱼新闻引用 `crypto-news`，不重采（M2 尚未接入引用）。
- **口径必须随结论走**：交易所净流必须标注"全币种聚合 USD"，清算必须标注"仅 OKX 口径"，稳定币净增发要提示跨链 mint/burn 噪音，OI 必须标注"Binance USD-M 单所"。
- **方向不一致不打扰**：候选的 `severity`（本会触发的级别）照常记录，但 `aligned=false` 时 `would_alert=false`；归因是附加信息，不改变候选的产生与分级。

## 2. 数据源与端点

全部数据源**免 API key、$0**；当前使用下表 9 个入口（M2 的 6 个 + 本地 OI 历史 + walletflow 的公共 RPC + 告警补标签的 Etherscan 地址页），其余（Binance 永续逐笔、mempool.space、Etherscan API 等）未接入。

| 源 | 端点 / 读取方式 | 用途 | 约束与预算（以配置为准） | 解析器 |
|---|---|---|---|---|
| Binance 现货 | `GET https://api.binance.com/api/v3/aggTrades?symbol=BTCUSDT&limit=1000[&endTime=ms]` | 逐笔成交 + taker 主动方向，回补 900 秒 | 权重 4/页；`limit` ≤ 1000；每币对每轮最多 12 页（配置 `request_limit`/`max_pages`/`window_seconds`）；超时 8 秒、不重试 | `libs/crypto_core/market/binance.py` 的 `parse_agg_trades` |
| DefiLlama CEX | `GET https://api.llama.fi/cexs` | 关注交易所的 `currentTvl`、`inflows_24h/1w/1m` | 超时 12 秒、重试 1 次；值缺失为 `None` | `market/defillama.py` 的 `parse_cex_flows` |
| DefiLlama 稳定币 | `GET https://stablecoins.llama.fi/stablecoins?includePrices=true` | 关注币种的 `circulating` / `circulatingPrevDay` / `circulatingPrevWeek` | 超时 12 秒、重试 1 次；只取 `rules.stablecoin.symbols` | `market/defillama.py` 的 `parse_stablecoin_supply` |
| OKX 清算 | `GET https://www.okx.com/api/v5/public/liquidation-orders?instType=SWAP&uly=BTC-USDT&state=filled&limit=100` | 已成交清算明细（方向/价格/张数/时间） | `limit` 单次最多 100；超时 8 秒、重试 1 次；**仅 OKX** | `market/okx.py` 的 `parse_liquidations` |
| crypto-price exports | 只读 `../crypto-price/data/exports/latest.json`（相对模块根，配置 `sources.price_exports.path`） | 价格、24h 涨跌幅、24h 成交量作行情背景；**`funding_rate` 与 `oi` 作归因输入** | 超过 `max_age_seconds: 900` 标记 `fresh=false`；缺失/损坏则降级 | 模块 `scripts/common.py` 的 `load_price_context` |
| Binance OI 历史（主） | `GET https://fapi.binance.com/futures/data/openInterestHist?symbol=BTCUSDT&period=15m&limit=3` | 官方 15m OI 历史，算 15m 变化 | 免 key；每币对每轮 1 次请求（`sources.binance.oi_hist`）；不受本机休眠造成的采样断点影响 | `market/binance.py` 的 `parse_open_interest_hist` |
| 本地 OI 历史（兜底） | `data/state/oi-history.json`（相对模块 `data/`，随 `--data-dir` 重定向） | 跨轮累计 `oi`，官方端点失败时兜底 + 交叉校验 | 保留 24h / 每资产最多 400 点；`--dry-run` 只读不写 | 模块 `scripts/run.py` 的 `_oi_changes` + `indicators.oi_change_pct` |
| **公共 RPC `eth_getLogs`**（walletflow） | `POST https://ethereum-rpc.publicnode.com`、`https://bsc-rpc.publicnode.com`（`eth_blockNumber` / `eth_getLogs` / `eth_getBlockByNumber`，支持 batch 时间戳） | 扫 ETH+BSC 的 USDT/USDC Transfer 日志（`topic0=keccak Transfer`），单笔 ≥$10M（ETH）/ ≥$5M（BSC）；ETH 增量 ≤120 块、BSC ≤900 块 | 免 key、$0；ETH 30 块/次、BSC 300 块/次（节点报“范围过大”时自动对半拆分）；单轮约 20–40 次调用；超时 25 秒；失败只降级该链 | `scripts/walletflow.py` 内置 JSON-RPC（标准库 `urllib`，不依赖 libs `http`） |
| **Etherscan 地址页**（仅告警补标签，`alerts.enrich`） | `GET https://etherscan.io/address/<addr>`（读页面 HTML，正则取 name tag） | 告警渲染时给地址补浏览器公开 name tag；**不参与信号/记录/对账** | 免 key、$0；**仅 ETH**（BSC 不抓）；超时 6 秒、0 重试；结果缓存 `data/state/address-tags.json`（TTL 30 天）；`--dry-run` 不请求 | `scripts/walletflow_alerts.py` 的 `parse_etherscan_tag`（模块内正则，无 libs 解析器） |

`crypto-price` 的导出契约（`price/snapshot/v1`）在 M2 只**新增**字段：`assets[*].funding_rate`、`funding_ts`、`oi`、`oi_ts`，旧消费者可忽略。本模块当前只读取 `funding_rate` 与 `oi`（`funding_ts`/`oi_ts` 未使用，留给 M3 做过期判定）。

端点与窗口配置在 [config/app.yaml](../../crypto-onchain/config/app.yaml)，标的与合约面值在 [config/watchlist.yaml](../../crypto-onchain/config/watchlist.yaml)。二模块**不**使用 Binance 永续 `/fapi/v1/aggTrades`（权重更高）、不接 Binance `!forceOrder@arr`（仅 WebSocket）、不接 mempool.space；稳定币只做净增发，不做价格脱锚信号。walletflow **不使用 Etherscan key**：env 的 `ETHERSCAN_API_KEY` 保留为备用源（当前代码不读取）；告警补标签走公开地址页抓取（非官方 API、免 key、best-effort），仍不使用 key。

## 3. 指标与信号公式

所有金额为等值 USD（`float`）、比例 0–1（`float`）、缺失为 `None` 而非 0（避免把"缺数据"误读成"指标为 0"）。指标计算集中在 [scripts/indicators.py](../../crypto-onchain/scripts/indicators.py)，判级集中在 [scripts/signals.py](../../crypto-onchain/scripts/signals.py)。

### 3.1 `taker_sell_pressure`（逐笔主动卖压）

**计算**（窗口 300 秒；同一轮还会计算 900 秒窗口，但 15 分钟窗口只记录、不判级）：

- 逐笔等值额 `quote_qty = price × qty`；
- taker 方向：Binance `m=true` 表示买方是 maker，即**主动方为卖方** → `taker_side="sell"`；`m=false` → `"buy"`；
- `sell_ratio = Σ(sell 的 quote_qty) / Σ(所有 quote_qty)`；
- 大单：单笔 `quote_qty ≥ large_trade_usd[asset]`；大额卖单笔数 `large_sell_count`。

**判级**（阈值全部来自 `rules.taker`）：

| 级别 | 条件（同时成立） |
|---|---|
| P1 | `sell_ratio ≥ p1_ratio(0.58)` 且 `large_sell_count ≥ p1_min_large_sells(3)` |
| P0 | `sell_ratio ≥ p0_ratio(0.65)` 且 `large_sell_count ≥ p0_min_large_sells(6)` |
| 样本门槛 | 5m 成交额 `quote_volume_usd ≥ min_window_quote_usd[asset]`，否则本轮不出候选（归因层同样忽略该主动卖占比） |

**记录但不参与判级**：`window_change_pct`（5m 窗口首末价变化）、`large_sell_usd`、`buy/sell_quote_usd`、`coverage_seconds` 等写入指标与 `extra`，供影子回算。需求中的"占比连续 3 个 5m 窗口"与"P0 要求 5m 跌幅 ≥0.8%"属于**未实现**的 AND 条件（模块不做 taker 跨轮窗口状态），见 §12。

### 3.2 `cex_reserve_outflow`（交易所储备净流）

**计算**（数据源 `parse_cex_flows`，字段 `inflows_24h/1w/1m`、`currentTvl`）：

- 只保留 `watchlist.cex_watch` 名单内的交易所（大小写不敏感匹配 `name`）；
- TVL 低于 `rules.cex_flow.min_tvl_usd`（10 亿美元）的交易所不参评（`eligible=false`）；
- 对参评交易所分别求和：

```
tvl_usd        = Σ eligible.tvl
net_24h_usd    = Σ eligible.inflows_24h
net_24h_bps    = net_24h_usd / tvl_usd × 10000
net_1w_bps     = Σ eligible.inflows_1w / tvl_usd × 10000
```

符号约定：DefiLlama `inflows_*` **正数 = 流入交易所**，负数 = 流出；候选只在净流出方向产生。

**判级**（`rules.cex_flow`）：

| 级别 | 条件 |
|---|---|
| P0 | `net_24h_bps ≤ -p0_net_24h_bps_tvl(-150 bps)` |
| P1 | `net_24h_bps ≤ -p1_net_24h_bps_tvl(-50 bps)`，或 `net_1w_bps ≤ -p1_net_1w_bps_tvl(-400 bps)` |
| 样本门槛 | `n_eligible > 0`，否则不出候选 |

**口径限制**：这是全币种聚合 USD，只能回答"交易所储备在流出"，不能回答"哪个币种被转出/转入"，也不能推出"提币即卖出"。

### 3.3 `liquidation_cascade`（强平瀑布）

**计算**（数据源 `parse_liquidations`；`size` 为合约张数，`price` 取破产价 `bkPx`）：

```
notional_usd = size × contract_value × price
```

- `contract_value` 来自 `watchlist.assets[asset].contract_value`（即 OKX USDT 本位永续的 ctVal：BTC 0.01、ETH 0.1、BNB 0.01，单位分别为币/张）；
- `posSide=long` 的清算计入 `long_usd`（多头被强平 → 被动卖盘）；`posSide=short` 计入 `short_usd`（空头被强平 → 被动买盘）；
- 统计窗口 `rules.liquidations.window_minutes`（15 分钟）；`coverage_minutes` 用**返回的全部样本**首末时间差计算，是"本次样本自身覆盖时长"；
- 价格优先取 `crypto-price exports` 的 `price`，缺失时回退到 5m 逐笔的最后一笔价格；两者都没有时 `usd_known=false`，不出候选。

**判级**（`rules.liquidations`，按资产分层）：

| 级别 | BTC | ETH | BNB |
|---|---|---|---|
| P1 `p1_usd` | 30,000,000 | 15,000,000 | 8,000,000 |
| P0 `p0_usd` | 100,000,000 | 50,000,000 | 25,000,000 |

**覆盖门槛**：`coverage_minutes < min_coverage_minutes(5)` 时不出候选，防止拿几十秒样本下结论；`coverage_minutes` 为 `None`（仅 1 条样本）时不套用该门槛，靠金额阈值与影子回算兜底。

**方向与类别**：`long_ratio = long_usd / total_usd`；`long_ratio ≥ long_bias_ratio(0.6)` 归为"多头被强平（被动卖盘）"，否则为"空头被强平（被动买盘）"；`long_ratio` 缺失时为"多空混合强平"。类别只进候选 `note`/`extra`，不指认主体。**仅 OKX 口径 ≠ 全市场**。M2 归因层复用同一份 15m 清算额与 `long_ratio`，但**不重复检查 `coverage_minutes`**（覆盖门槛在信号层生效，归因层只按金额与多头占比判定）。

### 3.4 `stablecoin_outflow`（稳定币净赎回）

**计算**（数据源 `parse_stablecoin_supply`，只取 `rules.stablecoin.symbols`：USDT/USDC/USDe/FDUSD）：

```
net_issue_24h_usd = Σ (circulating - circulatingPrevDay)   # DefiLlama peggedUSD
net_issue_1w_usd  = Σ (circulating - circulatingPrevWeek)
```

正数 = 净增发（增量资金进场），负数 = 净赎回（资金撤退）。候选只在净赎回方向产生。

**判级**（`rules.stablecoin`）：

| 级别 | 条件 |
|---|---|
| P1 | `net_issue_24h_usd ≤ p1_net_issue_24h_usd(-5 亿美元)` |
| P0 | `net_issue_24h_usd ≤ p0_net_issue_24h_usd(-15 亿美元)` **且** BTC 24h 涨跌幅 `≤ -p0_requires_btc_drop_pct(-3.0%)` |
| 样本门槛 | `n > 0`，否则不出候选 |

**口径限制**：跨链桥 mint/burn 会造成"看起来净增发/净赎回"的噪音；只作宏观背景，不指认资金去向。

### 3.5 `oi_change_pct`（OI 变化，M2 新增）

`indicators.oi_change_pct(points, now_ts, window_minutes=15, tolerance_minutes=10)` 是纯函数：

- 输入 `points` 为 `[[ts, oi], ...]` 或 `[{"ts": ..., "oi": ...}, ...]`；无效行、`oi ≤ 0` 跳过；
- 样本 < 2 返回 `None`；
- `target = now_ts − window_minutes × 60`；取 `ts ≤ target` 中**最近**的一个点作为"过去 OI"，与 target 的距离超过 `tolerance_minutes × 60` 视为历史不足，返回 `None`；
- 当前 OI 取列表 `ts` 最大的点；若该点比 target 还早，返回 `None`；
- 返回百分比：`(current_oi − past_oi) / past_oi × 100`（`-3.5` 表示 15 分钟 OI 下降 3.5%）。

该指标不联网、不写盘；输入优先用官方 `openInterestHist`（15m 周期，容差放宽到 20 分钟以适配采样边界），官方不可用时用 `run.py` 的本地跨轮历史兜底（§4）。

## 4. OI 历史（官方优先 + 本地兜底）

数据来源优先级：① Binance 官方 `openInterestHist`（主，免 key，15m 周期）；② 本模块本地跨轮历史 `oi-history.json`（兜底 + 交叉校验）。官方端点返回的 `oi` 为 USD-M 全市场 `sumOpenInterest`，与 `crypto-price` exports 的 `oi` 同源（Binance USD-M）。

### 4.1 本地兜底文件与结构

`data/state/oi-history.json`（相对模块 `data/`；`--data-dir DIR` 会整体重定向到 `DIR/state/oi-history.json`）：

```json
{
  "BTC": [[1794176000, 99000.0], [1794176300, 98100.0]],
  "ETH": [[1794176000, 1250000.0], [1794176300, 1205000.0]]
}
```

- 键为 watchlist 资产（BTC/ETH/BNB），值为按时间升序的 `[epoch 秒, oi]` 列表；
- 文件缺失/损坏/非对象时一律按空处理（`common.safe_json_load` 返回 `{}`）；
- 这是本模块**唯一的跨轮状态**，模块不保存 taker/清算的跨轮窗口。

### 4.2 每轮写入流程（`run._oi_changes`）

1. 读 `rules.attribution.oi_history_hours`（默认 24）与 `oi_window_minutes`（默认 15）；
2. 读历史文件，只保留 `ts ≥ now_ts − 24h` 的点；
3. **优先**用本轮抓到的官方 `openInterestHist` 点（`period=15m`、`limit=3`）算 15m 变化，成功则 `oi_source="binance_hist"`；
4. 官方不可用（请求失败/解析为空/历史不足）时，若本轮资产快照的 `oi` 非空，追加 `[now_ts, oi]`（来自 `crypto-price` exports 的 `assets[asset].oi`）后调 `indicators.oi_change_pct(points, now_ts, window_minutes=15)`，`oi_source="local"`；
5. 每资产只保留最近 **400** 点（24h × 5 分钟 ≈ 288 点，留余量）；
6. **非 `--dry-run`** 时覆盖写回文件（写入失败 `OSError` 被静默吞掉，不影响本轮）；`--dry-run` 只读不写。

写回时机在归因**之前**，所以本轮刚追加的 `oi` 会立即参与 15m 变化计算；一轮失败（无 `oi`）不会追加空点。首次非 dry-run 轮次会创建该文件（内容为 `{各资产: []}`）。

### 4.3 降级语义

| 场景 | `oi_change_15m_pct` | 对归因的影响 |
|---|---|---|
| OI 缺失（price exports 无 `oi`） | `null` | 多头强平只能给 `medium`（无法凭 OI 升降升 `high`/降 `low`）；`short_add` 保持 `medium`；去杠杆无法成立 |
| 官方 OI 历史不足（< 2 点 / 距目标 > 20 分钟）| 尝试本地兜底 | 本地也失败才 `null`；不会硬编一个假变化 |
| 本地历史不足（< 2 点 / 距目标 > 10 分钟） | `null` | 同上；不会硬编一个假变化 |
| 正常 | 数值（%） | 按 §5 的置信度规则升降 |

`oi_change_15m_pct` 与来源 `oi_source`（`binance_hist` / `local` / `null`）会写进快照的每个资产与 `onchain/pressure/v1` 的对应字段，`null` 表示不可得。

## 5. 归因引擎（M2）

### 5.1 输入、开关与阈值

入口：`attribution.build_attributions(assets, cex_flows, stablecoins, rules, directions, oi_changes)` → `{"assets": {asset: attribution|None}, "market": attribution|None}`。

单资产输入（快照 `assets[asset]` 的字段 + 单独传入的 OI 变化）：`direction`、`change_24h_pct`、`taker_5m`（`sell_ratio`/`quote_volume_usd`/`large_sell_count`）、`liq_15m`（`total_usd`/`long_ratio`/`coverage_minutes`）、`funding_rate`、`oi_change_15m_pct`。

- **价格门控**：`direction == "down"` 或 `change_24h_pct ≤ -0.5` 才输出资产级归因；否则返回 `None`（不输出"买盘承接"类结论）。
- **开关**：`rules.attribution.enabled=false` 时资产级与市场级归因全部返回 `None`（`attribute_market` 同样读取该开关）。
- **阈值来源**：`taker.p1_ratio`（0.58）、`taker.min_window_quote_usd[asset]`、`liquidations.p1_usd[asset]`、`liquidations.long_bias_ratio`（0.6）、`cex_flow.p1_net_24h_bps_tvl`（50）、`stablecoin.p1_net_issue_24h_usd`（−5 亿）、`attribution.oi_significant_pct`（2.0）；函数内默认值仅作键缺失兜底，与 YAML 初值一致。
- **taker 样本门槛**：5m 成交额低于 `min_window_quote_usd[asset]` 时，主动卖占比不参与归因（视作 `None`）。

### 5.2 判定顺序与置信度（先匹配先生效）

| 顺序 | `category_code` | 中文类别 | 触发条件（全部满足） | 置信度 |
|---|---|---|---|---|
| 1 | `long_liquidation` | 多头被强平（被动卖盘） | 15m 清算额 ≥ `p1_usd[asset]` 且 `long_ratio ≥ 0.6` | OI 15m ≤ −2% → `high`；OI > 0 → `low`；其余（含 OI 未知、OI 在 (−2%, 0]）→ `medium` |
| 2 | `short_add` | 空头加仓 / 主动砸盘 | 5m 主动卖占比 ≥ 0.58 且 OI 未显著下降（`oi is None` 或 `oi ≥ −2%`） | 默认 `medium`；若 OI ≥ 2% 且资金费率 ≤ 0 → `high`（资金费率缺失仍 `medium`） |
| 3 | `capital_exit` | 增量资金撤离（卖压背景） | `cex_net_24h_bps ≤ −50` 且 `stablecoin_net_issue_24h_usd ≤ −5 亿`（两者都非空） | `medium`（市场级见 §5.4） |
| 4 | `deleverage` | 去杠杆（非新增抛售，反证） | OI 15m ≤ −2%，且 taker 无显著主动卖（`None` 或 < 0.58），且 CEX 净流未达流出门槛（`None` 或 ≥ −50bps） | `medium` |

- 四个分支自上而下判断，**第一个满足的返回，不再看后面的类别**；都不满足返回 `None`。因此"OI 下降 + 清算多头为主"优先归为强平，而"OI 下降 + 资金流出"优先归为资金撤离（资金撤离在去杠杆之前判定）。
- `long_liquidation` 的 `medium` 分支覆盖 OI 未知；这正是 OI 历史不足时的降级路径（§4.3）。
- 归因层**不复用信号层的 P0/P1 分级**，只判断"类别 + 置信度"；`severity` 仍由 `signals.py` 决定，两者相互独立。

### 5.3 三段输出结构

`attribution._base` 产出固定结构（市场级同构，`asset=None`）：

| 字段 | 类型 | 含义 |
|---|---|---|
| `asset` | str \| null | 资产；市场级为 `null` |
| `category` | str | 中文类别（`CATEGORY_CN`） |
| `category_code` | str | `long_liquidation` / `short_add` / `capital_exit` / `deleverage` |
| `confidence` | str | `high` / `medium` / `low` |
| `evidence` | list[str] | **证据**：价格、资金费率、主动卖占比、清算额/多头占比、CEX 净流、稳定币净赎回、OI 变化等（非空项） |
| `counter_evidence` | list[str] | **反证条件**：什么情况说明该推断不成立（每类固定 2 条） |
| `unknown` | str | **不可知**：固定"无法判断卖方身份（CEX 匿名撮合、链上地址假名）；本结论只是概率归因"，并按类别追加说明 |
| `basis` | str | 依据摘要（如"清算数据 + 价格方向"） |

各类别 `unknown` 追加的说明：多头强平 → "被强平的具体账户不可见"；空头加仓/主动砸盘 → "主动卖单属于新开空、平多还是搬砖不可区分"；资金撤离 → "资金流出后是否卖出不可见"；去杠杆 → "杠杆资金来自哪类账户不可见"；市场级 → "市场级只有总量，没有币种/主体拆分"。

示例（多头强平，OI 下降故 `high`）：

```json
{
  "asset": "BTC",
  "category": "多头被强平（被动卖盘）",
  "category_code": "long_liquidation",
  "confidence": "high",
  "evidence": [
    "价格 24h -2.30%",
    "资金费率 -0.0100%",
    "OKX 15m 清算 4000.0 万美元（多头占比 90%）；清算样本覆盖 8 分钟",
    "5m 主动卖占比 62.0%（大额卖单 2 笔，成交额 500.0 万美元）",
    "15m OI 变化 -2.90%"
  ],
  "counter_evidence": [
    "若 OI 未下降，则更可能是新开空/主动抛售，而不是强平",
    "清算数据仅 OKX 口径，不代表全市场"
  ],
  "unknown": "无法判断卖方身份（CEX 匿名撮合、链上地址假名）；本结论只是概率归因；被强平的具体账户不可见",
  "basis": "清算数据 + 价格方向"
}
```

### 5.4 资产级 vs 市场级

- **资产级**（`attribute_asset`）：按 §5.2 四类判定，逐资产输出；`capital_exit` 在资产级会沿用市场级的两条资金条件（CEX + 稳定币），并在反证里提示"CEX 净流是全币种聚合，不能归到单一币种"。
- **市场级**（`attribute_market(cex_flows, stablecoins, btc_direction)`）：只输出 `capital_exit` 一类，条件是 **BTC 24h 方向为 `down`** 且 `cex_net_24h_bps ≤ −50` 且 `stablecoin_net_issue_24h_usd ≤ −5 亿`；置信度 `high` 当 CEX 净流绝对值 ≥ 150bps，否则 `medium`。市场级阈值同样从 `rules.cex_flow.p1_net_24h_bps_tvl`（默认 50bps）与 `rules.stablecoin.p1_net_issue_24h_usd`（默认 −5 亿美元）读取。
- 快照 `attribution` = `{assets: {BTC/ETH/BNB: 对象或 null}, market: 对象或 null}`；候选只带摘要（`category`/`category_code`/`confidence`），完整三段在快照与 exports 里。

### 5.5 已知边界

- 归因是**概率归因**，不是事实认定；`unknown` 段始终存在，文案不得把类别读成身份。
- 归因层不重复做数据质量门：`price_context` 过期时方向被强制为 `unknown`，但 `change_24h_pct` 仍在快照里，若它 ≤ −0.5%，价格门控仍可能让归因成立；数据质量门属 M3。
- 清算覆盖不足的候选仍可能被归因为强平（§3.3）；`p1_usd` 未配置的资产不会命中强平分支。
- OI 来自 Binance USD-M 单所，CEX 净流是全币种聚合，稳定币有 mint/burn 噪音——这些限制写进每类 `counter_evidence` / `unknown`。

## 6. 阈值与配置归属

**所有业务阈值只在 [config/rules.yaml](../../crypto-onchain/config/rules.yaml)，代码不硬编码业务阈值**（函数内默认值仅作键缺失兜底，与 YAML 初值一致）。改阈值不改代码；三份 YAML 都有 `schema_version: 1`，启动时由 `common.schema_problem` 校验，不匹配直接退出码 2。

| 配置键 | 值（2026-11-09 初值） | 作用 |
|---|---|---|
| `taker.window_seconds` | 300 | 主判级窗口 |
| `taker.p1_ratio` / `p0_ratio` | 0.58 / 0.65 | 主动卖占比门槛 |
| `taker.large_trade_usd` | BTC 5,000,000 / ETH 2,000,000 / BNB 1,000,000 | 单笔大单门槛（按资产分层） |
| `taker.p1_min_large_sells` / `p0_min_large_sells` | 3 / 6 | 大额卖单笔数门槛 |
| `taker.min_window_quote_usd` | BTC 2,000,000 / ETH 1,000,000 / BNB 200,000 | 样本不足不判级（归因层同样忽略） |
| `cex_flow.p1_net_24h_bps_tvl` / `p0_net_24h_bps_tvl` | 50 / 150 | 24h 净流出 / TVL 的 bps 门槛 |
| `cex_flow.p1_net_1w_bps_tvl` | 400 | 1 周净流出 / TVL 只升 P1 |
| `cex_flow.min_tvl_usd` | 1,000,000,000 | 交易所参评 TVL 下限 |
| `liquidations.window_minutes` | 15 | 清算统计窗口 |
| `liquidations.p1_usd` / `p0_usd` | BTC 30M/100M、ETH 15M/50M、BNB 8M/25M | 清算额分级（USD） |
| `liquidations.long_bias_ratio` | 0.6 | 多头被爆占比 → 类别（归因复用） |
| `liquidations.min_coverage_minutes` | 5 | 样本覆盖下限（信号层） |
| `stablecoin.symbols` | USDT, USDC, USDe, FDUSD | 稳定币关注列表 |
| `stablecoin.p1_net_issue_24h_usd` / `p0_net_issue_24h_usd` | -500,000,000 / -1,500,000,000 | 净赎回分级（USD；p1 归因复用） |
| `stablecoin.p0_requires_btc_drop_pct` | 3.0 | 升 P0 需 BTC 24h 跌幅 |
| `attribution.enabled` | true | 归因引擎总开关（资产级 + 市场级） |
| `attribution.oi_significant_pct` | 2.0 | 15m OI 变化 ≥2% 视为显著加仓/减仓 |
| `attribution.oi_window_minutes` | 15 | OI 变化窗口（对应 crypto-price 的 OI 快照） |
| `attribution.oi_history_hours` | 24 | 本地 OI 历史保留时长 |
| `exports.file` / `exports.schema` | `exports/onchain-pressure.json` / `onchain/pressure/v1` | 跨模块导出路径与契约名（`app.yaml`） |
| `walletflow.min_usd` / `chains[].min_usd` | 10,000,000（ETH 主网）/ 5,000,000（BSC） | 大额转账**记录**阈值（`app.yaml`；2026-11-09 由 5M/2M 调高） |
| `walletflow.alerts.enabled` / `min_usd` | true / 10,000,000 | 事件驱动告警开关与**告警**阈值（可与记录阈值不同；BSC 记录 5M 但告警仍按 10M） |
| `walletflow.alerts.p0_usd` / `p0_cex_in_usd` | 50,000,000 / 25,000,000 | P0 分级：单笔 ≥$50M，或转入交易所（`cex_in`）且 ≥$25M |
| `walletflow.alerts.only_p0` / `daily_cap` / `burst_trigger_ratio` / `burst_cap` | true / 10 / 2 / 30 | **只推 P0**（P1 记 suppressed，`reason=only_p0`）；`daily_cap` 仅对 P1 生效（当前等于不推）；P0 常规受 `daily_cap=10` 限制；特殊期解除上限 |
| `walletflow.alerts.max_per_round` / `quiet_hours` / `guess` | 5 / `""` / true | 单条消息最多笔数；`quiet_hours` 默认空 = 全天即时（非空时仅对 P1 生效，当前 P1 不推送）；是否允许“推测”备注 |
| `walletflow.alerts.rare_only` / `rare.*` | true / `min_usd: 500,000,000`、`flow_anomaly_ratio: 3`、`flow_min_usd: 100,000,000`、`daily_cap: 2` | B 模式：只发两类重大异常（单向未知 ≥$500M；1h 交易所净流入 >7日同时段均值×3 且 ≥$100M），每日 ≤2 条；其余靠每日情报摘要 |
| `walletflow.alerts.whale_usd` / `whale_cex_in_usd` / `whale_daily_cap` | 100,000,000 / 50,000,000 / 5 | 巨鲸保护：超过常规 `daily_cap` 后，≥$100M（或入所 ≥$50M）仍再发至多 5 条/天 |
| `walletflow.alerts.special.*` | `enabled: true`、`manual: false`、`p0_transfers_today: 600`、`btc_move_24h_pct: 6.0`、`cex_outflow_bps: 150`、`stablecoin_outflow_usd: -1,500,000,000` | 特殊期（牛市/熊市/黑天鹅）判定：命中任一 → 解除常规 10 条、特殊期硬上限 30 条/天（仍保留去重与单轮 ≤5 笔） |
| `walletflow.alerts.enrich.*` | `etherscan_tags: true` / `timeout_seconds: 6` / `cache_days: 30` | 告警补标签：仅 ETH 地址抓 Etherscan 地址页 name tag，缓存 `data/state/address-tags.json` TTL 30 天；失败忽略；`--dry-run` 不抓 |
| `shadow.target_precision` / `target_daily_candidates` | 0.60 / 3 | 影子期校准目标（**尚无自动回算**，需离线分析 jsonl） |
| `shadow.eval_horizon_minutes` / `eval_direction_move_pct` | 60 / 0.5 | 校准口径声明 |
| `delivery.*` | `mode: shadow`、静默时段、每日 P1 ≤5、冷却 30/240/30/720 分钟（`delivery.*` 骨架） | **M3 规划（仅 4 类卖压候选）**，代码不读取；walletflow 告警用 `walletflow.alerts.*`，已生效 |

配置职责划分：看什么/怎么换算在 `watchlist.yaml`（交易对、`okx_uly`、`contract_value`、关注 CEX）；运行参数在 `app.yaml`（端点、窗口、翻页、超时、产物路径、轮询周期、exports 路径）；注意力度在 `rules.yaml`。`watchlist.yaml` 中的 `okx_inst`、`chart_url` 为展示/预留字段，代码未读取。

## 7. 候选结构

### 7.1 信号层（`signals._candidate` 的返回结构 + `run.py` 附加字段）

| 字段 | 类型 | 含义 |
|---|---|---|
| `signal` | str | `taker_sell_pressure` / `cex_reserve_outflow` / `liquidation_cascade` / `stablecoin_outflow` |
| `scope` | str | `asset`（单资产）/ `cex`（交易所合计）/ `market`（全市场背景） |
| `asset` | str \| null | 单资产信号为 BTC/ETH/BNB；cex/stablecoin 信号为 `null` |
| `exchange` | str \| null | 预留字段，恒为 `null`（未来按交易所拆分用） |
| `severity` | str | 本会触发的级别 `P0`/`P1`（仅当阈值满足时才产生候选） |
| `value` | float \| null | 触发的指标值：taker 为占比、cex 为 bps、清算为 USD、稳定币为净增发 USD |
| `threshold` | object | 本次判级实际套用的阈值快照（便于影子期复算） |
| `direction` | str | 信号隐含方向：taker/cex/stablecoin 为 `down`；清算为 `down`/`up`/`unknown` |
| `aligned` | bool \| null | 与价格 24h 方向是否一致；方向未知为 `null` |
| `would_alert` | bool | `bool(severity) and aligned is not False` |
| `note` | str | 中文说明，只含"类别 + 证据数值 + 行情方向" |
| `extra` | object | 各信号附加证据（如 `sell_quote_usd`、`net_1w_usd`、`long_ratio`、`symbols`、`venue: "OKX"`） |
| `attribution` | object \| 缺省 | **M2 附加**：归因命中时的摘要 `{category, category_code, confidence}`（`run.py` 在归因后写入，未命中则无此键） |

候选在同一轮内按 `(severity 优先级 P0→P1, signal 名称)` 排序；`severity` 只表示"如果开推送本会发什么级别"，**4 类卖压候选不会推送**（walletflow 告警是独立通道，不走候选体系）。

### 7.2 落盘层（`data/state/shadow-candidates.jsonl`，每条候选一行）

在 7.1 基础上追加运行上下文：

| 字段 | 含义 |
|---|---|
| `generated_at` | 本轮生成时间（本地时区 `本地时区` 的 ISO 字符串） |
| `now_ts` | 本轮"现在"的 epoch 秒（fixtures 模式为 `max(成交 ts)`，见 [technical.md](technical.md)） |
| `price_context` | `{BTC/ETH/BNB: {price, change_24h_pct}}` 快照 |
| `cex_net_24h_bps` | 本轮交易所净流 bps（供跨信号对照） |
| `attribution` | 归因摘要（命中时）或 `null` |

写入采用追加（`append_jsonl`），失败/降级轮次可能没有候选行；`--status` 按 `generated_at` 前缀统计当日 P0/P1 条数。

## 8. 跨模块导出契约 `onchain/pressure/v1`

### 8.1 写入时机与路径

- 路径：`app.yaml` 的 `exports.file`（默认 `exports/onchain-pressure.json`，相对模块 `data/`；`--data-dir` 跟随重定向）；
- 每次**非 `--dry-run`** 轮次覆盖写（原子写，自动创建父目录），与影子快照/候选同轮产出；`--dry-run` 不写任何文件；
- 写入不受 `shadow.enabled` 控制（`_write_exports` 独立于 `_write_shadow`）；
- 契约 `schema: "onchain/pressure/v1"`、`source_module: "crypto-onchain"`、`mode: "shadow"`。

### 8.2 顶层字段

| 字段 | 类型 | 含义 |
|---|---|---|
| `schema` | str | 固定 `onchain/pressure/v1` |
| `generated_at` | str | 本轮生成时间（ISO，本地时区） |
| `source_module` | str | 固定 `crypto-onchain` |
| `mode` | str | 固定 `shadow`（M3 才可能有其它值） |
| `assets` | object | 逐资产对象（见 8.3） |
| `market` | object | 市场级资金流与归因（见 8.4） |
| `candidates` | object | `{n, by_severity: {P0: n, P1: n}}`，本轮候选计数 |
| `walletflow` | object | **M3 附加（只增字段）**：链上大额转账概要（`enabled/source/chains[]/n_large/usd_total/recent/alerts/errors`（每链含扫描范围/调用数/错误）；`alerts={sent_round,suppressed,error}` 为事件驱动告警摘要），与快照同源；关闭时为 `{enabled: false}` |
| `caveats` | list[str] | 固定 3 条口径提示：CEX 净流是全币种聚合 USD；清算仅 OKX 口径；只回答卖压类别、不含卖方身份 |

### 8.3 `assets[asset]` 字段

| 字段 | 类型 | 来源 / 含义 |
|---|---|---|
| `price` | float \| null | 快照资产价格 |
| `change_24h_pct` | float \| null | 24h 涨跌幅 |
| `taker_5m_sell_ratio` | float \| null | 5m 主动卖占比 |
| `taker_5m_quote_usd` | float \| null | 5m 成交额 |
| `liq_15m_usd` | float \| null | 15m OKX 清算额 |
| `liq_15m_long_ratio` | float \| null | 清算中多头占比 |
| `oi_change_15m_pct` | float \| null | 15m OI 变化（来自本地历史；不足为 `null`） |
| `funding_rate` | float \| null | 资金费率（crypto-price exports） |
| `attribution` | object \| null | 完整资产级归因对象（§8.5），未命中为 `null` |

### 8.4 `market` 字段

| 字段 | 类型 | 含义 |
|---|---|---|
| `cex_net_24h_bps` | float \| null | 关注交易所 24h 净流 / TVL（bps） |
| `cex_net_24h_usd` | float \| null | 关注交易所 24h 净流（USD） |
| `stablecoin_net_issue_24h_usd` | float \| null | 稳定币 24h 净增发（USD，负数为净赎回） |
| `btc_direction` | str | BTC 24h 方向（`up`/`down`/`flat`/`unknown`） |
| `attribution` | object \| null | 市场级归因对象（只有 `capital_exit` 一类，见 §5.4） |

### 8.5 归因对象字段（`attribution`）

| 字段 | 类型 | 含义 |
|---|---|---|
| `asset` | str \| null | 资产；市场级为 `null` |
| `category` | str | 中文类别 |
| `category_code` | str | 四类之一 |
| `confidence` | str | `high` / `medium` / `low` |
| `evidence` | list[str] | 证据（见 §5.3） |
| `counter_evidence` | list[str] | 反证条件 |
| `unknown` | str | 不可知说明（固定首句"无法判断卖方身份…"） |
| `basis` | str | 依据摘要 |

### 8.6 兼容性约定与消费者现状

- **只增字段**：新版本只能新增键，不改已有键名/语义；旧消费者可忽略新字段。
- **当前没有下游消费方**：`crypto-analyst` 的日报归因段仍属 M3 规划，尚未读取该文件；不要把它当作已接入的跨模块依赖。
- 快照 `shadow-latest.json` 的 `attribution` 与导出里的 `attribution` 同源（同一轮计算结果），便于离线对账。

## 9. 影子产物与状态查询

影子期输出来自 [scripts/run.py](../../crypto-onchain/scripts/run.py)，路径可在 `app.yaml` 的 `shadow.*` / `exports.*` 配置（相对模块 `data/`）；`shadow.enabled=false` 时不写快照/候选（exports 仍写）。

| 产物 | 内容 | 写入时机 |
|---|---|---|
| `data/state/shadow-latest.json` | 每轮全量快照，`schema: "onchain/shadow/v1"`；含 `assets`（价格背景 + taker 5m/15m + 清算 15m + `oi`/`funding_rate`/`oi_change_15m_pct`）、`cex_flows`、`stablecoins`、`price_context`、`candidates`、**`attribution`**、`source_health`、`errors`、`now_ts` | 每轮覆盖写（原子写），dry-run 不写 |
| `data/state/shadow-candidates.jsonl` | 每条候选一行（§7.2，含 `attribution` 摘要），只追加；供 2 周后回算精确率/日均条数 | 每轮有候选时追加 |
| `data/state/oi-history.json` | 跨轮 OI 历史（§4.1），24h / 400 点上限 | 每轮非 dry-run 覆盖写 |
| `data/exports/onchain-pressure.json` | 跨模块契约 `onchain/pressure/v1`（§8） | 每轮非 dry-run 覆盖写 |
| `data/state/walletflow-cursor.json` | 链上大额转账扫描游标 `{"chains": {"ethereum": block, "bsc": block}, updated_at}`；首轮无游标时 ETH 回看 120 块 / BSC 回看 900 块 | 每轮成功（非 dry-run 且 `walletflow.error` 为空）时覆盖写 |
| `data/raw/<date>/walletflow.jsonl` | 当日大额记录（`ts/block/symbol/amount/usd/from/to/tx/log_index/direction/counterparty`），只追加 | 每轮有记录且非 dry-run 时追加 |
| `data/logs/onchain.log` | 每轮一行汇总：`assets/cex_n/stable_n/candidates/attributions/walletflow/errors`；由 `collect.sh` 每日首轮调用 `tools/prune.py` 清理 | 每轮非 dry-run 追加 |

读取侧：`--status` 只读快照与候选文件（不联网、不读 exports 文件），返回快照路径、`snapshot_ok`、最近 `generated_at`、候选总数/当日数（P0/P1 分列）、最近 3 条候选、**快照里的 `attribution`** 与 exports 路径；中文摘要额外打印一行"归因: BTC=…（high）；市场=…（medium）"，无归因时显示"无（价格未下跌或数据不足）"。快照不存在时退出码 1。

## 9.5 每日影跑摘要（`digest.py`，M2.5）

定位：**只汇总，不告警**。影子期需要判断"归因有没有信息量"，但每天逐条看 jsonl 不现实，所以每天 21:00 推一条 Telegram 汇总；它不参与 P0/P1 分级、不发候选明细。**2026-11-09 起摘要新增“今日链上告警”统计行**（读取 walletflow 告警状态），但摘要本身仍不是告警通道。

| 项 | 约定 |
|---|---|
| 触发 | `collect.sh` 每轮末尾调用 `scripts/digest.py --if-due --notify`；到点（`app.digest.time`，默认 21:00 本地时区）且今天没发过才发；机器休眠则在唤醒后首个轮次补发 |
| 去重 | `data/state/digest.json` 的 `last_sent_date`；发送成功才写；失败下一轮重试 |
| 数据 | 只读 `data/state/shadow-latest.json`、`data/state/shadow-candidates.jsonl`（当日）、`data/raw/<date>/attributions.jsonl`（`run.py` 每轮追加的非空归因）、`data/raw/<date>/walletflow.jsonl`（当日大额转账）、`data/state/walletflow-alerts.json`（当日告警条数/抑制数） |
| 内容 | 快照时间 + 三币 24h；今日候选数（P0/P1/would_alert）；归因记录数 + 类别分布 + 最近 3 条；**链上大额转账 N 笔/合计/最大一笔（walletflow）**；**今日链上告警：N 条（常规上限 10，特殊期可解除）；抑制 K 条**；CEX/稳定币资金面；校准 Day N/14；固定口径说明 |
| 命令 | `--dry-run`（打印）/ `--notify`（立即发）/ `--if-due`（每日入口）/ `--force`（补发）/ `--json` / `--data-dir` |
| 凭据 | 专用 Bot 优先：`TELEGRAM_ONCHAIN_BOT_TOKEN`/`TELEGRAM_ONCHAIN_CHAT_ID`（独立 Bot，`common.load_bot_env`），未填回退 `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`；缺失返回 rc=2/3，不影响采集轮次（`collect.sh` 用 `|| true`） |
| 状态/产物 | `data/state/digest.json`；`data/raw/<date>/attributions.jsonl`；`onchain.log` 记 `digest: sent <date>` |

**与 walletflow 告警的区别**：摘要是一条固定时点的汇总消息，不携带"现在该行动"的语义；walletflow 告警是事件驱动的独立消息（P0/P1、去重、日限额与特殊期，见 §9.7）。4 类卖压候选的推送仍属 M3。

## 9.6 链上大额转账监控（walletflow，M3 附加能力）

定位：只回答"链上发生了哪些大额转账"，**不回答"地址属于谁"**。它不是第五类卖压信号，不参与候选/归因判定；产出为落盘记录 + 每日摘要 + **事件驱动告警（§9.7，已上线）**。数据记录（快照/明细/exports）本身不猜方向；告警文案可在 `unknown` 时带明确标注的“推测”备注。

### 9.6.1 数据源与扫描窗口

- 公共 RPC（`app.walletflow.chains[].rpc_url`：ETH 默认 `ethereum-rpc.publicnode.com`、BSC 默认 `bsc-rpc.publicnode.com`），方法 `eth_blockNumber` / `eth_getLogs` / `eth_getBlockByNumber`（时间戳用 JSON-RPC batch 一次取）；免 key、$0。
- 只扫 ETH 主网 + BSC 的 USDT/USDC 的 `Transfer(address,address,uint256)` 日志（`topic0 = 0xddf252…`）；稳定币按 1.0 折算 USD。
- 单轮增量 `pending_range(cursor, latest, scan_blocks_init, max_blocks_per_round)`，参数按链取 `app.walletflow.chains[]`：
  - 无游标（首轮）→ `latest-scan_blocks_init+1 .. latest`（ETH `scan_blocks_init=120`、BSC 900）；
  - 有游标 → `cursor+1 .. latest`；若跨度 > `max_blocks_per_round`（ETH 120 / BSC 900），只扫最近 N 块，超出部分记 `skipped_blocks`（不补扫，避免长间隔下的调用膨胀）；
  - 游标已在最新块 → 本轮不扫（返回 `None`，不产生空区间调用）。
- `eth_getLogs` 按 `chunk_blocks` 分块（ETH 30 / BSC 300，公共 RPC 对单次区块范围有上限，实测值稳定）：ETH 120 块 = 每币种 4 次、2 币种 8 次 + 1 次 `eth_blockNumber` + 逐块时间戳（最多 20 块）≈ 28–29 次/轮（2026-11-09 实测 28 次）；BSC 900 块 = 每币种 3 次 + 1 次 `eth_blockNumber` + 时间戳。

### 9.6.2 解码与阈值

- 每条日志：`topics[0]` 必须是 Transfer topic；`data` 为 `uint256` 金额；`topics[1]`/`topics[2]` 取末 20 字节为 from/to；`blockNumber`/`logIndex`/`transactionHash` 来自日志。
- 金额 `amount = value / 10**decimals`（USDT/USDC `decimals=6`），`usd = amount × price`（`price` 缺省 1.0）。
- 过滤：`usd < min_usd` 丢弃（`walletflow.min_usd=10,000,000`；BSC 链级覆盖 `chains[].min_usd=5,000,000`）；字段非法（非对象、`topics` < 3、`data` 非十六进制）返回 `None`，不抛异常。
- 时间戳：对本轮出现大额记录的块逐块 `eth_getBlockByNumber` 取准确时间戳（**最多 20 个块**；取不到的 `ts=null`），记录按 `(ts, log_index)` 倒序。

### 9.6.3 mint/burn 与去重

- 按 from/to 判 `kind`：from=零地址 → `mint`；to=零地址 → `burn`；否则 `transfer`。
- **默认排除 mint/burn**（`include_mint_burn: false`）：稳定币增发/销毁占大额 Transfer 的大头，不是钱包行为；需要时置 true，`kind` 字段保留三种值。
- 去重键 `(tx, log_index)`，`seen` 集合在**同一轮所有币种间共享**（同一笔转账不会因多币种重复计入）；`--fixtures` 回放走同一套解码/过滤/去重逻辑。

### 9.6.4 方向标注（内置已验证标签 + 仅方向）

- 标签来自 `app.walletflow.labels`，**已内置 23 个 2026-11-09 验证过的地址（21 cex / 1 mm / 1 issuer）**：
  - 官方来源：Binance 透明度页（2022-11 快照，排除 Cold Wallet 7/20/28）、Bitfinex 官方 `wallets.txt`（只留 hot wallet）；
  - 数据驱动补充：对我们自己抓到的大额转账对手方逐个查 Etherscan name tag，命中交易所标签才收录（Binance 129/151/Deposit、Coinbase 29/Prime 1、Kraken 4/5、OKX 217、Deribit 9）；
  - Jump Trading 标为 `type: mm`（做市商，不参与 cex 方向）。
- `classify_direction`：双边都是 `type=cex` → `cex_internal`（交易所内部/互转，不构成单向卖压）；仅接收方 cex → `cex_in`；仅发送方 cex → `cex_out`；否则 `unknown`（**数据记录层没有标签时一律 unknown，不猜**）。
- 命中标签时记录 `counterparty: {name, type}`；`type=mm` 不参与方向判定。**不做实体识别/身份推断**；标签不影响记录本身是否产生。
- **BSC 无免费标签源**：内置 23 个标签均为 ETH 主网地址，BSC 转账方向基本为 `unknown`；告警层对 `unknown` 的低置信“推测”见 §9.7，仍明确标注、不作实体断言。
- 地址会轮换/新增，按同一方法（官方清单 + Etherscan name tag）定期更新；冷钱包刻意排除（入所多为托管划转，不代表即将卖出）。Etherscan key 未被使用。

### 9.6.5 游标与产物

- 游标 `data/state/walletflow-cursor.json`：`{"chains": {ethereum: block, bsc: block}, updated_at}`；每条链单独取本轮 `to_block`（没有则 `latest_block`），失败链不前进。
- 前进条件：**非 `--dry-run` 且本轮 `walletflow.error` 为空**；RPC 失败时刻意不前进，下一轮从旧游标重试（积压超过 120 块时仍按单轮上限记 `skipped_blocks`）。
- 明细 `data/raw/<date>/walletflow.jsonl`：每轮有记录且非 dry-run 时追加，字段 `ts/block/symbol/amount/usd/from/to/tx/log_index/direction/counterparty`（不含 `chain`/`token`/`kind`）。
- 快照 `shadow-latest.json` 的 `walletflow` 块：顶层 `enabled`、`source="eth_getLogs/public-rpc"`、`chains[]`、`n_large`、`usd_total`、`recent`（最多 5 条）、**`alerts`（`{sent_round, suppressed, error}`，告警开启且非 fixtures 时存在）**、`errors[]`；`chains[]` 每项含 `name`/`latest_block`/`from_block`/`to_block`/`scanned_blocks`/`skipped_blocks`/`rpc_calls`/`n_large`/`usd_total`/`error`；关闭时为 `{enabled: false}`。
- exports `onchain/pressure/v1` 新增顶层 `walletflow` 字段（与快照同源，**只增字段**，因此也含 `alerts` 摘要）；`--dry-run` 不写游标/明细/exports/告警状态。
- 告警状态 `data/state/walletflow-alerts.json`（§9.7.3）：`date/messages/sent/suppressed/pending/last_sent_at`。
- 告警标签缓存 `data/state/address-tags.json`（§9.7.6）：仅告警真实发送时读写（`--dry-run` 不调用补标签，不读不写），30 天 TTL。
- 每日摘要（§9.5）保留“链上大额转账（ETH 主网 + BSC 的 USDT/USDC）：N 笔 ≥ 阈值，合计 X；最大一笔 $Y（from → to）”行，并进 `data` 的 `walletflow: {n, usd_total, top}`（`top` 为按 USD 前 2 条，摘要只展示最大一条）；**另新增“今日链上告警：N 条（常规上限 10，特殊期可解除）；抑制 K 条”行**。

### 9.6.6 失败降级

| 场景 | 表现 | 处理 |
|---|---|---|
| RPC 超时/报错/返回非对象 | `fetch_transfers` 捕获异常 → `(records=[], meta, error="类型: 摘要")` | 只写 `snapshot.walletflow.error`；**不阻塞主流程、不改变 rc、不进 `errors[]`**；游标不前进，下轮重试 |
| 首轮无游标 | 回看 120 块 | 失败同样只记 error，下一轮仍回看 120 块 |
| 扫描跨度 >120 块 | `skipped_blocks > 0` | 只扫最近 120 块，跳过的块不补扫（记数供复盘） |
| 单块时间戳取不到 | `ts=null` | 记录仍保留，排序用 `ts or 0` |
| 写游标/明细失败（`OSError`） | 静默吞掉 | 不影响本轮；下一轮可能重复扫同一段并重复落明细（去重键只在单轮内生效） |

### 9.6.7 实测数据（2026-11-09 21:31，ETH 主网，阈值调高前 $5M 口径）

- 单轮 120 块：41 笔 ≥$5M（USDT/USDC），合计约 **6.42 亿美元**；排除 mint/burn 后均为钱包间转账。
- 单轮约 20–40 次 RPC、30–60 秒；游标 `{"chains": {"ethereum": 26378841, "bsc": 132626533}, updated_at}`；0 成本、免 key。BSC 900 块内 0 笔 ≥$2M（深度小于 ETH）。
- **后续调整（同日）**：记录阈值调高为 **ETH $10M / BSC $5M**，并上线事件驱动告警（告警阈值 `alerts.min_usd=$10M`）；上述 $5M/$2M 数字仅作调高前基线。
- **不做地址归属**：数据记录里只有 from/to 地址与方向（仅内置验证标签命中时给出 `cex_in`/`cex_out`/`cex_internal`，其余 `unknown`），日常摘要只报笔数/合计/方向计数/最大一笔；告警文案的“推测”备注必须明确标注。

## 9.7 链上大额转账事件驱动告警（`walletflow_alerts.py`，已上线）

定位：把 walletflow 每轮"新出现的大额转账"**立即**推给用户，不等 21:00 日报；不是身份识别，也不是 4 类卖压候选的推送通道。入口由 `run.py` 在 walletflow 采集与落盘之后调用（`alerts.enabled=true` 且**非 `--fixtures`** 时）；异常只写 `walletflow.alerts.error`，不影响主流程与 rc。2026-11-10 起告警消息字段扩充（代币数量/区块/地址标签/背景/交易链接），见 §9.7.6。

### 9.7.1 触发、去重与聚合

- **候选**：`walletflow_alerts.process(records, alerts_cfg, paths, labels, dry_run, now, context)`，输入为本轮 `walletflow.py` 的记录（已按记录阈值过滤、排除 mint/burn、按链去重）；`context` 由 `run.py` 传入 BTC 24h 涨跌幅、CEX 24h 净流 bps、稳定币 24h 净增发，供特殊期判定。
- **阈值**：`alerts.min_usd=10,000,000`（与记录阈值独立；BSC 记录阈值 $5M，但只有 ≥$10M 的转账才会进告警候选）。
- **去重**：键 `(tx, log_index)`（`_key`）；`state.sent` + `state.suppressed` 里已有的键不再发；跨日自动重置。
- **同 tx 多腿**：同一 `tx` 可能有多条腿（例如原子换手 A→B 与 B→A 各一条），选择时按 `tx` **只保留 USD 最大的一腿**，避免重复刷屏；状态去重键仍是 `(tx, log_index)`。
- **聚合**：一条消息装本轮多条；渲染时同一 `from→to` + 同币种的多笔合并为一行并显示“（N 笔）”，金额为合计；不同交易对手分行。
- **单轮上限**：`max_per_round=5` 笔（按 USD 从大到小取），其余记 `suppressed`（`reason=round_limit`）。
- **pending**：上一轮发送失败的记录（最多 `PENDING_MAX=20`）优先并入本轮候选重试。

### 9.7.2 分级、全天即时、日限额与特殊期

| 项 | 规则（`app.walletflow.alerts.*`） |
|---|---|
| P0 | 单笔 USD ≥ `p0_usd=50,000,000`，**或** `direction == "cex_in"` 且 USD ≥ `p0_cex_in_usd=25,000,000` |
| P1 | 其余达到 `min_usd` 的转账 |
| 发送时段 | `quiet_hours` 默认 `""` = **全天即时**：仅 P0 立即发送；P1 不推送（仍落盘/进摘要）（送达即时，由接收端自行处理）；该配置仍可临时配回时段——非空时仅 P1 记 `suppressed`（`reason=quiet_hours`）、**P0 穿透** |
| 常规日限额 | `daily_cap=10` 条消息/天（按**发送成功的消息条数**计，一条消息内含多笔只算 1 条） |
| 巨鲸保护 | 单笔 ≥`whale_usd=100M` 或转入交易所 ≥`whale_cex_in_usd=50M`：不受常规 10 条限制，额外额度 `whale_daily_cap=5`/天（总上限 10+5=15），消息标「🐋 巨鲸」；防止发满 10 条后漏掉巨鲸 |
| 扩容 | 仅 `only_p0=false` 时按当天大额笔数扩容（> `daily_cap × burst_trigger_ratio` 后 `cap = min(burst_cap, daily_cap + (笔数 − daily_cap×burst_trigger_ratio)//2)`）；`only_p0=true` 时 P0 固定用 `daily_cap=10`，不扩容 |
| P0 限额 | `only_p0=true`：P0 当日上限 = `daily_cap=10`（特殊期解除）；`only_p0=false`：P0 上限 `burst_cap=30`。与 P1 共用 `messages` 计数，超限记 `daily_cap` |
| 超限 | 被限额挡下的记录记 `suppressed`（`reason=daily_cap`），当日不再重试 |
| 特殊期触发 | `special.enabled=true`、`manual=false`：当日 P0 笔数 ≥`p0_transfers_today=600`、\|BTC 24h 涨跌幅\| ≥`btc_move_24h_pct=6.0%`（`context.btc_change_24h`）、CEX 24h 净流出 ≥`cex_outflow_bps=150`（`cex_net_24h_bps ≤ −150`）、稳定币 24h 净赎回 ≥15 亿美元（`stablecoin_net_issue_24h_usd ≤ −1,500,000,000`）任一命中；`manual=true` 手工强制 |
| 特殊期效果 | **解除日限额**：解除常规 10 条，但仍受特殊期硬上限 `max_messages=30` 条/天；仍保留 `(tx, log_index)` 去重、同 tx 多腿只留 USD 最大一腿、单轮 `max_per_round=5`；消息的“时间/本轮笔数”行追加「· ⚠ 特殊期：日限额已解除」 |

判定顺序：先看发送时段（默认 `quiet_hours` 为空，不抑制；仅显式配置时才可能压 P1）→ 再定限额（特殊期 `cap`/`hard` 均为 `None`；常规期 P0 用 hard、P1 用动态 cap）→ 再看单轮 5 笔上限；候选先按 USD 降序排序，保证大额优先。

### 9.7.3 状态与发送

`data/state/walletflow-alerts.json`：

```json
{
  "date": "2026-11-09",
  "messages": 3,
  "sent": [{"key": "0xabc:0", "severity": "P0", "usd": 60000000, "ts": "2026-11-09T12:00:00"}],
  "suppressed": [{"key": "0xdef:1", "severity": "P1", "usd": 12000000, "reason": "daily_cap"}],
  "pending": [],
  "last_sent_at": "2026-11-09T12:00:00"
}
```

- **发送成功才计入状态**：成功后才把本轮记录写入 `sent`、`messages += 1`、清空 `pending`；失败（凭据缺失/Telegram 返回错）把本轮记录并入 `pending`（截断到 20 条）后写状态，下一轮重试。
- `date` 变化时整体重置当天计数与列表。
- 渲染：HTML（`parse_mode` 由 `send_message` 处理），标题“【crypto-onchain】链上大额转账”；每条聚合行含 图标+P0/P1+币种+金额+链、代币数量与区块号、`from → to`（缩写）、地址标签、方向、背景（有则写）与交易链接（字段明细见 §9.7.6）；末尾带任务标签 `job_label("crypto-onchain")`。**特殊期**时在标题下“时间 · 本轮 N 笔”行追加「· ⚠ 特殊期：日限额已解除」（`render_message(special=True)`）。
- **`--dry-run`**：只生成文本，**不发送、不写状态**；文本通过 `walletflow.alerts_preview` 放进快照（不落盘）并在 stdout/JSON 打印。**`--fixtures` 回放完全不调用告警**（不发送、不预览）。

### 9.7.4 文案与“推测”备注（设计要求）

- **地址标签渲染**：`地址标签：from: … / to: …`（只列有标签的一侧）；**流向+方向合并渲染**：`流向：<from 实体> → <to 实体>（<方向说明>）`，实体来自内置标签 / Etherscan tag（交易所/做市商/发行方），不清楚写「未知」；`unknown` 时括号内写「unknown · 无法判断买卖方向」，推测另起括号；来源为内置 `walletflow.labels` 优先，其次 Etherscan 地址页 name tag（§9.7.6）；两边都没有 → `地址标签：双方均无公开标签`。标签只用于展示，不写入数据记录。
- 方向为 `cex_in`/`cex_out`/`cex_internal` 时，按标签渲染中文方向说明（如“cex_in（转入交易所 · Binance 14）”），**不附加推测**。
- 方向为 `unknown` 且 `alerts.guess=true` 时，用 `build_history(raw_dir, labels, days=7)` 统计最近 7 天 `raw/<date>/walletflow.jsonl`：
  - 地址与某已知交易所地址同现（对方是 `type=cex` 标签）≥3 次 → `疑似与 <交易所名> 关联的地址`；
  - 否则地址出现 ≥5 次 → `疑似做市商/高频机构地址`；
  - 地址已在 `labels` 中、零地址、或历史为空 → 不猜（返回 `None`）。
- 渲染为 `方向：unknown（推测：<备注>）`；**“推测”字样固定存在，“猜不到就不写”**。推测是低置信线索，**不是结论、不是身份断言**；不写入 `data/raw/.../walletflow.jsonl` 或 exports 的归属字段（数据层仍只有 `direction`）。

### 9.7.5 产物与降级

- 快照 `walletflow.alerts = {sent_round, suppressed, error}`（`run.py` 汇总）；dry-run 额外有 `walletflow.alerts_preview`（内存快照，不落盘）。
- exports 顶层 `walletflow.alerts` 随快照导出（只增字段）。
- 告警/摘要凭据：优先 `TELEGRAM_ONCHAIN_BOT_TOKEN`/`TELEGRAM_ONCHAIN_CHAT_ID`（独立 Bot），未填回退 `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`（`crypto_core.notify`）；凭据缺失只记 error 并把记录留 pending，**不改 rc、不阻塞采集**。
- 关键边界：只对 USDT/USDC、ETH+BSC 生效；BSC 无免费标签源，方向多为 `unknown`；不做实体断言；Etherscan key 未使用。

### 9.7.6 消息字段与地址标签补全（2026-11-10）

**每条聚合行**（`render_message`；`_group_records` 把同 `from→to` 同币种的多笔合并为一行，金额为合计、显示“（N 笔）”）：

| 行 | 内容 | 来源/说明 |
|---|---|---|
| 标题行 | `🔴/🟡 <b>P0/P1</b> 币种 <b>$金额</b>[（N 笔）] · 链` | 图标 P0=🔴、P1=🟡；金额 `_fmt_usd`（≥1M 显示 `x.xxM`，否则 `xK`）；链为 `rec.chain` 大写（`ETHEREUM`/`BSC`，缺失回退 `ETH`） |
| 数量/区块 | `代币数量：12,345,678.00 USDC ｜ 区块：12345678` | `_fmt_amount`：千分位、两位小数（金额或 USD 为 0 时只写币种）；区块缺失写 `?` |
| 地址 | `0xabc1…def2 → 0x1234…5678` | `_short`：前 6 位 + `…` + 后 4 位 |
| 地址标签 | `地址标签：from: Binance 14 / to: …` 或 `地址标签：双方均无公开标签` | 内置 `walletflow.labels` 优先，其次 Etherscan name tag（见下） |
| 流向 + 方向（合并一行） | `流向：交易所 Binance 14 → 未知（cex_in · 转入交易所）` / `流向：未知 → 未知（unknown · 无法判断买卖方向）（推测：…）` | `_entity()` 把地址映射成“交易所/做市商/发行方 + 名称”或浏览器标签，不清楚写「未知」；方向与流向同行，避免两行重复 |
| 方向 | `cex_in（转入交易所 · Binance 14）` / `cex_out（转出交易所 · …）` / `cex_internal（交易所内部划转）` / `unknown（无法判断买卖方向）[（推测：…）]` | 聚合组内方向不一致时按 `unknown` 渲染；推测规则见 §9.7.4 |
| 背景（有则写） | `今日第 N 笔大额（累计 N 笔 / $X，占 Y%）；近 7 天 from 出现 X 次 / to 出现 Y 次；同一 tx 共 N 腿，仅报最大一腿` | 今日统计来自 `raw/<date>/walletflow.jsonl`（`today={n, usd}`；`--dry-run` 不落盘，可能少计本轮）；频次来自 `build_history` 最近 7 天；腿数来自本轮记录按 `tx` 计数 |
| 交易链接 | `交易：https://etherscan.io/tx/<hash>` / `https://bscscan.com/tx/<hash>` | `_tx_url`：`chain=="bsc"` → BscScan，否则 Etherscan；发送时 `disable_preview=True`（不展开链接预览） |

消息头：`【crypto-onchain】链上大额转账` + `MM-DD HH:MM · 本轮 N 笔[ · ⚠ 特殊期：日限额已解除]`；末尾 `🏷 任务：<job_label("crypto-onchain")>`。聚合行中的数量/区块/from→to/方向/链接取该组**首笔**记录（金额为组内合计）。

**地址标签补全（`walletflow_alerts.enrich_tags`）**：

- 配置 `app.walletflow.alerts.enrich`：`etherscan_tags=true`（关掉则完全不抓）、`timeout_seconds=6`、`cache_days=30`。
- 时机：`process` 在选出本轮 `to_send` 后、渲染前调用；**仅真实发送**（`dry_run=False`）才抓取；`--dry-run` 用空 `tags` 渲染（只能显示内置标签），**不联网**。
- 范围：本轮最多 `max_per_round=5` 笔 × from/to 双边地址（去重后通常 0–2 个新地址）；**仅 `chain=="ethereum"` 的地址抓取**，BSC 地址直接记空串；内置 labels 命中的地址同样参与缓存/抓取，只是渲染时优先用内置名。
- 抓取：`https://etherscan.io/address/<addr>` 页面 HTML（带桌面 UA 头），`parse_etherscan_tag` 用正则 `ETHERSCAN_TAG_RE` 取页面里的 name tag；不是 nametag API、不用 key、不做重试。
- 缓存：`data/state/address-tags.json`，结构 `{地址: {"tag": "...", "ts": epoch秒}}`；TTL `cache_days`（默认 30 天），命中且未过期直接用（**不再联网**，命中空标签也不再抓）；过期或未命中才抓；**抓取失败/空标签也会写缓存**（`tag=""` + 当前 `ts`），避免每轮重复抓；写盘失败静默吞掉。
- 优先级：渲染时内置 labels 的 `name` 优先于 Etherscan 标签；两者都没有 → “双方均无公开标签”。
- 边界：标签来源是公开页面/内置清单，**仍不做实体断言**；抓不到不影响发送、告警状态与 rc；页面结构变化可能让标签整批失效（静默降级）。

## 10. 失败模式与降级

总原则：**单源失败不拖垮整轮**，每轮以 `errors[]` 记录降级项，快照/候选照常落盘；退出码由 [technical.md](technical.md) §6 的规则给出。

| 场景 | 表现 | 处理 |
|---|---|---|
| Binance `aggTrades` 失败/超时/429 | 该资产 `taker_5m.n_trades=0`、`sell_ratio=null`，`errors` 记 `trades: ...` | 其它源继续；无 taker 候选；归因失去主动卖证据，可能落到资金撤离/去杠杆；rc=1 |
| DefiLlama `/cexs` 失败或解析为空 | `cex_flows.n_eligible=0`、bps 为 `null` | 不出 cex 候选；资产级/市场级 `capital_exit` 因条件不满足而不成立（市场级直接 `null`）；去杠杆的资金条件视为未恶化；rc=1 |
| DefiLlama 稳定币失败 | `stablecoins.n=0`、净增发 0 | 不出稳定币候选；`capital_exit` 不成立；rc=1 |
| OKX 清算失败/为空 | 该资产 `liq_15m.usd_known=false` 或 `n=0` | 不出清算候选；不会归因为 `long_liquidation`；rc=1 |
| `crypto-price` exports 缺失/损坏 | `price_context.ok=false`，价格回退到 5m 最后一笔，方向 `unknown`，`oi`/`funding_rate` 为 `null` | 降级运行；OI 变化 `null` → 归因置信度降级；rc=1 |
| exports 过期（`generated_at` 超 900 秒） | `price_context.fresh=false`，但 `ok=true` | 仍使用其价格；方向被强制 `unknown`（候选 `aligned=null`），但 `change_24h_pct` 仍可能让归因价格门控通过；数据质量门属 M3 |
| OI 历史文件缺失/损坏 | 历史按空处理 | `oi_change_15m_pct=null` → 归因降级；下一轮重建文件 |
| OI 历史写入失败 | `OSError` 被静默吞掉 | 本轮归因不受影响；下一轮基于旧文件继续（可能历史不足） |
| taker 5m 成交额低于该资产门槛 | 主动卖占比不参与归因 | 可能落到资金撤离/去杠杆或不出结论 |
| 覆盖不足（清算样本 <5 分钟） | 不出清算候选 | 信号层样本门槛显式实现；归因层不重复检查 |
| exports/快照/候选写盘失败（磁盘/权限） | `OSError` 向上抛出，本轮异常退出 | `collect.sh` 记录 rc 并触发 healthcheck；不会静默丢产物 |
| 分页被截断（`truncated=true`） | taker 窗口覆盖可能不足 900 秒 | 照常判级，`sources.trades_truncated` 供影子复盘 |
| 公共 RPC / walletflow 失败（超时/报错） | `snapshot.walletflow.error` 非空、`n_large=0` | 只记快照 error；**不阻塞主流程、不改变 rc、不进 `errors[]`**；游标不前进，下轮重试；`collect.sh`/healthcheck 不会因此告警 |
| walletflow 告警凭据缺失/发送失败 | `walletflow.alerts.error` 非空、`sent_round=0` | 记录并入 `pending`（≤20）下轮重试；**不改变 rc、不阻塞采集**；告警状态写盘 |
| walletflow 告警代码异常 | `run.py` 捕获后把 `walletflow.alerts.error` 写成异常摘要 | 本轮照常落盘/退出；告警下轮重试 |
| Etherscan 地址页抓取失败/超时/页面结构变化 | 该地址无浏览器标签 | `fetch_etherscan_tag` 捕获异常返回空串并静默；空标签也写缓存（30 天）避免反复抓；不影响发送、状态与 rc |
| 告警标签缓存读写失败 | 标签不持久化/缺失 | 读：按空缓存处理；写：`OSError` 静默吞掉；本轮仍用内存结果渲染 |
| 告警 dry-run 联网 | — | **不调用 `enrich_tags`**（不访问 Etherscan），预览只有内置标签（可能显示“双方均无公开标签”） |
| 告警 dry-run | 只生成 `alerts_preview` | **不发送、不写告警状态**；预览仅在 stdout/JSON |
| 三源全空 | 无资产成交、无 CEX、无稳定币 | 快照照常写（结构完整），rc=3；4 类卖压候选不推送（walletflow 告警独立，若本轮有大额转账仍可能发送） |
| 配置 schema 不符 | 启动即失败 | stderr 打印配置错误，rc=2，不写任何产物 |
| 单实例/上一轮未结束 | `run.py` 自身无锁 | `collect.sh` 用 `mkdir data/state/collect.lock` 单实例锁跳过本轮；`--dry-run`/手动直跑 `run.py` 不受锁保护 |

**已实现**的可靠性项：`collect.sh` 的单实例锁、start/done 日志、healthcheck（`tools/healthcheck.py --quiet --notify --fix`，异常才告警/自愈）、每日首轮 `tools/prune.py` 清理、rc 透传；walletflow 告警的日限额/静默/去重/pending 重试。**未实现**（M3 规划，针对 4 类卖压候选）：`data_quality` P2 信号、DefiLlama 异常波动检测、exports 过期/源异常的数据质量门、`data/state/feedback.jsonl` 与 `calibration.jsonl` 回算闭环、每日 P1 限额与冷却执行、taker 连续 3 窗口与 P0 5m 跌幅等 AND 条件。

## 11. 测试方案

### 9.7.7 智能判断：值不值得打扰（2026-11-10）

单看链上转账无法判断意义，context.py 把价格 / 新闻 / 资金面融合成 0–N 分，只有 ≥smart.min_score 才推送：

| 维度 | 条件 | 加分 |
|---|---|---|
| 价格波动 | BTC/ETH/BNB 任一 24h 绝对值 ≥ stress_move_24h_pct=3% | +2 |
| 价格信号 | crypto-price exports 的 P0/P1 信号 | +2 / +1 |
| 新闻 | 6h 内 P0/P1 且标题/分类命中关键词（hack/exploit/ETF/Fed/rate/清算/暴跌…） | +3 / +2 |
| 资金面 | CEX 24h 净流出 ≥100bps | +1 |
| 资金面 | 稳定币 24h 净赎回 ≥5 亿 | +1 |
| 方向 | BTC 24h ≤-2% 且方向 cex_in | +1 |

- 巨鲸（≥whale_usd 或入所 ≥whale_cex_in_usd）始终推送，不参与打分；
- 未达阈值 → suppressed(reason=low_context, score)，仍落盘/进摘要、不打扰；
- 推送消息新增「推送理由：…」行（最多 3 条上下文）；
- 配置 walletflow.alerts.smart.*；enabled=false 即恢复所有 P0 都推。

### 11.1 150 项离线单测（不联网）

| 测试文件 | 项数 | 覆盖内容 |
|---|---|---|
| `tests/test_indicators.py` | 18 | taker 占比/大单/窗口移动/空样本/坏行；`direction_of`；**`oi_change_pct` 历史变化/历史不足/容差超限/点数不足/字典与坏行**；CEX bps 与 `min_tvl_usd` 过滤；稳定币净增发求和；清算 USD 折算、多空偏置、窗口过滤、价格缺失降级 |
| `tests/test_signals.py` | 22 | taker P1/P0 的占比+大单+样本量组合、按资产分层门槛、`require_price_direction` 开关、方向对齐（`aligned`/`would_alert`）、note 身份词黑名单；CEX P1/P0 与 1w 慢变量；清算覆盖不足/单条样本/类别/方向；稳定币 P0 需 BTC 跌幅；候选排序与空输入 |
| `tests/test_attribution.py` | 15 | **四类归因**（多头强平 high/medium/low、空头加仓 high、资金撤离、去杠杆反证）；价格未下跌返回 `None`；`attribution.enabled=false` 资产级/市场级均关闭；taker 样本不足被忽略；市场级资金撤离与价格/阈值门控；`build_attributions` 形状与空输入；输出身份词黑名单 |
| `tests/test_digest.py` | 7 | 到点/已发/未启用判断；摘要统计（候选数/归因类别/校准 Day）；dry-run 不写状态；JSON 输出 |
| `tests/test_walletflow.py` | 12 | Transfer 解码（大额/低于阈值/mint-burn 默认排除/坏日志）；阈值过滤 + `(tx, log_index)` 去重；可选标签方向（`cex_in`/`cex_out`/无标签 unknown）；扫描区间（首轮回看/增量/缺口封顶/无新块）；`summarize` 与多链 fixture 回放 |
| `tests/test_walletflow_alerts.py` | 21 | **分级**（单笔 ≥$50M、入所 ≥$25M）；**日限额与扩容公式**（10/20/30 边界）；**发送时段**（默认全天即时；显式配置时 P1 抑制、P0 穿透）；**特殊期判定**（大额笔数/\|BTC 24h\| / CEX 净流出/稳定币净赎回四条 + `manual` 手工强制；特殊期 `cap`/`hard` 为 `None`）；**去重**（state 键 `tx:log_index`；**同 tx 多腿只保留 USD 最大一腿**）；**推测备注**（关联 ≥3 次/频次 ≥5 次、无历史不猜、已标签不猜）；**聚合**（同 from→to 同币种“（N 笔）”，只出现一次箭头）；**预览不写状态**；**历史构建** `build_history`；**地址标签补全**（`parse_etherscan_tag` 解析页面 name tag；`enrich_tags` 命中缓存不再联网） |
| `tests/test_run_fixtures.py` | 5 | fixtures 端到端（快照 schema、BTC 主动卖压/清算、CEX 净流、稳定币净赎回、候选集合、**归因类别与置信度、候选归因摘要、`onchain/pressure/v1` 导出字段**、**`walletflow` 快照/明细与 exports 字段（ETH $10M / BSC $5M 口径：8M 被过滤，12M USDC + 7M BSC 保留）**、jsonl 行数）；`--dry-run` 不落文件（含 OI 历史与 exports）；全流程身份词黑名单；全源缺失 rc=3；`--status` 读取 |

合计 **150 项**（含 walletflow 12 + walletflow_alerts 23 + context 9 + bot_env 4 + holdings 等），全部为纯函数或离线回放，不依赖网络与凭据。fixtures 位于 [tests/fixtures/](../../crypto-onchain/tests/fixtures/)：`binance_{BTC,ETH,BNB}USDT.json`、`cexs.json`、`stablecoins.json`、`okx_liq_{BTC,ETH,BNB}-USDT.json`、`price_export.json`（M2 已补 `funding_rate` / `oi`）、`binance_oi_hist_{BTC,ETH,BNB}USDT.json`（官方 OI 历史）、`walletflow_logs_ethereum.json` / `walletflow_logs_bsc.json`（`eth_getLogs` 原始日志：ETH 8M USDT 低于 $10M 被过滤、重复项去重、12M USDC 保留；BSC 7M USDT ≥$5M 保留、9M mint 默认排除、600K 低于阈值）。告警的 22 项单测为纯函数/临时目录，**不联网、不发送**（标签测试用临时缓存目录，不发请求）；e2e 在 `--fixtures` 下不调用告警。

### 11.2 质量门 `verify.sh`

[scripts/verify.sh](../../crypto-onchain/scripts/verify.sh) 依次执行：ruff 静态检查 → `libs/crypto_core` 单测 → 本模块 150 项单测 → fixtures 端到端（写临时目录，不联网）；`--with-network` 追加一次真实采集的 `--dry-run` 冒烟（只写临时目录）。无 venv 时回退 `python3`。

### 11.3 仍未自动化的验收项

需求 §8 中"fixtures 回放强平瀑布/主动砸盘/去杠杆三类场景"目前由 `test_attribution.py`（单测）与综合 fixtures（e2e）覆盖，没有三套独立 fixtures；"卡片证据/反证/不可知三段"尚未有卡片渲染（M3），只有归因对象三段；影子期精确率/日均候选回算没有自动化脚本，需离线分析 `shadow-candidates.jsonl`；联网一轮 ≥4 类信号的验收需手动跑 `verify.sh --with-network`。

## 12. 演进（M2 + M3 附加与链上告警已落地；4 类卖压推送/校准为规划）

| 阶段 | 内容 | 现状 |
|---|---|---|
| M1（已落地） | 4 类免费源采集、指标、阈值、方向门控、影子落盘、离线单测 | ✅ |
| M2（已落地） | **归因引擎**：价格方向 × OI/资金费率 × 清算方向 × 资金流组合成 4 类结论，固定 `evidence`/`counter_evidence`/`unknown`，资产级 + 市场级；**OI 历史**：官方 `openInterestHist`（主）+ `data/state/oi-history.json`（24h/400 点，兜底）+ `indicators.oi_change_pct`；**exports**：`data/exports/onchain-pressure.json`（`onchain/pressure/v1`，只增字段，暂无下游消费）；**候选带归因摘要**；`--status` 显示归因；每日影跑摘要（21:00，只汇总不告警）；150 项离线单测（含 walletflow 12 + walletflow_alerts 23 + context 9 + bot_env 4） | ✅ 本文档描述的范围 |
| M3 附加（已落地） | **链上大额转账（walletflow）**：公共 RPC `eth_getLogs` 扫 ETH 主网 + BSC 的 USDT/USDC Transfer，记录阈值 ETH $10M / BSC $5M；游标 + 按链增量；默认排除 mint/burn；快照/exports/每日摘要 + 明细落盘；12 项单测 | ✅ 记录/游标/摘要上线 |
| M3 附加告警（已上线） | **事件驱动链上大额转账告警（walletflow_alerts）**：每轮新增 ≥$10M 发一条聚合 Telegram；仅 P0（全天即时）、常规 ≤10 条、特殊期解除（P1 不推送）、**特殊期解除日限额**、去重、pending 重试、明确标注的“推测”备注、dry-run 预览；**2026-11-10 消息字段增强**（代币数量/区块/地址标签/背景/交易链接；Etherscan 页面 name tag，仅 ETH，缓存 30 天，dry-run 不联网）；22 项单测 | ✅ 告警可用；4 类卖压推送仍属 M3 |
| M3（规划，仅 4 类卖压候选） | 2 周影子校准（`shadow.target_precision=0.60`、`shadow.target_daily_candidates=3`）→ 达标开 **Telegram 推送/卡片**（证据/反证/不可知三段渲染，P0/P1）；去重冷却/静默/日限额（`delivery.*` 真正生效）；feedback/calibration 回算闭环；数据质量门（`data_quality` P2、exports 过期拦截）；`crypto-analyst` 消费 `onchain/pressure/v1`；taker 连续 3 窗口、P0 5m 跌幅、1h 清算窗口等 AND 条件；`run.py` 自身单实例锁 | ❌ 未实现 |

> 再次强调：**4 类卖压候选**都只是"本会触发什么级别"的影子记录，不发送任何消息；归因结论写入快照/候选/exports，但**没有任何下游消费方**。**walletflow 是例外**：其链上大额转账事件告警已上线（仅 P0 推送、全天即时（P1 不推送，仍落盘/进摘要） + 日限额 + 特殊期解除限额），但数据层不判断地址归属；“推测”备注必须带“推测/疑似”字样、只作线索。不要把本文档 §12 的 4 类卖压 M3 能力当作已交付。
