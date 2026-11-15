"""Orphan module wiring gate — invokes selftests of previously-orphan modules.

Purpose (see 板块 05/07/03 assessment reports, 2026-10-14; N8 wiring, 2026-10-16):
    The project had several high-value modules that were shipped but never
    referenced by any gate/entry point. This script makes them part of the CI
    contract so they cannot silently rot.

Modules wired:
    engine/spectral_guard.py              (板块 05, 376 行) — spectral signature
    engine/digital_twin_lab.py            (板块 07, 561 行) — closed-loop twin
    engine/physics_constrained_conformal.py (板块 03, 452 行) — physics prior UQ
    engine/fourier_symbol_diag.py         (板块 05 P1, 492 行) — 算子级 Fourier 探针
    engine/conformal_kernel.py            (板块 03 P1, 819 行) — 10 种 conformal 统一接口

Each module's `if __name__ == "__main__": selftest()` contract is exercised
by importing and calling `_selftest()` directly.  A FAIL here means the module
is broken, not "unreferenced".

Usage:
    python scripts/check_orphan_selftest.py
"""
from __future__ import annotations

import importlib
import os
import sys
import time
import traceback
from pathlib import Path
from typing import List, Tuple

# 仓库根 = scripts/ 的父目录。脚本单独执行时 sys.path[0] 是 scripts/，
# 不会自动带上仓库根；必须在 import engine.* 之前把根目录显式加进 sys.path，
# 否则 5 个模块全部 IMPORT FAILED: No module named 'engine'。
# （2026-10-17 查缺补漏 L-06：本脚本此前 CI 引用=0，从未被 CI 跑过，
#  这条路径 bug 一直潜伏着。）
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

ROOT = str(_ROOT)


def _normalize_selftest_return(ret: object) -> bool:
    """Normalize heterogeneous _selftest() return conventions.

    Two conventions coexist in this codebase:
      1. bool-style: return True / False  (spectral_guard, digital_twin_lab,
         physics_constrained_conformal).
      2. exit-code style: return 0 on success, non-zero on failure
         (conformal_kernel, fourier_symbol_diag — mirror the
         `if __name__ == "__main__": sys.exit(_selftest())` idiom so the
         CLI path and the CI path agree).

    This adapter keeps both conventions valid and prevents a false-negative
    when a module returns `0` (which `bool(0)` would wrongly treat as FAIL).
    """
    if isinstance(ret, bool):
        return ret
    if isinstance(ret, (int, float)) and not isinstance(ret, bool):
        return ret == 0
    return bool(ret)


def _run_selftest(module_path: str) -> Tuple[str, bool, str, float]:
    """Import module_path and call _selftest(). Return (name, ok, msg, seconds)."""
    t0 = time.time()
    try:
        mod = importlib.import_module(module_path)
    except Exception as e:
        return (module_path, False, f"IMPORT FAILED: {e}", time.time() - t0)

    if not hasattr(mod, "_selftest"):
        return (module_path, False, "NO _selftest FUNCTION", time.time() - t0)

    try:
        ret = mod._selftest()
        ok = _normalize_selftest_return(ret)
        msg = "OK" if ok else f"SELFTEST RETURNED {ret!r} (treated as FAIL)"
        return (module_path, ok, msg, time.time() - t0)
    except Exception as e:
        tb = traceback.format_exc().splitlines()[-3:]
        return (module_path, False, f"EXCEPTION: {e} | {tb}", time.time() - t0)


# ---------------------------------------------------------------------------
# Modules to wire. Add new orphan modules here as they are created; CI will
# auto-guard against silent rot.
# ---------------------------------------------------------------------------
ORPHAN_MODULES: List[str] = [
    "engine.spectral_guard",
    "engine.digital_twin_lab",
    "engine.physics_constrained_conformal",
    "engine.fourier_symbol_diag",
    "engine.conformal_kernel",
]


def main() -> int:
    print(f"[orphan_selftest] wiring {len(ORPHAN_MODULES)} modules via CI")
    print("-" * 72)
    failures: List[str] = []
    for path in ORPHAN_MODULES:
        name, ok, msg, dur = _run_selftest(path)
        icon = "✓" if ok else "✗"
        print(f"  {icon} {name:46s} {('PASS' if ok else 'FAIL'):5s} ({dur:.2f}s)")
        if not ok:
            failures.append(f"{name}: {msg}")

    print("-" * 72)
    if failures:
        print(f"[orphan_selftest] FAIL: {len(failures)}/{len(ORPHAN_MODULES)} failed")
        for f in failures:
            print(f"    - {f}")
        return 1

    print(f"[orphan_selftest] PASS: all {len(ORPHAN_MODULES)} orphan modules wired & green")
    return 0


if __name__ == "__main__":
    sys.exit(main())
