# 需求评估：crypto-onchain —— "谁在卖"的可观测代理（不是身份）

> 状态：**M2 已落地（2026-11-09）**：在 M1 的 4 类免费信号 + 影子落盘之上，新增**归因引擎**（4 类卖压类别，固定 `evidence` / `counter_evidence` / `unknown` 三段）、**OI 历史（官方 `openInterestHist` 优先 + 本地跨轮兜底）**、**`onchain/pressure/v1` 跨模块导出**、**每日影跑摘要（21:00，只汇总不告警）**与 **150 项离线单测**（indicators 18 / signals 22 / attribution 15 / digest 7 / walletflow 12 / walletflow_alerts 23 / context 9 / bot_env 4 / e2e 5）；并落地 **M3 附加能力「链上大额转账监控」（walletflow，公共 RPC 免 key）**：**链上大额转账的事件驱动告警已上线**（Telegram、仅 P0 推送、全天即时（P1 不推送，仍落盘/进摘要）、常规日限额、特殊期解除日限额、允许明确标注的“推测”备注；消息含代币数量/区块/地址标签/背景/交易链接，告警时可补 Etherscan 地址页公开 name tag，内置清单优先展示，缓存 30 天，dry-run 不联网）。`verify.sh` 质量门覆盖同一套检查。**4 类卖压候选仍只落盘不推送**：其 Telegram 推送/卡片、去重冷却/静默/日限额、feedback 与校准回算属 M3 规划，2 周影子校准达标后才启动。
> 文档区：本目录（requirement / design / architecture / technical 四件套）。
> 关联：[根 README 模块约定](../../README.md) · [架构设计 §5.3](../architecture.md) · [ADR-0005 领域命名](../adr/0005-domain-naming.md) · [crypto-price 需求](../crypto-price/requirement.md) · [约定](../conventions.md)

## 0. 结论先行（TL;DR）

1. **"谁在卖"没有可靠答案**：CEX 匿名撮合（只看到成交量，看不到账户）、链上地址假名（只看到 0xabc…，看不到人）、实体标签是付费能力（Etherscan nametag 属 Pro Plus，导出标签属 Enterprise）。
2. **能做的只有"卖压类别归因"**：把问题从"谁"降级为可检验维度——**多头被强平（被动卖盘）/ 空头加仓 / 主动砸盘 / 增量资金撤离 / 去杠杆反证**。每条结论必须带"证据 + 反证条件 + 不可知说明"三段，**只回答类别、不回答谁**。
3. **免费源足以覆盖上述维度**（`$0`、免 key）；付费标签源默认全关，只列清单。
4. **最大的免费发现**：DefiLlama `api.llama.fi/cexs` 免 key 直接给出 88 家交易所的 `inflows_24h/1w/1m` + `currentTvl`（本机实测 2026-11-09）。这是零成本拿到"CEX 储备净流"的唯一可行路径。
5. **最大限制**：`/cexs` 是**全币种聚合 USD**，不是 BTC/ETH 分币种净流；分币种交易所余额必须付费（CryptoQuant ≥$99/mo）。因此"链上净流"只能作**交易所级总量背景**，不能断言"BTC 被转进交易所"。OI 也只有 Binance USD-M 单所。
6. **误报是主要成本**：单源单阈值必爆。方案 = 多维度 AND + 相对归一化 + 分位阈值 + **2 周影子模式只落盘**，达标才开推送。
7. **M2 已交付可检验的归因链路**：`crypto-price` 的 `funding_rate`/`oi` 只读字段 + Binance 官方 `openInterestHist`（15m 变化，主）/ 本地跨轮历史（兜底）→ 4 类归因判定（含置信度升降）→ 快照/候选/`onchain/pressure/v1` 落盘。**归因结论目前没有下游消费方**（crypto-analyst 规划中），也不推送。
8. **M3 才做打扰（4 类卖压候选）**：Telegram 推送/卡片、去重冷却/静默/日限额、feedback/calibration 回算；healthcheck、单实例锁、每日 prune 已由 `collect.sh` 接入，不属于缺口。
9. **M3 附加能力已落地 + 事件驱动告警已上线（2026-11-09 上线；2026-11-10 消息增强）**：`walletflow` 用公共 RPC `eth_getLogs` 免 key 观测 ETH 主网 + BSC 的 USDT/USDC 的大额转账（ETH 单笔 ≥$10M、BSC 单笔 ≥$5M，2026-11-09 调高；原 $5M/$2M），默认排除 mint/burn；只回答"链上发生了什么大额转账"，**不回答"地址属于谁"**；游标按链增量前进。**每轮新增大额转账 ≥`alerts.min_usd`（$10M）即发一条聚合 Telegram 告警**：P0（单笔 ≥$50M 或转入交易所且 ≥$25M）与 P1 **全天即时**发送（`quiet_hours` 默认空；由接收端自行处理）；P1 不推送；常规 ≤10 条消息/天（特殊期解除），超限/单轮超量记 `suppressed`；**巨鲸保护**：单笔 ≥$100M 或入所 ≥$50M 不受 10 条限制、额外最多 5 条/天；**特殊期解除日限额**（当日 P0 ≥600 笔 / |BTC 24h 涨跌幅| ≥6% / CEX 24h 净流出 ≥150bps / 稳定币 24h 净赎回 ≥15 亿美元，或 `manual=true`）→ 解除常规 10 条但仍有特殊期硬上限 30 条/天，仍保留去重与单轮 5 笔、消息标注「⚠ 特殊期：日限额已解除」；发送失败进 `pending` 下轮重试，`--dry-run` 只预览不写状态。方向 `unknown` 时可在文案里加**明确标注“推测”**的低置信备注（基于 7 天历史关联/出现频次），**不做实体断言**。产物进快照/exports/每日摘要（摘要新增“今日链上告警”行），明细落盘。**2026-11-10 消息增强**：每条聚合行展示 P0/P1+币种+金额+链、代币数量（千分位）与区块号、from→to（缩写）、地址标签（内置清单优先，其次告警时抓 Etherscan 地址页公开 name tag，仅 ETH、缓存 `data/state/address-tags.json`、TTL 30 天、失败忽略；两边都没有写“双方均无公开标签”）、方向（`cex_in`/`cex_out`/`cex_internal` 或 `unknown（无法判断买卖方向）`，可附“（推测：…）”）与背景（今日第 N 笔/累计笔数与金额/该笔占比、近 7 天 from/to 出现次数、同 tx 多腿只报最大一腿提示），并附 Etherscan/BscScan 交易链接；`--dry-run` 预览不联网、不补浏览器标签。标签仍只来自内置清单或公开页面，**不做实体断言**。

---

## 1. 要解决什么问题（一句话）

用户在放量下跌时问"这波是谁砸的"：`crypto-price` 只能回答"跌了/放量/OI 异动"，`crypto-news` 只能回答"新闻层面有 ETF 流出/巨鲸报道"（二手、滞后）。**缺一个把"逐笔主动卖盘 + 交易所储备流 + 清算 + 稳定币增量 + OI/资金费率"放在同一处，给出可检验类别判断的模块。**

不做会怎样：用户只能靠社群叙事（"某巨鲸砸盘/某机构出货"）解释行情，容易把正常去杠杆误读为机构出逃，做出错误仓位决策。

