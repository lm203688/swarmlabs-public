#!/usr/bin/env python
"""N4 · Monod 非线性拟合（Zenodo 19416053 真实 36 条件 E. coli 数据）。

原实现的问题（评估报告 §硬科技提升）：
1. 原 mu_max = 0.002667 标为 "mean of 36"，但 Monod 的 mu_max 是最大可能比速
   增长率（对应 S→∞ 的饱和值），不是"平均"。取平均掩盖了数据分布。
2. 原 K_s = 0.1 g/L 恰等于最小测试葡萄糖（0.1%），是左截断（left-censored）：
   拟合无法区分 K_s < 0.1 与 K_s = 0.1，报告应给出上界 "< 0.1 g/L"，
   不是点估计。
3. 没有残差、置信区间、可辨识性诊断。

本脚本的正确做法：
1. 解析 36 个真实 (T, S) 条件的 d(OD)/dt 时间序列。
2. 对每个条件，用指数期（前 30% 时间窗内正值最大处）取 mu 观测值。
3. 分温度做 Monod 拟合并报告每个温度的可辨识性；再做全数据 Arrhenius 联合拟合。
4. 若拟合 K_s 触底至数据下界，明标 "left-censored: K_s < {S_min}"，不用点估计。
5. 残差、CI、RMSE、R² 全写入 data/real_anchors.json 的 monod_fit 块。

用法：
    python scripts/monod_curve_fit.py                    # 使用缓存的 xlsx
    python scripts/monod_curve_fit.py --download         # 重新下载 Zenodo
    python scripts/monod_curve_fit.py --input .tmp_z/glucose_1pct.xlsx
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
import openpyxl
from scipy.optimize import curve_fit

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
TMP_DIR = ROOT / ".tmp_z"
XLSX_PATH = TMP_DIR / "glucose_1pct.xlsx"
ZENODO_URL = (
    "https://zenodo.org/api/records/19416053/files/"
    "Porcentaje_glucosa_1hoja.xlsx/content"
)
ZENODO_RECORD = "https://zenodo.org/records/19416053"


# ─────────────────────────────────────────────────────────────────────────
# Monod 模型
# ─────────────────────────────────────────────────────────────────────────

def monod(S: np.ndarray, mu_max: float, K_s: float) -> np.ndarray:
    """Monod: mu = mu_max · S / (K_s + S)."""
    S = np.asarray(S, dtype=float)
    return mu_max * S / (K_s + S)


def arrhenius_mu_max(T_celsius: float, mu_ref: float, E_a: float,
                     T_ref_celsius: float = 37.0) -> float:
    """Arrhenius temperature scaling of mu_max: mu_max(T) = mu_ref · exp(-E_a·(1/T - 1/T_ref))."""
    R = 8.314  # J/(mol·K)
    T_K = T_celsius + 273.15
    T_ref_K = T_ref_celsius + 273.15
    return mu_ref * np.exp(-E_a / R * (1.0 / T_K - 1.0 / T_ref_K))


def monod_arrhenius(S: np.ndarray, T_celsius: np.ndarray,
                    mu_ref: float, K_s: float, E_a: float) -> np.ndarray:
    """Monod with Arrhenius temperature scaling. Returns mu values."""
    S = np.asarray(S, dtype=float)
    T = np.asarray(T_celsius, dtype=float)
    mu_max_T = arrhenius_mu_max(T, mu_ref, E_a)
    return mu_max_T * S / (K_s + S)


def _monod_arrhenius_stacked(X: np.ndarray, mu_ref: float, K_s: float,
                              E_a: float) -> np.ndarray:
    """Wrapper for curve_fit: X has shape (2, N) = [S, T]."""
    S, T = X[0], X[1]
    return monod_arrhenius(S, T, mu_ref, K_s, E_a)


# ─────────────────────────────────────────────────────────────────────────
# 数据解析
# ─────────────────────────────────────────────────────────────────────────

def _ensure_xlsx(download: bool = False) -> Path:
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    if not download and XLSX_PATH.exists() and XLSX_PATH.stat().st_size > 1000:
        return XLSX_PATH
    print(f"[monod] downloading Zenodo file → {XLSX_PATH}")
    req = urllib.request.Request(ZENODO_URL, method="GET")
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
    XLSX_PATH.write_bytes(raw)
    print(f"[monod] downloaded {len(raw)} bytes")
    return XLSX_PATH


def parse_conditions(xlsx_path: Path) -> List[Dict[str, Any]]:
    """Parse the wide 'Ensayos' sheet into a list of (T, S, [dOD/dt over time]) conditions.

    Layout: 144 columns = 36 groups × 4 columns per group
           (Temperatura, %glucosa, tcultivo_min, d(OD)/dt).
    Rows = time steps.
    """
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    ws = wb["Ensayos"]
    rows = list(ws.iter_rows(values_only=True))
    header = rows[0]
    n_groups = len(header) // 4

    # Each group has its own (T, S) label from column headers
    conditions: List[Dict[str, Any]] = []
    for g in range(n_groups):
        base = g * 4
        cols = header[base:base + 4]
        # Header row may repeat the group label; use first data row to get T, S
        first_data = rows[1][base:base + 4] if len(rows) > 1 else None
        if first_data is None:
            continue
        T = first_data[0]
        S_pct = first_data[1]
        if T is None or S_pct is None:
            continue
        # Time series for this group
        time_series = []
        for r_idx in range(1, len(rows)):
            row = rows[r_idx][base:base + 4]
            t_val = row[2]
            d_od = row[3]
            if t_val is None or d_od is None:
                continue
            try:
                time_series.append((float(t_val), float(d_od)))
            except (TypeError, ValueError):
                continue
        conditions.append({
            "temperature_C": float(T),
            "glucose_pct": float(S_pct),
            "glucose_gL": float(S_pct),  # % 直接当 g/L，沿用原 anchor 口径
            "time_series": time_series,
            "n_timepoints": len(time_series),
        })
    return conditions


def estimate_mu_per_condition(cond: Dict[str, Any]) -> Tuple[float, float, float]:
    """Estimate specific growth rate mu for a (T, S) condition.

    Approach: take the maximum of positive d(OD)/dt over the exponential window.
    The exponential window is defined as timepoints before the growth curve
    flattens — approximate as timepoints where d(OD)/dt > 0 and their time is
    in the first 40% of the total elapsed time.

    Returns: (mu_obs, mu_max_of_series, time_of_max).
    """
    ts = cond["time_series"]
    if not ts:
        return float("nan"), float("nan"), float("nan")
    times = np.array([t for t, _ in ts])
    d_od = np.array([d for _, d in ts])
    t_end = times.max()
    # Exponential-phase window: first 40% of elapsed time, positive derivatives only
    mask = (times <= 0.4 * t_end) & (d_od > 0)
    if not np.any(mask):
        # Fallback: max over all positive
        pos = d_od > 0
        if not np.any(pos):
            return float("nan"), float("nan"), float("nan")
        idx = np.argmax(np.where(pos, d_od, -np.inf))
        return float(d_od[idx]), float(np.max(d_od[pos])), float(times[idx])
    vals = d_od[mask]
    times_masked = times[mask]
    idx_local = int(np.argmax(vals))
    return (
        float(vals[idx_local]),
        float(np.max(d_od[np.where(d_od > 0)])) if np.any(d_od > 0) else float("nan"),
        float(times_masked[idx_local]),
    )


# ─────────────────────────────────────────────────────────────────────────
# 拟合
# ─────────────────────────────────────────────────────────────────────────

def _ci_from_cov(params, pcov, level=0.95):
    """Two-sided CI from parameter covariance (normal approx)."""
    n, k = len(params), len(params)
    if pcov is None or k != len(pcov):
        return [None] * k
    from scipy.stats import norm
    z = norm.ppf(1 - (1 - level) / 2)
    se = np.sqrt(np.diag(pcov))
    return [(float(p) - z * float(se[i]), float(p) + z * float(se[i]))
            for i, p in enumerate(params)]


def fit_monod_at_temperature(S_arr: np.ndarray, mu_arr: np.ndarray,
                             S_min_tested: float) -> Dict[str, Any]:
    """Fit Monod at one temperature. Returns params + CI + identifiability."""
    p0 = (float(np.max(mu_arr) * 1.1), float(np.median(S_arr) * 0.5))
    try:
        params, pcov = curve_fit(
            monod, S_arr, mu_arr, p0=p0,
            bounds=([1e-8, 1e-8], [float("inf"), float("inf")]),
            maxfev=20000,
        )
        fitted = monod(S_arr, *params)
        resid = mu_arr - fitted
        ss_res = float(np.sum(resid ** 2))
        ss_tot = float(np.sum((mu_arr - mu_arr.mean()) ** 2))
        r2 = 1 - ss_res / ss_tot if ss_tot > 0 else float("nan")
        rmse = float(np.sqrt(np.mean(resid ** 2)))
        ci = _ci_from_cov(params, pcov)
    except Exception as e:
        return {"ok": False, "error": str(e), "S": S_arr.tolist(), "mu": mu_arr.tolist()}

    mu_max_hat, K_s_hat = params
    left_censored = bool(K_s_hat <= S_min_tested * 1.05)  # within 5% of lower bound
    return {
        "ok": True,
        "mu_max": float(mu_max_hat),
        "mu_max_ci95": list(ci[0]),
        "K_s": float(K_s_hat),
        "K_s_ci95": list(ci[1]),
        "S_min_tested": float(S_min_tested),
        "left_censored": left_censored,
        "K_s_reported": f"< {S_min_tested}" if left_censored else f"{K_s_hat:.4g}",
        "r2": r2,
        "rmse": rmse,
        "n_points": int(len(S_arr)),
        "residuals": resid.tolist(),
    }


def fit_arrhenius_monod(S_all: np.ndarray, T_all: np.ndarray,
                        mu_all: np.ndarray, S_min_tested: float
                        ) -> Dict[str, Any]:
    """Joint Arrhenius-Monod fit across all temperatures."""
    p0 = (float(np.max(mu_all) * 1.1), 0.5, 4000.0)  # E_a J/mol ~ 40-80 kJ/mol for mesophiles
    # Stack S and T into a single xdata for curve_fit
    X_all = np.vstack([S_all, T_all])
    try:
        params, pcov = curve_fit(
            _monod_arrhenius_stacked, X_all, mu_all, p0=p0,
            bounds=([1e-8, 1e-8, 1000.0], [float("inf"), float("inf"), 100000.0]),
            maxfev=50000,
        )
        fitted = _monod_arrhenius_stacked(X_all, *params)
        resid = mu_all - fitted
        ss_res = float(np.sum(resid ** 2))
        ss_tot = float(np.sum((mu_all - mu_all.mean()) ** 2))
        r2 = 1 - ss_res / ss_tot if ss_tot > 0 else float("nan")
        rmse = float(np.sqrt(np.mean(resid ** 2)))
        ci = _ci_from_cov(params, pcov)
        mu_ref, K_s_hat, E_a = params
    except Exception as e:
        return {"ok": False, "error": str(e)}

    left_censored = bool(K_s_hat <= S_min_tested * 1.05)
    return {
        "ok": True,
        "mu_ref_at_37C": float(mu_ref),
        "mu_ref_ci95": list(ci[0]),
        "K_s": float(K_s_hat),
        "K_s_ci95": list(ci[1]),
        "E_a_J_per_mol": float(E_a),
        "E_a_ci95": list(ci[2]),
        "E_a_kJ_per_mol": float(E_a / 1000.0),
        "S_min_tested": float(S_min_tested),
        "left_censored": left_censored,
        "K_s_reported": f"< {S_min_tested}" if left_censored else f"{K_s_hat:.4g}",
        "r2": r2,
        "rmse": rmse,
        "n_points": int(len(S_all)),
    }


# ─────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=None, help="path to xlsx (default: .tmp_z/glucose_1pct.xlsx)")
    ap.add_argument("--download", action="store_true", help="force re-download from Zenodo")
    ap.add_argument("--output", default=str(DATA_DIR / "real_anchors.json"))
    args = ap.parse_args()

    xlsx = Path(args.input) if args.input else _ensure_xlsx(download=args.download)
    print(f"[monod] input: {xlsx}")

    conditions = parse_conditions(xlsx)
    print(f"[monod] parsed {len(conditions)} (T, S) conditions")
    temps = sorted({c["temperature_C"] for c in conditions})
    glucoses = sorted({c["glucose_gL"] for c in conditions})
    print(f"[monod] temperatures: {temps}°C")
    print(f"[monod] glucose g/L: {glucoses}")

    # Estimate mu per condition
    mu_data = []
    for c in conditions:
        mu_obs, mu_max_series, t_max = estimate_mu_per_condition(c)
        mu_data.append({
            "T_C": c["temperature_C"],
            "S_gL": c["glucose_gL"],
            "mu_obs": mu_obs,
            "mu_max_series": mu_max_series,
            "t_at_max_min": t_max,
            "n_timepoints": c["n_timepoints"],
        })

    print(f"[monod] extracted mu for {sum(1 for m in mu_data if not np.isnan(m['mu_obs']))} "
          f"of {len(mu_data)} conditions")

    # Per-temperature Monod fits (honest per-T)
    per_temp_fits = {}
    for T in temps:
        sub = [m for m in mu_data if m["T_C"] == T and not np.isnan(m["mu_obs"])]
        if len(sub) < 3:
            per_temp_fits[str(T)] = {"ok": False, "error": f"only {len(sub)} valid points"}
            continue
        S = np.array([m["S_gL"] for m in sub])
        mu = np.array([m["mu_obs"] for m in sub])
        per_temp_fits[str(T)] = fit_monod_at_temperature(S, mu, S_min_tested=glucoses[0])

    # Joint Arrhenius-Monod fit
    valid = [m for m in mu_data if not np.isnan(m["mu_obs"])]
    S_all = np.array([m["S_gL"] for m in valid])
    T_all = np.array([m["T_C"] for m in valid])
    mu_all = np.array([m["mu_obs"] for m in valid])
    joint = fit_arrhenius_monod(S_all, T_all, mu_all, S_min_tested=glucoses[0])

    # Summary stats
    mu_max_hat = None
    if joint.get("ok"):
        # Effective mu_max at each temperature from joint fit
        mu_maxes_by_T = [arrhenius_mu_max(t, joint["mu_ref_at_37C"], joint["E_a_J_per_mol"])
                         for t in temps]
        mu_max_hat = float(np.max(mu_maxes_by_T))
        mu_max_at_29 = float(arrhenius_mu_max(29.0, joint["mu_ref_at_37C"],
                                                joint["E_a_J_per_mol"]))
    else:
        mu_maxes_by_T = []
        mu_max_at_29 = None

    fit_summary = {
        "n_conditions_total": len(conditions),
        "n_conditions_with_mu": len(valid),
        "n_temperatures": len(temps),
        "n_glucose_levels": len(glucoses),
        "temperatures_C": temps,
        "glucose_gL": glucoses,
        "S_min_tested_gL": glucoses[0],
        "mu_obs_range": [float(np.min(mu_all)), float(np.max(mu_all))],
        "mean_mu_obs": float(np.mean(mu_all)),
        "joint_arrhenius_monod": joint,
        "per_temperature_fits": per_temp_fits,
        "mu_max_at_29C_from_joint": mu_max_at_29,
        "mu_max_peak_across_temps": mu_max_hat,
    }

    # Identifiability warning
    ident_warn = []
    if joint.get("ok") and joint["left_censored"]:
        ident_warn.append(
            f"K_s is left-censored at S_min={glucoses[0]} g/L. Report as "
            f"'K_s < {glucoses[0]} g/L' (upper bound), not as point estimate. "
            f"Fitted K_s={joint['K_s']:.4g} sits at the data boundary."
        )
    ident_warn.append(
        "Only 4 glucose levels tested (0.1, 0.5, 1.0, 2.0 g/L) — extremely "
        "sparse for a 2-parameter Monod fit. K_s and mu_max are strongly "
        "correlated; use per-condition curve_fit only as descriptive, not as "
        "confirmatory."
    )
    fit_summary["identifiability_warnings"] = ident_warn

    # Old vs new comparison
    old_values = {"mu_max_old_mean_of_36": 0.002667, "K_s_old_hardcoded": 0.1}
    fit_summary["old_values_for_comparison"] = old_values
    if joint.get("ok"):
        fit_summary["old_vs_new"] = {
            "mu_max_old_mean": 0.002667,
            "mu_max_new_at_29C_from_joint": mu_max_at_29,
            "mu_max_new_peak_across_temps": mu_max_hat,
            "K_s_old_reported_as_point": 0.1,
            "K_s_new_reported": joint["K_s_reported"],
            "K_s_left_censored": joint["left_censored"],
        }

    # Write to real_anchors.json
    out_path = Path(args.output)
    d = json.loads(out_path.read_text(encoding="utf-8"))

    # Update anchor mu_max_real notes to reflect the corrected semantics
    for a in d.get("anchors", []):
        if a.get("id") == "ecoli_monod_mu_max_real":
            a["value"] = round(mu_max_at_29, 6) if mu_max_at_29 else a.get("value")
            a["method"] = "scipy.optimize.curve_fit on 36 real conditions, Monod+Arrhenius"
            a["notes"] = (
                f"Mu_max at 29°C from joint Arrhenius-Monod fit on 36 real (T, S) conditions "
                f"from Zenodo 19416053. NOT a mean of 36 — curve_fit estimates mu_ref @ 37°C "
                f"then Arrhenius-corrects to 29°C. Original value 0.002667 was labelled 'mean of 36' "
                f"which misrepresents Monod semantics. Full fit metadata below in monod_fit block."
            )
        if a.get("id") == "ecoli_monod_ks_real":
            if joint.get("ok") and joint["left_censored"]:
                a["value"] = None
                a["reportable_as"] = f"< {glucoses[0]} g/L (left-censored upper bound)"
                a["method"] = "scipy.optimize.curve_fit, but K_s hit data lower bound"
                a["notes"] = (
                    f"Original K_s = 0.1 g/L equals the smallest glucose tested. "
                    f"Monod fit returns K_s={joint['K_s']:.4g} g/L which sits at the "
                    f"data boundary — CANNOT distinguish K_s < 0.1 from K_s = 0.1. "
                    f"Report as upper bound '< {glucoses[0]} g/L', NOT a point estimate. "
                    f"This is left-censoring, not identifiability failure of the fit itself."
                )

    d["monod_fit"] = fit_summary
    out_path.write_text(json.dumps(d, ensure_ascii=False, indent=2, default=str),
                         encoding="utf-8")
    print(f"[monod] wrote {out_path}")

    # Print summary
    print()
    print("=" * 60)
    print("MONOD FIT SUMMARY")
    print("=" * 60)
    print(f"  n_conditions: {fit_summary['n_conditions_with_mu']} / "
          f"{fit_summary['n_conditions_total']}")
    if joint.get("ok"):
        print(f"  mu_ref @ 37°C : {joint['mu_ref_at_37C']:.6g} "
              f"(95% CI {joint['mu_ref_ci95'][0]:.4g}–{joint['mu_ref_ci95'][1]:.4g})")
        print(f"  E_a           : {joint['E_a_kJ_per_mol']:.2f} kJ/mol "
              f"(95% CI {joint['E_a_ci95'][0]/1000:.2f}–{joint['E_a_ci95'][1]/1000:.2f})")
        print(f"  K_s           : {joint['K_s_reported']} g/L  "
              f"{'[LEFT-CENSORED]' if joint['left_censored'] else ''}")
        print(f"  R²           : {joint['r2']:.4f}  RMSE: {joint['rmse']:.6g}")
        if mu_max_at_29:
            print(f"  mu_max @ 29°C : {mu_max_at_29:.6g} /min  (vs old mean 0.002667)")
    else:
        print(f"  joint fit FAILED: {joint.get('error')}")
    print()
    print("  Identifiability warnings:")
    for w in ident_warn:
        print(f"    - {w}")
    return 0 if joint.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
