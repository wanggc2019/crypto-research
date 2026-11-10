# 需求：crypto-x-kol —— 关注的 KOL 的 X 推文监控

> 状态：**已实现并运行**（2026-11-08）｜模块目录：[`crypto-x-kol/`](../../crypto-x-kol/README.md)
> 关联：[设计文档](design.md) · [业务架构图](architecture.md) · [技术架构](technical.md) · [约定](../conventions.md)

## 1. 要解决什么问题（一句话）

我关注的一批加密 KOL（预计 30 个以内）分散在 X 上、发帖频率不一，人工逐个刷成本高、容易漏；
需要自动抓取他们的新推文，按重要性排序后通过 Telegram 送达，并保留本地日报。

## 2. 边界：做什么 / 不做什么

### 做

- **白名单监控**：`config/kol.yaml` 里 enabled 的账号（默认 26 个），每 5 分钟抓一次 X 主页。
- **推文级解析**：把主页 Markdown 解析成一条条推文（正文、引用正文、是否有图/视频），
  丢弃置顶帖、转推、纯图片帖（没有可读文本）。
- **增量去重**：首轮只建基线（不把主页历史帖当新帖）；之后按「账号 + 正文归一化」指纹增量识别，
  同一推文只推送一次。
- **中文翻译 + 一句话总结**：外文推文用 DeepSeek 翻成中文，并给每条一句话总结；
  报告/告警只展示中文（原文留在 raw）；翻译过的推文标 **[翻译]**；无 key 时自动降级显示原文。
- **作者可识别**：每条推文展示 `@handle（name · 职业）`，职业来自 `kol.yaml` 的 `role`（如
  「Michael Saylor · Strategy 执行主席」），避免只看到账号名不知道是谁。
- **价值评分（决定推不推）**：每条新帖按「作者层级 + 主题（安全/监管/上币/资金/宏观…）+ 资产 +
  跨 KOL 同题 + 7 天新颖度 - 推广/日常/传闻」算 0–100 分，分级 P0/P1/P2/MUTE；
  **只有 P0/P1 推 Telegram**，P2/MUTE 只进日报（日报带分数与理由，可审计）。
- **送达与限流**：每日最多推 `alert.caps.per_day`（默认 8）条、单条消息最多 5 条；
  全天即时发送（设计约束不设夜间静默）。日报落 `data/reports/<date>.md`。
- **零成本**：匿名 `r.jina.ai`（20 RPM、免 key、不计 token）；不需要官方 X API。

### 不做

- **不做互动量分析**：X 匿名页面没有点赞/转发/浏览数、没有粉丝增量，也不提供发帖时间；
  这些需要官方 X API（按量计费）或第三方聚合 API，本模块不做。
- **不做搜索/发现**：不支持按关键词跨账号搜索、发现新 KOL、回溯历史推文（只能看到主页最近约 20 条）。
- **不做泛 KOL 全量**：只盯白名单；不自动关注、不采集回复/点赞/转推流。
- **不重复 crypto-news 的监控对象**：crypto-news 的 T2 高管发声（CZ、何一、Brian Armstrong、Ben Zhou、
  Jesse Powell）由 crypto-news 负责；本模块名单与它保持 0 交集，避免同一条推文双推。
  该约束由 `tools/tests/test_account_overlap.py` 自动校验。
- **不发帖、不交易、不预测价格**：纯信息监控。

## 3. 触发条件与等级（价值分，不是事件模型）

| 等级 | 条件（默认阈值，可在 rules.yaml 调） | 动作 |
|---|---|---|
| P0 | 价值分 ≥45 且（安全/监管类 或 ≥2 位 KOL 4h 内同题） | 立即推送（全天即时） |
| P1 | 价值分 ≥28 | 立即推送（全天即时） |
| P2 | 价值分 ≥10 | 只进日报 |
| MUTE | 推广/抽奖/GM 等降权后 <10 | 归档（日报仍保留，可审计） |

- 评分不含互动量（X 匿名页没有点赞/转发数），「热度」用**跨 KOL 同题**替代。
- 每日上限 `alert.caps.per_day`（默认 8）；P0 可绕过每日上限（`p0_bypass_cap`），但受单条消息上限约束。
- 同一推文只处理一次（`pushed-<date>.json` 记录 decision=alert/digest）。

## 4. 数据源 / 输入

| 来源 | 方式 | 频率 | 成本 | 备注 |
|---|---|---|---|---|
| X 主页 | `https://r.jina.ai/https://x.com/<handle>` | 300s/账号 | 0（匿名 20 RPM） | 免 key、不计 token；页面无时间戳/互动数 |
| 可选 `JINA_API_KEY` | 同上，带 `Authorization` 头 | 300s/账号 | 一次性 10M token，之后 $0.05/M | 仅提额到 500 RPM；默认关闭 |
| Telegram Bot | 仓库根 `.env` 的 `TELEGRAM_BOT_TOKEN/CHAT_ID` | 有新帖时 | 0 | 与 crypto-news 共用通道 |
| DeepSeek API | `https://api.deepseek.com/chat/completions`（`deepseek-chat`） | 每条新帖一次（批量 8 条/请求，带缓存） | 约几分钱/天 | 翻译 + 一句话总结；无 key 降级为原文 |

