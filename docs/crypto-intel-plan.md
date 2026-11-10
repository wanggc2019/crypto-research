# 加密市场情报监测方案 v0.6

> 目标：把"分散、嘈杂、真假混杂"的加密信息，压缩成**每天不超过 10 条、每条都能回答"这事跟我仓位有什么关系"的信号**。
> 定位：信息基础设施，不构成投资建议。所有结论必须能回溯到原始信源链接。
>
> **当前版本 v0.6（2026-11-08，已落地）**：关注 BTC / ETH / BNB；不监控 KOL，改为头部交易所高管发声白名单；零成本（无 VPS、无付费 API）+ Telegram 送达。
> **v0.6 变更**：按实际实现更新"运行架构"（§3.2），补充调度方式（launchd 每 10 分钟）与落地进度（§8）；评分模型与阈值未改。

---

## 0. 先想清楚：筛选难的本质

不是"新闻太多"，而是**没有事先定义"什么信息会改变我的决策"**。没有这个定义，任何过滤器都只能凭感觉。

所以本方案的第一步不是接数据源，而是写下一张**决策触发清单**：只有会触发下面四类动作的信息，才值得打扰你。

| 触发类型 | 影响的是什么 | 典型信息 |
|---|---|---|
| A. 环境变了 | 全市场 beta（系统性） | 监管立法/执法、ETF 审批、美联储意外、税收政策 |
| B. 标的变了 | 单币 alpha（基本面/供需） | 上币下币、解锁/增发/回购、主网升级、治理、核心团队变动 |
| C. 交易对手不安全 | 生存（本金安全） | 交易所暂停提币/被黑/储备异常、稳定币脱锚、托管方出事 |
| D. 状态变了 | 择时（波动/流动性） | 资金费率极值、OI 骤变、清算瀑布、巨鲸异动、ETF 净流出入 |

**不属于以上四类的，默认不进告警，只进归档。**

---

## 1. 总体架构：一条五段式流水线

```
[采集] → [归一化] → [过滤/聚类] → [评分] → [路由/呈现] → [反馈校准]
  信源     统一结构     去噪去重     0-100 分    分级推送       周度调参
```

- **采集**：RSS / 官方 API / 网页 diff / 链上-行情 API / 社交 List。
- **归一化**：统一成 `{来源, 时间, 标题, 正文, 链接, 语言, 实体[]}`。
- **过滤/聚类**：白名单 + 实体词典 + 广告识别 → 同一事件合并成"事件簇"。
- **评分**：信源权重 + 事件基准 + 严重度 + 确认度 − 噪音惩罚。
- **路由**：P0 即时、P1 小时级、P2 日报、其余归档。
- **校准**：记录误报/漏报，每周调权重。

一句话原则：**宁慢勿假，宁少勿滥；一手信源永远优先于二手解读。**

---

## 2. 信源分层（T0–T3）

权重递减，只有 T0/T1 可以单独触发 P0；T2/T3 默认只能作为"发现线索"，必须回溯原文。**例外**：官方认证账号的高管本人原创，按接近 T0 的权重处理（见 `crypto-news/config/people.yaml`）。

> 当前采集范围只启用 BTC/ETH/BNB 相关信源；SOL、ASTER 专属信源已冻结（详见 `crypto-news/config/sources.yaml`）。

### T0 一手信源（权重最高，可直接定性）

- **交易所公告**：Binance / OKX / Coinbase / Upbit / Bithumb / Bybit / Kraken 的 Announcements 页（上币、下币、暂停充提、合约规则、地区限制）。
- **监管官网**：美国 SEC（Press Releases / Litigation / EDGAR）、CFTC、美联储、财政部/OFAC；香港 SFC；新加坡 MAS；日本 FSA；韩国 FSC；欧盟 MiCA/ESMA。
- **项目官方**：官方 X、GitHub Release、治理论坛（Snapshot/Tally/Commonwealth）、官方博客。
- **数据/链上**：DefiLlama（稳定币、TVL）、Arkham/Nansen（地址标签）、Whale Alert、Tenderly（攻击监控）、Etherscan 大额转账。
- **ETF/机构**：BlackRock/Fidelity 等发行人官网、13F/持仓披露、Farside 等 ETF 资金流。

### T1 专业媒体（可信度高，需交叉确认）

The Block、CoinDesk、Cointelegraph、Blockworks、DL News、The Defiant；
中文：PANews、律动 BlockBeats、吴说区块链、深潮 TechFlow、Foresight News。

### T2 高管发声（替代 KOL）

