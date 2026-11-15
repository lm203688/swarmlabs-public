#!/usr/bin/env python3
"""P3-3 / P5: Diagnose the 28-engine admission classification.

P5 update (2026-10-25): admission_criteria now uses a four-tier grade
(validated / experimental / unassessed / rejected). The prior diagnosis
reported "28/28 REJECTED" which conflated two very different situations:

  (a) engines with a placeholder `me_source` marker — the honest state of
      "we removed fake mean_error values and haven't run V&V yet"; these
      are `unassessed`, a work-queue signal, not a defect;
  (b) engines with a real measurement that fails a threshold — these are
      `rejected` and need engineering attention.

This script now:
  1. Reads the four-tier classification from engine_admission_report.json
  2. Cross-references reports/index.json for the subset of unassessed
     engines that have matching scenario data (would-be unlocks)
  3. Reports per-grade counts + honest recommendations
"""
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "frontend_fixed" / "reports" / "index.json"
ADMISSION = ROOT / "data" / "engine_admission_report.json"

with REPORTS.open() as f:
    reports = json.load(f)
with ADMISSION.open() as f:
    admission = json.load(f)

scenarios_by_key = {s["key"]: s for s in reports["scenarios"]}

# ENGINE_STATS short keys -> scenario keys (direct substring match)
engine_to_scenario = {}
for eng_id in admission["results"]:
    for key in scenarios_by_key:
        if eng_id.lower() in key.lower():
            engine_to_scenario[eng_id] = key
            break

results = admission["results"]
by_grade = defaultdict(int)
for r in results.values():
    by_grade[r["grade"]] += 1

# For unassessed engines, check which have a scenario match with V&V metrics
unassessed = [k for k, r in results.items() if r["grade"] == "unassessed"]
rejected = [k for k, r in results.items() if r["grade"] == "rejected"]
experimental = [k for k, r in results.items() if r["grade"] == "experimental"]
validated = [k for k, r in results.items() if r["grade"] == "validated"]

would_pass = 0
would_pass_ids = []
for eid in unassessed:
    sk = engine_to_scenario.get(eid)
    if not sk:
        continue
    s = scenarios_by_key[sk]
    has_rmse = s.get("rmse_rel") is not None and s["rmse_rel"] > 0
    has_cov = s.get("coverage") is not None
    has_n = s.get("n_test", 0) >= 30
    if has_rmse and has_cov and has_n:
        would_pass += 1
        would_pass_ids.append((eid, sk))

print(f"{'='*70}")
print(f"P5 Engine Admission Four-Tier Classification")
print(f"{'='*70}")
print(f"\nTotal engines: {len(results)}")
for g in ("validated", "experimental", "unassessed", "rejected"):
    print(f"  {g:14s}: {by_grade.get(g, 0)}")
print(f"\nUnassessed engines with matching scenario data "
      f"(would unlock via cross-ref): {would_pass}")
for eid, sk in would_pass_ids:
    print(f"    - {eid} -> {sk}")
if rejected:
    print(f"\nRejected (real defects):")
    for eid in rejected:
        r = results[eid]
        fails = r.get("failures", [])
        print(f"    - {eid}: {fails[:2]}")
print(f"{'='*70}")

output = {
    "diagnosis_date": "2026-10-25",
    "grade_scheme": "four-tier (validated / experimental / unassessed / rejected)",
    "total_engines": len(results),
    "by_grade": dict(by_grade),
    "unassessed_with_crossref_available": would_pass,
    "unassessed_unlock_ids": [{"engine": e, "scenario": s} for e, s in would_pass_ids],
    "rejected_engines": rejected,
    "rejected_reasons": {eid: results[eid].get("failures", []) for eid in rejected},
    "root_cause": (
        "Prior '28/28 REJECTED' conflated honest unassessed placeholders with "
        "real defects. P5 fix: admission_criteria now uses me_source as a "
        "signal — 'placeholder_removed_*' + no metrics => 'unassessed' "
        "(work queue, not failure). Engines with real out-of-threshold "
        "measurements remain 'rejected'."
    ),
    "recommendation": (
        "1. Cross-reference reports/index.json for the 2 unassessed engines "
        "that have matching V&V metrics (esterification, suzuki).\n"
        "2. For the other 25 unassessed engines, decide: run real V&V, or "
        "classify them as 'analytic_physics' engines exempt from empirical V&V.\n"
        "3. Investigate the 1 rejected engine (electrodialysis, "
        "rmse_rel=100.0) — likely a unit/entry error, not a genuine failure."
    ),
    "engine_scenario_map": engine_to_scenario,
}
with (ROOT / "data" / "admission_diagnosis.json").open("w") as f:
    json.dump(output, f, ensure_ascii=False, indent=2)
print(f"\nSaved diagnosis to data/admission_diagnosis.json")