---

## 2. 边界：做什么 / 不做什么

### 做（M2 已实现 + M3 附加能力）

- **4 类卖压代理信号**（详见 §4）：`taker_sell_pressure`（逐笔主动卖）、`cex_reserve_outflow`（交易所储备净流）、`liquidation_cascade`（强平瀑布）、`stablecoin_outflow`（稳定币净增发）。
- **归因引擎**：把上述信号与"价格方向 + OI 变化 + 资金费率"组合成 4 种可解释结论（多头强平 / 空头加仓 / 主动砸盘 / 资金撤离 / 去杠杆反证），每条固定 `evidence` / `counter_evidence` / `unknown` 三段，并给 `high`/`medium`/`low` 置信度。
- **影子模式（4 类卖压候选）**：只落盘不推送；快照、候选 jsonl、OI 历史（官方 + 本地兜底）、exports 四类产物。
- **跨模块契约**：`data/exports/onchain-pressure.json`（`onchain/pressure/v1`，只增字段）；**当前无下游消费**，为 `crypto-analyst` 日报归因段预留。
- 单命令可跑、离线 fixtures 回放、`--dry-run` 无副作用（不写 OI 历史/exports/walletflow 告警状态）、`--status` 可查最近归因。
- **链上大额转账监控（walletflow，M3 附加能力）**：公共 RPC `eth_getLogs` 扫 ETH 主网 + BSC 的 USDT/USDC 的 Transfer 日志，单笔等值 ≥$10,000,000（ETH 主网）或 ≥$5,000,000（BSC）才记录；ETH 单轮增量 ≤120 块（30 块/次）、BSC ≤900 块（300 块/次；节点报“范围过大”时自动对半拆分）；默认排除 mint/burn；只回答"链上发生了什么大额转账"，**不回答"地址属于谁"**。
- **链上大额转账事件驱动告警（已上线）**：每轮新增转账达到 `walletflow.alerts.min_usd`（$10M）即推一条聚合 Telegram；P0 分级、**仅 P0 全天即时发送**（P1 不推送）（`quiet_hours` 默认空，可配置）、常规日限额与扩容、**特殊期解除日限额**、发送成功才计入状态；方向 `unknown` 可带**明确标注“推测”**的低置信备注（不做实体断言）；每条聚合行含代币数量/区块/from→to/地址标签/方向/背景/交易链接（2026-11-10 消息增强）。

### 不做

- **不识别、不推断、不暗示具体个人或机构身份**。结论禁止出现"某机构/某巨鲸在卖"，只能写"主动卖盘占比升高""多头被强平"等类别；引用第三方标签（若将来接入）必须标注来源与"标签可能错误"。
- **不接付费标签/余额源**：Arkham、Nansen、Glassnode、CryptoQuant、CoinGlass 默认全关（清单见 §9），不做默认依赖。
- **不自建全链索引/地址聚类**：公共 RPC `eth_getLogs` 单次限 800 区块（cloudflare-eth）；本模块只用它对 USDT/USDC 的 Transfer 日志做增量扫描（walletflow，每 30 块一次调用），不自建全链索引、不做地址聚类。
- **不识别、不推断地址归属/实体**：walletflow 数据记录（快照/明细/exports）不带任何归属字段；`walletflow.labels` 内置 23 个已验证地址（21 cex / 1 mm / 1 issuer）（官方清单 + Etherscan name tag，2026-11-09），命中后仅标注入所/出所/内部方向（`cex_in`/`cex_out`/`cex_internal`），不做实体识别。**告警文案允许在方向 `unknown` 时附“推测：疑似与 X 关联/疑似高频机构地址”的低置信备注（设计要求），但必须带“推测/疑似”字样、只作线索，绝不当结论或身份断言。** **Etherscan key 未被使用**：walletflow 走公共 RPC，env 中的 `ETHERSCAN_API_KEY` 保留为备用源（当前代码不读取；公共 RPC 限流或将来需要 BSC 标签/更强查询时再评估）；**BSC 无免费标签源**（内置标签均为 ETH 主网地址，BSC 方向基本为 `unknown`）。**告警地址标签的来源**：内置 `walletflow.labels` 优先，其次告警发送时抓取 Etherscan 地址页公开 name tag（`etherscan.io/address/<addr>` 页面 HTML，仅 ETH、免 key、非官方 API、best-effort、缓存 30 天）；两者都没有就显示“双方均无公开标签”。标签只用于展示文案，**仍不做实体断言**。
- **默认排除 mint/burn**：稳定币增发/销毁（`include_mint_burn: false`）占大额 Transfer 的大头，不是钱包行为，默认不记录。
- **不重复 crypto-price 的价格/衍生品采集**：价格、OI、资金费率**只读** `crypto-price/data/exports/latest.json`。
- **不重复 crypto-news 的 ETF 资金流与鲸鱼新闻**：Farside 代理与新闻线索已在 news，本模块只在卡片里**引用**，不重采（M2 尚未接入引用）。
- **不接 Binance 清算 WebSocket**：Python 3.9 标准库无 WS 客户端，零依赖原则下不做；用 OKX 公开 REST 清算做代理，结论注明"仅 OKX 口径"。
- **不做交易/下单/仓位**；**不覆盖山寨币**（BTC/ETH/BNB；BNB 链上数据源不稳定，标记"尽力而为"）。
- **不输出"买盘承接"类结论**：价格未下跌（方向非 down 且涨跌幅 > −0.5%）时不产生资产级归因；"价格持平/上涨 + 大额卖单成交"仅作观察思路，不属于 M2 已实现能力。

### M3 附加能力：链上大额转账监控（walletflow，2026-11-09 已落地；告警已上线）