**不监控广义 KOL**（影响者、喊单号、财经博主）；只监控**能影响市场的高管本人原创发言**：

| 人物 | 身份 | 敏感度 |
|---|---|---|
| CZ（赵长鹏） | Binance 创始人 | 高 |
| 何一（Yi He） | Binance 联合创始人 | 高 |
| Brian Armstrong | Coinbase CEO | 中 |
| Ben Zhou | Bybit CEO | 中 |
| Jesse Powell | Kraken 联合创始人 | 低 |
| 徐明星 / Richard Teng | OKX 创始人 / Binance CEO | 待核实 handle |

规则：
- 只保留**本人原创**；转发（RT）、点赞、无本人评论的引用一律丢弃。
- 账号被盗是真实风险，涉及"被起诉/辞职"这类重大表态必须有第二渠道确认。
- 路由：提到持仓标的/上币/监管/安全 → **P1**；泛市场观点 → **P2**；本人被起诉/调查/辞职 → **P0**。

> 零成本获取方式：`r.jina.ai` 代理 `x.com/<handle>` 主页，返回最近帖子 Markdown，**无需 X API key**。已实测 CZ、何一主页可用。fxtwitter 会间歇性 404，只作辅助。

### T3 聚合/搬运（只做线索发现）

Google News RSS、CoinGecko/CMC 新闻、各类公众号搬运号。仅用于发现"可能漏了"，不作为决策依据。

---

## 3. 采集通道与成本

| 通道 | 适用信源 | 实现方式 | 成本 |
|---|---|---|---|
| RSS | 媒体、部分交易所/监管 | RSSHub 自建或现成 feed | 低（免费/VPS ~$5/月） |
| 官方 API | 交易所公告、X List、Telegram 频道 | 官方接口 + key | X API 有月费；其余多为免费 |
| 网页 diff | 无 RSS 的监管/公告页 | 定时抓取 + 内容指纹比对 | 低 |
| 行情/链上 API | 稳定币、TVL、资金费率、OI、清算 | DefiLlama / CoinGecko / CoinGlass / Binance 公开接口 | 免费额度可用 |
| 邮件 | Google Alerts、监管邮件订阅 | 邮箱解析入流水线 | 免费 |

两套组合：
- **零成本起步（≈¥0–70/月）**：自建 RSSHub + 轻量编排；交易所公告走 Binance/Bybit/OKX 官方 JSON API；Kraken RSS；SEC/CFTC/Fed/吴说 RSS；英文五家媒体 RSS；Google News 关键词（上币/SEC/ETF）；Telegram Bot 盯 3–5 个快讯频道；changedetection 盯 OKX/Upbit/Bithumb/OFAC 等无 RSS 页面；DefiLlama/CoinGecko/Binance 行情。X 先手动或低频。
- **进阶付费（≈$200–600/月）**：X API 按量计费（务必设消费上限）、CoinGlass、CryptoQuant 或 Glassnode（约 $29–99/月）、Arkham/Nansen 地址标签、The Block 订阅。

> 需注意各平台 ToS：抓取频率过高会被封 IP/账号，交易所公告页尤其敏感。以 RSS/官方 API 为主，网页抓取做兜底。

### 3.1 各信源的获取要点与坑（2026-11 调研）

| 信源 | 方式 | 坑 |
|---|---|---|
| Binance 公告 | CMS JSON API | 网页版有 JS 挑战（202），走 JSON；公告常在深夜，延迟即滑点 |
| OKX 公告 | v5 `support/announcements` | **不带 annType 参数**就能返回；带参数反而报错 |
| Bybit 公告 | v5 `announcements/index` | 免费稳定，可按 type 筛上币类 |
| Coinbase | Statuspage + 页面 | blog RSS 被 Cloudflare 拦截 |
| Upbit / Bithumb | 页面抓取 | 无公开公告 API，韩国站有实名/地区限制 |
| Kraken | blog RSS | 上币看 asset-listings |
| SEC / CFTC / Fed | RSS | SEC 必须带合规 UA，否则 403 |
| OFAC / SFC / MAS / FSA / FSC / ESMA | 网页 + 邮件 | 无 RSS，只能页面 diff |
| 英文媒体 | RSS | CoinDesk 需跟随 308 重定向 |
| 中文媒体 | RSSHub 自建 | 律动 `/rss` 403；公共 RSSHub 实例多已 403，必须自建 |
| X List / KOL | API v2 | **已明确不监控**；API 按量计费易失控，nitter 已死，cookie 抓取违反 ToS 易封号 |
| X 高管主页 | `r.jina.ai` 代理 | `https://r.jina.ai/https://x.com/<handle>`，5–10 分钟一次；免费、免 API key，jina 有限速；只取本人原创 |
| Telegram | Bot API | Bot 只能读被拉入的频道；userbot 抓公开频道违规 |
| 链上标签 | Whale Alert/Arkham/Nansen | 需 key/付费；Glassnode/CryptoQuant 约 $29–799/月 |
| Farside（ETF 流） | 网页 | Cloudflare 403 |

