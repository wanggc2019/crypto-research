# crypto-x-kol · 关注的 KOL 的 X 推文监控

> 仓库约定见 [../README.md](../README.md) 与 [../docs/conventions.md](../docs/conventions.md)；
> 完整文档见 [需求](../docs/crypto-x-kol/requirement.md) · [设计](../docs/crypto-x-kol/design.md) ·
> [业务架构](../docs/crypto-x-kol/architecture.md) · [技术架构](../docs/crypto-x-kol/technical.md)。

**一句话**：盯住 30 个以内的 KOL 的 X 主页，把「新出现的推文」解析成结构化条目，落日报并推送 Telegram。
数据源是 `r.jina.ai` 代理 X 主页（**匿名可用、免 API key、不消耗 token**），默认不用官方 X API。

## 运行

```bash
./scripts/run.sh                 # 抓一轮 + 写日报（不推送，安全默认）
./scripts/run.sh --notify        # 抓一轮 + 写日报 + 推送新帖
./scripts/run.sh --dry-run       # 只打印，不写 raw/state
./scripts/run.sh --test          # 发一条 Telegram 测试消息
./scripts/run.sh --only saylor,vitalik   # 只跑指定账号（调试/验证 handle）
./scripts/run.sh --date 2026-11-08       # 回放某天
```

调度（launchd，每 `app.yaml` 的 `poll_seconds`，默认 300 秒）：

```bash
make launchd module=crypto-x-kol action=install
make launchd module=crypto-x-kol action=status
make healthcheck module=crypto-x-kol
```

日志：`data/logs/collect.log`（start/done、「采集完成」行，供 `tools/healthcheck.py` 解析）。
日报：`data/reports/<YYYY-MM-DD>.md`；原始条目：`data/raw/<YYYY-MM-DD>/posts.jsonl`。

## 配置

| 文件 | 作用 |
|---|---|
| `config/kol.yaml` | **KOL 白名单**（`handle` 不带 @；`name`/`role` 作者信息；`enabled` 开关；T1/T2 排序权重） |
| `config/app.yaml` | 轮询间隔、请求超时/节流、`JINA_API_KEY` 开关、DeepSeek 翻译配置、推送（全天即时） |
| `config/rules.yaml` | 主题分组、评分权重、P0/P1 阈值、每日/单条上限、送达策略（见下方「怎么挑告警」） |

改名单只需编辑 `kol.yaml`，下一轮（≤5 分钟）自动生效；`handle` 写错会记录在
`data/state/kol-<handle>.json` 的 `last_error`，不影响其他账号。

## 抓取方式与已知限制

- 匿名 `r.jina.ai`：20 请求/分钟、**免费且不消耗 token**。本模块用 `min_interval_seconds: 4`
  节流（约 15 次/分钟），避免 26 个号在 1 分钟内打完触发限流。
- **JINA_API_KEY 默认不用**：key 只把限速提到 500 RPM，但会消耗一次性 10M token 额度
  （用完 $0.05/百万 token），且不会改善页面内容。只有账号数/频率明显变大，或要用 `s.jina.ai`
  搜索时才打开 `app.yaml` 的 `fetch.use_api_key`。
- X 匿名页面**没有发帖时间、点赞/转发数**；日报里的时间统一是「首次发现时间」（`first_seen`）。
- 部分账号会返回登录墙（匿名访问被 X 拦截）：这类**记为 SKIP（跳过）而不是失败**，不影响 rc
  （每轮稳定 `rc=0`、healthcheck 干净）；稳定登录墙的账号（如 `@saylor`）默认 `enabled: false`。
  只有 404/页面改版/网络错误才算失败，单账号失败不拖垮整轮。
- **合规**：抓取 X 页面属于 X ToS 的灰色地带，仅建议个人低频研究使用，不要做公开产品/批量转售。

## 怎么挑告警（价值分）

全部新帖都进日报，但**只有 P0/P1 才推 Telegram**（P2/MUTE 只进日报）：

| 等级 | 条件（默认阈值） | 动作 |
|---|---|---|
| P0 | 价值分 ≥45 且（安全/监管类 或 ≥2 位 KOL 4h 内同题） | 立即推送（全天即时） |
| P1 | 价值分 ≥28 | 立即推送（全天即时） |
| P2 | 价值分 ≥10 | 只进日报 |
| MUTE | 推广/抽奖/日常（GM）等降权后 <10 | 归档（日报里仍可审计） |