- **数据源与成本**：公共 RPC `eth_getLogs`（默认 `https://ethereum-rpc.publicnode.com`，配置 `app.walletflow.chains[].rpc_url` 可换；免 key、$0）；只扫 ETH 主网 + BSC 的 USDT/USDC 的 `Transfer` 日志（`topic0 = keccak("Transfer(address,address,uint256)")`）。
- **阈值与窗口**：单笔等值 ≥$10,000,000（ETH 主网）/ ≥$5,000,000（BSC）才记录（稳定币按 1.0 折算 USD；2026-11-09 由 $5M/$2M 调高）；单轮增量：ETH ≤120 块（约 24 分钟，覆盖 Mac 休眠缺口，每 30 块一次 `eth_getLogs`）、BSC ≤900 块（300 块/次；节点报“范围过大”时自动对半拆分）；超出部分记 `skipped_blocks`（不补扫）；首轮无游标时按各自 `scan_blocks_init` 回看；大额记录逐块取准确时间戳（单轮最多 20 个块）。
- **产物**：快照 `walletflow` 块（`enabled/source/chains[]/n_large/usd_total/recent/alerts/errors`（每链含扫描范围/调用数/错误））、exports `onchain/pressure/v1` 新增顶层 `walletflow` 字段（**只增**，含告警摘要）、`data/raw/<date>/walletflow.jsonl` 当日明细、游标 `data/state/walletflow-cursor.json`（`{chains: {ethereum, bsc}, updated_at}`，每链独立前进）、告警状态 `data/state/walletflow-alerts.json`。
- **事件驱动告警（已上线，2026-11-09）**：
  - **触发**：每轮新增的大额转账（状态去重键 `(tx, log_index)`；**同一 tx 多腿只保留 USD 最大一腿**）达到 `walletflow.alerts.min_usd=$10M` 即发；不等到日报。**一条消息聚合本轮**：同一 `from→to` 同币种的多笔合并为一行并显示“（N 笔）”；单条消息最多 `max_per_round=5` 笔，其余记 `suppressed`（`reason=round_limit`）。
  - **消息字段（2026-11-10 增强，每条聚合行）**：标题行 `🔴/🟡 P0/P1 币种 <金额> [（N 笔）] · 链`（链为 `ETHEREUM`/`BSC`）；`代币数量：<千分位，如 12,345,678.00 USDC> ｜ 区块：<区块号>`；`<from 缩写> → <to 缩写>`（前 6 位 + … + 后 4 位）；`地址标签：from: … / to: …`（两边都没有则“地址标签：双方均无公开标签”）；`流向：<实体> → <实体>（<方向说明>）`（实体=交易所/做市商/发行方或浏览器标签，不清楚写「未知」；方向与流向合并为一行，避免重复）；`方向：cex_in/cex_out/cex_internal 或 unknown（无法判断买卖方向）（推测：…）`；`背景：今日第 N 笔大额（累计 N 笔 / $X，占 Y%）；近 7 天 from 出现 X 次 / to 出现 Y 次；同一 tx 共 N 腿，仅报最大一腿`（有则写）；`交易：<Etherscan/BscScan 交易链接>`（ETH → `etherscan.io/tx/<hash>`、BSC → `bscscan.com/tx/<hash>`）；消息仍以 `🏷 任务：…` 结尾。
  - **分级**：`P0` = 单笔 ≥$50M **或** 转入交易所（`cex_in`）且 ≥$25M；`P1` = 其余。**发送时段**：`quiet_hours` 默认 `""` = 全天即时，仅 P0 立即发送；P1 不推送（仍落盘/进摘要）（送达即时，由接收端自行处理）；该配置仍可临时配回时段——一旦非空，仅 P1 记 `suppressed`（`reason=quiet_hours`）、P0 穿透。
  - **常规日限额**：`daily_cap=10` 条消息/天；当天大额笔数 > `daily_cap × burst_trigger_ratio(2) = 20` 后扩容为 `min(burst_cap=30, 10 + (今日笔数 − 20) // 2)`；P0 常规受 `daily_cap=10` 限制；超限记 `suppressed`（`reason=daily_cap`）。
  - **特殊时期（牛市/熊市/黑天鹅）解除日限额**：`special.enabled=true`、`manual=false`，命中任一即特殊期——当日大额笔数 ≥`p0_transfers_today=600`、|BTC 24h 涨跌幅| ≥`btc_move_24h_pct=6.0%`、CEX 24h 净流出 ≥`cex_outflow_bps=150`（bps）、稳定币 24h 净赎回 ≥15 亿美元（`stablecoin_outflow_usd=-1,500,000,000`）；`manual=true` 可手工强制（观察/应急）。特殊期内 **解除常规 10 条，但仍受特殊期硬上限 `max_messages=30` 条/天**，但仍保留 `(tx, log_index)` 去重、同 tx 多腿只留 USD 最大一腿、单轮 `max_per_round=5`；消息的“时间/本轮笔数”行追加「· ⚠ 特殊期：日限额已解除」。
  - **状态与重试**：`data/state/walletflow-alerts.json`（`date/messages/sent/suppressed/pending/last_sent_at`）；**发送成功才计入状态**，失败进 `pending`（≤20 条）下一轮重试；跨日自动重置。
  - **预览**：`--dry-run` 只生成告警文本（stdout/JSON 的 `walletflow.alerts_preview`），**不发送、不写告警状态**；`--fixtures` 回放不发送。
  - **地址标签补全（`alerts.enrich`，2026-11-10）**：`etherscan_tags=true`、`timeout_seconds=6`、`cache_days=30`。告警发送前对本轮要发的地址做 best-effort 补标签：渲染时内置 `walletflow.labels` 的 name 优先；Etherscan 地址页公开 name tag（`https://etherscan.io/address/<addr>` 页面 HTML，**非官方 API、免 key**）作为补充；**仅 ETH 地址抓取，BSC 不抓**；结果缓存 `data/state/address-tags.json`（`{地址: {tag, ts}}`，TTL 30 天；失败/空标签也会写缓存，避免反复抓）；抓取失败静默忽略，不影响发送与 rc；**`--dry-run` 预览不联网**（只有内置标签）。典型每轮 0–2 个新地址（最多不超过单轮 5 笔的双边地址数）。
  - **失败隔离**：告警异常只写快照 `walletflow.alerts.error`，**不阻塞主流程、不改变 rc**。
- **允许“猜”的边界（设计要求，2026-11-09 调整）**：方向为 `unknown` 时，告警文案可在括号内标注**低置信推测**，例如“unknown（推测：疑似与 Binance 14 关联的地址）”“疑似做市商/高频机构地址”；依据是最近 7 天 `data/raw/<date>/walletflow.jsonl` 的历史：与已知交易所地址同现 ≥3 次 → “疑似与 X 关联的地址”；地址出现 ≥5 次 → “疑似做市商/高频机构地址”；**猜不到就不写**。推测永远带“推测/疑似”字样、**只作线索，绝不当结论，不做实体/身份断言**；落盘记录本身仍只有方向字段，不新增归属字段。
- **B 模式：仅重大异常即时提醒（2026-11-10 用户选择）**：日常不逐笔打扰，只在两种情况下即时发 Telegram（每日 ≤2 条）：① 单笔**单向未知** ≥ `rare.min_usd=$500M`；② 最近 1 小时**交易所净流入** > 7 日同时段均值 ×3 且 ≥$100M。其余信息由每日 21:00 链上情报摘要给出（净流/噪声过滤/单向大额分析）。配置 `alerts.rare_only=true` + `alerts.rare.*`；想完全安静设 `enabled=false`，想恢复逐笔告警设 `rare_only=false`。
- **智能判断（2026-11-10，设计约束）**：单看链上转账看不出意义，推送前结合 价格波动（BTC/ETH/BNB 24h 绝对值 ≥3% → +2）、crypto-price 信号（P0 +2 / P1 +1）、crypto-news 新闻（6h 内 P0 命中关键词 +3 / P1 +2；关键词：hack/exploit/ETF/Fed/rate/liquidation/Binance/SEC/监管/清算/暴跌 等）、CEX 24h 净流出 ≥100bps → +1、稳定币净赎回 ≥5 亿 → +1、BTC 下跌 ≥2% 时转入交易所 → +1；总分 ≥smart.min_score=2 才推送，否则记 suppressed(reason=low_context)、只落盘/进摘要；巨鲸（≥$100M 或入所 ≥$50M）始终推送；消息新增「推送理由：…」行说明为什么发。
- **每日摘要**：保留“链上大额转账（ETH 主网 + BSC 的 USDT/USDC）：N 笔 ≥ 阈值，合计 X；最大一笔（from → to）”行，**新增一行**“今日链上告警：N 条（常规上限 10，特殊期可解除）；抑制 K 条”（读告警状态文件；摘要本身仍不是告警通道）。
- **失败降级**：RPC 失败只记 `snapshot.walletflow.error`，**不阻塞主流程、不改变 rc、不进 `errors[]`**；游标不前进，下一轮从旧游标重试。
- **实测（2026-11-09，阈值调高前 $5M 口径）**：ETH 120 块内 23–41 笔 ≥$5M（视窗口），单轮最高约 5.4 亿美元；BSC 900 块内 0 笔 ≥$2M（BSC 稳定币大额转账明显更少）；单轮约 20–40 次 RPC 调用、耗时 30–60 秒。阈值调高后笔数相应减少。
- **阶段**：4 类卖压候选仍是影子期（不推送）；**walletflow 的链上大额转账告警已上线**，其余归因/卖压告警仍属 M3。

