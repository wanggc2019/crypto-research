# Day-1 零成本跑通清单

> 目标：不花一分钱、不装第三方依赖，先把"采集 → 落盘"这条链跑起来，**只记录、不告警**。
> 适用环境：macOS + 系统自带 Python 3.9（已确认无 `requests`、无 `PyYAML`，所以全程用标准库 + Ruby 校验）。
>
> **2026-11-08 更新**：本文是最初的 Day-1 跑通记录，仅作历史参考；脚本已全部落地，且代码已迁入 `crypto-news/` 子目录。
> 实际用法以 [根 README](../README.md) 与 [crypto-news/README.md](../crypto-news/README.md) 为准：
> `crypto-news/scripts/run.sh` / `collect.sh` / `install_launchd.sh`，产物在 `crypto-news/data/`，凭据在仓库根 `.env`。

---

## 0. 目录

```
crypto-news/                  # 2026-11-08 起：仓库内的需求模块目录（下表为最初结构）
├── config/
│   ├── watchlist.yaml     # 只看 BTC/ETH/BNB（SOL/ASTER 冻结）
│   ├── sources.yaml       # 零成本可达信源（含 verified 标记）
│   ├── entities.yaml      # 实体词典
│   ├── rules.yaml         # 防噪/防骗/去重
│   ├── thresholds.yaml    # 评分与 P0 硬条件
│   └── notify.yaml        # Telegram 送达
├── scripts/               # fetch / score / notify / run / collect / install_launchd
└── data/raw/YYYY-MM-DD/   # 原始 JSONL（可回放）
```

## 1. 建 Telegram Bot（5 分钟，免费）

1. Telegram 搜 `@BotFather` → 发 `/newbot` → 起名 → 拿到 `BOT_TOKEN`。
2. 点开你新建的 Bot，随便发一句 `hi`。
3. 浏览器打开 `https://api.telegram.org/bot<TOKEN>/getUpdates`，在返回 JSON 里找 `"chat":{"id":123456789,...}`，这就是 `CHAT_ID`。
4. 写入环境变量（建议放 `~/.zshrc` 或本目录 `.env`，不要提交）：

```bash
export TELEGRAM_BOT_TOKEN="123456:ABC..."
export TELEGRAM_CHAT_ID="123456789"
```

5. 自测发送：

```bash
curl -s "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/sendMessage" \
  -d "chat_id=$TELEGRAM_CHAT_ID" \
  --data-urlencode "text=✅ crypto-intel 通道打通" \
  -d "parse_mode=HTML" | head -c 200
```

## 2. 校验配置

Python 没有 PyYAML，用系统自带 Ruby 校验：

```bash
ruby -ryaml -e 'Dir["config/*.yaml"].sort.each{|f| d=YAML.load_file(f); puts "OK   #{f}" }'
```

预期 6 行 OK。任何一行报错，先修 YAML 再往下走。

## 3. 手动跑第一次采集（只落盘，不推送）

脚本已落地（`crypto-news/scripts/fetch.py`）。以下是最初的跑通标准：

- 输入：`config/sources.yaml` 里 `verified: true` 的条目；
- 输出：`crypto-news/data/raw/YYYY-MM-DD/items.jsonl`，每行一条 `{source_id, ts, title, url, raw}`；
- 约束：单次运行不超过 30 秒、每源不重复拉、失败重试 2 次后跳过；
- **禁止**：这一步不评分、不推送、不调 Telegram。

## 4. 观察 3 天，再决定下一步

记录三个数：
1. 每天抓到的条数（预期：全部源合计 300–800 条/天）；
2. 其中**真正跟你 3 个标的（BTC/ETH/BNB）有关**的条数（预期：10–30 条/天）；
3. 误报/明显无关的比例。

如果第 2 个数 > 50，说明过滤还不够，先调 `rules.yaml` 再谈评分。

## 5. 之后才做（按顺序）

1. `score.py`：分类 + 打分 + 分级；
2. 人工复核一周，标误报/漏报；
3. `route.py`：接 Telegram，**先只推 P2 日报**，跑通再加 P1/P0；
4. 最后才是定时任务。

> 状态（2026-11-08）：以上 4 步已全部完成，定时任务已由 launchd 每 10 分钟运行。

## 已知限制（零成本阶段的取舍）

| 限制 | 影响 | 以后怎么补 |
|---|---|---|
| 无 X API | 错过 KOL 提前量 | 付费按量接入，或只用 Telegram 频道 |
| 无 CoinGlass | 拿不到精确清算/OI | 付费接入 |
| ETF 流数据被 Cloudflare 拦 | ETF 只能看标题级 | SoSoValue/付费源或发行商 AUM 推算 |
| 无分布式部署 | 本机不常开就断采 | VPS 或 launchd 保活 |
| 中文媒体无 RSS | 用 Google News 站点检索代理 | 自建 RSSHub |

**原则：零成本阶段宁缺毋滥，缺的那几块恰好是"增强"，不是"必需"。**
