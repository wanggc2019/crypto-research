<!--
PR 标题格式（CI 会校验，必须遵守）：
  [scope][type] 摘要
  scope: crypto-news | crypto-price | crypto-analyst | crypto-policy | crypto-onchain |
         crypto-x-kol | crypto-jobs | crypto-knowledge | libs | tools | docs | ci | repo
  type : feat | fix | docs | chore | refactor | test | perf | build | revert
  示例：[crypto-news][feat] 英文标题 AI 中文翻译
  规则：一个 PR 只做一件事；标题 ≤72 字符；不要 WIP/fixup/tmp。
-->

## 需求 / 背景

<!-- 要解决什么问题；对应 docs/<module>/requirement.md 或任务说明 -->

## 范围

- 影响模块：
- 涉及共享层（libs/tools/CI）：
- 明确不做的：

## 改动

<!-- 主要改了哪些文件/模块；必要时列 1/2/3 -->

## 验证

- [ ] `ruff check` 通过
- [ ] 本模块单测通过（含新功能用例）
- [ ] `make gate` 全绿（pre-push 与 PR CI 均会跑）
- [ ] 涉及调度：`make launchd module=<name> action=status`
- [ ] 涉及网络/行情：`python3 tools/gate.py --with-network`（可选）

## 风险与回退

<!-- 失败模式、影响范围、回退方式（git revert <squash-commit>） -->

## 关联

<!-- issue / 其它 PR / 文档链接 -->