### M3 规划（未实现，不要当作已交付；仅指 4 类卖压候选）

- **4 类卖压候选的 Telegram 推送 / 卡片渲染**：P0/P1 语义、证据/反证/不可知三段上卡片；等 2 周影子校准达标（精确率 ≥60%、日均候选 ≤3 条）才开。**注意：walletflow 的链上大额转账告警不在此列，已于 2026-11-09 上线（事件驱动、全天即时、常规日限额、特殊期解除日限额）。**
- **4 类信号的去重冷却 / 静默时段 / 每日限额**：`rules.yaml` 的 `delivery.*` 仍是配置骨架，代码不读取；walletflow 告警有自己的一套去重/全天即时/日限额与特殊期解除限额（见 §2）。
- **feedback / calibration 回算**：`data/state/feedback.jsonl`、`calibration.jsonl` 与自动精确率回算均未实现。
- **`crypto-analyst` 消费 exports**：契约已写盘，但**尚无消费者**。

---

## 3. 数据源能力矩阵（2026-11-09 核实，含本机实测）

| 数据源 | 能回答什么 | 免费额度 / 限流 | 成本 | 状态 | 备注 |
|---|---|---|---|---|---|
| **DefiLlama `/cexs`** | 88 家 CEX 的储备净流（24h/1w/1m，USD 聚合）+ TVL | 免 key；本机连续 15 次全 200，无公开硬限流 | $0 | ✅ 实测 | **全币种聚合，非分币种**；`/entities` 返回 402 需订阅 |
| Binance 现货 `/api/v3/aggTrades` | 逐笔成交 + `isBuyerMaker`（taker 方向）→ 主动买/卖占比、大单 | 免 key；limit≤1000；权重 4；IP 权重 6000/min | $0 | ✅ 实测 | 每 5 分钟轮询 3 个币对，权重预算充裕 |
| Binance USDⓈ-M `/fapi/v1/aggTrades` | 永续逐笔 taker 流 | 免 key；limit≤1000；权重 20 | $0 | ✅ 实测 | 比现货贵 5×，未接入（本模块不重采） |
| OKX `/api/v5/public/liquidation-orders` | 清算明细（方向/仓位/价格） | 免 key；实测可取 `details[]` | $0 | ✅ 实测 | **仅 OKX 口径**；Binance 只有 WS `!forceOrder@arr` |
| Binance `!forceOrder@arr` | 全市场清算流 | 免 key，仅 WebSocket | $0 | ✅ | 无 REST，标准库不可用 ⇒ 不做 |
| DefiLlama `/stablecoins` | 稳定币市值/价格 → 24h 净增发 | 免 key | $0 | ✅（libs 已有解析器） | 注意跨链 mint/burn 噪音 |
| **crypto-price exports** | 价格、24h 涨跌幅、成交量；**M2 新增 `funding_rate` / `oi`** | 本地只读 JSON（`../crypto-price/data/exports/latest.json`） | $0 | ✅ 已接入 | `funding_rate` 来自 Binance 永续 `/fapi/v1/premiumIndex`，`oi` 来自 `/fapi/v1/openInterest`；`funding_ts`/`oi_ts` 当前未读 |
| mempool.space | BTC 出块、矿池、手续费、算力 | 免 key；**限流数值未公开**（会 429/封禁） | $0 | ✅ | 未接入（M2 不做矿工收入信号） |
| Etherscan V2 | ETH 大额 ERC20 转账、地址余额 | 免费 **3 calls/s、10 万/日**，仅 selected chains | $0（需注册 key） | ❌ **未接入，key 未使用** | **nametag 属 Pro Plus、导出标签属 Enterprise ⇒ 免费 API 拿不到实体标签**；walletflow 走公共 RPC，不需要 Etherscan key，env 变量保留为备用源（当前不读取）；**告警补标签另走公开地址页抓取**（`etherscan.io/address/<addr>` 页面 HTML 解析 name tag，免 key、仅 ETH、缓存 30 天、失败忽略），不是 nametag API |
| Blockscout | 多链地址/代币/标签 | 免费 10 万 credits/日、5 RPS | $0 | ✅ | 标签覆盖弱，仅作兜底（未接入） |
| **公共 RPC `eth_getLogs`** | 大额转账监控 + 事件驱动告警：USDT/USDC 的 Transfer 日志（`topic0 = keccak Transfer`）→ ETH 单笔 ≥$10M / BSC 单笔 ≥$5M 记录；新增 ≥$10M 即发 Telegram 告警 | 免 key（`ethereum-rpc.publicnode.com` / `bsc-rpc.publicnode.com`）；**ETH 120 块/轮（30 块/次）、BSC 900 块/轮（300 块/次；节点报“范围过大”时自动对半拆分）、约 20–40 次 RPC 调用/轮** | $0 | ✅ **已接入（walletflow + 告警已上线）** | 扫 ETH 主网 + BSC 的 USDT/USDC；默认排除 mint/burn；**不识别地址归属**；BSC 无免费标签源；BSC 单次区块范围取 300 块（400+ 会被节点拒绝） |
| Arkham | 实体标签/机构流向 | 试用 10 万 credits | **≥$100/mo** | ✅ | 可选，默认关 |
| CryptoQuant | 分币种交易所余额、矿工抛压 | 免费仅 market data | **≥$99/mo** | ✅ | 可选，默认关 |
| Glassnode | BTC/ETH 链上指标 | Advanced $49/mo **不含 API** | **API ≥$584/mo** | ✅ | 太贵，默认关 |
| CoinGlass | 全市场清算/订单簿 | 未见免费档 | **≥$29/mo** | ✅ | 用 OKX 代理替代 |
| Dune | SQL 查 CEX netflow | 免费档**只能看不能跑**；新号 2500 credits/14 天 | 各档月费 | ✅ | 不做 |

**结论**：免费能覆盖"逐笔主动卖盘 + 交易所级净流 + 清算 + 稳定币增量 + 价格/OI/资金费率背景"，以及"稳定币大额转账的金额/地址/时间"（walletflow）；**免费拿不到"分币种交易所余额"和"实体标签"**——这两项恰好是"谁"的核心，因此身份结论不可得。地址在转账记录里仍是假名，归属不可得。

---

## 4. 信号与归因设计：把"谁"降级为可检验类别

### 4.1 三个可得层次