> 注：本轮调研中"X API 取消档位改按量计费"及部分 2026 年市场事件的说法标注为**待核实**，接入前请以官方文档/账单为准。

### 3.2 运行架构（轻量版，单机可跑；已落地）

> 仓库按需求拆成子目录（2026-11-08）：根目录只放共享层（README / `.env` / `docs/` / CI），
> 本需求全部代码在 `crypto-news/` 下；后续新需求（如价格告警）新建同级目录，模块间不互相 import。

```
crypto-research/                # 仓库根：README / .env / docs / .github / .vscode
├── crypto-news/                 # ← 本需求：加密情报采集 / 评分 / 告警
│   ├── config/                 # 全部 YAML，改配置不改代码
│   │   ├── watchlist.yaml      # 关注范围（BTC/ETH/BNB）
│   │   ├── sources.yaml        # 信源清单：URL、类别、tier、轮询间隔
│   │   ├── people.yaml         # 高管发声白名单与分级路由
│   │   ├── entities.yaml       # 实体词典：币种/项目/人物/机构
│   │   ├── rules.yaml          # 过滤/防骗/去重规则
│   │   ├── thresholds.yaml     # 评分权重与 P0 硬条件
│   │   └── notify.yaml         # Telegram 送达格式与限速
│   ├── scripts/                # 代码与本模块入口
│   │   ├── fetch.py            # 采集 + 归一化（RSS/JSON API/网页 diff）→ data/raw
│   │   ├── score.py            # 过滤/去重聚类/分类/评分/分级 → 本地日报 + 告警状态
│   │   ├── notify.py           # Telegram 推送（去重、夜间静默、退避重试）
│   │   ├── run.sh              # 一键：采集 + 评分（--notify 追加推送）
│   │   ├── collect.sh          # 定时入口：加锁跑 run.sh --notify，日志留痕
│   │   └── install_launchd.sh  # 安装/卸载/查看 launchd 定时任务
│   ├── tests/                  # 25 项单元测试（unittest）
│   ├── deploy/launchd/         # LaunchAgent 模板
│   └── data/
│       ├── raw/YYYY-MM-DD/items.jsonl   # 原始留存，可回溯重放
│       ├── reports/YYYY-MM-DD.md        # 本地日报（P0/P1/P2 分级）
│       ├── state/alerts-<date>.json     # 当日告警，供推送
│       ├── state/notified.json          # 推送去重记录
│       └── logs/                        # 采集与调度日志
└── crypto-price/               # 未来新需求按同样约定新建（暂未建）
```

**调度**：macOS launchd `com.crypto-research.crypto-news` 每 10 分钟执行 `crypto-news/scripts/collect.sh`（锁文件防重叠，日志 `crypto-news/data/logs/collect.log`）；夜间 22:00–07:00 只放行 P0；同一事件 24h 内只推一次。

**为什么留 raw 层**：评分规则一定会改，原始数据要能重放回测；否则调参就是拍脑袋。

### 3.3 工具选型

| 方案 | 适合 | 代价 |
|---|---|---|
| DSH 原生（定时触发 + web_fetch/脚本 + 本地文件） | 不想维护服务器 | 依赖本机常开 |
| Python 脚本 + cron/launchd | 想完全可控、可回测 | 需要写代码 |
| n8n / Huginn | 想可视化编排 | 节点多时难维护 |
| 现成 SaaS（如各类币圈快讯） | 省事 | 无法定制评分、信源黑箱 |

> DSH 生态里已有 RSS 阅读、通知推送、IM 桥接类插件，可作为 Phase 3 的送达层备选；但核心评分逻辑建议自己掌握，不要外包给黑箱。

---

## 4. 筛选与评分（核心）

### 4.0 事件分类（先分类，再评分）

9 类互斥；冲突时优先级：**安全 > 交易所 > 监管 > 其他**。

