# crypto-research

个人加密市场研究/监测仓库。**一个仓库 = 若干自包含需求模块**，每个需求一个子目录。

## 模块

| 目录 | 需求 | 运行时 | 说明 |
|---|---|---|---|
| [`crypto-news/`](crypto-news/README.md) | 行业信息采集 → 过滤聚类 → 评分分级 → Telegram 告警/日报 | Python 3.9（标准库） | 原需求，2026-11-08 从仓库根目录迁入 |
| [`crypto-price/`](crypto-price/README.md) | BTC/ETH/BNB 价格/衍生品异动实时告警 | Python 3.9（标准库） | 2026-11-08 新建，P4 首个验证架构的新模块 |
| [`crypto-analyst/`](crypto-analyst/README.md) | 行情分析 / 情景推演（只读 exports + 公开 K 线） | Python 3.9（标准库） | 2026-11-08 新建，**需求定稿 + P2 主体完成**；每日简报/打分/宏观/复盘（22 单测） |
| [`crypto-x-kol/`](crypto-x-kol/README.md) | 关注的 KOL 的 X 推文监控（r.jina.ai 抓主页 → 价值分过滤 → DeepSeek 中文翻译/一句话总结 → 日报/Telegram） | Python 3.9（标准库） | 2026-11-08 新建，白名单 26 个账号（≤30），每 5 分钟；抓取零成本，翻译几分钱/天 |
| [`crypto-policy/`](crypto-policy/README.md) | 国内数字货币政策/法规/案例雷达 → 法规库 + 速报 + 解读草稿（规则门过滤，AI 只处理候选） | Python 3.9（标准库） | 2026-11-08 上线：5 个 T0/T1 源（+2 备用）、6 小时、零 API；P0/P1 推送 + AI 解读草稿（每天 08:30） |
| [`crypto-jobs/`](crypto-jobs/README.md) | 加密行业技术岗位（DevOps/SRE/MLOps/开发）市场情报：多源采集 → 技能词典抽取 → 每日快照/差分 → 周趋势 Telegram | Python 3.9（标准库） | 2026-11-08 新建 MVP：公开 ATS/招聘站、零成本、每日 18:00 日更 + 周日 10:00 周报 + 每 2 小时新岗位速报（两个 launchd 任务）、离线 fixtures 回放；2026-11-09 纳入开发岗位 |
| [`crypto-onchain/`](crypto-onchain/README.md) | 链上/交易所资金流"卖压归因"（DefiLlama CEX 净流 + Binance 逐笔主动卖 + OKX 清算 + 稳定币净增发 → 4 类归因 + exports） | Python 3.9（标准库） | 2026-11-09 新建，**M2 归因引擎 + 链上大额转账（公共 RPC，≥$5M）**：零 key、零成本、影子模式（只落盘/不推送）；2 周校准后才接 M3 告警 |
| [`crypto-knowledge/`](crypto-knowledge/README.md) | 每日 4 条：13:00 概念课 / 19:00 理论或实践课 / 21:00 回顾总结（周日为本周回顾）/ 23:00 复盘课（间隔复习） | Python 3.9（标准库） | 2026-11-09 新建；160 条课程内容（4 阶段，每条 10–15 分钟）、SRS 1/3/7/14/30/60 天、4 时段 launchd；独立 Bot `独立 Bot` |

> 历史：2026-11-08 之前的代码直接平铺在仓库根（`scripts/`、`config/`、`tests/`、`data/`），
> 现已整体迁入 `crypto-news/`；同一轮迁移里把旧的 launchd 任务换成了 `com.crypto-research.crypto-news`。

## 结构

