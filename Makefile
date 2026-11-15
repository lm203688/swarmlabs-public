# SwarmLabs — development Makefile
# Windows-friendly: all commands are single-line, no bash-isms.
# Requires: Python 3.10+ (3.13 recommended). On Windows Git Bash works.

PY     ?= python
PIP    ?= pip
CLI    ?= $(PY) -m swarmlabs
ROOT   := .

.PHONY: help install install-dev demo check test doctor build-ledger \
        rebuild serve mcp clean lint fmt feedback workbench trajectory

help:
	@echo "SwarmLabs development targets"
	@echo ""
	@echo "  make install       pip install -e . (installs swarmlabs CLI)"
	@echo "  make install-dev   pip install -e .[dev,full]"
	@echo "  make demo          run the end-to-end demo"
	@echo "  make check         run all 5 check batteries"
	@echo "  make doctor        environment self-check"
	@echo "  make rebuild       rebuild ledger + reports + gates (CLI)"
	@echo "  make serve         start v3 API on :8787"
	@echo "  make mcp           start MCP server"
	@echo "  make workbench     start the local web Workbench on :8788"
	@echo "  make feedback      show feedback state / audit"
	@echo "  make trajectory    show recent run trajectories"
	@echo "  make clean         remove __pycache__ and .pyc files"

install:
	$(PIP) install -e .

install-dev:
	$(PIP) install -e ".[dev,full]"

demo:
	$(CLI) demo

check:
	$(CLI) check

test: check

doctor:
	$(CLI) doctor

scenarios:
	$(CLI) scenarios

gates:
	$(CLI) gates

anchors:
	$(CLI) anchors

serve:
	$(CLI) serve --host 127.0.0.1 --port 8787

mcp:
	$(CLI) mcp --selftest

rebuild:
	$(CLI) rebuild

workbench:
	$(CLI) workbench --host 127.0.0.1 --port 8788 --open

feedback:
	$(CLI) feedback audit

trajectory:
	$(CLI) trajectory list

clean:
	@find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	@find . -name "*.pyc" -delete 2>/dev/null || true
	@echo "cleaned"