| # | 类别 | 触发词（中/英） |
|---|---|---|
| 1 | 协议安全 | 被盗/攻击/重入/私钥泄露/闪电贷 — exploit, hack, drained, reentrancy, compromised key |
| 2 | 交易所托管 | 暂停提币/挤兑/储备/破产 — withdrawals halted, insolvent, proof of reserves, bank run |
| 3 | 监管执法 | 起诉/和解/禁令/牌照/税收 — lawsuit, settlement, ban, license, IRS |
| 4 | 代币经济 | 解锁/悬崖/增发/回购 — unlock, cliff, emission, buyback, burn |
| 5 | 宏观 | 利率/CPI/非农/流动性 — FOMC, CPI, NFP, rate hike, QT |
| 6 | 资金流 | ETF 净流入/巨鲸/稳定币增发 — ETF inflow, whale, mint, exchange netflow |
| 7 | 技术升级 | 硬分叉/主网/EIP/客户端 bug — hard fork, mainnet, EIP, client bug |
| 8 | 治理 | 提案/投票/治理攻击/财库 — proposal, quorum, governance attack, treasury |
| 9 | 市场结构 | 清算/基差/做市商/到期 — liquidation, basis, market maker, expiry |

### 4.1 第一层：硬规则过滤（先砍 70% 噪音）

1. **白名单**：只保留 T0/T1 信源 + 已订阅的 T2 账号。
2. **实体词典**：币种/项目/人物/机构/事件词，维护在 `entities.yaml`，随持仓和关注列表更新。
3. **广告/喊单识别**：话术（保本/稳赚/限时额度/内幕/"官方客服"私聊/领空投）、单日同币 > 3 条、给目标价+时限、付费未披露 → 直接丢或大幅降权。
4. **假公告识别**：只认"官网 + 官方 X"双发；截图和转发不算；公告 URL 必须在白名单域名内。
5. **链接风控**：短链、要求 connect 后无限 approve、签名即转账的页面 → 直接拒绝。
6. **语言与地区**：中文为主 + 英文关键源，其余按需。
7. **域名/账号画像**：编辑距离 ≤ 2 的相似域名、同形字（а/a、0/O）、注册 < 90 天的域名；注册 < 30 天、粉丝/互动比 > 100、改名清号的账号 → 降权或丢弃。

### 4.2 第二层：去重与聚类

1. **URL 规范化**：小写主机、去 `utm_*/ref/fbclid`、去尾斜杠、展开短链 → sha256 精确去重。
2. **SimHash 64bit**（正文 shingle=3），汉明距离 ≤ 3 判同文。
3. **标题**：字符 3-gram Jaccard ≥ 0.8，或归一化编辑距离 ≤ 0.2。
4. **Embedding**：cosine ≥ 0.85 且同处 24h 窗口 → 同一 `event_id`（bge-small 本地即可跑）。
5. **合并计分**：同簇取最高权威源定 S；k 只数独立源（转载/同集团计 1）；**来源互相矛盾时 C=0 并降一级**。
6. 同一簇 24h 内只推 1 次，有重大进展才"更新推送"。

### 4.3 第三层：影响评分（0–100）

用"加权 + 时效衰减"，比简单加总更能反映真实优先级：

```
Score = clamp(0, 100, (0.20·S + 0.30·T + 0.35·M + 0.15·C) × D)
```

- **S 信源分**：官方一手 100 / 一线媒体 80 / 垂直媒体 60 / KOL 25 / 匿名截图 10。
- **T 事件基准**：协议安全 95、交易所 90、稳定币脱锚 85、监管执法 75、宏观 70、解锁 55、资金流 50、技术升级 45、治理 40、市场结构 45。
- **M 严重度量级** = max(金额分, 脱锚分, 层级分) + 10×(命中维度−1)，封顶 100
  - 金额分 = 25×log10(损失USD/1e4)（$1万=0，$100万=50，$1亿=100）
  - 脱锚分 = 25×|bps|/100（100bps=25，400bps=100）
  - 层级分 = 地方 20 / 部门 50 / 国家级 80 / 多国联合 100
- **C 确认度** = 100×(1−0.5^(k−1))，k=独立信源数（同集团/转载计 1；官方一手计 1.3 倍，封顶 100）。
- **D 时效衰减** = 2^(−Δt/h)：安全/监管/交易所 h=24，行情/宏观 h=6。

**分级**：P0 ≥ 78 或命中硬条件；P1 58–77；P2 35–57；< 35 归档不推。

