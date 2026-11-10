# 设计：crypto-policy —— 国内数字货币政策/法规/案例雷达 + 法规库 + 解读草稿

> 需求与验收见 [requirement.md](requirement.md)；业务架构图见 [architecture.md](architecture.md)。
> 模块代码：[`crypto-policy/`](../../crypto-policy/README.md)

## 1. 总体方案

```
权威源列表页 ──fetch──> 新条目? ──no──> 结束
                              │yes
                              ▼
                    详情页 → 正文/元数据/哈希 → data/docs/<id>.json
                              ▼
              规则门（classify）：类型/主题/文号/施行日/废止关系/等级
                              ▼
              ┌───────────────┼────────────────┐
              ▼               ▼                ▼
       data/index/policies  data/exports    data/pending
       （法规库/效力）       （跨模块契约）    （P0/P1 待解读）
              ▼
       P0 立即推送 / P1 当日汇总（brief + notify）
```

核心原则：**程序先过滤，AI 只处理候选**。AI 不扫描全量、不决定是否推送，
只对 `data/pending/` 里的 P0/P1 生成解读草稿。

## 2. 数据源与适配

| 源 | 列表选择器 | 详情 | 失败降级 |
|---|---|---|---|
| 央行·条法司 | `item_href_re: /tiaofasi/144941/3581332/\d{16,}/index\.html` | 通用 `<p>` 抽取 | 记 errors，继续其他源 |
| 央行·新闻 | `/goutongjiaoliu/113456/113469/\d{16,}/index\.html` | 同上 | 同上 |
| 外汇局·政策法规 | `/safe/20\d{2}/\d{4}/\d+\.html` | 同上 | 同上 |
| 证监会·规章库 | `/csrc/c106256/c\d+/content\.shtml` | 通用 `<p>`（133 段） | 限速 1.2s；`_2` 分页 302 不做 |
| 最高法·司法解释 | `/fabu/xiangqing/\d+\.html` | `<div class="txt">` 兜底 + div.title 标题 | 单源失败隔离 |
| 最高法公报·司法解释/案例 | `/Details/[0-9a-fA-F]{20,40}\.html` | 通用 `<p>` | 本机 302 循环，`enabled:false` 备用 |
| 国外监管源（SEC/CFTC/Fed/ECB/ESMA/FCA/FSA） | RSS（`crypto_core.sources.rss`） | 摘要 + 详情（限 2 篇/源） | 标题未命中 crypto 主题则忽略；规则制定 P0、执法/指引 P1、新闻稿 P2 |
| 美国 Federal Register | JSON API（多 query 合并、按日期） | 摘要 + 详情（限 1 篇） | Rule→P0 / Proposed Rule→P1 / Notice→P2；只取近 180 天 |
| 欧盟 EUR-Lex | HTML 检索页（`sortOne=date`） | 仅列表标题（详情页 JS 不可靠） | 标题命中 crypto 主题才进库 |

配置在 `config/sources.yaml`；新增源只改配置，不改代码。列表页用标准库 `HTMLParser` 解析 `<a>`，
`item_href_re` 负责把导航链接排除；日期优先从 URL 猜（PBC `YYYYMMDD...`、SAFE `/YYYY/MMDD/`），
详情页再覆盖。

## 3. 处理规则（规则门 G1）

| 步骤 | 规则 |
|---|---|
| 去重 | `id = sha256(source_id + "\|" + url)[:24]`，`state/source-<id>.json` 记录已见 |
| 基线 | 首次运行 `initialized=false`：全部标记 `baseline=true`，不推送 |
| 文档类型 | `rules.yaml` 正则按序：司法解释 → 法律 → 行政法规 → 部门规章 → 规范性文件 → 案例 → 解读 |
| 文号 | `doc_no_re`，如 `银发〔2026〕42号` |
| 施行日 | `effective_re`；`自发布之日起施行` 用发布日 |
| 主题 | `config/app.yaml` 的 `topics` 词表，标题命中优先、正文命中降级 |
| 等级 | 法律/行政法规/司法解释 → P0；部门规章/规范性文件标题命中高敏感主题 → P0，否则 P1/P2；案例 → P1；解读/新闻 → P2 |
| 废止关系 | `supersede_re` 兼容「文号+同时废止」与「废止+文号」两种写法 |
| 相关性 | 标题命中主题 → 进库；P0 允许正文命中；其余忽略 |

