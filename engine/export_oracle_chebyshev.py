#!/usr/bin/env python3
"""
engine/export_oracle_chebyshev.py — Offline Chebyshev fit of oracle functions.

Design (task #334):
  CF Pages Workers can't run Python, but oracle functions are low-dim.
  We fit each oracle offline on a Chebyshev grid → export coefficient JSON +
  audit error bound.  JS side implements Clenshaw evaluation; Worker accepts
  arbitrary predictions, evaluates the JS oracle, and computes R²/coverage/κ
  using the same gate_ledger policy constants.

  Scope: dim 1-3 only (75/95 scenarios).  dim≥4 skipped (too expensive to fit).

  Output: frontend_fixed/reports/oracle/<key>.json  (one file per scenario)
  Also writes frontend_fixed/reports/oracle/manifest.json with digests.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import warnings
from pathlib import Path
from typing import Callable, Dict, List, Tuple

import numpy as np
from numpy.polynomial import chebyshev as C

warnings.filterwarnings("ignore")

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
#  Chebyshev utilities
# ---------------------------------------------------------------------------

def cheb_nodes(n: int, lo: float, hi: float) -> np.ndarray:
    k = np.arange(n)
    c = np.cos(np.pi * k / max(n - 1, 1))
    return 0.5 * (hi + lo) - 0.5 * (hi - lo) * c


def rescale(x: np.ndarray, lo: float, hi: float) -> np.ndarray:
    mid, span = 0.5 * (hi + lo), 0.5 * (hi - lo)
    return (x - mid) / span if span > 1e-15 else np.zeros_like(x)


def rescale_back(t: np.ndarray, lo: float, hi: float) -> np.ndarray:
    return 0.5 * (hi + lo) + 0.5 * (hi - lo) * t


# ---------------------------------------------------------------------------
#  Fitters
# ---------------------------------------------------------------------------

def fit_1d(f: Callable, lo: float, hi: float, degree: int = 24) -> Tuple[np.ndarray, dict]:
    n = max(2 * degree + 2, 48)
    nodes = cheb_nodes(n, lo, hi)
    scaled = rescale(nodes, lo, hi)
    y = np.array([f(np.array([x])) for x in nodes])
    c = C.chebfit(scaled, y, degree)
    # Audit
    t_test = np.linspace(-1, 1, 200)
    x_test = rescale_back(t_test, lo, hi)
    y_true = np.array([f(np.array([x])) for x in x_test])
    y_pred = C.chebval(t_test, c)
    max_err = float(np.max(np.abs(y_true - y_pred)))
    return c, {"max_abs_err": max_err, "max_rel_err": max_err / (float(np.std(y_true)) or 1.0),
               "degree": degree, "n_samples": n}


def _tensor_chebfit(tensors, y, degrees):
    """General tensor-product Chebyshev fit via least squares.
    
    tensors: list of 1D arrays (scaled to [-1,1]) for each axis.
    y: array with shape matching len(tensors) per axis.
    degrees: list of degrees per axis.
    Returns: coefficient tensor with shape (degrees[0]+1, degrees[1]+1, ...).
    """
    dim = len(tensors)
    V = [C.chebvander(tensors[d], degrees[d]) for d in range(dim)]  # each (n_d, deg_d+1)
    
    # Build tensor product basis matrix:
    # For 2D: basis[i*n1+j, p*(d1+1)+q] = V[0][i,p] * V[1][j,q]
    # For 3D: basis[i*n1*n2+j*n2+k, p*(d1+1)*(d2+1)+q*(d2+1)+r] = V[0][i,p] * V[1][j,q] * V[2][k,r]
    # Use einsum to build the full tensor, then reshape.
    
    if dim == 1:
        basis = V[0]  # (n0, d0+1)
    elif dim == 2:
        # B[i,j,p,q] = V[0][i,p] * V[1][j,q]
        B = np.einsum('ip,jq->ijpq', V[0], V[1])
        n_total = B.shape[0] * B.shape[1]
        n_coeff = B.shape[2] * B.shape[3]
        basis = B.reshape(n_total, n_coeff)
    elif dim == 3:
        # B[i,j,k,p,q,r] = V[0][i,p] * V[1][j,q] * V[2][k,r]
        B = np.einsum('ip,jq,kr->ijkpqr', V[0], V[1], V[2])
        n_total = B.shape[0] * B.shape[1] * B.shape[2]
        n_coeff = B.shape[3] * B.shape[4] * B.shape[5]
        basis = B.reshape(n_total, n_coeff)
    else:
        # Fallback for higher dims: iterative kron
        basis = V[0]
        for d in range(1, dim):
            basis = np.einsum('ij,kj->ik', basis, V[d])
    
    # Flatten y to 1D
    y_flat = y.flatten()
    n_total = y_flat.shape[0]
    
    # Solve least squares
    coeffs_flat, *_ = np.linalg.lstsq(basis, y_flat, rcond=None)
    
    # Reshape to tensor
    coeff_shape = tuple(deg + 1 for deg in degrees)
    return coeffs_flat.reshape(coeff_shape)


def _tensor_chebval(tensors, coeffs):
    """Evaluate tensor-product Chebyshev series.
    
    tensors: list of 1D arrays (scaled to [-1,1]).
    coeffs: coefficient tensor.
    Returns: array with shape matching len(tensors) per axis.
    """
    dim = len(tensors)
    V = [C.chebvander(tensors[d], coeffs.shape[d] - 1) for d in range(dim)]
    
    # Build tensor product basis matrix
    if dim == 1:
        basis = V[0]
    elif dim == 2:
        B = np.einsum('ip,jq->ijpq', V[0], V[1])
        n_total = B.shape[0] * B.shape[1]
        n_coeff = B.shape[2] * B.shape[3]
        basis = B.reshape(n_total, n_coeff)
    elif dim == 3:
        B = np.einsum('ip,jq,kr->ijkpqr', V[0], V[1], V[2])
        n_total = B.shape[0] * B.shape[1] * B.shape[2]
        n_coeff = B.shape[3] * B.shape[4] * B.shape[5]
        basis = B.reshape(n_total, n_coeff)
    else:
        basis = V[0]
        for d in range(1, dim):
            basis = np.einsum('ij,kj->ik', basis, V[d])
    
    # Evaluate
    y_flat = basis @ coeffs.flatten()
    
    # Reshape to output
    out_shape = tuple(len(t) for t in tensors)
    return y_flat.reshape(out_shape)


def fit_2d(f: Callable, b0, b1, deg0=16, deg1=16) -> Tuple[np.ndarray, dict]:
    n0, n1 = max(2*deg0+2, 24), max(2*deg1+2, 24)
    x0 = cheb_nodes(n0, b0[0], b0[1])
    x1 = cheb_nodes(n1, b1[0], b1[1])
    t0, t1 = rescale(x0, b0[0], b0[1]), rescale(x1, b1[0], b1[1])
    y = np.empty((n0, n1))
    for i in range(n0):
        for j in range(n1):
            y[i, j] = f(np.array([rescale_back(t0[i], b0[0], b0[1]),
                                   rescale_back(t1[j], b1[0], b1[1])]))
    c = _tensor_chebfit([t0, t1], y, [deg0, deg1])
    # Audit
    tx0, tx1 = np.linspace(-1, 1, 12), np.linspace(-1, 1, 12)
    y_true = np.empty((12, 12))
    for i in range(12):
        for j in range(12):
            y_true[i, j] = f(np.array([rescale_back(tx0[i], b0[0], b0[1]),
                                        rescale_back(tx1[j], b1[0], b1[1])]))
    y_pred = _tensor_chebval([tx0, tx1], c)
    max_err = float(np.max(np.abs(y_true - y_pred)))
    return c, {"max_abs_err": max_err, "max_rel_err": max_err / (float(np.std(y_true)) or 1.0),
               "degrees": [deg0, deg1], "n_coefficients": int((deg0+1)*(deg1+1)), "n_samples": n0*n1}


def fit_3d(f: Callable, bounds, deg0=12, deg1=12, deg2=12) -> Tuple[np.ndarray, dict]:
    ns = [max(2*d+2, 20) for d in (deg0, deg1, deg2)]
    grids = [cheb_nodes(ns[d], bounds[d][0], bounds[d][1]) for d in range(3)]
    tg = [rescale(grids[d], bounds[d][0], bounds[d][1]) for d in range(3)]
    y = np.empty((ns[0], ns[1], ns[2]))
    for i in range(ns[0]):
        for j in range(ns[1]):
            for k in range(ns[2]):
                y[i, j, k] = f(np.array([rescale_back(tg[d][idx], bounds[d][0], bounds[d][1])
                                          for d, idx in enumerate((i, j, k))]))
    c = _tensor_chebfit(tg, y, [deg0, deg1, deg2])
    # Audit
    tg_t = [np.linspace(-1, 1, 10) for _ in range(3)]
    y_true = np.empty((10, 10, 10))
    for i in range(10):
        for j in range(10):
            for k in range(10):
                y_true[i, j, k] = f(np.array([rescale_back(tg_t[d][idx], bounds[d][0], bounds[d][1])
                                              for d, idx in enumerate((i, j, k))]))
    y_pred = _tensor_chebval(tg_t, c)
    max_err = float(np.max(np.abs(y_true - y_pred)))
    return c, {"max_abs_err": max_err, "max_rel_err": max_err / (float(np.std(y_true)) or 1.0),
               "degrees": [deg0, deg1, deg2], "n_coefficients": int((deg0+1)*(deg1+1)*(deg2+1)), "n_samples": ns[0]*ns[1]*ns[2]}


# ---------------------------------------------------------------------------
#  Main export
# ---------------------------------------------------------------------------

def export_oracles(output_dir: Path) -> dict:
    from engine.scenario_registry import all_scenarios
    
    output_dir.mkdir(parents=True, exist_ok=True)
    scenarios = all_scenarios()
    
    manifest = {
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_scenarios": len(scenarios),
        "exported": 0,
        "skipped_high_dim": 0,
        "failed": 0,
        "engine_git_commit": os.environ.get("GIT_COMMIT", "unknown"),
    }
    
    results = {}
    t_start = time.time()
    
    for idx, (key, sc) in enumerate(sorted(scenarios.items())):
        f = sc.get("f")
        bounds = sc.get("bounds")
        dim = sc.get("dim", 1)
        
        if not callable(f):
            print(f"  SKIP {key}: f is not callable")
            manifest["failed"] += 1
            results[key] = {"error": "f not callable"}
            continue
        if not bounds or len(bounds) != dim:
            print(f"  SKIP {key}: bad bounds")
            manifest["failed"] += 1
            results[key] = {"error": "bad bounds"}
            continue
        
        if dim > 3:
            manifest["skipped_high_dim"] += 1
            results[key] = {"skipped": True, "reason": f"dim={dim} > 3"}
            continue
        
        bounds_tuples = [(float(b[0]), float(b[1])) for b in bounds]
        
        try:
            if dim == 1:
                coeffs, audit = fit_1d(f, bounds_tuples[0][0], bounds_tuples[0][1], degree=24)
                coeff_data = {
                    "format": "chebyshev_1d",
                    "degrees": [24],
                    "coefficients": coeffs.tolist(),
                    "bounds": bounds_tuples,
                    "dim": dim,
                    "audit": audit,
                }
                method_name = "chebyshev_1d"
                
            elif dim == 2:
                coeffs, audit = fit_2d(f, bounds_tuples[0], bounds_tuples[1], deg0=16, deg1=16)
                coeff_data = {
                    "format": "chebyshev_2d",
                    "degrees": [16, 16],
                    "coefficients": coeffs.tolist(),
                    "bounds": bounds_tuples,
                    "dim": dim,
                    "audit": audit,
                }
                method_name = "chebyshev_2d"
                
            else:  # dim == 3
                coeffs, audit = fit_3d(f, bounds_tuples, deg0=12, deg1=12, deg2=12)
                coeff_data = {
                    "format": "chebyshev_3d",
                    "degrees": [12, 12, 12],
                    "coefficients": coeffs.tolist(),
                    "bounds": bounds_tuples,
                    "dim": dim,
                    "audit": audit,
                }
                method_name = "chebyshev_3d"
            
            payload = {
                "scenario_key": key,
                "method": method_name,
                "data": coeff_data,
            }
            
            out_path = output_dir / f"{key}.json"
            out_path.write_text(json.dumps(payload, indent=2))
            
            file_bytes = out_path.read_bytes()
            sha = hashlib.sha256(file_bytes).hexdigest()[:12]
            n_coeff = audit.get("n_coefficients", int(np.prod([d+1 for d in coeff_data["degrees"]])))
            results[key] = {
                "method": method_name,
                "dim": dim,
                "sha": sha,
                "size_bytes": len(file_bytes),
                "max_abs_err": audit.get("max_abs_err"),
                "max_rel_err": audit.get("max_rel_err"),
                "n_coefficients": n_coeff,
            }
            manifest["exported"] += 1
            
            elapsed = time.time() - t_start
            print(f"  [{elapsed:5.1f}s] ({idx+1}/{len(scenarios)}) OK  {key:35s}  "
                  f"dim={dim}  coeffs={n_coeff:>5}  max_err={audit.get('max_abs_err', 0):.2e}",
                  flush=True)
            
        except Exception as e:
            print(f"  FAIL {key}: {type(e).__name__}: {e}", flush=True)
            import traceback
            traceback.print_exc()
            results[key] = {"error": str(e)}
            manifest["failed"] += 1
    
    manifest["scenarios"] = results
    manifest_bytes = json.dumps(results).encode()
    manifest["total_digest"] = hashlib.sha256(manifest_bytes).hexdigest()
    
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    
    total_time = time.time() - t_start
    print(f"\n  Done in {total_time:.1f}s")
    print(f"  Manifest: {manifest_path}")
    print(f"  Exported: {manifest['exported']}, Skipped (dim>3): {manifest['skipped_high_dim']}, Failed: {manifest['failed']}")
    
    return manifest


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else PROJECT_ROOT / "frontend_fixed" / "reports" / "oracle"
    export_oracles(out)
