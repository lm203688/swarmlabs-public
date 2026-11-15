"""兼容垫片：历史代码 `from virtual_experiment.parity_framework import ...` 仍可用。

2026-08-15 整合：内核唯一真源已迁至 `engine/surrogate.py`。本文件不再持有
实现，仅转发，避免分裂出两份内核。新增代码请直接 `from engine import ...`。
"""

import os
import sys

_THIS = os.path.dirname(os.path.abspath(__file__))
# 把项目根（engine/ 的父目录）加入 path，使 `from engine.surrogate import ...` 可被解析。
_PROJECT_ROOT = os.path.abspath(os.path.join(_THIS, "..", ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from engine.surrogate import (  # noqa: E402,F401
    ExperimentSpace,
    GPSurrogate,
    NeuralOperatorSurrogate,
    SyntheticOracle,
    RemoteLabOracle,
    Calibrator,
    ActiveLearner,
    run_parity_loop,
    mirror_run,
    SurrogateEngine,
)

__all__ = [
    "ExperimentSpace",
    "GPSurrogate",
    "NeuralOperatorSurrogate",
    "SyntheticOracle",
    "RemoteLabOracle",
    "Calibrator",
    "ActiveLearner",
    "run_parity_loop",
    "mirror_run",
    "SurrogateEngine",
]
