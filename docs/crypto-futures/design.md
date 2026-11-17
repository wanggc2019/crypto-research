# 设计：crypto-futures —— 合约持仓结构监控与告警

> 状态：M1 已实现（2026-11-17）｜需求见 [requirement.md](requirement.md)｜技术架构见 [technical.md](technical.md)

## 1. 数据源

全部为 Binance USD-M 公开接口（免 key），`period=5m`：

| 代号 | 接口 | 取值 |
|---|---|---|
| `G` | `/futures/data/globalLongShortAccountRatio` | `longShortRatio`、`longAccount`（做多账户占比） |
| `T` | `/futures/data/topLongShortPositionRatio` | `longShortRatio`、`longAccount`（大户多仓占比） |
| `K` | `/futures/data/takerlongshortRatio` | `buyVol`、`sellVol` |
| `OI` | `/futures/data/openInterestHist` | `sumOpenInterest`、`sumOpenInterestValue` |
| 资金费率 | `/fapi/v1/premiumIndex` | `lastFundingRate`、`markPrice`（实时快照） |
| 实时 OI（M2） | `/fapi/v1/openInterest` | `openInterest`（实时快照） |
| 结算间隔（M2） | `/fapi/v1/fundingInfo` | `fundingIntervalHours`（只列出被调整过的合约，其余默认 8h） |

**RT 序列（M2）**：每轮把资金费率、标记价、实时 OI、结算间隔按采样时刻**向下取整到 5m 桶**存一个点
`(rate, mark, oi, interval_h)`。它没有历史接口、不回补，所以不在 `METRICS` 里；过期判断与其他指标相同（20 分钟）。

解析在共享库 `crypto_core.market.binance`：`parse_long_short_ratio`、`parse_taker_ratio`（本期新增），
`parse_open_interest_hist` 只增了 `oi_usd` 字段；M2 新增 `parse_funding_info`。

**只用已收盘的周期**：接口时间戳是 5m 周期的**起点**，最新那个点往往属于还没走完的周期，数值还会变，
而且不同节点返回不一致（2026-11-17 联网冒烟时发现）。所以 `ts + 5m > now` 的点一律丢弃，下一轮再取。

## 2. 每轮流程（`run.py`）

1. **补历史**：某指标本地最早点晚于「7 天前」→ 分页回补 7 天（失败后 6 小时内不重试）；最新点落后 > 60 分钟 → 回补缺口；
2. **取最近 12 个点**（每资产每接口 1 次，共 15 次），按时间戳并入本地历史，旧于 7.05 天的裁掉；
   同时每资产取 1 次实时 OI（M2，+3 次），连同资金费率写入当前 5m 桶的 `RT` 点；
   结算间隔 `fundingInfo` 每天取 1 次，**本轮任一资金费率越过 `funding_extreme` P1 阈值时当轮强制刷新**；
3. **判过期**：某指标最新点早于 `now − 20 分钟` 即过期，依赖它的规则这轮不评估；
4. **评估 + 治理**：`engine.step()`（见 §3、§4）；
5. **卡片**：先用已有触发记录算历史参照，再渲染卡片、推送（`--notify`）、把本卡写进触发记录；
6. **30 天缓存**（每天一次，失败 6 小时后重试）：重拉近 29.5 天（遇 `-1130` 改 29 天），覆盖写；**第一次成功时**用它按同一规则代码回放，把回放出的触发写进记录（`source=warmup`），历史参照从上线第一天就有样本；
7. **产物**：`exports/latest.json`、`state/last_run.json`、`raw/<日期>/*.jsonl`。

`--dry-run` 跳过第 6 步和所有写盘。

## 3. 规则（`rules.py`，线上与回测共用）

记号：`chg(X, Δ)` = X 当前点相对**恰好 Δ 之前那个点**的变化百分比（取不到为 None，不找相邻点）；
7 天窗口 = 当前点之前 `[ts − 2016×5m, ts)` 内的点，有效点不足 90% 时不评估。

