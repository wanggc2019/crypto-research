# 交接文档（SESSION HANDOFF）

> 用途：任何新会话**先读这份**，不必翻旧聊天记录。
> 最近更新：2026-11-09 23:50（本地时区）—— 新增 crypto-knowledge（每天 13:00/19:00/21:00/23:00 四班、每节 10–15 分钟课程 + 21:00 回顾总结，160 条内容）；crypto-jobs 改为单任务双时间：每天 18:00 日更推日报、周日 10:00 周报；日常报告统一走 独立 Bot
> 说明：目录最初叫 `crypto-new`（占位名），2026-11-08 02:18 按领域更名为 `crypto-news`，launchd Label 同步为 `com.crypto-research.crypto-news`

## 0. 项目一句话

个人加密情报监测系统 **crypto-intel**：把分散、嘈杂的加密信息压缩成每天 ≤10 条可决策信号。

- 仓库：`~/vscode/crypto-research` · GitHub：本仓库
- **结构**：一个仓库 = 若干自包含需求模块
  - [`crypto-news/`](../crypto-news/README.md) —— 消息面情报采集/评分/告警
  - [`crypto-price/`](../crypto-price/README.md) —— BTC/ETH/BNB 价格/衍生品异动实时告警（2026-11-08 新增）
  - [`crypto-analyst/`](../crypto-analyst/README.md) —— 行情分析/情景推演（2026-11-08 新增；需求定稿 + P2 主体完成：每日简报/打分/宏观/22 单测/CI；只读 exports + 公开 K 线）
  - [`crypto-x-kol/`](../crypto-x-kol/README.md) —— 关注的 KOL 的 X 推文监控（2026-11-08 新增；r.jina.ai 匿名抓主页 → 增量去重 → 日报/Telegram，白名单 26 个账号、每 5 分钟、零成本）
  - [`crypto-policy/`](../crypto-policy/README.md) —— 国内数字货币政策/法规/案例雷达（2026-11-08 新增 MVP；3 个 T0/T1 源 → 规则门 → 法规库/速报/待解读队列；每 1 小时、零 API）
  - [`crypto-jobs/`](../crypto-jobs/README.md) —— 加密行业技术岗位（DevOps/SRE/MLOps/开发）市场情报（2026-11-08 新增 MVP；公开 ATS/招聘站 → 技能词典抽取 → 日快照/差分 → 周日 10:00 周报；零成本；2026-11-09 纳入开发岗位）
  - [`crypto-onchain/`](../crypto-onchain/README.md) —— 链上/交易所资金流"卖压归因"（2026-11-09 新增 **M2 归因引擎**；DefiLlama CEX 净流 + Binance 逐笔主动卖 + OKX 清算 + 稳定币净增发 → 4 类归因 + exports `onchain/pressure/v1`；**链上大额转账 + 事件驱动告警**（ETH/BSC USDT/USDC，公共 RPC，ETH ≥$10M / BSC ≥$5M，免费免 key；即时 Telegram + 日限额 + 特殊期解除 + 推测备注）；零 key、零成本）
  - [`crypto-knowledge/`](../crypto-knowledge/README.md) —— 每天 4 条学习内容（3 节 10–15 分钟课程 + 21:00 回顾总结；160 条内容、4 阶段、SRS 间隔复习、4 时段 launchd；独立 Bot 独立 Bot）
  - `libs/crypto_core/` —— 共享能力（多模块共用，模块只向下依赖）
  - 后续需求按 `crypto-<领域词>/` 新建，禁止占位名
- 文档入口：[架构设计](architecture.md)（P0–P4 已落地）· [文档导航](README.md)（共享文档 + docs/<module>/ 需求·设计·业务架构·技术架构） · [运行与下线 Runbook](runbook.md) · [编码与运行约定](conventions.md) · [ADR](adr/) · [拆分记录](code-plan-module-split.md)

## 1. 设计约束（默认配置，不要擅自改）

