# 业务架构：crypto-knowledge

> 只画本模块的业务能力与数据流；仓库级分层/调度见 [../architecture.md](../architecture.md)。

## 1. 业务能力

```mermaid
flowchart LR
  A[课程内容库<br/>160 条 · 4 阶段] --> B[选卡引擎<br/>游标 + SRS]
  B --> C[消息渲染<br/>概念/理论/实践/复盘]
  C --> D[Telegram 推送<br/>13:00 / 19:00 / 21:00 / 23:00]
  D --> E[学习状态<br/>progress.json]
  E --> B
  D --> F[日报存档<br/>data/reports]
  A --> G[内容校验<br/>check_content.py]
```

## 2. 业务数据流

```mermaid
flowchart TD
  L[launchd 三个触发时间] --> S[collect.sh 加锁]
  S --> M[main.py 选时段]
  M -->|13:00| C1[概念轨道下一条]
  M -->|19:00| C2[课程轨道下一条<br/>理论或实践]
  M -->|23:00| C3[今日已发 + 到期待复习]
  C1 --> R[渲染 Telegram HTML]
  C2 --> R
  C3 --> R
  R --> T[发送]
  T -->|成功| W[写 progress.json + reports]
  T -->|失败| X[不写状态 · 下轮重试]
```

## 3. 关键流程：一天的三个班次

```mermaid
sequenceDiagram
  participant L as launchd
  participant C as collect.sh
  participant M as main.py
  participant S as progress.json
  participant T as Telegram

  L->>C: 13:00 触发
  C->>M: run.sh --notify --slot auto
  M->>S: 读取状态（今天 13 是否已发）
  M->>M: 取概念游标条目 → 渲染
  M->>T: 发送概念课（10–12 分钟）
  M->>S: 游标+1，记录 sent[今天][13]，next_due=+1 天

  L->>C: 19:00
  C->>M: run.sh --notify --slot auto
  M->>M: 取课程游标条目（理论/实践）→ 渲染
  M->>T: 发送理论/实践课（12–15 分钟）
  M->>S: 游标+1，记录 sent[今天][19]

  L->>C: 21:00
  C->>M: run.sh --notify --slot auto
  M->>S: 读今日已发 + 进度
  M->>T: 发送今日回顾与总结（约 8 分钟；周日为本周回顾）
  M->>S: 记录 sent[今天][21]（不推进 SRS）

  L->>C: 23:00
  C->>M: run.sh --notify --slot auto
  M->>S: 读今日已发 + 到期待复习
  M->>T: 复盘课：今日检索 + 间隔复习 + 综合思考（正常通知）
  M->>S: 复习条目 reps+1，next_due 按 SRS 递进
```

## 4. 内容阶段与主题

| 阶段 | 主题 | 概念 | 理论 | 实践 |
|---|---|---|---|---|
| 1 | 基础：货币、账本、密码学、比特币 | 20 | 12 | 8 |
| 2 | 机制：以太坊、L2、稳定币、DeFi | 20 | 12 | 8 |
| 3 | 市场：交易所、链上数据、代币经济、宏观 | 20 | 10 | 10 |
| 4 | 输出：监管、安全、前沿、研究方法 | 20 | 11 | 9 |

## 5. 与其它模块的关系

- **只读**：不依赖任何其它 `crypto-*` 模块的运行时数据；课程内容里引用了 `crypto-price` / `crypto-onchain` / `crypto-policy` 的公开 exports 作为实践素材，但不 import、不写入。
- **共享层**：配置/存储/时间/Telegram 推送来自 `libs/crypto_core`。
- **运维**：`tools/render_launchd.py` 安装调度，`tools/healthcheck.py` 做运行期自检，`tools/prune.py` 清理日志。
