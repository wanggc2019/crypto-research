# -*- coding: utf-8 -*-
"""全仓唯一的 LLM 入口（ADR 0011）：按功能名从 config/ai.yaml 选提供商/模型，再发请求。

调用方只写功能名：
    spec = llm.resolve("x-kol.analyze")            # {"task", "provider", "label", "model", "effort", "key_env", ...}
    key = llm.key_for(spec)                         # 没有 key 返回 ""，调用方自行降级
    text = llm.complete(spec, messages, key)        # 失败抛 LLMError
或一步到位：llm.chat_for("policy.interpret", messages)（没有 key 也抛 LLMError）。

提供商：claude-code（本机 Claude Code 订阅，claude -p，不需要 API key）、anthropic（官方 SDK + ANTHROPIC_API_KEY）、
deepseek（OpenAI 兼容 /chat/completions，标准库 http）。
只负责「找 key → 发请求 → 取文本 → 抽 JSON」；提示词、批量、缓存、降级留在调用方。
注意：调用即把内容发给对应提供商（推文/标题/政策原文/代码 diff），调用方需自行确认可外发。
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.error

from . import config as _config
from . import env as _env
from . import http as _http
from . import paths as _paths

AI_CONFIG = "config/ai.yaml"
PROVIDER_KINDS = ("claude-code", "anthropic", "deepseek")
NO_KEY_PROVIDERS = ("claude-code",)  # 用本机登录，不需要 key_env
SUBSCRIPTION = "subscription"         # claude-code 的 key_for 返回值（非空 = 可用）
CLAUDE_PATHS = [os.path.expanduser("~/.local/bin"), "/usr/local/bin", "/opt/homebrew/bin"]
EFFORTS = ("low", "medium", "high", "xhigh", "max")

DEFAULT_MODEL = "claude-opus-5-5"
FAST_MODEL = "claude-haiku-5-5"
API_KEY_ENV = "ANTHROPIC_API_KEY"
DEFAULT_MAX_TOKENS = 16000  # 含思考 token；非流式请求的推荐上限，太小会截断
# 服务端拒答回退（stop_reason=refusal 时由 API 在同一次调用里换模型重跑）；Haiku 不支持
FALLBACK_BETA = "server-side-fallback-2026-07-01"
FALLBACK_MODELS = ("claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-2", "claude-fable-5-1", "claude-opus-5")


class LLMError(RuntimeError):
    """请求/HTTP/响应错误（消息含 HTTP 状态码与片段，不含 key）。"""


def find_api_key(env=None, names=(API_KEY_ENV,), use_os_environ=True, use_env_file=True):
    """按顺序找 API key：env dict → 进程环境变量 → 仓库根 .env；都没有返回 ""。"""
    sources = []
    if env is not None:
        sources.append(env)
    if use_os_environ:
        sources.append(os.environ)
    if use_env_file:
        sources.append(_env.load_env())
    for src in sources:
        for name in names:
            value = str(src.get(name) or "").strip()
            if value:
                return value
    return ""


def load_ai_config(path=None):
    """读 config/ai.yaml（不存在返回 {}）。"""
    return _config.load_yaml(path or _paths.repo_path(AI_CONFIG)) or {}


def validate_config(cfg, tasks_in_code=(), agents_in_code=()):
    """返回问题列表（空 = 合法）：提供商种类、默认/各功能字段、agent 字段、代码里用到的功能/agent 是否都已登记。"""
    problems = []
    providers = cfg.get("providers") or {}
    for name in providers:
        if name not in PROVIDER_KINDS:
            problems.append("providers.%s：不支持的提供商（只实现了 %s）" % (name, " / ".join(PROVIDER_KINDS)))
        elif name not in NO_KEY_PROVIDERS and not (providers[name] or {}).get("key_env"):
            problems.append("providers.%s：缺少 key_env" % name)
    tasks = cfg.get("tasks") or {}
    for task in ["defaults"] + sorted(tasks):
        try:
            resolve(task if task != "defaults" else None, cfg=cfg)
        except LLMError as e:
            problems.append(str(e))
    for task in sorted(set(tasks_in_code) - set(tasks)):
        problems.append("tasks.%s：代码里用到了，但 config/ai.yaml 没有登记" % task)
    agent_tasks = (cfg.get("agents") or {}).get("tasks") or {}
    for task in sorted(agent_tasks):
        try:
            resolve_agent(task, cfg=cfg)
        except LLMError as e:
            problems.append(str(e))
    for task in sorted(set(agents_in_code) - set(agent_tasks)):
        problems.append("agents.tasks.%s：脚本里用到了，但 config/ai.yaml 没有登记" % task)
    return problems


def resolve(task, cfg=None):
    """功能名 → 生效配置（defaults ← tasks[task]，再补提供商信息）。未登记的功能抛 LLMError。"""
    cfg = load_ai_config() if cfg is None else cfg
    tasks = cfg.get("tasks") or {}
    if task is not None and task not in tasks:
        raise LLMError("tasks.%s：config/ai.yaml 没有登记这个功能" % task)
    defaults = cfg.get("defaults") or {}
    own = (tasks.get(task) or {}) if task else {}
    where = "tasks.%s" % task if task else "defaults"
    provider = own.get("provider") or defaults.get("provider") or "anthropic"
    pinfo = (cfg.get("providers") or {}).get(provider)
    if provider not in PROVIDER_KINDS or pinfo is None:
        raise LLMError("%s：提供商 %r 未在 providers 里定义或不支持" % (where, provider))
    # 换了提供商的功能不继承 defaults 的 model/effort（别家的模型名不能用），改用该提供商的 default_model
    inherit = defaults if provider == (defaults.get("provider") or "anthropic") else {}
    model = own.get("model") or inherit.get("model") or pinfo.get("default_model")
    if not model:
        raise LLMError("%s：缺少 model" % where)
    effort = own.get("effort", inherit.get("effort"))
    spec = {"max_tokens": own.get("max_tokens") or defaults.get("max_tokens")}
    if effort and effort not in EFFORTS:
        raise LLMError("%s：effort=%r 不合法（%s）" % (where, effort, "/".join(EFFORTS)))
    return {
        "task": task, "provider": provider, "label": pinfo.get("label") or provider, "model": str(model),
        "effort": effort if provider in ("anthropic", "claude-code") else None,
        "key_env": pinfo.get("key_env") or "", "base_url": pinfo.get("base_url"),
        "max_tokens": int(spec.get("max_tokens") or DEFAULT_MAX_TOKENS),
    }


AGENT_RUNNERS = ("claude", "dsh")
AGENT_AUTHS = ("subscription", "api")


def resolve_agent(task, cfg=None):
    """headless agent 功能名 → 生效配置（agents.defaults ← agents.tasks[task]）。未登记/字段不合法抛 LLMError。"""
    cfg = load_ai_config() if cfg is None else cfg
    agents = cfg.get("agents") or {}
    tasks = agents.get("tasks") or {}
    if task not in tasks:
        raise LLMError("agents.tasks.%s：config/ai.yaml 没有登记这个 agent" % task)
    spec = dict(agents.get("defaults") or {})
    spec.update(tasks.get(task) or {})
    where = "agents.tasks.%s" % task
    runner = spec.get("runner") or "claude"
    auth = spec.get("auth") or "subscription"
    if runner not in AGENT_RUNNERS:
        raise LLMError("%s：runner=%r 不支持（%s）" % (where, runner, "/".join(AGENT_RUNNERS)))
    if auth not in AGENT_AUTHS:
        raise LLMError("%s：auth=%r 不支持（%s）" % (where, auth, "/".join(AGENT_AUTHS)))
    tools = spec.get("allowed_tools") or []
    if runner == "claude" and (not tools or not isinstance(tools, list)
                               or not all(isinstance(t, str) and t for t in tools)):
        raise LLMError("%s：allowed_tools 必须是非空字符串列表" % where)
    bad = [t for t in tools if isinstance(t, str) and t.startswith("Write(")]
    if bad:
        raise LLMError("%s：%s 不生效——Claude Code 的文件权限只认 Edit(路径)，请改成 Edit(...)" % (where, bad[0]))
    effort = spec.get("effort")
    if effort and effort not in EFFORTS:
        raise LLMError("%s：effort=%r 不合法（%s）" % (where, effort, "/".join(EFFORTS)))
    return {"task": task, "runner": runner, "auth": auth, "model": spec.get("model") or None, "effort": effort,
            "allowed_tools": list(tools), "timeout_seconds": int(spec.get("timeout_seconds") or 1800)}


def claude_binary():
    """本机 claude 命令（launchd 的 PATH 很干净，额外找 ~/.local/bin 等）；找不到返回 ""。"""
    path = os.pathsep.join([os.environ.get("PATH", "")] + CLAUDE_PATHS)
    return shutil.which("claude", path=path) or ""


def key_for(spec, env=None, use_os_environ=True, use_env_file=True):
    """按 spec 的 key_env 找 key（见 find_api_key）；没有返回 ""。claude-code：装了 claude 就返回 SUBSCRIPTION。"""
    if spec["provider"] in NO_KEY_PROVIDERS:
        return SUBSCRIPTION if claude_binary() else ""
    return find_api_key(env=env, names=(spec["key_env"],), use_os_environ=use_os_environ,
                        use_env_file=use_env_file)


def complete(spec, messages, api_key, timeout=60, retries=2, base_url=None):
    """按 spec 的提供商发一次请求，返回文本；失败抛 LLMError。base_url 只给离线测试用。"""
    if spec["provider"] == "claude-code":
        return _claude_code_chat(messages, spec["model"], spec.get("effort"), timeout=timeout)
    if spec["provider"] == "deepseek":
        return _openai_compat_chat(messages, api_key, spec["model"], base_url or spec.get("base_url"),
                                   max_tokens=spec["max_tokens"], timeout=timeout, retries=retries)
    return chat(messages, api_key, model=spec["model"], max_tokens=spec["max_tokens"], effort=spec.get("effort"),
                timeout=timeout, retries=retries, base_url=base_url)


def chat_for(task, messages, env=None, timeout=60, retries=2):
    """resolve + key_for + complete；没有 key 也抛 LLMError。"""
    spec = resolve(task)
    key = key_for(spec, env=env)
    if not key:
        raise LLMError("missing %s（%s 用 %s）" % (spec["key_env"] or "claude 命令", task, spec["label"]))
    return complete(spec, messages, key, timeout=timeout, retries=retries)


def _claude_code_chat(messages, model, effort=None, timeout=60, binary=None):
    """claude -p 当纯文本补全用：不给任何工具、自定义 system prompt、新会话、不读用户设置；用本机订阅登录。"""
    binary = binary or claude_binary()
    if not binary:
        raise LLMError("找不到 claude 命令（Claude Code 未安装）")
    system, rest = _split_system(messages)
    prompt = "\n\n".join(str(m["content"]) for m in rest)
    cmd = [binary, "-p", "--no-session-persistence", "--setting-sources", "project,local", "--tools", "",
           "--output-format", "json", "--model", model]
    if effort:
        cmd += ["--effort", effort]
    if system:
        cmd += ["--system-prompt", system]
    env = dict(os.environ)
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):  # 留着会被改成按量计费
        env.pop(name, None)
    env["PATH"] = os.pathsep.join([env.get("PATH", "")] + CLAUDE_PATHS)
    try:
        proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout, env=env,
                              cwd=tempfile.gettempdir())  # 中性目录：不带入任何项目上下文
    except subprocess.TimeoutExpired:
        raise LLMError("claude -p 超过 %s 秒" % timeout) from None
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        raise LLMError("claude -p 退出码 %s：%s" % (proc.returncode, (proc.stderr or proc.stdout)[-300:].strip())) from None
    if data.get("is_error") or data.get("subtype") != "success":
        raise LLMError("claude -p 失败（%s，%s）：%s" % (data.get("subtype"), data.get("api_error_status"),
                                                   str(data.get("result") or "")[:300]))
    if data.get("stop_reason") == "refusal":
        raise LLMError("模型拒绝回答")
    if data.get("stop_reason") == "max_tokens":
        raise LLMError("输出被 max_tokens 截断")
    text = str(data.get("result") or "").strip()
    if not text:
        raise LLMError("claude -p 没有返回文本")
    return text


def _openai_compat_chat(messages, api_key, model, base_url, max_tokens=None, timeout=60, retries=2):
    """OpenAI 兼容 /chat/completions（DeepSeek 等）；返回 choices[0].message.content。"""
    if not api_key:
        raise LLMError("missing API key")
    if not base_url:
        raise LLMError("缺少 base_url")
    payload = {"model": model, "messages": list(messages or [])}
    if max_tokens:
        payload["max_tokens"] = min(int(max_tokens), 8192)  # DeepSeek chat 输出上限 8K
    url = base_url.rstrip("/") + "/chat/completions"
    last = None
    for _ in range(int(retries or 0) + 1):
        try:
            data = _http.post_json(url, payload, timeout=timeout, headers={"Authorization": "Bearer " + api_key})
            text = data["choices"][0]["message"]["content"]
            if not isinstance(text, str) or not text.strip():
                raise ValueError("空回答")
            return text.strip()
        except urllib.error.HTTPError as e:
            try:
                detail = e.read().decode("utf-8", "ignore")[:300]
            except Exception:  # noqa: BLE001
                detail = ""
            finally:
                e.close()
            last = LLMError("HTTP %s: %s" % (e.code, detail))
            if 400 <= e.code < 500 and e.code != 429:
                break  # 参数/鉴权错误重试也没用
        except urllib.error.URLError as e:
            last = LLMError("网络错误：%s" % e)
        except (ValueError, KeyError, IndexError, TypeError) as e:
            last = LLMError("响应格式不对：%s: %s" % (type(e).__name__, e))
        except OSError as e:  # socket.timeout 等
            last = LLMError("%s: %s" % (type(e).__name__, e))
    raise last


def _split_system(messages):
    """OpenAI 风格的 [{"role": "system"}, ...] → (system 文本, 其余 messages)。"""
    system, rest = [], []
    for m in messages or []:
        if m.get("role") == "system":
            system.append(str(m.get("content") or ""))
        else:
            rest.append({"role": m["role"], "content": m["content"]})
    return "\n\n".join(s for s in system if s), rest


def _text_of(resp):
    if resp.stop_reason == "refusal":
        details = getattr(resp, "stop_details", None)
        raise LLMError("模型拒绝回答（%s）" % (getattr(details, "category", None) or "未说明类别"))
    if resp.stop_reason == "max_tokens":
        raise LLMError("输出被 max_tokens 截断")
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
    if not text:
        raise LLMError("响应里没有文本（stop_reason=%s）" % resp.stop_reason)
    return text


def chat(messages, api_key, model=None, max_tokens=DEFAULT_MAX_TOKENS, effort=None,
         timeout=60, retries=2, base_url=None):
    """调一次 Messages API，返回回答文本（str）。失败抛 LLMError。

    messages 可含 role=system（自动并入 system 参数）；effort: low|medium|high|xhigh|max（None=模型默认）；
    retries 交给 SDK（429/5xx/网络错误指数退避重试）；base_url 只给离线测试用。
    """
    if not api_key:
        raise LLMError("missing API key")
    import anthropic  # 延迟导入：不调 LLM 的模块不需要 SDK

    model = model or DEFAULT_MODEL
    system, msgs = _split_system(messages)
    kwargs = {"model": model, "max_tokens": int(max_tokens), "messages": msgs}
    if system:
        kwargs["system"] = system
    if effort:
        kwargs["output_config"] = {"effort": effort}
    client = anthropic.Anthropic(api_key=api_key, base_url=base_url, timeout=timeout,
                                 max_retries=int(retries or 0))
    try:
        if model in FALLBACK_MODELS:
            resp = client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **kwargs)
        else:
            resp = client.messages.create(**kwargs)
    except anthropic.APIStatusError as e:
        raise LLMError("HTTP %s: %s" % (e.status_code, str(e.message)[:300])) from None
    except anthropic.APIConnectionError as e:
        raise LLMError("网络错误：%s" % e) from None
    return _text_of(resp)


def extract_json(text, kind="object"):
    """从模型输出里取 JSON（容忍 ```json 围栏与前后废话）。kind: "object" | "array"。解析失败抛 ValueError。"""
    t = str(text or "").strip()
    t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    open_ch, close_ch = ("{", "}") if kind == "object" else ("[", "]")
    start, end = t.find(open_ch), t.rfind(close_ch)
    if start < 0 or end <= start:
        raise ValueError("模型输出不是 JSON %s" % kind)
    return json.loads(t[start: end + 1])
