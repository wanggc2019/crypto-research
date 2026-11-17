# 需求：crypto-futures M2 —— 资金费率 / OI 告警从 crypto-price 迁入

> 状态：**待评审（2026-11-17）**，评审通过后再写代码｜上游：[M1 需求](requirement.md) §8 · [crypto-price 需求](../crypto-price/requirement.md)
> 关联：[消息发送规范](../notify-policy.md) · [ADR 0005 领域命名](../adr/0005-domain-naming.md)

## 1. 要解决什么问题（一句话）

让 **crypto-price 只负责现货价格**，所有永续合约类告警统一由 **crypto-futures** 发出、用合约卡片样式。

现状的问题：

1. **职责混在一起**：crypto-price 的价格来自三所现货，但它同时从 Binance `fapi` 取资金费率和 OI 并据此告警，使得「price 模块到底是不是只看现货」说不清（本需求的起因）；
2. **卡片误导**（M1 需求 §5.1 已列出）：资金费率告警标题也写「价格异动」；只有 Binance 单源，却显示「多源核验 3/3」；看不到 OI、多空比等背景；
3. 合约数据由两个模块各自采集，口径不一致（crypto-price 1 分钟一轮的实时 OI；crypto-futures 5 分钟一轮的 OI 历史）。

**为什么不改名为 crypto-spot**：`price` 是合规的领域词（ADR 0005）。M2+M3 之后 crypto-price 只剩现货价格，名实已经一致；改名要改 launchd Label、迁移 `data/` 状态、跨模块导出路径以及模块外约 40 个文件，收益只是名字更精确。M3 之后若仍想改，另开 ADR。

## 2. 现状数据（决定迁移风险）

| 项 | 数据 | 来源 |
|---|---|---|
| crypto-price `funding` 告警 | 2026-11-08 至 11-17 **0 次** | 主仓库 `crypto-price/data/state/alerts-*.json` |
| crypto-price `open_interest` 告警 | 同期 **0 次** | 同上 |
| 资金费率结算值，阈值 \|rate\| ≥ 0.10% | 近 90 天（每币 270 次结算）**0 次**；最大值 BTC 0.0100% / ETH 0.0100% / BNB 0.0324% | `/fapi/v1/fundingRate` |
| OI 15m 变化 ≥ 5% 且价格同向 ≥ 0.5% | 近 29.6 天 **BNB 1 次**，BTC / ETH 0 次；无 ≥ 10% 的情况 | crypto-futures 30 天 5m 缓存（价格用 `oi_usd / oi` 近似） |
| crypto-futures M1 | 2026-11-17 19:28 起 launchd 运行，rc=0 | `crypto-futures/data/logs/collect.log` |

结论：这两条规则是**极少触发的尾部告警**，迁移不会显著改变推送量，迁移期间漏报的概率也很低。正因为太少触发，「两边并跑对比」得不到有意义的样本，所以采用一次切换（§5）。

## 3. 边界：做什么 / 不做什么

### 做

1. **crypto-futures 新增两条规则**，阈值与语义沿用 crypto-price 现值（不借迁移调阈值）：

   | 规则 | 条件 | 级别 | 冷却 |
   |---|---|---|---|
   | `funding_extreme` | 当前资金费率（`premiumIndex.lastFundingRate`）\|rate\| ≥ 0.10% | P1；≥ 0.20% 为 P0 | 2h（同 crypto-price） |
   | `oi_spike` | 15 分钟 OI 变化 \|chg\| ≥ 5%，且同期合约标记价同向变化 ≥ 0.5% | P1；≥ 10% 为 P0 | 1h（同 crypto-price） |

   - 方向：资金费率正 = 多头付费（`long`），负 = 空头付费（`short`）；OI 增 + 价涨 = `up`，OI 减 + 价跌 = `down`；
   - 加剧、同轮合并一张卡、P0 每资产每本地日 ≤ 2 条，全部沿用 M1 的 `governance`（P0 限额与 M1 规则**共用**）。
