# 设计：crypto-jobs —— 加密行业技术岗位市场情报

> 需求与验收见 [requirement.md](requirement.md)；业务架构图见 [architecture.md](architecture.md)；
> 模块内部实现见 [technical.md](technical.md)。代码：[`crypto-jobs/`](../../crypto-jobs/README.md)

## 1. 总体方案（一句话）

**每天全量抓一遍公开岗位 → 归一化 + 规则抽取技能 → 存当日快照 → 和上一份/7 天前对比 → 周报 + Telegram + exports。**

为什么是「每日全量」而不是「增量」：

- 目标岗位量级小（当前几百条），全量成本低（几十个请求/天）；
- 全量才能**可靠发现岗位关闭**（增量只能看到新岗位）；
- 每天同一时间取数，快照按天对齐，趋势口径稳定；
- 出问题时可从 `data/raw/<date>/` 回放，换词典重算历史。

```
sources.fetch_all ──► normalize.normalize_jobs ──► skills.extract
        (T0/T1/T2)         (角色/资历/远程/去重)        (规则词典)
              │                     │                      │
              ▼                     ▼                      ▼
        raw/<date>/jobs.jsonl   state/snapshots/<date>.json + jobs-index.json
                                      │
                               diff + metrics ──► reports/<date>.md
                                      │            reports/<date>-weekly.md
                                      ▼            exports/skill-report.json
                                  Telegram（周报）
```

## 2. 数据源与适配

### 2.1 选源原则（优先级从高到低）

1. **官方公开接口**：公司 ATS 的 job board API（Greenhouse/Lever/Ashby/Workable）——为招聘页嵌入设计，
   无需登录、无 key、结构化、字段稳定；
2. **加密垂直招聘站**：web3.career、cryptojobslist、remote3 等，信息公开且更新快，但需要 HTML/RSS 解析；
3. **通用远程招聘站**：RemoteOK/Remotive/Jobicy/Himalayas/WWR，覆盖面广但必须过 crypto 语境过滤；
4. **明确不碰**：LinkedIn / Indeed / Glassdoor（ToS 禁止 + 反爬对抗），任何需要登录/付费的站点。

每个源在 `config/sources.yaml` 里登记：`id / type / tier / company / url / parser / enabled / notes`。
`notes` 必须写清接口形态与合规依据；失败源保留在配置里（`enabled: false` 或自动 health 记录），
方便恢复与排查。

### 2.2 源分层与信任

| 层 | 含义 | 用途 | 排序/去重优先级 |
|---|---|---|---|
| T0 | 公司官方 ATS | 主样本（技能频率、公司活跃度） | 最高，跨源去重时保留 |
| T1 | 加密垂直招聘站 | 补充中小团队/非 T0 公司 | 次之 |
| T2 | 通用远程站（crypto 过滤后） | 补充远程岗位 | 最低，重复时丢弃 |

### 2.3 采集层行为

- 串行 + 每源 timeout 20s + 失败重试 1 次；单源异常只记 `source_health.error`，不中断整轮；
- 每源最多 `fetch.max_jobs_per_source` 条；JD 正文去 HTML 后截断 `description_max_chars`；
- 同一轮内按 `source_id + job_id` 去重；跨源按「公司 + 标题 + 地点」去重（T0 优先）；
- 每轮写 `source_health`（`ok/count/ms/error`），周报里展示源健康与失败原因。

### 2.4 合规与伦理（硬性）

- 只访问**公开可匿名读取**的招聘信息；遵守 `robots.txt` 与站点 ToS；不登录、不绕过验证码/风控；
- 请求频率极低（每源每日 ≤ 1 次全量），UA 如实、超时可控；
- 只存**岗位信息**，不存招聘方联系人个人数据；报告只做聚合统计，不整篇转载 JD；
- 源站要求删除/停止时，把该源 `enabled: false` 并记录原因。

### 2.5 送达凭据（可分离 Bot）

