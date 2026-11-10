# crypto-onchain · 链上与交易所资金流"卖压归因"（M2 归因引擎 + 链上大额转账与事件驱动告警）

> 本目录是 `crypto-research` 仓库的一个需求模块（2026-11-09 新建；M1 影子采集 → M2 归因引擎 → M3 附加链上大额转账与事件驱动告警）。
> 仓库级约定见 [../README.md](../README.md) 与 [../docs/conventions.md](../docs/conventions.md)。
> 文档见 [docs/crypto-onchain/](../docs/crypto-onchain/README.md)：[需求评估](../docs/crypto-onchain/requirement.md) · [设计](../docs/crypto-onchain/design.md) · [业务架构](../docs/crypto-onchain/architecture.md) · [技术架构](../docs/crypto-onchain/technical.md)。

**这个模块回答什么**：放量下跌时，卖压来自哪一类（多头被强平（被动卖盘）/ 空头加仓 / 主动砸盘 / 增量资金撤离 / 去杠杆反证）。
**这个模块不回答什么**：**谁在卖**。CEX 匿名撮合 + 链上假名 + 实体标签付费，身份不可得；结论只含"类别 + 证据 + 反证条件 + 不可知说明"，永不出现"某机构/某巨鲸在卖"的断言。

**M3 附加能力（记录 + 告警已上线）**：链上大额转账监控（`walletflow`）——公共 RPC 免 key 扫 ETH 主网 + BSC 的 USDT/USDC 的 Transfer 日志，记录阈值 **ETH ≥$10M / BSC ≥$5M**（默认排除 mint/burn）；只回答"链上发生了什么大额转账"，**不回答"地址属于谁"**。每轮新增 ≥$10M 即发一条聚合 **Telegram 告警**（仅 P0 推送、全天即时（P1 不推送，仍落盘/进摘要）、常规日限额、特殊期解除日限额、明确标注的“推测”备注）；**2026-11-10 消息增强**：每条聚合行含代币数量（千分位）/区块号/from→to 缩写/地址标签/方向/背景/交易链接，地址标签来自内置清单或 Etherscan 地址页公开 name tag（仅 ETH、缓存 30 天、失败忽略、dry-run 不联网）。4 类卖压候选仍只落盘不推送。

## 当前阶段：M2 影子模式（4 类卖压候选）+ walletflow 记录与事件告警已上线

- 采集 4 类免费信号 + 读 `crypto-price` 的 exports 行情背景（价格 / 24h 涨跌 / **资金费率 / OI**）；
- 按 `config/rules.yaml` 算出"本会触发什么级别"的候选（P0/P1 + 方向对齐 `aligned`/`would_alert`），写入 `data/state/shadow-candidates.jsonl`；
- **归因引擎**（`scripts/attribution.py`）：把单信号 + OI/资金费率 + 资金流组合成 4 类卖压结论，每条固定 `evidence` / `counter_evidence` / `unknown` 三段，**不输出身份**；候选会带上归因摘要（`category` / `category_code` / `confidence`）；
- **OI 15m 变化**：优先用 Binance 官方 `openInterestHist`（免 key，`period=15m`），失败时用本地跨轮历史 `data/state/oi-history.json`（每轮追加 crypto-price exports 的 `oi`）兜底；两者都不可得时 `oi_change_15m_pct=null`，归因置信度自动降级（`oi_source` 记录来源）；
- **跨模块导出**：`data/exports/onchain-pressure.json`（`onchain/pressure/v1`，只增字段）；**目前尚无下游消费**（crypto-analyst 规划中）；
- **链上大额转账（walletflow，M3 附加能力）**：公共 RPC `eth_getLogs` 增量扫 ETH 主网 + BSC 的 USDT/USDC 的 Transfer，记录阈值 ETH ≥$10,000,000、BSC ≥$5,000,000（2026-11-09 由 $5M/$2M 调高），默认排除 mint/burn；游标 `data/state/walletflow-cursor.json`（`{chains: {ethereum, bsc}}`）+ ETH 单轮 ≤120 块（30 块/次）、BSC ≤900 块（300 块/次；节点报“范围过大”时自动对半拆分）；明细 `data/raw/<date>/walletflow.jsonl`；快照/exports/每日摘要带 `walletflow`（只增字段）；`walletflow.labels` 内置 23 个已验证地址（21 cex / 1 mm / 1 issuer）（官方清单 + Etherscan name tag），只标方向（`cex_in`/`cex_out`/`cex_internal`），**数据记录不做地址归属/实体识别**；RPC 失败只记 `walletflow.error`、不阻塞、不改 rc；
- **专用 Bot**：告警/摘要通过 `TELEGRAM_ONCHAIN_BOT_TOKEN`/`TELEGRAM_ONCHAIN_CHAT_ID` 的 独立 Bot 发送（未填回退共享 Bot）。

