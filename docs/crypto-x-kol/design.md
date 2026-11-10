# 设计：crypto-x-kol —— 关注的 KOL 的 X 推文监控

> 需求与验收见 [requirement.md](requirement.md)；业务架构图见 [architecture.md](architecture.md)。
> 模块代码：[`crypto-x-kol/`](../../crypto-x-kol/README.md)

## 1. 总体方案

```
每 300s：
  kol.yaml 白名单 ──> fetch.py ──> r.jina.ai 抓 X 主页（4s 间隔）
                                   └─> 解析成推文（正文/引用/媒体/指纹）
                                   └─> 首轮基线？是→只记 seen，不产出
                                   └─> 否→新指纹落 data/raw/<date>/posts.jsonl
  digest.py ──> 读当日 raw ──> 高信号排序 ──> data/reports/<date>.md
             └─> 只推 P0/P1（每日上限；全天即时）；P2/MUTE 只进日报
```

- 数据源：`r.jina.ai` 匿名代理（20 RPM、免 key、不计 token）；默认不接官方 X API。
- 去重单位是**推文指纹**（不是整页 hash）：X 页面里的 `blob:` 视频地址/图片地址每次抓取会变，
  整页 hash 会假变更，因此解析后按「账号 + 正文 + 引用」归一化再哈希。
- 首轮基线：`state.initialized=false` 时只记录 seen，不产出新帖，避免把主页历史帖一次性刷屏。

## 2. 数据源与适配

| 源 | 接口 | 解析函数 | 失败降级 |
|---|---|---|---|
| X 主页 | `https://r.jina.ai/https://x.com/<handle>` | `fetch.parse_page` | 登录墙/404/改版 → `ParseError`，仅该账号失败，`last_error` 落 state |
| Telegram | `crypto_core.notify.telegram.send_message` | `digest.build_message` | 发送失败 rc=1，不标记已推送，下轮重试 |
| 离线回放 | `fetch.py --fixtures DIR`（`DIR/x.com/<handle>.md`） | 同上 | 无网络、无推送，供单测/CI |

实测（2026-11-08，匿名 jina）：26 个 enabled 账号 26/26 可解析；`@saylor` 稳定登录墙已默认关闭；
`@woonomic`、`@Phyrex_Ni`、`@CryptoDonAlt` 为失效 handle，已替换为 `@willywoo`、`@PhyrexNi`、`@CryptoCred`。

## 3. 处理/算法/规则

### 3.1 页面解析（`fetch.parse_page`）

```
page ──> 定位 "## <name>'s posts" 行，截到页脚（登录墙/What's happening/© 20xx）
     ──> 按行首 "*   " 切块
     ──> 丢弃块首为 Pinned / Reposted 的块
     ──> 找头像标记 "[![Image N: ..](pbs.twimg.com/profile_images/..)](https://x.com/<h>)"
         · 第一个头像的 handle != 当前账号 → 丢弃（转推/他人内容）
         · 第二个头像 → 之后是引用正文，之前是本人正文
     ──> 清洗 Markdown：去图片/视频/头像；链接保留可见文字；压缩空白
     ──> 正文为空（纯图/纯视频）→ 丢弃
     ──> fingerprint = sha256("<handle>|<norm_title(正文)>|<norm_title(引用)>")[:24]
```

- `norm_title` 来自 `crypto_core.text`：小写 + 去标点/空白，跨图片/URL 变化保持稳定。
- 解析结果：`{handle, text, quote, has_media, fingerprint}`。
- `ParseError` 分三类：找不到 posts 区、命中登录墙、账号 404/不存在。

### 3.2 增量与状态（`fetch.diff_posts` / `_mark_seen`）

- `state.initialized=false` → 返回 `([], baseline=True)`，把当前所有指纹写入 `seen`。
- 否则 `fresh = [p for p in parsed if p.fingerprint not in seen]`，随后把 parsed 全部标记 seen。
- `seen` 上限 2000，超出后按时间保留最近一半（防状态文件膨胀）。
- 落盘记录：`{id, kind:"kol_post", date, handle, name, tier, tags, text, quote, has_media,
  fingerprint, url, first_seen, fetched_at}`；`id = sha256(handle|fingerprint)[:24]`。

### 3.3 价值评分与告警策略（`score.py` / `digest.py`）

**为什么不是「全推」**：30 个 KOL 一天几十条推文，全部推送会变成噪音源。评分只用页面里可得的信号
（作者层级/主题/资产/同题/新颖度），**不含互动量**（X 匿名页没有）。

```
score = 作者层级(T1 18 / T2 10)
      + 主题权重（critical 30 / strong 16 / action 8 / breaking 10）
      + 资产关注 +5 + 引用 +2
      + 跨KOL同题（4h 内每位 +10，上限 +20）
      + 7 天内首次提该主题 +8
      - 推广 -25 / 日常 GM -18 / 传闻 -10
      → clamp 0..100
```

