# 技术架构：crypto-jobs（模块内部）

> 业务架构见 [architecture.md](architecture.md)；需求见 [requirement.md](requirement.md)；实现见 [design.md](design.md)。
> **仓库级**技术架构见 [../architecture.md](../architecture.md)。

## 1. 模块结构

```
crypto-jobs/
├── scripts/
│   ├── common.py       # 薄适配层：路径 + libs 导出 + 配置/环境加载
│   ├── sources.py      # 采集层：各源 adapter → 原始 job dict + source_health
│   ├── normalize.py    # 归一化：角色/资历/远程/去重/过滤
│   ├── skills.py       # 技能抽取：词典加载 + 别名/正则匹配 + required 段
│   ├── snapshot.py     # 快照/索引：保存、加载、差分、保留清理
│   ├── metrics.py      # 指标：技能频率、周环比、角色/公司/分类分布、每公司招聘统计、历史序列
│   ├── report.py       # 渲染：Markdown 周报/日报 + Telegram HTML 摘要
│   ├── notify.py       # 送达：libs.notify 封装 + dry-run
│   ├── main.py         # CLI 入口：日更 / 周报 / dry-run / fixtures / test
│   ├── e2e.py          # 离线全链路回放（CI/gate 用）
│   └── run.sh / collect.sh / run_weekly.sh / alert.sh
├── config/
│   ├── app.yaml        # 采集/过滤/报告/送达/快照保留
│   ├── roles.yaml      # 目标角色 + 排除词 + 资历规则
│   ├── skills.yaml     # 技能分类法（核心资产）+ drilldown 宽口径拆解
│   ├── sources.yaml    # 源注册表（含 ToS/接口备注）
│   └── exchanges.yaml  # CEX / DEX 名单（交易所动态与对比）
├── tests/
│   ├── test_sources.py / test_skills.py / test_pipeline.py
│   └── fixtures/{sources,jobs}/   # 原始响应 + JD 样本（离线回放）
├── deploy/launchd/
│   ├── agent.plist.tmpl    # 报告任务：每日 18:00 日更 + 周日 10:00 周报
│   └── alert.plist.tmpl    # 事件速报：每 2 小时扫描新岗位
└── data/                   # raw/ state/ reports/ exports/ logs/（gitignored）
```

## 2. 组件职责与数据契约

| 阶段 | 文件 | 输入 | 输出 |
|---|---|---|---|
| 采集 | `sources.fetch_all` | `config/sources.yaml`、可选 fixtures | `(jobs[raw], health[])` |
| 归一化 | `normalize.normalize_jobs` | raw jobs + app/roles 配置 + 词典 | `(jobs[job/v1], stats)` |
| 抽取 | `skills.extract` | JD 纯文本 | `{skills, required, counts, by_category}` |
| 快照 | `snapshot` | job/v1 列表 | `jobs-snapshot/v1`、`jobs-index/v1` |
| 指标 | `metrics.build_report_data` | 当前/基线快照 + 索引 + 词典（含 `drilldown`） | `skill-report/v1`（top_skills[].children = 宽口径拆解；无拆解时 top_skills[].related = 常搭配技能；companies[].role_titles = 角色→样例题名） |
| 渲染 | `report.render_weekly` / `render_daily` / `render_telegram` | `skill-report/v1` | Markdown / HTML |
| 送达 | `notify.send_weekly` | HTML 摘要 + 周报 Markdown | Telegram 消息 + `sendDocument` 文件；`dry-run` 只打印 |
| 编排 | `main.run_daily` / `run_weekly` | CLI | 落盘产物 + 退出码 |

## 3. 运行链路

```
launchd（18:00 报告任务）
  └─ collect.sh（目录锁 data/state/collect.lock）
       ├─ run.sh --notify
       │    └─ main.py：fetch → normalize → skills → snapshot/diff → metrics → reports/exports
       │         └─ （默认）日报推送：delivery.daily_digest=true 且新增≥阈值（不做夜间静默）
       ├─ healthcheck.py --module crypto-jobs --quiet --notify --fix
       └─ prune.py --module crypto-jobs --apply（每日一次，幂等标记）

（周日 10:00 的 collect.sh 周报轮）
  ├─ run.sh                     # 先采集最新快照，不推日报
  └─ run.sh --weekly --notify   # baseline 7 天前 → metrics → 周报 md → Telegram

（每 2 小时 alert 任务，独立 launchd）
  └─ alert.sh（目录锁 data/state/alert.lock）
       └─ main.py --alerts --notify：fetch → normalize → 对比 data/state/alert-seen.json
            └─ 有新增目标岗位 → 立即推「新岗位速报」；无新增静默
```

