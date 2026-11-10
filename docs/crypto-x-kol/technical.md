# 技术架构：crypto-x-kol（模块内部）

> 业务架构见 [architecture.md](architecture.md)；需求见 [requirement.md](requirement.md)；实现见 [design.md](design.md)。
> **仓库级**技术架构见 [../architecture.md](../architecture.md)。

## 1. 模块结构

```
crypto-x-kol/
├── README.md
├── config/
│   ├── app.yaml        # 轮询/节流/推送/静默时段
│   ├── kol.yaml        # KOL 白名单（handle/enabled/tier/tags）
│   └── rules.yaml      # 主题分组/评分权重/阈值/每日上限/⚡ 关键词
├── scripts/
│   ├── common.py       # 薄适配层：sys.path + 路径/配置/JSON/日志
│   ├── fetch.py        # 抓取 r.jina.ai → 解析 → 基线/去重 → raw/state
│   ├── score.py        # 价值评分：主题/层级/资产/同题/新颖度 → P0/P1/P2/MUTE
│   ├── analyze.py      # DeepSeek 中文翻译 + 一句话总结（批量/缓存/降级）
│   ├── xlink.py        # 告警前按需抓 HTML 还原原帖 /status/<id> 链接（失败回退主页）
│   ├── digest.py       # 读 raw → 评分 → 日报 → 只推 P0/P1（去重/上限/静默）
│   ├── e2e.py          # 离线端到端（fixtures 回放，gate 自动跑）
│   ├── run.sh          # 参数拆分：fetch / digest 共用一轮
│   └── collect.sh      # launchd 入口：锁 + run.sh --notify + healthcheck + prune
├── tests/              # 34 项离线单测 + fixtures（X 页面 Markdown）
├── deploy/launchd/     # agent.plist.tmpl（StartInterval=__INTERVAL__）
└── data/               # raw/state/reports/logs/exports（gitignored）
```

单文件职责：`fetch.py` ~360 行、`digest.py` ~330 行、`common.py` ~110 行，均 <500 行；
共享逻辑（HTTP/YAML/JSON/时间/文本/Telegram）全部走 `libs/crypto_core`，模块内不复制。

## 2. 组件职责与数据契约

| 阶段 | 文件 | 输入 | 输出 |
|---|---|---|---|
| 入口 | `run.sh` | CLI 参数 | 依次执行 fetch + digest；参数按 `--only/--limit/--fixtures`（fetch）、`--notify/--test`（digest）拆分 |
| 调度入口 | `collect.sh` | launchd 触发 | 加锁 → `run.sh --notify` → healthcheck → 每日 prune；写 `data/logs/collect.log` |
| 抓取 | `fetch.py` | `kol.yaml`、`app.yaml`、`r.jina.ai`/fixtures | `raw/<date>/posts.jsonl`、`state/kol-<handle>.json`、`logs/fetch-<date>.json` |
| 解析 | `fetch.parse_page` | 主页 Markdown 字符串 | `[{handle,text,quote,has_media,fingerprint}]` |
| 去重 | `fetch.diff_posts` / `_mark_seen` | 解析结果 + state | 新帖列表 / 基线标记 / 更新后的 seen |
| 翻译/总结 | `analyze.analyze` / `localize` | 待处理 posts + DeepSeek API | `state/analysis-<date>.json`（id → 中文/总结/是否翻译） |
| 评分 | `score.evaluate` | 当日 posts + 最近 N 天 raw + rules | 带 score/level/topic/reasons 的 posts |
| 报告 | `digest.build_report` | 评分后的 posts | `reports/<date>.md`（全部新帖可审计） |
| 原帖链接 | `xlink.attach` | 选中的 P0/P1 + X 主页 HTML | `post["tweet_url"]`（失败回退主页） |
| 推送 | `digest.build_alert_message` / `run` | 仅 P0/P1 + 每日上限/静默 | Telegram HTML；`pushed-<date>.json`（decision=alert/digest） |
| 回放 | `e2e.py` | `tests/fixtures/` | 临时 data 目录中的 raw/report（不联网、不推送） |

## 3. 运行链路

```
launchd(StartInterval=300) → scripts/collect.sh（mkdir 锁，防重叠）
  → ./scripts/run.sh --notify
      → fetch.py（逐账号，间隔 4s；失败单账号记录）
      → digest.py（读 raw → 日报 → 待推送 → 静默判断 → Telegram）
  → tools/healthcheck.py --module crypto-x-kol --quiet --notify --fix --agent
  → tools/prune.py --module crypto-x-kol --apply（当天首轮）
  → 写 collect.log：start / 采集完成 / done(rc=N)
```

退出码（run.sh 取 fetch/digest 的较大者）：`0` 正常；`1` 部分账号失败/推送失败；
`2` Telegram 凭据错误；`3` 全部账号失败。

## 4. 依赖（libs/crypto_core）

