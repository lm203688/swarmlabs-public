"""SwarmLabs trajectory recorder.

Every work order that flows through ``runner.run_work_order`` produces a
trajectory record — a JSON file capturing the exact input, the six-stage
pipeline stages, the verdict, screening counts, timing, and machine
metadata. This is the "audit tape" that turns every run into a
reconstructible event, not just a boolean outcome.

Storage
-------
- ``data/trajectories/<wo_id>.json`` — per-run snapshot
- ``data/trajectories/log.jsonl`` — append-only log of all runs (last-write-wins
  per run but preserves ordering; new runs always append one line)

Design rules
------------
- Never mutate the job dict. Record it as-is plus timing envelope.
- Trajectory writes are best-effort: if the disk write fails, the run
  still succeeds (the audit log is a supplement, not a gate).
- Every run is idempotent — re-running the same work order overwrites its
  snapshot but appends a new line to the log.
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


SCHEMA = "svp.trajectory/v0.1"


def repo_root() -> Path:
    from swarmlabs.rebuild import repo_root
    return repo_root()


def traj_dir() -> Path:
    d = repo_root() / "data" / "trajectories"
    d.mkdir(parents=True, exist_ok=True)
    return d


def log_path() -> Path:
    return traj_dir() / "log.jsonl"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _sha(obj: Any) -> str:
    b = json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(b).hexdigest()[:16]


def record_trajectory(work_order: Dict[str, Any],
                      job: Dict[str, Any],
                      started_at: float,
                      extra_meta: Optional[Dict[str, Any]] = None,
                      force_out_dir: Optional[str] = None) -> Optional[Path]:
    """Write a trajectory record. Returns the path, or None on failure.

    Failures here are logged to stderr but do NOT raise — a trajectory
    failure must never fail the run.
    """
    import sys

    try:
        wo_id = (work_order.get("work_order_id")
                 or job.get("work_order_id")
                 or f"unnamed_{int(time.time()*1000)}")
        wo_id_safe = "".join(c if c.isalnum() or c in "-_." else "_"
                             for c in str(wo_id))

        now = time.time()
        stages = job.get("stages") or []
        stage_entries = []
        for i, s in enumerate(stages):
            prev = now if i == 0 else None
            # at_utc is already in the stage; keep it verbatim
            entry = {
                "index": i,
                "stage": s.get("stage") or s.get("name") or f"stage_{i}",
                "ok": s.get("ok"),
                "at_utc": s.get("at_utc"),
                "errors": s.get("errors") or [],
                "warnings": s.get("warnings") or [],
            }
            stage_entries.append(entry)

        trajectory = {
            "schema": SCHEMA,
            "recorded_at": _now(),
            "recorded_at_ms": int(now * 1000),
            "duration_ms": round((now - started_at) * 1000, 2),
            "work_order_id": wo_id,
            "work_order_sha": _sha(work_order),
            "job_sha": _sha(job),
            "outcome": job.get("outcome"),
            "verdict": job.get("verdict"),
            "screening": job.get("screening"),
            "scope": job.get("scope"),
            "work_order_summary": job.get("work_order_summary"),
            "stages": stage_entries,
            "stage_count": len(stage_entries),
            "python": None,
            "meta": extra_meta or {},
        }
        try:
            import sys as _s
            trajectory["python"] = _s.version.split()[0]
        except Exception:
            pass  # noqa: silent-ok — Exception 可安全忽略（已审计 2026-10-08）

        # write the snapshot
        out_dir = force_out_dir or str(traj_dir())
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        snapshot = Path(out_dir) / f"{wo_id_safe}.json"
        snapshot.write_text(
            json.dumps(trajectory, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

        # append to log
        log_line = {
            "recorded_at": trajectory["recorded_at"],
            "work_order_id": wo_id,
            "outcome": trajectory["outcome"],
            "verdict": (trajectory["verdict"] or {}).get("hard_verdict"),
            "duration_ms": trajectory["duration_ms"],
            "snapshot": snapshot.name,
        }
        with log_path().open("a", encoding="utf-8") as f:
            f.write(json.dumps(log_line, ensure_ascii=False, default=str) + "\n")

        return snapshot
    except Exception as e:
        import sys as _sys
        print(f"[trajectory] record failed: {type(e).__name__}: {e}",
              file=_sys.stderr)
        return None


def read_trajectory(wo_id: str) -> Optional[Dict[str, Any]]:
    p = traj_dir() / f"{wo_id}.json"
    if not p.exists():
        # Try sanitizing the id
        wo_id_safe = "".join(c if c.isalnum() or c in "-_." else "_"
                             for c in str(wo_id))
        p = traj_dir() / f"{wo_id_safe}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def list_trajectories(limit: int = 20) -> List[Dict[str, Any]]:
    p = log_path()
    if not p.exists():
        return []
    lines = p.read_text(encoding="utf-8").splitlines()
    entries = []
    for ln in reversed(lines):
        ln = ln.strip()
        if not ln:
            continue
        try:
            entries.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
        if len(entries) >= limit:
            break
    return entries


def clear_trajectories() -> int:
    """Delete all trajectory snapshots and the log. Returns count removed."""
    d = traj_dir()
    if not d.exists():
        return 0
    removed = 0
    for f in d.glob("*.json"):
        f.unlink()
        removed += 1
    if log_path().exists():
        log_path().unlink()
        removed += 1
    return removed


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    import argparse, json as _json
    p = argparse.ArgumentParser(prog="swarmlabs trajectory",
                                description="Show run trajectories.")
    sub = p.add_subparsers(dest="op", required=True)

    sp = sub.add_parser("list", help="list recent runs (log tail)")
    sp.add_argument("--limit", type=int, default=20)
    sp.add_argument("--json", action="store_true")

    sp = sub.add_parser("show", help="show one trajectory snapshot")
    sp.add_argument("work_order_id")
    sp.add_argument("--json", action="store_true")

    sp = sub.add_parser("clear", help="delete all trajectories (needs --yes)")
    sp.add_argument("--yes", action="store_true")

    args = p.parse_args(argv)

    if args.op == "list":
        entries = list_trajectories(limit=args.limit)
        if args.json:
            print(_json.dumps(entries, ensure_ascii=False, indent=2))
        else:
            print(f"recent runs: {len(entries)}")
            for e in entries:
                v = e.get("verdict") or "?"
                o = e.get("outcome") or "?"
                print(f"  {e.get('recorded_at','')}  {e.get('work_order_id','?'):<28}  "
                      f"verdict={v:<12}  outcome={o:<28}  {e.get('duration_ms',0):>8}ms")
        return 0

    if args.op == "show":
        t = read_trajectory(args.work_order_id)
        if not t:
            print(f"error: no trajectory for {args.work_order_id}",
                  file=__import__('sys').stderr)
            return 1
        print(_json.dumps(t, ensure_ascii=False, indent=2, default=str))
        return 0

    if args.op == "clear":
        if not args.yes:
            print("error: refuse to clear without --yes",
                  file=__import__('sys').stderr)
            return 2
        n = clear_trajectories()
        print(f"cleared {n} trajectory records")
        return 0

    return 0