- **逐笔告警默认关闭（B 模式：仅单向未知 ≥$500M 或 1h 交易所净流入异常才即时提醒，≤2 条/天）**（`alerts.enabled: false`，2026-11-10 用户反馈单笔信息量低/打扰多）；链上信息改由**每日 21:00 摘要**输出分析（净流/噪声过滤/单向大额）。需要恢复逐笔告警时改 true，以下阈值/限额逻辑仍保留：

- **事件驱动告警（`scripts/walletflow_alerts.py`，已实现，默认关闭）**：每轮新增的大额转账 ≥`alerts.min_usd=$10M` 且为 P0 才发一条聚合 Telegram（`only_p0=true`，P1 只落盘/进摘要）；**P0** = 单笔 ≥$50M 或转入交易所且 ≥$25M，**P1** = 其余。**`quiet_hours` 默认空 `""` = 全天即时发送**，P0/P1 都立即发（送达即时，由接收端自行处理）；该配置仍可临时配回时段，非空时仅 P1 抑制、P0 穿透。常规 ≤10 条消息/天（P1 不推送；特殊期解除；**巨鲸保护**：≥$100M 或入所 ≥$50M 额外最多 5 条/天，标「🐋 巨鲸」），当天大额笔数 >20 后按 `min(30, 6+(笔数−20)//2)` 扩容（仅 only_p0=false），P0 常规受 daily_cap=10 限制；**特殊期解除日限额**（`special.*` 命中任一：当日 P0 笔数 ≥600、|BTC 24h 涨跌幅| ≥6%、CEX 24h 净流出 ≥150bps、稳定币 24h 净赎回 ≥15 亿美元，或 `manual=true` 手工强制）→ 解除常规 10 条但仍有特殊期硬上限 30 条/天，仍保留去重与单轮 5 笔、消息标注「⚠ 特殊期：日限额已解除」。同一 tx 多腿只保留 USD 最大一腿，同 `from→to` 同币种合并为“（N 笔）”，单条最多 5 笔；发送成功才计入 `data/state/walletflow-alerts.json`，失败进 `pending`（≤20）下轮重试；`--dry-run` 只预览不写状态；**流向与方向合并一行：`流向：<实体> → <实体>（cex_in/cex_out/cex_internal 或 unknown · 无法判断买卖方向）`（实体=交易所/做市商/发行方或 Etherscan 标签，不清楚写“未知”）**；**方向 `unknown` 时允许带“推测：疑似与 X 关联/疑似高频机构地址”的低置信备注（明确标注，不做实体断言）**；**每条聚合行 2026-11-10 起补全：代币数量（千分位）/区块/from→to 缩写/地址标签/背景/交易链接**；地址标签渲染时优先内置 `walletflow.labels`，Etherscan 地址页公开 name tag 作为补充（`alerts.enrich`，仅 ETH、超时 6 秒、缓存 `data/state/address-tags.json` 30 天、失败忽略；`--dry-run` 预览不联网）；BSC 无免费标签源，Etherscan key 未使用；
- **每日影跑摘要**：今日候选数（P0/P1/would_alert）、归因类别分布、链上大额转账、**今日链上告警条数/抑制数**、资金面、校准 Day N/14；到点后首个 `collect.sh` 轮次发送，当日去重（`data/state/digest.json`）；**它本身不是告警通道**。
- **仍属 M3（4 类卖压候选）**：Telegram 推送/卡片、去重冷却/静默/日限额、feedback 与校准回算；影跑 2 周、精确率 ≥60% 且日均候选 ≤3 条，才开（见需求 §6/§11）。

## 归因类别（只回答类别，不回答谁）

| category_code | 结论 | 判定（从高到低优先级，先匹配先生效） | 置信度 |
|---|---|---|---|
| `long_liquidation` | 多头被强平（被动卖盘） | OKX 15m 清算 ≥ P1 且多头占比 ≥ 0.6 | OI 15m ≤ −2% → `high`；OI > 0 → `low`；其余/未知 → `medium` |
| `short_add` | 空头加仓 / 主动砸盘 | 5m 主动卖占比 ≥ 58% 且 OI 未显著下降（历史不足也满足） | OI ≥ 2% 且资金费率 ≤ 0 → `high`；其余 → `medium` |
| `capital_exit` | 增量资金撤离（卖压背景） | CEX 24h 净流 ≤ −50bps TVL 且稳定币 24h 净赎回 ≤ −5 亿美元 | `medium` |
| `deleverage` | 去杠杆（非新增抛售，反证） | OI 15m ≤ −2%，且没有强平 / 主动卖 / 资金撤离的证据 | `medium` |

