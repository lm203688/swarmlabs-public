#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""L-09 pytest: engine/anchor_intake.py (363 lines, 0 prior coverage).

Covers:
- normalize_unit_token alias resolution + whitespace/case
- parse_unit dimension mapping and unknown units
- normalize_value same-dimension conversion + UnitMismatch on cross-dim
- ReferenceCandidate.from_dict y→value bridge (the "silent fail" guard)
- screen_candidate: each rejection/needs_human/accept path
- screen_batch aggregation (counts, rows, policy string)

Fail-closed semantics must hold: no automatic-accept without confirmed_by.
"""

from __future__ import annotations

import math
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine.anchor_intake import (  # noqa: E402
    ACCEPTED,
    NEEDS_HUMAN,
    REJECTED,
    ReferenceCandidate,
    UnitMismatch,
    normalize_unit_token,
    normalize_value,
    parse_unit,
    screen_batch,
    screen_candidate,
)


# ------------------------------------------------- normalize_unit_token

class TestNormalizeUnitToken:
    def test_case_and_space(self):
        assert normalize_unit_token("  g/L ") == "g/l"
        assert normalize_unit_token("G / L") == "g/l"

    def test_power_notation(self):
        # ** becomes ^, alias hr^-1 -> h^-1
        assert normalize_unit_token("h**1") == "h^1"
        assert normalize_unit_token("hr^-1") == "h^-1"

    def test_unicode_alias(self):
        # °C → degc
        assert normalize_unit_token("°C") == "degc"

    def test_passthrough_unknown(self):
        # unknown token is returned unchanged (still normalized form)
        assert normalize_unit_token("unknown-thing") == "unknown-thing"


# ----------------------------------------------------------- parse_unit

class TestParseUnit:
    def test_g_per_l(self):
        dim, factor, offset = parse_unit("g/L")
        assert dim == "mass_conc"
        assert factor == 1.0
        assert offset == 0.0

    def test_mg_per_l_scaling(self):
        dim, factor, _ = parse_unit("mg/L")
        assert dim == "mass_conc"
        assert math.isclose(factor, 1e-3)

    def test_time_units(self):
        assert parse_unit("s")[0] == "time"
        assert parse_unit("min")[1] == 60.0
        assert parse_unit("h")[1] == 3600.0

    def test_dimensionless(self):
        assert parse_unit("")[0] == "dimensionless"
        assert parse_unit("%")[1] == 0.01

    def test_unknown_returns_none(self):
        assert parse_unit("furlong/bullwhip") is None

    def test_superscript_minus(self):
        # h⁻¹ → h^-1
        d = parse_unit("h⁻¹")
        assert d is not None
        assert d[0] == "rate"

    def test_kelvin_vs_celsius_distinct_dimensions(self):
        # K and degC must NOT be same dimension (no silent affine conversion)
        assert parse_unit("K")[0] == "temperature_K"
        assert parse_unit("°C")[0] == "temperature_C"
        assert parse_unit("K")[0] != parse_unit("°C")[0]


# -------------------------------------------------------- normalize_value

class TestNormalizeValue:
    def test_g_per_l_identity(self):
        assert normalize_value(1.0, "g/L", "g/L") == 1.0

    def test_mg_to_g_scaling(self):
        # 1000 mg/L == 1 g/L
        assert math.isclose(normalize_value(1000.0, "mg/L", "g/L"), 1.0)

    def test_minutes_to_hours(self):
        assert math.isclose(normalize_value(60.0, "min", "h"), 1.0)

    def test_percent_to_dimensionless(self):
        assert math.isclose(normalize_value(50.0, "%", "-"), 0.5)

    def test_unknown_unit_raises(self):
        with pytest.raises(UnitMismatch, match="unknown unit"):
            normalize_value(1.0, "furlong", "g/L")

    def test_unknown_target_raises(self):
        with pytest.raises(UnitMismatch, match="unknown target"):
            normalize_value(1.0, "g/L", "furlong")

    def test_dimension_mismatch_raises(self):
        with pytest.raises(UnitMismatch, match="dimension mismatch"):
            normalize_value(1.0, "g/L", "min")  # mass_conc vs time

    def test_kelvin_celsius_mismatch_raises(self):
        # Cross-dim guardrail: K and °C are distinct dims → refused
        with pytest.raises(UnitMismatch, match="dimension mismatch"):
            normalize_value(273.15, "K", "°C")


# ------------------------------------------ ReferenceCandidate bridge

class TestReferenceCandidate:
    def test_from_dict_y_bridge(self):
        """Contract: y → value bridge. Without it, every real submission silently fails."""
        c = ReferenceCandidate.from_dict({"label": "x", "y": 42.0, "unit": "g/L"})
        assert c.value == 42.0
        assert c.unit == "g/L"

    def test_from_dict_value_wins_over_y(self):
        # Explicit value takes precedence over y
        c = ReferenceCandidate.from_dict({"value": 1.0, "y": 999.0})
        assert c.value == 1.0

    def test_from_dict_none_safe(self):
        c = ReferenceCandidate.from_dict(None)
        assert c.value is None
        assert c.label == ""
        assert c.raw == {}

    def test_to_dict_preserves_raw(self):
        raw = {"x": 1, "y": 2, "extra": "keep"}
        c = ReferenceCandidate(label="L", value=2.0, unit="g/L", raw=raw)
        d = c.to_dict()
        assert d["extra"] == "keep"
        assert d["label"] == "L"
        assert d["value"] == 2.0

    def test_defaults(self):
        c = ReferenceCandidate()
        assert c.label == ""
        assert c.value is None
        assert c.source_kind == "external"
        assert c.raw == {}


# ------------------------------------------------------- screen_candidate

class TestScreenCandidate:
    def _mk(self, **kw):
        base = dict(
            label="candidate",
            value=1.0,
            unit="g/L",
            citation="Smith, 2020",
            confirmed_by="Alice",
        )
        base.update(kw)
        return ReferenceCandidate(**base)

    def test_accepted_happy_path(self):
        c = self._mk()
        r = screen_candidate(c, spec_unit="g/L")
        assert r["verdict"] == ACCEPTED
        assert r["reasons"] == []
        assert math.isclose(r["normalized_value"], 1.0)

    def test_reject_bad_value_nan(self):
        c = self._mk(value=float("nan"))
        r = screen_candidate(c, spec_unit="g/L")
        assert r["verdict"] == REJECTED
        assert "bad_value" in r["reasons"]

    def test_reject_bad_value_none(self):
        c = self._mk(value=None)
        r = screen_candidate(c, spec_unit="g/L")
        assert r["verdict"] == REJECTED
        assert "bad_value" in r["reasons"]

    def test_reject_unverifiable_citation(self):
        # No year, no DOI → unverifiable
        c = self._mk(citation="some reference without year")
        r = screen_candidate(c, spec_unit="g/L")
        assert r["verdict"] == REJECTED
        assert "unverifiable_citation" in r["reasons"]

    def test_accept_citation_with_doi(self):
        c = self._mk(citation="doi:10.1000/xyz")
        r = screen_candidate(c, spec_unit="g/L")
        assert r["verdict"] == ACCEPTED

    def test_reject_unknown_unit(self):
        c = self._mk(unit="furlong")
        r = screen_candidate(c, spec_unit="g/L")
        assert r["verdict"] == REJECTED
        assert "unknown_unit" in r["reasons"]

    def test_reject_dimension_mismatch(self):
        c = self._mk(unit="min")  # time vs mass_conc
        r = screen_candidate(c, spec_unit="g/L")
        assert r["verdict"] == REJECTED
        assert "dimension_mismatch" in r["reasons"]

    def test_needs_human_when_spec_unit_unknown(self):
        # No target unit → cannot check dimension → fail-closed to NEEDS_HUMAN
        c = self._mk(unit="g/L", citation="Smith 2020")
        r = screen_candidate(c, spec_unit="")
        assert r["verdict"] == NEEDS_HUMAN
        assert "spec_unit_unknown_cannot_check_dimension" in r["reasons"]

    def test_needs_human_awaiting_confirmation(self):
        # No confirmed_by → mechanical checks pass but no auto-accept
        c = self._mk(confirmed_by="")
        r = screen_candidate(c, spec_unit="g/L")
        assert r["verdict"] == NEEDS_HUMAN
        assert "awaiting_human_confirmation" in r["reasons"]

    def test_needs_human_out_of_range(self):
        c = self._mk(value=1e9)  # way outside [0, 10]
        r = screen_candidate(c, spec_unit="g/L", plausible_range=(0.0, 10.0))
        assert r["verdict"] == NEEDS_HUMAN
        assert "out_of_plausible_range" in r["reasons"]

    def test_no_automatic_accept_without_confirmed_by(self):
        """Fail-closed invariant: ACCEPTED requires a named human."""
        c = ReferenceCandidate(
            label="x",
            value=1.0,
            unit="g/L",
            citation="Smith, 2020",
            confirmed_by="",  # missing
        )
        r = screen_candidate(c, spec_unit="g/L")
        assert r["verdict"] != ACCEPTED


# ---------------------------------------------------------- screen_batch

class TestScreenBatch:
    def _good(self, label="ok", value=1.0):
        return ReferenceCandidate(
            label=label, value=value, unit="g/L",
            citation="Smith, 2020", confirmed_by="Alice",
        )

    def _bad_cite(self, label="bad"):
        return ReferenceCandidate(
            label=label, value=1.0, unit="g/L",
            citation="no year no doi", confirmed_by="Alice",
        )

    def test_batch_counts_and_buckets(self):
        batch = [self._good("a"), self._good("b"), self._bad_cite("c")]
        r = screen_batch(batch, spec_unit="g/L")
        assert r["n"] == 3
        assert r["counts"][ACCEPTED] == 2
        assert r["counts"][REJECTED] == 1
        assert r["counts"][NEEDS_HUMAN] == 0
        assert len(r["accepted"]) == 2
        assert len(r["rejected"]) == 1
        assert len(r["needs_human"]) == 0

    def test_batch_empty(self):
        r = screen_batch([], spec_unit="g/L")
        assert r["n"] == 0
        assert r["counts"] == {ACCEPTED: 0, NEEDS_HUMAN: 0, REJECTED: 0}

    def test_batch_policy_string(self):
        r = screen_batch([self._good()], spec_unit="g/L")
        # policy must warn of no-automatic-accept path
        assert "no automatic-accept" in r["policy"].lower()

    def test_batch_schema_and_range(self):
        r = screen_batch([self._good()], spec_unit="g/L",
                         plausible_range=(0.0, 100.0))
        assert r["schema"] == "swarmlabs-vlab-anchor-screening/1.0"
        assert r["plausible_range"] == [0.0, 100.0]
        r2 = screen_batch([self._good()], spec_unit="g/L")
        assert r2["plausible_range"] is None

    def test_batch_row_shape(self):
        r = screen_batch([self._good("only")], spec_unit="g/L")
        assert len(r["rows"]) == 1
        row = r["rows"][0]
        assert row["label"] == "only"
        assert row["verdict"] == ACCEPTED
        assert "normalized_value" in row
        assert "checks" in row


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
