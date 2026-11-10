---
name: crypto-research-workflow
description: 在 crypto-research 仓库开发或维护模块时使用：开工检查、需求→模块、测试与 gate、分支+PR+CI 交付、launchd/健康检查、常见坑。仓库入口是 AGENTS.md。
whenToUse: 当前工作目录是 crypto-research（含 crypto-* 模块、docs/、AGENTS.md），或需要修改该仓库任一模块/文档/调度/数据源。
---

# crypto-research 开发流程

先读仓库根 `AGENTS.md`（开工检查 + 硬规则），再按下面步骤执行；项目事实以仓库文档为准，本 skill 只做流程索引。

## 1. 开工

- `pwd; git status -sb; git branch --show-current` —— 确认在仓库、分支干净；
- 工作区可能有并行会话的未提交改动：**不要 `git add -A`、不要 stash 别人**；
- 读 `docs/HANDOFF.md`（当前状态/偏好/待办）与 `docs/conventions.md`（规范）；
- **先加载 `session-status-reporting` skill**（`.agents/skills/session-status-reporting/SKILL.md`）：
  按它拆验收项、每轮主动报进度/完成度、收尾自验并明确说明是否完成，不要等用户问；
- 运维任务先 `make jobs` / `make healthcheck module=<name>` 看现状。

## 2. 需求评估

- 一句话目标 + 边界（做什么/不做什么）+ 可测量验收标准；
- 改 `docs/<module>/requirement.md`；新模块用 `make new-module name=crypto-<领域词> desc=...`；
- 明确是否零成本、是否涉及调度/凭据/跨模块导出。

## 3. 实现

- Python 3.9 标准库；共享逻辑进 `libs/crypto_core`，模块间零 import；
- 配置放 `<module>/config/*.yaml`；所有外部副作用支持 `--dry-run`，解析层支持 `--fixtures`；
- 单源/单账号失败继续跑并汇总 rc；状态文件有界；运行产物只写模块 `data/`；
- 改 bug 先补会失败的测试。

## 4. 验证

```bash
make gate            # 必跑：ruff + 全部单测 + 回放 E2E + 重复审计 + launchd
python3 tools/gate.py --with-network   # 涉及外部源时可选
```

- 新模块的 `tests/` 与 `scripts/e2e.py` 会被 gate 自动发现；PR CI 跑同一套；
- 涉及调度改动：`make launchd module=<name> action=status`。

## 5. 交付（不要直推 main）

```bash
git switch -c <feat|fix|docs|chore>/<scope>-<简述> main
git add <本轮文件> && git commit -m "<type>(<scope>): ..."
git push -u origin HEAD && gh pr create    # 标题 [scope][type] 摘要（≤72 字，CI 校验）；正文按 PR 模板
gh pr checks <n> --watch                   # CI 绿
gh pr merge <n> --squash --delete-branch
```

- 一个需求/独立改动 = 一个 PR；main 上一个 PR 一个 squash commit → `git revert` 整需求回退；
- 私有免费仓库无服务端保护：本地 pre-push 会拒绝直推 main（`ALLOW_MAIN_PUSH=1` 仅紧急）。

## 6. 运维

- 调度：`make launchd module=<name> action=install|status|uninstall`（tools/render_launchd.py）；
- 自检：每轮 `healthcheck`；每日 `prune`；日志 `data/logs/collect.log`；
- 送达策略：**全天即时（无夜间静默，2026-11-10 起）**；`quiet_hours` 可配回旧行为；同轮 P1 不要标成 digest；
- Mac 休眠会导致 launchd 漏跑，属已知现象。

## 7. 常见坑

- r.jina.ai 的 X 主页 Markdown 不含时间/互动数/status id；原帖链接需 HTML 解析；
- 整页 hash 会因 `blob:` URL 变化误判；用逐帖指纹；
- X 登录墙记 SKIP（不失败）；全部跳过才 rc=3；抓取要节流（匿名 20 RPM）；
- DeepSeek 翻译/总结要批量+缓存+无 key 降级；
- Telegram 消息长度/预览/限额；测试消息要标明样例；
- CI：runner 无 `.venv`（Makefile 已回退 python3）；`tests/fixtures/` 可入库（hygiene 已放行）。

## 8. 参考

- `AGENTS.md`（入口 checklist）
- `docs/HANDOFF.md` · `docs/conventions.md` · `docs/runbook.md` · `docs/architecture.md`
- `docs/<module>/{requirement,design,architecture,technical}.md`