| 想知道 | 可得性 | 我们怎么回答 |
|---|---|---|
| 卖的是**哪边**（主动卖/被动平仓） | ✅ 可得 | `aggTrades.isBuyerMaker` → taker 卖占比；清算明细 → 多/空头被爆 |
| 卖的是**哪类仓位**（多头强平/空头加仓/主动抛售） | ✅ 可推断（概率） | 价格方向 × OI 变化 × 资金费率 × 清算方向（读 crypto-price） |
| 卖的是**谁**（个人/机构/矿工/ETF） | ❌ 免费不可得 | 只回答"增量资金在撤/交易所储备在流出"，不指认主体 |

### 4.2 卖压代理矩阵

| # | 信号 | 数据（免费） | 能回答 | 不能回答 | 归属层面 | M2 状态 |
|---|---|---|---|---|---|---|
| 1 | `taker_sell_pressure` | Binance 现货 `aggTrades` | 主动卖占比、单笔大额卖、卖压集中在哪个价位 | 卖方身份、是否对敲 | 微观（分钟级） | ✅ 已实现 |
| 2 | `cex_reserve_outflow` | DefiLlama `/cexs` | 交易所储备在流出还是流入（全币种 USD） | 分币种、单笔、来源 | 中观（日级） | ✅ 已实现 |
| 3 | `liquidation_cascade` | OKX 清算 REST + OI（price exports） | 哪边被爆、爆了多少、是否连锁 | 全市场（仅 OKX 口径） | 结构（小时级） | ✅ 已实现 |
| 4 | `stablecoin_outflow` | DefiLlama `/stablecoins` | 增量资金是否撤退（净增发转负） | 资金具体去向 | 宏观（日级） | ✅ 已实现 |
| 5 | `funding_oi_regime` | crypto-price `exports/latest.json` | 多头拥挤/空头加仓（资金费率+OI） | — | 结构（小时级） | ✅ **作为归因输入**（不单独出信号） |
| 6 | `miner_revenue` | mempool.space | 矿工收入/算力骤降（潜在抛压背景） | 矿工是否真在卖 | 宏观（日级，弱信号，仅 P2） | ❌ 未接入（M3 之后再评估） |
| 7 | `etf_flow` | 引用 crypto-news（不重采） | ETF 净流出（二手新闻口径） | 申赎主体 | 宏观（日级） | ❌ 未接入（M3 卡片引用） |

> 另有 **M3 附加能力 `walletflow`（链上大额转账监控）**：它**不是第五类卖压信号**，不参与候选与归因判定；只提供"某时刻有 N 笔 ≥$10M（BSC ≥$5M）的 USDT/USDC 转账"这一事实（默认排除 mint/burn），写入快照/exports/每日摘要供人工研判，并在每轮新增 ≥$10M 时发**事件驱动 Telegram 告警**（仅 P0 推送、全天即时（P1 不推送，仍落盘/进摘要） + 日限额 + 特殊期解除限额，已上线）。**不输出地址归属或实体结论**；方向 `unknown` 时的“推测”备注必须明确标注、只作线索。

### 4.3 归因规则（M2 已实现，最终以 [design.md](design.md) §5 为准）

归因按固定顺序判断，**先匹配先生效**；价格未下跌（24h 方向非 `down` 且涨跌幅 > −0.5%）时不输出资产级归因。

| 顺序 | 类别 | 触发条件（全部满足） | 置信度 |
|---|---|---|---|
| 1 | **多头被强平（被动卖盘）** `long_liquidation` | OKX 15m 清算额 ≥ 资产 P1 且多头占比 ≥ 0.6 | OI 15m ≤ −2% → `high`；OI > 0 → `low`；其余/未知 → `medium` |
| 2 | **空头加仓 / 主动砸盘** `short_add` | 5m 主动卖占比 ≥ 58% 且 OI 未显著下降（历史不足也满足） | OI ≥ 2% 且资金费率 ≤ 0 → `high`；其余 → `medium` |
| 3 | **增量资金撤离（卖压背景）** `capital_exit` | CEX 24h 净流 ≤ −50bps TVL 且稳定币 24h 净赎回 ≤ −5 亿美元 | `medium`（市场级 \|CEX bps\| ≥ 150 时 `high`） |
| 4 | **去杠杆（非新增抛售，反证）** `deleverage` | OI 15m ≤ −2%，且无强平/主动卖/资金撤离证据 | `medium` |

每类结论固定三段：

- **证据 `evidence`**：价格、资金费率、主动卖占比、清算额与多头占比、CEX 净流、稳定币净赎回、OI 变化等（只写实际拿到的数值）。
- **反证条件 `counter_evidence`**：什么情况说明推断错了，例如"若 OI 未下降，则更可能是新开空/主动抛售""清算仅 OKX 口径""CEX 净流是全币种聚合，不能归到单一币种"。
- **不可知 `unknown`**：固定"无法判断卖方身份（CEX 匿名撮合、链上地址假名）；本结论只是概率归因"，并按类别补充（如"被强平的具体账户不可见"）。

> **归因只回答"哪类仓位/哪类资金在卖"，不回答"谁"。** 置信度低（`low`）表示类别证据弱（如 OI 上升却出现大额清算，更像新开空而非强平），但仍只是概率归因；`unknown` 段永远存在。

---

## 5. 阈值怎么定

### 5.1 四条原则

1. **相对归一化**：不用裸美元。净流用 `/TVL`（bps），大单用 `/24h 成交额`（bps），清算用 `/24h 成交额`。
2. **分位 + 绝对额双门槛**：`max(绝对额初值, 滚动 90d p90/p95)`。冷启动无历史时用绝对额初值，30 天后切分位（**分位化尚未实现，M3**）。
3. **AND 门控**：至少 2 个独立维度同向才允许 P0/P1（如净流 + taker 卖占比；清算 + OI 下降）。**部分 AND 条件仍未实现**（见 §5.3）。
4. **资产分层**：BTC/ETH/BNB 流动性差一个量级，阈值分开写在 `config/rules.yaml` / `watchlist.yaml`。

### 5.2 已实现阈值（与代码/配置一致）

| 信号 | 计算 | 分级条件 |
|---|---|---|
| `taker_sell_pressure` | 5m 主动卖额占比 + 大单笔数 + 样本量 | P1：占比 ≥58% **且** 大单 ≥3 笔；P0：占比 ≥65% **且** 大单 ≥6 笔；5m 成交额低于 `min_window_quote_usd[asset]` 不出候选 |
| 大单定义 | 单笔等值 | BTC ≥$5M、ETH ≥$2M、BNB ≥$1M |
| `cex_reserve_outflow` | `Σ inflows_24h / Σ TVL`（bps） | P1：≤ −50bps（或 1w ≤ −400bps）；P0：≤ −150bps；TVL <$10 亿的交易所不参评 |
| `liquidation_cascade` | 15m OKX 清算额 × 多头占比 | P1：BTC ≥$30M / ETH ≥$15M / BNB ≥$8M；P0：BTC ≥$100M / ETH ≥$50M / BNB ≥$25M；清算样本覆盖 <5 分钟不出候选 |
| `stablecoin_outflow` | 24h 总市值净变化 | P1：≤ −$5 亿；P0：≤ −$15 亿 **且** BTC 24h 跌幅 ≥3% |
| 归因辅助 | 15m OI 变化 | `oi_significant_pct=2.0`：≤ −2% 视为显著减仓（强平升 high / 去杠杆成立），≥ +2% 视为显著加仓（空头加仓升 high 条件之一）；历史不足为 `null`，置信度降级 |

