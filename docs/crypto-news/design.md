# 设计：crypto-news —— 行业信息情报采集/评分/告警

> 需求与验收见 [requirement.md](requirement.md)；业务架构图见 [architecture.md](architecture.md)。
> 完整方案（v0.6，含信源实测/成本/路线）：[crypto-intel-plan.md](../crypto-intel-plan.md)

## 1. 总体方案：五段式流水线

```
[采集] → [归一化] → [过滤/聚类] → [评分] → [路由/呈现] → [反馈校准]
 44 源    统一结构    去噪去重      0–100 分   P0/P1/P2/归档     周度调参
```

- **采集**：RSS / 交易所公告 JSON API / 网页 diff / `r.jina.ai` 代理（X 高管、Farside）。
- **归一化**：`{source_id, source_name, tier, title, url, body, published_at, fetched_at}` 落 `data/raw/<date>/items.jsonl`。
- **过滤/聚类**：实体词典 + watchlist 匹配 → 标题/正文相似度聚类成事件簇；跨轮次按事件标题指纹去重。
- **评分**：`Score = clamp(0,100, (0.20·S + 0.30·T + 0.35·M + 0.15·C) × D)`。
- **路由**：P0/P1 全天即时 / P2 日报归档；24h 同事件去重（2026-11-10 起取消夜间静默，`quiet_hours` 可配回旧行为）。

## 2. 信源分层（T0–T3）

| 层级 | 内容 | 处理 |
|---|---|---|
| T0 | 交易所公告、监管官网（SEC/CFTC/Fed）、项目官方、DefiLlama/Farside 等一手数据 | 可单独定性；单源可 P0 |
| T1 | The Block / CoinDesk / Cointelegraph / Blockworks / DL News、吴说 / PANews 等 | 需交叉确认 |
| T2 | 头部交易所创始人/CEO **本人原创**（X，经 r.jina.ai） | 按接近 T0 权重，路由到 P1/P2 |
| T3 | Google News / 聚合/搬运 | **只做线索**；不计入独立源，不能单独触发 P0 |

## 2.2 美股 / 宏观（一期，新闻层）

- 信源：CNBC 头条、MarketWatch 头条（T1）+ Google News「美股 × 加密」检索（T3）。
- 过滤：只保留**市场级事件**（指数大跌/抛售、Fed/CPI/关税、加密相关股票/ETF），单只个股日常新闻不入库。
- 分级：无 BTC/ETH/BNB 关联最高 **P2**；有关联最高 **P1**（永不 P0）；宏观基准分 85、衰减 12 小时。
- 英文标题在卡片/日报自动附「🈯 中文解读」（规则版，零依赖），如：
  `Bitcoin loses 50-week moving average as 24-hour crypto long liquidations top $1 billion`
  → `BTC 跌破 50 周均线；24 小时加密多头清算超 10 亿美元`

## 2.1 组织动向线索（B 方案）

- 只收三类：**裁员/重组**、**高管/核心团队离职**、**可核实身份的举报**；不收匿名"内幕"、氛围/吐槽。
- 信源：
  - **官方 WARN 备案**（加州 EDD xlsx、纽约州 DOL 页面）→ tier **T0**，官方一手；只保留最近 30 天、仅交易所雇主相关备案；
  - **Google News 关键词**（裁员/离职，英文/中文）→ tier **T3**，只作线索；
  - **Reddit 社区**（经 Google News 索引，免登录/免抓取）→ tier **T3**，只作线索。
- 分级：类别 `industry_labor`（基准分 40）→ **已确认最高 P1，未确认 P2，永不 P0**；
  T3 硬事件也不靠分数升 P1（聚合站只作线索）。
- 展示：P2 进日报；P1 卡片类别显示「组织动向」。

## 2.3 英文标题 AI 中文解读

- 卡片/日报的基础解读是规则版（`libs/text.cn_summary`）；**Telegram 推送前**会对英文标题再走一次 AI 翻译：
  `crypto-news/scripts/notify.py` → `tools/translate_titles.py`（DeepSeek `deepseek-chat`，批量一次调用）。