**P0 硬条件（命中任一，不看总分，立即推）**
1. 稳定币脱锚 > 200bps 持续 15 分钟，或法币抵押型价格 < 0.95；
2. 交易所暂停提币/到账延迟 > 2 小时，或 24h 储备流出 > 10%；
3. 被盗 > $10M 或占 TVL > 5%（参照：Bybit 2025-02 单案约 $14.6 亿）；跨链桥 1h 流出 > $20M；
4. ETF 正式决议、SEC 起诉头部交易所、国家级禁令；
5. FOMC 实际结果偏离市场定价 > 30%（如 ±50bp 意外）；
6. 全市场 1h 清算 > $5 亿。

**P1 参考**：被盗 $1–10M、脱锚 50–200bps、单笔 > $5000 万巨鲸转入交易所、资金费率 > 0.1%/8h、OI 24h ±25%、期权 skew > 25vol。

### 4.4 第四层：LLM 摘要（只对 P0/P1）

每条输出三行结构化结论：
1. 发生了什么（事实，含金额/主体/时间）；
2. 为什么重要（影响哪类资产、多大概率、持续性）；
3. 建议动作（观察 / 减仓 / 对冲 / 不动）——**标注为参考，不替代判断**。

硬约束：必须带原文链接；无源断言一律打回；数字类信息要求双源。

---

## 5. 黑天鹅早警指标（领先于新闻）

新闻是"已发生"，这些指标是"正在发生"：

| 指标 | 免费来源 | 计算口径 | 预警阈值 | 历史参照 |
|---|---|---|---|---|
| 稳定币溢价/脱锚 | Curve/Uniswap 池、CoinGecko、DefiLlama | 主流池 TWAP 偏离 | > 50bps 观察，> 200bps P0 | UST 2022-05 数日内归零，约 $400 亿蒸发 |
| 交易所储备 | DefiLlama CEX、CryptoQuant 免费档 | BTC/ETH 余额 7 日变化 | −5% 警惕，−10% P0 | FTX 2022-11 提币挤兑 |
| 资金费率 | Binance/Bybit 公开接口 | 永续 8h 费率 | > 0.1% 或 < −0.05% | 2021-04 多头拥挤后回撤 |
| 未平仓量 | CoinGlass/交易所 | 24h OI 变化 | ±25% | 2025-10 清算瀑布 |
| 期权 skew | Deribit | 25Δ put-call | > 25vol | 2022-11 恐慌定价 |
| 清算量 | CoinGlass | 1h 全市场强平 | > $5 亿 | 2025-10-11 |
| 巨鲸转账 | Etherscan、Whale Alert | ≥ $5000 万转入 CEX | 单笔即报 | FTX 崩盘前 Alameda 转账 |
| 跨链桥流出 | DefiLlama Bridges | 1h 净流出 | > $2000 万 | Ronin 2022-03，$6.24 亿 |
| 协议 TVL | DefiLlama | 24h TVL 变化 | −20% | 2022-05 Terra |
| 宏观偏离 | FRED、CME FedWatch | 实际 vs 市场定价 | 偏离 > 30% | 2020-03 紧急降息 |

初始阈值只是起点，跑两周后按误报率校准；更严谨的做法是用 DefiLlama/CoinGlass 历史数据回测一遍再定阈值。

---

## 6. 告警路由与节奏

| 级别 | 时效 | 渠道 | 数量目标 |
|---|---|---|---|
| P0 | < 2 分钟 | Telegram/邮件/桌面通知/声音，多通道冗余 | ≤ 2 条/天 |
| P1 | 1 小时聚合 | 单条简报 | ≤ 5 条/天 |
| P2 | 每日 08:00 + 21:00 | 日报/晚报（市场快照 + 事件表 + 次日日历） | 1 份 |
| 周报 | 周日 | 复盘 + 评分校准 + 黑天鹅 checklist 更新 | 1 份 |

- **夜间静默**：22:00–07:00 仅 P0。
- **冷却期**：同一事件 30 分钟内不重复推。
- **每日上限**：超过 10 条即说明阈值太松，回去调分。

---

## 7. 事件日历（提前布局，不追涨杀跌）

| 日历 | 频率/时间 | 来源 |
|---|---|---|
| FOMC 决议 | 每年 8 次，固定日程 | Fed 官网、CME FedWatch |
| CPI | 每月中旬 | BLS 官网 |
| 非农 | 每月首个周五 | BLS 官网 |
| 代币解锁 | 按项目 | TokenUnlocks/Tokenomist + 项目文档 |
| ETF 决议 | 不定期 | SEC EDGAR + 发行商 S-1 更新 |
| 硬分叉/主网 | 不定期 | GitHub Release、官方博客 |
| 期权到期 | 每月最后周五（季度大到期 3/6/9/12 月末） | Deribit |

