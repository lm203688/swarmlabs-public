"""SwarmLabs: AI4S verification layer.

Public entrypoints:
- `swarmlabs.cli`   : unified CLI (console_scripts entry `swarmlabs`)
- `swarmlabs.demo`  : one-shot end-to-end demo (used by `swarmlabs demo`)
- `swarmlabs.runner`: low-level runner used by CLI and MCP integrations

Quickstart:

    pip install -e .
    swarmlabs doctor
    swarmlabs demo
    swarmlabs scenarios
    swarmlabs gates
    swarmlabs anchors
    swarmlabs check
    swarmlabs serve --port 8787
"""
from __future__ import annotations

__version__ = "0.1.0"
__all__ = ["__version__"]
