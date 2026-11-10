# 技术架构：crypto-policy

> 业务架构见 [architecture.md](architecture.md)｜设计见 [design.md](design.md)

## 1. 代码结构

```
crypto-policy/
├── config/
│   ├── app.yaml          # 轮询/上限/主题词/是否忽略静默
│   ├── sources.yaml      # 源清单 + 列表链接正则
│   └── rules.yaml        # 类型/等级/文号/施行日/废止正则
├── scripts/
│   ├── common.py         # 薄适配层：路径/配置/HTTP/存储/时间
│   ├── fetch.py          # 列表/RSS 解析、详情抽取、去重、基线、落盘
│   ├── classify.py       # 规则门：类型/主题/文号/等级/废止
│   ├── index.py          # 法规库 upsert / 状态 / supersedes
│   ├── brief.py          # 卡片与周报/解读文本渲染
│   ├── notify.py         # Telegram 发送封装
│   ├── main.py           # 编排入口（采集→分类→入库→导出→推送）
│   ├── interpret.py      # 解读草稿（规则抽取；有 key 时调 DeepSeek）
│   ├── push_text.py      # 把解读 Markdown 推送并归档 pending（DSH 任务用）
│   ├── interpret-prompt.md # DSH headless 解读任务卡
│   ├── interpret.sh      # DSH 解读入口（事件驱动，由 collect.sh 调用）
│   ├── weekly.py         # 周报（手动）
│   ├── e2e.py            # 离线端到端（gate 自动发现）
│   ├── run.sh            # 一键入口
│   └── collect.sh        # 定时任务入口（加锁 + --notify）
├── tests/
│   ├── fixtures/         # 真实页面快照（列表/详情）
│   └── test_core.py      # 14 个离线单测
└── deploy/launchd/agent.plist.tmpl
```

## 2. 组件职责

| 组件 | 输入 | 输出 | 说明 |
|---|---|---|---|
| `fetch` | `sources.yaml` | items + `data/docs` | 列表 `<a>` 解析、详情 `<p>` 抽取、URL 日期兜底、基线 |
| `classify` | item | record（`level/doc_no/topics/...`） | 纯规则、可单测 |
| `index` | record | `policies.json` | 文号为主键；版本 + 废止关系 |
| `brief` | record | HTML 卡片 / Markdown | 不调用模型；要点为规则抽取 |
| `notify` | HTML | Telegram | 凭据来自仓库根 `.env` |
| `main` | CLI | 报告/导出/推送 | `--dry-run` 无副作用；`--fixtures` 离线 |
| `weekly` | index | 周报 + 可选推送 | 仅手动按需；无变化静默；不排期 |

## 3. 运行链路

```
① collect.sh（launchd，每 6h，加锁）
  └─ run.sh --notify
       └─ main.py
            ├─ fetch.run（7 源，单源失败隔离 + backlog 补抓）
            ├─ classify.build_record × N
            ├─ index.upsert（状态 / 废止关系）
            ├─ exports + reports + pending
            ├─ 送达判断（不设静默，24/7 即时）
            └─ notify（P0 单条 / P1 汇总，跨源按文号去重）

② collect.sh 内部：pending 非空 → interpret.sh（事件驱动）
  └─ pending 为空 → 退出（零成本）；不设静默，立即推送
  └─ dsh headless 读 prompt → 写 interpret-<id>.md → push_text.py → 归档 pending
```

## 4. 状态与数据

| 路径 | 内容 | 保留 |
|---|---|---|
| `data/state/source-<id>.json` | 已见 id、基线、最近检查/错误 | 长期（小） |
| `data/state/notified.json` | 已推送 id | 长期（小） |
| `data/docs/<id>.json` | 正文（≤20k 字符）+ hash | 长期 |
| `data/index/policies.json` | 法规库 | 长期 |
| `data/raw/<date>/items.jsonl` | 原始条目 | 90 天（`make prune`） |
| `data/exports/policy-<date>.json` | 跨模块契约 | 7 天 |
| `data/reports/*.md` | 速报/周报 | 长期 |

## 5. 配置与调度

- `poll_seconds: 21600` 由 `tools/render_launchd.py` 读取，`make launchd module=crypto-policy action=install`；
- Label：`com.crypto-research.crypto-policy`；
- 凭据：仓库根 `.env` 的 `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`（与 crypto-news 共用）。

## 6. 测试

```bash
cd crypto-policy
PYTHONPATH=../libs/crypto_core/src python -m unittest discover -s tests -v
PYTHONPATH=../libs/crypto_core/src python scripts/e2e.py
```

- 单测覆盖：列表解析（PBC/SAFE）、详情解析、42 号文分类（P0/文号/施行日/废止）、卡片、法规库状态迁移；
- 全部离线，不联网、不写生产状态；`make gate` 自动发现本模块并跑单测 + `scripts/e2e.py`。

## 7. 失败与运维

| 现象 | 排查 |
|---|---|
| 某源 `found=0` | 页面改版 → 改 `sources.yaml` 的 `item_href_re` |
| `detail_fail` 高 | 详情页结构变化/反爬 → 用 `r.jina.ai` 兜底或加 headers |
| 推送失败 | 检查 `.env` 凭据、`--test` 验证通道 |
| 误报多 | 调 `config/app.yaml` 的 `topics` 或 `rules.yaml` 的 `high_topics` |
| 漏检 | 看 `last_error`、周报、`data/raw` 里的被忽略条目 |