日历的作用是"提前知道哪天要紧张"，而不是当天才反应。**每个 P2 日报末尾附"未来 7 天日历"。**

---

## 8. 落地路线（先设计，后自动化）

| 阶段 | 内容 | 周期 |
|---|---|---|
| Phase 0 | 写决策触发清单、关注列表、实体词典、P0 阈值 | 1 天 |
| Phase 1 | 接 T0/T1 RSS + 网页 diff + 免费行情 API，**只记录不告警** | 3–5 天 |
| Phase 2 | 上评分脚本 + 去重聚类，人工复核一周，标误报/漏报 | 3 天 |
| Phase 3 | 接告警渠道，P0/P1 推送；接入 LLM 摘要与日报 | 3 天 |
| Phase 4 | 加链上/宏观早警指标，做本地 dashboard，持续校准 | 持续 |

**当前进度（2026-11-08）**：Phase 0–2 已完成；Phase 3 已接入 Telegram 推送与本地日报（LLM 三行摘要尚未接入）；Phase 4 的早警指标只完成了数据采集（资金费率、稳定币、TVL、价格），阈值判断与历史回测尚未实现。

**关键纪律：Phase 1–2 必须先跑"只记录不告警"**，否则会被噪音淹没，最后放弃整个系统。

---

## 9. 风险与合规

- 只做信息聚合与提示，不构成投资建议；LLM 摘要必须人工复核后再用于决策。
- 防钓鱼：所有公告以**官方域名**核对；警惕相似域名、假 KOL 账号、假空投链接。
- 抓取遵守各平台 ToS，控制频率；优先 RSS/官方 API。
- 隐私：API key 与告警凭据本地保存，不入库不外传。

---

## 10. 已确认参数与定制结论（2026-11-08，v0.5）

| 参数 | 已确认 | 对方案的影响 |
|---|---|---|
| 关注范围 | **BTC / ETH / BNB**（SOL、ASTER 暂时冻结） | 实体词典只放这 3 个 + 稳定币；上币类公告凡不在这 3 个里的一律不进告警 |
| 送达渠道 | **Telegram** | 用自建 Bot 推送到自己的 chat，零成本；P0/P1/P2 分层走同一 Bot 不同消息格式 |
| 预算与自建 | **先零成本跑通** | 不买 VPS、不买 X API、不买 CoinGlass；用公开 RSS + 官方 JSON API + Telegram Bot + 本机运行为主 |
| 社交监控 | **不监控 KOL**；只监控头部交易所创始人/CEO 的本人原创 | 用 `r.jina.ai` 代理 X 主页，零成本；见 `crypto-news/config/people.yaml` |
| 时区 | 本地时区（） | 夜间静默按本地时间 22:00–07:00 计算 |
| 语言 | 中文为主，英文源保留原文标题 | 摘要输出中文，附原文链接 |

**由"只看 BTC/ETH/BNB"推出的关键简化**：
1. 交易所上币/活动公告 90% 是噪音，只保留：涉及这 3 个标的的公告 + 维护/暂停充提 + 下架。
2. 不需要全市场山寨币词典，词典规模从几千个降到几十个，误报率会低很多。
3. 信号重心是 beta 驱动：**监管、宏观、ETF 资金流、交易所/稳定币风险**——恰好是零成本信源最全、最可靠的部分。
4. **BNB 的风险结构与 BTC/ETH 不同**：它一半是平台币，Binance 的监管动作会直接传导，所以单列了一条 P0（Binance 被起诉/和解/处罚）和一条"季度销毁"的 P2 正向信号。
5. SOL 状态页、ASTER 代币经济等专属信源已移除（配置与实测记录保留，恢复时一行开关）。
6. 噪音容忍度可以更低：这三个标的相关的"喊单/预测"几乎全可丢，系统只留事实与一手公告。

### 10.1 零成本 Telegram 送达规格