| 规则 | 输入 | 条件 | 方向 | 幅度（用于加剧） |
|---|---|---|---|---|
| `crowding_build` P0 | G、OI | `chg(OI 美元价值, 4h) ≥ 5%` 且 G ≥ 7 天 p95 / ≤ p5（最近秩分位） | long / short | OI 4h 变化 |
| `smart_divergence` P1 | G、T | `chg(G, 24h) ≥ 15%` 且 `chg(T, 24h) ≤ −15%`，或都反过来 | retail_long / retail_short | 两腿绝对值较小者 |
| `retail_shift` P1 | G | `|chg(G, 1h)| ≥ 20%` | up / down | 1h 变化绝对值 |
| `taker_burst` P1 | K | 最近 3 点 Σ买/Σ卖 ≥ 3.5 或 ≤ 1/3.5，且 15 分钟量 ≥ 前 24h 滚动 15 分钟量中位数 × 3（有效窗口需过半） | buy / sell | max(R, 1/R) |
| `extreme_level` P2 | G | G ≥ 7 天最高 / ≤ 最低（严格大于/小于） | high / low | — |
| `funding_extreme` P1/P0（M2） | RT | `|rate| ≥ 0.10%` 为 P1、`≥ 0.20%` 为 P0；年化 = rate × (24 / 结算间隔小时) × 365，间隔未知按 8h 并在卡片注明 | long（正）/ short（负） | `|rate|`（%） |
| `oi_spike` P1/P0（M2） | RT | 参考桶 = −15 分钟桶，缺失则 −20 分钟桶（OI 与标记价**同一桶**），更早视为缺数据；`|chg(OI)| ≥ 5%`（P0 ≥ 10%）且标记价同向变动 ≥ 0.5% | up / down | `|chg(OI)|` |

M2 两条规则在同一数据点、同级别时排在 M1 规则之后（卡片主规则仍是 M1 的那条）。

## 4. 治理（`engine.py`）

- **按数据时间戳计时**（不是墙钟），所以线上逐轮运行和离线逐点回放结果完全一致（单测 `test_live_rounds_match_backtest` 保证）；
- **游标**：每个资产每条规则一个游标，只评估游标之后、输入都已就绪、且在最近 60 分钟内的点；评估过的点（不论命中与否）游标都会越过，之后即使缺口回补补齐了数据也不再评估——这是有意的：回补的数据通常已超过 60 分钟，补发旧告警没有意义，且这样线上与回测的评估集合一致；
- **冷却**：同规则 + 同资产 + 同方向 4h，`smart_divergence` 12h，`funding_extreme` 2h，`oi_spike` 1h；
- **加剧**：冷却期内幅度 ≥ 上次 1.5 倍且距上次 ≥ 1h，再报一次并标「加剧」；
- **同点合并**：同一资产同一个数据时间戳命中多条，只出一张卡（级别高者为主，其余列「同时出现」）。需求里写的「同轮」按「同一数据点」实现：线上各接口延迟不同，同一轮里可能评估好几个时间点，按数据点合并才能和回测一致；
- **P0 上限**：每资产每本地日 2 条（M2 两条规则与 M1 **共用**额度，D4）（按机器本地时区切日，与仓库其他模块一致；回测结果因此可能随时区略有不同，e2e 固定 `TZ=UTC`）；P2 只记 `raw/<日期>/p2.jsonl`；
- **状态裁剪**：冷却记录保留到最长冷却时长之后，P0 计数只留昨天和今天。

## 5. 卡片（`cards.py`）

区块固定顺序：标题 → 结论 → 指标 → 背景（合约价 24h · OI 4h · 资金费率）→ 同时出现 → 解读 → 历史参照 → 口径 → 页脚。

