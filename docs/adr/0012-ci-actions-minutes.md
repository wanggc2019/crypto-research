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

1. `ci.yml` 只有一个 job `checks`：hygiene → gitleaks → `make gate` → PR 标题。删除 `pr-title.yml`、`gitleaks.yml`。每一步都是 `!cancelled()`：前一步失败后面照跑，一次运行就能看到全部结果（例如 gitleaks 误报时照样知道 gate 过没过），省掉为看结果而多推的几次。
2. 只在 PR 上跑；**合并到 main 不再重跑**（PR 上已跑过，本地 pre-push 也跑过 gate）；每周一 02:00（UTC）定时对 main 全量跑一次（gitleaks 扫全部历史），兜底绕过 PR 直推 main 或 `--no-verify` 的提交；需要时手动 `workflow_dispatch`。
3. 不监听 PR `edited`：只改正文也会产生一次运行，job 被跳过后留下一个同名的 skipped 检查，会盖住之前 gate 的红灯（PR 检查列表按名字只显示最新一次）。标题检查在运行时通过 API 读当前标题，改标题后执行 `gh run rerun <run-id> --failed` 即可。改 base 分支同理，手动重跑。
4. 同一 PR 新推送取消旧运行（concurrency）。AI 审查的 concurrency 先于 job 的 `if` 生效，所以加别的标签（如 `module:xxx`，常与 opened 同时到达）放进以 `run_id` 区分的独立组，不取消正在进行的审查；它留下的 skipped `review` 检查不影响判断（AI 审查本身不会失败）。
5. 私有仓库下 gitleaks-action 要读 PR 的提交列表：workflow 权限加 `pull-requests: read`。
6. AI 审查只在 PR 打开 / 重新打开 / 转为 ready 时跑一次（draft 打开时不审，转 ready 时再审）；推送后要重审，给 PR 加标签 `ai-review`（`gh pr edit <n> --add-label ai-review`，再次重审先移除再加）。AI 审查仍是独立 job：只有它拿得到 `CLAUDE_CODE_OAUTH_TOKEN`，不与执行 `pip install -r requirements.txt` 和全部测试的 job 合并（见 ADR 0011）。

`tools/tests/test_workflows.py` 校验（解析 YAML 结构）：只有 `ci.yml` 与 `ai-review.yml` 两个 workflow；没有 `push`、`edited` 触发，CI 没有 job 级 `if`；CI 只有一个 job，包含四项检查（`run: make gate`、标题从 API 读），checkout 之后每一步都是 `!cancelled()`；hygiene 不用 `! cmd`；加别的标签不取消 AI 审查；AI 审查不随每次推送跑，保留 fork 守卫、draft 不审。

## 后果

- 预估每个 PR 计费：CI 约 3 分钟 × 推送次数 + AI 审查约 1–2 分钟；按每月 100–150 个 PR、每个 PR 平均 1.7 次推送，约 1000–1300 分钟，在 2000 以内。连续多天每天 15–20 个 PR 的高峰会接近上限，届时看 GitHub 账单页的用量。
- 合并到 main 后只有每周一次定时运行（实测单次 31 秒、按 1 分钟计费，约 4–5 分钟/月）。PR 基于旧 main 时，合并结果没有在 CI 里单独测过；本仓库单人开发、PR 很少并行，接受这个风险。担心时合并前 `gh pr update-branch` 再跑一次。
- 后续 PR 推送不再自动 AI 审查，需要时手动加标签。
- 改标题后 `gh run rerun --failed` 会重跑整个 job（约 1 分钟计费），不再是原来单独的标题 job；开 PR 前先确认标题格式。
- `.gitleaksignore` 按「提交:文件:规则:行」放行，只覆盖那两个历史提交；以后若改动这两行假凭据，会重新报出，届时在该行加 `gitleaks:allow` 注释。
- 修正了一个原有 bug：hygiene 用的是 `! git ls-files | grep …`，GitHub 以 `bash -e` 执行，而 `set -e` 对 `!` 开头的命令不生效，`.env` 与 `data/` 入库检查其实一直没起作用（只有最后一行 `.venv` 决定结果）。现在改为显式 `exit 1`。
- 私有个人仓库的 gitleaks-action 不需要 `GITLEAKS_LICENSE`（只有组织账号需要）。
- 额度用完时 Actions 停跑，PR 检查会一直挂起；本地 `make gate` / pre-push 仍是同一套检查，可照常把关，次月额度恢复。

## 执行记录（2026-11-17）

- 切到私有后 Actions 完全不启动（"recent account payments have failed or your spending limit needs to be increased"）：11 月已计 2879 分钟、花费上限 $0，本月私有额度已用完。用户决定**临时改回公开，12 月 1 日额度重置后再切私有**，届时验证私有状态下 gitleaks 的 `pull-requests: read` 权限。
- 第一次 `workflow_dispatch` 全量运行时，gitleaks 首次扫描全部历史（之前只扫 PR 和推送的增量提交），报了 4 条，均在 2026-11-10「Initial public release」两个提交中，核对后都是误报：`crypto-price/tests/test_adversarial.py:43` 的假 `TELEGRAM_BOT_TOKEN`（`123456:…`，与 `.env` 任何值都不同），以及 `crypto-policy/tests/fixtures/pbc_tiaofasi_list.html:583` 人民银行公开网页的隐藏字段 `modulekey`。已按指纹写入 `.gitleaksignore`（只放行这 4 处）。
