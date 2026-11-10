# 仓库模块化：`crypto-news` 落地记录 + 后续新需求约定

> 状态：**已落地**（2026-11-08 02:20，本地时区）
> 改名记录：目录最初叫 `crypto-new`（占位名），2026-11-08 02:18 按领域更名为 `crypto-news`；launchd Label 同步改为 `com.crypto-research.crypto-news`，旧 Label 由安装脚本自动清理。
> 变更：把原先平铺在仓库根目录的情报流水线整体迁入 `crypto-news/`；仓库改为「一个仓库 = 若干自包含需求模块」。
> 关联：[根 README](../README.md)、[HANDOFF](HANDOFF.md)、[方案 v0.6](crypto-intel-plan.md)、[架构设计 v1](architecture.md)（长期架构与共享能力抽取决议见后者）

---

## 0. 结论

- **一个需求 = 一个子目录**，目录名 `crypto-<topic>`；本仓库第一个模块是 `crypto-news/`（加密情报采集/评分/告警）。
- 根目录只留共享层：`README.md`、`.env` / `env.example`、`.gitignore`、`docs/`、`.github/`、`.vscode/`。
- 后续新需求（用户已举的例子：**价格告警 → `crypto-price/`**）新建同级目录，模块之间不互相 import。
- 迁移只动位置，**业务逻辑零改动**（评分模型、阈值、信源清单都不变）；`.venv` 已重建、launchd 任务已换新 Label。

---

## 1. 目标结构（当前仓库实况）

```
crypto-research/                     # 仓库根 = 共享层
├── README.md                        # 模块总览 + 约定 + 快速开始
├── env.example                      # 凭据模板（TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID）
├── .env                             # 真实凭据（gitignored，所有模块共用）
├── .gitignore                       # data/、*.log、.env、.venv/ 等（对任意层级生效）
├── .github/workflows/ci.yml         # 每个模块一个 job（当前只有 crypto-news）
├── .vscode/                         # 调试配置（路径已指向 crypto-news）
├── docs/                            # 跨模块共享文档
│   ├── crypto-intel-plan.md         # 方案 v0.6（§3.2 已按新结构更新）
│   ├── HANDOFF.md                   # 交接文档（命令/路径已更新）
│   ├── code-plan-module-split.md    # 本文
│   ├── day1-setup.md                # 历史记录（已标注路径迁移）
│   └── history/                     # 历史会话存档（不动）
└── crypto-news/                      # ── 需求 1：加密情报采集/评分/告警 ──
    ├── README.md                    # 模块说明（运行/产物/定时任务/原则）
    ├── pyproject.toml               # ruff 配置（target py39）
    ├── requirements.txt             # 开发增强：PyYAML / ruff
    ├── .venv/                       # 模块虚拟环境（gitignored，重建而非 mv）
    ├── scripts/                     # 代码（内部结构保持不变，便于回滚）
    │   ├── common.py                # 路径常量 / 配置加载 / 通用工具
    │   ├── fetch.py                 # 采集 + 归一化
    │   ├── score.py                 # 过滤 / 聚类 / 评分 / 分级
    │   ├── notify.py                # Telegram 推送（凭据读仓库根 .env）
    │   ├── run.sh / collect.sh      # 手动 / 定时入口
    │   ├── verify.sh                # 质量门：ruff + 25 单测 + 端到端
    │   └── install_launchd.sh       # 安装/卸载/查看 launchd
    ├── config/*.yaml                # 7 个配置（原样迁移）
    ├── tests/                       # 25 项 unittest（路径已随目录自动生效）
    ├── deploy/launchd/              # LaunchAgent 模板
    └── data/                        # 运行产物（gitignored）：raw/ reports/ state/ logs/
```

---

## 2. 已执行的迁移（可回滚记录）

