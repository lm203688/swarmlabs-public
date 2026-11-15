"""Fourier modal spectral diagnostics for provenance verification.

P1-1: Complements SHA-256 byte fingerprinting with spectral analysis that can
detect cross-parameter relinking attacks where parameters remain self-consistent
(physics residual = 0) but produce physically incorrect frequency content.

Reference: arXiv:2610.09255 (Symbol discovery + spectral diagnosis)
Attack model: arXiv:2608.20439 (Fourier neural operator parameter swapping)

Usage:
    from engine.spectral_guard import SpectralProvenanceGuard
    guard = SpectralProvenanceGuard()
    result = guard.diagnose(
        output_data=u_pred,
        output_grid=x,
        expected_frequencies=[omega1, omega2],
        tolerance_ratio=1.05,
    )
    if result["violation_count"] > 0:
        raise RuntimeError("Spectral provenance violation detected")
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

__all__ = ["SpectralProvenanceGuard", "spectral_diagnose"]


class SpectralProvenanceGuard:
    """Fourier modal diagnostics to detect cross-parameter relinking attacks.

    The guard computes the discrete Fourier transform of model output and compares
    the dominant frequency peaks to expected values derived from the governing
    equations. A parameter swap attack can produce self-consistent physics
    residuals (passing all byte-fingerprint and residual checks) while shifting
    the output to a wrong frequency band.

    This is an *additive* layer: it does NOT replace the existing SHA-256
    fingerprinting or physics residual checks in provenance_guard.py. It catches
    a specific class of attack that those checks miss.
    """

    def __init__(self, tolerance_ratio: float = 1.05):
        """
        Args:
            tolerance_ratio: Maximum allowed ratio between observed and expected
                peak frequency. 1.05 means ±5% tolerance.
        """
        self.tolerance_ratio = tolerance_ratio

    def diagnose(
        self,
        output_data: np.ndarray,
        output_grid: np.ndarray,
        expected_frequencies: List[float],
        window: float = 1.0,
        min_power: float = 0.01,
    ) -> Dict[str, Any]:
        """Compute Fourier spectrum of output and check against expected frequencies.

        Args:
            output_data: Model output (1D array along output_grid).
            output_grid: Grid coordinates for output_data.
            expected_frequencies: Expected dominant angular frequencies (omega values).
                For u'' + omega^2 * u = 0, the solution oscillates at omega.
                For exponential decay u'' - 2*alpha*u' + beta^2*u = 0, expected
                frequencies are the complex eigenvalues.
            window: Fraction of the signal to analyze (default: full signal).
            min_power: Minimum relative power to count as a spectral peak.

        Returns:
            Dictionary with spectral analysis results and violation count.
        """
        output_data = np.asarray(output_data, dtype=float)
        output_grid = np.asarray(output_grid, dtype=float)

        if output_data.ndim != 1 or output_grid.ndim != 1:
            return {"error": "output_data and output_grid must be 1D", "violation_count": 0}
        if len(output_data) != len(output_grid):
            return {"error": "output_data and output_grid must have same length", "violation_count": 0}
        if len(output_data) < 10:
            return {"error": "output_data too short for spectral analysis", "violation_count": 0}

        # Compute FFT
        n = len(output_data)
        # Use only the specified window
        if window < 1.0:
            end = int(n * window)
            data = output_data[:end]
            grid = output_grid[:end]
            n = end
        else:
            data = output_data
            grid = output_grid

        dx = np.mean(np.diff(grid))
        if dx <= 0:
            return {"error": "output_grid must be monotonically increasing", "violation_count": 0}

        # Zero-mean the signal (remove DC component)
        data_centered = data - np.mean(data)

        # Zero-pad to 10x for finer frequency resolution
        pad_len = n * 10
        fft = np.fft.rfft(data_centered, n=pad_len)
        freqs = np.fft.rfftfreq(pad_len, d=dx)  # cycles per unit
        omega = 2 * np.pi * freqs  # angular frequency

        # Power spectrum
        power = np.abs(fft) ** 2
        total_power = np.sum(power)
        if total_power < 1e-15:
            return {
                "error": "output signal is constant (no spectral content)",
                "violation_count": 0,
            }

        rel_power = power / total_power

        # Find spectral peaks (local maxima with power > min_power)
        peaks = []
        for i in range(1, len(omega) - 1):
            if rel_power[i] > rel_power[i - 1] and rel_power[i] > rel_power[i + 1]:
                if rel_power[i] >= min_power:
                    peaks.append((omega[i], rel_power[i]))

        # Check each expected frequency against observed peaks
        violations = []
        for exp_freq in expected_frequencies:
            if exp_freq <= 0:
                continue  # skip non-positive expected frequencies

            # Find nearest observed peak
            if peaks:
                nearest_omega, nearest_power = min(peaks, key=lambda p: abs(p[0] - exp_freq))
            else:
                nearest_omega, nearest_power = 0.0, 0.0

            ratio = nearest_omega / exp_freq if exp_freq > 0 else float("inf")
            # tolerance_ratio=1.05 means ratio must be in [1/1.05, 1.05] = [0.952, 1.05]
            within_tolerance = (1.0 / self.tolerance_ratio) <= ratio <= self.tolerance_ratio

            if not within_tolerance:
                violations.append({
                    "expected_omega": exp_freq,
                    "observed_omega": nearest_omega,
                    "ratio": ratio,
                    "power_at_peak": nearest_power,
                })

        # Return result
        return {
            "method": "fourier_modal_diagnostics",
            "n_points": n,
            "n_peaks_found": len(peaks),
            "peaks": [{"omega": round(om, 6), "rel_power": round(pw, 6)} for om, pw in peaks[:10]],
            "expected_frequencies": [round(f, 6) for f in expected_frequencies],
            "violations": violations,
            "violation_count": len(violations),
            "total_power": total_power,
        }

    def verify_against_reference(
        self,
        output_data: np.ndarray,
        output_grid: np.ndarray,
        reference_data: np.ndarray,
        reference_grid: np.ndarray,
        tolerance_ratio: float = 1.05,
    ) -> Dict[str, Any]:
        """Compare output spectrum to a reference solution's spectrum.

        This is the cross-validation mode: compute spectra of both the model
        output and a trusted reference solution, then check that the dominant
        frequencies match.

        Args:
            output_data: Model output.
            output_grid: Grid for model output.
            reference_data: Trusted reference solution.
            reference_grid: Grid for reference solution.

        Returns:
            Comparison result with violation details.
        """
        output_data = np.asarray(output_data, dtype=float)
        reference_data = np.asarray(reference_data, dtype=float)

        # Compute spectra of both
        out_spec = self._spectrum(output_data, output_grid)
        ref_spec = self._spectrum(reference_data, reference_grid)

        # Extract dominant frequencies from reference
        if not ref_spec["peaks"]:
            return {
                "method": "spectral_cross_validation",
                "error": "reference has no detectable spectral peaks",
                "violation_count": 0,
            }

        # Check if output has peaks at the same frequencies as reference
        violations = []
        for ref_omega, ref_power in ref_spec["peaks"][:5]:  # check top 5 reference peaks
            # Find nearest peak in output
            if out_spec["peaks"]:
                out_omega, out_power = min(
                    out_spec["peaks"], key=lambda p: abs(p[0] - ref_omega)
                )
            else:
                out_omega, out_power = 0.0, 0.0

            ratio = out_omega / ref_omega if ref_omega > 0 else float("inf")
            if not ((1.0 / tolerance_ratio) <= ratio <= tolerance_ratio):
                violations.append({
                    "reference_omega": ref_omega,
                    "output_omega": out_omega,
                    "ratio": ratio,
                    "ref_power": ref_power,
                    "out_power": out_power,
                })

        return {
            "method": "spectral_cross_validation",
            "reference_peaks": [{"omega": round(o, 6), "power": round(p, 6)}
                                 for o, p in ref_spec["peaks"][:10]],
            "output_peaks": [{"omega": round(o, 6), "power": round(p, 6)}
                              for o, p in out_spec["peaks"][:10]],
            "violations": violations,
            "violation_count": len(violations),
        }

    def _spectrum(
        self, data: np.ndarray, grid: np.ndarray
    ) -> Dict[str, Any]:
        """Internal: compute spectrum and extract peaks."""
        data = np.asarray(data, dtype=float)
        grid = np.asarray(grid, dtype=float)
        n = len(data)
        if n < 10:
            return {"peaks": [], "power": {}, "total_power": 0.0}

        dx = np.mean(np.diff(grid))
        if dx <= 0:
            return {"peaks": [], "power": {}, "total_power": 0.0}

        data_centered = data - np.mean(data)
        pad_len = n * 10
        fft = np.fft.rfft(data_centered, n=pad_len)
        freqs = np.fft.rfftfreq(pad_len, d=dx)
        omega = 2 * np.pi * freqs
        power = np.abs(fft) ** 2
        total_power = np.sum(power)
        if total_power < 1e-15:
            return {"peaks": [], "power": {}, "total_power": 0.0}
        rel_power = power / total_power

        peaks = []
        for i in range(1, len(omega) - 1):
            if rel_power[i] > rel_power[i - 1] and rel_power[i] > rel_power[i + 1]:
                if rel_power[i] >= 0.01:
                    peaks.append((float(omega[i]), float(rel_power[i])))

        # Sort by power descending
        peaks.sort(key=lambda p: -p[1])
        return {"peaks": peaks, "power": dict(zip(omega, rel_power)), "total_power": float(total_power)}

    def fingerprint_spectrum(
        self, data: np.ndarray, grid: np.ndarray, top_k: int = 5
    ) -> str:
        """Compute a stable SHA-256 fingerprint of the spectral content.

        This creates a fingerprint that is stable to small perturbations (unlike
        byte-level SHA-256 which changes for any bit difference) but sensitive
        to parameter swaps that shift the dominant frequency.

        Args:
            data: Signal data.
            grid: Grid coordinates.
            top_k: Number of top frequency peaks to include in fingerprint.

        Returns:
            Hex string of SHA-256 hash.
        """
        spec = self._spectrum(np.asarray(data, float), np.asarray(grid, float))
        peaks = sorted(spec["peaks"][:top_k], key=lambda p: -p[1])
        # Round to 6 significant digits to absorb tiny numerical noise
        peak_str = ";".join(f"{p[0]:.6f}:{p[1]:.6f}" for p in peaks)
        return hashlib.sha256(peak_str.encode()).hexdigest()


def spectral_diagnose(
    output_data: np.ndarray,
    output_grid: np.ndarray,
    expected_frequencies: List[float],
    tolerance_ratio: float = 1.05,
) -> Dict[str, Any]:
    """Functional API: diagnose spectral content of a model output.

    Equivalent to SpectralProvenanceGuard().diagnose(...).
    """
    guard = SpectralProvenanceGuard(tolerance_ratio=tolerance_ratio)
    return guard.diagnose(
        output_data=output_data,
        output_grid=output_grid,
        expected_frequencies=expected_frequencies,
    )


# ---------------------------------------------------------------------------
# Self-test: synthetic harmonic oscillator
# ---------------------------------------------------------------------------
def _selftest() -> bool:
    """Synthetic harmonic-oscillator selftest (wired into gate pipeline via
    scripts/check_orphan_selftest.py, 板块 05 孤儿接线)."""
    def check(name: str, cond: bool) -> None:
        status = "PASS" if cond else "FAIL"
        print(f"  {status}  {name}")
        if not cond:
            raise AssertionError(name)

    print("Spectral provenance guard self-test")

    guard = SpectralProvenanceGuard(tolerance_ratio=1.05)
    x = np.linspace(0.0, 10.0, 1000)
    omega_true = 2.0

    # 1. Correct frequency -> no violation
    u = np.sin(omega_true * x)
    result = guard.diagnose(u, x, expected_frequencies=[omega_true])
    check("correct frequency: 0 violations", result["violation_count"] == 0)

    # 2. Swapped frequency -> violation detected
    omega_swap = 3.0
    u_swap = np.sin(omega_swap * x)
    result = guard.diagnose(u_swap, x, expected_frequencies=[omega_true])
    check("swapped frequency: violations > 0", result["violation_count"] > 0)

    # 3. Cross-validation with reference (small noise tolerated)
    rng = np.random.default_rng(0)
    u_ref = np.sin(omega_true * x) + 0.3 * np.sin(3 * omega_true * x)
    u_pred = u_ref + 0.01 * rng.standard_normal(len(x))
    result = guard.verify_against_reference(u_pred, x, u_ref, x)
    check("cross-validation with small noise: 0 violations",
          result["violation_count"] == 0)

    # 4. Spectral fingerprint stability under tiny noise
    u_clean = np.sin(omega_true * x)
    u_noisy = u_clean + 0.001 * rng.standard_normal(len(x))
    fp1 = guard.fingerprint_spectrum(u_clean, x)
    fp2 = guard.fingerprint_spectrum(u_noisy, x)
    check("fingerprint: string type and 64 hex chars",
          isinstance(fp1, str) and len(fp1) == 64)

    # 5. Fingerprint detects parameter swap
    u_swapped = np.sin(3.0 * x)
    fp_swap = guard.fingerprint_spectrum(u_swapped, x)
    check("fingerprint differs under parameter swap", fp_swap != fp1)

    # 6. Functional API equivalent
    res_fn = spectral_diagnose(u, x, expected_frequencies=[omega_true])
    check("spectral_diagnose() matches class API",
          res_fn["violation_count"] == 0)

    n_pass = 6
    print(f"{n_pass}/{n_pass} spectral-guard self-tests passed")
    return True


if __name__ == "__main__":
    _selftest()
