# ADR 0012：仓库改回私有，CI 按 Actions 分钟精简

- 状态：已采纳
- 日期：2026-11-17

## 背景

仓库原为私有，曾用完 GitHub Free 私有仓库每月 2000 分钟（Linux）的 Actions 额度，之后改为公开。2026-11-17 统计了全部运行（302 次、538 个 job，11-15～11-16 两天、36 个 PR）：

| workflow | 实际运行 | 私有仓库计费 |
|---|---|---|
| CI（hygiene + gate 两个 job，PR 和推送 main 各一次） | 87 分钟 | 342 分钟 |
| Gitleaks（PR 和推送 main 各一次） | 12 分钟 | 82 分钟 |
| PR Title | 3 分钟 | 60 分钟 |
| AI Review（每次推送都跑） | 18 分钟 | 58 分钟 |
| 合计 | 约 120 分钟 | 542 分钟 |

计费按 job 取整到分钟：实际 120 分钟被算成 542 分钟。照此速度约 4 天用完一个月的额度。

## 决策

用户决定：仓库回到**私有**（研究逻辑、KOL 名单、数据源与运维文档不公开），同时精简 CI：

1. `ci.yml` 只有一个 job `checks`：hygiene → gitleaks → `make gate` → PR 标题（最后一步 `!cancelled()`，gate 失败时也能看到标题结果）。删除 `pr-title.yml`、`gitleaks.yml`。
2. 只在 PR 上跑；**合并到 main 不再重跑**（PR 上已跑过，本地 pre-push 也跑过 gate）；每周一 02:00（UTC）定时对 main 全量跑一次（gitleaks 扫全部历史），兜底绕过 PR 直推 main 或 `--no-verify` 的提交；需要时手动 `workflow_dispatch`。
3. PR `edited` 只有改了标题或 base 分支才跑（整套重跑，避免一次只查标题的绿灯盖住之前 gate 的红灯）。
4. 同一 PR 新推送取消旧运行（concurrency）。concurrency 先于 job 的 `if` 生效，所以会被跳过的事件（只改正文的 `edited`、加别的标签的 `labeled`）放进以 `run_id` 区分的独立组，不取消正在跑的检查/审查。
5. 私有仓库下 gitleaks-action 要读 PR 的提交列表：workflow 权限加 `pull-requests: read`。
6. AI 审查只在 PR 打开 / 重新打开 / 转为 ready 时跑一次；推送后要重审，给 PR 加标签 `ai-review`（`gh pr edit <n> --add-label ai-review`，再次重审先移除再加）。AI 审查仍是独立 job：只有它拿得到 `CLAUDE_CODE_OAUTH_TOKEN`，不与执行 `pip install -r requirements.txt` 和全部测试的 job 合并（见 ADR 0011）。

`tools/tests/test_workflows.py` 校验（解析 YAML 结构）：只有 `ci.yml` 与 `ai-review.yml` 两个 workflow、没有 `push` 触发、CI 只有一个 job 且包含四项检查（`run: make gate`、标题检查在最后且 `!cancelled()`）、edited 只在改标题/base 时跑、会被跳过的事件不取消运行、AI 审查不随每次推送跑。

## 后果

- 预估每个 PR 计费：CI 约 3 分钟 × 推送次数 + AI 审查约 1–2 分钟；按每月 100–150 个 PR、每个 PR 平均 1.7 次推送，约 1000–1300 分钟，在 2000 以内。连续多天每天 15–20 个 PR 的高峰会接近上限，届时看 GitHub 账单页的用量。
- 合并到 main 后只有每周一次定时运行（约 4 分钟/月）。PR 基于旧 main 时，合并结果没有在 CI 里单独测过；本仓库单人开发、PR 很少并行，接受这个风险。担心时合并前 `gh pr update-branch` 再跑一次。
- 后续 PR 推送不再自动 AI 审查，需要时手动加标签。
- 私有个人仓库的 gitleaks-action 不需要 `GITLEAKS_LICENSE`（只有组织账号需要）。
- 额度用完时 Actions 停跑，PR 检查会一直挂起；本地 `make gate` / pre-push 仍是同一套检查，可照常把关，次月额度恢复。

## 执行记录（2026-11-17）

- 切到私有后 Actions 完全不启动（"recent account payments have failed or your spending limit needs to be increased"）：11 月已计 2879 分钟、花费上限 $0，本月私有额度已用完。用户决定**临时改回公开，12 月 1 日额度重置后再切私有**，届时验证私有状态下 gitleaks 的 `pull-requests: read` 权限。
- 第一次 `workflow_dispatch` 全量运行时，gitleaks 首次扫描全部历史（之前只扫 PR 和推送的增量提交），报了 4 条，均在 2026-11-10「Initial public release」两个提交中，核对后都是误报：`crypto-price/tests/test_adversarial.py:43` 的假 `TELEGRAM_BOT_TOKEN`（`123456:…`，与 `.env` 任何值都不同），以及 `crypto-policy/tests/fixtures/pbc_tiaofasi_list.html:583` 人民银行公开网页的隐藏字段 `modulekey`。已按指纹写入 `.gitleaksignore`（只放行这 4 处）。