价格未下跌（24h 方向非 `down` 且涨跌幅 > −0.5%）时不输出资产级归因；市场级另有一条"资金撤离"结论（BTC 方向 `down` + 同样的两条资金条件）。

## 数据源（全部免 key、$0）

| 信号 | 数据源 | 口径限制 |
|---|---|---|
| 逐笔主动卖压 | Binance 现货 `aggTrades`（`isBuyerMaker` → taker 方向） | 单所；权重 4/页，limit≤1000 |
| 交易所储备净流 | DefiLlama `api.llama.fi/cexs` | **全币种聚合 USD**，非分币种 |
| 清算 | OKX `liquidation-orders` REST | **仅 OKX 口径**（Binance 只有 WS，标准库不做） |
| 稳定币净增发 | DefiLlama `stablecoins.llama.fi` | 跨链 mint/burn 噪音 |
| 行情背景 + OI/资金费率 | `crypto-price/data/exports/latest.json`（只读） | 缺失/过期自动降级；OI 为 Binance USD-M 单所 |
| 链上大额转账 + 告警 | 公共 RPC `eth_getLogs`（`ethereum-rpc.publicnode.com` / `bsc-rpc.publicnode.com`，免 key）+ Etherscan 地址页（仅告警补标签，页面抓取、免 key） | ETH + BSC 的 USDT/USDC Transfer；记录阈值 ETH ≥$10M / BSC ≥$5M；默认排除 mint/burn；**不识别地址归属**；约 20–40 次 RPC/轮；每轮新增 ≥$10M 发聚合 Telegram（仅 P0 推送、全天即时（P1 不推送，仍落盘/进摘要）、常规日限额、特殊期解除限额）；消息含数量/区块/标签/背景/交易链接；标签来自内置清单或 Etherscan 地址页公开 name tag（仅 ETH、缓存 30 天） |

## 快速开始

```bash
cd ~/vscode/crypto-research/crypto-onchain

./scripts/run.sh                     # 跑一轮：采集 + 归因 + 写影子快照/候选/导出 + walletflow 事件告警
./scripts/run.sh --dry-run --json    # 只预览（含告警预览 alerts_preview），不写任何文件（含 OI 历史、告警状态与 exports）
./scripts/run.sh --status            # 看最近快照/今日候选数量/最近归因（不联网）
./scripts/run.sh --fixtures tests/fixtures --data-dir /tmp/onchain --dry-run --json  # 离线回放（不发送告警）

./scripts/verify.sh                  # ruff + libs 单测 + 本模块 150 项单测 + fixtures 端到端
./scripts/verify.sh --with-network   # 额外跑一次真实采集（只写临时目录）
```

调度（每 5 分钟一轮，`config/app.yaml` 的 `poll_seconds`；`collect.sh` 内含单实例锁、healthcheck 与每日 prune；该任务同时负责 walletflow 事件告警）：

```bash
make launchd module=crypto-onchain action=install
make launchd module=crypto-onchain action=status
```

## 配置（改这里 = 改注意力边界）

| 文件 | 作用 |
|---|---|
| [config/watchlist.yaml](config/watchlist.yaml) | BTC/ETH/BNB 的交易对、OKX 合约面值（ctVal）、关注哪些 CEX |
| [config/rules.yaml](config/rules.yaml) | 4 类信号的阈值/分级、方向门控、**归因参数（`attribution.*`）**、影子期校准目标、M3 送达策略骨架 |
| [config/app.yaml](config/app.yaml) | 数据源端点、回补窗口/翻页上限、影子产物路径、**exports 契约（`exports.file`/`exports.schema`）**、轮询周期、**链上大额转账（`walletflow.*`：RPC/阈值/窗口/币种/内置交易所标签）**、**事件驱动告警（`walletflow.alerts.*`：开关/阈值/P0 分级/日限额/扩容/全天即时/特殊期/推测/`enrich` 标签补全）**、**每日摘要（`digest.enabled/time/start_date/calibration_days`）** |

## 产物（`data/`，gitignored）