阈值全部在 [config/rules.yaml](../../crypto-onchain/config/rules.yaml)，改阈值不改代码。

### 5.3 目标项（未实现，留待 M3 校准后决定）

- taker "主动卖占比连续 3 个 5m 窗口"；
- taker P0 额外要求"5m 跌幅 ≥0.8%"；
- CEX P0 要求 taker 卖占比 AND；
- 清算 1h 窗口 + OI 下降 / 价格 15m 跌幅的额外 AND；
- 分位阈值与 90 天滚动窗口；
- 影子期精确率/日均候选的自动回算脚本。

### 5.4 分级与打扰控制

**4 类卖压候选（M3 规划，当前未生效）**：

- P0：优先推送；P1：按静默/日限额策略（M3 规划）；P2：只落盘归档。
- 去重键 `onchain:<asset>:<signal>:<direction>:<bucket>`；冷却 30–240 分钟；同类同轮只报最强一条。
- 每日 P1 上限 **5 条**；静默时段与送达策略只写在 `delivery.*` 骨架里（M3 前代码不读取）。
- **方向门控**已实现：链上信号与价格方向不一致时只记"潜在卖压"（`would_alert=false`），不打扰。
- 以上送达策略只在 `rules.yaml` 留了配置骨架，**代码尚未读取**。

**walletflow 链上大额转账告警（已上线，独立送达层）**：

- 分级：P0 = 单笔 ≥$50M 或转入交易所且 ≥$25M；P1 = 其余。**`quiet_hours` 默认空 = 全天即时**，仅 P0 立即发送；P1 不推送（仍落盘/进摘要）（由接收端自行处理）；该配置可临时配回时段，此时 P1 记 `suppressed`、P0 穿透。
- 特殊期（牛市/熊市/黑天鹅）：`special.enabled=true`、`manual=false`；命中当日 P0 笔数 ≥600、|BTC 24h 涨跌幅| ≥6%、CEX 24h 净流出 ≥150bps、稳定币 24h 净赎回 ≥15 亿美元任一，或 `manual=true` 手工强制 → 解除常规 10 条（特殊期硬上限 30 条/天）。
- 去重：`(tx, log_index)`，**同一 tx 多腿只保留 USD 最大一腿**（特殊期同样生效）；范围：本轮新增；聚合：同一 `from→to` 同币种合并为一行（“（N 笔）”），单条消息最多 5 笔。
- 限额：常规 ≤10 条消息/天（P1 不推送；特殊期解除）；当天大额笔数 >20 时按 `min(30, 10 + (笔数−20)//2)` 扩容；P0 默认要经过**智能判断**（见下）才会推送；P0 常规受 daily_cap=10 限制；**巨鲸保护**：单笔 ≥$100M 或转入交易所 ≥$50M 不受 10 条限制，额外最多 5 条/天（总上限 15），消息标「🐋 巨鲸」；特殊期解除以上限额但保留去重与单轮 5 笔。
- 状态：`data/state/walletflow-alerts.json`；发送成功才计入；失败进 `pending`（≤20）下轮重试；`--dry-run` 只预览不写状态；特殊期消息在“时间/本轮笔数”行标注「· ⚠ 特殊期：日限额已解除」；地址标签缓存单独写 `data/state/address-tags.json`（TTL 30 天）。
- 消息内容（2026-11-10 增强）：标题行（图标+分级+币种+金额+链）、代币数量（千分位）与区块号、from→to（缩写）、地址标签（内置优先 → Etherscan 地址页 name tag → “双方均无公开标签”）、方向（含低置信“推测”备注）、背景（今日第 N 笔/累计笔数与金额/占比、近 7 天 from/to 出现次数、同 tx 多腿提示）、Etherscan/BscScan 交易链接；`--dry-run` 预览不补浏览器标签（不联网）。

---

## 6. 误报怎么控（本模块成败关键）

| # | 手段 | 具体做法 | M2 状态 |
|---|---|---|---|
| 1 | **多维度 AND** | 信号层"占比 + 大单 + 样本量"三条件；归因层多个条件同时成立才出类别 | ✅ 已实现 |
| 2 | **方向一致性门控** | 卖压信号必须与 `crypto-price` 的实际价格方向同向；否则 `aligned=false`、`would_alert=false` | ✅ 已实现 |
| 3 | **归一化防漂移** | `/TVL`（bps），不写死美元数 | ✅ 已实现（分位化未实现） |
| 4 | **已知误报源清单** | 交易所 hot→cold 划转、跨链桥 mint/burn、做市商对敲、ETF 申赎时点；每条结论写 `counter_evidence` | ✅ 已写进三段输出 |
| 5 | **数据质量门** | 源降级 → 只降级不阻塞；exports 过期拦截、DefiLlama 异常波动检测 | ❌ 部分（降级已做，数据质量门属 M3） |
| 6 | **影子模式 14 天** | 只落盘不推送；回算"信号出现后 1h 同向 ≥0.5%"的精确率，**≥60% 且日均候选 ≤3 条**才开 P1 推送 | ⏳ 进行中（无自动回算脚本） |
| 7 | **误报回收闭环** | 推送带反馈口令；`feedback.jsonl` 记 good/noise；每 50 条或每周重算阈值 | ❌ M3 |
| 8 | **止损线** | 影子期不达标 ⇒ 不做实时告警，只保留"每日资金流归因摘要"进日报 | ❌ M3 决策 |
| 9 | **限额+时段** | 4 类卖压候选：每日 P1 ≤5；静默/限额按 `delivery.*` 骨架（M3）；连续 3 天 P1 超限自动收紧 | ❌ M3（4 类信号）；✅ walletflow 告警已有常规日限额/扩容/特殊期解除限额/全天即时（见 §5.4） |

> 误报预算显式化：影子期允许 ≤1 条无效/天；上线后连续 7 天有效率 <50% 即回滚到"摘要模式"。

---

## 7. 与现有模块的边界

```
crypto-price ──(exports/latest.json: price/change_24h_pct/funding_rate/oi)──> crypto-onchain（只读，不重采）
crypto-news  ──(ETF 流向新闻/鲸鱼报道)──────────────────> crypto-onchain（M3 卡片引用，不重采）
crypto-onchain ──(exports/onchain-pressure.json: onchain/pressure/v1)──> crypto-analyst（M3 接入；当前无消费方）
libs/crypto_core：http/events/notify/store/timeutil/config/defillama 复用；
                  market 解析器（binance.aggTrades / okx.liquidations / defillama）带内联样本单测
```

- 阈值/路由/信号组合全部留模块 `config/`，不进 libs（约定 §6）。
- 模块间零 import；4 类卖压候选告警沿用 `alert/v1` 与 P0/P1/P2（M3 生效）；**walletflow 大额转账告警已用 Telegram 的事件驱动消息（仅 P0 推送、全天即时（P1 不推送，仍落盘/进摘要） + 日限额 + 特殊期解除限额）**。
- 调度 Label：`com.crypto-research.crypto-onchain`；`collect.sh` 已含单实例锁、healthcheck、每日 prune。
- `onchain/pressure/v1` **只增字段**；在 `crypto-analyst` 真正接入前，不得假设存在下游消费者。

