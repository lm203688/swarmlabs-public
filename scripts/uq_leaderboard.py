#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UQ Leaderboard — compare conformal/UQ methods across scenarios.

Compares:
  - Gaussian (uncalibrated, 1.96σ)
  - Cross-conformal (k-fold, calibrated)
  - Sharpness-aware conformal (if available)

Metrics: coverage (PICP), sharpness (NMPIW), calibration error (cal_err)

Output: JSON leaderboard + optional HTML table.

Run:
    python scripts/uq_leaderboard.py              # full run
    python scripts/uq_leaderboard.py --top 10     # top 10 scenarios
    python scripts/uq_leaderboard.py --json       # machine-readable output
"""
import argparse
import json
import os
import sys
import time
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, REPO)

from engine.conformal import (
    nonconformity_std,
    conformal_quantile,
    cross_conformal_scores,
    picp,
    mpiw,
    interval_score,
    miscalibration_area,
    evaluate_scenario,
)
from engine.scenario_registry import get_registry


def run_leaderboard(top_n=None, verbose=True):
    """Run UQ comparison across all scenarios."""
    t0 = time.time()
    reg = get_registry()
    scenarios = list(reg.items)

    if top_n:
        # Prioritize flagship scenarios
        scenarios.sort(key=lambda s: (not s.get("flagship", False), s.get("rubric_passed", 0)))
        scenarios = scenarios[:top_n]

    leaderboard = []

    for i, sc in enumerate(scenarios):
        key = sc.get("key", "unknown")
        entry = {
            "key": key,
            "domain": sc.get("domain", "unknown"),
            "flagship": sc.get("flagship", False),
            "dim": sc.get("dim", 1),
        }

        try:
            result = evaluate_scenario(sc, alpha=0.05, seed=0, noise_rel=0.03)
            conf = result.get("conformal", {})
            base = result.get("baseline", {})
            entry["coverage"] = conf.get("picp", None)
            entry["sharpness"] = conf.get("nmpiw", None)
            entry["cal_err"] = conf.get("interval_score", None)
            entry["baseline_coverage"] = base.get("picp", None)
            entry["baseline_sharpness"] = base.get("nmpiw", None)
            entry["verdict"] = result.get("verdict", "unknown")
            entry["q_explodes"] = result.get("q_explodes", False)
            entry["status"] = "ok"
        except Exception as e:
            entry["status"] = f"error: {str(e)[:100]}"

        if verbose:
            cov = entry.get("coverage")
            shar = entry.get("sharpness")
            cov_s = f"{cov:.3f}" if cov is not None else "N/A"
            shar_s = f"{shar:.3f}" if shar is not None else "N/A"
            print(f"  [{i+1}/{len(scenarios)}] {key:35s} cov={cov_s} sharp={shar_s}")

        leaderboard.append(entry)

    elapsed = time.time() - t0

    # Summary statistics
    valid = [e for e in leaderboard if e["status"] == "ok"]
    summary = {
        "total_scenarios": len(leaderboard),
        "evaluated": len(valid),
        "errors": len(leaderboard) - len(valid),
        "elapsed_seconds": round(elapsed, 1),
    }

    if valid:
        covs = [e["coverage"] for e in valid if e["coverage"] is not None]
        sharps = [e["sharpness"] for e in valid if e["sharpness"] is not None]
        cal_errs = [e["cal_err"] for e in valid if e["cal_err"] is not None]

        if covs:
            summary["coverage_mean"] = round(float(np.mean(covs)), 4)
            summary["coverage_min"] = round(float(np.min(covs)), 4)
            summary["coverage_max"] = round(float(np.max(covs)), 4)
            summary["coverage_below_90"] = sum(1 for c in covs if c < 0.90)
            summary["coverage_below_95"] = sum(1 for c in covs if c < 0.95)

        if sharps:
            summary["sharpness_mean"] = round(float(np.mean(sharps)), 4)
            summary["sharpness_median"] = round(float(np.median(sharps)), 4)
            summary["sharpness_ok_count"] = sum(1 for s in sharps if s <= 2.0)
            summary["sharpness_overwide_count"] = sum(1 for s in sharps if s > 2.0)

        if cal_errs:
            summary["cal_err_mean"] = round(float(np.mean(cal_errs)), 4)

    # Rankings
    if valid:
        # Best coverage
        by_coverage = sorted(
            [e for e in valid if e.get("coverage") is not None],
            key=lambda e: e["coverage"], reverse=True
        )
        summary["top_5_coverage"] = [
            {"key": e["key"], "coverage": e["coverage"]}
            for e in by_coverage[:5]
        ]

        # Sharpest (lowest NMPIW)
        by_sharpness = sorted(
            [e for e in valid if e.get("sharpness") is not None],
            key=lambda e: e["sharpness"]
        )
        summary["top_5_sharpest"] = [
            {"key": e["key"], "sharpness": e["sharpness"]}
            for e in by_sharpness[:5]
        ]

        # Worst (need improvement)
        by_worst = sorted(
            [e for e in valid if e.get("coverage") is not None],
            key=lambda e: e["coverage"]
        )
        summary["bottom_5_coverage"] = [
            {"key": e["key"], "coverage": e["coverage"], "sharpness": e.get("sharpness")}
            for e in by_worst[:5]
        ]

    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "summary": summary,
        "entries": leaderboard,
    }


def format_leaderboard_md(result):
    """Format leaderboard as Markdown table."""
    lines = []
    s = result["summary"]
    lines.append(f"# UQ Leaderboard — SwarmLabs")
    lines.append(f"\n_Generated: {result['generated_at']}_\n")
    lines.append(f"## Summary\n")
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Scenarios evaluated | {s['evaluated']}/{s['total_scenarios']} |")
    lines.append(f"| Mean coverage (PICP) | {s.get('coverage_mean', 'N/A')} |")
    lines.append(f"| Coverage < 0.90 | {s.get('coverage_below_90', 'N/A')} |")
    lines.append(f"| Coverage < 0.95 | {s.get('coverage_below_95', 'N/A')} |")
    lines.append(f"| Mean sharpness (NMPIW) | {s.get('sharpness_mean', 'N/A')} |")
    lines.append(f"| Sharpness ≤ 2.0 (ok) | {s.get('sharpness_ok_count', 'N/A')} |")
    lines.append(f"| Sharpness > 2.0 (overwide) | {s.get('sharpness_overwide_count', 'N/A')} |")
    lines.append(f"| Mean calibration error | {s.get('cal_err_mean', 'N/A')} |")
    lines.append(f"| Elapsed | {s['elapsed_seconds']}s |")

    # Top performers
    if s.get("top_5_coverage"):
        lines.append(f"\n## Top 5 Coverage\n")
        lines.append(f"| Rank | Key | Coverage |")
        lines.append(f"|------|-----|----------|")
        for i, e in enumerate(s["top_5_coverage"], 1):
            lines.append(f"| {i} | `{e['key']}` | {e['coverage']:.3f} |")

    if s.get("top_5_sharpest"):
        lines.append(f"\n## Top 5 Sharpest (lowest NMPIW)\n")
        lines.append(f"| Rank | Key | NMPIW |")
        lines.append(f"|------|-----|-------|")
        for i, e in enumerate(s["top_5_sharpest"], 1):
            lines.append(f"| {i} | `{e['key']}` | {e['sharpness']:.4f} |")

    if s.get("bottom_5_coverage"):
        lines.append(f"\n## Bottom 5 Coverage (needs improvement)\n")
        lines.append(f"| Key | Coverage | NMPIW |")
        lines.append(f"|-----|----------|-------|")
        for e in s["bottom_5_coverage"]:
            shar = e.get("sharpness", "N/A")
            shar_s = f"{shar:.4f}" if isinstance(shar, float) else str(shar)
            lines.append(f"| `{e['key']}` | {e['coverage']:.3f} | {shar_s} |")

    # Full table
    lines.append(f"\n## Full Leaderboard\n")
    lines.append(f"| Key | Domain | Coverage | NMPIW | Cal Err | Flagship |")
    lines.append(f"|-----|--------|----------|-------|---------|----------|")
    for e in result["entries"]:
        if e["status"] != "ok":
            continue
        cov = e.get("coverage")
        shar = e.get("sharpness")
        cal = e.get("cal_err")
        cov_s = f"{cov:.3f}" if cov is not None else "-"
        shar_s = f"{shar:.4f}" if shar is not None else "-"
        cal_s = f"{cal:.3f}" if cal is not None else "-"
        flag = "⭐" if e.get("flagship") else ""
        lines.append(f"| `{e['key']}` | {e['domain'][:20]} | {cov_s} | {shar_s} | {cal_s} | {flag} |")

    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="UQ Leaderboard")
    ap.add_argument("--top", type=int, default=None, help="Limit to top N scenarios")
    ap.add_argument("--json", action="store_true", help="JSON output only")
    ap.add_argument("--output", default=None, help="Output file path")
    args = ap.parse_args()

    result = run_leaderboard(top_n=args.top)

    out_path = args.output
    if out_path is None:
        out_path = os.path.join(REPO, "data", "uq_leaderboard.json")

    # Write JSON
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n✅ JSON written: {out_path}")

    if not args.json:
        md = format_leaderboard_md(result)
        md_path = out_path.replace(".json", ".md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md)
        print(f"✅ Markdown written: {md_path}")

        # Print summary
        s = result["summary"]
        print(f"\n{'='*60}")
        print(f"  UQ Leaderboard Summary")
        print(f"{'='*60}")
        print(f"  Scenarios: {s['evaluated']}/{s['total_scenarios']}")
        print(f"  Coverage: mean={s.get('coverage_mean')}, <0.90={s.get('coverage_below_90')}, <0.95={s.get('coverage_below_95')}")
        print(f"  Sharpness: mean={s.get('sharpness_mean')}, ok={s.get('sharpness_ok_count')}, overwide={s.get('sharpness_overwide_count')}")
        print(f"  Cal err: mean={s.get('cal_err_mean')}")
        print(f"{'='*60}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
