#!/usr/bin/env python3
"""P3-2: Backfill model_registry.json metrics from reports/index.json.

Reads the real V&V metrics (rmse_rel, r2, coverage, verdict) from
reports/index.json and writes them into model_registry.json so that
evidence_grade and metrics become non-null.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "frontend_fixed" / "reports" / "index.json"
REGISTRY = ROOT / "data" / "model_registry.json"

with REPORTS.open() as f:
    reports = json.load(f)
with REGISTRY.open() as f:
    registry = json.load(f)

scenarios_by_key = {s["key"]: s for s in reports["scenarios"]}

backfilled = 0
upgraded = 0
for model in registry["models"]:
    key = model["scenario"]
    if key not in scenarios_by_key:
        continue
    s = scenarios_by_key[key]
    m = model.setdefault("metrics", {})
    m["rmse_rel"] = s.get("rmse_rel")
    m["r2"] = s.get("r2")
    m["coverage"] = s.get("coverage")
    m["cal_err"] = s.get("cal_err")
    m["verdict"] = s.get("verdict")
    m["n_train"] = s.get("n_train")
    m["n_test"] = s.get("n_test")
    m["kernel"] = s.get("kernel")
    m["validation_type"] = s.get("validation_type")
    backfilled += 1
    if s.get("verdict") in ("PASS", "MARGINAL", "REFUTED"):
        model["evidence_grade"] = "validated"
        upgraded += 1

registry["_meta"]["backfilled_from"] = str(REPORTS.relative_to(ROOT))
registry["_meta"]["backfilled_at"] = "2026-10-24"
registry["_meta"]["backfilled_count"] = backfilled
registry["_meta"]["upgraded_to_validated"] = upgraded

with REGISTRY.open("w") as f:
    json.dump(registry, f, ensure_ascii=False, indent=2)

print(f"backfilled={backfilled} upgraded_to_validated={upgraded} total_models={len(registry['models'])}")