- 默认复用仓库根 `.env` 的 `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`；
- 本模块支持 `TELEGRAM_JOBS_BOT_TOKEN` / `TELEGRAM_JOBS_CHAT_ID` 覆盖（例如统一用 **独立 Bot** 发岗位周报/日报），不影响其他 crypto-* 模块；
- 用 `python3 scripts/notify.py --whoami` 可确认当前实际发送 Bot。

### 2.6 当前覆盖（2026-11-09 实测含开发岗位，随源变动）

| 项 | 实测值 |
|---|---|
| 启用源 | **40 个**（T0 35 / T1 2 / T2 3），全部为公开、免 key |
| 一轮原始岗位 | **1931 条**（T0 1760 / T1 116 / T2 55），40/40 源健康 |
| 目标岗位 | **457 条**（DevOps/SRE/MLOps/平台 + 开发岗位），覆盖 **57 家公司**（含 8 家 CEX + 4 家 DEX） |
| 技能词典 | **225 个技能 / 20 个类别**（见 `config/skills.yaml`） |
| 单轮耗时 | 约 40–120 秒（串行 40 请求 + 抽取） |

T0 覆盖：Coinbase / OKX / Bybit / Binance / Gemini / Kraken / Gate.io（交易所）；Fireblocks / Anchorage /
BitGo / Ledger / CertiK / Elliptic / TRM Labs / OpenZeppelin（托管与安全）；Alchemy / QuickNode / Blockdaemon /
Helius（基础设施）；Ripple / Consensys / Aptos / Mysten(Sui) / Offchain(Arbitrum) / Matter Labs(zkSync) /
Uniswap / Safe / Circle / Paxos / Morpho / Gauntlet / Immutable（协议与支付）。
点名但无公开 ATS 的公司（如 Chainalysis、Figment、Solana Labs、dYdX）不硬抓，改用同赛道可公开访问的公司补样本。

## 3. 数据契约

### 3.1 `job/v1`（归一化岗位，快照内）

```json
{
  "key": "greenhouse:coinbase:1234567",
  "source_id": "greenhouse:coinbase",
  "source_tier": "T0",
  "company": "Coinbase",
  "company_slug": "coinbase",
  "job_id": "1234567",
  "title": "Senior Site Reliability Engineer",
  "role": "sre",
  "seniority": "senior",
  "remote": "remote",
  "locations": ["Remote - USA"],
  "url": "https://...",
  "posted_at": "2026-11-01T...",
  "updated_at": "...",
  "employment": "full-time",
  "salary": null,
  "description_hash": "sha256[:16]",
  "skills": ["kubernetes", "prometheus", "terraform"],
  "required_skills": ["kubernetes"],
  "tags": []
}
```

> 快照**不存 JD 正文**（控制体积），正文在 `data/raw/<date>/jobs.jsonl`；`description_hash` 用于识别 JD 变更。

### 3.2 `jobs-snapshot/v1` / `jobs-index/v1`

- 快照：`{schema, date, generated_at, source_health, counts, jobs[]}`，每天一份；
- 索引：`{schema, updated_at, jobs: {key: {first_seen, last_seen, company, title, role, source_id}}}`，
  用于「本周新增」与关闭判定；首次建库不产生新增。

### 3.3 `skill-report/v1`（周报/日报/export 共用）

关键字段：`period{start,end,days,baseline_date}`、`overview{jobs,companies,new,closed,changed,remote_share,...}`、
`top_skills[{skill,label,category,count,share,required,delta}]`、`rising/falling/new_skills`、
`by_role`、`by_category`、`companies[{company, jobs, new_jobs, remote_share, roles, top_skills, sample_titles}]`、`notable_new_jobs`、`skill_history`、`source_health`、`taxonomy_size`。

### 3.4 `jobs-export/v1`（跨模块只读）

`schema/generated_at/source_module/date/counts/source_health/jobs[]`，供 `crypto-analyst` 等只读消费；
只增字段不改语义。

