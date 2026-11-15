#!/usr/bin/env python
"""PINN 外部参照交叉校验。

目标：用已知解析解的 ODE 验证 engine/pinn.py 的 PINN1D 实现。
PINN1D 求解 u'' = f(x) 的边值问题，f 是右端项函数。

判据：PINN 预测与解析解的 max abs error < 阈值 → PASS。

靶子：
  1. u'' = -sin(x), u(0)=0, u(1)=1 → u(x) = sin(x) + (1-sin(1))*x
  2. u'' = 0, u(0)=0, u(1)=1 → u(x) = x
  3. u'' = -2x, u(0)=0, u(1)=1 → u(x) = x - x^3 + (2/3)x^2  ... 
     实际上 u'' = -2x → u = x - x^3/3 + C1x + C2
     用 u(0)=0 → C2=0; u(1)=1 → 1 - 1/3 + C1 = 1 → C1 = 1/3
     所以 u(x) = x + x/3 - x^3/3 = 4x/3 - x^3/3

用法：
    python scripts/validate_pinn_external.py
"""
from __future__ import annotations

import json
import os
import sys
import time

import numpy as np

os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

DATA = os.path.join(ROOT, "data")

from engine.pinn import PINN1D


def pin_pde_problem():
    """靶子 1: u'' = -sin(x), u(0)=0, u(1)=1.
    PINN1D 的 fit 签名: fit(f, bc=(u0, u1)), 其中 f(x) 是 u'' = f(x) 的右端项
    所以 f(x) = -sin(x)
    解析解: u(x) = sin(x) + (1 - sin(1)) * x
    """
    x_grid = np.linspace(0, 1, 100)
    exact = np.sin(x_grid) + (1 - np.sin(1)) * x_grid

    pinn = PINN1D(x_grid=x_grid, hidden=20)

    # f(x) = -sin(x)  (u'' = -sin(x))
    pinn.fit(f=lambda x: -np.sin(x), bc=(0.0, 1.0), seed=42)

    pred = pinn.predict(x_grid)
    max_err = float(np.max(np.abs(pred - exact)))
    report = pinn.report()

    return {
        "problem": "u'' = -sin(x), u(0)=0, u(1)=1",
        "exact_solution": "sin(x) + (1-sin(1))*x",
        "max_abs_error": round(max_err, 6),
        "pinn_report": report,
        "pass": max_err < 0.05,
    }


def pin_thermal_steady():
    """靶子 2: u'' = 0, u(0)=0, u(1)=1.
    f(x) = 0 (u'' = 0)
    解析解: u(x) = x
    """
    x_grid = np.linspace(0, 1, 100)
    exact = x_grid.copy()

    pinn = PINN1D(x_grid=x_grid, hidden=20)
    pinn.fit(f=lambda x: np.zeros_like(x), bc=(0.0, 1.0), seed=42)

    pred = pinn.predict(x_grid)
    max_err = float(np.max(np.abs(pred - exact)))
    report = pinn.report()

    return {
        "problem": "u'' = 0, u(0)=0, u(1)=1",
        "exact_solution": "x",
        "max_abs_error": round(max_err, 6),
        "pinn_report": report,
        "pass": max_err < 0.01,
    }


def pin_quadratic():
    """靶子 3: u'' = -2x, u(0)=0, u(1)=1.
    f(x) = -2x (u'' = -2x)
    解析解: u(x) = 4x/3 - x^3/3
    验证: u'' = -2x ✓; u(0) = 0 ✓; u(1) = 4/3 - 1/3 = 1 ✓
    """
    x_grid = np.linspace(0, 1, 100)
    exact = 4 * x_grid / 3 - x_grid ** 3 / 3

    pinn = PINN1D(x_grid=x_grid, hidden=20)
    pinn.fit(f=lambda x: -2 * x, bc=(0.0, 1.0), seed=42)

    pred = pinn.predict(x_grid)
    max_err = float(np.max(np.abs(pred - exact)))
    report = pinn.report()

    return {
        "problem": "u'' = -2x, u(0)=0, u(1)=1",
        "exact_solution": "4x/3 - x^3/3",
        "max_abs_error": round(max_err, 6),
        "pinn_report": report,
        "pass": max_err < 0.02,
    }


def main():
    print("=" * 70)
    print("PINN 外部参照交叉校验")
    print("=" * 70)

    results = []
    t0 = time.time()

    targets = [
        ("PDE: u'' = -sin(x)", pin_pde_problem),
        ("Thermal: u'' = 0", pin_thermal_steady),
        ("Quadratic: u'' = -2x", pin_quadratic),
    ]

    for i, (name, func) in enumerate(targets, 1):
        print(f"\n[{i}/{len(targets)}] {name}")
        t_start = time.time()
        try:
            result = func()
            result["elapsed_sec"] = round(time.time() - t_start, 2)
            results.append(result)
            print(f"  max_abs_error = {result['max_abs_error']:.6f}")
            print(f"  pde_residual  = {result['pinn_report']['max_pde_residual']:.6f}")
            print(f"  PASS: {result['pass']}")
            print(f"  耗时: {result['elapsed_sec']}s")
        except Exception as e:
            import traceback
            print(f"  ERROR: {e}")
            traceback.print_exc()
            results.append({
                "problem": name,
                "verdict": "ERROR",
                "error": str(e),
            })

    total_elapsed = time.time() - t0
    n_pass = sum(1 for r in results if r.get("pass"))
    n_fail = sum(1 for r in results if r.get("pass") is False)
    n_error = sum(1 for r in results if r.get("verdict") == "ERROR")

    print(f"\n{'='*70}")
    print(f"汇总: PASS={n_pass}, FAIL={n_fail}, ERROR={n_error}")
    print(f"总耗时: {total_elapsed:.2f}s")

    report = {
        "version": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "module": "engine/pinn.py (PINN1D)",
        "criteria": {
            "PDE: u'' = -sin(x)": "max_abs_error < 0.05",
            "Thermal: u'' = 0": "max_abs_error < 0.01",
            "Quadratic: u'' = -2x": "max_abs_error < 0.02",
        },
        "summary": {
            "pass": n_pass,
            "fail": n_fail,
            "error": n_error,
            "total_elapsed_sec": round(total_elapsed, 2),
        },
        "results": results,
    }

    path = os.path.join(DATA, "pinn_external_validation.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n报告已写入: {path}")


if __name__ == "__main__":
    main()
