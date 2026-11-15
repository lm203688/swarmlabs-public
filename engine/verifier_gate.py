#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verifier-based promote gate for synthetic anchors.

Wraps the existing vv-gate (engine-kit/skills/vv-gate/scripts/vv_gate.py)
to provide an independent adjudication layer for anchor promotion decisions.

Design principles:
1. Independence: The verifier holds ground truth the candidate does not.
2. Degradation: Unreachable verifier degrades to NEEDS_HUMAN, never blocks.
3. Auditable: Every verdict is logged with exit code and reason.

Exit codes (from vv-gate):
    0: PROCEED - anchor can be promoted
    2: BLOCK_AUTONOMOUS_ACTION - anchor must not be promoted
    3: PROCEED_WITH_HUMAN_CHECK - needs human signature
    4: UNREACHABLE - gate unavailable (no verdict exists)
    1: ERROR - local call/parameter error

Usage:
    gate = VerifierGate(scenario="microbio_monod", prediction_path="preds.json")
    verdict = gate.check()
    if verdict["gate"] == "PROCEED":
        promote_anchor()
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Verdict:
    """Result of a verifier gate check."""

    exit_code: int
    gate: str
    reason: str
    details: Dict[str, Any] = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        """True if the anchor can be promoted."""
        return self.gate in ("PROCEED", "PROCEED_WITH_HUMAN_CHECK")

    @property
    def needs_human(self) -> bool:
        """True if human confirmation is required."""
        return self.gate == "PROCEED_WITH_HUMAN_CHECK"

    @property
    def blocked(self) -> bool:
        """True if the anchor must not be promoted."""
        return self.gate == "BLOCK_AUTONOMOUS_ACTION"

    @property
    def unreachable(self) -> bool:
        """True if the verifier gate was unreachable."""
        return self.gate == "UNREACHABLE"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "exit_code": self.exit_code,
            "gate": self.gate,
            "reason": self.reason,
            "details": self.details,
        }


