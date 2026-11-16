# ADR 0011：项目内 AI 默认换成 Claude，并按功能可配置

- 状态：已采纳（2026-11-16；11-17 修订：默认改为 Claude Code 订阅，不用 API key）：直接调用与 headless agent 均已切换，并按功能可配置
- 背景：项目里的 AI 原来全部是 DeepSeek——4 处直接调用 OpenAI 兼容 `/chat/completions`（`crypto_core.llm`），另有若干脚本通过 `dsh` CLI 起 headless agent。用户要求全部换成 Claude，并且以后能按模块/功能自定义用哪个 AI。
- 决策：
  1. **唯一入口仍是 `crypto_core.llm`**。**默认提供商 `claude-code`**（用户决定：用本机 Claude Code 订阅，不用 `ANTHROPIC_API_KEY`）：`claude -p` 当纯文本补全用——`--tools ""` 不给任何工具、`--system-prompt` 替换 Claude Code 默认提示词、`--no-session-persistence` 新会话、`--setting-sources project,local` 不读用户设置/hooks、在临时目录运行不带项目上下文、启动前从环境里去掉 `ANTHROPIC_API_KEY`，解析 `--output-format json` 的 `result`。另保留可选提供商 `anthropic`（官方 Python SDK `anthropic` 2.9.0 + API key，按量计费）。SDK 要求 Python ≥ 3.10，因此 ADR 0010 的收尾（下限 3.12、CI 只跑 3.12）提前完成；「只用标准库」改为「标准库 + `requirements.txt` 已列出的依赖」。
  2. **按功能配置（`config/ai.yaml`）**：代码里只写功能名（`<模块>.<功能>`），提供商 / 模型 / effort / max_tokens 都从配置读；`defaults` 给未写的字段兜底；换了提供商的功能不继承 `defaults` 的模型，改用该提供商的 `default_model`。支持的提供商：`claude-code`（订阅，默认）、`anthropic`（官方 SDK + key）、`deepseek`（OpenAI 兼容，标准库 HTTP）。新加提供商 = 在 `llm` 里加一种调用方式 + 在配置里登记。
  3. **当前默认**（用户决定；提供商均为 `claude-code`）：

     | 功能名 | 调用方 | 模型 | effort |
     |---|---|---|---|
     | `x-kol.analyze` | `crypto-x-kol/scripts/analyze.py` 推文翻译/总结 | `claude-haiku-5-5` | low |
     | `news.translate` | `tools/translate_titles.py`（crypto-news 标题翻译） | `claude-haiku-5-5` | low |
     | `policy.interpret` | `crypto-policy/scripts/interpret.py` 政策解读草稿 | `claude-opus-5-5` | 默认（medium） |
     | `ci.pr-review` | `tools/ai_review_pr.py`（CI PR 审查） | `claude-opus-5-5` | high（CI 用 `CLAUDE_CODE_OAUTH_TOKEN` 登录订阅，见后果） |

  4. **凭据**：`claude-code` 不需要 key（`key_for` 在找到 `claude` 命令时返回 `subscription`）；其它提供商在配置里只写环境变量名（`providers.<p>.key_env`），值只在仓库根 `.env`（CI 里是同名 GitHub secret）。查找顺序：调用方传入的 env → 进程环境变量 → 根 `.env`；不再读 `~/.dsh/.credentials.yaml`。没有 key 或配置有误时各调用方降级（原文/规则抽取/跳过审查）。
  5. **请求约定**（anthropic）：不发 `temperature`/`top_p`/`top_k`（SDK 2.x 的 API 版本 `2026-10-21` 会拒绝）；`max_tokens` 默认 16000（含思考 token）；`stop_reason` 为 `refusal`/`max_tokens` 时抛 `LLMError`；网络/429/5xx 交给 SDK 指数退避重试。deepseek：4xx（除 429）不重试，`max_tokens` 封顶 8192。
  6. **Opus 默认开服务端拒答回退**（beta `server-side-fallback-2026-07-01`，`fallbacks: "default"`）：遇到安全分类器误拒时，API 在同一次调用内换模型重跑；Haiku 不支持，不开。
  7. **校验**：`tools/tests/test_ai_config.py`（gate 自动跑）检查 `config/ai.yaml` 合法，并扫描代码里的 `llm.resolve("…")` / `llm.chat_for("…")` / `TASK = "…"` 与脚本里的 `agent_run.py <功能名>`，确保每个功能/agent 都已登记。
  8. **headless agent**（`config/ai.yaml` 的 `agents`，`tools/agent_run.py` 启动）：默认 `runner: claude` + `auth: subscription`（用户决定：用本机 Claude Code 登录、占订阅额度、不另计费；启动时从环境里去掉 `ANTHROPIC_API_KEY`，防止被改成按量计费），可按任务改为 `auth: api`（`--bare` + `.env` 的 key）或 `runner: dsh`。每次新会话、不读用户级设置/hooks；**按任务只放行提示词里需要的工具**（`allowed_tools`），其余自动拒绝；`agent_run.py` 另写死拒绝读写 `.env` 与上网。已实测：launchd 式最小环境下订阅登录可用；未放行的写文件/`touch`/`rm` 被拒绝；读 `.env` 被拒绝。
  9. **测试不得真调模型**：单测用本地 http.server 冒充两种 HTTP API（`base_url` 只在测试里传），用假的 `claude` 脚本测 claude-code；gate 时把记录调用的假 `claude` 放到 PATH 最前，启动次数应为 0；各模块回放走 `--no-analyze`/规则路径。验证方法：在环境变量里放假 key 并把 `ANTHROPIC_BASE_URL` 指向本地记录服务跑一遍 gate，请求数应为 0。