## 4. 技能抽取（核心算法）

### 4.1 词典结构（`config/skills.yaml`）

```yaml
schema_version: 1
categories:
  - {id: container, label: 容器编排}
skills:
  - id: kubernetes
    label: Kubernetes
    category: container
    aliases: [kubernetes, k8s, kube]
    regex: null          # 歧义词才需要显式正则
```

- 词典按「通用 DevOps/SRE/MLOps + 加密行业特有（节点/验证者/托管/低延迟撮合）」组织；
- 每个技能必须有稳定 `id`（趋势按 id 聚合）、`label`（展示）、`category`（聚合）、`aliases`；
- 另有一层 `drilldown`：把 AI / 可观测性 / 钱包 / dApp 等**宽口径大项**映射到具体子技能，日/周报里展开成「AI 327｜LLM 113、机器学习 75、RAG 38」这种形式（只影响展示聚合，不改变抽取）；
- `go / rust / R / c / ai / ml` 等短词必须显式 `regex` 或加语境，禁止裸别名（有反例单测）。

### 4.2 抽取流程

1. 输入 JD 纯文本（HTML 已清洗），小写化；
2. 对每个技能用「别名/正则」做词边界匹配，统计出现次数；
3. 识别 **Requirements / Qualifications / Must have / 任职要求 / 岗位要求** 段，
   段内命中的技能标记为 `required`（硬性要求），其余为提及；
4. 输出 `{skills, required, counts, by_category}`；同一岗位同一技能只计 1 次（用于频率口径）。

### 4.3 为什么用规则而不是 LLM

- 零成本、无网络依赖、结果确定，可写**回归测试**（换词典可重算历史）；
- 频率统计需要「可解释」：报告里能说清每个数字来自哪些技能词；
- 加密行业技能词相对固定，词典 + 别名足够；LLM 留作后续「JD 摘要/新兴技能发现」增强，不参与主口径。

### 4.4 词典维护

- 新增技能 = 改 `config/skills.yaml` + 补一条测试；
- 每月抽样 20 条「技能抽取为空」的 JD，人工看漏了什么，迭代别名；
- 词典版本随报告记录（`taxonomy_size`），避免跨版本直接比较。

## 5. 指标定义

| 指标 | 定义 | 注意 |
|---|---|---|
| 在招岗位 | 当日快照内、目标岗位（含开发）、技能≥1 的岗位数 | 样本量，不是全市场 |
| 技能提及岗位数 | 提到该技能的岗位数（同岗位多次算 1） | 主指标 |
| 技能占比 | 提及岗位数 / 在招岗位数 | 受样本构成影响 |
| 硬性要求数 | 在 requirements 段命中的岗位数 | 更接近「刚需」 |
| 周环比 | 当前快照 − baseline 快照（默认 7 天前） | baseline 缺失则首次建基线 |
| 新增 | `jobs-index.first_seen` 落在窗口内的岗位 | 首次建库 = 0 |
| 关闭 | baseline 有、当前无，且该源当轮健康 | 源故障不算关闭 |
| 升温/降温 | `abs(delta) ≥ rising_delta` 且基线 ≥ min_skill_count | 阈值在 app.yaml |
| 新出现技能 | 基线 0 次、当前 ≥2 个岗位 | 可能是新工具，也可能是词典误报 |

## 6. 输出与送达