2. **OI 用实时值**（待确认 D1）：每轮额外请求 `/fapi/v1/openInterest`（每资产 1 次），连同已在请求的 `premiumIndex` 标记价存入本地 1 天序列，15 分钟变化按时间戳就近取 ≥ 15 分钟前的点；**不**用 `openInterestHist`（最多约 12 分钟延迟，尾部告警会晚到 15 分钟以上）。
3. **卡片**：沿用 M1 卡片结构（结论 → 指标 → 背景三项 → 解读 → 历史参照 → 口径），标题分别为「资金费率极端」「持仓量急变」；口径行固定写「Binance USD-M 单所」，不出现「多源核验」「价格异动」。资金费率卡片要给出折算年化（rate × 3 × 365）。
4. **crypto-price 关闭这两条规则**：`config/rules.yaml` 里 `funding.enabled` / `open_interest.enabled` 改为 `false`；**继续采集并导出** `funding_rate` / `funding_ts` / `oi` / `oi_ts`（crypto-onchain 的归因引擎在读），导出契约不变。
5. 回测：`scripts/backtest.py` 支持两条新规则，用近 30 天数据（资金费率用结算记录）输出触发次数，作为验收依据。
6. 文档同 PR 更新：crypto-futures 的 README / design / technical，crypto-price 的 requirement / README（规则清单删去这两条，注明已迁到 crypto-futures），HANDOFF。

### 不做

- 不删 crypto-price 的衍生品采集代码和规则代码（只关配置，回退 = 改回 `true`）；删除放 M3；
- 不改 crypto-onchain / crypto-analyst 的读取路径（M3）；
- 不改模块名、launchd Label、`data/` 目录；
- 不新增其他所数据，不调阈值。

## 4. 验收标准（可测量）

1. 单测：两条规则的触发 / 不触发边界（含正负对称、价格反向不触发、缺数据不触发）、P0/P1 分级、冷却与加剧、与 M1 规则同轮合并为一张卡、P0 日限额共用；
2. 卡片：两条规则的 fixtures 渲染包含 M1 §5.2 的全部必有区块、长度 ≤ 600 字；断言不出现「多源核验」「价格异动」「开多」「抄底」「减仓」；
3. `backtest.py` 在 §2 同一份 30 天数据上：`funding_extreme` 0 次、`oi_spike` 与 §2 的 1 次同量级（允许因标记价口径差 ±1）；
4. crypto-price：`--fixtures` 回放（含 `snapshot_shock.json`）不再产出 `funding` / `open_interest` 告警，其它规则输出与改动前**逐条一致**；`data/exports/latest.json` 字段与改动前一致；
5. 请求量：crypto-futures 常规 ≤ 18 次 / 5 分钟（M1 为 15，+3 次实时 OI）；M1 验收 5 同步改为 18；
6. `make gate` 全绿；上线后 24 小时 `collect.log` 每轮 rc=0；台账中合约类告警只出现 `module=crypto-futures`。

## 5. 上线与回退

- **一次切换**：同一个 PR 内既新增 crypto-futures 规则，又关闭 crypto-price 规则；合并后两个模块下一轮自动生效（launchd 跑工作区代码，无需重装）。
- 不做影子并跑：§2 显示 90 天内几乎无样本，并跑对比没有意义；回测 + 单测 + 回放代替。
- **回退**：`git revert` 该 squash commit；或只把 crypto-price 两个 `enabled` 改回 `true`（双发总比漏发好）。

## 6. 后续（M3，另开需求）

1. crypto-onchain、crypto-analyst 改读 `crypto-futures/data/exports/latest.json` 里的 `funding_rate` / `oi_usd`；
2. 之后 crypto-price 删除 `fapi` 采集、衍生品规则代码和这四个导出字段（导出契约升版本），成为纯现货模块；
3. 届时再评估是否改名（默认不改）。

## 7. 需要评审确认的决策

| # | 决策 | 推荐 | 备选 |
|---|---|---|---|
| D1 | OI 数据口径 | 每轮加 3 次实时 `/fapi/v1/openInterest`，告警延迟 ≤ 5 分钟 | 只用已有的 `openInterestHist`：不加请求，但告警最多晚约 17 分钟 |
| D2 | 阈值 | 沿用 crypto-price 现值（资金费率 0.10% / 0.20%，OI 5% / 10% + 价格 0.5%） | 借机下调资金费率阈值（如 0.05%）：90 天回测仍为 0 次，意义不大 |
| D3 | 切换方式 | 同一 PR 一次切换（§5） | 先并跑 7 天再关 crypto-price：会有双发风险，且几乎没有样本 |
| D4 | P0 日限额 | 与 M1 规则共用（每资产每日 ≤ 2） | 新规则单独计数：极端行情下 P0 可能多发 |