- 主题分组在 `rules.yaml` 的 `topics`：`security`/`regulation` 是 critical（可升 P0），
  `listing`/`flows`/`macro`/`upgrade` 是 strong；`action`/`breaking` 加权；
  `promo`/`gm`/`rumor` 扣分。英文词按单词边界匹配（避免 `SEC` 命中 `second`）。
- 资产提及**不参与**同题/新颖度（否则 BTC 日常讨论会互相触发），只加 5 分。
- 分级：P0 = 分数 ≥45 且（critical 或 consensus≥2）；P1 = ≥28；P2 = ≥10；否则 MUTE。
- 推送：只有 P0/P1 推 Telegram；P2/MUTE 只进日报。每日上限 `alert.caps.per_day`（默认 8），
  单条消息上限 `alert.caps.max_per_message`（默认 5）；P0 可绕过每日上限（`p0_bypass_cap`）。
- 全天即时发送（用户 2026-11-10 要求不设夜间静默）；P0/P1 去重状态
  `data/state/pushed-<date>.json = {date, ids:{post_id: {decision: alert|digest, ts, score, level}}}`。
- 首次运行/历史上下文：评分用最近 `novelty_days+1` 天的 raw 计算「同题」与「首次提」；
  基线帖不进 raw，所以监控初期的新颖度加分偏多（可接受，`novelty` 权重可调）。

### 3.4 中文翻译与一句话总结（`analyze.py`）

- 外文推文发给 DeepSeek（`deepseek-chat`，OpenAI 兼容 `/chat/completions`），要求返回：
  `{id, lang, zh（中文翻译）, summary（一句话总结）}`；中文推文不翻译、只生成 summary。
- **批量**：默认 8 条/请求，减少延迟与费用；**缓存**：`data/state/analysis-<date>.json` 按 post id 存
  `{lang, zh, summary, ts, model}`，同一条推文只付费一次。
- **降级**：没有 `DEEPSEEK_API_KEY`（`.env` → `~/.dsh/.credentials.yaml` refs）时跳过，报告/告警回退原文，不报错；
  单批 API 失败只影响该批，下一轮重试（缓存未写入）。
- **呈现**：报告与 Telegram 告警只展示中文译文 + `一句话：…`；英文原文不再展示（raw 保留）。
  翻译过的推文前缀 **[翻译]**（中文原帖不加）；作者统一显示 `@handle（name · 职业）`，
  name/role 来自 `kol.yaml`（旧 raw 在 digest 运行时从配置补齐）。
- 安全边界：只把推文正文/引用发给 DeepSeek，不发送任何凭据；介意可在 `app.yaml` 关闭。

## 4. 输出与送达

| 产物 | 路径 | 契约 |
|---|---|---|
| 原始新帖 | `data/raw/<date>/posts.jsonl` | 追加 JSONL，一行一条推文（可回放） |
| 日报 | `data/reports/<date>.md` | 每日一份，含 ⚡ 高信号 + 按账号全部新帖 |
| 账号状态 | `data/state/kol-<handle>.json` | `{initialized, seen, last_checked, last_ok, last_error}` |
| 推送去重 | `data/state/pushed-<date>.json` | `{date, ids:{post_id: {decision: alert|digest, ts, score, level}}}` |
| 翻译缓存 | `data/state/analysis-<date>.json` | `{date, model, items:{post_id: {lang, zh, summary, ts}}}` |
| 运行统计 | `data/logs/fetch-<date>.json`、`data/logs/kol.log`、`data/logs/collect.log` | 供 healthcheck/prune |
| 跨模块导出 | — | 暂不提供（后续要让 crypto-analyst 消费再加 `data/exports/`） |

告警链接：推送前对**选中要发的** P0/P1 按需抓一次 X 主页 HTML（`X-Respond-With: html`），
用 `<article>` 里的 `/status/<id>` + 正文匹配还原原帖链接（`xlink.py`）；失败回退主页链接。消息里 @handle 指向原帖，并附「🔗 原帖」链接便于核对。

Telegram 卡片（仅 P0/P1，保留链接预览：消息下方自动展开原帖卡片）：「🔔【crypto-x-kol】高价值推文 N 条（P0 a · P1 b）」+ 每条（P0 用 🔴、P1 用 🟡）
`@handle · HH:MM · 等级 分数` + 截断正文 + 理由（安全事件/同题/首次提…）；超出单条上限的部分只进日报。

## 5. 配置

