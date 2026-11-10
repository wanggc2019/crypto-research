---
name: crypto-data-source-verification
description: 评估一个新的免费加密数据源（HTTP API / RSS / 网页 / X 账号）是否可用于监控时使用：直连探测、免 key 与限额、代理回退、数据质量、去重键、合规、文档与测试固化。
whenToUse: 需要为加密研究项目新增或替换数据源（价格、新闻、社交、链上、ETF/宏观等），或判断某免费接口是否可靠/是否需要 fallback。
---

# 免费加密数据源验证流程

目标：用最短时间判断“这个源能不能用、怎么接、有什么坑”，并把结论固化到仓库文档/测试，避免下次重复试错。

## 1. 先明确需求

- 需要的字段：时间戳、价格/数值、资产、标题/正文、ID/URL、互动数据？
- 频率：实时（秒）/分钟/小时/天；历史回溯范围；
- 约束：零成本、免 key、可离线回放、单源失败可降级。

## 2. 直连探测（先不写代码）

用 `curl`/`python3 -c urllib` 至少验证：

```bash
curl -sS -o /dev/null -w '%{http_code} %{content_type} %{size_download}\n' --max-time 20 '<URL>'
curl -sS --max-time 20 '<URL>' | head -c 800
```

记录：HTTP 状态、Content-Type、返回大小、是否需要 key/登录、是否被 CF/403/429 拦截、是否登录墙（X/交易所常见）。
**先测 2–3 次**看稳定性；限流/风控往往不是每次 403。

## 3. 能否零成本接入

- 官方 API：查定价页（X API 2026 起纯按量 $0.005/条读；Basic 等档位已取消）；
- 免 key 端点：OKX announcements、Binance/OKX 行情、RSS、Google News、DefiLlama 等；
- 代理：`r.jina.ai`（匿名 20 RPM、免 key、不计 token；免费 key 500 RPM 但消耗一次性 10M token）；
- 第三方聚合：twitterapi.io / TwexAPI / Apify（便宜但非官方，稳定性/合规自负）；
- ToS：抓取 X 页面属灰色地带；个人低频研究可接受，公开产品/转售不行。

## 4. 代理与渲染（网页类）

- `r.jina.ai/<url>` 默认 Markdown：适合正文；**X 主页 Markdown 无时间/互动/status id**；
- 需要原帖 ID/链接：`X-Respond-With: html` 取 HTML，解析 `/status/<id>`；
- 缓存/登录墙/改版都要考虑；用不同 header（X-No-Cache、X-Engine、X-User-Agent）对照测试；
- 失败 fallback：fxtwitter（单条推文 JSON）、Nitter/RSSHub（多数已死）、Google News（线索）。

## 5. 数据质量与去重

- 有无稳定 ID/URL/时间戳？没有时间就记「首次发现时间」；没有互动数据就改口径（如用“跨 KOL 同题”替代热度）；
- 去重键：**不要用整页 hash**（`blob:` URL/广告/推荐位会变）→ 用「主体 + 归一化正文 + 引用」指纹；
- 解析层保留 raw 原文（可回放），展示层可翻译/摘要；
- 单源失败/登录墙：记为 SKIP/WARN，不拖垮整轮；全部失败才 rc=3。

## 6. 固化到仓库

- 源清单/结论写进 `docs/<module>/design.md` 或方案文档的「信源实测」表（✅/⚠️/❌ + 备注 + 成本）；
- 解析写成纯函数 + fixtures 单测；端到端加 `scripts/e2e.py`（离线）；
- 抓取参数（超时/重试/节流/headers）放 `config/*.yaml`，不要硬编码；
- 配置里注明限速/ToS/替代方案；凭据只走 `.env`。

## 7. 决策

- 优先：官方免 key 一手源 → 公开页面 + 代理 → 第三方聚合 → 官方付费 API；
- 若必须付费：先算每条成本与每日量，确认预算；把「为什么不用更贵/更便宜方案」写进 ADR；
- 退出条件：连续多日失败/改版/封禁 → 记录并切换 fallback。

## 8. 本仓库已验证的实例（参考）

- X 高管/KOL 主页：`r.jina.ai` 匿名可用；登录墙 SKIP；逐帖指纹去重；原帖链接需 HTML；
- Farside ETF 流量：直连 403，`r.jina.ai` 可取 Markdown 表；
- OKX announcements：`/api/v5/support/announcements` 免 key，**不要带 annType**；
- Solana 新闻：直连/代理 429 → 改用 status RSS + Google News；
- 无真正免费无 key 的 ETF 流 API（结论写进方案文档）。
