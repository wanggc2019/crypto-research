# 需求：crypto-jobs —— 加密行业技术岗位（DevOps / SRE / MLOps / 开发）市场情报

> 状态：MVP 已实现（2026-11-08）｜模块目录：[`crypto-jobs/`](../../crypto-jobs/README.md)
> 关联：[设计文档](design.md) · [业务架构](architecture.md) · [技术架构](technical.md) · [仓库约定](../conventions.md)

## 1. 要解决什么问题（一句话）

**加密行业的技术岗位（DevOps / SRE / MLOps / 平台基础设施 + 开发岗位）需求变化快、信息散落在几十个招聘站和公司 ATS 里**，
研究员/求职者靠人工刷页面既看不全、也看不出趋势；本模块把公开岗位采集回来，**自动抽取技能要求、按周对比**，
回答「市场上要哪些技能、哪些在升温、哪些公司在招人」。

不做会怎样：只能凭感觉判断技能热度（例如「Rust 在加密行业到底有多刚需」「MLOps 岗位是不是在变多」），
错过招聘信号（新团队、新业务线、新工具栈）且无法回溯。

## 2. 边界：做什么 / 不做什么

### 做

- 只采集**公开招聘信息**：公司官方 ATS（Greenhouse / Lever / Ashby / Workable 等）公开接口、
  加密垂直招聘站、带 crypto 标签的远程招聘站（详见 [design.md §2](design.md)）。
- 只保留**技术岗位**：DevOps、SRE、MLOps/AI Infra、Platform/Infrastructure，以及**开发岗位**
  （后端/前端/全栈/移动端/区块链与智能合约/数据/ML/AI 工程/嵌入式/交易系统/通用软件）；
  管理/销售/市场/法务/测试/安全运营等直接排除（可在 `config/roles.yaml` 调整）。
- 把岗位描述归一化为统一结构 `job/v1`，用**规则词典**抽取技能、区分「硬性要求 / 加分项」。
- 每天保存**在招岗位快照**，计算新增/关闭/描述变更，维护 `first_seen / last_seen` 索引。
- 每周生成**技能需求周报**：Top 技能、周环比、升温/降温/新出现、分角色技能、活跃公司、值得看的新岗位。
- 通过 Telegram 推送周报；把机器可读结果导出到 `data/exports/`，供 `crypto-analyst` 等模块只读消费。
- 持续监测：launchd 两个任务——报告任务每天 18:00 日更（推日报）、周日 10:00 周报（先采集再出报告）；**事件速报任务每 2 小时扫描一次，发现新增目标岗位立即推 Telegram**；每轮健康检查；数据自动清理。

### 不做

- **不抓 LinkedIn / Indeed / Glassdoor**（ToS 明确禁止自动化抓取 + 反爬对抗成本高）；
  不登录、不绕验证码、不伪造身份。
- **不做简历投递/求职匹配**；不采集候选人个人信息。
- **不做全市场普查结论**：样本来自配置的公开源，报告必须声明样本偏差，趋势看相对变化。
- **不做薪资预测**：ATS 薪资字段覆盖不稳定，先原样记录、暂不聚合结论。
- **不默认用 LLM 逐条读 JD**：分类与抽取用可解释的规则词典（零成本、可回归测试）；
  LLM 只作为后续可选增强（见 [design.md §10](design.md)）。
- 不做 Web 面板/数据库：个人项目，产物是 Markdown + JSON + Telegram。

## 3. 监测对象与分级

| 级别 | 条件 | 动作 |
|---|---|---|
| P0（立即关注） | 白名单公司出现目标岗位，或单周新增 ≥ 2 倍基线 | 周报中置顶；可选每日摘要（默认关闭） |
| P1（周报） | 技能周环比升温 ≥ 阈值、新出现技能（≥2 个岗位）、新增岗位 | 周报「趋势/新岗位」小节 + Telegram |
| P2（归档） | 其余岗位与技能变化 | 落 `data/reports/`、`data/exports/`，不推送 |

> 岗位变化是低频信号，默认**只推周报**，避免每天打扰；需要日推时把 `config/app.yaml`
> 的 `delivery.daily_digest` 改为 `true`。

## 4. 数据源 / 输入

| 类别 | 来源 | 方式 | 频率 | 成本 | 备注 |
|---|---|---|---|---|---|
| T0 官方 ATS | 加密公司 Greenhouse / Lever / Ashby / Workable board | 公开 JSON 接口（为招聘页嵌入设计） | 每日 | 0 | 结构化、字段全、最稳定 |
| T1 加密垂直站 | web3.career / cryptojobslist / remote3 等 | 公开 HTML/RSS（遵守 robots.txt） | 每日 | 0 | 补充 T0 覆盖不到的中小团队 |
| T2 通用远程站 | RemoteOK / Remotive / Jobicy / Himalayas / WWR | 公开 JSON API / RSS | 每日 | 0 | 必须过 crypto 语境过滤，否则噪音大 |