```
data/state/shadow-latest.json        # 每轮全量快照：资产指标 + 归因（attribution）+ 候选 + 源健康
data/state/shadow-candidates.jsonl   # 每条候选一行（severity / aligned / note / extra / attribution）
data/state/oi-history.json           # 跨轮 OI 历史 {asset: [[ts, oi], ...]}（24h / 400 点上限）
data/state/walletflow-cursor.json    # 链上大额转账游标 {chains: {ethereum, bsc}, updated_at}
data/state/walletflow-alerts.json    # 链上告警状态 {date, messages, sent, suppressed, pending, last_sent_at}
data/state/address-tags.json         # 告警地址标签缓存 {地址: {tag, ts}}（TTL 30 天；仅真实发送时写）
data/exports/onchain-pressure.json   # 跨模块契约 onchain/pressure/v1（每轮非 dry-run 覆盖写）
data/logs/onchain.log                # 每轮一行汇总（含 attributions=N）
data/raw/<date>/attributions.jsonl   # 当日归因记录（每日摘要统计用）
data/raw/<date>/walletflow.jsonl     # 当日链上大额记录（ETH ≥$10M / BSC ≥$5M，默认排除 mint/burn）
data/state/digest.json               # 摘要发送状态（last_sent_date，当日去重）
```

## 口径与红线（改代码前先看）

- 交易所净流是 **全币种聚合 USD**，只能写"交易所储备在流出"，不能写"BTC 被转进交易所"；
- 清算只用 OKX，卡片/日志必须标注"仅 OKX 口径"；OI 只来自 Binance USD-M 单所，跨所杠杆变化不可见；
- 方向不一致时保留 `severity` 供影子评估，但 `would_alert=false`（不打扰）；
- **4 类卖压候选禁止身份推断**：归因输出固定"无法判断卖方身份"；`tests/test_attribution.py` 与 `tests/test_run_fixtures.py` 有身份词黑名单断言，别绕过；
- **链上大额转账只报"发生了什么"**（金额/币种/时间/地址），**数据记录不判断地址归属或实体**；方向仅来自内置验证标签（`walletflow.labels`：官方清单 + Etherscan name tag，23 个），命中才给 `cex_in`/`cex_out`/`cex_internal`，否则 `unknown`；BSC 无免费标签源，方向基本 `unknown`；Etherscan key 未被使用；**告警文案的地址标签**渲染时优先内置清单，另由 `alerts.enrich` 抓 Etherscan 地址页公开 name tag 作补充（仅 ETH、缓存 30 天、失败忽略），只作展示、不写入数据记录、不做实体断言；
- **告警消息字段（2026-11-10 增强）**：标题（图标+P0/P1+币种+金额+链）、代币数量（千分位）+区块号、from→to（缩写）、地址标签（内置优先 → Etherscan 页面 name tag → “双方均无公开标签”）、方向（含“推测”备注）、背景（今日第 N 笔/累计笔数与占比、近 7 天 from/to 频次、同 tx 多腿提示）、交易链接（ETH → `etherscan.io/tx/<hash>`、BSC → `bscscan.com/tx/<hash>`）；`--dry-run` 只预览、**不联网补标签**；
- **允许“推测”但必须标注**：`walletflow_alerts.py` 可在 `unknown` 时加“（推测：疑似与 Binance 14 关联的地址）/疑似做市商/高频机构地址”，依据 7 天历史同现 ≥3 次或出现 ≥5 次；猜不到不写，**永远带“推测/疑似”、只作线索，绝不当结论或实体断言，不写入数据字段**；
- **告警送达**：`quiet_hours` 默认空 = 全天即时（P0/P1 都发，由接收端自行处理；可配置，非空时仅 P1 抑制、P0 穿透）；常规 ≤10 条消息/天（P1 不推送；特殊期解除）、扩容上限 30；**特殊期解除日限额**（当日 P0 ≥600 笔 / |BTC 24h 涨跌幅| ≥6% / CEX 24h 净流出 ≥150bps / 稳定币 24h 净赎回 ≥15 亿美元，或 `manual=true`）→ 常规 10 条解除但特殊期硬上限 30 条/天，仍保留 `(tx, log_index)` 去重、同 tx 多腿只留最大一腿与单轮 ≤5 笔，消息标注「⚠ 特殊期：日限额已解除」；`--dry-run` 不发送不写状态；发送成功才计入 `data/state/walletflow-alerts.json`；
- 阈值/路由留在本模块 `config/`，通用解析器在 `libs/crypto_core/market/`，模块内不复制解析逻辑；
- exports 契约**只增字段**，旧消费者可忽略新字段；目前无下游消费，不要假设有人已接入。
