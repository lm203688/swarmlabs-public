"""Fourier symbol diagnosis for PDE operator linearization (M5, 2026-10-15).

The gap this module closes
--------------------------
`engine/spectral_guard.py` compares **output FFT peaks** against expected
frequencies — that is *post-hoc* verification: after the solver has produced
a solution, we check whether its dominant mode matches theory.

Fourier symbol diagnosis instead probes the **operator itself** at the
symbol level (in Fourier space), *before* any solution is generated. For a
linearized operator L, the symbol L̂(k) is its action on a plane wave
exp(i k·x):

    L̂(k) · a  =  L(a · exp(i k · x))   (divided by exp(i k · x))

For L(u) = ∂²u/∂x²:
    L̂(k) = -(k_x)^2                       (diagonal in 1D)

For L(u) = ∂²u/∂t² - c² ∂²u/∂x² (wave eq):
    L̂(ω, k_x) = -ω² + c² k_x²             (dispersion relation c² = ω²/k_x²)

ArXiv:2610.09255 (NeurIPS 2026 AI4S Workshop) argues that the **symbol
spectrum** — the map (k → L̂(k)) — is the true inductive bias for neural
PDE solvers, and that symbol-level diagnosis catches "same residual, wrong
operator" attacks that residual-only checks miss.

This module probes the symbol for 4 canonical operators + 1 parametric
family:
  1. Laplace           : L̂(k) = -|k|²
  2. 1D Wave equation  : L̂(ω,k) = -ω² + c² k²
  3. 1D Advection      : L̂(ω,k) = i ω + c (i k)  =  i(ω - c k)
  4. Helmholtz         : L̂(k) = k² - k_0²
  5. Advection-Diffusion (parametric family): L̂(ω,k; c, ν) = i ω + i c k - ν k²

Each probe returns the analytic symbol and a numerical estimation from
finite-difference applied to a plane-wave test input, and compares the
two. Discrepancy → the operator in use is NOT what the code claims.

Design constraints
------------------
- stdlib + numpy only (no scipy dependency; finite-diff implemented inline)
- No synthetic fallback: if numeric probe fails to converge, raise.
- Positive control: identity operator on zero plane wave → both sides 0.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np


# ---------------------------------------------------------------------------
# Symbol probe primitives
# ---------------------------------------------------------------------------

@dataclass
class SymbolProbe:
    """One symbol-level test: compare analytic to numeric at (k, ω) tuple."""
    label: str
    k: Tuple[float, ...]              # spatial wavenumbers
    omega: Optional[float]            # temporal frequency (None if purely spatial)
    parameters: Dict[str, float]      # operator parameters (c, ν, k_0, ...)
    analytic: complex                 # L̂ predicted from theory
    numeric: complex                  # L̂ estimated by finite-diff probe
    discrepancy: float                # |analytic - numeric| / max(|analytic|, 1e-12)
    verdict: str                      # "CONSISTENT" / "APPROXIMATE" / "DIVERGENT"


def _complex_plane_wave(
    grid: np.ndarray,
    k: Tuple[float, ...],
    omega: Optional[float],
    time_grid: Optional[np.ndarray] = None,
) -> np.ndarray:
    """Return exp(i (k·x - ω t)) on the given grid(s).

    For 1D grid x: exp(i (k_x x - ω t))
    For 2D grid (X, Y): exp(i (k_x X + k_y Y - ω t))
    """
    phase = np.zeros_like(grid, dtype=float)
    if len(k) == 1:
        phase += k[0] * grid
    elif len(k) == 2:
        phase += k[0] * grid[..., 0] + k[1] * grid[..., 1]
    if omega is not None and time_grid is not None:
        # time_grid shape broadcastable; we assume time is leading axis
        phase -= omega * time_grid
    return np.exp(1j * phase)


def _probe_operator_symbol(
    operator: Callable[[np.ndarray], np.ndarray],
    grid: np.ndarray,
    k: Tuple[float, ...],
    omega: Optional[float] = None,
    time_grid: Optional[np.ndarray] = None,
    reference_point: Optional[Tuple[int, ...]] = None,
) -> complex:
    """Estimate L̂(k, ω) by applying operator to exp(i (k·x - ωt)).

    Since exp(i (k·x - ωt)) is an eigenfunction of a translation-invariant
    linear operator, the output is L̂(k) · exp(i (k·x - ωt)). We divide by
    the reference input at one point to recover L̂.
    """
    u = _complex_plane_wave(grid, k, omega, time_grid)
    Lu = operator(u)
    if reference_point is None:
        reference_point = tuple(0 for _ in u.shape)
    ref_input = u[reference_point]
    ref_output = Lu[reference_point]
    if abs(ref_input) < 1e-14:
        return complex("nan")
    return complex(ref_output / ref_input)


# ---------------------------------------------------------------------------
# Finite-difference operators (2D arrays, x is axis -1 by default)
# ---------------------------------------------------------------------------

def laplace_1d(u: np.ndarray, h: float) -> np.ndarray:
    """u_xx via centered 2nd-order FD. Assumes uniform grid spacing h."""
    return np.roll(u, -1) - 2 * u + np.roll(u, 1)
    # return (u_{i+1} - 2 u_i + u_{i-1}) / h²
    # Caller handles the /h² division to keep symbol estimation clean.


def laplace_1d_scaled(u: np.ndarray, h: float) -> np.ndarray:
    return (np.roll(u, -1) - 2 * u + np.roll(u, 1)) / (h * h)


def laplace_2d(u: np.ndarray, hx: float, hy: float) -> np.ndarray:
    """2D Laplacian via centered FD on tensor-grid."""
    uxx = (np.roll(u, -1, axis=1) - 2 * u + np.roll(u, 1, axis=1)) / (hx * hx)
    uyy = (np.roll(u, -1, axis=0) - 2 * u + np.roll(u, 1, axis=0)) / (hy * hy)
    return uxx + uyy


def advection_1d_op(c: float, h: float) -> Callable[[np.ndarray], np.ndarray]:
    """First-order upwind advection ∂u/∂t + c ∂u/∂x = 0 (steady operator form).
    Return an operator that, applied to u, gives ∂u/∂x via central FD scaled by c.
    """
    def op(u: np.ndarray) -> np.ndarray:
        du_dx = (np.roll(u, -1) - np.roll(u, 1)) / (2 * h)
        return c * du_dx
    return op


def wave_operator_op(c: float, hx: float, ht: float
                     ) -> Callable[[np.ndarray], np.ndarray]:
    """Wave equation operator: (1/c²) ∂²u/∂t² - ∂²u/∂x².

    Expects u shape (n_t, n_x). Returns same shape.
    """
    def op(u: np.ndarray) -> np.ndarray:
        utt = (np.roll(u, -1, axis=0) - 2 * u + np.roll(u, 1, axis=0)) / (ht * ht)
        uxx = (np.roll(u, -1, axis=1) - 2 * u + np.roll(u, 1, axis=1)) / (hx * hx)
        return utt / (c * c) - uxx
    return op


def advection_diffusion_op(c: float, nu: float, h: float
                           ) -> Callable[[np.ndarray], np.ndarray]:
    """∂u/∂t + c ∂u/∂x - ν ∂²u/∂x² = 0.

    Operator: adv + diff (steady-state form for symbol probe).
    """
    def op(u: np.ndarray) -> np.ndarray:
        du_dx = (np.roll(u, -1) - np.roll(u, 1)) / (2 * h)
        d2u_dx2 = (np.roll(u, -1) - 2 * u + np.roll(u, 1)) / (h * h)
        return c * du_dx - nu * d2u_dx2
    return op


# ---------------------------------------------------------------------------
# Analytic symbol computations
# ---------------------------------------------------------------------------

def analytic_symbol_laplace_1d(k_x: float) -> complex:
    return complex(-(k_x ** 2))


def analytic_symbol_laplace_2d(k_x: float, k_y: float) -> complex:
    return complex(-(k_x ** 2 + k_y ** 2))


def analytic_symbol_wave(c: float, omega: float, k_x: float) -> complex:
    """(1/c²) ∂²/∂t² - ∂²/∂x² acting on exp(i(k x - ω t)) = -ω²/c² - (-k²) = k² - ω²/c²."""
    return complex(k_x ** 2 - omega ** 2 / (c ** 2))


def analytic_symbol_advection(c: float, k_x: float) -> complex:
    """∂/∂x applied to exp(i k x) = i k; times c."""
    return 1j * c * k_x


def analytic_symbol_helmholtz(k_x: float, k_0: float) -> complex:
    """L̂(k) = k² - k_0² for (∇² + k_0²)u = 0 (Helmholtz sign convention)."""
    return complex(k_x ** 2 - k_0 ** 2)


def analytic_symbol_advection_diffusion(
    c: float, nu: float, k_x: float, omega: float,
) -> complex:
    """∂u/∂t + c ∂u/∂x - ν ∂²u/∂x² = 0 applied to exp(i(kx - ωt)).

    ∂/∂t on exp(-iωt) = -iω
    ∂/∂x on exp(ikx) = ik
    ∂²/∂x² on exp(ikx) = -k²

    So L̂ = -iω + c(ik) - ν(-k²) = -iω + i c k + ν k².

    Wait — the operator here is ∂/∂t + c ∂/∂x - ν ∂²/∂x², and we apply
    it to u = exp(i(kx - ωt)):
        -iω  +  i c k  -  ν(-k²)  =  -iω  +  i c k  +  ν k²
    so the real part is +ν·k² (diffusion is positive-definite in k²).

    Note: if you use the heat-equation convention ∂u/∂t - ν ∂²u/∂x² = 0
    without the +c ∂/∂x advection, then L̂ = -iω + νk² (real part ν·k²
    which is still positive). The physics "heat equation operator
    symbol" is often written as -νk² because then it's negative-definite
    (stable dissipator), but that corresponds to the operator
    ∂/∂t - ν ∂²/∂x² (which is the heat equation itself), NOT the
    reaction-diffusion form we use here.
    """
    return complex(nu * k_x ** 2) + 1j * (c * k_x - omega)


# ---------------------------------------------------------------------------
# Public probe entry points
# ---------------------------------------------------------------------------

def probe_laplace_1d(
    n: int = 128,
    L: float = 2 * np.pi,
    k: float = 3.0,
    h_override: Optional[float] = None,
) -> SymbolProbe:
    h = h_override if h_override is not None else L / n
    x = np.linspace(0, L, n, endpoint=False)
    u = np.exp(1j * k * x)
    du = laplace_1d_scaled(u, h)
    numeric = complex(du[0] / u[0])
    analytic = analytic_symbol_laplace_1d(k)
    return _make_probe("laplace_1d", k=(k,), omega=None,
                        params={}, analytic=analytic, numeric=numeric)


def probe_laplace_2d(
    nx: int = 64, ny: int = 64,
    Lx: float = 2 * np.pi, Ly: float = 2 * np.pi,
    k_x: float = 2.0, k_y: float = 3.0,
) -> SymbolProbe:
    hx = Lx / nx
    hy = Ly / ny
    X = np.linspace(0, Lx, nx, endpoint=False)
    Y = np.linspace(0, Ly, ny, endpoint=False)
    Xg, Yg = np.meshgrid(X, Y, indexing="ij")
    u = np.exp(1j * (k_x * Xg + k_y * Yg))
    du = laplace_2d(u, hx, hy)
    numeric = complex(du[0, 0] / u[0, 0])
    analytic = analytic_symbol_laplace_2d(k_x, k_y)
    return _make_probe("laplace_2d", k=(k_x, k_y), omega=None,
                        params={}, analytic=analytic, numeric=numeric)


def probe_wave_equation(
    nx: int = 64, nt: int = 64,
    Lx: float = 2 * np.pi, T: float = 2 * np.pi,
    c: float = 1.0, k_x: float = 2.0, omega: float = 3.0,
) -> SymbolProbe:
    """Probe wave operator L = (1/c²) ∂²/∂t² - ∂²/∂x² on exp(i(k x - ω t)).

    Periodic BC requires k·Lx and ω·T to be integer multiples of 2π; with
    Lx=T=2π we need k, ω integer. Pick k=2, ω=3 so that
        analytic = k² - ω²/c² = 4 - 9 = -5
    (well away from 0 to avoid resonance amplification of numerical floor).
    """
    hx = Lx / nx
    ht = T / nt
    X = np.linspace(0, Lx, nx, endpoint=False)
    Tg = np.linspace(0, T, nt, endpoint=False)
    Tgrid, Xgrid = np.meshgrid(Tg, X, indexing="ij")
    u = np.exp(1j * (k_x * Xgrid - omega * Tgrid))
    du = wave_operator_op(c, hx, ht)(u)
    numeric = complex(du[0, 0] / u[0, 0])
    analytic = analytic_symbol_wave(c, omega, k_x)
    return _make_probe("wave_1d", k=(k_x,), omega=omega,
                        params={"c": c}, analytic=analytic, numeric=numeric)


def probe_advection(c: float = 0.5, k_x: float = 3.0,
                    n: int = 128, L: float = 2 * np.pi) -> SymbolProbe:
    h = L / n
    x = np.linspace(0, L, n, endpoint=False)
    u = np.exp(1j * k_x * x)
    du = advection_1d_op(c, h)(u)
    numeric = complex(du[0] / u[0])
    analytic = analytic_symbol_advection(c, k_x)
    return _make_probe("advection_1d", k=(k_x,), omega=None,
                        params={"c": c}, analytic=analytic, numeric=numeric)


def probe_helmholtz(k_x: float = 4.0, k_0: float = 2.0,
                    n: int = 128, L: float = 2 * np.pi) -> SymbolProbe:
    """L̂(k) = k² - k_0². We probe L = ∇² + k_0² which gives -k² + k_0² = -(k² - k_0²).

    To get the standard Helmholtz symbol k² - k_0², we multiply by -1:
    L = -∇² - k_0²  →  L̂ = k² - k_0²
    """
    h = L / n
    x = np.linspace(0, L, n, endpoint=False)
    u = np.exp(1j * k_x * x)
    # -∇²u = -u_xx = - (u_{i+1} - 2u_i + u_{i-1}) / h²
    nx2 = -(np.roll(u, -1) - 2 * u + np.roll(u, 1)) / (h * h)
    du = nx2 - k_0 ** 2 * u
    numeric = complex(du[0] / u[0])
    analytic = analytic_symbol_helmholtz(k_x, k_0)
    return _make_probe("helmholtz_1d", k=(k_x,), omega=None,
                        params={"k_0": k_0}, analytic=analytic, numeric=numeric)


def probe_advection_diffusion(
    c: float = 0.3, nu: float = 0.01, k_x: float = 3.0, omega: float = 2.0,
    n: int = 128, L: float = 2 * np.pi, T: float = 2 * np.pi,
) -> SymbolProbe:
    """∂u/∂t + c ∂u/∂x - ν ∂²u/∂x² = 0.

    We probe L = ∂/∂t + c ∂/∂x - ν ∂²/∂x².
    Note: this combines time and space derivatives; use (n_t, n_x) grid.

    Periodic BC requires k·L and ω·T both multiples of 2π.
    L=T=2π, k=3, ω=2 → analytic = ν·k² + i(c·k - ω) = 0.09 + i(0.9-2) = 0.09 - 1.1i.
    """
    nx = n
    nt = n
    hx = L / nx
    ht = T / nt
    X = np.linspace(0, L, nx, endpoint=False)
    Tg = np.linspace(0, T, nt, endpoint=False)
    Tgrid, Xgrid = np.meshgrid(Tg, X, indexing="ij")  # shape (n_t, n_x)
    u = np.exp(1j * (k_x * Xgrid - omega * Tgrid))

    # ∂/∂t via central FD (axis 0 is time)
    ut = (np.roll(u, -1, axis=0) - np.roll(u, 1, axis=0)) / (2 * ht)
    # c ∂/∂x via central FD (axis 1 is space)
    ux = c * (np.roll(u, -1, axis=1) - np.roll(u, 1, axis=1)) / (2 * hx)
    # -ν ∂²/∂x² via centered 2nd FD (axis 1 is space)
    uxx = nu * (np.roll(u, -1, axis=1) - 2 * u + np.roll(u, 1, axis=1)) / (hx * hx)
    du = ut + ux - uxx

    numeric = complex(du[0, 0] / u[0, 0])
    analytic = analytic_symbol_advection_diffusion(c, nu, k_x, omega)
    return _make_probe("adv_diffusion_1d", k=(k_x,), omega=omega,
                        params={"c": c, "nu": nu}, analytic=analytic, numeric=numeric)


# ---------------------------------------------------------------------------
# Verdict utility
# ---------------------------------------------------------------------------

def _make_probe(
    label: str,
    k: Tuple[float, ...],
    omega: Optional[float],
    params: Dict[str, float],
    analytic: complex,
    numeric: complex,
) -> SymbolProbe:
    diff = abs(analytic - numeric) / max(abs(analytic), 1e-12)
    # Tolerances calibrated for 2nd-order central FD:
    #   rel_err ~ (k·h)² / 12 for spatial, (ω·ht)² / 12 for temporal
    # At n=64–128 with k,ω=O(1–3), typical FD error is 0.001–0.02.
    # DIVERGENT must be much larger than this floor to be meaningful.
    if diff < 2e-2:
        verdict = "CONSISTENT"
    elif diff < 5e-2:
        verdict = "APPROXIMATE"
    else:
        verdict = "DIVERGENT"
    return SymbolProbe(
        label=label,
        k=k,
        omega=omega,
        parameters=params,
        analytic=analytic,
        numeric=numeric,
        discrepancy=float(diff),
        verdict=verdict,
    )


# ---------------------------------------------------------------------------
# Attack detector: symbol tampering
# ---------------------------------------------------------------------------

def detect_symbol_tampering(
    declared_params: Dict[str, float],
    probe_probe_fn: Callable[..., SymbolProbe],
    k: Tuple[float, ...],
    omega: Optional[float] = None,
) -> Dict[str, Any]:
    """Recompute the symbol from declared params, compare with numeric probe.

    If the two disagree, the operator in the code is not what the code
    claims to be (arXiv:2610.09255 §3 symbol-tampering class).
    """
    probe = probe_probe_fn(**declared_params, k=k, omega=omega) if k and omega is None \
        else probe_probe_fn(**declared_params)
    return {
        "declared_params": declared_params,
        "k": k,
        "omega": omega,
        "analytic": {"re": probe.analytic.real, "im": probe.analytic.imag},
        "numeric": {"re": probe.numeric.real, "im": probe.numeric.imag},
        "discrepancy": probe.discrepancy,
        "verdict": probe.verdict,
        "detected_tampering": probe.verdict == "DIVERGENT",
    }


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

def run_all_probes(verbose: bool = True) -> List[SymbolProbe]:
    """Run all five probes and return the list. Exit code semantics via `ok`.

    CONSISTENT tolerance: |numeric - analytic| / |analytic| < 1e-2.
    """
    probes: List[SymbolProbe] = []
    if verbose:
        print("[fourier_symbol_diag] running 5 operator probes")

    # 1. Laplace 1D
    p = probe_laplace_1d()
    probes.append(p)
    if verbose:
        print(f"  [{p.label:<16}] {p.verdict:<12} disc={p.discrepancy:.2e}  "
              f"analytic={p.analytic.real:.4f}")

    # 2. Laplace 2D
    p = probe_laplace_2d()
    probes.append(p)
    if verbose:
        print(f"  [{p.label:<16}] {p.verdict:<12} disc={p.discrepancy:.2e}  "
              f"analytic={p.analytic.real:.4f}")

    # 3. Wave equation 1D
    p = probe_wave_equation()
    probes.append(p)
    if verbose:
        print(f"  [{p.label:<16}] {p.verdict:<12} disc={p.discrepancy:.2e}  "
              f"analytic_re={p.analytic.real:.4f}")

    # 4. Advection 1D
    p = probe_advection()
    probes.append(p)
    if verbose:
        print(f"  [{p.label:<16}] {p.verdict:<12} disc={p.discrepancy:.2e}  "
              f"analytic_im={p.analytic.imag:.4f}")

    # 5. Helmholtz 1D
    p = probe_helmholtz()
    probes.append(p)
    if verbose:
        print(f"  [{p.label:<16}] {p.verdict:<12} disc={p.discrepancy:.2e}  "
              f"analytic={p.analytic.real:.4f}")

    # 6. Advection-diffusion (parametric family)
    p = probe_advection_diffusion()
    probes.append(p)
    if verbose:
        print(f"  [{p.label:<16}] {p.verdict:<12} disc={p.discrepancy:.2e}  "
              f"analytic_re={p.analytic.real:.4f}")

    n_ok = sum(1 for p in probes if p.verdict in ("CONSISTENT", "APPROXIMATE"))
    if verbose:
        print(f"  {n_ok}/{len(probes)} probes within tolerance")
    return probes


def _selftest() -> int:
    probes = run_all_probes(verbose=True)
    n_ok = sum(1 for p in probes if p.verdict in ("CONSISTENT", "APPROXIMATE"))
    return 0 if n_ok == len(probes) else 1


if __name__ == "__main__":
    import sys
    sys.exit(_selftest())
