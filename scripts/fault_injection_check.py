#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/fault_injection_check.py — 语义级故障注入门禁（CI 硬门禁）。

为什么这个脚本存在
-------------------
2026-10-16 晚评估报告 §1 的实验 2 表明：把
``engine/verification_gate.py:468`` 的
``if r["verdict"] == GRADE_PASS and not r["ok"]:``
改成
``if False and r["verdict"] == GRADE_PASS and not r["ok"]:``
后，原有的 270 个 pytest 用例**全部通过**。语法没错、CI 绿灯，但
"No Anchored Counter, No PASS" 这条核心不变量已经被静默关掉——这正是
SwarmLabs 作为「可信验证层」的最致命的一类腐化。

这个脚本只证明一件事：**`tests/test_verification_gate_invariants.py` 确实能
抓住这条不变量**。做法是把 `assert_pass_has_anchor` 换成注入故障的版本，
然后断言新的测试会失败。测试若在此脚本下仍通过，就是"永远说 intact 的
校验器"——那种校验器等于没有。

失败情形（非零退出）：
  1. **BASELINE BROKEN**：注入前测试就跑不过——先修测试再谈故障注入。
  2. **INVARIANT UNPROTECTED**：注入后测试仍通过——测试没抓住该抓的不变量。
  3. **UNEXPECTED ERROR**：注入后抛了非 AssertionError 的异常——测试代码本身有问题。

用法
----
    python scripts/fault_injection_check.py
    python scripts/fault_injection_check.py --verbose

退出码
------
    0  注入故障后测试如期失败——不变量被保护
    1  任意断言失败或异常——不变量未受保护（阻断）