1. 在 Telegram 找 `@BotFather` → `/newbot` → 拿到 `BOT_TOKEN`（免费）。
2. 给自己的 Bot 发一条消息，再访问 `https://api.telegram.org/bot<TOKEN>/getUpdates` 拿 `chat_id`。
3. 推送用 `https://api.telegram.org/bot<TOKEN>/sendMessage`，参数 `chat_id`、`text`、`parse_mode=HTML`、`disable_web_page_preview=false`。
4. 凭据放本机环境变量（`TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`），不要写进配置文件。
5. 消息格式分三档：P0 加 🚨 前缀 + 卡片全文；P1 一行摘要 + 链接；P2 日报合并一条（Telegram 单条上限 4096 字符，超长自动分条）。
6. 限速：Telegram Bot API 约每秒 30 条、单聊天每分钟 20 条，正常用量远低于上限；失败按 1s/4s/16s 退避重试。
7. 防仿冒原则：官方账号一律以官网/文档的 "Official Links" 为准，名字极像的账号是钓鱼重灾区（案例：Aster 官方 TG 是 `t.me/AsterDEX`，而 `t.me/Aster_DEX` 是另一个空频道）。SOL/ASTER 当前已冻结，此条作为通用规则保留。

> 若以后要读**频道**（而不只是自己发），Bot 必须被拉进频道当管理员；公开频道也可以用 `t.me/s/<频道名>` 预览页做零成本抓取（已实测可用）。

---

## 附录 A：评分计算示例

> 事件：某 DeFi 池子被攻击，损失 $2500 万，官方公告 + 2 家媒体确认，发布 3 小时。

- S = 85（官方一手 + 媒体），T = 90（协议安全），
- M = 25×log10(2500) = 85（$2500 万），
- C = 100×(1−0.5^(3−1)) = 75（3 个独立源），
- Raw = 0.20×85 + 0.30×90 + 0.35×85 + 0.15×75 = **85.0**，
- D = 2^(−3/24) = 0.917 → **Score = 78 → P0**（同时命中"被盗 > $10M"硬条件）。

反例：某 KOL 发推「某币要上大所，冲」——S=25、T≈10、M=0、C=0 → 接近 0 分，归档不推。

## 附录 B：告警卡片模板（P0/P1）

```
🚨 P0 · 交易所风险 · 2026-11-07 23:40
【事件】XX 交易所暂停 XX 链充提，称链上异常
【来源】官方公告（链接）+ 链上数据（链接）
【影响】该链资产短期无法转移；同链其他所暂无异常
【动作参考】检查该所敞口 / 暂缓该链入金 / 观察是否升级为全站暂停
【确认度】T0 单源（官方）+ T1 媒体 2 家跟进
```

## 附录 C：黑天鹅周度 checklist

- [ ] 主流稳定币是否持续溢价/折价？
- [ ] 头部交易所储备是否连续净流出？
- [ ] 资金费率是否出现极端单边且持续？
- [ ] 是否有巨鲸持续转入交易所？
- [ ] 是否有协议 TVL 异常骤降但币价未反应？（可能是被盗前兆）
- [ ] 宏观日历：未来 7 天有无 FOMC/CPI/非农/解锁/ETF 决议？
- [ ] 本周误报/漏报复盘：哪条规则需要调？

## 附录 D：关键接口实测结果（2026-11-07 本机验证）

> 这一节是"能不能落地"的证据，全部为本机 curl 实测。其中 SOL/ASTER 相关条目已冻结（保留作恢复时参考），当前只需看 BTC/ETH/BNB 相关部分。

