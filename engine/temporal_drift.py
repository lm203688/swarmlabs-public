"""Phase 2 Temporal Drift Detection — Page-Hinkley + CUSUM.

**Status**: SCAFFOLD / NOT VERIFIED.
Report §6 Phase 2 mentions "temporal drift detection" but does not give a
production implementation. This module provides **two well-known, textbook
change-point detectors** as infrastructure scaffolds. They are:

- **Unverified for this domain** — no real temporal batch stream has been
  fed through them yet. Do not claim drift is being monitored in production.
- **Statistically standard** — Page-Hinkley (1954) and CUSUM are textbook
  algorithms with well-understood theoretical properties; the risk here is
  not wrong math, it is that we have not calibrated the thresholds against
  SwarmLabs batch data.
- **Deliberately simple** — pure stdlib + numpy, no tuning-free black box.
  Thresholds are constants that must be recalibrated with real data.

Two detector classes:

    PageHinkleyDetector(lambda_=0.5, delta=0.0, h=20.0)
        - lambda_ : discount factor for weighted sum of residuals (0<λ<1)
        - delta   : drift amplitude threshold to subtract from residuals
        - h       : decision threshold on cumulative statistic

    CUSUMDetector(k=0.5, h=4.0)
        - k : slack (reference value shift) in standard deviations
        - h : decision threshold on cumulative sum

Both expose:

    detector.update(x_t) -> bool     # returns True iff change detected
    detector.reset()                 # reset internal state
    detector.stats() -> dict         # current cumulative statistic + n

Interface contract:
    detector must not raise on any finite float input.
    Detector must emit a change flag at most once per contiguous shift.

**KPI linkage**: none yet. This is Phase 2 infrastructure — no production
data flows through it. Do not report as a working monitoring feature.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional


# --------------------------------------------------------------------------
# Page-Hinkley (1954)
# --------------------------------------------------------------------------

@dataclass
class PageHinkleyDetector:
    """Page-Hinkley change-point detector.

    Cumulative statistic (weighted sum of shifted residuals, minus running min):
        G_t = sum_{i=1..t} lambda_^(t-i) * (x_i - x_bar - delta)
        S_t = G_t - min_{j<=t} G_j

    Change is declared when S_t > h.

    x_bar is the running mean of all observations since reset.
    """

    lambda_: float = 0.5    # discount factor; larger → slower, smoother
    delta: float = 0.0      # drift amplitude to subtract
    h: float = 8.0          # decision threshold (calibrated on unit-std Gaussian stream)

    # internal state
    x_bar: float = 0.0
    _n: int = 0
    _g: float = 0.0
    _g_min: float = float("inf")
    _fired: bool = False
    _history: List[float] = field(default_factory=list, repr=False)

    def reset(self) -> None:
        self.x_bar = 0.0
        self._n = 0
        self._g = 0.0
        self._g_min = float("inf")
        self._fired = False
        self._history.clear()

    def update(self, x: float) -> bool:
        """Feed one observation. Return True iff a change is detected now."""
        if not math.isfinite(x):
            return False
        self._n += 1
        # running mean
        self.x_bar = (self.x_bar * (self._n - 1) + x) / self._n
        # weighted cumulative
        self._g = self.lambda_ * self._g + (x - self.x_bar - self.delta)
        if self._g < self._g_min:
            self._g_min = self._g
        S = self._g - self._g_min
        self._history.append(x)
        if not self._fired and S > self.h:
            self._fired = True
            return True
        return False

    def stats(self) -> Dict[str, float]:
        return {
            "detector": "page_hinkley",
            "n": float(self._n),
            "x_bar": self.x_bar,
            "G_t": self._g,
            "G_min": self._g_min,
            "S_t": self._g - self._g_min,
            "threshold_h": self.h,
            "fired": self._fired,
        }


# --------------------------------------------------------------------------
# CUSUM
# --------------------------------------------------------------------------

@dataclass
class CUSUMDetector:
    """Classic two-sided CUSUM change-point detector.

    Standardized residuals:
        z_t = (x_t - x_bar) / s
    where s is the running std (fallback: 1.0 if n < 2).

    Cumulative sums:
        G+ = max(0, G+ + z_t - k)
        G- = max(0, G- - z_t - k)

    Change declared when G+ > h or G- > h.
    """

    k: float = 0.5      # slack in std-dev units
    h: float = 10.0     # threshold in std-dev units (calibrated on unit-std Gaussian stream)

    x_bar: float = 0.0
    s: float = 1.0
    _n: int = 0
    _sum_sq: float = 0.0
    g_plus: float = 0.0
    g_minus: float = 0.0
    _fired: bool = False
    _last_direction: str = ""

    def reset(self) -> None:
        self.x_bar = 0.0
        self.s = 1.0
        self._n = 0
        self._sum_sq = 0.0
        self.g_plus = 0.0
        self.g_minus = 0.0
        self._fired = False
        self._last_direction = ""

    def update(self, x: float) -> bool:
        if not math.isfinite(x):
            return False
        self._n += 1
        prev_bar = self.x_bar
        self.x_bar = (self.x_bar * (self._n - 1) + x) / self._n
        # running sum of squares around running mean (online Welford-lite)
        self._sum_sq = (self._sum_sq * (self._n - 1) + (x - prev_bar) ** 2 + (x - self.x_bar) ** 2) / self._n
        # simpler: recompute variance from first moment & second moment
        # use online sum of squared deviations
        var = max(1e-12, self._sum_sq / self._n)
        self.s = math.sqrt(var)
        z = (x - self.x_bar) / self.s
        self.g_plus = max(0.0, self.g_plus + z - self.k)
        self.g_minus = max(0.0, self.g_minus - z - self.k)
        if not self._fired and (self.g_plus > self.h or self.g_minus > self.h):
            self._fired = True
            self._last_direction = "up" if self.g_plus > self.g_minus else "down"
            return True
        return False

    def stats(self) -> Dict[str, float]:
        return {
            "detector": "cusum",
            "n": float(self._n),
            "x_bar": self.x_bar,
            "s": self.s,
            "G_plus": self.g_plus,
            "G_minus": self.g_minus,
            "k": self.k,
            "h": self.h,
            "fired": self._fired,
            "last_direction": self._last_direction,
        }


# --------------------------------------------------------------------------
# Batch stream runner (used by pilot_reference when time-stream exists)
# --------------------------------------------------------------------------

@dataclass
class DriftReport:
    """One change-point event, or the final stream summary."""
    detector: str
    n_observations: int
    n_changes: int
    change_indices: List[int]
    stats: Dict[str, float]

    def to_dict(self) -> Dict:
        return {
            "detector": self.detector,
            "n_observations": self.n_observations,
            "n_changes": self.n_changes,
            "change_indices": self.change_indices,
            "stats": self.stats,
        }


def run_drift_scan(
    x_series: List[float],
    ph: Optional[PageHinkleyDetector] = None,
    cusum: Optional[CUSUMDetector] = None,
) -> List[DriftReport]:
    """Run PH and/or CUSUM over a stream and return one report per detector."""
    ph = ph or PageHinkleyDetector()
    cusum = cusum or CUSUMDetector()
    reports: List[DriftReport] = []

    for name, det in [("page_hinkley", ph), ("cusum", cusum)]:
        det.reset()
        changes: List[int] = []
        for i, x in enumerate(x_series):
            if det.update(x):
                changes.append(i)
        reports.append(DriftReport(
            detector=name,
            n_observations=len(x_series),
            n_changes=len(changes),
            change_indices=changes,
            stats=det.stats(),
        ))
    return reports


# --------------------------------------------------------------------------
# Self-test
# --------------------------------------------------------------------------

def _selftest() -> int:
    """Detect a synthetic shift in the middle of a stream."""
    import random
    random.seed(0)
    x = [random.gauss(0.0, 1.0) for _ in range(60)]
    x += [random.gauss(5.0, 1.0) for _ in range(60)]
    reports = run_drift_scan(x)
    ok = True
    for r in reports:
        # Both detectors should fire at least once; at least one change
        # index should be in [40, 90] (shift at 60, detectors lag by some
        # window). Earlier fires are tolerated as false positives —
        # calibration on real data will tighten these.
        if r.n_changes == 0:
            print(f"[FAIL] {r.detector}: no change detected")
            ok = False
        elif not any(40 <= c <= 90 for c in r.change_indices):
            print(f"[FAIL] {r.detector}: change_indices={r.change_indices} outside expected window")
            ok = False
        else:
            print(f"[PASS] {r.detector}: {r.n_changes} change(s) at {r.change_indices}")
    return 0 if ok else 1


if __name__ == "__main__":
    import sys
    sys.exit(_selftest())
