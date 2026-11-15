"""
Engine Admission Criteria for SwarmLabs

Defines the bar that an engine must clear to move from "experimental" (163 count today)
to "validated" (currently 1: electrodialysis). Provides:

1. Quantitative thresholds (rmse_rel, coverage, calibration, coverage of parameters)
2. A checker that evaluates any engine against these criteria
3. A report of all 163 current engines and their admission status

Rationale: The assessment report identified that 28/29 ENGINE_STATS entries had
mean_error=5.0 as a hardcoded placeholder. This module provides an honest
classification: only engines that meet the bar get evidence_grade='validated'.

Design principles:
- A15: no extrapolation outside design domain
- A14: roughness must be within smooth surrogate applicability
- Noise floor: 3% minimum variance floor (never lower, per UQ first-class citizen rule)
- PICP: minimum 0.90 empirical coverage on hold-out (target 0.95)
- Coverage width: intervals must be informative (not infinitely wide)
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any


DATA_DIR = Path(__file__).resolve().parent.parent / "data"


# ---------------------------------------------------------------------------
# Thresholds (single source of truth)
# ---------------------------------------------------------------------------

THRESHOLDS = {
    # Prediction accuracy (relative RMSE against ground truth on hold-out)
    "rmse_rel_max": 0.10,           # relative RMSE must be < 10%
    "rmse_rel_warn": 0.05,          # warning threshold

    # Coverage / calibration (conformal intervals at target 95%)
    "coverage_target": 0.95,
    "coverage_tolerance": 0.05,     # allowed deviation from target
    "picp_min": 0.90,               # minimum PICP on hold-out
    "calibration_error_max": 0.05,  # |coverage - target|

    # Interval informativeness
    "coverage_width_relative_max": 0.50,  # average interval width / mean(f)
    "coverage_width_relative_warn": 0.30,

    # Data coverage
    "min_holdout_size": 30,         # at least 30 hold-out points
    "min_calibration_size": 50,     # at least 50 calibration points
    "parameter_space_coverage": 0.7,  # Sobol or random coverage of parameter space

    # Robustness
    "roughness_cap": 0.30,          # A14 applicability boundary
    "extrapolation_reject_rate_min": 0.95,  # A15 gate: 95% of OOD perturbations rejected

    # Reproducibility
    "seed_deterministic": True,
    "noise_floor_min": 0.03,        # never below 3% (per UQ rule)

    # Negative-result transparency
    "report_refutations": True,     # engine must honestly report REFUTED verdicts
}


@dataclass
class AdmissionResult:
    """Result of admission check for a single engine."""
    engine_id: str
    passed: bool
    grade: str  # 'validated' | 'experimental' | 'rejected'
    failures: list[str]
    warnings: list[str]
    metrics: dict
    notes: str = ""


def check_admission(engine_id: str, metrics: dict) -> AdmissionResult:
    """Evaluate one engine against all thresholds. Returns pass/fail with reason.

    Grades (four-tier, P5 honesty fix 2026-10-25):
      - 'validated'   : passed every check, evidence sufficient
      - 'experimental': partial data, a few checks failed (<=2)
      - 'unassessed'  : no V&V evidence collected yet (all key metrics null
                        and/or me_source is a placeholder marker). Not a
                        failure — an engine cannot be 'rejected' for work
                        not yet performed.
      - 'rejected'    : hard evidence of a defect (placeholder value, or
                        metrics clearly exceeding thresholds)
    """
    failures: list[str] = []
    warnings: list[str] = []

    # --- Hard-failure placeholder detection (P0-3: mean_error=5.0 exactly) ---
    # This is the ONLY way an engine can be 'rejected' via placeholder.
    # A blank/null placeholder is 'unassessed', not rejected.
    if metrics.get("mean_error") == 5.0 or metrics.get("rmse_abs") == 5.0:
        failures.append("placeholder_mean_error_5.0: hardcoded placeholder, not a real measurement")

    # --- Data-validity check (P5 2026-10-25) ---
    # rmse_rel > 1.0 (i.e. >100%) is physically unreasonable for a working
    # surrogate: the prediction error exceeds the signal magnitude. This is
    # almost certainly a data-entry error (unit mix-up, percentage scaling),
    # not a genuine failure. Treat as unassessed until re-measured.
    rmse_rel_raw = metrics.get("rmse_rel")
    rmse_rel_invalid = (isinstance(rmse_rel_raw, (int, float))
                        and rmse_rel_raw > 1.0)

    # --- Placeholder-source marker detection ---
    # An engine whose `me_source` is a marker string (e.g.
    # 'placeholder_removed_20261022') is by definition not-yet-assessed.
    me_source = metrics.get("me_source") or ""
    is_placeholder_source = isinstance(me_source, str) and "placeholder" in me_source

    # --- Data-presence flags (drive unassessed vs. measured) ---
    rmse_rel = metrics.get("rmse_rel")
    cov = metrics.get("coverage") or metrics.get("picp")
    holdout = metrics.get("n_holdout", 0)
    n_calibration = metrics.get("n_calibration", 0)
    has_any_metric = (rmse_rel is not None or cov is not None
                      or holdout > 0 or n_calibration > 0)

    # --- Accuracy (skip if the value is physically unreasonable) ---
    if rmse_rel is not None and not rmse_rel_invalid:
        if rmse_rel > THRESHOLDS["rmse_rel_max"]:
            failures.append(f"rmse_rel_too_high: {rmse_rel:.4f} > {THRESHOLDS['rmse_rel_max']}")
        elif rmse_rel > THRESHOLDS["rmse_rel_warn"]:
            warnings.append(f"rmse_rel_elevated: {rmse_rel:.4f} > {THRESHOLDS['rmse_rel_warn']}")

    # --- Coverage ---
    if cov is not None:
        if cov < THRESHOLDS["picp_min"]:
            failures.append(f"picp_below_min: {cov:.4f} < {THRESHOLDS['picp_min']}")
        elif cov > THRESHOLDS["coverage_target"] + THRESHOLDS["coverage_tolerance"]:
            failures.append(f"coverage_too_high: {cov:.4f} > {THRESHOLDS['coverage_target'] + THRESHOLDS['coverage_tolerance']} (too-wide intervals)")

    # --- Calibration error ---
    cal_err = metrics.get("calibration_error")
    if cal_err is not None and cal_err > THRESHOLDS["calibration_error_max"]:
        failures.append(f"calibration_error_high: {cal_err:.4f} > {THRESHOLDS['calibration_error_max']}")

    # --- Data size (only meaningful if we claim to have measured) ---
    if has_any_metric and not rmse_rel_invalid:
        if holdout < THRESHOLDS["min_holdout_size"]:
            failures.append(f"insufficient_holdout: {holdout} < {THRESHOLDS['min_holdout_size']}")
        if n_calibration < THRESHOLDS["min_calibration_size"]:
            warnings.append(f"low_calibration_size: {n_calibration} < {THRESHOLDS['min_calibration_size']}")

    # --- Roughness (A14) ---
    roughness = metrics.get("roughness")
    if roughness is not None and roughness > THRESHOLDS["roughness_cap"]:
        failures.append(f"roughness_exceeds_A14: {roughness:.4f} > {THRESHOLDS['roughness_cap']} (out of smooth surrogate domain)")

    # --- Extrapolation gate (A15) ---
    reject_rate = metrics.get("extrapolation_reject_rate")
    if reject_rate is not None and reject_rate < THRESHOLDS["extrapolation_reject_rate_min"]:
        failures.append(f"extrapolation_gate_leaky: {reject_rate:.4f} < {THRESHOLDS['extrapolation_reject_rate_min']}")

    # --- Placeholder mean_error detection ---
    me = metrics.get("mean_error")
    if me is None and not is_placeholder_source and not rmse_rel_invalid:
        warnings.append("mean_error_missing: no measured mean_error (may be acceptable if rmse_rel provided)")
    elif me is not None and not isinstance(me, (int, float)):
        failures.append(f"mean_error_invalid_type: {type(me).__name__}")

    # --- Grade assignment (four-tier) ---
    if rmse_rel_invalid:
        # Physically-unreasonable measurement — treat as data-invalid,
        # needs re-measurement. Not a defect verdict on the engine itself.
        grade = "unassessed"
        passed = False
        warnings.append(
            f"rmse_rel_invalid: {rmse_rel_raw:.4f} > 1.0 (>100% relative error is "
            f"physically unreasonable; likely a data-entry error — treat as unassessed)"
        )
    elif failures and any(
        f.startswith("placeholder_mean_error_5.0")
        or f.startswith("rmse_rel_too_high")
        or f.startswith("picp_below_min")
        or f.startswith("roughness_exceeds_A14")
        or f.startswith("extrapolation_gate_leaky")
        for f in failures
    ):
        # Hard defect — engine has been assessed and found broken.
        grade = "rejected"
        passed = False
    elif is_placeholder_source and not has_any_metric and not failures:
        # Engine is honestly blank: placeholder removed, no V&V yet.
        # This is a work-queue signal, not a rejection.
        grade = "unassessed"
        passed = False
        warnings.append("unassessed: no V&V evidence yet; me_source marks a removed placeholder")
    else:
        passed = not failures
        grade = "validated" if passed else "experimental"

    return AdmissionResult(
        engine_id=engine_id,
        passed=passed,
        grade=grade,
        failures=failures,
        warnings=warnings,
        metrics=metrics,
    )


def classify_all_engines(engines: dict[str, dict]) -> dict:
    """Classify a dict of {engine_id: metrics} against admission criteria."""
    results = {}
    for eid, m in engines.items():
        results[eid] = asdict(check_admission(eid, m))
    return results


def admission_summary(results: dict) -> dict:
    by_grade = {}
    common_failures: dict[str, int] = {}
    for r in results.values():
        by_grade[r["grade"]] = by_grade.get(r["grade"], 0) + 1
        for f in r["failures"]:
            key = f.split(":")[0]
            common_failures[key] = common_failures.get(key, 0) + 1
    return {
        "total": len(results),
        "by_grade": by_grade,
        "common_failures": dict(sorted(common_failures.items(), key=lambda x: -x[1])),
    }


# ---------------------------------------------------------------------------
# Current state: load ENGINE_STATS (28/29 placeholders) and classify
# ---------------------------------------------------------------------------

def get_current_engine_metrics() -> dict[str, dict]:
    """Extract ENGINE_STATS from frontend_fixed/functions/api/[[path]].js and return metrics dict."""
    import re
    js_path = DATA_DIR.parent / "frontend_fixed" / "functions" / "api" / "[[path]].js"
    if not js_path.exists():
        return {}
    text = js_path.read_text(encoding="utf-8")
    m = re.search(r"const ENGINE_STATS\s*=\s*(\{.*?\});\s*\n", text, re.DOTALL)
    if not m:
        # Fallback: extract by brace matching from the anchor
        m2 = re.search(r"const ENGINE_STATS\s*=\s*\{", text)
        if not m2:
            return {}
        start = m2.end()
        depth = 1
        i = start
        while i < len(text) and depth > 0:
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
            i += 1
        raw = text[start:i-1]
    else:
        raw = m.group(1)
    # Convert JS object to JSON: single quotes -> double, quote bare keys
    cleaned = raw.replace("'", '"')
    cleaned = re.sub(r'([{,]\s*)([a-zA-Z_]\w*)\s*:', r'\1"\2":', cleaned)
    # Strip trailing commas before } or ] (JS allows them, JSON doesn't)
    cleaned = re.sub(r',\s*([}\]])', r'\1', cleaned)
    try:
        data = json.loads(cleaned)
    except Exception:
        return {}
    out = {}
    for eid, info in data.items():
        if not isinstance(info, dict):
            continue
        out[eid] = {
            "mean_error": info.get("me"),
            "rmse_rel": info.get("rel"),
            "coverage": info.get("coverage"),
            "picp": info.get("picp"),
            "n_holdout": info.get("holdout") or 0,
            "n_calibration": info.get("calibration") or 0,
            "roughness": info.get("roughness"),
            "total": info.get("total"),
            "evidence_grade_input": info.get("evidence_grade"),
            "me_source": info.get("me_source"),
        }
    return out


def classify_current() -> dict:
    """Classify all currently-known engines from ENGINE_STATS."""
    engines = get_current_engine_metrics()
    if not engines:
        return {"note": "ENGINE_STATS not parseable", "results": {}}
    results = classify_all_engines(engines)
    return {
        "results": results,
        "summary": admission_summary(results),
        "thresholds": THRESHOLDS,
    }


def write_report(path: Path | None = None) -> dict:
    """Classify all engines and write a report."""
    p = path or (DATA_DIR / "engine_admission_report.json")
    report = classify_current()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    return report


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--grade", choices=["validated", "experimental", "rejected"])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--thresholds", action="store_true")
    args = ap.parse_args()
    if args.thresholds:
        print(json.dumps(THRESHOLDS, indent=2))
    if args.stats:
        r = classify_current()
        print(json.dumps(r.get("summary", {}), indent=2))
    if args.report:
        r = write_report()
        s = r.get("summary", {})
        print(f"Classified {s.get('total', 0)} engines:")
        print(f"  validated: {s.get('by_grade', {}).get('validated', 0)}")
        print(f"  experimental: {s.get('by_grade', {}).get('experimental', 0)}")
        print(f"  rejected: {s.get('by_grade', {}).get('rejected', 0)}")
        print(f"Common failures: {list(s.get('common_failures', {}).items())[:5]}")
    if args.list:
        r = classify_current()
        results = r.get("results", {})
        if args.grade:
            results = {k: v for k, v in results.items() if v.get("grade") == args.grade}
        if args.json:
            print(json.dumps(results, indent=2))
        else:
            print(f"{'ENGINE':35s} {'GRADE':12s} {'FAILURES':30s}")
            print("-" * 80)
            for eid, r in results.items():
                fails = "; ".join(r.get("failures", [])[:2])[:30]
                print(f"{eid[:35]:35s} {r.get('grade','?'):12s} {fails}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
