# crypto-research 仓库级任务入口
#
# 用法：
#   make setup     # 建根 .venv（可选；没有 venv 也能跑，脚本会回退 python3）
#   make lint      # ruff 静态检查（libs / tools / 各模块）
#   make test      # libs 单测 + 各模块单测（全部离线）
#   make verify    # lint + test + crypto-news 端到端评分 + crypto-price 离线回放（提交前跑这个）
#   make gate      # 统一质量门：ruff + 全部单测 + 回放 E2E，全绿才打印「测试通过」
#   make gate online=1 strict=1   # 额外联网冒烟（strict 时外部失败也算失败）
#   make gate push=1              # 额外发一条静默 Telegram 测试消息（谨慎）
#   make hooks     # 安装 git pre-push 钩子：每次 push 前自动 make gate
#   make keep-awake           # AC 电源下保持 Mac 唤醒（caffeinate -s），避免夜间漏采
#   make keep-awake-status    # 查看唤醒任务状态
#   make keep-awake-uninstall # 卸载（恢复系统休眠）
#   make new-module name=crypto-price desc="价格告警"
#   make jobs                            # launchd 任务总览（只读；json=1 机器可读 / strict=1 有问题退出 1）
#   make healthcheck-jobs [notify=1]      # 仓库级 launchd 健康检查（notify=1 异常时推 Telegram，60 分钟冷却）
#   make launchd module=crypto-news action=install|status|uninstall [template=healthcheck]
#   make healthcheck module=crypto-news [fix=1] [notify=1]   # 手动跑一次健康检查
#   make prune module=crypto-news          # 数据保留清理（默认 dry-run）
#   make prune module=crypto-news apply=1  # 真正删除

REPO := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
PY   := $(shell if [ -x $(REPO)/.venv/bin/python ]; then echo $(REPO)/.venv/bin/python; else echo python3; fi)
LIBS := $(REPO)/libs/crypto_core/src
ALL_MODULES := $(patsubst %/,%,$(wildcard crypto-*/))
MODULES := $(patsubst %/tests,%,$(wildcard crypto-*/tests))
LINT_DIRS := libs/crypto_core/src tools $(foreach m,$(ALL_MODULES),$(wildcard $(m)/scripts) $(wildcard $(m)/tests))

.PHONY: setup lint test verify gate hooks new-module launchd jobs jobs-alert healthcheck-jobs prune clean

setup:
	@test -x $(PY) || (python3 -m venv $(REPO)/.venv && $(PY) -m pip install -q pyyaml ruff)
	@echo "venv: $(PY)"

lint:
	@$(REPO)/.venv/bin/ruff check $(LINT_DIRS)

test:
	@PYTHONPATH=$(LIBS) $(PY) -m unittest discover -s $(REPO)/libs/crypto_core/tests -v
	@PYTHONPATH=$(LIBS) $(PY) -m unittest discover -s $(REPO)/tools/tests -v
	@set -e; for m in $(MODULES); do \
		echo "== $$m =="; \
		$(PY) -m unittest discover -s $(REPO)/$$m/tests -v; \
	done

verify: lint test
	@echo "== crypto-news 端到端（离线重评分）=="
	@$(PY) $(REPO)/crypto-news/scripts/score.py
	@echo "== crypto-price 端到端（离线快照回放）=="
	@TMP=$$(mktemp -d); \
	for f in snapshot_normal snapshot_shock; do \
		$(PY) $(REPO)/crypto-price/scripts/run.py \
			--snapshot $(REPO)/crypto-price/tests/fixtures/$$f.json \
			--data-dir $$TMP --dry-run --json >/dev/null || exit 1; \
	done; rm -rf $$TMP
	@echo "== crypto-analyst 每日简报（离线 fixtures 回放）=="
	@$(PY) $(REPO)/crypto-analyst/scripts/daily_brief.py \
		--fixtures $(REPO)/crypto-analyst/tests/fixtures --date 2026-11-08 --dry-run >/dev/null
	@$(PY) $(REPO)/crypto-analyst/scripts/weekly.py \
		--fixtures $(REPO)/crypto-analyst/tests/fixtures --date 2026-11-08 --dry-run >/dev/null
	@echo "=== 全部通过 ✅ ==="

gate:
	@$(PY) $(REPO)/tools/gate.py \
		$(if $(online),--with-network,) $(if $(strict),--strict-network,) \
		$(if $(push),--push-test,) $(if $(only),--only $(only),)

hooks:
	@git config core.hooksPath .githooks
	@chmod +x .githooks/pre-push
	@echo "✅ 已启用 .githooks：push 前自动运行 make gate"

keep-awake:
	@$(PY) $(REPO)/tools/keep_awake.py --install

keep-awake-status:
	@$(PY) $(REPO)/tools/keep_awake.py --status

keep-awake-uninstall:
	@$(PY) $(REPO)/tools/keep_awake.py --uninstall

new-module:
	@test -n "$(name)" || (echo "用法：make new-module name=crypto-price desc='...'"; exit 2)
	@$(PY) $(REPO)/tools/new_module.py $(name) --desc "$(desc)"

jobs:
	@$(PY) $(REPO)/tools/jobs.py $(if $(json),--json,) $(if $(strict),--strict,) $(if $(errors),--strict-errors,)

jobs-alert:
	@$(PY) $(REPO)/tools/healthcheck.py --module all --quiet --notify

healthcheck-jobs:
	@$(PY) $(REPO)/tools/healthcheck.py --module all $(if $(notify),--notify,) $(if $(json),--json,)

launchd:
	@test -n "$(module)" || (echo "用法：make launchd module=crypto-news action=status"; exit 2)
	@$(PY) $(REPO)/tools/render_launchd.py --module $(module) --$(or $(action),status) \
		$(if $(template),--template $(template),)

healthcheck:
	@test -n "$(module)" || (echo "用法：make healthcheck module=crypto-news [fix=1] [notify=1]"; exit 2)
	@$(PY) $(REPO)/tools/healthcheck.py --module $(module) \
		$(if $(fix),--fix,) $(if $(notify),--notify,)

prune:
	@test -n "$(module)" || (echo "用法：make prune module=crypto-news [apply=1]"; exit 2)
	@$(PY) $(REPO)/tools/prune.py --module $(module) $(if $(apply),--apply,)

clean:
	@find $(REPO)/libs $(REPO)/tools $(foreach m,$(ALL_MODULES),$(REPO)/$(m)) -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
