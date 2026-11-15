"""Contract tests for the 3 orphan modules wired into the gate pipeline.

Complements `scripts/check_orphan_selftest.py` (which runs the modules'
embedded `_selftest()`). These tests assert on **public API contracts** that
downstream consumers rely on — the parts a `_selftest()` might miss.

Coverage (板块 assessment 2026-10-14):
    - engine.spectral_guard             板块 05 P0
    - engine.digital_twin_lab           板块 07 P0
    - engine.physics_constrained_conformal  板块 03 P0
"""
from __future__ import annotations

import json
from dataclasses import asdict

import numpy as np
import pytest


# ===========================================================================
# spectral_guard — 板块 05 P0
# ===========================================================================
class TestSpectralGuardContract:
    """spectral_guard.py exposes 2 public names: SpectralProvenanceGuard and spectral_diagnose."""

    def test_module_exports_two_public_names(self):
        import engine.spectral_guard as sg
        assert "SpectralProvenanceGuard" in sg.__all__
        assert "spectral_diagnose" in sg.__all__

    def test_diagnose_returns_dict_with_expected_keys(self):
        from engine.spectral_guard import SpectralProvenanceGuard
        guard = SpectralProvenanceGuard(tolerance_ratio=1.05)
        x = np.linspace(0.0, 10.0, 500)
        u = np.sin(2.0 * x)
        res = guard.diagnose(u, x, expected_frequencies=[2.0])
        assert isinstance(res, dict)
        assert "violation_count" in res
        assert isinstance(res["violation_count"], int)

    def test_diagnose_detects_frequency_swap(self):
        from engine.spectral_guard import SpectralProvenanceGuard
        guard = SpectralProvenanceGuard(tolerance_ratio=1.05)
        x = np.linspace(0.0, 10.0, 1000)
        u = np.sin(3.0 * x)  # wrong freq vs expected 2.0
        res = guard.diagnose(u, x, expected_frequencies=[2.0])
        assert res["violation_count"] > 0, "parameter-swap attack must be caught"

    def test_fingerprint_spectrum_is_64_char_hex(self):
        from engine.spectral_guard import SpectralProvenanceGuard
        guard = SpectralProvenanceGuard()
        x = np.linspace(0.0, 10.0, 500)
        u = np.sin(2.0 * x)
        fp = guard.fingerprint_spectrum(u, x)
        assert isinstance(fp, str)
        assert len(fp) == 64
        assert all(c in "0123456789abcdef" for c in fp)

    def test_functional_api_matches_class_api(self):
        from engine.spectral_guard import SpectralProvenanceGuard, spectral_diagnose
        x = np.linspace(0.0, 10.0, 500)
        u = np.sin(2.0 * x)
        a = spectral_diagnose(u, x, expected_frequencies=[2.0])
        b = SpectralProvenanceGuard().diagnose(u, x, expected_frequencies=[2.0])
        assert a["violation_count"] == b["violation_count"]


# ===========================================================================
# digital_twin_lab — 板块 07 P0
# ===========================================================================
class TestDigitalTwinLabContract:
    """digital_twin_lab.py provides 4 scenarios, run_twin_loop, compare_strategies, run_all_twins."""

    def test_builtin_scenarios_have_4_entries(self):
        from engine.digital_twin_lab import BUILTIN_TWIN_SCENARIOS
        assert len(BUILTIN_TWIN_SCENARIOS) == 4

    def test_each_scenario_has_required_attributes(self):
        from engine.digital_twin_lab import BUILTIN_TWIN_SCENARIOS
        for name, sc in BUILTIN_TWIN_SCENARIOS.items():
            assert hasattr(sc, "name"), name
            assert hasattr(sc, "bounds"), name
            assert hasattr(sc, "evaluate"), name
            assert hasattr(sc, "measure"), name

    def test_run_twin_loop_returns_result_with_required_fields(self):
        from engine.digital_twin_lab import run_twin_loop, BUILTIN_TWIN_SCENARIOS
        sc = BUILTIN_TWIN_SCENARIOS["digital_monod_growth"]
        res = run_twin_loop(
            scenario=sc,
            n_iterations=4,
            n_initial_points=8,
            strategy="cal",
            seed=0,
            noise_level=0.02,
        )
        assert hasattr(res, "final_rmse")
        assert hasattr(res, "initial_rmse")
        assert hasattr(res, "rmse_reduction_pct")
        assert hasattr(res, "width_reduction_pct")
        assert res.final_rmse < res.initial_rmse, "twin loop must improve RMSE"

    def test_compare_strategies_returns_all_strategies(self):
        from engine.digital_twin_lab import compare_strategies, BUILTIN_TWIN_SCENARIOS
        sc = BUILTIN_TWIN_SCENARIOS["digital_monod_growth"]
        res = compare_strategies(sc, n_iterations=3, n_initial_points=6, seed=0)
        assert "summary" in res
        assert "strategies" in res
        for strat in ("cal", "gp_std", "random"):
            assert strat in res["strategies"], strat

    def test_run_all_twins_covers_every_scenario(self):
        from engine.digital_twin_lab import run_all_twins, BUILTIN_TWIN_SCENARIOS
        res = run_all_twins(n_iterations=2, n_initial_points=5, seed=0)
        assert "results" in res
        assert set(res["results"].keys()) == set(BUILTIN_TWIN_SCENARIOS.keys())
        assert res["n_scenarios"] == len(BUILTIN_TWIN_SCENARIOS)


