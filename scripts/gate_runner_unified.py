"""Unified gate runner — consolidates 10+ check scripts into a single entry point.

P1-6: 门禁 8+ → 2。All check_*.py scripts are wrapped into one runner with
grouped execution, unified reporting, and CI-friendly exit codes.

Gate groups:
  - "fast"    : honesty invariants + public numbers + engine contract (< 30s)
  - "full"    : fast + vlab gates + anchor quality + scenario registry + SVP compliance
  - "deploy"  : full + ledger freshness + vlab server (pre-deployment)

Usage:
    python scripts/gate_runner_unified.py --group fast
    python scripts/gate_runner_unified.py --group full
    python scripts/gate_runner_unified.py --group deploy
    python scripts/gate_runner_unified.py --group fast --verbose
    python scripts/gate_runner_unified.py --list
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


# =========================================================================
# Gate definitions
# =========================================================================

@dataclass
class GateCheck:
    """A single gate check."""
    name: str
    script: str  # relative path from ROOT
    description: str
    timeout: int = 60  # seconds
    required: bool = True


@dataclass
class GateResult:
    """Result of a single gate check."""
    name: str
    status: str  # PASS / FAIL / ERROR / TIMEOUT / SKIP
    duration: float
    exit_code: int
    stdout: str = ""
    stderr: str = ""

    @property
    def passed(self) -> bool:
        return self.status == "PASS"


GATE_GROUPS: Dict[str, List[GateCheck]] = {
    "fast": [
        GateCheck("honesty_invariants", "scripts/check_honesty_invariants.py",
                  "Check honesty invariants (no fabricated data, DOI counts)"),
        GateCheck("public_numbers", "scripts/check_public_numbers.py",
                  "Check public_facts.json consistency"),
        GateCheck("engine_contract", "scripts/check_engine_contract.py",
                  "Check engine API contract compliance"),
    ],
    "full": [
        # fast checks
        GateCheck("honesty_invariants", "scripts/check_honesty_invariants.py",
                  "Check honesty invariants", timeout=60),
        GateCheck("public_numbers", "scripts/check_public_numbers.py",
                  "Check public_facts.json", timeout=60),
        GateCheck("engine_contract", "scripts/check_engine_contract.py",
                  "Check engine API contract", timeout=60),
        # extended checks
        GateCheck("vlab_gates", "scripts/check_vlab_gates.py",
                  "Check virtual lab gate invariants", timeout=120),
        GateCheck("anchor_quality", "scripts/check_anchor_quality.py",
                  "Check wet lab anchor quality", timeout=60),
        GateCheck("scenario_registry", "scripts/check_scenario_registry.py",
                  "Check scenario registry consistency", timeout=120),
        GateCheck("svp_compliance", "scripts/check_svp_compliance.py",
                  "Check SVP compliance", timeout=60),
        GateCheck("orphan_selftest", "scripts/check_orphan_selftest.py",
                  "Wire orphan modules (spectral_guard / digital_twin_lab / physics_constrained_conformal) via selftests",
                  timeout=120),
    ],
    "deploy": [
        # full checks + deployment-specific
        GateCheck("honesty_invariants", "scripts/check_honesty_invariants.py",
                  "Check honesty invariants", timeout=60),
        GateCheck("public_numbers", "scripts/check_public_numbers.py",
                  "Check public_facts.json", timeout=60),
        GateCheck("engine_contract", "scripts/check_engine_contract.py",
                  "Check engine API contract", timeout=60),
        GateCheck("vlab_gates", "scripts/check_vlab_gates.py",
                  "Check virtual lab gate invariants", timeout=120),
        GateCheck("anchor_quality", "scripts/check_anchor_quality.py",
                  "Check wet lab anchor quality", timeout=60),
        GateCheck("scenario_registry", "scripts/check_scenario_registry.py",
                  "Check scenario registry", timeout=120),
        GateCheck("svp_compliance", "scripts/check_svp_compliance.py",
                  "Check SVP compliance", timeout=60),
        GateCheck("ledger_fresh", "scripts/check_ledger_fresh.py",
                  "Check ledger freshness", timeout=60),
        GateCheck("vlab_server", "scripts/check_vlab_server.py",
                  "Check virtual lab server health", timeout=30),
    ],
}


# =========================================================================
# Runner
# =========================================================================

class GateRunner:
    """Unified gate runner."""

    def __init__(self, verbose: bool = False):
        self.verbose = verbose

    def run_group(self, group: str) -> List[GateResult]:
        """Run all checks in a group.

        Returns:
            List of GateResult, one per check.
        """
        if group not in GATE_GROUPS:
            print(f"ERROR: unknown gate group '{group}'. Available: {list(GATE_GROUPS.keys())}")
            sys.exit(1)

        checks = GATE_GROUPS[group]
        results = []
        start = time.time()

        print(f"\n{'='*60}")
        print(f"Gate Runner: {group} ({len(checks)} checks)")
        print(f"{'='*60}\n")

        for check in checks:
            result = self._run_single(check)
            results.append(result)
            status_icon = "✓" if result.passed else ("✗" if result.status == "FAIL" else "!")
            print(f"  {status_icon} {check.name:25s}  {result.status:7s}  "
                  f"({result.duration:.1f}s)")

        total_time = time.time() - start
        passed = sum(1 for r in results if r.passed)
        failed = sum(1 for r in results if not r.passed)

        print(f"\n{'='*60}")
        print(f"  {passed}/{len(results)} passed, {failed} failed  ({total_time:.1f}s total)")
        print(f"{'='*60}\n")

        return results

    def _run_single(self, check: GateCheck) -> GateResult:
        """Run a single gate check."""
        script_path = os.path.join(ROOT, check.script)
        if not os.path.exists(script_path):
            return GateResult(
                name=check.name, status="SKIP", duration=0.0,
                exit_code=-1, stderr=f"Script not found: {script_path}",
            )

        start = time.time()
        try:
            proc = subprocess.run(
                [sys.executable, check.script],
                capture_output=True, text=True,
                timeout=check.timeout, cwd=ROOT,
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
            )
            duration = time.time() - start
            status = "PASS" if proc.returncode == 0 else "FAIL"
            return GateResult(
                name=check.name, status=status, duration=duration,
                exit_code=proc.returncode,
                stdout=proc.stdout[:2000] if self.verbose else "",
                stderr=proc.stderr[:2000] if self.verbose else "",
            )
        except subprocess.TimeoutExpired:
            duration = time.time() - start
            return GateResult(
                name=check.name, status="TIMEOUT", duration=duration,
                exit_code=-1, stderr=f"Timeout after {check.timeout}s",
            )
        except Exception as e:
            duration = time.time() - start
            return GateResult(
                name=check.name, status="ERROR", duration=duration,
                exit_code=-1, stderr=str(e),
            )

    def report(self, results: List[GateResult]) -> Dict[str, Any]:
        """Generate a summary report."""
        passed = [r for r in results if r.passed]
        failed = [r for r in results if not r.passed]

        return {
            "total": len(results),
            "passed": len(passed),
            "failed": len(failed),
            "pass_rate": round(len(passed) / max(len(results), 1), 4),
            "checks": [
                {
                    "name": r.name,
                    "status": r.status,
                    "duration": round(r.duration, 2),
                    "exit_code": r.exit_code,
                }
                for r in results
            ],
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }


# =========================================================================
# CLI
# =========================================================================

def main():
    parser = argparse.ArgumentParser(description="Unified gate runner")
    parser.add_argument("--group", choices=["fast", "full", "deploy", "all"],
                        default="fast", help="Gate group to run")
    parser.add_argument("--list", action="store_true", help="List available gate groups")
    parser.add_argument("--verbose", action="store_true", help="Show stdout/stderr of each check")
    parser.add_argument("--output", help="Write JSON report to file")
    args = parser.parse_args()

    if args.list:
        print("Available gate groups:")
        for group, checks in GATE_GROUPS.items():
            print(f"\n  {group} ({len(checks)} checks):")
            for c in checks:
                req = " (required)" if c.required else " (optional)"
                print(f"    - {c.name}: {c.description}{req}")
        return

    runner = GateRunner(verbose=args.verbose)

    if args.group == "all":
        all_results = []
        for group in ["fast", "full", "deploy"]:
            results = runner.run_group(group)
            all_results.extend(results)
    else:
        all_results = runner.run_group(args.group)

    report = runner.report(all_results)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        print(f"Report written to {args.output}")

    # Exit code: 0 if all passed, 1 if any failed
    if report["failed"] > 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
