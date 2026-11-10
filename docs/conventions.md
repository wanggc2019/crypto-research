# 仓库编码与运行约定（conventions）

> 适用：`crypto-*` 需求模块与 `libs/crypto_core` 共享库。架构背景见 [architecture.md](architecture.md)。

## 1. 命名

| 对象 | 规范 | 例子 |
|---|---|---|
| 需求模块目录 | `crypto-<topic>`，**必须是领域词**，禁止 `new/temp/test` 占位名 | `crypto-news`、`crypto-price` |
| 模块内脚本 | 动词/名词直白，入口统一 `scripts/` | `fetch.py`、`score.py`、`run.sh` |
| 共享能力 | 按子领域分包，单文件单职责 | `notify/telegram.py`、`market/binance.py` |
| 数据文件 | 目录语义固定（见 §4） | `data/raw/2026-11-08/items.jsonl` |
| 事件 key | `<domain>:<target>:<event>` | `price:BTCUSDT:newlow` |
| 调度 Label | `com.crypto-research.<module>` | `com.crypto-research.crypto-news` |

## 2. 代码

- 只用 Python 3.9 标准库；PyYAML / ruff 仅开发增强（缺失要能降级运行）。
- 单文件 < 500 行；模块脚本总量 > 800 行时考虑拆 `src/` + 薄 CLI。
- 共享逻辑只能进 `libs/crypto_core`，模块见 diff 复制粘贴即视为 bug。
- 所有对外副作用（网络、推送、写盘）必须能 `--dry-run` 或注入关闭。
- 单源失败不拖垮整轮：记录 `errors` 继续跑，结束汇总退出码。

## 3. CLI 约定（各模块统一）

| 参数 | 含义 |
|---|---|
| 无参数 | 跑一轮，**不推送**（安全默认，只写本地产物） |
| `--once` | 跑一轮后退出（常驻型模块） |
| `--dry-run` | 只打印/只写本地，不产生外部副作用 |
| `--test` | 发一条测试消息验证通道 |
| `--fixtures <dir>` | 离线回放（CI 专用，禁止联网） |
| `--date <YYYY-MM-DD>` | 指定处理日期（默认今天） |
| `--json` | 机器可读输出 |

退出码：`0` 成功 / `1` 部分源失败但有产出 / `2` 配置或凭据错误 / `3` 网络全挂 / `4` 锁被占用跳过。

## 4. 数据目录

```
<module>/data/
├── raw/<date>/*.jsonl   # 原始数据（可回放，保留 90 天）
├── state/*.json         # 去重/冷却/游标/锁
├── reports/<date>.md    # 人读产物（长期保留）
├── exports/*.json       # ★ 跨模块只读契约（滚动 7 天）
└── logs/*.log           # 运行日志（30 天）
```

- 跨模块只允许读别人的 `exports/`，禁止读 `state/`、`raw/`、`reports/`。
- 运行产物一律 gitignored；凭据只在仓库根 `.env`。
- 保留策略由 `tools/prune.py` 执行（raw 90d / logs 30d / exports 7d / reports 365d / alerts 180d / 单日志 2MB 上限 / **data 总量硬上限 500MB**），模块的 `collect.sh` **每天首轮自动跑一次**，无需另设定时任务。

## 5. 告警事件

- 统一 `alert/v1`：`schema/module/severity/category/assets/title/detail/urls/ts/dedupe_key/score/summary/evidence`
- 严重度统一 `P0/P1/P2`；旧 `critical/warn/info` 由 `crypto_core.events.severity_of()` 映射。
- 去重键 = 归一化标题 sha256 前 24 位；**不含评分/来源数/时间**（否则每轮都会重复推送）。
- 旧落盘格式（`alerts-<date>.json`）只增字段、不改语义。
- **展示层**：精选/日报类标题（含「周精选/日报/Top10」等）会把标题分句与正文编号条目拆成**编号要点**（1/2/3…，按重要性排序，最多 5 条）；类别/硬条件/影响/动作参考的中文词表在 `crypto_core.events`（`CATEGORY_CN` / `HARD_CN` / `IMPACT_CN` / `ACTION_CN`），卡片不展示 S/T/M/C/D 内部明细，链接用「查看原文」文字链接。
- **质量门（硬条件不再是命中即推）**：P0 需同时满足「已确认 + 48h 内 + 衰减 D≥0.1」；已确认 = T0 单源 或 ≥2 个非 T3 独立源；T3 聚合/搬运站不计独立源；未确认事件最多 P1 且卡片显式标注；超 96h 的硬条件事件只按分数归档（`backfill`）。阈值在 `thresholds.yaml` 的 `scoring.hard`。

## 5.1 消息时间与时区（硬规则，2026-11-10）

- **所有 Telegram 消息**统一由 `crypto_core.notify.telegram.send_message` 追加页脚：
  `🕒 发送时间：YYYY-MM-DD HH:MM 本地时间`；模块不要自己重复加。
- 卡片/日报/周报里出现的任何时间字段，必须换算成 **本地时区（）** 并带时区标记；
  禁止只写 `MM-DD HH:MM` 而不标时区。
- 共享格式化函数：`crypto_core.timeutil.fmt_local(value, fmt)`（带 ``）、
  `now_label()`（完整时区）、`to_local()`（统一换算）。

## 6. 配置

