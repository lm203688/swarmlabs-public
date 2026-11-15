"""Swarmlabs CLI — unified entrypoint.

Console script:
    swarmlabs demo | run | scenarios | gates | anchors | confidence
             | check | doctor | serve | mcp | version
             | rebuild | feedback | trajectory | workbench

The engine modules under ``engine/`` remain the single source of truth.
This module wires them into user-facing commands via ``swarmlabs.runner``.

Exit codes:
    0 = success
    1 = runtime error / gate failure
    2 = usage error (bad arguments, missing files)
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from swarmlabs import __version__, demo as demo_mod, feedback as feedback_mod, runner


# ---------------------------------------------------------------------------
# Command handlers — each returns an int exit code and prints to stdout
# ---------------------------------------------------------------------------

def _print_json(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def cmd_demo(args) -> int:
    """End-to-end demo."""
    flags = []
    if args.json:
        flags.append("--json")
    return demo_mod.main(["--work-order", args.work_order] + flags)


def cmd_run(args) -> int:
    """Run a work order and print the job dict."""
    wo = runner.load_work_order(args.work_order)
    job = runner.run_work_order(wo, out_dir=args.out_dir,
                                record_trajectory=not args.no_trajectory)
    if args.json:
        _print_json(job)
    else:
        print(f"outcome : {job.get('outcome')}")
        v = job.get("verdict") or {}
        print(f"verdict : {v.get('hard_verdict')}  gate={v.get('gate')}")
        print(f"reason  : {v.get('reason')}")
        s = job.get("screening") or {}
        print(f"screen  : {s.get('counts')}")
        if job.get("_trajectory"):
            print(f"traj    : {job['_trajectory']}")
    return 0


def cmd_scenarios(args) -> int:
    """List scenarios."""
    res = runner.list_scenarios(limit=args.limit)
    if args.json:
        _print_json(res)
    else:
        print(f"Registry total : {res.get('registry_total')} "
              f"(build_complete={res.get('build_complete')})")
        print(f"Verdict counts : {res.get('verdict_counts')}")
        print(f"Trainable      : {res.get('trainable')} (with train/test data)")
        print("By domain (trainable):")
        for dom, cnt in res.get("by_domain", {}).items():
            print(f"  {dom:<60} {cnt}")
        if args.limit and args.limit <= 20:
            print("\nSample keys:")
            for k in res.get("keys", []):
                print(f"  - {k}")
    return 0


def cmd_gates(args) -> int:
    """List registered gates."""
    res = runner.list_gates()
    if args.json:
        _print_json(res)
    else:
        print(f"Total gates: {res['count']}")
        print(f"Summary    : {res.get('summary')}")
        for g in res.get("gates", []):
            mark = "!" if g["negation"] else " "
            print(f"  [{mark}] {g['id']:<6} ({g['category']:<10}) {g['description'][:60]}")
    return 0


def cmd_anchors(args) -> int:
    """List wet-lab anchors."""
    res = runner.list_anchors(filter_discipline=args.discipline)
    if args.json:
        _print_json(res)
    else:
        cov = res.get("coverage_3d") or {}
        print(f"Total anchors : {res['total']}")
        print(f"3D coverage   : {cov}")
        for a in res.get("anchors", [])[:25]:
            key = a.get("key") or a.get("label") or "?"
            disc = a.get("discipline", "?")
            dr = "DR" if a.get("decision_relevant") else "  "
            three_d = "3D" if all(a.get(k) for k in
                                  ("experiment_system", "version", "operating_condition")) else "1D"
            label = (a.get("label") or "")[:50]
            print(f"  [{dr}][{three_d}] {key:<26} [{disc:<10}] {label}")
    return 0


def cmd_confidence(args) -> int:
    """Compute confidence profile."""
    res = runner.confidence_profile(scenario_key=args.scenario)
    _print_json(res)
    return 0


def cmd_check(args) -> int:
    """Run the full check battery."""
    res = runner.run_checks(verbose=args.verbose)
    print(f"{'PASS' if res['ok'] else 'FAIL'}: "
          f"{res['checks_passed']}/{res['checks_run']} checks passed")
    for r in res["results"]:
        mark = "✓" if r["ok"] else "✗"
        print(f"  [{mark}] {r['check']:<20} rc={r.get('returncode', '?')}")
    if args.verbose:
        for r in res["results"]:
            if not r["ok"]:
                print(f"\n--- {r['check']} stderr ---")
                print(r.get("stderr", ""))
    return 0 if res["ok"] else 1


def cmd_doctor(args) -> int:
    """Environment self-check."""
    info = runner.doctor()
    _print_json(info)
    return 0


def cmd_version(args) -> int:
    """Print version."""
    print(f"swarmlabs {__version__}")
    return 0


def cmd_rebuild(args) -> int:
    """One-shot rebuild of all artifacts (fail-closed)."""
    from swarmlabs import rebuild as _r
    result = _r.run(
        rebuild_root=args.root,
        allow_shrink=args.allow_shrink,
        dry_run=args.dry_run,
        verbose=args.verbose,
    )
    if args.json:
        _print_json(result)
    else:
        for step in result["steps"]:
            mark = "✓" if step["ok"] else "✗"
            extra = step.get("elapsed", "")
            if extra:
                extra = f" {extra}s"
            if step.get("error"):
                extra = f" {step['error']}"
            print(f"  [{mark}] {step['id']:<20} rc={step.get('rc','-')}{extra}")
        print()
        print(f"before_total: {result['before_total']}")
        print(f"after_total : {result['after_total']}")
        print(f"outcome     : {result['outcome']}")
        print(f"elapsed     : {result['elapsed_total']}s")
        if not result["ok"]:
            if not result["shrink_ok"]:
                print("HINT: rebuild shrank the scenario set. Refusing to publish "
                      "without --allow-shrink (see memory: 09-23 rdkit incident).")
    return 0 if result["ok"] else 1


def cmd_feedback(args) -> int:
    """Delegate to the feedback CLI."""
    return feedback_mod.main(args.sub_args)


def cmd_trajectory(args) -> int:
    """Delegate to the trajectory CLI."""
    from swarmlabs import trajectory as _traj
    return _traj.main(args.sub_args)


def cmd_workbench(args) -> int:
    """Start the local Workbench (stdlib HTTP backend + single-file HTML frontend)."""
    from swarmlabs import workbench as _wb
    return _wb.run(host=args.host, port=args.port, open_browser=args.open)


def cmd_serve(args) -> int:
    """Start the v3 API HTTP server (delegates to engine/serve.py)."""
    print(f"Starting SwarmLabs v3 API on {args.host}:{args.port}...", file=sys.stderr)
    return runner._import("_").__name__ or _run_script("engine/serve.py",
                                                      ["--host", args.host,
                                                       "--port", str(args.port)])


def cmd_mcp(args) -> int:
    """Start the MCP server (delegates to engine-kit/vv_gate_server.py)."""
    return _run_script("engine-kit/vv_gate_server.py",
                       ["--selftest"] if args.selftest else [])


def _run_script(relpath: str, extra_args: List[str]) -> int:
    """Run a sibling script via subprocess."""
    import subprocess
    script = runner.repo_root() / relpath
    if not script.exists():
        print(f"error: script not found: {script}", file=sys.stderr)
        return 2
    cmd = [sys.executable, str(script)] + extra_args
    try:
        return subprocess.call(cmd, cwd=str(script.parent.parent))
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="swarmlabs",
        description="SwarmLabs: AI4S verification layer CLI",
    )
    p.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="print version and exit",
    )
    sub = p.add_subparsers(dest="cmd", metavar="COMMAND")

    sp = sub.add_parser("demo", help="end-to-end demo")
    sp.add_argument("--work-order", default="data/vlab/work-orders/w3-microbio-ecoli.json")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_demo)

    sp = sub.add_parser("run", help="run a work order")
    sp.add_argument("work_order", help="path to work order JSON")
    sp.add_argument("--out-dir", default=None)
    sp.add_argument("--no-trajectory", action="store_true",
                    help="skip writing a trajectory snapshot")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("scenarios", help="list scenarios")
    sp.add_argument("--limit", type=int, default=0)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_scenarios)

    sp = sub.add_parser("gates", help="list gates")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_gates)

    sp = sub.add_parser("anchors", help="list wet-lab anchors")
    sp.add_argument("--discipline", default=None)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_anchors)

    sp = sub.add_parser("confidence", help="compute confidence profile")
    sp.add_argument("--scenario", default=None)
    sp.set_defaults(func=cmd_confidence)

    sp = sub.add_parser("check", help="run the full check battery")
    sp.add_argument("--verbose", action="store_true")
    sp.set_defaults(func=cmd_check)

    sp = sub.add_parser("doctor", help="environment self-check")
    sp.set_defaults(func=cmd_doctor)

    sp = sub.add_parser("version", help="print version")
    sp.set_defaults(func=cmd_version)

    sp = sub.add_parser("serve", help="start the v3 API server")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8787)
    sp.set_defaults(func=cmd_serve)

    sp = sub.add_parser("mcp", help="start the MCP server")
    sp.add_argument("--selftest", action="store_true")
    sp.set_defaults(func=cmd_mcp)

    sp = sub.add_parser("rebuild", help="one-shot rebuild of all artifacts")
    sp.add_argument("--root", default=None, help="alternate repo root")
    sp.add_argument("--allow-shrink", action="store_true",
                    help="allow publishing a rebuild with fewer scenarios")
    sp.add_argument("--dry-run", action="store_true",
                    help="list steps without executing")
    sp.add_argument("--verbose", action="store_true")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_rebuild)

    sp = sub.add_parser("feedback", help="submit feedback / show state / audit")
    sp.add_argument("sub_args", nargs=argparse.REMAINDER)
    sp.set_defaults(func=cmd_feedback)

    sp = sub.add_parser("trajectory", help="list/show run trajectories")
    sp.add_argument("sub_args", nargs=argparse.REMAINDER)
    sp.set_defaults(func=cmd_trajectory)

    sp = sub.add_parser("workbench", help="start the local Workbench (web UI)")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8788)
    sp.add_argument("--open", action="store_true",
                    help="open browser after start")
    sp.set_defaults(func=cmd_workbench)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "cmd", None):
        parser.print_help()
        return 0
    try:
        return int(args.func(args) or 0)
    except Exception as e:
        print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
