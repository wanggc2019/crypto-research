# ADR 0011：项目内 AI 统一换成 Claude

- 状态：进行中（2026-11-16）：直接 API 调用已切换；headless agent（原 `dsh`）另起 PR 切到 `claude -p`
- 背景：项目里的 AI 原来全部是 DeepSeek——4 处直接调用 OpenAI 兼容 `/chat/completions`（`crypto_core.llm`），另有若干脚本通过 `dsh` CLI 起 headless agent。用户要求全部换成 Claude。
- 决策：
  1. **唯一入口仍是 `crypto_core.llm`**，内部改用官方 Anthropic Python SDK（`anthropic` 2.9.0，见 `requirements.txt`）。SDK 要求 Python ≥ 3.10，因此 ADR 0010 的收尾（下限 3.12、CI 只跑 3.12）提前完成；「只用标准库」改为「标准库 + `requirements.txt` 已列出的依赖」。
  2. **按任务选模型**（用户决定）：

     | 调用方 | 模型 | effort | 说明 |
     |---|---|---|---|
     | `crypto-x-kol/scripts/analyze.py` 推文翻译/总结 | `claude-haiku-5-5` | low | 每 5 分钟一轮、批量 8 条、按 id 缓存 |
     | `tools/translate_titles.py`（crypto-news 标题翻译） | `claude-haiku-5-5` | low | 批量一次调用、有缓存 |
     | `crypto-policy/scripts/interpret.py` 政策解读草稿 | `claude-opus-5-5` | 默认（medium） | 只处理 P0/P1 候选；`LLM_MODEL` 可覆盖 |
     | `tools/ai_review_pr.py`（CI PR 审查） | `claude-opus-5-5` | high | GitHub secret `ANTHROPIC_API_KEY` |

  3. **凭据**：仓库根 `.env` 的 `ANTHROPIC_API_KEY`（`find_api_key`：调用方传入的 env → 进程环境变量 → 根 `.env`）；不再读 `~/.dsh/.credentials.yaml`。没有 key 时各调用方照旧降级（原文/规则抽取/跳过审查）。
  4. **请求约定**：不发 `temperature`/`top_p`/`top_k`（SDK 2.x 的 API 版本 `2026-10-21` 会拒绝）；`max_tokens` 默认 16000（包含思考 token，过小会截断）；`stop_reason` 为 `refusal`/`max_tokens` 时抛 `LLMError`，由调用方降级；网络/429/5xx 交给 SDK 指数退避重试。
  5. **Opus 默认开服务端拒答回退**（beta `server-side-fallback-2026-07-01`，`fallbacks: "default"`）：遇到安全分类器误拒时，API 在同一次调用内换模型重跑；Haiku 不支持，不开。
  6. **测试不得真调模型**：单测用本地 http.server 冒充 Messages API（`base_url` 只在测试里传）；各模块回放走 `--no-analyze`/规则路径。验证方法：在环境变量里放假 key 并把 `ANTHROPIC_BASE_URL` 指向本地记录服务跑一遍 gate，请求数应为 0。
- 后果：
  - 成本由按量计费的 Anthropic API 承担（与 Claude Code 订阅分开）；Haiku 任务成本可忽略，Opus 只用于低频任务。
  - 没有 `.venv`（回退系统 `python3`）时 `import anthropic` 失败；`llm.chat` 是延迟导入，调用方都已 `except Exception` 降级，不会拖垮采集主链路。
  - 外发数据对象从 DeepSeek 变为 Anthropic（推文、标题、政策原文、PR diff）。
