#!/usr/bin/env bash
# SwarmLabs dev environment bootstrapper
# Usage: bash scripts/dev_up.sh
# Installs the package, runs doctor, and launches the demo.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

echo "==> SwarmLabs dev environment setup"
echo "    repo root : $REPO_ROOT"
echo "    python    : $(python --version)"
echo ""

# 1. Install in editable mode (skippable with SKIP_INSTALL=1)
if [ "${SKIP_INSTALL:-0}" != "1" ]; then
    echo "==> pip install -e .[dev]"
    python -m pip install -e ".[dev]" 2>&1 | tail -5
else
    echo "==> skipping install (SKIP_INSTALL=1)"
fi

echo ""
echo "==> doctor"
python -m swarmlabs doctor

echo ""
echo "==> check battery"
python -m swarmlabs check || true  # non-zero is fine here; we surface the failures

echo ""
echo "==> end-to-end demo"
python -m swarmlabs demo

echo ""
echo "==> done. Try:"
echo "    swarmlabs scenarios          # list 82 scenarios"
echo "    swarmlabs gates              # list 11 registered gates"
echo "    swarmlabs anchors            # list 19 wet-lab anchors"
echo "    swarmlabs serve              # start the v3 API on :8787"
