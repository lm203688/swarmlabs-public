#!/usr/bin/env python3
"""scripts/validate_jsonschemas.py — Validate the three core data artifacts against their JSON Schema.

Report §P0-C (2026-10-16 late) called out:
  「为 trust_surface.json / real_anchors.json / gate_ledger.json 写 jsonschema，
   入 CI（一个以"契约化"为卖点的项目，数据契约不能靠人眼）」

This script is the CI-facing validator. It:
  1. Loads each schema from schemas/*.schema.json (JSON Schema Draft-07).
  2. Loads each corresponding artifact from data/*.json.
  3. Runs Draft7Validator.check_schema() — schema itself must be well-formed.
  4. Runs Draft7Validator(schema).iter_errors(instance) — instance must conform.
  5. Exit 0 if all pass, 1 with a machine-readable summary otherwise.

Complementing scripts/check_data_contracts.py:
  - check_data_contracts.py: project-specific invariants (counts, architecture-inversion).
  - validate_jsonschemas.py: structural / type conformance per JSON Schema standard.
  Both are complementary — one is domain-specific, the other is standards-based.

Usage:
    python scripts/validate_jsonschemas.py              # human-readable
    python scripts/validate_jsonschemas.py --json       # JSON summary

Stdlib + jsonschema only. No local paths, no side effects.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

try:
    from jsonschema import Draft7Validator
except ImportError:  # pragma: no cover
    print(
        "jsonschema package not installed. "
        "Install with: pip install jsonschema>=4.0",
        file=sys.stderr,
    )
    sys.exit(2)


ROOT = Path(__file__).resolve().parent.parent
SCHEMAS_DIR = ROOT / "schemas"
DATA_DIR = ROOT / "data"
REPORTS_DIR = ROOT / "frontend_fixed" / "reports"

# (schema_file, artifact_path)
# 2026-10-17 L-11 补齐：public_facts / heldout_index / wet_lab_anchors ——
# 三者都是对外披露相关的口径源头（101 vs 100、73/82、19 锚点），
# 此前 schemas/ 只覆盖 3/124 个 JSON，契约保护空白正是 L-01 真源冲突的直接后果。
#
# 2026-10-26 PR-07 补齐：BatchRecord / ExperimentIntent / EvidenceManifest 三 schema
# 尚无生产 artifact，先以 schemas/examples/*.example.json 作为契约自检载体。
# 之后任何真实 CSV 入库都会以这三份 example 为参考模板。
EXAMPLES_DIR = SCHEMAS_DIR / "examples"

ARTIFACTS = [
    ("trust_surface.schema.json", DATA_DIR / "trust_surface.json"),
    ("real_anchors.schema.json", DATA_DIR / "real_anchors.json"),
    ("gate_ledger.schema.json", DATA_DIR / "gate_ledger.json"),
    ("public_facts.schema.json", DATA_DIR / "public_facts.json"),
    ("heldout_index.schema.json", REPORTS_DIR / "heldout_index.json"),
    ("wet_lab_anchors.schema.json", REPORTS_DIR / "wet_lab_anchors.json"),
    ("batch_record.schema.json", EXAMPLES_DIR / "batch_record.example.json"),
    ("experiment_intent.schema.json", EXAMPLES_DIR / "experiment_intent.example.json"),
    ("evidence_manifest.schema.json", EXAMPLES_DIR / "evidence_manifest.example.json"),
]


def _load_json(path: Path) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _validate_one(schema_path: Path, artifact_path: Path) -> Tuple[bool, List[dict]]:
    """Validate one (schema, artifact) pair. Returns (ok, list_of_errors)."""
    errors: List[dict] = []

    if not schema_path.exists():
        return (False, [{"file": str(schema_path), "error": "SCHEMA-NOT-FOUND"}])
    if not artifact_path.exists():
        return (False, [{"file": str(artifact_path), "error": "ARTIFACT-NOT-FOUND"}])

    try:
        schema = _load_json(schema_path)
        instance = _load_json(artifact_path)
    except json.JSONDecodeError as e:
        return (False, [{"file": str(schema_path), "error": f"JSON-PARSE: {e}"}])

    # 1. Schema itself must be well-formed
    try:
        Draft7Validator.check_schema(schema)
    except Exception as e:  # SchemaError
        return (False, [{"file": str(schema_path), "error": f"SCHEMA-INVALID: {e}"}])

    # 2. Instance must conform
    validator = Draft7Validator(schema)
    all_errors = sorted(
        validator.iter_errors(instance),
        key=lambda e: (list(e.absolute_path), e.message[:60]),
    )
    cap = 20
    for err in all_errors[:cap]:
        json_path = "$" + "".join(
            f"[{p!r}]" if isinstance(p, int) else f".{p}"
            for p in err.absolute_path
        )
        errors.append({
            "schema": schema_path.name,
            "artifact": artifact_path.name,
            "json_path": json_path,
            "type": err.validator,
            "message": err.message[:300],
        })
    if len(all_errors) > cap:
        errors.append({
            "schema": schema_path.name,
            "artifact": artifact_path.name,
            "note": f"... truncated ({len(all_errors) - cap} more errors)",
        })

    return (len(all_errors) == 0, errors)


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="Emit JSON summary")
    args = ap.parse_args(argv)

    results = []
    overall_ok = True
    for schema_name, artifact_path in ARTIFACTS:
        ok, errors = _validate_one(SCHEMAS_DIR / schema_name, artifact_path)
        results.append({
            "schema": schema_name,
            "artifact": str(artifact_path.relative_to(ROOT)) if hasattr(artifact_path, "relative_to") else str(artifact_path),
            "ok": ok,
            "errors": errors,
        })
        if not ok:
            overall_ok = False

    summary = {
        "overall_ok": overall_ok,
        "artifacts_checked": len(ARTIFACTS),
        "artifacts_passed": sum(1 for r in results if r["ok"]),
        "artifacts_failed": sum(1 for r in results if not r["ok"]),
        "total_errors": sum(len(r["errors"]) for r in results),
        "results": results,
    }

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if overall_ok else 1

    # Human-readable
    print(f"[validate_jsonschemas] checking {len(ARTIFACTS)} artifacts against JSON Schema Draft-07")
    print(f"  schemas dir: {SCHEMAS_DIR}")
    print(f"  data dir:    {DATA_DIR}")
    print()

    for r in results:
        status = "OK" if r["ok"] else "FAIL"
        err_count = len(r["errors"])
        print(f"  [{status}] {r['schema']} vs {r['artifact']}" + (f" ({err_count} errors)" if err_count else ""))
        for e in r["errors"]:
            if "note" in e:
                print(f"        {e['note']}")
                continue
            print(f"        - {e.get('json_path', '?')}: {e.get('type', '?')} → {e.get('message', '')[:120]}")

    print()
    if overall_ok:
        print(f"[OK] All {len(ARTIFACTS)} artifacts conform to their schemas.")
        return 0
    else:
        print(f"[FAIL] {summary['artifacts_failed']}/{len(ARTIFACTS)} artifacts failed. Total errors: {summary['total_errors']}")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