## 4. 依赖（libs/crypto_core）

| 能力 | 用法 |
|---|---|
| `http.get` / `get_json` / `get_text` | 统一 UA、超时、重试（采集层） |
| `config.load_config` | 读 `config/*.yaml`（PyYAML 缺失时 ruby 兜底） |
| `store.safe_json_load/dump`、`append_jsonl` | 原子写快照/索引/导出（断电不写半截） |
| `timeutil.now/today_str/ts_iso` | 日期/时间 |
| `text.clean_text/norm_title/sha256` | JD 清洗、标题归一、哈希 |
| `env.load_env` + `notify.check_credentials/send_message` | Telegram 凭据与发送 |

> 模块间零 import；只通过 `data/exports/` 对外。

## 5. 状态与数据

| 路径 | 保留 | 说明 |
|---|---|---|
| `data/raw/<date>/jobs.jsonl` | 90 天（prune） | 原始**目标**岗位（含 JD 截断正文），换词典可回放；非目标岗位只计数不落盘 |
| `data/raw/<date>/source-health.json` | 90 天 | 每源成功/失败/耗时 |
| `data/state/snapshots/<date>.json` | 200 天（模块自清理） | 每日目标岗位 + 技能 |
| `data/state/jobs-index.json` | 常驻（有界） | first_seen/last_seen |
| `data/state/last_run.json` | 滚动 | 健康检查消费 |
| `data/reports/<date>.md` / `<date>-weekly.md` | 365 天 | 人读产物 |
| `data/exports/skill-report.json` / `jobs-latest.json` / `company-stats.json` | 7 天（prune） | 跨模块只读契约；`company-stats` = 每公司岗位/新增/远程/角色/技能/代表岗位 |
| `data/logs/crypto-jobs.log` / `collect.log` | 30 天、单文件 2MB | 运行日志 |

## 6. 测试

| 层 | 文件 | 内容 |
|---|---|---|
| 采集层 | `tests/test_sources.py` | 每种 adapter 解析、字段完整、crypto 过滤、失败降级、fixtures 回放 |
| 技能层 | `tests/test_skills.py` | 词典结构、别名命中、required 段、歧义反例 |
| 流水线 | `tests/test_pipeline.py` | 归一化/去重/差分/索引/指标/渲染/Telegram 长度 |
| 端到端 | `scripts/e2e.py` | fixtures 回放两个日期 + 周报，断言产物 |
| 联网冒烟 | 手动 | `./scripts/run.sh --dry-run --json`（不在 gate 默认跑） |

`make gate` 会自动发现本模块（有 `tests/` + `scripts/e2e.py`），CI 另加独立 job 跑单测与 e2e。

## 7. CLI 与退出码

| 参数 | 含义 |
|---|---|
| 无参数 | 跑一轮日更，不推送（安全默认） |
| `--dry-run` | 联网抓取 + 计算，只打印，不写盘不推送 |
| `--notify` | 按配置推送（周报/日报）；同类型同日期已成功推送过则跳过 |
| `--daily-digest` | 手动补发日报摘要（忽略 `daily_digest` 开关；仍受同日去重约束） |
| `--alerts` | 新岗位速报：只抓取并推新增目标岗位，不写快照/日报 |
| `--force` | 忽略「同日已推送」去重，强制重发 |
| `--weekly` | 周报模式（只读历史快照） |
| `--fixtures <dir>` | 离线回放（CI，禁止联网） |
| `--data-dir <dir>` | 覆盖 data 目录（测试/CI） |
| `--date <YYYY-MM-DD>` | 处理日期 |
| `--limit N` / `--only id1,id2` | 调试：限制单源条数 / 只跑指定源 |
| `--json` | 机器可读输出 |
| `--test` | Telegram 通道测试 |

退出码：`0` 成功 / `1` 部分源失败但有产出 / `2` 配置或凭据错误 / `3` 网络全挂 / `4` 锁占用（collect.sh 跳过）。

## 8. 失败模式

| 场景 | 表现 | 处理 |
|---|---|---|
| 单源失败 | health.error，count=0 | 继续；周报折叠展示失败源 |
| 全部源失败 | jobs=0 | rc=3；不写空快照（避免误判全关闭） |
| 快照缺失 | 周报无 baseline | 首次基线：不计算新增；`first_baseline=true` |
| 索引损坏 | 读不到 | 当作空索引，按首日基线处理 |
| Telegram 失败 | 发送返回错误 | 打印警告，不影响本地报告落盘 |
| 锁残留 | 下一轮跳过 | healthcheck `--fix` 清过期锁 |
| 机器休眠 | 当天未跑 | 唤醒后 launchd 补跑；周报取最近快照 |