## 4. 输出与送达

| 产物 | 路径 | 契约 |
|---|---|---|
| 原始条目 | `data/raw/<date>/items.jsonl` | 追加，可回放 |
| 详情正文 | `data/docs/<id>.json` | 含 `title/url/body/body_hash/published_at` |
| 法规库 | `data/index/policies.json` | `{schema_version, updated_at, policies[]}`，含 `status/supersedes/superseded_by/version` |
| 跨模块导出 | `data/exports/policy-<date>.json` | `schema: policy/daily/v1` |
| 人读速报 | `data/reports/<date>.md` | 按 P0→P2 排序 |
| 待解读队列 | `data/pending/<id>.json` | 只含 P0/P1，供 DSH 任务消费 |
| 周报（手动） | `data/reports/weekly-<date>.md` | 最近 7 天法规库变化；无变化默认静默、不写不发；不排期 |

Telegram 卡片：标题/机关/文号/发布-施行日/主题/原文要点（最多 5 条）/「解读待生成」/原文链接；
P1 用汇总卡片，避免多条刷屏。

## 5. 配置

| 文件 | 作用 |
|---|---|
| `config/app.yaml` | `poll_seconds`（3600=1h）、单轮上限、主题词、`ignore_quiet_hours`（policy 默认 true）、卡片条数 |
| `config/sources.yaml` | 源清单、层级、列表 URL、链接正则 |
| `config/rules.yaml` | 类型/等级/文号/施行日/废止正则，高敏感主题 |

## 6. AI 解读（已上线）

- 模块只写 `data/pending/`，**不直接调用模型**；`interpret.py` 提供规则抽取兜底；
- `collect.sh` 检测到 `pending/` 非空后**立即**调用 `scripts/interpret.sh`（事件驱动，不另设定时任务）：
  pending 为空直接退出（零成本）；有则用 `dsh headless` 读 `data/docs/` 全文生成
  「效力/要点/影响/与旧规对比/风险」，写 `data/reports/interpret-<id>.md`，
  经 `push_text.py` 推送并归档 pending；
- 草稿标注「AI 草稿 · 非法律意见」，要求引用原文；人工复核后才算定稿；
- 无 `DEEPSEEK_API_KEY` 时 `interpret.py --no-llm` 仍可产出规则抽取草稿，产品不空转。

## 7. 测试方案

- 单测（`tests/test_core.py`，离线 fixtures）：列表解析、详情解析、42 号文分类、卡片、法规库废止关系；
- 端到端：`python3 scripts/e2e.py`（fixtures 回放 + 周报），`make gate` 自动发现；
- 联网冒烟：`./scripts/run.sh --dry-run --only pbc_tiaofasi`（可选，不写盘）。

## 8. 失败模式与对策

| 场景 | 表现 | 处理 |
|---|---|---|
| 单源失败 | errors 非空，其他源继续 | 退出码 1，日志留痕，下一轮重试 |
| 全源失败 | 无产出 | 退出码 3，不推送 |
| 静默时段 | policy 不启用（`ignore_quiet_hours: true`） | 24/7 即时推送；其他模块仍按各自策略 |
| 首次运行 | 无历史基线 | 只建基线，不推送 |
| 详情抓取失败 | 有标题无正文 | 仍按标题分类；P0 宽进，避免漏 |
| 页面改版 | 列表为空 | 记录 `last_error`，人工改 `sources.yaml` 正则 |
