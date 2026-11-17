# 需求：crypto-futures M2 —— 资金费率 / OI 告警从 crypto-price 迁入

> 状态：**已实现（2026-11-17）**；评审通过（D1–D4 均按推荐）。验收证据见 §8｜上游：[M1 需求](requirement.md) §8 · [crypto-price 需求](../crypto-price/requirement.md)
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
| 资金费率结算值，阈值 \|rate\| ≥ 0.10% | 近 90 天（每币 270 条结算记录，说明期间三币都是 8h 结算、未被调整）**0 次**；最大值 BTC 0.0100% / ETH 0.0100% / BNB 0.0324% | `/fapi/v1/fundingRate` |
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
   - 加剧、同轮合并一张卡、P0 每资产每本地日 ≤ 2 条，全部沿用 M1 的 `governance`（P0 限额与 M1 规则**共用**，副作用见 D4）；
   - 两条规则在 `config/rules.yaml` 各有 `enabled` 开关，可单独关闭（回退用，见 §5）。
2. **OI 用实时值**（D1）：每轮额外请求 `/fapi/v1/openInterest`（每资产 1 次），连同已在请求的 `premiumIndex` 标记价存入本地 1 天序列，实时样本按采样时刻**向下取整到 5 分钟桶**存一个点（OI、标记价、资金费率同一桶、同一时间戳），15 分钟变化以「当前桶 − 15 分钟」的桶为参考，没有则用「− 20 分钟」的桶，**OI 与标记价用同一个参考桶**（launchd 调度抖动只影响落在哪个桶，不会把 14 分 5x 秒的点排除）；**取不到（如 Mac 休眠后的空档）视为缺数据，不评估**，避免把 90 分钟的变化当成 15 分钟触发；**不**用 `openInterestHist`（最多约 12 分钟延迟，尾部告警会晚到 15 分钟以上）。
3. **卡片**：沿用 M1 卡片结构（结论 → 指标 → 背景三项 → 解读 → 历史参照 → 口径），标题分别为「资金费率极端」「持仓量急变」；口径行固定写「Binance USD-M 单所」，不出现「多源核验」「价格异动」。资金费率卡片要给出折算年化 = rate × (24 / 结算间隔小时) × 365；结算间隔取 `/fapi/v1/fundingInfo` 的 `fundingIntervalHours`（每天请求 1 次、缓存 24h；**本轮资金费率越过 P1 阈值时当轮强制刷新一次**，避免 Binance 临时缩短结算间隔时用旧值；没有该合约记录即为默认 8h；请求失败且无缓存时按 8h 并在卡片注明「按 8h 折算」）。
4. **crypto-price 关闭这两条规则**：`config/rules.yaml` 里 `funding.enabled` / `open_interest.enabled` 改为 `false`；**继续采集并导出** `funding_rate` / `funding_ts` / `oi` / `oi_ts`（crypto-onchain 的归因引擎在读），导出契约不变。
5. 回测：`scripts/backtest.py` 支持两条新规则，用近 30 天数据（资金费率用结算记录）输出触发次数，作为验收依据。
6. 文档同 PR 更新：crypto-futures 的 README / design / technical，crypto-price 的 requirement / README（规则清单删去这两条，注明已迁到 crypto-futures），HANDOFF。

### 不做

- 不删 crypto-price 的衍生品采集代码和规则代码（只关配置，回退 = 改回 `true`）；删除放 M3；
- 不改 crypto-onchain / crypto-analyst 的读取路径（M3）；
- 不改模块名、launchd Label、`data/` 目录；
- 不新增其他所数据，不调阈值。

## 4. 验收标准（可测量）

1. 单测：两条规则的触发 / 不触发边界（含正负对称、价格反向不触发、缺数据不触发、**参考桶只有 −25 分钟及更早时不触发**、−15 分钟桶缺失时用 −20 分钟桶、`enabled: false` 不触发、结算间隔非 8h 时年化正确、`fundingInfo` 失败时卡片注明「按 8h 折算」）、P0/P1 分级、冷却与加剧、与 M1 规则同轮合并为一张卡、P0 日限额共用；
2. 卡片：两条规则的 fixtures 渲染包含 M1 §5.2 的全部必有区块、长度 ≤ 600 字；断言不出现「多源核验」「价格异动」「开多」「抄底」「减仓」；
3. `backtest.py` 在 §2 同一份 30 天数据上：`funding_extreme` 0 次（结算值口径）、`oi_spike` ≥ 1 次（含 §2 的 BNB 那次）；回测用 5m `openInterestHist`，覆盖不到线上实时 OI 路径，所以另用**线上路径的 fixture**（实时 OI + 标记价序列）断言能触发 P1 与 P0；
4. crypto-price：`--fixtures` 回放（含 `snapshot_shock.json`）不再产出 `funding` / `open_interest` 告警，其它规则的**规则级告警列表**（`rules.evaluate` 输出，去掉这两条后）与改动前逐条一致；最终卡片的合并/限额结果允许因少了这两条而变化；`data/exports/latest.json` 字段与改动前一致；
5. 请求量：crypto-futures 常规 ≤ 18 次 / 5 分钟（M1 为 15，+3 次实时 OI），另加每天 1 次 `fundingInfo`；M1 验收 5 同步改为 18；
6. `make gate` 全绿；上线后 24 小时 `collect.log` 每轮 rc=0；台账中合约类告警只出现 `module=crypto-futures`。