仅标准库。可在 GitHub Actions 直接运行。
"""
from __future__ import annotations

import argparse
import contextlib
import importlib
import importlib.util
import json
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Optional, Tuple


ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = ROOT / "tests"
INVAR_TEST_FILE = TESTS_DIR / "test_verification_gate_invariants.py"

# verify_baseline()/verify_felt_by_tests() 用 `import engine.verification_gate`
# （绝对导入），要求仓库根在 sys.path 上。与 scripts/check_vlab_gates.py、
# scripts/vlab.py 同一先例；缺了会 ModuleNotFoundError: No module named 'engine'
# （ci-cd fault-injection-gate 实测红）。子进程 wrapper 已自行 insert，
# 但主进程没有——这里补齐。
sys.path.insert(0, str(ROOT))

ENGINE_GATE_MODULE = "engine.verification_gate"
FAULTY_MARKER = "_FAULT_INJECTED_ASSERT_PASS_HAS_ANCHOR"  # attribute name tag


# ===========================================================================
# Loading the invariant-test module without needing tests/__init__.py
# ===========================================================================
def load_invar_test_module() -> object:
    """Load tests/test_verification_gate_invariants.py directly by file path.

    The tests/ directory doesn't have __init__.py, so
    `from tests.xxx import yyy` doesn't work from a scripts/ context.
    We use importlib.util.spec_from_file_location to load it by path.
    """
    spec = importlib.util.spec_from_file_location(
        "_swarmlabs_invar_tests", str(INVAR_TEST_FILE)
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot import {INVAR_TEST_FILE}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_swarmlabs_invar_tests"] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# ===========================================================================
# The exact fault from the 2026-10-16 evening assessment report
# ===========================================================================
def _faulty_assert_pass_has_anchor(key: str, ledger: Optional[dict] = None) -> None:
    """Semantically identical to the fault at verification_gate.py:468.

    Original:
        if r["verdict"] == GRADE_PASS and not r["ok"]:
            raise ActionBlocked(...)
    Faulty:
        if False and r["verdict"] == GRADE_PASS and not r["ok"]:
            raise ActionBlocked(...)
    """
    return None  # pragma: no cover — the fault never raises


# ===========================================================================
# Checks
# ===========================================================================
def verify_baseline() -> None:
    """Run the invariant test with the ORIGINAL code — must PASS."""
    vg = importlib.import_module(ENGINE_GATE_MODULE)
    from engine.verification_gate import GRADE_PASS, ActionBlocked, assert_pass_has_anchor

    # Sanity: the original function must NOT be the faulty marker.
    assert getattr(vg, FAULTY_MARKER, False) is False, (
        "baseline check aborted — module already marked as fault-injected"
    )

    # Load the test helper to build a real ledger dict (not mock).
    test_mod = load_invar_test_module()
    _mk_ledger = test_mod._mk_ledger  # type: ignore[attr-defined]

    # Baseline assertion: unanchored PASS must raise ActionBlocked.
    led = _mk_ledger({
        "fault_probe": {"verdict": GRADE_PASS, "gate": "PROCEED"},
    })
    raised = False
    try:
        assert_pass_has_anchor("fault_probe", ledger=led)
    except ActionBlocked:
        raised = True
    assert raised, (
        "BASELINE BROKEN: unanchored PASS no longer raises ActionBlocked — "
        "the invariant is already broken before any fault injection"
    )
    print("[ok]   baseline: unanchored PASS raises ActionBlocked (original code)")


def verify_felt_by_tests() -> None:
    """Install the fault via monkey-patch, then prove the test catches it."""
    vg = importlib.import_module(ENGINE_GATE_MODULE)

    # Baseline: the test class must be loadable and callable.
    test_mod = load_invar_test_module()

    # Install fault. We must patch BOTH:
    #   (a) the engine module attribute — because the "real" implementation
    #       lives there, and other call sites look it up by module; and
    #   (b) the test module's globals — because Python's `from X import Y`
    #       binds Y to the test module's namespace at import time. Patching
    #       only (a) would leave the test calling the original function via
    #       its own globals, which defeats the whole point.
    orig_engine_fn = vg.assert_pass_has_anchor
    orig_test_fn = test_mod.assert_pass_has_anchor
    vg.assert_pass_has_anchor = _faulty_assert_pass_has_anchor
    test_mod.assert_pass_has_anchor = _faulty_assert_pass_has_anchor  # type: ignore[attr-defined]
    setattr(vg, FAULTY_MARKER, True)
    print(f"[inject] monkey-patched assert_pass_has_anchor in both "
          f"{ENGINE_GATE_MODULE} and the test module's globals")

    # Run the invariant test class — must fail.
    cls = test_mod.TestCiteCheckInvariant  # type: ignore[attr-defined]
    instance = cls()

    # Note: pytest's own failure mechanism uses `pytest.fail.Exception`,
    # which is a *BaseException* (not Exception) subclass that does NOT
    # inherit from AssertionError. If we only catch AssertionError, we miss
    # the case where pytest.raises() itself raised Failed because the
    # invariant test failed in the way we wanted. So we catch BaseException
    # and inspect the class name.
    import pytest as _pytest_mod

    failed = False
    trace: Optional[str] = None
    try:
        instance.test_assert_pass_has_anchor_raises_on_unanchored_pass()
    except BaseException as exc:  # noqa: BLE001 — must catch pytest's Failed (BaseException)
        exc_type_name = type(exc).__name__
        exc_cls = type(exc)
        # Two "expected" failure types:
        #   - AssertionError (plain Python assertions)
        #   - pytest.fail.Exception (pytest's `fail()` — a BaseException sibling of Exception)
        is_assert = issubclass(exc_cls, AssertionError)
        is_pytest_fail = (
            exc_type_name == "Failed"
            or getattr(exc, "__module__", "").startswith("pytest")
            or getattr(exc, "__module__", "").startswith("_pytest")
        )
        if is_assert or is_pytest_fail:
            failed = True
            trace = f"{exc_type_name}: {str(exc)[:200]}"
        else:
            print(f"[FAIL] UNEXPECTED ERROR under fault injection:", file=sys.stderr)
            traceback.print_exc()
            sys.exit(1)
    finally:
        # Restore even on unexpected failure.
        vg.assert_pass_has_anchor = orig_engine_fn
        test_mod.assert_pass_has_anchor = orig_test_fn  # type: ignore[attr-defined]
        if hasattr(vg, FAULTY_MARKER):
            delattr(vg, FAULTY_MARKER)

    assert failed, (
        "INVARIANT UNPROTECTED: after monkey-patching assert_pass_has_anchor to a "
        "silent-return version, the invariant test still passed. This means the test "
        "does not actually verify the 'No Anchored Counter, No PASS' invariant. "
        "Add a stronger negative assertion to "
        f"{INVAR_TEST_FILE.relative_to(ROOT)}."
    )
    print("[ok]   invariant test failed as expected under fault injection")
    print(f"       ({(trace.splitlines()[0] if trace else 'AssertionError').strip()})")
    print(f"[ok]   fault uninstalled; original implementation restored")


def run_pytest_with_fault_in_subprocess() -> Tuple[int, float, str]:
    """Run pytest as a subprocess with the fault injected at import time.

    This is a belt-and-suspenders check: it confirms the fault is caught in a
    **fresh** pytest process (not just within the same interpreter as this script).
    Returns (returncode, elapsed_seconds, stdout_tail).
    """
    # Wrapper: patch the module BEFORE importing pytest, then invoke pytest.main.
    wrapper = f"""