| 产物 | 路径 | 说明 |
|---|---|---|
| 日报 | `data/reports/<date>.md` | 每日快照摘要（默认不推送） |
| 周报 | `data/reports/<date>-weekly.md` | 主产物：概览/技能 Top/趋势/分角色/公司/新岗位/方法 |
| Telegram 周报 | — | `--weekly --notify`：HTML 摘要（≤3800 字符；**开头 3–5 段「核心结论」**，随后交易所 CEX/DEX 速览、公司 Top5（带类型）、技能 Top10（含拆解/常搭配））+ 完整 Markdown 报告文件（sendDocument） |
| 机器可读 | `data/exports/skill-report.json`、`jobs-latest.json`、`company-stats.json` | 跨模块契约，滚动保留；`company-stats` 为每公司招聘/角色/技能统计 |
| 新岗位速报 | — | 事件触发：每 2 小时扫描，发现新增目标岗位立即推 Telegram（`--alerts`） |
| 运行状态 | `data/state/last_run.json`、`data/logs/crypto-jobs.log` | 健康检查消费 |

送达策略：周报每周日 10:00 推（摘要 + 完整 Markdown 附件）；同一类型同一日期已成功推送过则跳过重发（`--force` 强制），推送记录在 `data/state/push-log.json`。
日报每天 18:00 推送（`daily_digest: false` 可关闭；可用 `--daily-digest` 手动补发）。**不做夜间/休息时间静默**：消息按时发送，是否查看由用户决定。

## 7. 配置

| 文件 | 作用 |
|---|---|
| `config/app.yaml` | 采集/过滤/报告阈值/送达/快照保留 |
| `config/roles.yaml` | 目标角色、排除词、资历规则 |
| `config/skills.yaml` | 技能分类法与别名/正则（核心资产） |
| `config/sources.yaml` | 源注册表（id/类型/tier/url/parser/notes） |
| `config/companies.yaml` | （可选）加密公司注册表：slug ↔ 显示名/ATS，供 T2 过滤与展示 |

## 8. 测试方案

- **单测（离线）**：角色/资历/远程归一化、跨源去重、快照差分（含源故障不算关闭）、索引窗口、
  指标口径、周报/日报渲染、Telegram 长度控制；技能抽取的歧义反例。
- **fixtures 回放**：每个 adapter 保存 4–6 份真实响应（裁剪），`--fixtures` 全链路零网络。
- **e2e（CI）**：`scripts/e2e.py` 回放两个日期 + 一次周报，断言快照/索引/日报/周报/exports 生成。
- **联网冒烟（可选）**：`./scripts/run.sh --dry-run --json`（不在 gate 默认跑，避免外部依赖）。

## 9. 失败模式与对策

| 场景 | 表现 | 处理 |
|---|---|---|
| 某源 404/改版 | 该源 count=0 | health 记 error，其余源继续；周报「失败源」折叠块 |
| 全源失败 | jobs=0 | 退出码 3，不写快照（避免把空快照当成「岗位全关」） |
| 源临时抖动 | 当天岗位变少 | 关闭判定要求源健康；连续 2 天未出现才计关闭 |
| JD 正文缺失 | 技能抽不出 | 岗位仍保留（字段为空），报告里 `min_skills` 过滤后不计入频率 |
| 词典误报 | 某技能异常飙升 | 反例单测 + 周报里同时看「硬性要求」口径交叉验证 |
| 快照损坏 | 读取失败 | `safe_json_load` 容错，按首日基线处理并告警（TODO） |
| 数据量增长 | 磁盘上涨 | 快照 400 天自清理 + `tools/prune.py` 每日跑 |
| 机器休眠错过 18:00 | 当天无快照 | launchd 唤醒后补跑；周报用「≤今天最近一份」快照 |

## 10. 演进路线

| 阶段 | 内容 | 状态 |
|---|---|---|
| P0 MVP | T0 ATS 采集 + 技能词典 + 快照/差分 + 周报 + Telegram | ✅ 本期 |
| P1 数据质量 | 更多 ATS（Lever/Ashby/Workable 全量）、薪资字段解析、required 权重、源健康告警 | 待办 |
| P2 分析增强 | 技能共现网络（哪些技能常一起出现）、公司/角色对比、LLM 只做「新岗位一句话摘要」 | 待办 |
| P3 展示 | 可选静态 dashboard（读 exports）或并入 `crypto-analyst` 周报 | 可选，非目标 |
