# 测试流程（TESTING）

> 适用：仓库内所有 `crypto-*` 模块。目标：**离线可复现、联网只做冒烟、推送必须标记**。
> 关联：[约定](conventions.md) · [Runbook](runbook.md) · [HANDOFF](HANDOFF.md)

## 1. 分层测试（从快到慢）

| 层 | 内容 | 命令 | 是否联网 | 进入 CI |
|---|---|---|---|---|
| L1 静态 | ruff、YAML/JSON 可解析、schema_version、配置交叉校验 | `make lint` / `make test` 中的 config 测试 | 否 | ✅ |
| L2 单元 | libs 解析器/工具；模块的指标、规则、送达、卡片、报告、状态容错 | `make test` | 否 | ✅ |
| L3 回放 E2E | 固定快照/fixtures 跑完整链路，断言事件、退出码、落盘、去重、静默、锁 | `make verify` 里的端到端段 | 否 | ✅ |
| L3.5 重复审计 | 发送记录中同一 dedupe_key 短时间重复；launchd 同一脚本被多个 Label 加载 | `make gate`（内置 `tools/audit_duplicates.py`） | 否（读本地 state/plist） | ✅（无数据时自动通过） |
| L4 联网冒烟 | 真实交易所/新闻源可达性、三源报价、价差、源状态 | `crypto-price/scripts/verify.sh --with-network`、`run.py --dry-run` | 是 | ❌（本地/按需） |
| L5 推送验证 | Telegram 通道、卡片渲染、模拟告警 | `run.py --test`、`--test-alert` | 是 | ❌（手动，见 §3） |
| L6 线上 canary | launchd 安装后观察 1 轮以上：rc、日志、health.json、无重复告警 | `make launchd … action=status`、`make healthcheck module=…` | 是 | ❌ |

## 2. 每次修改后的固定流程（质量门）

```bash
# 1) 改完代码后，先跑离线全量（约 3–5 秒）
make gate
# 输出「✅ 测试通过」才允许提交；失败会列出失败步骤，修完重新跑

# 2) 涉及网络/源状态时，再跑联网冒烟（外部失败默认只警告）
make gate online=1
# 需要严格把外部失败也当失败：
make gate online=1 strict=1

# 3) 涉及消息格式/推送时，显式跑一次静默通道测试（会发 TG）
make gate push=1

# 可选：让 git push 前自动跑 gate（本地钩子）
make hooks
```

**判定标准**：`gate` 退出码 0 且打印 `✅ 测试通过：N 步全绿` 才算完成；任何 `❌` 都继续修，不提交。

`make gate` 与 `make verify` 的区别：`verify` 保留原来的 CI/人工入口；`gate` 是新的统一入口，
会逐步汇总所有步骤、失败不中断、默认用临时目录跑 E2E，不写生产状态。

### 新增模块如何自动接入 gate

不需要改 `tools/gate.py`，只要满足：

1. 目录名 `crypto-<领域词>`，带 `scripts/` 或 `tests/`；
2. `tests/` 放离线 unittest（自动 `discover`）；
3. `scripts/` 自动纳入 ruff；
4. 需要端到端时，提供 `scripts/e2e.py`，退出码 0 即通过；
5. 重复消息审计自动扫描 `data/state/alerts-*.json`（有 `delivery.decision` 列表格式的模块）。

`Makefile` 的 `MODULES`/`LINT_DIRS` 和 CI 的 `gate` job 也按 `crypto-*/` 自动发现。
并行开发中的 WIP 模块可在 `tools/gate.json` 的 `exclude_modules` 临时排除，稳定后删掉该项即自动纳入。

## 3. 日常命令

```bash
cd ~/vscode/crypto-research

make lint                 # 只跑 ruff（libs/tools/所有模块）
make test                 # 全部离线单测（libs + tools + 各模块）
make verify               # 提交前质量门：lint + test + 两个回放 E2E

# 单模块
cd crypto-price && ./scripts/verify.sh                 # 离线
cd crypto-price && ./scripts/verify.sh --with-network  # 额外真实行情冒烟
cd crypto-analyst && ../.venv/bin/python -m unittest discover -s tests -v
```