import sys, os
sys.path.insert(0, {str(ROOT)!r})

import engine.verification_gate as vg


def _faulty_assert_pass_has_anchor(key, ledger=None):
    return None  # silent fault — mirrors the `if False and ...` mutation


vg.assert_pass_has_anchor = _faulty_assert_pass_has_anchor

import pytest
sys.exit(pytest.main([
    {str(INVAR_TEST_FILE.relative_to(ROOT))!r},
    "-q", "--no-header", "--tb=short", "-x",
]))
"""
    t0 = time.time()
    proc = subprocess.run(
        [sys.executable, "-c", wrapper],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(ROOT),
    )
    elapsed = time.time() - t0
    tail = (proc.stdout or "")[-2000:]
    return proc.returncode, elapsed, tail


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--json", action="store_true",
                    help="Emit a machine-readable summary at the end")
    args = ap.parse_args()

    t0 = time.time()
    print("fault_injection_check: start")
    print(f"  root={ROOT}")
    print(f"  invariant_test={INVAR_TEST_FILE.relative_to(ROOT)}")

    result = {
        "ok": False,
        "elapsed_s": None,
        "checks": [],
        "error": None,
    }

    try:
        # 1) Baseline must pass.
        verify_baseline()
        result["checks"].append({"name": "baseline", "ok": True})

        # 2) Monkey-patch fault injection — test must fail.
        verify_felt_by_tests()
        result["checks"].append({"name": "monkey_patch_detects_fault", "ok": True})

        # 3) Subprocess pytest with fault — must return non-zero.
        print("[sub] running pytest in fresh subprocess with fault installed...")
        code, elapsed, tail = run_pytest_with_fault_in_subprocess()
        if args.verbose or True:
            print(f"[sub] subprocess pytest returned {code} in {elapsed:.2f}s")
        if code == 0:
            print("[FAIL] subprocess pytest returned 0 with fault installed — "
                  "the invariant test did NOT catch the fault in a clean process.",
                  file=sys.stderr)
            print("---- pytest tail ----")
            print(tail)
            result["error"] = "subprocess_pytest_returned_zero"
            raise AssertionError(result["error"])
        result["checks"].append({
            "name": "subprocess_pytest_detects_fault",
            "ok": True,
            "pytest_ret": code,
            "elapsed_s": round(elapsed, 2),
        })
        print(f"[ok]   subprocess pytest returned {code} (expected non-zero) — "
              f"the invariant test catches the fault in a clean process too")

        result["ok"] = True
    except AssertionError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        result["error"] = str(exc)
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        traceback.print_exc()
        result["error"] = f"{type(exc).__name__}: {exc}"

    total = time.time() - t0
    result["elapsed_s"] = round(total, 2)

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))

    if result["ok"]:
        print(f"[done] fault injection check PASSED in {total:.2f}s")
        return 0
    else:
        print(f"[done] fault injection check FAILED in {total:.2f}s: {result['error']}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
