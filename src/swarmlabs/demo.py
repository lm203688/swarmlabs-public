"""SwarmLabs end-to-end demo.

Runs a real work order through the full VLab pipeline and prints a
human-readable report. Every number shown is loaded live from
``engine/vlab_orchestrator.py``, ``engine/wet_lab_anchors.py``, and
``engine/conformal_vv.py`` — nothing is hard-coded.

Usage:
    swarmlabs demo                                    # default work order
    swarmlabs demo --work-order data/vlab/work-orders/w3-microbio-ecoli.json
    swarmlabs demo --json                             # machine-readable output
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict

from swarmlabs import runner


DEFAULT_WO_PATH = "data/vlab/work-orders/w3-microbio-ecoli.json"


def _banner(title: str, width: int = 72) -> str:
    line = "─" * width
    return f"\n{line}\n  {title}\n{line}"


def _table(rows: list[tuple[str, Any]], col_w: int = 24) -> str:
    """Fixed-width key-value table."""
    lines = []
    for k, v in rows:
        v_str = json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else str(v)
        lines.append(f"  {k:<{col_w}} {v_str}")
    return "\n".join(lines)


def run_demo(work_order_path: str, as_json: bool = False) -> Dict[str, Any]:
    """Run the demo and return the assembled report dict."""
    wo_path = Path(work_order_path)
    if not wo_path.exists():
        raise FileNotFoundError(f"work order not found: {wo_path}")

    wo = runner.load_work_order(str(wo_path))
    job = runner.run_work_order(wo, out_dir=None)

    # --- Enrich with global context ---
    ctx: Dict[str, Any] = {}
    try:
        ctx["scenarios"] = runner.list_scenarios(limit=0)
    except Exception as e:
        ctx["scenarios_error"] = str(e)
    try:
        ctx["gates"] = runner.list_gates()
    except Exception as e:
        ctx["gates_error"] = str(e)
    try:
        ctx["anchors"] = runner.list_anchors()
    except Exception as e:
        ctx["anchors_error"] = str(e)

    return {
        "work_order_path": str(wo_path),
        "work_order_summary": wo,
        "job": job,
        "context": ctx,
    }


def print_report(report: Dict[str, Any]) -> None:
    """Human-readable report."""
    wo = report["work_order_summary"]
    job = report["job"]
    ctx = report["context"]

    print(_banner("SwarmLabs End-to-End Demo"))
    print(f"  Work order : {report['work_order_path']}")
    print(f"  Discipline : {wo.get('discipline', '?')}")
    print(f"  Goal       : {wo.get('goal', {}).get('quantity', '?')}"
          f" ({wo.get('goal', {}).get('unit', '?')})")
    print(f"  Scenario   : {wo.get('scenario_key', '?')}")

    # --- Stages ---
    print(_banner("Pipeline (6 stages)"))
    for stage in job.get("stages", []):
        name = stage.get("stage") or stage.get("name")
        ok = stage.get("ok")
        marker = "✓" if ok else "✗"
        extra = ""
        if not ok:
            err = stage.get("errors") or stage.get("reason") or ""
            if err:
                extra = f"  ← {err}"
        print(f"  [{marker}] {name:<15}{extra}")

    # --- Verdict ---
    print(_banner("Verdict"))
    verdict = job.get("verdict") or {}
    outcome = job.get("outcome")
    if verdict:
        print(_table([
            ("outcome", outcome),
            ("hard_verdict", verdict.get("hard_verdict")),
            ("gate", verdict.get("gate")),
            ("reason", verdict.get("reason")),
        ]))
    else:
        print(f"  outcome={outcome}  verdict=None (rejected at intake)")

    # --- Screening ---
    print(_banner("Corroboration Screening"))
    screening = job.get("screening") or {}
    counts = screening.get("counts") or {}
    print(_table([
        ("counts", counts),
        ("verdict_before", screening.get("verdict_before")),
        ("verdict_after", screening.get("verdict_after")),
        ("parity", screening.get("parity")),
    ]))

    # --- Context ---
    print(_banner("Engine Context"))
    scenarios = ctx.get("scenarios") or {}
    gates = ctx.get("gates") or {}
    anchors = ctx.get("anchors") or {}
    print(_table([
        ("scenarios (registry)", scenarios.get("registry_total")),
        ("scenarios (trainable)", scenarios.get("trainable")),
        ("verdict counts", scenarios.get("verdict_counts")),
        ("gates registered", gates.get("count")),
        ("anchors", anchors.get("total")),
        ("3D coverage", anchors.get("coverage_3d")),
    ]))

    # --- Bottom line ---
    print(_banner("Bottom line"))
    if outcome == "STALE_LEDGER_FAIL_CLOSED":
        print("  Ledger stale → fail-closed is CORRECT behavior.")
        print("  Run `python scripts/build_gate_ledger.py` (or `make rebuild`)")
        print("  to refresh before re-running.")
    elif outcome == "NEEDS_ONBOARDING":
        print("  Discipline not onboarded — no verdict issued.")
        print("  See onboarding_checklist for required fields.")
    elif outcome == "REJECTED_AT_INTAKE":
        print("  Work order rejected at intake. Fix schema and retry.")
    elif verdict:
        print(f"  → {verdict.get('hard_verdict')} "
              f"({verdict.get('gate')})")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="swarmlabs demo",
        description="End-to-end demo: work order → 6-stage pipeline → V&V verdict",
    )
    parser.add_argument("--work-order", default=DEFAULT_WO_PATH,
                        help=f"path to work order JSON (default: {DEFAULT_WO_PATH})")
    parser.add_argument("--json", action="store_true",
                        help="emit JSON output instead of human report")
    args = parser.parse_args(argv)

    try:
        report = run_demo(args.work_order, as_json=args.json)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    if args.json:
        # Strip bulky context for machine output
        slim = {
            "work_order_path": report["work_order_path"],
            "job": report["job"],
        }
        print(json.dumps(slim, ensure_ascii=False, indent=2))
    else:
        print_report(report)
    return 0