价值分（0–100，参数在 `config/rules.yaml` 的 `alert` / `topics` 段）：

```
作者层级 T1=18 / T2=10
+ 主题权重：安全/监管=30；上币/资金/宏观/升级=16；操作观点=8；突发词=10
+ 关注资产（BTC/ETH/BNB）+5 ｜ 引用 +2
+ 跨 KOL 同题：4h 内每多一位 +10（上限 +20）｜ 7 天内首次提该主题 +8
- 推广 -25 / 日常（GM）-18 / 传闻 -10
```

说明：
- X 匿名页面没有点赞/转发数，所以「热度」用**跨 KOL 同题**替代；
- 每日最多推 `alert.caps.per_day`（默认 8）条，单条消息最多 5 条，其余只进日报；
- 误报/漏报都靠 `thresholds` / `caps` / `topics` 关键词调，不用改代码；
- 日报里每条都带「级别 · 分数 · 理由」，方便回看为什么推/没推。

## 中文翻译 + 一句话总结（DeepSeek）

- 外文推文由 DeepSeek 翻成**中文**，每条再给一句**「他说了什么」总结**；
  报告和 Telegram 告警只展示中文译文 + 一句话，**不再展示英文原文**（原文保留在 `data/raw/<date>/posts.jsonl` 备查）。
- 翻译过的推文前面会标 **[翻译]**（中文原帖无此标记）；作者统一显示 `@handle（name · 职业）`，
  职业来自 `kol.yaml` 的 `role` 字段。
- Telegram 告警**保留链接预览**：消息下方会自动展开原帖卡片（预览的是 @handle 指向的具体推文）。
- 告警里的 @handle 链接指向**具体推文**（`https://x.com/<handle>/status/<id>`）：推送前按需抓一次
  X 主页 HTML 还原 status id；找不到时回退主页链接，不阻塞推送。
- 批量调用（默认每批 8 条）+ 按推文缓存（`data/state/analysis-<date>.json`），同一推文不重复付费；
  每天几十条约几千 token，成本可以忽略（DeepSeek 单价量级：几分钱/天）。
- **没有 key 时自动降级**：不报错，报告回退显示原文。key 优先级：
  `.env` 的 `DEEPSEEK_API_KEY` → `~/.dsh/.credentials.yaml` 的 `refs.DEEPSEEK_API_KEY`（与 crypto-news 巡检脚本一致）。
- 关闭：`app.yaml` 的 `analysis.enabled: false`，或单次 `digest.py --no-analyze`；`--dry-run` 不会调用 API。
- 注意：翻译/总结由 LLM 生成，可能误译或漏掉语气；以 raw 原文为准。

## 与 crypto-news 的边界（为什么保持两个模块）

| 维度 | crypto-news | crypto-x-kol |
|---|---|---|
| 目的 | 事件情报：过滤/聚类/评分 → P0/P1/P2 告警 | 观点流：白名单 KOL 的新推文 → 日报/推送 |
| 账号 | 5 个高管（CZ/何一/Brian Armstrong/Ben Zhou/Jesse Powell） | 26 个 KOL，**与高管名单 0 交集** |
| 处理 | 评分 S/T/M/C/D + 质量门 + 事件聚类 | 逐帖指纹 + 首轮基线 + 关键词排序 |
| 频率 | 600s | 300s |

两者共用 `r.jina.ai` 抓 X 的机制和 Telegram 通道，但账号集合、处理逻辑、产物互不重叠。
不合并的原因：KOL 推文属于「未确认观点」，不能进 crypto-news 的 P0/P1 事件池；频率也不同（300s vs 600s），
合并不省调度。`tools/tests/test_account_overlap.py` 会把「账号不重叠」变成硬约束，防止手滑双推。

## 测试

```bash
PYTHONPATH=libs/crypto_core/src python -m unittest discover -s crypto-x-kol/tests -v   # 44 项
PYTHONPATH=libs/crypto_core/src python crypto-x-kol/scripts/e2e.py   # 离线回放（fixtures）
```

`make gate` 会自动发现本模块的单测与 `scripts/e2e.py`（基线 → 增量 → 日报）。
