#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""L-09 pytest: engine/verifier_gate.py (351 lines, 0 prior coverage).

Covers:
- Verdict dataclass properties (allowed/needs_human/blocked/unreachable)
- _interpret_exit_code mapping (0/1/2/3/4/unknown)
- VerifierGate.check() error branches: missing prediction file, dry_run
- check_anchor_promotion convenience wrapper
- to_dict round-trip

Design: no subprocess calls. All tests use dry_run or monkey-patched
exit-code injection so we never actually invoke vv_gate.py.
"""

from __future__ import annotations

import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine.verifier_gate import (  # noqa: E402
    Verdict,
    VerifierGate,
    check_anchor_batch,
    check_anchor_promotion,
)


# --------------------------------------------------------------- Verdict

class TestVerdict:
    def test_proceed_allowed(self):
        v = Verdict(exit_code=0, gate="PROCEED", reason="ok")
        assert v.allowed is True
        assert v.blocked is False
        assert v.needs_human is False
        assert v.unreachable is False

    def test_proceed_with_human(self):
        v = Verdict(exit_code=3, gate="PROCEED_WITH_HUMAN_CHECK", reason="sig")
        assert v.allowed is True
        assert v.needs_human is True
        assert v.blocked is False

    def test_blocked(self):
        v = Verdict(exit_code=2, gate="BLOCK_AUTONOMOUS_ACTION", reason="bad")
        assert v.allowed is False
        assert v.blocked is True

    def test_unreachable(self):
        v = Verdict(exit_code=4, gate="UNREACHABLE", reason="no gate")
        assert v.unreachable is True
        assert v.allowed is False

    def test_to_dict_round_trip(self):
        v = Verdict(exit_code=0, gate="PROCEED", reason="r", details={"k": 1})
        d = v.to_dict()
        assert d == {
            "exit_code": 0,
            "gate": "PROCEED",
            "reason": "r",
            "details": {"k": 1},
        }

    def test_default_details_empty(self):
        v = Verdict(exit_code=0, gate="PROCEED", reason="")
        assert v.details == {}


# --------------------------------------------------- _interpret_exit_code

class TestExitCodeMapping:
    @pytest.mark.parametrize(
        "code,gate",
        [
            (0, "PROCEED"),
            (1, "ERROR"),
            (2, "BLOCK_AUTONOMOUS_ACTION"),
            (3, "PROCEED_WITH_HUMAN_CHECK"),
            (4, "UNREACHABLE"),
            (99, "UNKNOWN"),
            (-1, "UNKNOWN"),
        ],
    )
    def test_interpret(self, code, gate):
        assert VerifierGate._interpret_exit_code(code) == gate


# -------------------------------------------------------------- check()

class TestCheckErrorPaths:
    def test_missing_prediction_returns_error(self, tmp_path):
        gate = VerifierGate(
            scenario="microbio_monod",
            prediction_path=str(tmp_path / "does_not_exist.json"),
            dry_run=False,
        )
        # force script_path to be present so we hit the pred-file branch
        gate._script_path = str(tmp_path / "fake_vv_gate.py")
        (tmp_path / "fake_vv_gate.py").write_text("# stub")
        v = gate.check()
        assert v.gate == "ERROR"
        assert v.exit_code == 1
        assert "Prediction file not found" in v.reason

    def test_dry_run_returns_proceed(self):
        gate = VerifierGate(
            scenario="microbio_monod",
            prediction_path="irrelevant.json",
            dry_run=True,
        )
        v = gate.check()
        assert v.gate == "PROCEED"
        assert v.exit_code == 0
        assert v.details.get("dry_run") is True

    def test_script_missing_returns_unreachable(self, tmp_path):
        gate = VerifierGate(
            scenario="microbio_monod",
            prediction_path=str(tmp_path / "preds.json"),
            dry_run=False,
        )
        gate._script_path = None  # simulate not found
        v = gate.check()
        assert v.gate == "UNREACHABLE"
        assert v.exit_code == 4

    def test_timeout_returns_unreachable(self, tmp_path):
        """A subprocess.TimeoutExpired during check() must degrade to UNREACHABLE."""
        (tmp_path / "preds.json").write_text("{}")
        (tmp_path / "vv_gate.py").write_text("# stub")
        gate = VerifierGate(
            scenario="microbio_monod",
            prediction_path=str(tmp_path / "preds.json"),
            timeout=1,
            dry_run=False,
        )
        gate._script_path = str(tmp_path / "vv_gate.py")
        import subprocess as sp

        def _raise(*a, **kw):
            raise sp.TimeoutExpired(cmd=["x"], timeout=1)

        with patch("engine.verifier_gate.subprocess.run", side_effect=_raise):
            v = gate.check()
        assert v.gate == "UNREACHABLE"
        assert v.details.get("timeout") == 1


class TestParseResult:
    def test_exit_0_proceed(self):
        gate = VerifierGate(scenario="s", prediction_path="p")
        res = MagicMock()
        res.returncode = 0
        res.stdout = '{"reason": "all clear"}'
        res.stderr = ""
        v = gate._parse_result(res)
        assert v.gate == "PROCEED"
        assert v.reason == "all clear"

    def test_exit_2_blocked_with_stderr_reason(self):
        gate = VerifierGate(scenario="s", prediction_path="p")
        res = MagicMock()
        res.returncode = 2
        res.stdout = ""
        res.stderr = "hard fail\n"
        v = gate._parse_result(res)
        assert v.gate == "BLOCK_AUTONOMOUS_ACTION"
        assert "hard fail" in v.reason

    def test_bad_json_stdout_captured_as_raw(self):
        gate = VerifierGate(scenario="s", prediction_path="p")
        res = MagicMock()
        res.returncode = 0
        res.stdout = "not json"
        res.stderr = ""
        v = gate._parse_result(res)
        assert "raw_stdout" in v.details


# ------------------------------------------------- Convenience wrappers

class TestConvenienceFunctions:
    def test_check_anchor_promotion_dry_run(self):
        v = check_anchor_promotion(
            scenario="microbio_monod",
            prediction_path="any.json",
            dry_run=True,
        )
        assert isinstance(v, Verdict)
        assert v.gate == "PROCEED"

    def test_check_anchor_batch_dry_run_writes_files(self, tmp_path, monkeypatch):
        """check_anchor_batch writes temp JSON; with dry_run=True each verdict is PROCEED."""
        predictions = [{"k": 1}, {"k": 2}, {"k": 3}]
        # ensure data dir exists (script writes under ROOT/data)
        data_dir = os.path.join(ROOT, "data")
        os.makedirs(data_dir, exist_ok=True)
        try:
            verdicts = check_anchor_batch(
                scenario="microbio_monod",
                predictions=predictions,
                dry_run=True,
            )
            assert isinstance(verdicts, list)
            assert len(verdicts) == 3
            assert all(isinstance(v, Verdict) for v in verdicts)
        finally:
            # clean up any temp files created under data/
            for name in os.listdir(data_dir):
                if name.startswith("verifier_batch_") or name.startswith("verifier_pred_"):
                    try:
                        os.remove(os.path.join(data_dir, name))
                    except OSError:
                        pass


# --------------------------------------------------------------- selftest

class TestSelftest:
    def test_selftest_missing_script_returns_false(self):
        gate = VerifierGate(scenario="s", prediction_path="p")
        gate._script_path = None
        assert gate.selftest() is False


# --------------------------------------------------------- _find_vv_gate

class TestScriptDiscovery:
    def test_returns_none_when_no_candidate_exists(self, tmp_path, monkeypatch):
        """When neither engine-kit nor skills vv-gate path resolves, returns None."""
        # build a fake root with no vv_gate.py anywhere
        fake_root = tmp_path / "empty_root"
        (fake_root / "engine").mkdir(parents=True)
        (fake_root / "engine" / "verifier_gate.py").write_text("")
        # monkeypatch abspath so _find_vv_gate_script resolves under fake_root
        # (the code does dirname(dirname(abspath(__file__))) -> fake_root)
        import engine.verifier_gate as m

        orig_abspath = os.path.abspath
        monkeypatch.setattr(
            m.os.path,
            "abspath",
            lambda *a: str(fake_root / "engine" / "verifier_gate.py") if len(a) == 1 else orig_abspath(*a),
        )
        gate = m.VerifierGate(scenario="s", prediction_path="p")
        assert gate._script_path is None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
