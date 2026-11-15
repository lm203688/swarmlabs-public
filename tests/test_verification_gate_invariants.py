#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Core invariant tests for `engine/verification_gate.py`.

Why this file exists
-------------------
The 2026-10-16 assessment report's fault-injection experiment showed that a
semantic fault at `verification_gate.py:468` —

    - before: `if r["verdict"] == GRADE_PASS and not r["ok"]:`
    - after : `if False and r["verdict"] == GRADE_PASS and not r["ok"]:`

— passes all 270 existing tests while silently breaking the "No Anchored
Counter, No PASS" invariant. These tests exist to make that invariant
**machine-enforced**: any semantic weakening of the fail-closed paths must
cause at least one test here to fail.

Coverage (mapped to report §7 · P0-1):

  Group 1 — Fail-closed behavior
    - REFUTED scenario → ActionBlocked
    - Missing ledger entry → ActionBlocked
    - Unknown scenario key → ActionBlocked

  Group 2 — Cite-checker invariant ("No Anchored Counter, No PASS")
    - PASS without anchor/replication → cite_check_scenario reports violation
    - PASS with anchor → cite_check_scenario passes
    - assert_pass_has_anchor throws ActionBlocked on anchor-less PASS
    - assert_pass_has_anchor silently returns on anchor-present PASS
    - cite_check_ledger aggregates violations correctly

  Group 3 — Fingerprint freshness gate
    - ledger with mismatched all_digest → assert_ledger_not_stale throws
    - ledger with matching all_digest → assert_ledger_not_stale passes

These tests construct synthetic ledgers directly (no disk I/O), so they
are not sensitive to the state of `frontend_fixed/reports/gate_ledger.json`
or `data/gate_ledger.json`.
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict

import pytest

from engine.verification_gate import (
    ActionBlocked,
    GATE_BLOCK,
    GRADE_PASS,
    GRADE_REFUTED,
    assert_action_allowed,
    assert_ledger_not_stale,
    assert_pass_has_anchor,
    cite_check_ledger,
    cite_check_scenario,
    engine_fingerprint,
    gate_for_scenario,
)


# ===========================================================================
# helpers
# ===========================================================================
def _mk_ledger(scenarios: Dict[str, Dict[str, Any]],
               all_digest: str = "0" * 64,
               git_commit: str = "test-commit-sha") -> Dict[str, Any]:
    """Build a minimal ledger dict for tests.

    The `all_digest` field is compared by `ledger_is_stale()` against
    `engine_fingerprint()["all_digest"]`. Passing a zero-string sentinel
    guarantees the freshness gate fires (stale=True) unless the caller
    explicitly opts in.
    """
    return {
        "generated_at_utc": "2026-10-16T00:00:00Z",
        "total": len(scenarios),
        "counts": {},
        "n_blocked": sum(
            1 for e in scenarios.values() if e.get("gate") == GATE_BLOCK
        ),
        "n_human_check_required": 0,
        "blocked": [k for k, e in scenarios.items() if e.get("gate") == GATE_BLOCK],
        "scenarios": scenarios,
        "provenance": {
            "engine": {
                "all_digest": all_digest,
                "git_commit": git_commit,
                "n_modules": 1,
            }
        },
    }


def _current_digest() -> str:
    """Get the current engine all_digest so tests can build a FRESH ledger."""
    return engine_fingerprint()["all_digest"]


# ===========================================================================
# Group 1 — Fail-closed behavior
# ===========================================================================
class TestFailClosed:
    """REFUTED / missing ledger entries must always block autonomous action."""

    def test_refuted_scenario_raises_action_blocked(self):
        led = _mk_ledger({
            "my_refuted": {"verdict": GRADE_REFUTED, "gate": GATE_BLOCK},
        })
        with pytest.raises(ActionBlocked) as excinfo:
            assert_action_allowed("my_refuted", ledger=led, enforce_fresh=False)
        assert "my_refuted" in str(excinfo.value)

    def test_unknown_scenario_raises_action_blocked(self):
        led = _mk_ledger({"some_scen": {"verdict": GRADE_PASS, "gate": "PROCEED"}})
        with pytest.raises(ActionBlocked) as excinfo:
            assert_action_allowed("does_not_exist", ledger=led, enforce_fresh=False)
        assert "no gate ledger entry" in str(excinfo.value)

    def test_empty_ledger_raises_action_blocked(self):
        # enforce_fresh=False so we test the "no entry" branch, not staleness.
        with pytest.raises(ActionBlocked):
            assert_action_allowed("anything", ledger={}, enforce_fresh=False)

    def test_gate_for_scenario_unknown_returns_none(self):
        led = _mk_ledger({"known": {"gate": "PROCEED"}})
        assert gate_for_scenario("missing_key", led) is None