```
crypto-research/                        # 一个仓库 = 多个自包含需求模块 + 共享库
├── README.md / Makefile / pyproject.toml / env.example / .env
├── .github/workflows/ci.yml            # hygiene / lint / libs / 各模块测试
├── docs/                               # 架构、Runbook、约定、ADR、需求、方案、HANDOFF
├── libs/crypto_core/                   # 共享能力（模块只向下依赖）
│   └── src/crypto_core/
│       ├── paths.py env.py config.py timeutil.py store.py text.py http.py
│       ├── events.py dedupe.py         # alert/v1 事件模型 + 去重/冷却
│       ├── notify/                     # Telegram 发送 + 中文卡片模板
│       ├── sources/                    # RSS / 交易所公告 / Telegram 预览 / 页面指纹
│       └── market/                     # Binance / OKX / Coinbase / DefiLlama 解析
├── tools/                              # 仓库级工具（不装成库、不被模块 import）
│   ├── healthcheck.py                  # 运行期自检/诊断/自愈/按需 AI
│   ├── render_launchd.py new_module.py prune.py
│   ├── agents/inspect-prompt.md        # headless 巡检任务卡
│   └── tests/
├── crypto-news/                        # 模块 1：消息面情报（10 分钟）
│   └── scripts/ config/ tests/ deploy/launchd/ data/
├── crypto-price/                       # 模块 2：价格/衍生品异动（60 秒）
│   └── scripts/ config/ tests/ deploy/launchd/ data/
└── crypto-<next>/                      # 后续需求按同样约定新增（链上/宏观/报告…）
```

