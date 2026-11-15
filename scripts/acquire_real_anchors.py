"""Extract real experimental data from downloaded sources and convert to wet_lab_anchors format.

P1-3: Real experimental anchors from published sources (Figshare/Zenodo).

Sources:
  - Zenodo 19416053: E. coli growth at different temperatures and glucose concentrations
  - Zenodo 7311805: Methylene blue adsorption on water hyacinth activated carbon (Langmuir)
  - Figshare 2048400: Yeast ethanol fermentation inhibition data

Usage:
    python scripts/acquire_real_anchors.py
    python scripts/acquire_real_anchors.py --output data/real_anchors.json
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def extract_ecoli_monod_params(filepath: str) -> Dict[str, Any]:
    """Extract Monod growth parameters from E. coli growth data.

    Data: Optical density vs time at different temperatures and glucose concentrations.
    Parameters: mu_max (max growth rate), K_s (substrate half-saturation).

    The data is stored as d(OD)/dt at each time point for multiple conditions.
    We aggregate the growth rates at the beginning of growth (lag phase excluded)
    to estimate mu_max at different glucose concentrations.
    """
    import openpyxl

    wb = openpyxl.load_workbook(filepath, read_only=True, data_only=True)
    ws = wb.active

    # Parse all rows
    rows = list(ws.iter_rows(min_row=1, values_only=True))
    if len(rows) < 2:
        return {"error": "empty data"}

    # Header row tells us the structure
    # Data is in blocks of 4 columns: (Temperatura, %glucosa, tcultivo, d(OD)/dt)
    # Each row has multiple blocks side by side

    header = rows[0]
    # Find all blocks by scanning for "Temperatura" headers
    blocks = []
    for i, cell in enumerate(header):
        if cell == "Temperatura" and i + 3 < len(header):
            blocks.append(i)

    # Parse data into conditions
    conditions = {}
    for row_idx in range(1, len(rows)):
        row = rows[row_idx]
        for block_idx in blocks:
            temp = row[block_idx]
            glucose_pct = row[block_idx + 1]
            time_min = row[block_idx + 2]
            growth_rate = row[block_idx + 3]

            if temp is None or glucose_pct is None or time_min is None or growth_rate is None:
                continue

            key = (int(temp), float(glucose_pct))
            if key not in conditions:
                conditions[key] = []
            conditions[key].append({
                "time_min": float(time_min),
                "growth_rate": float(growth_rate),
            })

    # For each condition, find the max growth rate (approximation of mu_max)
    # and the growth rate at low glucose (approximation related to K_s)
    results = []
    for (temp, glucose_pct), data_points in sorted(conditions.items()):
        growth_rates = [p["growth_rate"] for p in data_points if p["growth_rate"] > 0]
        if not growth_rates:
            continue

        mu_max = max(growth_rates)
        # Convert glucose percentage to concentration (assuming standard medium ~100 g/L total sugar)
        glucose_conc = glucose_pct * 1.0  # approximate: 1% = 1 g/L

        results.append({
            "temperature_C": temp,
            "glucose_pct": glucose_pct,
            "glucose_conc_g_L": glucose_conc,
            "mu_max_est": round(mu_max, 6),
            "n_data_points": len(data_points),
        })

    # Estimate K_s by finding where growth rate is half of mu_max
    # Group by temperature and find the glucose concentration at half-max growth
    temp_groups = {}
    for r in results:
        t = r["temperature_C"]
        if t not in temp_groups:
            temp_groups[t] = []
        temp_groups[t].append(r)

    ks_estimates = []
    for temp, group in sorted(temp_groups.items()):
        if len(group) < 2:
            continue
        # Sort by glucose concentration
        group.sort(key=lambda x: x["glucose_conc_g_L"])
        # Find max growth rate at this temperature
        mu_max_at_temp = max(r["mu_max_est"] for r in group)
        half_max = mu_max_at_temp / 2.0
        # Find glucose concentration at half-max
        for r in group:
            if r["mu_max_est"] >= half_max:
                ks_estimates.append({
                    "temperature_C": temp,
                    "K_s_est": r["glucose_conc_g_L"],
                    "mu_max": mu_max_at_temp,
                })
                break

    return {
        "source": "Zenodo 19416053",
        "doi": "10.5281/zenodo.19416053",
        "title": "Optical Density Data from Growth of E. coli",
        "authors": "González Siso et al.",
        "year": 2024,
        "conditions": results,
        "K_s_estimates": ks_estimates,
        "mu_max_estimates": [r["mu_max_est"] for r in results],
    }


def extract_mb_adsorption_params(data_dir: Optional[str] = None) -> Dict[str, Any]:
    """Extract Langmuir adsorption parameters for methylene blue.

    Source: Zenodo 7311805 - Water hyacinth activated carbon adsorption.

    2026-10-15 hardening (per Sci assessment report §M1 discipline):
    This source publishes only the paper PDF, not raw isotherm data files.
    The Zenodo record exposes no numerical time-series / concentration
    table suitable for Langmuir fitting. To honour the "no synthetic
    fallback" rule, we do NOT fabricate parameters from published prose.
    If no real numeric data file is present in the source directory, the
    function returns an empty record and the caller MUST record this
    anchor as 0 (unavailable), not as hardcoded values.
    """
    # No numerical isotherm data exists for this source. Return empty and
    # let build_anchors() skip it. This is the honest answer.
    return {
        "source": "Zenodo 7311805",
        "doi": "10.5281/zenodo.7311805",
        "title": "Bio-Waste-Derived Adsorbent Materials for Methylene Blue Removal",
        "authors": "Multiple authors (water hyacinth activated carbon study)",
        "year": 2024,
        "status": "unavailable_no_raw_data",
        "reason": (
            "Zenodo record publishes only the paper PDF, not raw isotherm "
            "concentration-time series. Fabricating qmax/K_L from published "
            "prose would violate the no-synthetic-fallback rule in "
            "reports/assessment/SwarmLabs_硬科技提升执行方案_2026-10-15.md §M1."
        ),
        "parameters": {},
        "isotherm_type": "Langmuir",
    }


def extract_yeast_fermentation_params(data_dir: Optional[str] = None) -> Dict[str, Any]:
    """Extract yeast ethanol fermentation parameters.

    Source: Figshare 2048400 - Substrate and Product Inhibition on Yeast Performance.

    2026-10-15 hardening: The Figshare API v2 returns HTTP 403 for this
    record (rate-limit / auth-gated). No raw numeric data is available
    locally; data/raw/yeast_ethanol_fermentation/ is empty. Per the
    "no synthetic fallback" rule we record 0 instead of fabricating the
    95% yield / 70 g/L inhibition threshold numbers from published prose.
    """
    return {
        "source": "Figshare 2048400",
        "doi": "10.25378/figshare.2048400",
        "title": "Substrate and Product Inhibition on Yeast Performance in Ethanol Fermentation",
        "authors": "Multiple authors (S. cerevisiae BY4742 study)",
        "year": 2020,
        "status": "unavailable_api_403",
        "reason": (
            "Figshare API v2 returns HTTP 403 for record 2048400. No raw "
            "fermentation data files are accessible without interactive "
            "manual download. Per the no-synthetic-fallback rule this "
            "anchor is honestly recorded as 0."
        ),
        "parameters": {},
    }


def build_anchors(ecoli_data: Dict, mb_data: Dict, yeast_data: Dict) -> List[Dict]:
    """Convert extracted data into wet_lab_anchors format."""
    anchors = []

    # Anchor 1: E. coli Monod - K_s estimate
    if ecoli_data.get("K_s_estimates"):
        ks = ecoli_data["K_s_estimates"][0]
        anchors.append({
            "id": "ecoli_monod_ks_real",
            "scenario": "ecoli_monod_growth",
            "parameter": "K_s",
            "value": ks["K_s_est"],
            "unit": "g/L",
            "source_type": "experimental",
            "source_url": "https://zenodo.org/records/19416053",
            "doi": ecoli_data["doi"],
            "temperature_C": ks["temperature_C"],
            "mu_max_est": ks["mu_max"],
            "notes": f"Estimated from {ecoli_data['title']}, {ecoli_data['year']}. "
                     f"K_s ≈ glucose conc at half-max growth rate at {ks['temperature_C']}°C.",
        })

    # Anchor 2: E. coli Monod - mu_max estimate
    mu_maxes = [r["mu_max_est"] for r in ecoli_data.get("conditions", []) if r["mu_max_est"] > 0]
    if mu_maxes:
        anchors.append({
            "id": "ecoli_monod_mu_max_real",
            "scenario": "ecoli_monod_growth",
            "parameter": "mu_max",
            "value": round(np.mean(mu_maxes), 6),
            "unit": "1/min",
            "source_type": "experimental",
            "source_url": "https://zenodo.org/records/19416053",
            "doi": ecoli_data["doi"],
            "n_conditions": len(mu_maxes),
            "notes": f"Mean of {len(mu_maxes)} experimental growth rates from "
                     f"{ecoli_data['title']}, {ecoli_data['year']}.",
        })

    # Anchor 3: MB Adsorption - qmax
    params = mb_data.get("parameters", {})
    if params:
        anchors.append({
            "id": "mb_adsorption_qmax_real",
            "scenario": "mb_adsorption_langmuir",
            "parameter": "qmax",
            "value": params["qmax_mg_g"],
            "unit": "mg/g",
            "source_type": "experimental",
            "source_url": "https://zenodo.org/records/7311805",
            "doi": mb_data["doi"],
            "pH": params["pH"],
            "temperature_C": params["temperature_C"],
            "notes": f"Langmuir maximum adsorption capacity from {mb_data['title']}, "
                     f"{mb_data['year']}. R²={params['R2']}.",
        })

        # Anchor 4: MB Adsorption - K_L
        anchors.append({
            "id": "mb_adsorption_K_L_real",
            "scenario": "mb_adsorption_langmuir",
            "parameter": "K_L",
            "value": params["K_L_L_mg"],
            "unit": "L/mg",
            "source_type": "experimental",
            "source_url": "https://zenodo.org/records/7311805",
            "doi": mb_data["doi"],
            "notes": f"Langmuir adsorption constant from {mb_data['title']}, "
                     f"{mb_data['year']}.",
        })

    # Anchor 5: Yeast fermentation - max yield
    yparams = yeast_data.get("parameters", {})
    if yparams:
        anchors.append({
            "id": "yeast_ethanol_yield_real",
            "scenario": "yeast_ethanol_fermentation",
            "parameter": "max_yield_pct",
            "value": yparams["max_ethanol_yield_pct"],
            "unit": "%",
            "source_type": "experimental",
            "source_url": "https://figshare.com/articles/dataset/2048400",
            "doi": yeast_data["doi"],
            "temperature_C": yparams["temperature_C"],
            "pH": yparams["pH"],
            "notes": f"Maximum ethanol yield at optimal conditions from {yeast_data['title']}, "
                     f"{yeast_data['year']}.",
        })

        # Anchor 6: Yeast fermentation - ethanol inhibition threshold
        anchors.append({
            "id": "yeast_ethanol_inhibition_real",
            "scenario": "yeast_ethanol_fermentation",
            "parameter": "ethanol_inhibition_threshold",
            "value": yparams["ethanol_inhibition_threshold_g_L"],
            "unit": "g/L",
            "source_type": "experimental",
            "source_url": "https://figshare.com/articles/dataset/2048400",
            "doi": yeast_data["doi"],
            "notes": f"Ethanol concentration above which yeast stops fermenting, from "
                     f"{yeast_data['title']}, {yeast_data['year']}.",
        })

    return anchors


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Acquire real experimental anchors")
    parser.add_argument("--output", default="data/real_anchors.json",
                        help="Output JSON file path")
    parser.add_argument("--ecoli-file", default="data/raw/ecoli_growth.xlsx",
                        help="Path to E. coli growth XLSX file")
    args = parser.parse_args()

    print("=== Real Experimental Anchor Acquisition ===\n")

    # 1. E. coli growth data
    print("[1/3] Extracting E. coli Monod growth data...")
    if os.path.exists(args.ecoli_file):
        ecoli_data = extract_ecoli_monod_params(args.ecoli_file)
        print(f"  Found {len(ecoli_data.get('conditions', []))} conditions")
        print(f"  K_s estimates: {len(ecoli_data.get('K_s_estimates', []))}")
        print(f"  mu_max range: {min(ecoli_data.get('mu_max_estimates', [0])):.6f} - "
              f"{max(ecoli_data.get('mu_max_estimates', [0])):.6f}")
    else:
        print(f"  WARNING: {args.ecoli_file} not found, skipping")
        ecoli_data = {}

    # 2. MB adsorption data — may be unavailable (see function docstring).
    print("\n[2/3] Extracting MB adsorption parameters...")
    mb_data = extract_mb_adsorption_params()
    if mb_data.get("parameters"):
        print(f"  qmax = {mb_data['parameters']['qmax_mg_g']} mg/g")
        print(f"  K_L = {mb_data['parameters']['K_L_L_mg']} L/mg")
    else:
        print(f"  STATUS: {mb_data.get('status', 'unavailable')}")
        print(f"  REASON: {mb_data.get('reason', 'no raw numeric data published')}")

    # 3. Yeast fermentation data — may be unavailable (see function docstring).
    print("\n[3/3] Extracting yeast fermentation parameters...")
    yeast_data = extract_yeast_fermentation_params()
    if yeast_data.get("parameters"):
        print(f"  Max yield = {yeast_data['parameters']['max_ethanol_yield_pct']}%")
        print(f"  Ethanol inhibition threshold = {yeast_data['parameters']['ethanol_inhibition_threshold_g_L']} g/L")
    else:
        print(f"  STATUS: {yeast_data.get('status', 'unavailable')}")
        print(f"  REASON: {yeast_data.get('reason', 'no raw data accessible')}")

    # 4. Build anchors
    print("\n[4/4] Building anchor records...")
    anchors = build_anchors(ecoli_data, mb_data, yeast_data)
    print(f"  Created {len(anchors)} real experimental anchors")

    # 5. Output
    output = {
        "acquired_at": __import__("datetime").datetime.now().isoformat(),
        "total_anchors": len(anchors),
        "sources": [
            {"id": "zenodo_19416053", "type": "E. coli growth", "url": "https://zenodo.org/records/19416053"},
            {"id": "zenodo_7311805", "type": "MB adsorption", "url": "https://zenodo.org/records/7311805"},
            {"id": "figshare_2048400", "type": "Yeast fermentation", "url": "https://figshare.com/articles/dataset/2048400"},
        ],
        "anchors": anchors,
    }

    output_path = os.path.join(ROOT, args.output)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\nOutput: {output_path}")
    print(f"  {len(anchors)} anchors written")

    # Print summary
    print("\n=== Anchor Summary ===")
    for a in anchors:
        print(f"  {a['id']:40s} {a['parameter']:20s} = {a['value']:.4f} {a['unit']}")

    return output


if __name__ == "__main__":
    main()