---

## 8. 验收标准（可测量）

1. `make gate` 离线全绿（ruff + libs + 本模块 150 项单测 + fixtures 端到端），不联网。**当前 150 项**（indicators 18 / signals 22 / attribution 15 / digest 7 / walletflow 12 / walletflow_alerts 23 / context 9 / bot_env 4 / e2e 5）。
2. `--fixtures` 回放强平瀑布 / 主动砸盘 / 去杠杆场景，输出类别与置信度正确；结论含"证据/反证/不可知"三段；**全流程不出现身份断言**（关键词黑名单）。**M2：单测覆盖 4 类归因 + 三段 + 身份黑名单；e2e 用一套综合 fixtures（强平/资金撤离）**，三套独立 fixtures 未拆。
3. `./scripts/run.sh --dry-run --json` 联网一轮：输出 ≥4 类信号，0 API key；DefiLlama 净流与官网差 ≤1%。**M2：需手动跑 `verify.sh --with-network`，未纳入离线验收**。
4. 权重预算：Binance 3 币对 × 每分钟 12 次 × 权重 4 = 144 weight/min，远低于 6000/min；OKX 清算 5 分钟一次。✅ 已按 300s 轮询核算。
5. 影子模式 14 天（4 类卖压候选）：日均候选 ≤3；精确率（事后 1h 同向 ≥0.5%）≥60%；重复推送 0。⏳ 校准期；4 类候选无推送，walletflow 告警按 `(tx, log_index)` 去重 + 日限额控制。
6. 分级/冷却/静默/日上限/去重键均有单测。**4 类卖压送达层属 M3，当前只有 `severity`/`aligned` 单测；walletflow 告警的分级/全天即时（含可配置静默）/特殊期判定/日限额/扩容/去重/聚合/推测/预览不写状态/标签解析与缓存命中不联网已有 21 项单测。**
7. `exports/onchain-pressure.json` 满足 `schema/generated_at/source_module` 契约且只增字段。**M2：e2e 断言 `schema`/`source_module`/`assets[*].attribution`/`oi_change_15m_pct` 等字段；没有独立 JSON Schema 文件**。
8. 连续运行 7 天：无 429/封禁、无数据断流超 15 分钟、`data/` 增长 <50MB。⏳ 影子期观察。
9. 归因结论固定 `evidence` / `counter_evidence` / `unknown` 三段，且三段中不出现身份词。**M2：已实现并有单测**。
10. 链上大额转账：离线单测覆盖解码/阈值/去重/可选标签方向/扫描区间（12 项）；fixtures e2e 断言快照与 exports 的 `walletflow` 字段（ETH $10M / BSC $5M 口径）；RPC 失败时只写 `walletflow.error`、游标不前进、rc 不变。**M3 附加能力：已实现并验证**。
11. 链上大额转账告警：离线单测覆盖分级（P0 单笔/入所）、日限额扩容公式、全天即时与可配置静默（P1 抑制、P0 穿透）、**特殊期判定（四条触发条件 + `manual` 手工强制）与解除日限额**、去重（含同 tx 多腿只保留最大一腿）、推测备注、聚合“（N 笔）”、预览不写状态、历史构建、**地址标签补全（`parse_etherscan_tag` 解析页面 name tag + `enrich_tags` 缓存命中不联网）**（21 项）；`--dry-run` 只预览不发送、不联网；发送失败进 `pending`。**已实现并验证**。

---

## 9. 成本与依赖

| 项 | 成本 | 是否默认 |
|---|---|---|
| DefiLlama、Binance/OKX 公开接口、crypto-price exports | $0 | ✅ 默认开启 |
| 公共 RPC `eth_getLogs`（walletflow） | $0（免 key） | ✅ 默认开启 |
| Etherscan 地址页（仅告警补标签，`alerts.enrich`） | $0（公开页面抓取，免 key；非官方 API、best-effort） | ✅ 默认开启（仅 ETH；BSC 不抓） |
| Etherscan 免费 key（可选，仅 ERC20 大额转账；**无实体标签**） | $0（注册） | ❌ 未接入；walletflow 走公共 RPC，key 未使用（保留为备用源） |
| Arkham API（实体标签/机构流向） | ≥$100/mo | ❌ 默认关 |
| CryptoQuant（分币种交易所余额、矿工流） | ≥$99/mo | ❌ 默认关 |
| Glassnode API | ≥$584/mo | ❌ 默认关 |
| CoinGlass（全市场清算） | ≥$29/mo | ❌ 默认关（用 OKX 代理） |

共享能力：复用 `http`、`events`、`notify`、`store`、`timeutil`、`config`、`market.defillama`；`libs/crypto_core/market/` 已有 `binance` / `okx` / `defillama` 解析器（带单测）。M2 归因层与 walletflow 采集只依赖标准库（walletflow 用 `urllib` 直连公共 RPC）；**walletflow 告警与每日摘要复用 libs 的 `notify`（Telegram Bot API，$0）**，无新增第三方依赖。

---

## 10. 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| **身份幻觉**：用户把"多头强平"读成"机构在卖" | 决策错误 | 结论固定 `unknown` 段；标题只写类别；README 首屏写死边界 |
| 免费标签缺失，链上转账无法判"入所" | 净流只能到交易所聚合层 | 结论标注口径；walletflow 数据记录方向仅在内置验证标签命中时标注（`cex_in`/`cex_out`/`cex_internal`），其余 `unknown`；告警“推测”备注必须带“推测/疑似”字样、只作线索 |
| **推测备注被读成身份结论** | 用户把“疑似与 Binance 关联的地址”当成“某机构在卖” | 推测只在方向 `unknown` 时出现、固定“推测/疑似”前缀；落盘记录不新增归属字段；文案禁止“某机构/某巨鲸/某基金”等实体断言 |
| 链上告警刷屏 | 用户关掉告警 | P1 不推送；常规 ≤10 条消息/天（特殊期解除）；**特殊期解除日限额但仍保留去重与单轮 5 笔**、消息标注「⚠ 特殊期」；发送成功才计入状态 |
| **地址归属幻觉**：把"大额转账"读成"某机构在买/卖" | 误判主体 | walletflow 不输出归属字段，摘要只报笔数/合计/最大一笔；口径写死"不判断地址属于谁" |
| 公共 RPC 不稳定/限流 | 本轮无大额转账数据 | 失败只记 `walletflow.error`，不阻塞主流程、不改 rc；游标不前进，下轮重试；`rpc_url` 可换备用 RPC |
| DefiLlama 全币种聚合 ≠ 分币种 | 可能误读成"BTC 净流出" | 文案写"交易所储备（全币种 USD）"；分币种结论明示不可得 |
| OI 只有 Binance USD-M 单所 | 跨所杠杆变化不可见，置信度可能偏 | 每类 `counter_evidence`/`unknown` 注明；OI 历史不足自动降级 |
| BNB 链数据源不确定（BscScan 免费档份额未确认） | BNB 覆盖弱 | BNB 标记"尽力而为"，缺数据时降级不阻塞 |
| Binance 地区性 451 / 429 | 数据断流 | 上线前在目标环境复测；OKX 兜底；降频；`Retry-After` 退避 |
| 误报疲劳 | 用户关掉告警 | 影子模式 + 日上限 5 + 有效率止损线（§6） |
| 范围蔓延成"全链上平台" | 工期失控 | 固定 4 类信号与 4 类归因；付费源与自建索引明确不做 |
| 阈值过拟合影子期 | 上线后失效 | 分位阈值 + 每周重算（M3）；每次阈值变更记录到 `data/state/calibration.jsonl`（M3） |

