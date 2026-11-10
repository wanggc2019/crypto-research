# crypto-knowledge · 每日碎片化学习推送

> 本目录是 `crypto-research` 仓库的一个需求模块（2026-11-09 新建）。
> 仓库级约定见 [../README.md](../README.md) 与 [../docs/conventions.md](../docs/conventions.md)。

**一句话**：每天 13:00 / 19:00 / 21:00 / 23:00（本地时区）各推一条**不同的内容**——概念课、理论/实践课、**今日回顾与总结**、睡前复盘课——用吃饭、通勤、睡前等碎片时间；**周日 21:00 升级为「本周回顾与总结」**，从零基础逐步建立加密行业认知。

- 方案来源：[个人学习方案](../docs/crypto-learning-plan.md)（16 周主题弧线 + 每天 3 节 10–15 分钟课程 + 21:00 回顾总结）
- 内容库：`config/curriculum/stage{1..4}.yaml`，共 160 条（概念 80 / 理论 45 / 实践 35）
- 学习节奏：**间隔复习（SRS）**，条目在第 1/3/7/14/30/60 天再次出现；内容池用完后自动进入复习模式，不空转
- 学习笔记：写进**本地** `learning/`（`.gitignore` 不入库；`new-note.sh` 建周笔记，每天 2 分钟）
- 合规定位：**不交易、不出入金、不荐股**；实践只在测试网/公开只读数据上完成
- 成本：零 API、零第三方依赖；只复用仓库根 `.env` 的 Telegram 凭据

## 运行

```bash
./scripts/run.sh --status                    # 进度 / 内容库存 / 下一条 / 待复习
./scripts/run.sh --slot 13 --dry-run          # 渲染 13:00 概念课（不写盘不推送）
./scripts/run.sh --slot 19 --dry-run          # 渲染 19:00 理论/实践
./scripts/run.sh --slot 21 --dry-run          # 渲染 21:00 今日回顾与总结
./scripts/run.sh --slot 21 --weekly --dry-run # 渲染本周回顾（预览用）
./scripts/run.sh --slot 23 --dry-run          # 渲染 23:00 睡前复盘
./scripts/run.sh --notify                    # 按当前时段发送（collect.sh 用）
./scripts/run.sh --test                      # Telegram 通道测试
./scripts/run.sh --whoami                    # 查看实际使用的 Bot（token 打码）
./scripts/run.sh --date 2026-11-10 --slot 13 --notify --force   # 补发/重发某时段
```

校验课程内容：

```bash
python3 scripts/check_content.py             # 条目数、各阶段分布、schema 问题
```

## 凭据（独立 Bot：独立 Bot）

学习内容默认发到独立 Bot，凭据在仓库根 `.env`（不入库、不打印）：

```bash
TELEGRAM_KNOWLEDGE_BOT_TOKEN=   # @BotFather → /newbot 创建 独立 Bot 后拿到的 token
TELEGRAM_KNOWLEDGE_CHAT_ID=     # 你的用户 ID（先给新 Bot 发一条 /start；可与共享 Bot 相同）
```

- **不填则回退**共享 `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`，不影响其它模块。
- 也兼容 `TELEGRAM_LEARNING_*`、`独立 Bot_*` 命名。
- 填完验证：`./scripts/run.sh --whoami`，应显示 `机器人：独立 Bot`。

## 调度（每天四班）

```bash
make launchd module=crypto-knowledge action=install
make launchd module=crypto-knowledge action=status
make launchd module=crypto-knowledge action=uninstall
```

模板：`deploy/launchd/agent.plist.tmpl`（`StartCalendarInterval` 四个时间点：13:00 / 19:00 / 21:00 / 23:00）。周日 21:00 自动渲染本周回顾。
同日同时段只发一次（`data/state/progress.json` 去重）；错过时段自动跳过，不补发刷屏。

## 数据与产物

```
crypto-knowledge/data/
├── state/progress.json     # 游标 + 每条 SRS 状态 + 当日已发记录（gitignored）
├── reports/<date>.md       # 当天三条消息的存档（人读，长期保留）
└── logs/                   # collect.log / crypto-knowledge.log / launchd.*.log
```

## 文档

- [需求](../docs/crypto-knowledge/requirement.md) · [设计](../docs/crypto-knowledge/design.md)
- [业务架构](../docs/crypto-knowledge/architecture.md) · [技术架构](../docs/crypto-knowledge/technical.md)
- 内容格式规范：[config/curriculum/README.md](config/curriculum/README.md)

## 约定

- 内容只放 `config/curriculum/`；业务逻辑放 `scripts/`；通用能力（配置/存储/推送/时间）用 `libs/crypto_core`。
- 不新增第三方依赖（Python 3.9 标准库；PyYAML 缺失时由共享库回退 Ruby）。
- 单测离线可跑：`python3 -m unittest discover -s tests -v`。