M2 卡片：口径写「Binance USD-M 单所 · 实时快照（每 5 分钟）」；卡片时间戳是当前 5m 桶，此时 G/T/OI 这个周期还没收盘，
所以背景与多空比取 20 分钟内最新的已收盘点（`indicators.latest_at`）；历史参照的触发价退回 RT 标记价。
7 天位置写「最高 / 最低 / 最高（最低）1% 以内 / N% 分位」，不出现 0% 或 100% 分位；可见文字 ≤ 600 字（超出时只截「解读」，历史参照、口径、页脚与免责声明保持完整），HTML 截断用共享的 `crypto_core.notify.templates.truncate_html`（从 crypto-price 抽出，price 行为不变）。
用词约束：账户比只说「做多的账户」；大户持仓比下降写「可能减多，也可能加空」；单测禁止「多源核验 / 开多 / 抄底 / 减仓」。
推送走 `crypto_core.notify.send_signal`（`kind=signal`，进全仓台账与预算），关闭链接预览。

## 6. 历史参照（`reference.py`）

同一规则、3 个币合并；只统计早于当前且已满 24h、已有结果的记录（近 30 天）；样本 < 3 显示「样本不足（N 次）」。
结果 = 触发后 24h 的合约价涨跌（`OI 美元价值 / OI 张数`），由本地历史或 30 天缓存补齐。

## 7. 健康检查

- 单个接口失败或过期只停用依赖它的规则，本轮照常；
- 任一指标超过「过期阈值 + 60 分钟」没有新数据（或资金费率超过 60 分钟没取到）→ `last_run.json` 写 `primary_ok=false` 和说明（哪个资产、哪个接口）；
- `tools/healthcheck.py` 读到后判 FAIL，正文直接用模块给的说明；告警由仓库级 watchdog 发送，**同一问题 60 分钟冷却**（仓库统一做法；需求里写的 6 小时冷却没有另做，以免一个模块单独一套告警节奏）。

## 8. 退出码

`0` 成功（只有 SKIP 也算）/ `1` 有真实错误（解析失败、HTTP 4xx 等）或推送失败 / `2` 凭据或配置错误 / `3` 全部请求真实失败。
限频（403/418/429）、超时、断网记为 SKIP，等下一轮。

## 9. 测试

| 文件 | 覆盖 |
|---|---|
| `tests/test_indicators.py` | 精确时间戳对齐、窗口不含当前点、最近秩分位（含小窗口）、先求和再相除、买入或卖出量为 0、合约价除零 |
| `tests/test_rules.py` | 5 条规则触发 / 不触发边界、1/3.5 对称、覆盖率不足跳过、开关 |
| `tests/test_engine.py` | 冷却（含 12h）、加剧、同点合并、P0 本地日上限、按接口过期、只回看 1 小时、P2 不出卡 |
| `tests/test_reference.py` | 排除当前与未满 24h、样本不足、去重、补结果、365 天裁剪 |
| `tests/test_cards.py` | 三个样例时点（真实数据）的区块、顺序、长度、禁用词、HTML 转义 |
| `tests/test_sources.py` | 只取已收盘周期、分页、30 天上限与 29 天回退、HTTP 错误分 skip / error |
| `tests/test_m2.py` | M2：两条规则边界（正负对称、反向 / 不足不触发、−15/−20 分钟桶、更早视为缺数据、同一参考桶、开关、结算间隔与年化）、与 M1 同点合并、共用 P0 额度、冷却、RT 过期、卡片、RT 采样与 fundingInfo 缓存 / 强制刷新 / 失败、实时 OI 失效标记、fixtures、回测推出 RT、参照价回退 |
| `tests/test_run.py` | 冷启动回补后首轮出 P0、dry-run 不落盘、线上逐轮 = 回测、推送走假发送、凭据缺失、退出码、接口失效标记 |
| `scripts/e2e.py` | 一整轮离线回放（含 30 天缓存与预热），exports 符合契约 |

fixtures：`tests/fixtures/replay/` 为 2026-10-22 至 10-30（3 币）与 11-04 至 11-05（ETH）的真实 5m 原始响应（gzip，约 400 KB）。