详细启用的源、每个源的接口/解析器/ToS 备注见 [`config/sources.yaml`](../../crypto-jobs/config/sources.yaml)
与 [design.md §2](design.md)。**不引入付费 API / 不买 VPS / 不抓受限站点。**

> 2026-11-09 实测（含开发岗位）：**40 个启用源**（T0 35 / T1 2 / T2 3）全部健康，一轮抓到 1931 条原始岗位，
> 目标岗位 **457 条**，覆盖 **57 家公司**；技能词典 225 个。

## 5. 持续监测的产物

| 产物 | 路径 | 用途 |
|---|---|---|
| 目标岗位原始数据（可回放） | `data/raw/<date>/jobs.jsonl`、`source-health.json` | 审计、换词典后重算 |
| 每日快照 | `data/state/snapshots/<date>.json` | 历史对比、周报数据源 |
| 岗位索引 | `data/state/jobs-index.json` | `first_seen/last_seen` → 新增/关闭判定 |
| 周报 | `data/reports/<date>-weekly.md` | 人读主产物（含全量公司招聘统计 + Top10 公司画像） |
| Telegram 周报 | — | `--weekly --notify`：HTML 摘要 + 完整 Markdown 报告附件（sendDocument） |
| 日报 | `data/reports/<date>.md` | 当日快照/差分 |
| 机器可读导出 | `data/exports/skill-report.json`、`jobs-latest.json`、`company-stats.json` | 跨模块只读契约（`company-stats` = 每公司岗位/角色/技能统计） |
| 运行汇总 | `data/state/last_run.json`、`data/logs/crypto-jobs.log` | 健康检查/排障 |

## 6. 验收标准（可测量）

1. **离线全绿**：`make gate` 通过（ruff + libs/tools/各模块单测 + crypto-jobs e2e 回放）。
2. **离线 e2e**：`python3 crypto-jobs/scripts/e2e.py` 退出 0，且生成快照/索引/日报/周报/exports。
3. **联网日更**：`cd crypto-jobs && ./scripts/run.sh --dry-run --json` 退出码 ∈ {0,1}，
   `overview.jobs > 0`，至少 3 个源健康（当前实测值见 HANDOFF）。
4. **周报**：`./scripts/run.sh --weekly --dry-run` 输出包含「技能需求 Top」「方法与局限」两节。
5. **词典可扩展**：新增一个技能到 `config/skills.yaml` 后，`tests/test_skills.py` 通过，
   周报里无需改代码即可出现该技能。
6. **合规**：`config/sources.yaml` 中每个源都有 `notes` 说明接口/ToS；代码里没有 LinkedIn/Indeed 抓取。
7. **数据不膨胀**：`tools/prune.py --module crypto-jobs`（collect.sh 每日跑）能清理 raw/logs/exports，
   快照按 `keep_days=400` 自清理。

## 7. 依赖的共享能力

- `crypto_core.http`：统一 UA / 超时 / 重试（采集层）
- `crypto_core.config` / `store` / `timeutil` / `text` / `env`：配置、原子写、时间、清洗、凭据
- `crypto_core.notify`：Telegram 发送 + 凭据校验（本模块只做卡片内容）
- 不依赖其他 `crypto-*` 模块（零 import）；只通过 `data/exports/` 对外提供契约。

## 8. 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| ATS 改版 / slug 失效 | 某源 0 条 | 单源降级 + `source_health` 记录；周报告警失败源；配置可快速改 slug |
| 反爬/限流 | 部分源偶发失败 | 每日一次、串行、UA/超时/重试 1 次；只选公开接口；失败不拖垮整轮 |
| 样本偏差 | 结论不代表全市场 | 报告显式声明覆盖源与样本量；用趋势而非绝对值 |
| 技能词典覆盖不足 | 低估某些技能 | 词典版本化 + 单测；从「未命中岗位」抽样迭代；报告标注词典规模 |
| 误报（技能词歧义） | 频率失真 | 短词用显式正则/上下文；反例测试固化在 `test_skills.py` |
| 岗位「关闭」误判 | 新增/关闭波动假象 | 关闭需该源当轮健康，且与 7 天前快照对比，避免单日抖动 |
| 数据量增长 | 磁盘占用 | 快照 400 天、raw 90 天（prune）、JD 截断 20k 字符 |
| 法律/合规 | 源方封禁 | 只公开数据、遵守 robots/ToS、不高频、不转载全文；报告只聚合 |