| 项 | 结论 |
|---|---|
| 关注范围 | **BTC / ETH / BNB**；SOL、ASTER 已冻结；其他山寨不看 |
| 社交监控 | **不监控泛 KOL 喊单**；crypto-news 只盯头部交易所创始人/CEO 的**本人原创**（CZ、何一、Brian Armstrong、Ben Zhou 等，见 `crypto-news/config/people.yaml`）；另有 **crypto-x-kol** 白名单监控关注的 KOL |
| 预算 | 先**零成本**跑通：不买 VPS、不买 X API、不买 CoinGlass |
| 送达 | **Telegram Bot**（凭据在仓库根 `.env`，不入库；各模块共用） |
| 时区 | 系统本地时区；**不设夜间静默，任何时间即时发送** |
| 语言 | 中文为主，保留英文源原文标题 |
| 工作方式 | **项目准则：需求评估 → 改代码 → 测试全绿 → 分支 commit + push + PR → CI 绿后 squash merge；每需求一个 PR；不直推 main（本地 pre-push 会拒绝）；不留未提交改动**；能自动验证的不要人工介入；先设计后自动化 |
| 仓库结构 | 一个目录一个需求（`crypto-<领域词>`）；模块间零 import；公共能力进 `libs/crypto_core`；根目录只放共享层 |

## 2. 当前状态（2026-11-08 23:55）

