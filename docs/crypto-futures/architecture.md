# 业务架构：crypto-futures

> 业务能力、数据流与关键流程。代码层面见 [technical.md](technical.md)。

## 1. 业务能力

```mermaid
flowchart LR
  subgraph 输入["Binance USD-M 公开接口（单所）"]
    G["账户多空比"]
    T["大户持仓多空比"]
    K["合约主动买卖量"]
    OI["持仓量（张数 + 美元价值）"]
    F["资金费率"]
  end
  subgraph 判断["持仓结构判断"]
    R1["拥挤加杠杆 P0"]
    R2["账户比与大户持仓比背离 P1"]
    R3["账户比急转 P1"]
    R4["合约主动买卖失衡 P1"]
    R5["账户比创 7 天极值 P2"]
  end
  G --> R1 & R2 & R3 & R5
  T --> R2
  K --> R4
  OI --> R1
  判断 --> GOV["治理：冷却 / 加剧 / 合并 / P0 上限"]
  GOV --> CARD["卡片：结论 · 指标 · 背景 · 解读 · 历史参照 · 口径"]
  F --> CARD
  CARD --> TG["Telegram（即时类，全天）"]
  判断 --> EXP["exports: futures/snapshot/v1"]
```

## 2. 一轮的数据流

```mermaid
sequenceDiagram
  participant L as launchd（300s）
  participant R as run.py
  participant B as Binance
  participant S as data/state
  participant N as notify（台账 + 预算）
  L->>R: collect.sh → run.sh --notify
  R->>S: 读 7 天历史、治理状态、触发记录
  R->>B: 冷启动 / 缺口回补（仅需要时）
  R->>B: 4 指标 × 3 币取最近 12 点 + 资金费率
  R->>R: 丢弃未收盘周期 → 判过期 → 规则 → 治理
  R->>N: send_signal（卡片）
  R->>S: 写历史、触发记录、exports、last_run.json
  R->>B: 每天一次：30 天缓存（首次回放预热历史参照）
```

## 3. 与其他模块的关系

```mermaid
flowchart LR
  FUT["crypto-futures"] -->|exports/latest.json| FUTX["futures/snapshot/v1"]
  FUTX -.M3 规划.-> AN["crypto-analyst 日报"]
  FUTX -.M3 规划.-> ON["crypto-onchain 归因"]
  PRICE["crypto-price"] -->|资金费率 / OI 告警（M2 评审是否迁入）| TG["Telegram"]
  FUT --> TG
  FUT --> LIB["libs/crypto_core：http / market.binance / notify / contracts"]
  WD["tools/watchdog + healthcheck"] -->|读 last_run.json| FUT
```

- 和 crypto-price 互不 import：资金费率与 OI 由本模块自己取，price 的告警暂不动（M2 再评审）；
- 下游目前没有消费方（`consumers` 为空），exports 先保证字段完整。
