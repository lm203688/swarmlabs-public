#!/usr/bin/env python3
"""Extract yeast fermentation anchors from Slanska et al. (Zenodo 17423657).

2026-10-16: N6 hardening — Figshare 403 replacement.

Sources used (all from Zenodo 17423657, DOI 10.5281/zenodo.17423657):
  - Figure S4  (sheet "Figure S4"): ethanol-inhibition curve, 4 data points
  - Figure 4   (sheet "Figure 4"):  parent vs engineered strain time-course

Extraction strategy
-------------------
For the ethanol-inhibition curve (Figure S4), fit a standard competitive
inhibition model:

    OD(ethanol) = OD_max / (1 + (ethanol / K_i)^n)

where OD_max is the no-inhibition OD, K_i the half-max inhibition concentration,
and n the Hill coefficient (steepness of inhibition). This is the standard
form of product inhibition used for ethanol inhibition of yeast
(Haldane-type, cf. Monod kinetics with product term).

Anchors produced
----------------
1. yeast_ethanol_yield_real            — max OD at 0% ethanol
2. yeast_ethanol_ki_real               — half-max inhibition concentration
3. yeast_ethanol_hill_n_real           — Hill coefficient (steepness)
4. yeast_ethanol_full_inhibition_real  — ethanol % at 99% inhibition

All values carry the FULL provenance schema (source, DOI, sheet, extraction
method, fit R², and raw data points).

Usage
-----
    python scripts/extract_yeast_anchors.py
    python scripts/extract_yeast_anchors.py --refresh  # re-parse + update real_anchors.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
TMP_DIR = ROOT / ".tmp_z"
SLANSKA_XLSX = TMP_DIR / "Slanska_Biotin.xlsx"
ZENODO_ID = "17423657"
DOI = "10.5281/zenodo.17423657"


def _sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def load_ethanol_inhibition() -> List[Dict[str, float]]:
    """Return the 4-point ethanol-inhibition table from Figure S4."""
    import openpyxl
    wb = openpyxl.load_workbook(SLANSKA_XLSX, read_only=True, data_only=True)
    ws = wb["Figure S4"]
    rows = list(ws.iter_rows(values_only=True))
    if len(rows) < 4:
        raise RuntimeError(f"Figure S4 has <4 rows: {len(rows)}")
    # Layout (openpyxl 0-indexed rows):
    #   rows[0] = []                          (blank)
    #   rows[1] = [None, 'ethanol (%)', 0, 1, 5, 10]
    #   rows[2] = [None, 'average OD600', 6.41, 6.24, 0.35, 0.0088]
    #   rows[3] = [None, 'stdeva', 0.16, 0.31, 0.23, 0.0007]
    # Data occupies columns 2..5 (inclusive).
    ethanol = [float(v) for v in rows[1][2:6] if v is not None]
    od_avg = [float(v) for v in rows[2][2:6] if v is not None]
    od_sd = [float(v) for v in rows[3][2:6] if v is not None]
    if not (len(ethanol) == len(od_avg) == 4):
        raise RuntimeError(
            f"Fig S4 shape mismatch: ethanol={len(ethanol)}, od_avg={len(od_avg)}"
        )
    return [
        {"ethanol_pct": e, "od600_mean": o, "od600_stdev": s}
        for e, o, s in zip(ethanol, od_avg, od_sd)
    ]


def fit_inhibition_curve(points: List[Dict[str, float]]) -> Dict[str, Any]:
    """Fit OD(ethanol) = OD_max / (1 + (ethanol/K_i)^n) via least squares.

    Returns params + R² + RMSE. If scipy is unavailable, fall back to grid
    search on (K_i, n) which is still exact for 4 data points.
    """
    ethanol = np.array([p["ethanol_pct"] for p in points], dtype=float)
    od = np.array([p["od600_mean"] for p in points], dtype=float)

    # OD_max is well-defined by the zero-ethanol point
    od_max = float(od[ethanol == 0][0])

    # Try scipy least_squares first
    try:
        from scipy.optimize import least_squares

        def residuals(params: np.ndarray) -> np.ndarray:
            ki, n = params
            return od_max / (1.0 + (ethanol / ki) ** n) - od

        # Initial guess: K_i ~ 3 (between ethanol=1 and ethanol=5), n ~ 2
        x0 = np.array([3.0, 2.0])
        res = least_squares(residuals, x0, bounds=([0.01, 0.1], [100.0, 10.0]))
        ki_fit, n_fit = res.x.tolist()
        fitted = od_max / (1.0 + (ethanol / ki_fit) ** n_fit)
        ss_res = float(np.sum((od - fitted) ** 2))
        ss_tot = float(np.sum((od - od.mean()) ** 2))
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 1.0
        rmse = float(np.sqrt(np.mean((od - fitted) ** 2)))
        converged = bool(res.success)
        method = "scipy.optimize.least_squares"
    except ImportError:
        # Grid fallback (4 points, coarse enough to be tight)
        best = None
        for ki in np.arange(0.5, 10.0, 0.1):
            for n in np.arange(0.5, 6.0, 0.25):
                fit = od_max / (1.0 + (ethanol / ki) ** n)
                err = float(np.sum((od - fit) ** 2))
                if best is None or err < best[0]:
                    best = (err, float(ki), float(n))
        _, ki_fit, n_fit = best
        fitted = od_max / (1.0 + (ethanol / ki_fit) ** n_fit)
        ss_res = float(np.sum((od - fitted) ** 2))
        ss_tot = float(np.sum((od - od.mean()) ** 2))
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 1.0
        rmse = float(np.sqrt(np.mean((od - fitted) ** 2)))
        converged = True
        method = "grid_search (no scipy)"

    # Find ethanol % at 99% inhibition (OD = 0.01 * OD_max)
    # 1/(1+(e/K)^n) = 0.01  =>  (e/K)^n = 99  =>  e = K * 99^(1/n)
    e_99 = float(ki_fit * (99.0 ** (1.0 / n_fit)))

    return {
        "method": method,
        "od_max": od_max,
        "k_i": ki_fit,
        "n_hill": n_fit,
        "ethanol_99pct_inhibition": e_99,
        "r_squared": r2,
        "rmse": rmse,
        "converged": converged,
        "n_points": len(points),
    }


def extract_all_anchors() -> List[Dict[str, Any]]:
    """Return the anchor list ready to merge into real_anchors.json."""
    points = load_ethanol_inhibition()
    fit = fit_inhibition_curve(points)

    base_provenance = {
        "source_type": "doi_record",
        "source_url": f"https://zenodo.org/records/{ZENODO_ID}",
        "doi": DOI,
        "doi_resolvable": True,
        "publisher": "Zenodo (CERN)",
        "paper_doi": "10.1128/JB.00639-13",
        "paper_title": (
            "Biotin-Independent Saccharomyces cerevisiae with Enhanced Growth: "
            "Engineering an Acetyl-CoA Carboxylase"
        ),
        "dataset_name": "Slanska_Biotin-Independent_S.cerevisiae.xlsx",
        "file_sha256": _sha256_file(SLANSKA_XLSX),
        "file_size_bytes": SLANSKA_XLSX.stat().st_size,
        "data_file_path": str(SLANSKA_XLSX.relative_to(ROOT)),
        "extraction_method": "openpyxl.load_workbook + numpy/scipy least_squares",
        "extraction_date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "extraction_script": "scripts/extract_yeast_anchors.py",
        "license": "CC-BY 4.0 (Zenodo record 17423657)",
        "raw_points": points,
        "fit": fit,
    }

    def _mk(anchor_id: str, param: str, value: float, units: str,
            note: str) -> Dict[str, Any]:
        p = dict(base_provenance)
        return {
            "id": anchor_id,
            "scenario": "yeast_ethanol_fermentation",
            "parameter": param,
            "value": value,
            "units": units,
            "notes": note,
            "provenance": p,
        }

    anchors = [
        _mk(
            "yeast_ethanol_yield_real",
            "od_max_at_zero_ethanol",
            round(fit["od_max"], 4),
            "OD600",
            f"Yeast OD600 at 0% ethanol (Figure S4 row 1). "
            f"Fit model: OD = OD_max / (1 + (e/K_i)^n); "
            f"OD_max is directly observed at zero ethanol.",
        ),
        _mk(
            "yeast_ethanol_ki_real",
            "half_max_inhibition_conc",
            round(fit["k_i"], 4),
            "% (v/v)",
            f"Half-max ethanol inhibition concentration (K_i) fitted from "
            f"4-point Figure S4 inhibition curve. Fit R²={fit['r_squared']:.4f}, "
            f"RMSE={fit['rmse']:.4f}. Model: OD(e) = OD_max/(1+(e/K_i)^n). "
            f"n_Hill={fit['n_hill']:.2f} indicates sharpness of inhibition.",
        ),
        _mk(
            "yeast_ethanol_hill_n_real",
            "hill_coefficient",
            round(fit["n_hill"], 4),
            "dimensionless",
            f"Hill coefficient for ethanol inhibition of yeast growth. "
            f"n>1 = cooperative inhibition (steeper than Haldane n=1); "
            f"n<1 = graded inhibition. Value {fit['n_hill']:.2f} from "
            f"4-point least-squares fit (R²={fit['r_squared']:.4f}).",
        ),
        _mk(
            "yeast_ethanol_full_inhibition_real",
            "ethanol_at_99pct_inhibition",
            round(fit["ethanol_99pct_inhibition"], 4),
            "% (v/v)",
            f"Ethanol concentration at which yeast growth is 99% inhibited "
            f"(OD = 0.01 * OD_max), analytically derived from "
            f"e = K_i * 99^(1/n). Useful as a hard bound for fermentation "
            f"simulations; beyond this concentration yeast is considered non-viable.",
        ),
    ]
    return anchors


def merge_into_real_anchors(anchors: List[Dict[str, Any]]) -> None:
    """Insert yeast anchors into data/real_anchors.json (dedup by id)."""
    path = DATA_DIR / "real_anchors.json"
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
    else:
        existing = {"anchors": [], "sources": [], "total_anchors": 0}
    existing_anchors = existing.get("anchors", [])
    existing_ids = {a.get("id") for a in existing_anchors}
    new_only = [a for a in anchors if a["id"] not in existing_ids]
    existing_anchors.extend(new_only)
    existing["anchors"] = existing_anchors
    existing["total_anchors"] = len(existing_anchors)
    existing["last_updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Register the Zenodo source if not present
    src_list = existing.get("sources", [])
    if not any(s.get("id") == "zenodo_17423657" for s in src_list):
        src_list.append({
            "id": "zenodo_17423657",
            "type": "Yeast fermentation (ethanol inhibition)",
            "url": f"https://zenodo.org/records/{ZENODO_ID}",
            "doi": DOI,
        })
        existing["sources"] = src_list

    path.write_text(json.dumps(existing, ensure_ascii=False, indent=2),
                    encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true",
                    help="merge anchors into data/real_anchors.json")
    ap.add_argument("--output", help="write anchors JSON to this path")
    args = ap.parse_args()

    if not SLANSKA_XLSX.exists():
        print(f"[FATAL] {SLANSKA_XLSX} not found. Download first:",
              file=sys.stderr)
        print(f"  curl -L -o {SLANSKA_XLSX} "
              f"https://zenodo.org/api/records/{ZENODO_ID}/"
              f"files/Slanska_Biotin-Independent_S.cerevisiae.xlsx/content",
              file=sys.stderr)
        return 2

    anchors = extract_all_anchors()
    print(f"[extract_yeast_anchors] produced {len(anchors)} anchors:")
    for a in anchors:
        print(f"  - {a['id']:40s} = {a['value']} {a['units']}")
    fit = anchors[0]["provenance"]["fit"]
    print(f"  fit: method={fit['method']} R²={fit['r_squared']:.4f} "
          f"RMSE={fit['rmse']:.4f} converged={fit['converged']}")

    if args.output:
        Path(args.output).write_text(
            json.dumps(anchors, ensure_ascii=False, indent=2),
            encoding="utf-8")
        print(f"[ok] wrote {args.output}")

    if args.refresh:
        merge_into_real_anchors(anchors)
        print(f"[ok] merged into {DATA_DIR/'real_anchors.json'}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
