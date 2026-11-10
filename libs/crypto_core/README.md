# crypto_core —— crypto-research 共享能力库

> 状态：**P0–P2 已落地**（2026-11-08）｜覆盖路径/凭据/配置/存储/时间/事件/去重/推送/HTTP/抓取/行情
> 设计详见 [docs/architecture.md](../../docs/architecture.md) §5–§8

## 定位

仓库内多个需求模块共用的「能力」，不是「策略」：

- ✅ 放这里：路径/凭据/配置/存储/时间、事件模型、去重、推送、HTTP、行情/抓取客户端
- ❌ 不放这里：阈值、评分权重、信源清单、业务路由、调度决策、模块 CLI

约束：只用 Python 3.9 标准库（PyYAML 可选，缺失走系统 Ruby 兜底）；不 import 任何模块；不启动调度；不读 `sys.argv` 做业务决策。

## 当前能力（P0+P1）

| 模块 | 内容 |
|---|---|
| `paths` | `repo_root()` / `module_root(name)` / `find_repo_root()` / `ensure_dirs()` |
| `env` | 根 `.env` 定位与读写（模块内 `.env` 可覆盖）、`get()` |
| `config` | YAML（PyYAML/Ruby 兜底）/ JSON 加载、`check_schema_version()` |
| `timeutil` | `now/today_str/ts_iso`、静默时段、`age_hours()`、`decay()` |
| `store` | JSON 原子读写、JSONL 追加/读取、目录创建 |
| `text` | `clean_text` / `norm_url` / `sha256` / `contains_any` / `norm_title` |
| `events` | `alert/v1` 事件模型 `Alert`、`severity_of()`、`event_key()`（去重键） |
| `dedupe` | `DailyKeys`（按天去重，兼容既有 notified.json）、`Cooldown`（分钟级冷却） |
| `notify` | `check_credentials` / `send_message(..., silent=)` / `discover_chat_id` / `format_alert` / 模板与 HTML 转义 |
| `http` | 统一 UA、308 重定向、超时/重试/退避的 GET / GET-text / GET-json |
| `sources.*` | RSS/Atom 解析、Binance/OKX/Bybit 公告解析、Telegram 预览页、页面指纹与纯文本 |
| `market.*` | Binance 资金费率/24h ticker/K 线/持仓量、OKX ticker/K 线、Coinbase 24h stats/ticker、DefiLlama 稳定币脱锚（全部免 key、坏数据返 None/[]） |
| `events` 展示词表 | `category_cn()` / `hard_cn()` / `impact_cn()` / `action_cn()`（中文类别、硬条件、影响、动作参考） |

## 使用方式

模块无需安装，两种方式二选一：

1. **入口脚本自行探测仓库根**（crypto-news 采用，launchd/直接 `python scripts/x.py` 都能跑）：

```python
import os, sys
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # <module>/
_LIBS = os.path.join(os.path.dirname(_ROOT), "libs", "crypto_core", "src")
if _LIBS not in sys.path:
    sys.path.insert(0, _LIBS)
from crypto_core import events, notify
```

2. **PYTHONPATH**（CI / make / libs 自己的测试）：

```bash
PYTHONPATH=libs/crypto_core/src python -m unittest discover -s libs/crypto_core/tests
```

```python
from crypto_core import events, notify

alert = events.Alert(
    module="crypto-news", severity="P0", category="exchange_custody",
    title="Binance pauses withdrawals", assets=["BNB"],
)
print(notify.format_alert(alert.to_dict()))   # 渲染
# notify.send_message(token, chat_id, text)   # 发送（token 由调用方传入）
```

## 版本与变更

- `__version__` 仅作文档用；仓库内同步演进，不做 PyPI 发布。
- 破坏性改动要求：同一次提交里改完所有调用方 + 更新本文件「当前能力」+ 在 `docs/adr/` 写一条记录。
- 单文件建议 < 500 行；单文件先有测试再加入。

## 测试

```bash
make test            # = libs 单测 + 各模块单测（全离线）
# 或
PYTHONPATH=libs/crypto_core/src python -m unittest discover -s libs/crypto_core/tests -v
```

解析类单测全部用内联样本（`tests/test_sources_market.py`），不联网。