- 后果：
  - 默认不产生 API 费用：所有 Claude 调用（直接调用 + agent）都走本机 Claude Code 订阅，与你日常使用共享 5 小时/7 天额度；高频的 x-kol/标题翻译用 Haiku + low，Opus 只用于低频任务。额度紧张时可把单个功能改成 deepseek 或 anthropic。
  - 依赖本机：Mac 上要装着 Claude Code 并保持登录。
  - CI（GitHub Actions）的 PR 审查也走订阅（2026-11-17 用户决定）：`claude setup-token` 生成的长期 token 存为 secret `CLAUDE_CODE_OAUTH_TOKEN`，workflow 装固定版本 `@anthropic-ai/claude-code@2.1.325` 后由 `claude -p` 审查；未配 secret 时不装、审查跳过（warning，不失败）。token 过期需重新 `claude setup-token` 并更新 secret。workflow 运行的是 PR head 的代码（只对同仓库 PR 运行，fork 跳过），因此有推送权限的人能接触到该 token——本仓库只有本人推送。为缩小供应链面，该 job 只装 `.github/ai-review-requirements.txt`（PyYAML，`--no-deps --require-hashes` 按 sha256 锁定）和锁定版本的 Claude Code 2.1.325（其 npm 依赖只有 Anthropic 自己的同版本平台包，npm 按 registry 完整性哈希校验），不装 `requirements.txt` 里的 anthropic SDK 及其未锁版本的间接依赖。`tools/tests/test_workflows.py` 校验：只有一处 pip 安装且只装锁定文件、每个包锁版本+哈希且与 `requirements.txt` 一致、Claude Code 只有一个写死的版本且与本 ADR 一致。
  - 没有 `.venv`（回退系统 `python3`）时 `import anthropic` 失败；SDK 是延迟导入，调用方都已 `except Exception` 降级，不会拖垮采集主链路。
  - 外发数据对象取决于各功能所选提供商（推文、标题、政策原文、PR diff；agent 读到的本地报告/数据）。
  - 额度用尽或登录过期时调用失败，各调用方按原逻辑降级（原文/规则抽取/报告照发、AI 部分跳过）。