依赖与运行（详细图见 [架构设计 §3.1](docs/architecture.md#31-结构图) 与 [Runbook §1](docs/runbook.md)）：

```
crypto-news ─┐
crypto-price ┼──> libs/crypto_core ──> Python 3.9 标准库
tools ───────┘         （模块之间零 import；跨模块只读 data/exports/ 契约）

launchd（各模块主任务）
├─ crypto-news   每 10 分钟 → collect.sh → fetch → score → notify → Telegram（每轮自检）
│                                    └─ healthcheck（异常→自愈/告警；≥6h 才调 AI 深度分析）
├─ crypto-price  每 60 秒  → collect.sh → 行情 → 规则 → Telegram（每轮自检）
└─ crypto-x-kol    每 5 分钟 → collect.sh → fetch（r.jina.ai 抓 X 主页）→ digest → 日报/Telegram（每轮自检）
```

## 约定（新增需求时照做）

1. **一个目录 = 一个需求**，目录名 `crypto-<topic>` 且必须是**领域词**（如 `crypto-news`、`crypto-price`、`crypto-onchain`），不要用 `crypto-new`/`temp` 这类占位名；代码、配置、测试、数据、部署资产都放自己目录里。
2. **根目录只放共享层**：`README.md`、`.env`/`env.example`、`.gitignore`、`docs/`、`.github/`、`.vscode/`。
3. **模块之间不互相 import**，只通过文件/数据契约交互；公共能力下沉到 `libs/crypto_core`（禁止在模块里复制共享逻辑）。
4. 运行产物一律放模块内 `data/`（已被 `.gitignore` 覆盖），密钥只放根 `.env`。
5. 新模块自带 README，并在本文件「模块」表加一行；CI 在 `.github/workflows/ci.yml` 加一个 job。

## 共享凭据

仓库根 `.env`（不入库）：

```bash
TELEGRAM_BOT_TOKEN=<bot_token>
TELEGRAM_CHAT_ID=<your_chat_id>
```

模板见 [`env.example`](env.example)。模块可以直接读根 `.env`（`crypto-news` 已支持：模块内无 `.env` 时回退到仓库根）。

`env.example` 是**入库的凭据清单/模板**（只有变量名和注释，真实值永远为空）；根 `.env` 是**本机实际值**、被 `.gitignore` 排除。新增可选凭据（不填则对应功能自动关闭）：`JINA_API_KEY`、`DEEPSEEK_API_KEY`、`CMC_API_KEY`、`ETHERSCAN_API_KEY`（crypto-onchain 链上地址级监控，免费档够用）。

## 快速开始

```bash
make setup                 # 建根 .venv（可选；没有 venv 也能跑）
make verify                # 全仓质量门：ruff + libs 单测 + 模块单测 + 端到端
make gate                  # 统一质量门（每次修改后跑；全绿才打印「测试通过」）
make hooks                 # 安装 pre-push 钩子：push 前自动 make gate
make test                  # 只跑单测
make new-module name=crypto-xxx desc="..."         # 新需求起步（生成骨架）
make prune module=crypto-news                      # 数据清理（默认 dry-run）
make healthcheck module=crypto-news                # 运行期健康检查（--fix 可自愈）
```

模块手动跑一轮：

```bash
cd crypto-news
./scripts/run.sh           # 采集 + 评分（不推送）
./scripts/run.sh --notify  # 采集 + 评分 + 推送
./scripts/verify.sh        # 模块级自检（含 libs 单测）

cd crypto-price
./scripts/run.sh --dry-run # 行情预览（不写状态、不推送）
./scripts/run.sh --notify  # 行情 + 规则 + 推送
./scripts/run.sh --test    # 静默 Telegram 通道测试
./scripts/verify.sh        # 模块级自检


cd crypto-x-kol
./scripts/run.sh --dry-run # 抓一轮 KOL 主页，只打印（不写状态、不推送）
./scripts/run.sh           # 抓一轮 + 写日报（不推送）
./scripts/run.sh --notify  # 抓一轮 + 日报 + 推送新帖
./scripts/run.sh --test    # Telegram 通道测试
```

调度：

```bash
make launchd module=crypto-news action=status      # 每 10 分钟（news）
make launchd module=crypto-price action=status     # 每 60 秒（price）
make launchd module=crypto-analyst action=status   # 单任务：每日 10:00 日报 / 周日 20:00 周报+AI
make launchd module=crypto-x-kol action=status       # 每 5 分钟（KOL 推文）
./crypto-news/scripts/install_launchd.sh [--status|--uninstall]
./crypto-price/scripts/install_launchd.sh [--status|--uninstall]
```

## 文档

- [AGENTS.md](AGENTS.md) —— **任何新会话/Agent 的开工入口**（检查清单、硬规则、常见坑、项目级 skills）
- [架构设计 v1](docs/architecture.md) —— 仓库分层、共享能力（libs）、契约、演进路线（P0–P4 已落地）
- [文档导航](docs/README.md) —— 共享文档 + **每个需求一个目录**：[docs/crypto-news/](docs/crypto-news/README.md)、[docs/crypto-price/](docs/crypto-price/README.md)、[docs/crypto-x-kol/](docs/crypto-x-kol/README.md)、[docs/crypto-jobs/](docs/crypto-jobs/README.md)（需求/设计/业务架构/技术架构）
- [运行与下线 Runbook](docs/runbook.md) —— **定时任务怎么跑、怎么暂停/卸载/彻底下线、故障排查**
- [编码与运行约定](docs/conventions.md) —— 命名/配置/数据/日志/CLI/退出码规范
- [测试流程](docs/TESTING.md) —— 分层测试、离线 fixtures、联网冒烟、推送验证、CI
- [架构决策记录 ADR](docs/adr/) —— 每项关键决策一段记录
- [方案 v0.6](docs/crypto-intel-plan.md) —— 信源分层、五段式流水线、评分模型、黑天鹅早警、落地路线
- [个人学习方案](docs/crypto-learning-plan.md) —— 长期碎片化学习路线（知识地图 / 10–15 分钟课程 / 资源库 / 合规边界）
- [交接文档 HANDOFF](docs/HANDOFF.md) —— **先读这份**：设计约束、当前状态、常用命令、TODO
- [模块化规划](docs/code-plan-module-split.md) —— 为什么拆、怎么拆、后续新模块怎么加
- [Day-1 零成本清单](docs/day1-setup.md) —— 历史记录（路径以本文件与 HANDOFF 为准）