| 能力 | 模块 |
|---|---|
| HTTP GET（浏览器 UA、超时、重试） | `crypto_core.http.get_text` |
| 配置读取（PyYAML，缺失时 ruby 兜底） | `crypto_core.config.load_yaml` |
| `.env` 凭据 | `crypto_core.env.load_env` |
| JSON 原子读写 / JSONL | `crypto_core.store.safe_json_load/safe_json_dump/append_jsonl/read_jsonl` |
| 文本归一化 / 哈希 | `crypto_core.text.norm_title/sha256` |
| 时间 / 静默时段 | `crypto_core.timeutil` |
| Telegram | `crypto_core.notify.check_credentials/send_message` |
| 路径 | `crypto_core.paths.ensure_dirs` |

## 5. 状态与数据

| 路径 | 内容 | 上限/保留 |
|---|---|---|
| `data/state/kol-<handle>.json` | `initialized`、`seen`（指纹→时间）、`last_checked`、`last_ok`、`last_error` | seen ≤2000（滚动裁剪） |
| `data/state/pushed-<date>.json` | 当日处理决策 `{post_id: {decision: alert|digest, ts, score, level}}` | 按天；由 prune 清旧日（state 目录 date 文件 180d 规则只匹配 alerts-*，故长期保留，增长很小） |
| `data/state/analysis-<date>.json` | 中文翻译/一句话总结缓存 `{id: {lang, zh, summary, ts, model}}`；`localize()` 据此返回是否 [翻译] | 按天；几十条/天，体积很小 |
| `data/raw/<date>/posts.jsonl` | 新帖完整记录 | 90 天 |
| `data/reports/<date>.md` | 日报 | 365 天 |
| `data/logs/collect.log` | 每轮 start/done/采集完成 | 30 天 + 单文件 2MB |
| `data/logs/fetch-<date>.json` | 每轮逐账号统计 | 30 天 |

- 账号状态文件名用小写 handle：`kol-<handle.lower()>.json`。
- 指纹只由「handle + 归一化正文 + 归一化引用」决定，图片/视频/blob URL 变化不影响去重。

## 6. 测试

| 文件 | 覆盖 |
|---|---|
| `tests/test_parse.py`（8 项） | 置顶/纯图/转推过滤、引用拆分、Markdown 清洗、指纹稳定性、登录墙/404/缺 posts 区 |
| `tests/test_fetch.py`（9 项） | 首轮基线 → 增量、同页重复去重、部分失败 rc=1、全失败 rc=3、登录墙 SKIP/全跳过 rc=3、state、role 落盘 |
| `tests/test_digest.py`（13 项） | 日报结构、告警截断/转义、每日/单条上限、pushed 状态、中文替换、[翻译]/作者信息 |
| `tests/test_score.py`（11 项） | P0/P1/P2/MUTE 分级、critical/strong、同题加成、新颖度、推广/GM/传闻扣分、英文边界 |
| `tests/test_analyze.py`（8 项） | 中文判定、请求构造、模型输出解析容错、批量/缓存、无 key/API 失败降级、翻译标记 |
| `tests/test_xlink.py`（6 项） | HTML article/status 解析、正文匹配、同 handle 单次抓取、失败静默回退、开关 |
| `scripts/e2e.py` | fixtures：基线 → 新增 1 条 → 评分 → 日报生成；gate 自动执行 |

运行：`PYTHONPATH=libs/crypto_core/src python -m unittest discover -s crypto-x-kol/tests -v`。

## 7. 失败模式

| 场景 | 表现 | 处理 |
|---|---|---|
| 账号登录墙 | 该账号 `SKIP`、`last_skipped` | 不计失败、rc 不受影响（每轮稳定 0）；连续登录墙可 `enabled:false` |
| 账号 404/改版/网络错误 | 该账号 `ERR`、`last_error` | 继续其他账号；rc=1（部分失败） |
| 全部账号失败/全部登录墙 | rc=3 | healthcheck FAIL → 告警（--notify） |
| 匿名 20 RPM 限流 | 间歇性空页/登录墙 | `min_interval_seconds=4`；必要时临时开 JINA_API_KEY |
| 推送失败 | rc=1，pushed 未标记 | 下轮重试 |
| DeepSeek 失败/无 key | 该批无中文 | 降级原文、下轮重试；不影响告警 |
| 原帖链接解析失败 | 告警链回退主页 | 静默处理，不阻塞推送 |
| 误把重要推文判成 P2/MUTE | 日报仍保留分数/理由 | 调 `rules.yaml` 的 thresholds/topics；不需要改代码 |
| 告警过多 | 每轮最多 5 条、每日 8 条 | 调高 thresholds 或收紧 caps |
| 凭据错误 | rc=2 | 提示检查根 `.env` |
| 锁残留 | collect.sh 跳过 | healthcheck `--fix` 30 分钟后自动清理 |
| data 膨胀 | >500MB | collect.sh 首轮 prune；healthcheck disk 检查 |
| 休眠漏跑 | 无日志 | launchd 唤醒补跑；待推送不丢 |