- **架构进度**：P0 ✅ 基础件、P1 ✅ 告警共性、P2 ✅ 抓取/行情原语、P3 ✅ 工具化、**P4 ✅ 首个新模块 crypto-price**；架构验证完成，后续新需求只加模块
- **代码位置**：`crypto-news/scripts/`、`crypto-price/scripts/`（业务）+ `libs/crypto_core/src/crypto_core/`（共享）
- **环境**：根 `.venv`（Python 3.9 + ruff/PyYAML）；模块不再自带 venv；无 venv 时脚本回退 `python3`
- **质量门**：`make verify` = ruff + libs 72 项 + tools 17 项 + crypto-news 42 项 + crypto-price 154 项（含 22 项红队对抗用例）+ crypto-analyst 35 项 + 各模块端到端，**全绿**；CI 不联网
- **定时任务 1（news）**：launchd `com.crypto-research.crypto-news`，每 10 分钟跑 `crypto-news/scripts/collect.sh`
- **定时任务 2（price）**：launchd `com.crypto-research.crypto-price`，每 60 秒跑 `crypto-price/scripts/collect.sh`；单实例锁 + 每轮健康检查 + 每日 prune
- **定时任务 3（weekly）**：launchd `com.crypto-research.crypto-analyst.weekly`，每周日 22:00 生成并推送行情分析周报（数据表 + 场景 + 复盘）；原 crypto-analyst 与 20:05 DSH AI 复盘已退役（收口到 crypto-analyst）
- **定时任务 4（kol）**：launchd `com.crypto-research.crypto-x-kol`，每 5 分钟跑 `crypto-x-kol/scripts/collect.sh`（r.jina.ai 抓 26 个 KOL 的 X 主页 → 指纹增量 → 日报/Telegram；账号间 4s 节流，单轮约 2 分钟）
- **crypto-x-kol 特点**：零成本、免 key（匿名 20 RPM 不消耗 token）；X 匿名页面无发帖时间/互动数，日报用「首次发现时间」；`@saylor` 等稳定登录墙账号默认关闭；**只推 P0/P1（价值分：主题/层级/同题/新颖度），P2/MUTE 只进日报，每日上限 8 条**；外文推文由 DeepSeek 翻成中文 + 一句话总结（缓存、无 key 降级原文）
- **定时任务 5（policy）**：launchd `com.crypto-research.crypto-policy`，每 1 小时跑 `crypto-policy/scripts/collect.sh`（国内 5 源可用：央行条法司/新闻、外汇局、证监会规章、最高法司法解释；国外 9 源可用：SEC/CFTC/Fed、ECB/ESMA、FCA、FSA + Federal Register（规则原文）+ EUR-Lex（法规检索）；公报 2 + MAS 1 备用 → 规则门（国外仅标题命中，规则制定 P0/执法 P1）→ 法规库 + P0/P1 速报；首次运行只建基线不推送；**每轮自检 + 有新政策立即触发 AI 解读，全部合并在本任务里，policy 只有 1 个 launchd 任务**）
- **定时任务 8（jobs 报告）**：launchd `com.crypto-research.crypto-jobs`，**两个触发时间**：每天 18:00 日更（采集 → 快照/差分 → 日报推送，周日 18:00 还会补跑一次周报去重）；周日 10:00 周报轮（先采集最新快照 → `--weekly --notify` 生成并推送周报）；每轮 healthcheck + 每日 prune；已安装并 healthcheck 正常
- **定时任务 9（jobs 速报）**：launchd `com.crypto-research.crypto-jobs.alert`，每 2 小时跑 `crypto-jobs/scripts/alert.sh`（`--alerts --notify`：只抓取并推新增目标岗位，无新增静默；状态 `data/state/alert-seen.json`；不做夜间静默）
- **crypto-jobs 实测（2026-11-09，含开发岗位）**：**40 个公开源**全部健康 → 1931 条原始岗位 → **457 条目标岗位**（57 家公司；software 128 / backend 63 / platform 44 / mobile 38 / ml 33 / blockchain 32 / data 30 / devops 21 / fullstack 17 / mlops 14 / frontend 13 / trading 13 / sre 9 / embedded 2）；技能词典 225 个；周报含全量公司统计、Top10 画像、**交易所动态（CEX/DEX）+ 招聘节奏**；首份周报 `crypto-jobs/data/reports/2026-11-09-weekly.md`（首次基线，下周开始有环比）
- **Telegram**：凭据已校验；news 卡片中文化；price 卡片含现价/各窗口涨跌/多源价差/触发规则；**所有消息带 `【模块名】` + `🏷 任务：<launchd Label>`**，可区分 news/price/健康检查
- **噪音控制（2026-11-08）**：price 早报默认只写本地不自动推送；健康检查默认只对 FAIL 推 Telegram（WARN 只落 `health.json`，`--notify-warn` 可开）
- **告警口径**：news 硬条件需「已确认 + 48h 内 + D≥0.1」；price 按 8 类规则分级；**所有告警全天即时（无静默）**，P2 只归档
- **日报/周报**：价格日报每天 18:30（）推送；数据周报每周日 22:00；AI 复盘周日 20:05（仅读周报文件，不重算数字）
- **价格数据源**：Binance 现货 + USD-M 永续（主源，K 线/资金费率/OI）、OKX、Coinbase（交叉核验），全部免 key；三源中位数 + 价差门控，单源失败自动降级
- **价格告警历史**：旧实现不在本仓库；`crypto-price/` 按当前架构独立维护
- **红队验证**：独立验证员发现 10 个边界 bug（held 重复、state 类型损坏、快照坏输入、重试风暴、日报重试/时间窗、凭据 rc、时区、价差阻断、锁残留、卡片截断），已全部修复并固化为 `crypto-price/tests/test_adversarial.py`
- **crypto-onchain（M2 归因引擎，2026-11-09）**：M1 的 4 类免费信号（逐笔主动卖/CEX 储备净流/OKX 清算/稳定币净赎回）+ 只读 `crypto-price/data/exports/latest.json`（M2 起该契约新增 `funding_rate`/`oi`，只增字段）→ **归因引擎**输出 4 类结论（多头强平/空头加仓/资金撤离/去杠杆），每条带 evidence / counter_evidence / unknown 三段，**不输出身份**。跨轮状态 `data/state/oi-history.json`（24h）用于 15m OI 变化；跨模块导出 `data/exports/onchain-pressure.json`（`onchain/pressure/v1`）。**链上大额转账 + 每日情报分析**（逐笔告警 2026-11-10 起默认关闭（只保留两类重大异常即时提醒）；21:00 摘要输出净流/噪声/单向大额分析）：公共 RPC 扫 ETH/BSC 的 USDT/USDC（ETH ≥$10M / BSC ≥$5M，排除 mint/burn），游标按链，明细进快照/exports/摘要；**新增大额转账即时推 Telegram**（P0=单笔 ≥$50M 或入所 ≥$25M；仅 P0 推送、全天即时；**告警消息含：代币数量/区块/from→to/地址标签（内置+Etherscan name tag，缓存 30 天）/流向+方向合并行（实体→实体，unknown 写“无法判断买卖方向”，不清楚写“未知”；推测另起括号）/今日累计与占比/交易链接**；常规 ≤10 条消息/天，（only_p0=false 时才按笔数扩容至 burst_cap=30）；同一 tx 多腿只留最大一腿、同对手方合并）；**特殊期（当日 P0 ≥600 笔 / |BTC 24h|≥6% / CEX 净流出 ≥150bps / 稳定币净赎回 ≥15 亿，或 manual）自动解除日限额**；允许 unknown 后括号标注「推测」，猜不到不写；内置 23 个已验证交易所标签（21 cex / 1 mm / 1 issuer）。**仅 4 类卖压候选仍只落盘不推送**；**150 项离线单测** + fixtures e2e + `make gate` 全绿。**专用 Bot**：链上告警/摘要走 独立 Bot（`.env` 的 `TELEGRAM_ONCHAIN_BOT_TOKEN`/`TELEGRAM_ONCHAIN_CHAT_ID`，未填回退共享 Bot）；**智能判断**（价格/新闻/资金面评分，≥2 分才推，低于阈值记 low_context 不打扰）；**巨鲸保护**：单笔 ≥$100M 或入所 ≥$50M 超过 10 条后仍再发至多 5 条/天（标「🐋 巨鲸」）；see [需求](crypto-onchain/requirement.md) · [模块 README](../crypto-onchain/README.md)
- **crypto-knowledge（2026-11-09）**：每天四班：13:00 概念课 / 19:00 理论或实践课 / 21:00 今日回顾与总结（约 8 分钟）/ 23:00 复盘课（正常通知）；160 条课程内容（概念 80 / 理论 45 / 实践 35，4 阶段由浅入深），SRS 间隔 1/3/7/14/30/60 天；同日同时段去重、错过时段补发、内容耗尽转复习；**68 个离线单测** + `make gate` 全绿；launchd `com.crypto-research.crypto-knowledge`（四个 StartCalendarInterval）；**独立 Bot 独立 Bot**（`.env` 的 `TELEGRAM_KNOWLEDGE_*`，未填回退共享 Bot，`--whoami` 可验证）。see [需求](crypto-knowledge/requirement.md) · [模块 README](../crypto-knowledge/README.md)