- 模块配置放 `<module>/config/`；YAML 优先（可注释），机器生成可用 JSON。
- 提交 `*.example.*` 模板（空凭据）；含凭据的真实配置 gitignore。
- 阈值、权重、信源清单、路由策略属于模块私有，**不进 libs**。

## 7. 测试

- `make test` 必须能**断网**全绿；联网冒烟只做可选步骤（`verify.sh --with-network`）。解析层用内联样本 fixtures 测试（见 `libs/crypto_core/tests/test_sources_market.py`）。
- libs：纯单测；模块：规则单测 + 端到端重评分；后续补 `--fixtures` 回放。
- 任何修复先补一条会失败的测试，再改实现。

## 8. 文档规划（需求 / 设计 / 业务架构 / 技术架构）

一个需求模块 = **一个代码目录 + 一个文档目录**，同名对应：

```
crypto-<topic>/                     # 代码：入口/配置/测试/数据/调度
docs/crypto-<topic>/                # 文档：固定五件套
├── README.md          # 索引：是什么/状态/代码与调度入口/其余文档链接
├── requirement.md     # 需求：为什么做、做什么/不做什么、触发条件、验收标准、依赖、风险
├── design.md          # 设计：数据源、算法/规则、接口契约、产物、测试方案、失败模式
├── architecture.md    # 业务架构图（Mermaid）：业务能力/业务数据流/关键流程/模块关系
└── technical.md       # 模块技术架构：代码结构/组件职责/运行链路/状态数据/测试
```

- **共享层**（跨需求）在 `docs/` 根：`README.md`（导航）、`architecture.md`（仓库级技术架构）、`runbook.md`、`conventions.md`、`adr/`。
- `technical.md` 只写**本模块内部**技术架构；仓库级分层/依赖/调度不要重复画。
- 需求变更先改 `requirement.md`，实现与 `design.md`/`technical.md`/代码同一个 PR 更新。
- 新需求用 `make new-module` 生成，会自动创建 `docs/<name>/` 五件套（模板 `docs/_template/`）。
- 验收标准必须可测量（命令 + 期望输出），不能只写"能用"。

## 9. 提交与 PR（项目准则，2026-11-09 设计评审）

**流程：需求评估 → 修改代码 → 测试全绿 → 分支 commit → push → PR → CI 绿 → squash merge → 删分支。**

1. **需求评估**：先明确范围 / 验收标准（写进模块 `requirement.md` 或任务说明）；
2. **修改代码**：代码 + 配置 + 测试 + 文档一起改；
3. **测试没问题**：`make gate` 全绿（至少模块单测 + `scripts/e2e.py`），必要时 `--with-network` 冒烟；
4. **绝不直接 push main**：每个任务/并行会话从 main 拉自己的分支：
   `git switch -c <type>/<scope>-<短描述> main`
   （`type` = feat / fix / docs / refactor / chore；`scope` = 模块名，如 `crypto-x-kol`）；
5. **一个需求/一个独立改动 = 一个 PR**：
   - PR 内可以有 1–N 个 commit（仍按“一个提交只做一件事”拆）；
   - 不把多个不相关需求塞进同一个 PR，也不把一个可独立上线的需求硬拆成很多 PR；
6. `git push -u origin HEAD` → `gh pr create`；PR 描述按 [.github/pull_request_template.md](../.github/pull_request_template.md)
   （需求 / 改动 / 验证 / 风险与回退）；
7. PR 上 CI 跑 gate；**CI 全绿**后 squash 合并，
   main 上保持「一个 PR 一个 commit」，方便整需求回退：
   `gh pr merge <n> --squash --delete-branch --subject "[scope][type] 摘要 (#<n>)" --body "<PR 描述>"`
   - **必须显式传 `--subject`**：单个 commit 的 PR 默认会用该 commit message 当 squash 标题，
     容易变成 `feat(scope): ...` 之类不符合规范的格式；
   - **`--subject` 不会自动追加 `(#N)`**，需按 PR 编号手工带上（与历史 squash 提交保持一致，
     方便 `git revert` / 追溯）；
   - squash 标题的 `[scope][type]` 部分必须与 PR 标题一致（CI 校验的是 PR 标题）；
8. **回退**：`git revert <squash-commit>` 回退整个需求；紧急时直接在 main revert 该 commit，不影响其它 PR；
9. **并行会话**：各自分支/PR，不共享工作分支；冲突在分支内解决，不把冲突带进 main；
   不属于本轮的 WIP 不 `git add -A`；
10. **本地强制**：`.githooks/pre-push` 会拒绝任何直推 `main` 的 push（紧急放行 `ALLOW_MAIN_PUSH=1`，慎用）；
    其它分支 push 仍先跑 `make gate`。
11. 说明：当前 GitHub 私有仓库无法启用服务端 branch protection / rulesets
    （API 403，网页提示需迁移到 GitHub Team/Enterprise 组织账号）；所以“禁止直推 main”
    目前由**本地 pre-push 守卫 + PR 流程约定**保证，不是 GitHub 服务端强制。
    升级团队账号或仓库转 public 后可改为服务端强制。
- 一个提交只做一件事：`refactor:` 搬代码不改行为、`feat:` 新能力、`fix:` 修 bug、`docs:` 文档、`chore:` 杂项。
- 提交前跑 `make verify`；涉及调度改动时额外跑 `make launchd module=<name> action=status`。
- 新模块用 `make new-module` 生成；调度统一用 `tools/render_launchd.py`（不再手写 install 脚本）；数据清理用 `make prune`。