class VerifierGate:
    """Independent verifier gate for anchor promotion.

    Wraps the vv-gate script to provide an independent adjudication layer.
    The verifier holds ground truth the candidate does not, making this a
    true third-party verification (not self-grading).

    Args:
        scenario: Scenario identifier (e.g., "microbio_monod")
        prediction_path: Path to JSON file with predictions
        timeout: Timeout in seconds (default 30)
        dry_run: If True, don't actually call the verifier (for testing)
    """

    def __init__(
        self,
        scenario: str,
        prediction_path: str,
        timeout: int = 30,
        dry_run: bool = False,
    ):
        self.scenario = scenario
        self.prediction_path = prediction_path
        self.timeout = timeout
        self.dry_run = dry_run

        # Resolve vv-gate script path
        self._script_path = self._find_vv_gate_script()

    def _find_vv_gate_script(self) -> Optional[str]:
        """Find the vv-gate script."""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        candidates = [
            os.path.join(root, "engine-kit", "skills", "vv-gate", "scripts", "vv_gate.py"),
            os.path.join(root, "skills", "vv-gate", "scripts", "vv_gate.py"),
        ]
        for path in candidates:
            if os.path.isfile(path):
                return path
        return None

    def check(self) -> Verdict:
        """Run the verifier gate check.

        Returns:
            Verdict with exit_code, gate, reason, and details.
        """
        if self.dry_run:
            return self._dry_run_verdict()

        if not self._script_path or not os.path.isfile(self._script_path):
            return Verdict(
                exit_code=4,
                gate="UNREACHABLE",
                reason="vv-gate script not found",
                details={"script_path": self._script_path},
            )

        if not os.path.isfile(self.prediction_path):
            return Verdict(
                exit_code=1,
                gate="ERROR",
                reason=f"Prediction file not found: {self.prediction_path}",
                details={},
            )

        # Build command
        cmd = [
            sys.executable, self._script_path,
            "verify", self.scenario,
            "--pred", self.prediction_path,
            "--json",
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
            return self._parse_result(result)
        except subprocess.TimeoutExpired:
            return Verdict(
                exit_code=4,
                gate="UNREACHABLE",
                reason=f"Verifier gate timed out after {self.timeout}s",
                details={"timeout": self.timeout},
            )
        except Exception as e:
            return Verdict(
                exit_code=1,
                gate="ERROR",
                reason=str(e),
                details={},
            )

    def _parse_result(self, result: subprocess.CompletedProcess) -> Verdict:
        """Parse the subprocess result into a Verdict."""
        exit_code = result.returncode

        # Parse JSON output
        details = {}
        if result.stdout:
            try:
                details = json.loads(result.stdout)
            except json.JSONDecodeError:
                details = {"raw_stdout": result.stdout[:500]}

        # Interpret exit code
        gate = self._interpret_exit_code(exit_code)
        reason = result.stderr.strip() if result.stderr else ""

        # Extract reason from details if available
        if "reason" in details and not reason:
            reason = details["reason"]

        return Verdict(
            exit_code=exit_code,
            gate=gate,
            reason=reason,
            details=details,
        )

    @staticmethod
    def _interpret_exit_code(code: int) -> str:
        """Map exit code to gate string."""
        mapping = {
            0: "PROCEED",
            2: "BLOCK_AUTONOMOUS_ACTION",
            3: "PROCEED_WITH_HUMAN_CHECK",
            4: "UNREACHABLE",
            1: "ERROR",
        }
        return mapping.get(code, "UNKNOWN")

    def _dry_run_verdict(self) -> Verdict:
        """Return a mock verdict for dry-run testing."""
        return Verdict(
            exit_code=0,
            gate="PROCEED",
            reason="dry-run: mock verdict",
            details={"dry_run": True, "scenario": self.scenario},
        )

    def selftest(self) -> bool:
        """Run the vv-gate selftest.

        Returns:
            True if selftest passes, False otherwise.
        """
        if not self._script_path or not os.path.isfile(self._script_path):
            return False

        cmd = [sys.executable, self._script_path, "selftest"]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            return result.returncode == 0
        except Exception:
            return False


def check_anchor_promotion(
    scenario: str,
    prediction_path: str,
    timeout: int = 30,
    dry_run: bool = False,
) -> Verdict:
    """Convenience function to check if an anchor can be promoted.

    Args:
        scenario: Scenario identifier
        prediction_path: Path to predictions JSON file
        timeout: Timeout in seconds
        dry_run: If True, don't actually call the verifier

    Returns:
        Verdict with gate decision
    """
    gate = VerifierGate(
        scenario=scenario,
        prediction_path=prediction_path,
        timeout=timeout,
        dry_run=dry_run,
    )
    return gate.check()


def check_anchor_batch(
    scenario: str,
    predictions: List[Dict],
    timeout: int = 30,
    dry_run: bool = False,
) -> List[Verdict]:
    """Check multiple predictions in a batch.

    Args:
        scenario: Scenario identifier
        predictions: List of prediction dicts
        timeout: Timeout in seconds
        dry_run: If True, don't actually call the verifier

    Returns:
        List of Verdicts (one per prediction)
    """
    # Write predictions to temp file
    temp_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data",
        f"verifier_batch_{id(predictions)}.json",
    )
    os.makedirs(os.path.dirname(temp_path), exist_ok=True)
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(predictions, f, indent=2)

    try:
        verdicts = []
        for i, pred in enumerate(predictions):
            # Check each prediction individually
            pred_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "data",
                f"verifier_pred_{id(predictions)}_{i}.json",
            )
            with open(pred_path, "w", encoding="utf-8") as f:
                json.dump([pred], f, indent=2)

            gate = VerifierGate(
                scenario=scenario,
                prediction_path=pred_path,
                timeout=timeout,
                dry_run=dry_run,
            )
            verdicts.append(gate.check())
            os.unlink(pred_path)
        return verdicts
    finally:
        if os.path.isfile(temp_path):
            os.unlink(temp_path)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Verifier gate for anchor promotion")
    parser.add_argument("scenario", help="Scenario identifier")
    parser.add_argument("--pred", required=True, help="Path to predictions JSON")
    parser.add_argument("--timeout", type=int, default=30, help="Timeout in seconds")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--dry-run", action="store_true", help="Don't call verifier")
    parser.add_argument("--selftest", action="store_true", help="Run selftest")
    args = parser.parse_args()

    if args.selftest:
        gate = VerifierGate(scenario=args.scenario, prediction_path=args.pred)
        ok = gate.selftest()
        print(f"SELFTEST {'OK' if ok else 'FAILED'}")
        sys.exit(0 if ok else 1)

    verdict = check_anchor_promotion(
        scenario=args.scenario,
        prediction_path=args.pred,
        timeout=args.timeout,
        dry_run=args.dry_run,
    )

    if args.json:
        print(json.dumps(verdict.to_dict(), indent=2))
    else:
        print(f"gate={verdict.gate}")
        print(f"exit_code={verdict.exit_code}")
        print(f"reason={verdict.reason}")
        if verdict.details:
            print(f"details={json.dumps(verdict.details, indent=2)}")

    # Exit with the same code as the gate
    sys.exit(verdict.exit_code if verdict.exit_code in (0, 2, 3, 4) else 1)