当前规模（2026-11-08）：libs 76 / tools 55 / crypto-news 37 / crypto-price 161 / crypto-analyst 35 / crypto-x-kol 57 / crypto-policy 19 / crypto-jobs 114（+ fixtures e2e），`make gate` 全绿。

## 4. 推送/消息验证（避免误发）

| 目的 | 命令 | 说明 |
|---|---|---|
| 只验证通道 | `run.py --test` | **静默消息**（不响铃） |
| 看真实格式告警 | `run.py --test-alert` | 用离线冲击快照跑完整链路，消息带【模拟测试】，**不碰生产状态/冷却** |
| 只看不发送 | `run.py --test-alert --dry-run` | 打印卡片 |
| 日报预览 | `run.py --report --dry-run` | 打印 Markdown + Telegram 文本，不写文件/不推送 |
| 周报预览 | `crypto-analyst/scripts/run.sh --dry-run` | 离线 fixtures 也可 |

**硬规则**：
- 回放/测试消息必须带 `【模拟测试】` 或 `--dry-run`；
- 不要用 `--notify` 回放快照来“顺便测试”，否则会写生产冷却/计数，且容易误发；
- 测试发送前先 `--dry-run` 看文本；
- 健康检查的 AI 修复只对 FAIL 触发，首次立即、之后多级退避（15/30/60/120/240/360 分钟），恢复正常即清零。

## 5. 新功能/修复的测试顺序

1. **先写一个会失败的测试**（单元或 fixture 回放）→ 2. 实现 → 3. `make test` 全绿；
4. 更新/新增 fixtures，补端到端断言；5. `make verify` 全绿；
6. 联网 `run.py --dry-run`（看源状态/价差/事件是否符合预期）；
7. 必要时 `--test` / `--test-alert` 验证推送；
8. 改调度/配置后 `make launchd … action=status` + 观察一轮 `collect.log`；
9. 提交前 `git status` 确认没有 `data/`、`.env` 进入暂存区。

## 6. 故障/回归测试

- **状态损坏**：`test_adversarial.py` 覆盖非法 JSON、合法 JSON 但字段类型错误；
- **网络异常**：mock `http.get_json` 抛 `TimeoutError`，覆盖单源失败、全挂、快速失败跳过；
- **数据质量**：价差 >50bps/>100bps、源数不足、报价过期；
- **推送治理**：静默/冷却/升级/日限额/失败重试/跨重启去重；
- **调度**：锁冲突（rc=4）、过期锁自愈、时区（本地时区）。

## 7. CI

`.github/workflows/ci.yml` 在 push/PR 时跑：
- hygiene：`data/`、`.env`、`.venv` 不得入库；
- lint（libs/tools/全部模块）；
- libs 单测、tools 单测、每个模块单测；
- crypto-price 两个快照回放、crypto-analyst fixtures 回放。

CI **不联网、不读真实 `.env`**；联网与 Telegram 推送只在本地/手动执行。

## 8. 已知边界

- CI 不验证交易所当前可用性（外部依赖不稳定），靠 L4 本地冒烟；
- 没有独立 staging 环境，L5/L6 直接对同一个 Telegram 聊天做**带标记**的测试；
- 休眠/VPN/网络切换会造成瞬时源失败，属于 L4 观察项，不应当作代码回归。

## 9. 提交纪律（每次验证后立即提交）

1. 每次改完代码跑 `make gate`；**通过后立即 commit**（只 `git add` 本次改动的文件）+ push，不要攒着。
2. 失败就继续修，重跑 `make gate`，通过前不提交。
3. 工作区里有并行会话的 WIP 时，**禁止 `git add -A`**；只提交自己负责的文件。
4. 提交信息写清楚：改了什么、验证结果（如 `make gate 17 步全绿`）。
5. 如果本次没有代码改动、只有验证，不必空提交；下一次有改动时一起提交。
