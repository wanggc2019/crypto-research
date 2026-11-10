# 业务架构：crypto-x-kol —— 关注的 KOL 的 X 推文监控

> 需求见 [requirement.md](requirement.md)，实现细节见 [design.md](design.md)。
> 仓库技术架构（分层/调度）见 [../architecture.md](../architecture.md)。

## 1. 业务能力图

```mermaid
flowchart LR
    subgraph 输入
        K["KOL 白名单<br/>config/kol.yaml"]
        X["X 主页<br/>x.com/&lt;handle&gt;"]
    end
    subgraph 处理
        F["抓取 + 解析<br/>fetch.py"]
        D["指纹去重 / 基线<br/>state/kol-*.json"]
        R["排序 + 日报<br/>digest.py"]
    end
    subgraph 输出
        M["Telegram<br/>新帖推送"]
        P["本地日报<br/>reports/&lt;date&gt;.md"]
    end
    K --> F
    X -->|"r.jina.ai 代理<br/>匿名 20 RPM"| F
    F --> D
    D --> R
    R --> M
    R --> P
```

## 2. 业务数据流

```mermaid
flowchart LR
    A["X 主页 Markdown"] --> B["解析：正文/引用/媒体/指纹"]
    B --> C["首轮基线 / 增量去重"]
    C -->|新帖| D["raw/&lt;date&gt;/posts.jsonl"]
    D --> E["高信号排序 + 日报"]
    E --> F["pushed-&lt;date&gt;.json 去重"]
    F --> G["Telegram（仅 P0/P1；每日上限；夜间只放行 P0）"]
    D --> H["reports/&lt;date&gt;.md"]
```

## 3. 关键流程 / 生命周期

```mermaid
sequenceDiagram
    participant L as launchd（300s）
    participant C as collect.sh（锁）
    participant F as fetch.py
    participant J as r.jina.ai
    participant X as X 主页
    participant D as digest.py
    participant T as Telegram

    L->>C: 每 300s
    C->>F: run.sh --notify
    loop 每个 enabled 账号（间隔 4s）
        F->>J: GET r.jina.ai/https://x.com/<handle>
        J->>X: 抓取并转 Markdown
        X-->>J: 页面
        J-->>F: Markdown
        F->>F: 解析 / 基线 / 指纹去重
    end
    F->>D: 新帖写 raw/<date>/posts.jsonl
    D->>D: 读 raw → 排序 → 写日报
    alt 有待推送且非静默时段
        D->>T: 只发 P0/P1（≤5 条/轮）
        D->>D: 标记 pushed-<date>.json
    else 静默时段
        D->>D: 只写日报，保留待推送
    end
    C->>C: healthcheck --quiet --fix --agent
```

## 4. 与其他模块 / 共享库的关系

| 关系 | 说明 |
|---|---|
| `libs/crypto_core` | `paths/env/config/store/text/timeutil`、`http.get_text`、`notify.check_credentials/send_message`、`timeutil.in_quiet_hours` |
| `crypto-news` | 零 import；T2「高管发声」仍由 crypto-news 负责，本模块只做 KOL 推文监控；共用根 `.env` 的 Telegram 凭据 |
| `crypto-analyst` | 当前不消费本模块数据；如后续要进周报，由本模块提供 `data/exports/` 契约 |
| 调度 | launchd Label `com.crypto-research.crypto-x-kol`，`StartInterval` 取 `app.yaml` 的 `poll_seconds` |
| 健康检查 | `tools/healthcheck.py --module crypto-x-kol`：解析 `collect.log` 的 start/done 与「采集完成」行；失败可自愈/告警 |
| 数据维护 | `tools/prune.py --module crypto-x-kol`：raw 90d / reports 365d / logs 30d / data 500MB 上限 |

## 5. 状态机（单条推文）

```mermaid
stateDiagram-v2
    [*] --> 首页可见: 抓取成功
    首页可见 --> 已记录: 首轮基线（不产出）
    首页可见 --> 新帖: 首轮之后、指纹未见过
    新帖 --> 已推送: Telegram 发送成功
    新帖 --> 待推送: 超过每轮 12 条 / 静默时段 / 发送失败
    待推送 --> 已推送: 后续轮次发送成功
    已推送 --> [*]
```