## 3. 常用命令

```bash
cd ~/vscode/crypto-research

make setup                  # 建根 venv（可选）
make gate                   # 统一质量门（每次修改后跑；全绿才打印「测试通过」）
make gate online=1          # 额外联网冒烟（外部失败默认警告）
make hooks                  # 安装 pre-push 钩子：push 前自动 make gate
make verify                 # 原全仓质量门（保留兼容）
make test                   # 只跑单测（离线）
make lint                   # 只跑 ruff

make jobs                                        # ★ launchd 任务总览（只读；json=1 / strict=1）
make healthcheck-jobs notify=1                    # 仓库级 launchd 健康检查（异常推 Telegram，60 分钟冷却）
make launchd module=crypto-news action=status     # 单模块定时任务状态（底层 tools/render_launchd.py；暂停/下线见 docs/runbook.md）
make launchd module=crypto-price action=status
make healthcheck module=crypto-news                # 手动跑一次健康检查（fix=1 自愈 / notify=1 告警）
make healthcheck module=crypto-price
make new-module name=crypto-xxx desc="..."        # 生成新模块骨架
tail -f crypto-news/data/logs/collect.log         # 每 10 分钟一轮日志
tail -f crypto-price/data/logs/collect.log        # 每 60 秒一轮日志

# 模块内手动跑
./crypto-news/scripts/run.sh            # 采集 + 评分（不推送）
./crypto-news/scripts/run.sh --notify   # 采集 + 评分 + 推送
./crypto-news/scripts/verify.sh         # 模块级自检（含 libs 单测）

./crypto-price/scripts/run.sh --dry-run --json   # 行情预览（不写状态、不推送）
./crypto-price/scripts/run.sh --notify           # 行情 + 规则 + 推送
./crypto-price/scripts/run.sh --status           # 最近运行/冷却/夜间压制
./crypto-price/scripts/run.sh --test             # 静默 Telegram 通道测试
./crypto-price/scripts/run.sh --report           # 立即生成/推送日报
./crypto-price/scripts/verify.sh                 # 模块级自检 + 离线 fixtures 回放

./crypto-jobs/scripts/run.sh --dry-run --json   # 联网抓一轮岗位，只打印（不写盘不推送）
./crypto-jobs/scripts/run.sh                    # 日更：raw + 快照 + 索引 + 日报 + exports
./crypto-jobs/scripts/run.sh --notify --daily-digest  # 手动补发日报摘要（同日去重）
./crypto-jobs/scripts/run.sh --weekly --notify  # 生成并推送技能趋势周报（摘要+md 附件；同日去重）
./crypto-jobs/scripts/run.sh --weekly --notify --force  # 强制重发
python3 crypto-jobs/scripts/e2e.py              # 离线全链路回放（CI/gate）

./crypto-onchain/scripts/run.sh                 # 影子一轮：采集 + 候选落盘（不推送）
./crypto-onchain/scripts/run.sh --dry-run        # 只预览（不写文件）
./crypto-onchain/scripts/run.sh --status         # 最近影子快照/今日候选数
./crypto-onchain/scripts/run.sh --fixtures crypto-onchain/tests/fixtures --data-dir /tmp/o --dry-run --json  # 离线回放
./crypto-onchain/scripts/verify.sh               # 模块级自检（ruff + libs + 本模块单测 + fixtures）

./crypto-knowledge/scripts/run.sh --status       # 学习进度/内容库存/下一条/待复习
./crypto-knowledge/scripts/run.sh --slot 13 --dry-run   # 预览 13:00 概念课（不写盘不推送）
./crypto-knowledge/scripts/run.sh --slot 21 --dry-run   # 预览 21:00 回顾与总结
./crypto-knowledge/scripts/run.sh --test         # Telegram 通道测试
python3 crypto-knowledge/scripts/check_content.py  # 课程内容校验（160 条）
```