## 5. 上线与回退

- **一次切换**：同一个 PR 内既新增 crypto-futures 规则，又关闭 crypto-price 规则；合并后两个模块下一轮自动生效（launchd 跑工作区代码，无需重装）。
- 不做影子并跑：§2 显示 90 天内几乎无样本，并跑对比没有意义；回测 + 单测 + 回放代替。
- **回退**：
  - 整体：`git revert` 该 squash commit；
  - 新规则误报 / 刷屏：把 crypto-futures `config/rules.yaml` 里对应规则 `enabled` 改为 `false`；需要时同时把 crypto-price 的对应 `enabled` 改回 `true`，恢复旧告警（双发总比漏发好）。

### 已知风险

- **资金费率口径差**：§2 的「90 天 0 次」用的是结算值，线上规则用 `premiumIndex` 实时值（每 5 分钟一次）；结算前实时费率可能短暂越过阈值又回落，所以线上触发会比回测多。只有实时值能说明真实频率：上线首周看台账，超过 1 条/天再评估阈值或加「连续 N 轮」确认。

## 6. 后续（M3，另开需求）

1. crypto-onchain、crypto-analyst 改读 `crypto-futures/data/exports/latest.json` 里的 `funding_rate` / `oi_usd`；
2. 之后 crypto-price 删除 `fapi` 采集、衍生品规则代码和这四个导出字段（导出契约升版本），成为纯现货模块；
3. 届时再评估是否改名（默认不改）。

## 7. 评审决策（2026-11-17：全部采用推荐项）

| # | 决策 | 推荐 | 备选 |
|---|---|---|---|
| D1 | OI 数据口径 | 每轮加 3 次实时 `/fapi/v1/openInterest`，告警延迟 ≤ 5 分钟 | 只用已有的 `openInterestHist`：不加请求，但告警最多晚约 17 分钟 |
| D2 | 阈值 | 沿用 crypto-price 现值（资金费率 0.10% / 0.20%，OI 5% / 10% + 价格 0.5%） | 借机下调资金费率阈值（如 0.05%）：90 天回测仍为 0 次，意义不大 |
| D3 | 切换方式 | 同一 PR 一次切换（§5） | 先并跑 7 天再关 crypto-price：会有双发风险，且几乎没有样本 |
| D4 | P0 日限额 | 与 M1 规则共用（每资产每日 ≤ 2）。副作用：资金费率极端常持续数小时，可能先用掉当日 P0 额度，挤掉同日更重要的 M1 P0；90 天结算值从未 ≥ 0.20%，但实时值口径会更多（§5 已知风险），所以首周观察项明确盯「funding P0 是否挤占 M1 P0 额度」，发生即改单独计数 | 新规则单独计数：极端行情下 P0 可能多发 |

## 8. 实现与验收记录（2026-11-17）

| 验收 | 结果 |
|---|---|
| 1 单测 | `crypto-futures/tests/test_m2.py` 29 项通过；crypto-price `test_rules.py` 新增「线上配置已关闭、不再产出」2 项 |
| 2 卡片 | 两条规则的卡片包含全部必有区块、≤ 600 字、无禁用词（`TestCards`） |
| 3 回测 | `backtest.py --cache <线上 30 天缓存> --data <结算记录>`：28.5 天 16 张卡（0.56 条/天），`oi_spike` 1 次、`funding_extreme` 0 次 |
| 4 crypto-price 回放 | `snapshot_normal` / `snapshot_shock` 两份 fixture 规则级告警列表前后逐条一致；导出字段未改 |
| 5 请求量 | 联网 `run.py --once --dry-run --json`（读主仓库数据目录）：19 次 = 15 + 3 次实时 OI + 当日首次 `fundingInfo`，0 错误 |
| 6 上线 | 合并后下一轮自动生效；24 小时 `collect.log` 与台账待观察 |