## 5. 验收标准（可测量）

1. `PYTHONPATH=libs/crypto_core/src python -m unittest discover -s crypto-x-kol/tests -v`：46 项全绿
   （解析/抓取/评分/日报/翻译五组，含 P0/P1/P2/MUTE 分级、同题、去重、上限、翻译解析/降级、作者信息用例）。
2. `PYTHONPATH=libs/crypto_core/src python crypto-x-kol/scripts/e2e.py`：输出
   `✅ crypto-x-kol e2e 通过（基线 → 增量 → 日报）`。
3. `./crypto-x-kol/scripts/run.sh --dry-run`：enabled 账号成功率 ≥ 90%（2026-11-08 实测 26/26）。
4. `make launchd module=crypto-x-kol action=status`：已加载；`crypto-x-kol/data/logs/collect.log`
   有 `start` / `done (rc=0)` / `采集完成：26 个源…`。
5. 首轮运行不推送历史帖；之后同一推文跨轮次不重复推送（`pushed-<date>.json`）。
6. 评分可复现：安全/监管突发类 → P0；上币/资金类 → P1；普通观点 → P2；抽奖/GM → MUTE
   （见 `tests/test_score.py` 与现场 dry-run）。
7. 每日推送不超过 `alert.caps.per_day`；全天即时发送（无夜间静默）。
8. 翻译/总结：外文推文报告与告警只展示中文、带 **[翻译]** 标记；同一条推文只调用一次 API（缓存命中）；
   无 `DEEPSEEK_API_KEY` 时降级为原文且不报错。
9. 作者信息：日报与告警都显示 `@handle（name · 职业）`；`kol.yaml` 的 `role` 缺失时至少显示 name。
10. 告警里的 @handle 链接指向**具体推文**（`/status/<id>`）；解析失败回退主页且不阻塞推送。
11. `make gate` 通过（本模块单测 + `scripts/e2e.py` 自动纳入）。

## 6. 依赖的共享能力

- `libs/crypto_core`：`paths/env/config/store/text/timeutil`、`http.get_text`、
  `notify.check_credentials/send_message`、`timeutil.in_quiet_hours`。
- 需要新增：DeepSeek API 凭据 `DEEPSEEK_API_KEY`（`.env` 或 `~/.dsh/.credentials.yaml` 的 refs）。

## 7. 风险与对策

| 风险 | 对策 |
|---|---|
| X 登录墙/网页限制（部分账号匿名不可读、403/429） | 记为 **SKIP**（不算失败、不影响 rc / healthcheck），`last_skipped` 留痕；全部 SKIP 时 rc=0、**不告警**，等下一轮；实测稳定登录墙的账号默认 `enabled:false`（如 @saylor） |
| 页面结构改版导致解析失败 | `ParseError` 分类 + 单账号失败继续；fixtures 单测锁住当前结构，改版时改 `fetch.py` 并补 fixture |
| 匿名 20 RPM 被限流 | 账号间 `min_interval_seconds: 4`（约 15 次/分钟）；26 个号一轮约 2 分钟 |
| 无发帖时间/互动数 | 明确用「首次发现时间」；数量级分析不在本模块范围内 |
| 抓取违反 X ToS | 仅个人低频研究；不公开分发、不转售；需要合规规模时改用官方 API |
| KOL 列表失效（改名/停用/少发） | 每轮状态带 `last_error`；配置文件可随时改，下一轮生效 |
| 推送刷屏 | P2/MUTE 不推；每日上限 8 条、单条消息 5 条；夜间只放行 P0 |
| 关键信息被误判为 P2/MUTE | 所有新帖都在日报里带分数/理由；调 `alert.thresholds`/`topics` 关键词即可，不用改代码 |
| 告警过多（阈值偏松） | 调高 `thresholds.P1`、调低 `weights.strong`，或收紧 `alert.caps.per_day` |
| 机器休眠漏跑 | launchd 唤醒后补跑；首轮会补推未推送条目 |
| DeepSeek 不可用/超时 | 该批翻译跳过，报告回退原文；缓存保证下轮只重试未翻译的推文 |
| 翻译误译/漏信息 | 原文保留在 raw；报告标注「AI 生成」；重要事件以原帖/官方为准 |
| 推文内容发送到第三方 API | 只发推文正文/引用给 DeepSeek，不发送凭据；介意可 `analysis.enabled: false` |