| 文件 | 作用 |
|---|---|
| `config/kol.yaml` | KOL 白名单（handle/name/role/tier/tags），默认 26 个 enabled + 2 个 disabled（@saylor、@0xMert_） |
| `config/app.yaml` | `poll_seconds=300`；`fetch.timeout/retries/min_interval_seconds=4/page_retries=2`、`fetch.tweet_link*`（原帖链接）；`use_api_key=false`；`analysis.*`（DeepSeek 翻译/总结）；`digest.text_chars=140`、`digest.quiet_hold=false` |
| `config/rules.yaml` | `topics` 主题分组；`alert` 评分权重/惩罚/阈值/`caps`（每日 8、单条 5）；`high_signal`（⚡ 标记） |

## 6. 测试方案

- 单测（离线，57 项）：`crypto-x-kol/tests/`
  - `test_parse.py`（8）：置顶/纯图/转推过滤、引用拆分、Markdown 清洗、指纹稳定性、登录墙/404；
  - `test_fetch.py`（7）：首轮基线、增量 1 条、重复抓取去重、部分失败 rc=1、全失败 rc=3、状态、role 落盘；
  - `test_score.py`（11）：P0/P1/P2/MUTE 分级、同题加成、新颖度、推广/GM/传闻扣分、英文词边界；
  - `test_digest.py`（13）：日报/告警结构、HTML 转义、每日/单条上限、pushed 状态、中文替换、[翻译]/作者信息；
  - `test_analyze.py`（8）：中文判定、请求构造、响应解析容错、批量/缓存、无 key/失败降级、翻译标记；
  - `test_xlink.py`（6）：HTML article/status 解析、正文匹配、同 handle 单次抓取、失败静默回退、开关。
- 端到端：`make gate` 自动执行 `scripts/e2e.py`（fixtures 回放：基线 → 增量 → 日报）。
- 联网冒烟（手动）：`./scripts/run.sh --dry-run`（26 个号约 90s）。

## 7. 失败模式与对策

| 场景 | 表现 | 处理 |
|---|---|---|
| 单账号登录墙/网页限制 | 该账号 **SKIP**（`last_skipped` 留痕），不计失败、不影响 rc | 其余账号继续；连续登录墙的账号可在 `kol.yaml` 里 `enabled:false` |
| 单账号 404/改版/网络错误 | 该账号 `ERR`，`last_error` 落 state | 其余账号继续；rc=1（部分失败） |
| 全部账号被网页限制（登录墙/403/429） | `采集完成：... 成功 0 ... 跳过 26`，打印「等下一轮」，**rc=0** | 不告警，下一轮自动重试 |
| 全部账号真实错误 | `采集完成：... 成功 0 ... 失败 26`，rc=3 | healthcheck 标记 FAIL 并告警 |
| 匿名被限流/域名临时封禁 | Jina 返回 `AbuseAlleviationError`/403 | 按网页限制 SKIP、等下一轮；频繁时临时开 `JINA_API_KEY` |
| jina 页面缓存导致延迟 | 同一内容重复返回 | 指纹去重保证不重复推送；需要实时可加 `X-Cache-Tolerance` 头（未默认开启） |
| 推送失败 | rc=1，pushed 不标记 | 下一轮重试 |
| 机器休眠 | 该轮未跑 | launchd 唤醒补跑；待推送队列不丢 |
| 报告/日志膨胀 | data 增长 | collect.sh 每日 `prune --apply`（raw 90d/reports 365d/logs 30d/500MB 上限） |
| DeepSeek API 超时/限流 | 该批翻译缺失 | 跳过该批、下轮重试；报告回退原文，不影响告警主链路 |
| 原帖链接解析失败 | @handle 指向主页 | `xlink.attach` 静默回退，不阻塞推送；链接不是原帖时可在消息里人工点开主页找 |
| 无 DEEPSEEK_API_KEY | 报告没中文 | 优雅降级显示原文；补 key 后下一轮自动补齐（缓存按 id 补） |

## 8. 与 crypto-news 的边界

两个模块都通过 `r.jina.ai` 读 X、都推 Telegram，但职责不同，保持独立：

| 维度 | crypto-news | crypto-x-kol |
|---|---|---|
| 定位 | 事件情报（需确认/评分） | 观点流（不评分，只增量推送） |
| 账号 | 5 个高管（people.yaml） | 26 个 KOL（kol.yaml） |
| 账号交集 | **必须为空** | 由 `tools/tests/test_account_overlap.py` 保证 |
| 处理 | 聚类/评分/质量门/24h 去重 | 逐帖指纹/首轮基线/关键词排序 |
| 频率 | 600s | 300s |

- 为什么不在 crypto-news 里加 KOL：KOL 推文是「未确认观点」，塞进 T2 会污染 P0/P1 质量门；
  且二者频率不同（300s vs 600s），合并并不能减少调度。
- 唯一需要人工维护的边界：不要把 `people.yaml` 的 5 个 handle 加进 `kol.yaml`（测试会拦住）。
- 已知的间接重复：媒体转述 KOL 言论时，crypto-news 可能另发一条事件告警；属于低频、可接受，
  因为一个是「原帖观点」、一个是「被媒体确认的事件」。
