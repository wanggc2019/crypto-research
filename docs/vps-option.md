# 备选方案：迁到 VPS 做 7×24 运行（暂缓）

> 状态：**暂缓**（2026-11-19 讨论）｜现状见 [HANDOFF §5](HANDOFF.md#5-注意事项)｜运行机制见 [runbook.md](runbook.md)

## 1. 为什么会想到它

所有模块由 launchd 在本机 Mac 上定时运行。Mac 一休眠，定时就停：

- `make keep-awake`（`caffeinate -s`）只在**插电**时防**空闲休眠**，挡不住**合盖**，用电池时也无效；
- 醒来后 launchd 只补跑一轮，错过的轮次不会逐一补跑；
- 以 crypto-futures 为例：历史数据缺口会回补，但规则只评估最近 60 分钟的数据点，**休眠期间的异动不会补发告警**（有意设计，过时告警没有意义）；
- 实测：2026-11-19 合盖约 2 小时，中间只在系统短暂唤醒时跑了一轮，前后各空了 60 多分钟。

## 2. 当前决定

**暂不迁移**：Mac 开盖插电 + `make keep-awake` 已够用，且零成本优先。

**何时重新评估**：合盖 / 休眠空档频繁到漏掉需要的告警。重新评估时按仓库流程先写需求，再实现。

## 3. 不迁 VPS 的过渡办法

| 办法 | 说明 | 注意 |
|---|---|---|
| 开盖插电（现状） | `make keep-awake` 防空闲休眠 | 不能合盖 |
| 外接显示器合盖模式 | 插电 + 外接显示器和键鼠，合盖不休眠（苹果官方支持） | 需要外设 |
| 强制禁止休眠 | `sudo pmset -a disablesleep 1`，恢复用 `sudo pmset -a disablesleep 0` | 全局持久设置，重启后仍生效；合盖放包里会过热、耗电，只在桌面用，出门前关掉；`pmset -g` 可确认当前状态 |

## 4. VPS 选型（重新评估时参考）

VPS = 在云服务商租一台一直开着的虚拟机，按月付费。

- **规格**：1 核 / 1 GB 内存足够（每 5 分钟几十个请求、少量计算）；
- **付费**：约 $4–6/月（Vultr、DigitalOcean、Hetzner、AWS Lightsail、阿里云轻量等）；
- **免费档**：Oracle Cloud Always Free（长期免费，可选非美国区域；注册审核严、名额看运气）；AWS 新用户免费档已改为额度制，以官网为准；Google Cloud e2-micro 免费档只在美国区域，**本项目不可用**（见 §5 第 1 条）；
- 与 Claude Code「云端环境」不同：那是临时的开发会话，会话结束就回收，不能常驻跑定时任务。

## 5. 迁移要点

1. **地区不能选美国**：Binance 对美国 IP 返回 HTTP 451，合约 / 现货接口都取不到；选新加坡、日本或欧洲；
2. **调度**：launchd 只在 macOS 上有；Linux 要新增 systemd timer（或 cron）部署模板，与 `tools/render_launchd.py` 对应，healthcheck / watchdog 的「任务是否在跑」检查也要适配；
3. **凭据**：根 `.env` 安全拷到服务器（权限 600），不进仓库；保管好 SSH 登录密钥；
4. **先停 Mac 再切换**：按 [runbook.md](runbook.md) 卸载 / 下线 Mac 侧 launchd 任务，跑 `make jobs` 确认已无任务（`healthcheck --fix` 会自动补装任务，只关 `--notify` 不够）；两边同时推送会重复告警；
5. **数据**：Mac 停跑**之后**再拷 `data/state/`（含通知台账、限速状态），否则两边状态分叉；也可以冷启动（crypto-futures 会自动回补 7 天历史）；
6. **AI 功能**：`crypto_core.llm` 默认走本机 Claude Code 订阅（`claude -p`），VPS 上没有登录态，需降级或改配置（[ADR 0011](adr/0011-llm-claude.md)）。
