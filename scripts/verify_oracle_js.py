#!/usr/bin/env python3
"""Cross-validate JS Clenshaw vs Python Chebyshev oracle.
Generates test points + Python ground truth, then Node evaluates with JS Clenshaw.
"""
import json
import sys
import numpy as np
from pathlib import Path
from numpy.polynomial import chebyshev as C

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
ORACLE_DIR = ROOT / "frontend_fixed" / "reports" / "oracle"
OUT = ROOT / "scripts" / "oracle_cross_test_points.json"

manifest = json.loads((ORACLE_DIR / "manifest.json").read_text())
scenarios = manifest.get("scenarios", {})
print(f"Manifest lists {len(scenarios)} scenarios")

test_points = []

for key, entry in scenarios.items():
    if len(test_points) >= 200:
        break
    jf = ORACLE_DIR / f"{key}.json"
    if not jf.exists():
        continue

    data = json.loads(jf.read_text())
    inner = data.get("data", {})
    dim = inner.get("dim", 1)
    bounds = inner.get("bounds", [])
    coeffs_arr = np.array(inner.get("coefficients", []), dtype=float)
    audit = inner.get("audit", {})

    if dim > 3 or len(coeffs_arr) == 0:
        continue

    rng = np.random.default_rng(42 + len(test_points))
    for _ in range(10):
        x = [float(rng.uniform(bounds[d][0], bounds[d][1])) for d in range(dim)]

        if dim == 1:
            t = (2 * x[0] - bounds[0][0] - bounds[0][1]) / (bounds[0][1] - bounds[0][0])
            y_true = float(C.chebval(t, coeffs_arr.flatten()))
        elif dim == 2:
            t0 = (2 * x[0] - bounds[0][0] - bounds[0][1]) / (bounds[0][1] - bounds[0][0])
            t1 = (2 * x[1] - bounds[1][0] - bounds[1][1]) / (bounds[1][1] - bounds[1][0])
            y_true = float(C.chebval2d(t0, t1, coeffs_arr))
        elif dim == 3:
            t0 = (2 * x[0] - bounds[0][0] - bounds[0][1]) / (bounds[0][1] - bounds[0][0])
            t1 = (2 * x[1] - bounds[1][0] - bounds[1][1]) / (bounds[1][1] - bounds[1][0])
            t2 = (2 * x[2] - bounds[2][0] - bounds[2][1]) / (bounds[2][1] - bounds[2][0])
            y_true = float(C.chebval3d(t0, t1, t2, coeffs_arr))
        else:
            continue

        test_points.append({
            "scenario_key": key,
            "x": x,
            "y_true": round(y_true, 10),
            "dim": dim,
            "bounds": bounds
        })

print(f"Generated {len(test_points)} test points from {len(set(p['scenario_key'] for p in test_points))} scenarios")

OUT.write_text(json.dumps({
    "points": test_points,
    "generated_at": __import__("datetime").datetime.now().isoformat(),
    "n_scenarios": len(scenarios),
}, indent=2))

print(f"Written to {OUT}")
if test_points:
    print(f"Sample: {test_points[0]}")