# 技术架构：crypto-knowledge

> 只写本模块内部实现；仓库级技术架构（分层/依赖/调度）见 [../architecture.md](../architecture.md)。

## 1. 代码结构

```
crypto-knowledge/
├── config/
│   ├── app.yaml                 # 时段、SRS 间隔、消息上限、silent
│   └── curriculum/
│       ├── README.md            # 内容格式规范
│       └── stage{1..4}.yaml     # 160 条课程内容
├── scripts/
│   ├── common.py                # 共享库适配 + 目录常量 + .env
│   ├── content.py               # 加载 / 校验 / 分轨 / 库存统计
│   ├── schedule.py              # 游标、SRS、去重、兜底
│   ├── render.py                # Telegram HTML 渲染 + 截断
│   ├── main.py                  # CLI：选时段 → 构建 → 发送 → 落状态
│   ├── check_content.py         # 内容校验 CLI（CI 用）
│   ├── run.sh                   # 手动/定时统一入口
│   └── collect.sh               # launchd 入口：锁 + run + healthcheck + prune
├── deploy/launchd/agent.plist.tmpl
├── tests/                       # 68 个离线单测 + fixtures
└── data/                        # gitignored：state/reports/logs
```

## 2. 组件职责

| 组件 | 职责 | 不做什么 |
|---|---|---|
| `common.py` | 探测仓库根、注入 `libs/crypto_core`、暴露目录、读 `.env`、日志 | 不写业务逻辑 |
| `content.py` | 读 `stage*.yaml`、schema 校验、拆概念/课程轨、库存统计 | 不做调度决策 |
| `schedule.py` | 游标、SRS 间隔、去重、今日回顾、内容耗尽兜底 | 不渲染、不发送 |
| `render.py` | 概念/理论/实践/复盘四种卡片；HTML 转义；截断 | 不读写状态 |
| `main.py` | 参数解析、时段解析、编排、发送、状态落盘、报告 | 不直接读 YAML 细节 |
| `collect.sh` | 目录锁、调用 run.sh、写 `collect.log`、healthcheck、prune | 不含业务逻辑 |

## 3. 运行链路

```
launchd 13/19/21/23
  → collect.sh
      → mkdir data/state/collect.lock（失败则跳过，exit 0）
      → ./scripts/run.sh --notify
          → main.py --slot auto
              → common.load_config("app.yaml")
              → content.load_all() + validate_or_raise()
              → schedule.normalize(load_state())
              → 选卡（concept / lesson / review）
              → render → truncate
              → notify.send_message()（HTML，正常通知）
              → schedule.mark_sent() → save_state()
              → data/reports/<date>.md 追加
      → 写 fetch/done 行到 collect.log
      → tools/healthcheck.py --quiet --notify --fix
      → tools/prune.py --apply（每日一次）
```

## 4. 状态与数据

| 文件 | 内容 | 生命周期 |
|---|---|---|
| `data/state/progress.json` | 游标、每条 `{first_on,last_on,reps,next_due}`、当日 `sent`、history | 永久（损坏自动回退） |
| `data/reports/<date>.md` | 当天三条消息的 Markdown 存档 | `prune` 默认 365 天 |
| `data/logs/collect.log` | 每轮 start/fetch/done，healthcheck 解析 | 30 天 / 单文件 2MB |
| `data/logs/crypto-knowledge.log` | 发送成功/失败明细 | 同上 |

### 4.1 凭据解析（独立 Bot 优先）

`common.resolve_bot_env()` 按顺序取第一组非空值：

1. `TELEGRAM_KNOWLEDGE_BOT_TOKEN` / `TELEGRAM_KNOWLEDGE_CHAT_ID`（默认，独立 Bot）
2. `TELEGRAM_LEARNING_*`、`TELEGRAM_LEARNING_*`（兼容命名）
3. `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`（共享 Bot 兜底）

`--whoami` 会显示 token 打码、命中的变量名、是否为专用 Bot，并调 `getMe` 核对机器人 username。
`--test` 与正式发送都走同一套解析；发送失败不推进学习状态。

时间语义：全部使用本机本地时间（`timeutil.now()`，部署在 本地时区）。SRS 只比较 `YYYY-MM-DD` 字符串，无时区换算。

## 5. CLI 与退出码

| 参数 | 含义 |
|---|---|
| `--slot 13\|19\|21\|23\|auto` | 时段；`auto` 按当前小时落在 12–15 / 18–20 / 20–22 / 22–24 窗口 |
| `--weekly` | 强制渲染本周回顾（预览/补发用，任意日期） |
| `--date YYYY-MM-DD` | 指定日期（补发/测试） |
| `--dry-run` | 只渲染，不写盘、不推送、不推进状态 |
| `--notify` | 发送并推进状态 |
| `--force` | 同日同时段重发同一条，不推进游标/SRS |
| `--test` | 通道测试 |
| `--status [--json]` | 进度 / 库存 / 待复习 / 下一条 |
| `--content` / `--fixtures` | 指定内容目录（CI 用） |
| `--data-dir` | 指定数据目录（测试用） |

退出码：`0` 成功 / `1` 部分失败 / `2` 配置或内容错误 / `3` 网络或凭据错误 / `4` 锁（collect.sh）。

## 6. 测试

- `test_content.py`：fixtures 加载/分轨/库存；重复 id、缺 quiz、非法 type、过短 section、非法 URL；生产内容 160 条与分布校验。
- `test_schedule.py`：默认/损坏状态、游标推进、SRS 间隔序列、到期与今日排除、耗尽兜底、day_number、progress。
- `test_render.py`：页眉、HTML 转义、理论/实践 emoji、复习 spoiler、截断不切标签。
- `test_e2e.py`：时段映射、dry-run 不写盘、notify 写状态并去重、一天四班、跨天推进、status JSON、非法日期、force 重发。
- 全部离线，不联网、不写生产数据（临时目录 + mock 发送）。
