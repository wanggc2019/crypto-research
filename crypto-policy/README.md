# crypto-policy · 国内数字货币政策/法规/案例雷达 + 法规库 + 解读草稿

> 本目录是 `crypto-research` 仓库的需求模块；仓库约定见 [../README.md](../README.md) 与
> [../docs/conventions.md](../docs/conventions.md)。共享能力用 `libs/crypto_core`。
>
> 完整文档：[需求](../docs/crypto-policy/requirement.md) · [设计](../docs/crypto-policy/design.md) ·
> [业务架构](../docs/crypto-policy/architecture.md) · [技术架构](../docs/crypto-policy/technical.md)

**一句话**：盯央行/外汇局等权威源，把「新法规、新案例」按文号收进法规库，
P0 立即推、P1 当日汇总，P0/P1 进 `data/pending/` 等 AI 生成解读草稿。
**程序先过滤，AI 只处理候选**；AI 不扫描全量、不决定是否推送。

## 运行

```bash
./scripts/run.sh                 # 跑一轮：采集+分类+入库+报告，不推送（安全默认）
./scripts/run.sh --notify        # 跑一轮并推送新 P0/P1
./scripts/run.sh --dry-run       # 只打印，不写盘、不推送
./scripts/run.sh --test          # 发一条测试卡片
./scripts/run.sh --only pbc_tiaofasi --limit 5   # 调试单源
./scripts/run.sh --fixtures tests/fixtures --dry-run  # 离线回放（CI 用）
./scripts/interpret.py --dry-run                 # 预览解读草稿（pending 为空则退出）
./scripts/interpret.py --notify                  # 生成并推送解读草稿
./scripts/interpret.py --doc-id <id> --notify    # 对指定文档出草稿（可回溯）
./scripts/push_text.py data/reports/interpret-<id>.md --id <id>  # DSH 任务推送用
```

调度（launchd，每 1 小时，来自 `config/app.yaml` 的 `poll_seconds`）：

```bash
make launchd module=crypto-policy action=install   # 唯一任务：每 6h 采集 + 推送 + 有新政策立即解读 + 每轮自检
make launchd module=crypto-policy action=status
```

数据源：

- **国内（5 可用 + 2 备用）**：央行条法司、央行新闻、外汇局政策法规、证监会规章库、最高法司法解释；
  最高法公报 2 源因站点对本机不稳定暂备用；最高检（403）/案例库（需登录）不接入。
- **国外（9 可用 + 1 备用）**：
  - 机构 RSS（新闻/执法/讲话）：美国 SEC、CFTC、美联储；欧盟 ECB、ESMA；英国 FCA；日本 FSA；新加坡 MAS 备用；
  - 法规原文：美国 **Federal Register**（Rule / Proposed Rule / Notice）、欧盟 **EUR-Lex**（法规检索，按日期排序）。
  国外源**只在标题命中 crypto 主题时进库**；最终规则→P0，拟议规则/执法/指引→P1，公告/新闻稿→P2；
  解读会写明辖区、文件状态、适用范围及对境内用户/中资机构的含义。

## 配置

| 文件 | 作用 |
|---|---|
| `config/app.yaml` | `poll_seconds`（3600=1h）、单轮上限、主题词、`ignore_quiet_hours` |
| `config/sources.yaml` | 源清单 + 列表链接正则；加源只改这里 |
| `config/rules.yaml` | 文档类型/等级/文号/施行日/废止正则 |

## 产物

| 产物 | 路径 |
|---|---|
| 法规库（文号/状态/替代关系） | `data/index/policies.json` |
| 速报（人读） | `data/reports/<date>.md` |
| 周报（手动，不排期） | `data/reports/weekly-<date>.md`（无变化默认静默） |
| 跨模块只读契约 | `data/exports/policy-<date>.json`（`schema: policy/daily/v1`） |
| 待 AI 解读 | `data/pending/<id>.json`（仅 P0/P1；解读后移到 `done/`） |
| 解读草稿 | `data/reports/interpret-<id>.md`（AI/规则，标注非法律意见） |
| 原文正文 | `data/docs/<id>.json` |

## 规则门（MVP）

- 文档类型：司法解释 / 法律 / 行政法规 / 部门规章 / 规范性文件 / 案例 / 解读；
- 等级：法律·行政法规·司法解释 → **P0**；央行/证监会/外汇局规范性文件标题命中虚拟货币/稳定币/RWA/挖矿 → **P0**，否则 P1/P2；典型案例 → P1；解读/新闻 → P2；
- 去重：URL 哈希 + 已见集合；首次运行只建**基线**，不推送；
- 废止关系：新文件正文「…（银发〔2021〕237号）同时废止」→ 旧条目自动 `repealed`。

## AI 解读（事件驱动，已上线）

主任务检测到新 P0/P1 后写入 `data/pending/`，`collect.sh` **立即调用** `scripts/interpret.sh`，
用 `dsh headless` 读原文生成「效力/要点/影响/与旧规对比/风险」，推送时标注 **「AI 草稿 · 非法律意见」**。
`pending/` 为空则完全不启动模型（零成本）；**不设静默时段，24/7 即时推送**。

## 测试

```bash
PYTHONPATH=../libs/crypto_core/src python -m unittest discover -s tests -v
PYTHONPATH=../libs/crypto_core/src python scripts/e2e.py
```

`make gate` 会自动发现本模块（单测 + `scripts/e2e.py`）。
