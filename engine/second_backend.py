"""Phase 2 Second Backend — interface stub for external reproduction.

**Status**: STUB / NOT IMPLEMENTED / NOT VERIFIED.
Report §6 Phase 2 mentions "second backend" as an external-reproduction
guarantee. This module defines the **contract** a second backend must
satisfy so that external reproduction is testable. It does NOT provide a
working second backend — that requires either a different surrogate library
(BayesOpt / BoTorch / GPyTorch) or a different compute platform.

The class `SecondBackend` is a **protocol-level interface**:

    backend = SomeSecondBackend()
    surrogate = backend.fit_surrogate(X_train, y_train, hyperparams)
    pred_mean, pred_std = surrogate.predict(X_test)

`ReferenceSecondBackend` is a **stub implementation** that simply wraps
the primary backend's predictor with a "not verified" provenance tag. It
is useful for CI plumbing tests and documentation only — it does NOT
provide independent numerical verification.

**KPI linkage**: `kpi-sci-external-reproduction` — target ≥ 0.85 bit-exact
match with primary backend on held-out data. Current status: unmeasurable
because no second backend exists yet.

**Hard constraint**: Do NOT claim any external reproduction capability
based on this module alone. The stub backend is by construction identical
to the primary — it can only reproduce the primary, which is not
independent verification.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple


# --------------------------------------------------------------------------
# Protocol (interface contract)
# --------------------------------------------------------------------------

class BackendSurrogate(Protocol):
    """Interface for a fitted surrogate model returned by a backend."""

    def predict(self, X: List[List[float]]) -> Tuple[List[List[float]], List[List[float]]]:
        """Return (mean, std) predictions for each query point.

        Shapes: X is (n, dim); mean and std are each (n, n_out).
        """
        ...

    def properties(self) -> Dict[str, Any]:
        """Return backend-specific metadata (kernel, ls, noise, etc.)."""
        ...


class SecondBackend(Protocol):
    """Interface for a full backend implementation."""

    name: str
    version: str
    is_independent: bool   # False means this backend wraps the primary
    provenance: str        # e.g. "stub-of-primary" or "independent-impl"

    def fit_surrogate(
        self,
        X_train: List[List[float]],
        y_train: List[float],
        hyperparams: Optional[Dict[str, Any]] = None,
    ) -> BackendSurrogate:
        """Fit a surrogate on training data. Return a BackendSurrogate."""
        ...


# --------------------------------------------------------------------------
# Stub implementation (NOT INDEPENDENT)
# --------------------------------------------------------------------------

@dataclass
class _StubSurrogate:
    """Wraps a primary predictor callable with a not-verified provenance tag."""

    _predict_fn: Callable[[List[List[float]]], Tuple[List[List[float]], List[List[float]]]]
    metadata: Dict[str, Any] = field(default_factory=dict)

    def predict(self, X: List[List[float]]) -> Tuple[List[List[float]], List[List[float]]]:
        return self._predict_fn(X)

    def properties(self) -> Dict[str, Any]:
        return {
            "kernel": "stub-of-primary",
            "provenance": "not-independent",
            **self.metadata,
        }


@dataclass
class ReferenceSecondBackend:
    """Reference (stub) second backend. NOT INDEPENDENT.

    Use case: smoke-testing the interface, wiring KPI plumbing, and
    demonstrating that the code paths work.

    The `predict_fn` argument lets callers inject a callable that returns
    (mean, std) for a list of query points. If predict_fn is None, the
    backend returns all-zero predictions (explicit unverified).
    """

    name: str = "reference-stub"
    version: str = "0.1.0"
    is_independent: bool = False
    provenance: str = "stub-of-primary"

    predict_fn: Optional[Callable[[List[List[float]]], Tuple[List[List[float]], List[List[float]]]]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def fit_surrogate(
        self,
        X_train: List[List[float]],
        y_train: List[float],
        hyperparams: Optional[Dict[str, Any]] = None,
    ) -> _StubSurrogate:
        if self.predict_fn is None:
            # No predictor provided: return zeros (explicit unverified).
            def _zeros(X: List[List[float]]) -> Tuple[List[List[float]], List[List[float]]]:
                n, dim = len(X), (len(X[0]) if X else 0)
                mean = [[0.0] for _ in X]
                std = [[float("inf")] for _ in X]  # infinite uncertainty = unverified
                return mean, std
            fn = _zeros
        else:
            fn = self.predict_fn
        return _StubSurrogate(_predict_fn=fn, metadata={
            "n_train": len(X_train),
            "dim": len(X_train[0]) if X_train else 0,
            "hyperparams": hyperparams or {},
            **self.metadata,
        })


# --------------------------------------------------------------------------
# Reproduction check (for when a real second backend exists)
# --------------------------------------------------------------------------

@dataclass
class ReproductionCheckResult:
    """Result of comparing primary vs. second backend predictions."""

    backend_a: str
    backend_b: str
    n_points: int
    n_exact_match: int      # bit-exact (or within tolerance)
    mean_abs_diff: float
    max_abs_diff: float
    tolerance: float
    passed: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "backend_a": self.backend_a,
            "backend_b": self.backend_b,
            "n_points": self.n_points,
            "n_exact_match": self.n_exact_match,
            "match_rate": round(self.n_exact_match / max(1, self.n_points), 6),
            "mean_abs_diff": round(self.mean_abs_diff, 6),
            "max_abs_diff": round(self.max_abs_diff, 6),
            "tolerance": self.tolerance,
            "passed": self.passed,
        }


def compare_predictions(
    preds_a: List[List[float]],
    preds_b: List[List[float]],
    backend_a: str,
    backend_b: str,
    tolerance: float = 1e-4,
) -> ReproductionCheckResult:
    """Compare two (mean, std) prediction tensors element-wise.

    tol=1e-4 for continuous floats (bit-exact is unrealistic across libraries).
    Match rate ≥ 0.85 is the kpi-sci-external-reproduction target.
    """
    if len(preds_a) != len(preds_b):
        raise ValueError("prediction lists must have same length")
    n = len(preds_a)
    if n == 0:
        return ReproductionCheckResult(backend_a, backend_b, 0, 0, 0.0, 0.0, tolerance, False)

    total = 0
    exact = 0
    sum_abs = 0.0
    max_abs = 0.0
    for pa, pb in zip(preds_a, preds_b):
        if len(pa) != len(pb):
            raise ValueError(f"prediction width mismatch: {len(pa)} vs {len(pb)}")
        for va, vb in zip(pa, pb):
            total += 1
            d = abs(float(va) - float(vb))
            sum_abs += d
            if d > max_abs:
                max_abs = d
            if d <= tolerance:
                exact += 1
    return ReproductionCheckResult(
        backend_a=backend_a,
        backend_b=backend_b,
        n_points=total,
        n_exact_match=exact,
        mean_abs_diff=sum_abs / total,
        max_abs_diff=max_abs,
        tolerance=tolerance,
        passed=(exact / total) >= 0.85,
    )


# --------------------------------------------------------------------------
# Self-test
# --------------------------------------------------------------------------

def _selftest() -> int:
    # Test 1: stub backend returns zeros with inf uncertainty when no predictor
    backend = ReferenceSecondBackend()
    surrogate = backend.fit_surrogate([[0.0], [1.0]], [0.1, 0.2])
    mean, std = surrogate.predict([[0.5], [1.5]])
    assert mean == [[0.0], [0.0]], "stub should return zero mean"
    assert std == [[float("inf")], [float("inf")]], "stub should return inf std"
    assert surrogate.properties()["provenance"] == "not-independent"
    print("[PASS] stub backend returns inf-uncertainty when no predictor wired")

    # Test 2: stub with injected predictor passes predictions through
    def _pred(X: List[List[float]]) -> Tuple[List[List[float]], List[List[float]]]:
        return [[x[0] * 2.0] for x in X], [[0.1] for _ in X]
    backend2 = ReferenceSecondBackend(predict_fn=_pred)
    surrogate2 = backend2.fit_surrogate([[1.0], [2.0]], [2.0, 4.0])
    m2, s2 = surrogate2.predict([[3.0]])
    assert m2 == [[6.0]], f"injected predictor mismatch: {m2}"
    print("[PASS] stub backend passes through injected predictor")

    # Test 3: compare_predictions returns match_rate=1.0 for identical inputs
    preds = [[1.0, 2.0], [3.0, 4.0]]
    r = compare_predictions(preds, preds, "a", "b")
    match_rate = r.n_exact_match / r.n_points
    assert r.passed and match_rate == 1.0 and r.max_abs_diff == 0.0
    print(f"[PASS] compare_predictions on identical inputs → {match_rate:.0%} match")

    # Test 4: compare_predictions detects disagreement
    preds_b = [[1.0 + 0.1, 2.0], [3.0, 4.0 + 0.2]]
    r2 = compare_predictions(preds, preds_b, "a", "b", tolerance=1e-4)
    match_rate2 = r2.n_exact_match / r2.n_points
    assert not r2.passed
    assert r2.n_exact_match == 2 and r2.n_points == 4
    print(f"[PASS] compare_predictions detects {match_rate2:.0%} match (below 85% threshold)")

    # Test 5: metadata flow
    backend3 = ReferenceSecondBackend(metadata={"source": "custom"})
    s3 = backend3.fit_surrogate([[0.0]], [0.0], hyperparams={"ls": 0.5})
    props = s3.properties()
    assert props["source"] == "custom"
    assert props["hyperparams"] == {"ls": 0.5}
    print("[PASS] metadata + hyperparams flow into surrogate.properties()")

    print("\n== 5/5 selftest passed ==")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_selftest())
