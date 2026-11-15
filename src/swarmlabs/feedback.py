"""SwarmLabs feedback loop — harness_evolver wired into the CLI.

Design
------
- **State** lives on disk under ``data/feedback/`` so the harness
  persists across CLI invocations.
- **Baseline harness** is built from ``engine.harness_registry`` — the same
  11 scenario-level gates that CI uses. This keeps the evolver aligned
  with production rather than a synthetic baseline.
- **Scenario set** for pair-evaluation comes from ``engine.conformal.load_scenarios()``
  (the ~10 trainable scenarios). More scenarios means more stable ΔS, but
  each evaluation is a full surrogate fit — kept small for speed.
- **Feedback** is stored as a JSON file per submission (``fb_<ts>.json``).
- **Audit** is a single append-only JSONL (``audit.jsonl``).
- The CLI never mutates ``engine/`` — it only records what happened and
  whether a candidate change would have been accepted. Persisting the
  new gate set back to disk is explicitly ``--promote`` (fail-closed by
  default).

Commands map to CLI subcommands:
    swarmlabs feedback submit --file fb.json
    swarmlabs feedback submit --inline '{"fb_id": ..., ...}'
    swarmlabs feedback state
    swarmlabs feedback audit [--tail 20]
    swarmlabs feedback list [--limit 20]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from swarmlabs import runner as _runner
from swarmlabs.rebuild import repo_root as _rr


def feedback_dir() -> Path:
    d = _rr() / "data" / "feedback"
    d.mkdir(parents=True, exist_ok=True)
    return d


def audit_path() -> Path:
    return feedback_dir() / "audit.jsonl"


def state_path() -> Path:
    return feedback_dir() / "state.json"


def _load_baseline_harness():
    """Build a baseline Harness from the 11 registered scenario gates."""
    from engine.harness_evolver import Harness, Gate as EvolverGate
    from engine.harness_registry import all_gates as reg_gates

    evolver_gates: List[EvolverGate] = []
    for g in reg_gates():
        # adapter: harness_registry.Gate → harness_evolver.Gate
        evolver_gates.append(EvolverGate(
            id=g.id,
            category=g.category,
            description=g.description,
            predicate=g.predicate,
            negation=g.negation,
        ))
    return Harness(version="v0.0", gates=evolver_gates)


def _scenario_loader():
    """Return a scenario loader bound to the real trainable scenarios."""
    from engine import conformal
    return conformal.load_scenarios


def _persist_state(payload: Dict[str, Any]) -> None:
    state_path().write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _append_audit(entry: Dict[str, Any]) -> None:
    with audit_path().open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")


def submit(feedback: Dict[str, Any],
           promote: bool = False,
           remove_gate_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    """Submit one feedback + optionally a candidate change.

    Returns:
      - accepted: bool — did the pair-eval accept the change?
      - decision: harness_evolver.Decision as dict
      - step: EvolutionStep as dict
      - state: full evolver state after the operation
      - persisted_to: path of the saved feedback file
    """
    from engine.harness_evolver import (
        Feedback, HarnessEvolver, decide, apply_candidate, Candidate,
        evaluate_harness,
    )

    if "fb_id" not in feedback:
        feedback["fb_id"] = f"fb_{int(time.time()*1000)}"
    if "ts" not in feedback:
        feedback["ts"] = int(time.time())

    fb = Feedback(
        fb_id=feedback["fb_id"],
        feedback_type=feedback.get("feedback_type", "gap"),
        scenario_id=feedback.get("scenario_id", ""),
        message=feedback.get("message", ""),
        suggestion=feedback.get("suggestion", ""),
        ts=feedback["ts"],
    )

    # persist the raw feedback first (audit trail)
    fb_path = feedback_dir() / f"{fb.fb_id}.json"
    fb_path.write_text(
        json.dumps(feedback, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    baseline = _load_baseline_harness()
    loader = _scenario_loader()
    evolver = HarnessEvolver(baseline=baseline, scenario_loader=loader, min_delta=0.0)

    # If the user proposed a removal, build a candidate via apply_candidate
    proposed_gate = None
    candidate = None
    if remove_gate_ids:
        from engine.harness_evolver import Gate as EG
        candidate = Candidate(
            base_version=evolver.current.version,
            target_version=f"candidate_{fb.fb_id}",
            feedback_ids=[fb.fb_id],
            added=[],
            removed=remove_gate_ids,
            rationale=fb.message,
        )

    accepted, decision = evolver.submit_feedback(
        feedback=fb,
        proposed_gate=proposed_gate,
        remove_gate_ids=remove_gate_ids,
    )

    step = evolver.audit[-1] if evolver.audit else None

    entry = {
        "fb_id": fb.fb_id,
        "accepted": accepted,
        "decision": decision.to_dict() if hasattr(decision, "to_dict") else {
            "accepted": decision.accepted,
            "reason": getattr(decision, "reason", ""),
        },
        "step": step.to_dict() if step else None,
        "state": evolver.state(),
        "persisted_to": str(fb_path),
        "promote": promote,
        "remove_gate_ids": remove_gate_ids or [],
        "ts": int(time.time()),
    }
    _append_audit(entry)
    _persist_state(evolver.state())
    return entry


def current_state() -> Dict[str, Any]:
    p = state_path()
    if not p.exists():
        return {
            "schema": "svp.harness.state/v0.1",
            "note": "no state recorded yet — run `swarmlabs feedback submit` first",
        }
    return json.loads(p.read_text(encoding="utf-8"))


def audit_log(tail: int = 20) -> List[Dict[str, Any]]:
    p = audit_path()
    if not p.exists():
        return []
    lines = p.read_text(encoding="utf-8").splitlines()
    entries = []
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            entries.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return entries[-tail:] if tail > 0 else entries


def list_feedback(limit: int = 20) -> List[Dict[str, Any]]:
    """Return persisted feedback files, newest first."""
    d = feedback_dir()
    files = sorted(d.glob("fb_*.json"), key=lambda p: p.stat().st_mtime,
                    reverse=True)
    out = []
    for f in files[:limit]:
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception as e:
            out.append({"file": str(f), "error": str(e)})
    return out


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="swarmlabs feedback",
                                description="Submit user feedback → pair-eval → audit.")
    sub = p.add_subparsers(dest="op", required=True)

    sp = sub.add_parser("submit", help="submit one feedback (+ optional candidate change)")
    sp.add_argument("--file", default=None, help="path to a feedback JSON")
    sp.add_argument("--inline", default=None, help="inline JSON string")
    sp.add_argument("--remove-gate", action="append", default=[],
                    help="gate ID to propose removing (repeatable)")
    sp.add_argument("--promote", action="store_true",
                    help="record the accepted change as a promotion request "
                         "(audit only; does not mutate engine/)")

    sp = sub.add_parser("state", help="print current evolver state")
    sp.add_argument("--json", action="store_true")

    sp = sub.add_parser("audit", help="show audit log")
    sp.add_argument("--tail", type=int, default=20)

    sp = sub.add_parser("list", help="list persisted feedback files")
    sp.add_argument("--limit", type=int, default=20)

    args = p.parse_args(argv)

    if args.op == "submit":
        if not args.file and not args.inline:
            print("error: pass --file or --inline", file=sys.stderr)
            return 2
        if args.file:
            fb = json.loads(Path(args.file).read_text(encoding="utf-8"))
        else:
            fb = json.loads(args.inline)
        entry = submit(fb, promote=args.promote, remove_gate_ids=args.remove_gate or None)
        print(json.dumps({
            "fb_id": entry["fb_id"],
            "accepted": entry["accepted"],
            "reason": (entry.get("decision") or {}).get("reason", ""),
            "state": entry.get("state"),
            "persisted_to": entry.get("persisted_to"),
        }, ensure_ascii=False, indent=2))
        return 0

    if args.op == "state":
        s = current_state()
        print(json.dumps(s, ensure_ascii=False, indent=2, default=str))
        return 0

    if args.op == "audit":
        entries = audit_log(tail=args.tail)
        print(f"audit entries shown: {len(entries)}")
        for e in entries:
            print(f"  [{e.get('ts')}] fb={e.get('fb_id')} "
                  f"accepted={e.get('accepted')} "
                  f"reason={(e.get('decision') or {}).get('reason','')[:70]}")
        return 0

    if args.op == "list":
        fbs = list_feedback(limit=args.limit)
        print(f"feedback entries: {len(fbs)}")
        for f in fbs:
            print(f"  {f.get('fb_id','?')}  [{f.get('feedback_type','?')}]  "
                  f"{(f.get('message','') or '')[:80]}")
        return 0

    return 0