| 步骤 | 动作 |
|---|---|
| 1 | `git tag pre-module-split` + 备份 `/tmp/crypto-research-backup-*.tgz` |
| 2 | `launchctl bootout com.crypto-research`，移除旧 LaunchAgent plist |
| 3 | `git mv scripts/ config/ tests/ pyproject.toml requirements.txt deploy/launchd/ → crypto-news/`（20 个文件，git 记录为 rename） |
| 4 | `mv data/ → crypto-news/data/`（未入库的运行产物） |
| 5 | 删除根 `.venv`，在 `crypto-news/.venv` 重建并装 PyYAML + ruff |
| 6 | `notify.py`：`.env` 查找改为「模块内 → 仓库根」回退 |
| 7 | launchd Label 改为 `com.crypto-research.crypto-news`；plist 重命名；安装脚本自动清理旧 Label |
| 8 | CI `.github/workflows/ci.yml`：`working-directory: crypto-news` |
| 9 | 根 README 改为模块总览；运行/产物/定时任务说明搬到 `crypto-news/README.md` |
| 10 | 文档：HANDOFF、方案 §3.2、Day-1 路径全部更新 |

**未改的东西**：评分公式/阈值/信源清单/去重逻辑/推送格式/数据文件格式——迁移不承担重构风险。

---

## 3. 模块间协作约定

1. **不互相 import**：需要别人的数据就读对方的产物文件；需要共享的能力先复制或后续再抽公共库。
2. **凭据只有一份**：仓库根 `.env`。新模块读取顺序建议「模块内 `.env`（可选覆盖）→ 仓库根 `.env`」。
3. **数据各归各**：运行产物写自己模块的 `data/`，不要跨模块写；根 `.gitignore` 的 `data/` 对任意层级生效。
4. **告警语义**：如果新模块也要推送，沿用 `notify.yaml` / 事件分级（P0/P1/P2）的约定，避免出现两套严重度词汇。
5. **调度 Label 带模块名**：`com.crypto-research.<模块目录名>`，避免旧任务残留。

---

## 4. 新增需求模板（以 `crypto-price/` 为例）

```
crypto-price/
├── README.md        # 定位、运行命令、调度方式、数据位置、边界（不做什么）
├── src/ 或 scripts/ # 入口；保证「单命令可跑」
├── config/          # 可版本化配置；密钥一律走仓库根 .env
├── tests/           # 至少覆盖核心规则/解析逻辑
├── deploy/          # 调度/部署资产（可选）
└── data/            # 运行产物（gitignored）
```

落地 checklist：

- [ ] 模块目录建成，`data/` 落在模块内且被 git 忽略；
- [ ] 凭据只读仓库根 `.env`，代码里不出现 token；
- [ ] 单命令可跑 + 一轮 dry-run 可验证（不联网也能跑测试）；
- [ ] 至少几个核心单测；CI 加一个 job；
- [ ] 模块 README 写好；根 `README.md` 模块表加一行；HANDOFF 加一节状态/命令；
- [ ] 调度 Label 用 `com.crypto-research.<模块名>`，安装脚本自带清理旧任务。

---

## 5. 回滚

```bash
cd ~/vscode/crypto-research
# 1) 卸掉新定时任务
./crypto-news/scripts/install_launchd.sh --uninstall
# 2) 回到迁移前
git reset --hard pre-module-split
# 3) 恢复被 mv 的数据与环境
mv crypto-news/data data 2>/dev/null || true
rm -rf crypto-news/.venv && rm -rf crypto-news
# 4) 重装旧任务（若旧 plist 已被清理，可用备份包恢复）
./scripts/install_launchd.sh
```

备份包：`/tmp/crypto-research-backup-20261108-0205.tgz`（含迁移前的完整工作区，排除 `.git`、`.venv`）。

---

## 6. 后续建议（按优先级）

> 说明：原先的 `crypto-monitor/`（Node 价格告警）已由移除，不在本仓库；需要价格告警时按 §4 模板新建 `crypto-price/`，不要再把它塞回 `crypto-news/` 或仓库根。

1. **`crypto-price/`**：价格/衍生品异动实时告警。注意秒级轮询需要常驻调度（launchd `KeepAlive` 或 VPS），且上线前先补规则单测；凭据与推送格式复用根 `.env` 与 P0/P1/P2 语义。
2. `crypto-news` 内部再演进（可选）：`fetch.py` 的信源 handler 超过 10 类时抽 adapter 注册表；Phase 4 早警指标独立成 `indicators` 模块；日报正文接 LLM 三行摘要。
3. 模块变多（>4 个）时再考虑加一层 `modules/` 或 workspace 工具（如 Makefile 统一 `verify`），现在不要提前抽象。