---

## 11. 里程碑与需要确认的决策点

| 阶段 | 内容 | 工期 | 产出 |
|---|---|---|---|
| M0 | 确认本文档 | — | 4 个决策（见下） |
| M1 ✅ | 模块骨架 + 4 类免费源采集 + 影子落盘 + 离线单测 | 2026-11-09 完成 | `--dry-run` 可跑、fixtures 回放、`make gate` 全绿 |
| M2 ✅ | **归因引擎**（4 类 + 置信度 + 三段）、**OI 历史**（官方 `openInterestHist` 主 + 本地兜底）、**`onchain/pressure/v1` 导出**、候选带归因摘要、`--status` 显示归因、150 项离线单测（含 walletflow 12 + walletflow_alerts 23 + context 9 + bot_env 4） | 2026-11-09 完成 | 快照/候选/exports 均含归因；OI 可算 15m 变化；4 类候选仍只落盘不推送 |
| M2.5 ✅ | **每日影跑摘要**（21:00，只汇总不告警；`collect.sh` 每日入口 + 当日去重 + 校准 Day 计数） | 2026-11-09 完成 | Telegram 摘要一条/天；不是告警，不影响 M3 判定 |
| M3 附加 ✅ | **链上大额转账监控（walletflow）**：公共 RPC `eth_getLogs` 扫 ETH 主网 + BSC 的 USDT/USDC Transfer（ETH 单笔 ≥$10M、BSC ≥$5M）；游标 + 增量按链；默认排除 mint/burn；快照/exports/每日摘要 + 明细落盘；**不识别地址归属**；12 项单测 | 2026-11-09 完成 | 记录、游标与摘要上线 |
| M3 附加告警 ✅ | **事件驱动链上大额转账告警（walletflow_alerts）**：每轮新增 ≥$10M 发一条聚合 Telegram；仅 P0 推送、全天即时；P1 不推送（仍落盘/进摘要）、**特殊期解除日限额**、去重、发送失败重试、明确标注的“推测”备注、dry-run 预览；**消息字段增强（代币数量/区块/地址标签/背景/交易链接；Etherscan 页面 name tag 缓存 30 天，dry-run 不联网）**；22 项单测 | 2026-11-10 更新 | 链上大额转账告警可用；**4 类卖压候选推送仍属 M3** |
| M3（规划） | 2 周影子校准 → 达标开 4 类卖压候选的 Telegram 推送/卡片、去重冷却/静默/日限额、feedback/calibration、`crypto-analyst` 消费 exports | 14 天+ | 上线推送 or 降级为日报摘要 |

**决策记录（2026-11-09 设计评审）**

1. ✅ 接受"只说类别、不说身份"的产品定位（前提已确认）。
2. ✅ 接受 **2 周影子模式**（4 类卖压候选期间只落盘、无实时告警）。**例外（用户 2026-11-09 明确要求）：walletflow 的链上大额转账告警提前上线**（事件驱动、全天即时、常规日限额、特殊期解除日限额），并允许方向 `unknown` 时带“推测”字样的低置信备注。
3. ✅ 可注册 **Etherscan 免费 key**（**当前未使用**：walletflow 走公共 RPC，不需要 key；环境变量保留为备用源（当前不读取）。免费档无实体标签，接入收益有限，留作可选）。
4. ✅ **付费预算维持 $0**：CoinGlass（≥$29/mo）、Arkham（≥$100/mo）、CryptoQuant（≥$99/mo）、Glassnode API（≥$584/mo）全部默认关闭；若将来要买，按 `config/rules.yaml` 之外的配置扩展位接，不动已建部分。

---

## 12. 附：核实方法与日期

- 核实日期：**2026-11-09（本地时区）**，本机实测 + 官方文档双源；限流/价格会变，上线前需在目标运行环境复测。
- 实测端点：`api.llama.fi/cexs`（88 家交易所，含 Binance/OKX）、`api.binance.com/api/v3/aggTrades`、`fapi.binance.com/fapi/v1/aggTrades`、`okx.com/api/v5/public/liquidation-orders`、`api.binance.com/api/v3/exchangeInfo`（权重 6000/min）、`ethereum-rpc.publicnode.com` 与 `bsc-rpc.publicnode.com`（`eth_blockNumber` / `eth_getLogs` / `eth_getBlockByNumber`）。
- walletflow 实测（2026-11-09，**阈值调高前 $5M/$2M 口径**）：ETH 120 块 41 笔 ≥$5M（含 5 笔 mint/burn；排除后 36 笔 / 5.91 亿），后续轮次 23–24 笔 / 5.0–5.4 亿；BSC 900 块 0 笔 ≥$2M；单轮约 20–40 次 RPC；游标按链前进（`{chains:{ethereum,bsc}}`）。当日稍后把记录阈值调高为 **ETH $10M / BSC $5M**，并上线事件驱动告警（告警阈值 `alerts.min_usd=$10M`）。
- M2 新增依赖的派生字段来自 `crypto-price` exports（其上游为 Binance 永续 `/fapi/v1/premiumIndex` 与 `/fapi/v1/openInterest`）；本模块只读 `funding_rate` / `oi`，不直接请求 Binance 衍生品接口。
- 2026-11-10 告警消息增强：字段扩充（代币数量/区块/地址标签/背景/交易链接）与 `alerts.enrich`（Etherscan 地址页 name tag，仅 ETH，缓存 `data/state/address-tags.json` 30 天，失败静默，dry-run 不联网）；单测 98 → 100（walletflow_alerts 19 → 21）。
- 文档依据：[Binance Spot REST](https://github.com/binance/binance-spot-api-docs/blob/master/rest-api.md)、[Etherscan Rate Limits](https://docs.etherscan.io/rate-limits)、[Etherscan nametag（Pro Plus）](https://docs.etherscan.io/api-reference/endpoint/getaddresstag)、[Blockscout API](https://docs.blockscout.com/devs/blockscout-api)、[Arkham access](https://arkm.com/llms/guides/getting-access.md)、[CryptoQuant pricing](https://cryptoquant.com/pricing)、[Glassnode pricing](https://studio.glassnode.com/pricing)、[CoinGlass pricing](https://www.coinglass.com/pricing)、[Dune credits](https://docs.dune.com/resources/credits-billing/overview)、[mempool.space REST](https://mempool.space/docs/api/rest)。
- 不确定项：Etherscan 免费档是否含 BNB Chain(56)（docs 仅写 selected chains）；mempool.space 具体 QPS 未公开；Nansen/Dune 各档月费未取到官方数字（本文只写区间）。

---
> 文档维护约定见 [docs/README.md](../README.md)；M1/M2 已完成（代码 + 五件套 + 根 README/CI 登记），M3 开工时同步更新 design/technical。
