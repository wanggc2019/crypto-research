# 开发工作流（Skill）· crypto-research

> 目的：把“干活有章法”固化成流程。每个需求/改动都按此执行，避免返工、漏提交、破坏并行会话。
> 适用：本仓库所有 `crypto-*` 模块与共享层（libs/tools/docs）。

## 0. 开工前（5 分钟）

1. 读 `docs/HANDOFF.md`（现状/偏好/TODO）与 `docs/conventions.md`（结构/数据/退出码）
2. `git status` + `git log -3`：**确认有没有并行会话的 WIP**；有则只动自己的路径
3. 明确本次是：新模块 / 改模块 / 改共享层（决定影响面）
4. 写下 3 条验收标准（可测量），否则不动手

## 1. 设计（先设计后自动化）

- 边界：一个目录 = 一个需求；**一个模块 = 一个 launchd 任务**（健康检查/AI 内嵌）
- 契约：跨模块只经 `data/exports/`（只增字段）；长周期数据缺失时加本地归档
- 数据流：输入（文件）→ 计算（纯函数）→ 渲染（模板）→ 发送（一层）
- 命名/配置：策略进 `config/*.yaml`；阈值/概率可配置；数字不写死在消息里

## 2. 实施顺序（小步）

```
数据层 → 计算层（纯函数+单测）→ 渲染层 → 调度（launchd/脚本）→ 文档 → 验证 → 提交
```

- 每层完成即自测；**不要憋到最后一起测**
- 新脚本必须：可 `--dry-run`、失败可降级、有日志、退出码明确
- 改共享层（libs/tools/Makefile/CI）要评估对其他模块的影响

## 3. 提交纪律（本次教训）

1. **每完成一个可验证单元就 commit**，不要攒一堆再提交
2. 只 `git add` 自己的路径（模块目录/自己的文档）；**并行会话的 WIP 不碰**
3. 提交前跑 `make gate`（或至少 ruff + 本模块单测）
4. commit message / PR 标题统一：`[scope][type] 摘要`（见 §3.1），正文写要点列表
5. 不要 `git add -A`（会吞并行改动）
6. 完成后 `git status` 确认只剩别人的改动

### 3.1 PR 规范（标题 = 合并后的 main 提交）

标题统一：**`[scope][type] 摘要`**

| 段 | 取值 |
|---|---|
| `scope` | 模块名 `crypto-news` / `crypto-price` / `crypto-analyst` / `crypto-policy` / `crypto-onchain` / `crypto-x-kol` / `crypto-jobs`；共享层 `libs` / `tools`；仓库级 `docs` / `ci` / `repo`（跨模块或纯流程） |
| `type` | `feat` `fix` `docs` `chore` `refactor` `test` `perf` `build` `revert` |
| `摘要` | 中文，一句话说清改了什么；≤72 字符，不带句号 |

示例：

- `[crypto-news][feat] 英文标题 AI 中文翻译`
- `[crypto-price][fix] 数据质量多资产异常合并为一条`
- `[libs][refactor] 统一 notify 任务标签`
- `[repo][docs] 确立 PR 标题规范与 CI 校验`

其它约定：

- **一个 PR 只做一件事**；跨模块改动 scope 用 `repo`。
- 分支名：`<type>/<scope>-<slug>`，如 `feat/crypto-news-ai-translate`、`chore/repo-pr-title-convention`。
- 不使用 `WIP`/`fixup`/`tmp`；草稿用 GitHub Draft PR。
- 标题由 CI workflow `PR Title` 校验；模板见 `.github/pull_request_template.md`。
- 可选：给 PR 加 `module:<scope>` 标签，便于按模块筛选。

> **流程自测记录（2026-11-09）**：用一条真实 PR 跑通
> 「分支 → commit → pre-push gate → PR 标题校验 → CI 全绿 → squash merge → 删分支」。
> 说明：标题规范、模板、CI 校验、pre-push 守卫都已实际生效，不是纸面规则。

## 4. 验证清单（提交前必过）

- [ ] `ruff check` 通过
- [ ] 本模块单测通过（含新功能用例）
- [ ] `make gate` 全绿（含 launchd 健康检查）
- [ ] 离线回放（`--fixtures` / `--dry-run`）可跑
- [ ] 手动跑一次真实路径（`--notify`/发送）并确认收到
- [ ] `make jobs` 任务状态 🟢；失败/超时有告警路径

## 5. 文档同步（同一提交内完成）

- 模块 `README.md`：定位、运行、调度、产物
- `docs/<模块>/`：requirement / design / architecture / technical
- 仓库索引：根 README 模块表、docs/README 索引、HANDOFF、runbook、CI/Makefile
- 数据契约变化：更新 exports schema 说明 + 消费方

## 6. 运维与安全

- 消息统一 `🏷 任务：<Label>`（launchd 注入 CRYPTO_JOB_LABEL）
- 调度：定时器只拉起脚本；AI 放最后、失败不影响主报告
- 数据：`exports` 7 天、`raw` 90 天、长周期用 `data/history` 归档
- 合规：不构成投资建议、不提供交易指引、单源信息标注“待确认”

## 7. 收尾

1. `git status` 检查 → 提交 → push（经用户同意）
2. 更新 `docs/HANDOFF.md` 的“当前状态/常用命令”
3. 下次开工先读 HANDOFF + 本文件
