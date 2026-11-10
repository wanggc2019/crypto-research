# 业务架构：crypto-jobs —— 加密行业技术岗位市场情报

> 需求见 [requirement.md](requirement.md)，实现细节见 [design.md](design.md)。
> 仓库技术架构（分层/调度）见 [../architecture.md](../architecture.md)。

## 1. 业务能力图

```mermaid
flowchart LR
    subgraph SRC["招聘数据源（公开、零成本）"]
        T0["T0 官方 ATS<br/>Greenhouse/Lever/Ashby"]
        T1["T1 加密招聘站<br/>web3.career 等"]
        T2["T2 通用远程站<br/>crypto 过滤后"]
    end
    subgraph JOB["crypto-jobs 能力"]
        C["采集<br/>多源适配/降级"]
        N["归一化<br/>角色/资历/远程/去重"]
        S["技能抽取<br/>规则词典 + required 段"]
        K["快照与差分<br/>新增/关闭/变更"]
        M["指标<br/>频率/周环比/升温"]
        R["周报 / 日报<br/>Markdown + Telegram"]
        E["导出<br/>exports/*.json"]
    end
    USER["研究员 / 求职者<br/>Telegram / 本地报告"]
    T0 --> C
    T1 --> C
    T2 --> C
    C --> N --> S --> K --> M --> R --> USER
    K --> E
    M --> E
```

## 2. 业务数据流

```mermaid
flowchart LR
    A["公开岗位<br/>（多源原始响应）"] --> B["raw/&lt;date&gt;/jobs.jsonl<br/>（可回放）"]
    B --> C["归一化岗位 + 技能<br/>（内存）"]
    C --> D["state/snapshots/&lt;date&gt;.json<br/>（每日在招快照）"]
    C --> E["state/jobs-index.json<br/>（first/last seen）"]
    D --> F["差分：新增/关闭/描述变更"]
    E --> F
    F --> G["metrics：技能频率/周环比/角色分布"]
    G --> H["reports/&lt;date&gt;.md、&lt;date&gt;-weekly.md"]
    G --> I["exports/skill-report.json<br/>jobs-latest.json"]
    H --> J["Telegram 周报"]
    I --> K["crypto-analyst 等只读消费"]
```

## 3. 关键流程 / 生命周期

```mermaid
sequenceDiagram
    participant L as launchd（每日 18:00）
    participant M as crypto-jobs
    participant S as 招聘源
    participant FS as data/
    participant U as 用户（Telegram）

    L->>M: collect.sh（加锁）
    M->>S: 串行拉取各源（timeout/重试）
    S-->>M: 岗位列表（JSON/HTML/RSS）
    M->>M: 归一化 → 技能抽取 → 目标角色过滤 → 跨源去重
    alt 全部源失败
        M-->>L: 退出码 3，不写快照
    else 至少一个源成功
        M->>FS: 写 raw / 快照 / 索引 / 日报 / exports
        M->>M: 与上一快照差分（新增/关闭/变更）
    end
    Note over L,M: 周日 10:00 周报轮（同一任务）：先采集 → 再 --weekly --notify
    L->>M: run_weekly.sh --weekly --notify
    M->>FS: 读快照历史 + 7 天前 baseline
    M->>U: 技能趋势周报（≤3800 字符）
```

## 4. 监测闭环

```mermaid
flowchart LR
    D["每日快照"] --> W["每周日 10:00 周报（同一任务）"]
    W --> Q{"有升温/新岗位？"}
    Q -->|是| T["Telegram 周报<br/>（置顶趋势）"]
    Q -->|否| A["仅落盘归档"]
    D --> H["collect.sh 每轮 healthcheck"]
    H -->|异常| F["--fix 自愈 / 推送告警"]
    D --> P["每日 prune（raw/logs/exports）"]
```

## 5. 与其他模块 / 共享库的关系

| 关系 | 说明 |
|---|---|
| `libs/crypto_core` | 只向下依赖：http / config / store / timeutil / text / env / notify |
| `crypto-news` / `crypto-price` / `crypto-policy` | **零 import**；互不影响 |
| `crypto-analyst` | 只读本模块 `data/exports/skill-report.json`、`jobs-latest.json`（只读契约） |
| `tools/healthcheck.py` | 读 `config/app.yaml` 的 `poll_seconds`、`data/state/last_run.json`、`collect.log` |
| `tools/prune.py` | 每日清理 raw/logs/exports，快照由模块自清理 |

## 6. 角色与场景

| 角色 | 场景 | 用的产物 |
|---|---|---|
| 技术研究者 | 判断哪些技能在加密行业变热 / 变冷 | 周报「技能 Top + 升温」、`skill-report.json` |
| 求职者/开发者（DevOps/SRE/MLOps/开发） | 看市场要求、目标公司、远程比例 | 周报「分角色技能 + 活跃公司 + 新岗位」 |
| 仓库维护者 | 排查源失效、词典漏词 | `source-health`、`raw/`、`last_run.json`、失败源折叠块 |