# ===========================================================================
# physics_constrained_conformal — 板块 03 P0
# ===========================================================================
class TestPhysicsConstrainedConformalContract:
    """Constraints must narrow intervals (never widen) and respect physics ceilings."""

    def test_apply_physics_constraints_never_widens(self):
        """Constraints must not EXPAND the interval — either clamp strictly or
        fail-closed swap when the constraint pushes lower past upper."""
        from engine.physics_constrained_conformal import (
            apply_physics_constraints, BUILTIN_CONSTRAINTS,
        )
        rng = np.random.default_rng(0)
        n = 50
        l = rng.uniform(-0.5, 0.5, n)
        u = l + rng.uniform(0.1, 1.0, n)
        c = BUILTIN_CONSTRAINTS["monod_growth"]
        nl, nu = apply_physics_constraints(l, u, [c], params={"mu_max": 0.81})
        # Interval must remain valid (fail-closed swap guarantees lower <= upper)
        assert np.all(nl <= nu + 1e-12)
        # Physics ceiling must hold
        assert np.all(nu <= 0.81 + 1e-9)
        # Never expand width: (nu - nl) <= (u - l)
        # (When a swap occurred the interval collapses to a point, which is even narrower.)
        width_before = u - l
        width_after = nu - nl
        assert np.all(width_after <= width_before + 1e-12), (
            "constraining must not widen intervals"
        )

    def test_langmuir_constraint_caps_at_one(self):
        from engine.physics_constrained_conformal import (
            apply_physics_constraints, BUILTIN_CONSTRAINTS,
        )
        l = np.array([-1.0, 0.0, 0.5, 2.0])
        u = l + np.array([1.0, 0.5, 0.5, 1.0])
        c = BUILTIN_CONSTRAINTS["langmuir_adsorption"]
        nl, nu = apply_physics_constraints(l, u, [c], params={})
        # θ ∈ [0, 1] — always respected by fail-closed swap
        assert np.all(nl >= 0.0 - 1e-12)
        assert np.all(nu <= 1.0 + 1e-12)
        assert np.all(nl <= nu + 1e-12)

    def test_physics_constrained_conformal_sharpness_monotone(self):
        from engine.physics_constrained_conformal import (
            physics_constrained_conformal, BUILTIN_CONSTRAINTS,
        )
        rng = np.random.default_rng(0)
        n = 40
        # Monod response y = μx/(ks+x), μ=0.8, ks=1
        x = rng.uniform(0.5, 5.0, n)
        y = 0.8 * x / (1.0 + x) + 0.02 * rng.standard_normal(n)
        pred_center = y + 0.05 * rng.standard_normal(n)
        sigma = np.full(n, 0.15)
        result = physics_constrained_conformal(
            y_true=y, pred_center=pred_center, sigma=sigma,
            constraints=[BUILTIN_CONSTRAINTS["monod_growth"]],
            params={"mu_max": 0.81}, alpha=0.05,
        )
        # constrained sharpness must be <= unconstrained
        assert result.sharpness_after <= result.sharpness_before + 1e-12

    def test_physics_constrained_conformal_no_constraint_is_noop(self):
        from engine.physics_constrained_conformal import physics_constrained_conformal
        rng = np.random.default_rng(0)
        n = 40
        y = rng.uniform(0.1, 1.0, n)
        pred_center = y + 0.05 * rng.standard_normal(n)
        sigma = np.full(n, 0.15)
        result = physics_constrained_conformal(
            y_true=y, pred_center=pred_center, sigma=sigma,
            constraints=[], params={}, alpha=0.05,
        )
        assert np.isclose(result.sharpness_after, result.sharpness_before, atol=1e-3)

    def test_result_to_dict_is_json_serializable(self):
        from engine.physics_constrained_conformal import (
            physics_constrained_conformal, BUILTIN_CONSTRAINTS,
        )
        rng = np.random.default_rng(0)
        n = 40
        y = rng.uniform(0.1, 1.0, n)
        pred_center = y + 0.05 * rng.standard_normal(n)
        sigma = np.full(n, 0.15)
        result = physics_constrained_conformal(
            y_true=y, pred_center=pred_center, sigma=sigma,
            constraints=[BUILTIN_CONSTRAINTS["monod_growth"]],
            params={"mu_max": 0.81}, alpha=0.05,
        )
        d = result.to_dict()
        s = json.dumps(d)
        assert isinstance(s, str)