- 译名统一：Bitcoin/BTC、Ethereum/ETH、Binance/币安、Nvidia/英伟达等；理解后意译，不逐词直译。
- 结果缓存 `data/state/translate-cache.json`（按标题哈希，最多 500 条），同一标题只翻译一次；
- 失败/超时/无 key 时自动回退规则版，不阻塞推送；`CRYPTO_TRANSLATE=0` 可关闭。
- 成本：每批英文标题约几百 token，正常日 0–3 条。

## 3. 评分与分级

- **S 信源分**：T0=100 / T1=80 / T2=85 / T3=60 左右（实现见 `score.py`）。
- **T 事件基准**：协议安全 95、交易所 90、稳定币脱锚 85、监管 75、宏观 70、解锁 55、资金流 50、技术升级 45、治理 40、市场结构 45。
- **M 量级**：金额（`25·log10(USD/1e4)`）、脱锚 bps、监管层级；多维度有加成。
- **C 确认度**：`100×(1−0.5^(k−1))`，k=独立源数；T0 单源直接 100。
- **D 时效衰减**：`2^(−Δt/h)`，安全/监管/交易所 h=24，行情/宏观 h=6。
- **分级**：P0 ≥78 或硬条件；P1 58–77；P2 35–57；<35 归档。

### 硬条件（必须过三道门）

命中任一硬条件（大额被盗 >$10M、交易所暂停提币、稳定币脱锚、ETF 决议、头部交易所被监管、FOMC 意外、清算瀑布 >$5亿、高管法律事件）后：

1. **已确认** = T0 单源，或 ≥2 个**非 T3** 独立源；
2. **时效** ≤48h（48–96h 降 P1，更旧只归档）；
3. **衰减** D ≥ 0.1。

区间外/未确认的事件：单一可信源（T0/T1/T2）的近期事件 → P1 标「未确认」；T3 单源 → P2/归档。
阈值在 `config/thresholds.yaml` 的 `scoring.hard`。

## 4. 摘要与展示

- 精选/日报类标题（周精选/日报/Top10…）自动拆成**编号要点**（标题分句 + 正文编号条目，按重要性排序，最多 5 条）。
- Telegram 卡片中文：`P0 · 协议安全` + 标题 + 要点 + 标的/来源（已确认/未确认）+ 影响 + 动作参考 + 触发条件 + 评分/时间 + 「查看原文」链接。

## 5. 数据与状态

| 路径 | 内容 | 保留 |
|---|---|---|
| `data/raw/<date>/items.jsonl` | 原始条目（可回放） | 90 天 |
| `data/state/<source>.json` | 每源游标/已见（有界：3000→1000） | 不删 |
| `data/state/alerts-<date>.json` | 当日 P0/P1（含评分明细/确认度字段） | 180 天 |
| `data/state/notified.json` | 推送去重（按天） | 不删 |
| `data/reports/<date>.md` | 本地日报 | 365 天 |
| `data/exports/` | 跨模块只读契约（预留） | 7 天 |
| `data/logs/` | collect/launchd/healthcheck 日志 | 30 天 + 单文件 2MB |

## 6. 配置

| 文件 | 作用 |
|---|---|
| `config/watchlist.yaml` | 关注标的（BTC/ETH/BNB） |
| `config/sources.yaml` | 44 个 verified 信源（URL/类型/轮次/tier；含组织动向线索 6 个：Google News 裁员关键词 2 + WARN 备案 2 + Reddit 社区（经 Google 索引）2） |
| `config/people.yaml` | 高管白名单与分级路由 |
| `config/entities.yaml` | 实体词典 / 事件关键词 |
| `config/rules.yaml` | 防噪/防骗/去重规则 |
| `config/thresholds.yaml` | 评分权重 / 分级阈值 / 衰减 / 硬条件门槛 |
| `config/notify.yaml` | Telegram 送达格式与限速 |

## 7. 测试与运维

- 52 项离线单测（配置/采集解析/聚类/评分/分级/卡片/去重），`make verify` + CI 全绿。
- 端到端：对当日 raw 重跑 `score.py`（不联网也可验证）。
- 调度：launchd 600s → `collect.sh`（锁）→ fetch → score → notify；每轮末尾内嵌 healthcheck（自愈/诊断/AI 按需）。
- 失败模式：单源失败继续（rc=1）、全源失败 rc=3；锁残留 30 分钟后 healthcheck 自动清；凭据错误 rc=2。