| 接口/页面 | 状态 | 说明 |
|---|---|---|
| Binance 公告 CMS JSON `binance.com/bapi/composite/v1/public/cms/article/list/query?type=1` | ✅ 200 免 key | 交易所公告最省事的通道，直接拿结构化 JSON |
| OKX 公告 API `okx.com/api/v5/support/announcements` | ✅ 200 免 key | **不要带 annType 参数**，带参数反而报错 |
| Bybit 公告 API `api.bybit.com/v5/announcements/index?locale=en-US` | ✅ 200 免 key | `retCode: 0`，结构化 JSON |
| Binance 公告网页 `binance.com/en/support/announcement` | ⚠️ 202 | 有 JS 挑战，不适合直接抓，优先走 CMS JSON |
| Coinbase 博客 RSS `blog.coinbase.com/feed` | ⚠️ Cloudflare 拦截 | 需换官方公告接口或邮件订阅 |
| Upbit 公告接口 | ⚠️ 待确认 | 上币信息可先通过 `api.upbit.com/v1/market/all` 增量发现 |
| Binance 合约 `fapi.binance.com/fapi/v1/premiumIndex` | ✅ 200 免 key | 资金费率/标记价格，早警指标可用 |
| SEC 新闻 RSS `sec.gov/news/pressreleases.rss` | ✅ 200 | 监管 T0 源 |
| CFTC RSS `cftc.gov/PressRoom/PressReleases/rss` | ✅ 200 | 监管 T0 源 |
| 美联储 RSS `federalreserve.gov/feeds/press_all.xml` | ✅ 200 | 宏观 T0 源 |
| The Block RSS `theblock.co/rss.xml` | ✅ 200 | T1 媒体 |
| Google News RSS 自定义查询 | ✅ 200 | T3 线索发现，可按关键词组合 |
| DefiLlama 稳定币 `stablecoins.llama.fi/stablecoins` | ✅ 200 | 脱锚监控数据源 |
| DefiLlama 价格 `coins.llama.fi/prices/current/...` | ✅ 200 | 免 key 行情 |
| DefiLlama 链 TVL `api.llama.fi/v2/chains` | ✅ 200 | TVL 骤降监控 |
| Coinbase `api.exchange.coinbase.com` | ✅ 200 | 现货价格/盘口 |
| Kraken `api.kraken.com/0/public/Ticker` | ✅ 200 | 现货价格 |
| Upbit `api.upbit.com/v1/market/all` | ✅ 200 | 韩国市场/上币线索 |
| CoinGecko `/api/v3/ping` | ✅ 200 | 免费额度可用 |
| CoinGlass 公开 API | ⚠️ 需 API key | 清算/OI 数据要付费或注册 |
| SEC EDGAR 网页查询 | ⚠️ 403 需合规 UA | 需带联系邮箱的 User-Agent，控制频率 |
| CoinDesk RSS | ⚠️ 308 跳转 | 需跟随重定向到新地址 |
| Aster 文档 `.md` / `llms.txt` | ✅ 200 | 文档页追加 `.md` 取纯文本，`llms.txt` 是全站索引，diff 即可发现规则变更 |
| Aster 代币经济页 | ✅ 200 | 已核实：团队 cliff 至 2027-09-17，线性 vesting 改为 staking emission |
| `status.solana.com/history.rss` | ✅ 200 | 与 summary.json 互补，用于确认停机等级 |
| `r.jina.ai` 代理 Farside `farside.co.uk/btc`、`/eth` | ✅ 200 | **零成本拿到 ETF 日度净流入**（直连 403）；免费但限速，1 小时一次足够 |
| tokenomist.ai 解锁日历 | ⚠️ 403 禁止自动化 | 需人工每周查看或换付费源 |
| solana.com 新闻页 / RSS | ⚠️ 429（jina 代理也被 Vercel 拦） | SOL 动态改走 Google News + 状态页 + solanafloor/solanacompass 页面 diff |
| Aster 官方 Telegram `t.me/AsterDEX` | ⚠️ 无公开预览页 | 频道预览为空，无法零成本抓；`t.me/Aster_DEX` 是另一个空频道，**仿冒风险，不要抓** |
| Aster Medium 博客 | ⚠️ 403 Cloudflare | 以文档索引为准 |
| `r.jina.ai` 代理 X 主页 `r.jina.ai/https://x.com/cz_binance`、`/heyibinance` | ✅ 200 | **零成本拿到高管最近帖子**（免 API key）；返回 Markdown，含帖文与配图链接 |

结论：**T0/T1 的 RSS 与行情/稳定币数据基本可零成本打通；交易所公告用 Binance CMS 这类 JSON 接口最稳；X/Telegram 和 CoinGlass 是仅有的"要账号或付费"的部分。**

## 附录 E：个人投资者的每日 / 每周 SOP

**开盘前（08:00，10 分钟）**
1. 看隔夜 P0/P1 告警与夜间静默期积压；
2. 扫一遍 T0 公告（交易所/监管）与 T1 头条；
3. 看市场快照：BTC/ETH 价格、资金费率、OI、稳定币溢价；
4. 看未来 7 天日历，标出需要预留注意力的日子。

**盘中（每 2–4 小时，3 分钟）**
1. 只看 P0 与新增 P1，不看信息流；
2. 核对任何"截图/传闻"是否已被 T0/T1 确认；
3. 记录一条：今天改变了什么判断？

**收盘后（21:00，10 分钟）**
1. 读日报（市场快照 + 事件表 + 次日日历）；
2. 回看当天是否漏报（对照 T1 媒体头条）；
3. 给当天推送打分：有用/噪音，喂给校准。

**每周日（30 分钟）**
1. 周报复盘：本周 P0 是否真的重要？漏了哪些？
2. 调权重/阈值（只改 1–2 个参数，避免过拟合）；
3. 更新关注列表、实体词典、黑天鹅 checklist。

**原则**：系统负责"不漏"，人负责"判断"。不要用刷信息流代替思考。