# ===========================================================================
# Group 2 — "No Anchored Counter, No PASS" invariant
# ===========================================================================
class TestCiteCheckInvariant:
    """A PASS verdict without any anchor/replication must be flagged."""

    def test_pass_without_anchor_is_violation(self):
        entry = {"verdict": GRADE_PASS, "gate": "PROCEED"}
        r = cite_check_scenario("unanchored", entry)
        assert r["verdict"] == GRADE_PASS
        assert r["ok"] is False, (
            "PASS without any anchor must fail cite_check — this is the core invariant"
        )
        assert "PASS_without_any_anchor_or_replication" in r["violations"]

    def test_pass_with_anchor_is_ok(self):
        entry = {
            "verdict": GRADE_PASS,
            "gate": "PROCEED",
            "wet_lab_anchors": [{"grade": "AGREE", "anchor": "x"}],
        }
        r = cite_check_scenario("anchored", entry)
        assert r["ok"] is True
        assert any("anchor_fields" in reason for reason in r["reasons"])

    def test_pass_with_replication_is_ok(self):
        entry = {"verdict": GRADE_PASS, "gate": "PROCEED", "replication_passed": True}
        r = cite_check_scenario("replicated", entry)
        assert r["ok"] is True
        assert "replication" in r["reasons"]

    def test_non_pass_verdict_is_ok_trivially(self):
        # Non-PASS verdicts are not "claims of passing" — no anchor needed.
        entry = {"verdict": GRADE_REFUTED, "gate": GATE_BLOCK}
        r = cite_check_scenario("refuted", entry)
        assert r["ok"] is True

    def test_assert_pass_has_anchor_raises_on_unanchored_pass(self):
        """This is THE invariant from the 2026-10-16 fault injection.

        If this test ever passes silently, then the fail-closed semantic
        at verification_gate.py:468 has been weakened.
        """
        led = _mk_ledger({
            "bad_pass": {
                "verdict": GRADE_PASS,
                "gate": "PROCEED",
                # deliberately no anchors / no replication
            },
        })
        with pytest.raises(ActionBlocked) as excinfo:
            assert_pass_has_anchor("bad_pass", ledger=led)
        assert "No Anchored Counter, No PASS" in str(excinfo.value)

    def test_assert_pass_has_anchor_passes_when_anchored(self):
        led = _mk_ledger({
            "good_pass": {
                "verdict": GRADE_PASS,
                "gate": "PROCEED",
                "anchors": [{"grade": "AGREE"}],
            },
        })
        # Should NOT raise. If the invariant is corrupted (e.g. the
        # `if False and ...` fault), this test would still pass — the
        # negative test above is the real guard.
        assert_pass_has_anchor("good_pass", ledger=led)

    def test_cite_check_ledger_aggregates_violations(self):
        led = _mk_ledger({
            "pass_no_anchor": {"verdict": GRADE_PASS, "gate": "PROCEED"},
            "pass_with_anchor": {
                "verdict": GRADE_PASS,
                "gate": "PROCEED",
                "anchors": [{"grade": "AGREE"}],
            },
            "refuted": {"verdict": GRADE_REFUTED, "gate": GATE_BLOCK},
        })
        audit = cite_check_ledger(led)
        assert audit["checked"] == 3
        assert audit["pass_count"] == 2
        assert audit["n_violations"] == 1
        assert audit["pass_with_anchor"] == 1
        violated_keys = [v["key"] for v in audit["violations"]]
        assert violated_keys == ["pass_no_anchor"]

    def test_cite_check_scenario_grade_alias(self):
        # entry uses "grade" instead of "verdict" — must be honored.
        entry = {"grade": GRADE_PASS, "gate": "PROCEED"}
        r = cite_check_scenario("grade_alias", entry)
        assert r["ok"] is False  # still no anchor


# ===========================================================================
# Group 3 — Fingerprint freshness gate
# ===========================================================================
class TestFingerprintFreshness:
    """Stale ledger must be rejected; fresh ledger must pass."""

    def test_stale_ledger_raises_action_blocked(self):
        led = _mk_ledger(
            {"some": {"verdict": GRADE_PASS, "gate": "PROCEED"}},
            all_digest="deadbeef" * 8,  # deliberately mismatched
        )
        with pytest.raises(ActionBlocked) as excinfo:
            assert_ledger_not_stale(led)
        assert "stale" in str(excinfo.value)

    def test_fresh_ledger_passes(self):
        """Ledger whose all_digest matches the current engine fingerprint passes."""
        led = _mk_ledger(
            {"some": {"verdict": GRADE_PASS, "gate": "PROCEED"}},
            all_digest=_current_digest(),
        )
        status = assert_ledger_not_stale(led)
        assert status["stale"] is False

    def test_missing_ledger_raises_action_blocked(self):
        """Passing an empty ledger dict exercises the 'no ledger' branch.

        Note: `ledger_is_stale(None)` would load the real on-disk ledger via
        `load_ledger()`, which exists in this repo — that is NOT the case we
        want to test. We want to test what happens when no ledger is present.
        """
        with pytest.raises(ActionBlocked) as excinfo:
            assert_ledger_not_stale({})
        assert "fail closed" in str(excinfo.value)

    def test_assert_action_allowed_enforces_fresh_by_default(self):
        """The default `enforce_fresh=True` must block stale ledgers."""
        led = _mk_ledger(
            {"some": {"verdict": GRADE_PASS, "gate": "PROCEED"}},
            all_digest="stale" * 16,
        )
        with pytest.raises(ActionBlocked):
            assert_action_allowed("some", ledger=led)


# ===========================================================================
# Group 4 — Self-consistency (proves the tests exercise real code paths)
# ===========================================================================
class TestSelfConsistency:
    """Sanity checks that pin the test harness to real behavior.

    If any of these fail, the tests above are misleading — they exercise
    mocks that no longer match the real engine.
    """

    def test_engine_fingerprint_returns_all_digest(self):
        fp = engine_fingerprint()
        assert isinstance(fp["all_digest"], str)
        assert len(fp["all_digest"]) == 64
        assert all(c in "0123456789abcdef" for c in fp["all_digest"])

    def test_cite_check_scenario_on_empty_entry(self):
        # Empty entry should not crash; should return non-PASS ok=True.
        r = cite_check_scenario("empty", {})
        assert r["ok"] is True  # verdict is None, so not PASS

    def test_action_blocked_is_runtime_error_subclass(self):
        # ActionBlocked must be catchable as a RuntimeError — downstream
        # error taxonomy in scripts/error_taxonomy.py depends on this.
        assert issubclass(ActionBlocked, RuntimeError)