## 4. 注意事项

1. 项目**必须**在 `~/vscode/crypto-research`：`~/Documents` 受 macOS TCC 保护，launchd 里的 bash 读不了。
2. 合盖休眠会暂停定时，唤醒后续跑；零成本本机跑法的固有取舍。price 每 60 秒一轮，唤醒后补齐；需要 7×24 按 Runbook 上 VPS。
3. 凭据统一在**仓库根 `.env`**；两个模块的 `scripts/common.py` 都把 `libs/crypto_core` 加入 `sys.path`（不用设 PYTHONPATH）。price 的行情源全部免 key。
4. 新需求 = 新目录（`crypto-<领域词>`），共享逻辑加进 `libs/crypto_core` 而不是复制；新模块记得：README、根 README 模块表、CI job、launchd label。
5. `crypto-jobs` 支持独立 Bot：`.env` 里加 `TELEGRAM_JOBS_BOT_TOKEN` / `TELEGRAM_JOBS_CHAT_ID`（也兼容 `TELEGRAM_JOBS_BOT_TOKEN` 等命名）即可用 独立 Bot 发报告（不填回退共享 Bot）；当前已配置 **独立 Bot**，日报/周报均从它发出；`scripts/notify.py --whoami` 可查当前 Bot。
6. 数据/日志不会无限增长：`tools/prune.py` 每天随首轮 collect 自动执行（raw 90d / logs 30d / reports 365d / alerts 180d / 单日志 2MB / data 总量 500MB 硬上限）。
6. price 的单实例锁是 `data/state/collect.lock`（目录锁 + pid），上一轮未结束会跳过（rc=4）；healthcheck 识别同一个锁并在 `--fix` 时清过期锁。
7. price 的告警阈值全部在 `crypto-price/config/rules.yaml`；交易对/关键位在 `watchlist.yaml`；轮询/数据源/日报在 `app.yaml`。改完跑 `./crypto-price/scripts/verify.sh`。
8. 历史会话存档已移除（公开仓库，避免包含个人信息）；**完全退出 DSH 再重开**后可在会话列表看到。

## 5. 下一步 TODO

1. ~~`crypto-price/`（P4，业务需求）~~ ✅ 2026-11-08 已完成：8 类规则 + 三源核验 + P0/P1/P2 送达治理 + 日报 + launchd（60s）+ 离线测试；见 [需求](crypto-price/requirement.md) / [设计](crypto-price/design.md) / [业务架构](crypto-price/architecture.md) / [技术架构](crypto-price/technical.md)。
2. 需要更多报表面板时再加 `crypto-analyst/`（消费 `crypto-news/data/exports/` 与 `crypto-price/data/exports/latest.json`）。
3. `crypto-news` 业务演进：Phase 4 早警指标、LLM 三行摘要、周报校准（见 [方案 §5–§8](crypto-intel-plan.md)）。
4. `crypto-price` 后续可选：CoinGlass 清算数据（付费后才准）、关键位自动校准、VPS 部署。
5. 数据体积变大后跑 `make prune module=<name> apply=1`（默认 dry-run）。
6. `crypto-jobs` 后续：把 `config/sources.yaml` 的源补到 20+（Lever/Ashby 全量）、薪资字段解析、技能共现分析；每月抽样「技能抽取为空」的 JD 迭代 `skills.yaml`。
7. `crypto-knowledge` 后续：课程内容按学习反馈迭代（新增条目直接追加 `config/curriculum/stage*.yaml`，跑 `check_content.py` + `build_index.py`）、可选加「周日一次本周回顾」、把本地 `learning/` 笔记目录（不入库）与模块进度联动；若某周实践被反复跳过，说明该条太难，需降级或拆分。
