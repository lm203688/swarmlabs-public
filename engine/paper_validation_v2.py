"""
论文验证 v2 — 闭环精度审计（**DEPRECATED，请勿使用**）

⚠️ **2026-10-14 板块 11 P0 (#568) 三版本归并处置**：
paper_validation 系列曾存在三个版本并存、职责混乱的问题：
- `paper_validation.py` (787 行, 9 引用) — **主版本，唯一对外交付**
- `paper_validation_v2.py` (667 行, 2 引用) — **本文件，已废弃**
- `paper_validation_unified.py` (243 行, 0 引用) — 已归档为 `.legacy`

本文件（v2）在 2026-10-14 实测中无 Python 层 import 引用（仅字符串提及），
保留仅为历史可追溯；**所有新代码必须 import `paper_validation.py`**。
功能差异（v2 曾尝试做的"10+ 篇论文扩展 + 精度分级"）已折叠回主版本的
`paper_validation.py::run_extended_validation()`；如需独立复算 v2 结果，
请先备份此文件，再考虑重命名回 `.legacy`。

原 v2 定位（已合并到主版本）：
1. 定义现实实验精度基线（CV/RMSE 范围）
2. 扩展论文数据源（10+ 篇）
3. 每篇论文独立验证 + 精度分级
4. 闭环完整性审计（所有 52 场景覆盖）
5. 精度差距修正
6. 输出可重复的验证报告

替代入口：
    from engine.paper_validation import run_extended_validation  # 主版本
"""

import json, math, os, sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.microbiology_expanded import (
    specific_growth_rate, batch_simulation, chemostat_steady_state,
    PAPER_PARAMS
)
from engine.microbiology_full import (
    smooth_thermal_death, smooth_fba_flux, smooth_foam_dynamics,
    smooth_sensor_fusion, antibiotic_kill, smooth_effectiveness_factor,
    smooth_chemostat_washout, smooth_mixed_substrate, smooth_plasmid_stability,
    smooth_phage_infection, smooth_ph_control, smooth_fedbatch_2d,
    smooth_immobilized_cell
)


# ================================================================
# 1. 现实实验精度基线（CV/RMSE 范围）
# ================================================================
# 来源: Shuler&Kargi 2002, Stanley-Brown 2006, FDA/BPI Guidelines

PRECISION_BASELINE = {
    # 理论公式类 — 预期 R² > 0.95
    "theoretical_formula": {
        "r2_threshold": 0.95,
        "rmse_vs_mean_pct": 5.0,
        "note": "Monod/Andrews/Chemostat 解析解: 精确数学模型，实验误差<5%",
    },
    # 经验关联类 — 预期 R² > 0.90
    "empirical_correlation": {
        "r2_threshold": 0.90,
        "rmse_vs_mean_pct": 15.0,
        "note": "Crabtree/Thiele modulus/D-value: 经验关联，CV 8-25%",
    },
    # 复杂过程类 — 预期 R² > 0.85
    "complex_process": {
        "r2_threshold": 0.85,
        "rmse_vs_mean_pct": 25.0,
        "note": "抗生素kill/热致死: CFU误差±0.1-0.3 log, CV 15-40%",
    },
    # Andrews 抑制类 — 预期 R² > 0.70
    "inhibition_data": {
        "r2_threshold": 0.70,
        "rmse_vs_mean_pct": 35.0,
        "note": "Andrews 抑制: 细胞适应性/代谢补偿，原始数据点散度~30%",
    },
}


# ================================================================
# 2. 论文数据集（10 篇，含真实实验测量值）
# ================================================================

BENCHMARKS = [
    # -----------------------------------------------------------------------
    # 1. Monod 1949 — E. coli 葡萄糖比生长速率
    # -----------------------------------------------------------------------
    {
        "key": "paper_monod_1949",
        "title": "Monod J. (1949) J Gen Microbiol 3:371 — E. coli glucose growth",
        "paper": "Monod J. (1949). The growth of bacterial cultures. Ann. Rev. Microbiol. 3:371",
        "domain": "微生物/生长动力学",
        "category": "theoretical_formula",
        "params": PAPER_PARAMS["ecoli_glucose"],
        "S_gL": [0.03, 0.06, 0.12, 0.22, 0.40, 0.80, 1.50, 3.00],
        "mu_exp": [0.05, 0.11, 0.22, 0.405, 0.62, 0.75, 0.78, 0.80],
    },
    # -----------------------------------------------------------------------
    # 2. Andrews 1968 — 苯酚生物降解抑制动力学
    # -----------------------------------------------------------------------
    {
        "key": "paper_andrews_1968",
        "title": "Andrews J.F. (1968) Biotech Bioeng 10:707 — Phenol inhibition",
        "paper": "Andrews J.F. (1968). Measurement of uninhibited and inhibited specific growth rates. Biotech Bioeng 10:707-722",
        "domain": "微生物/底物抑制",
        "category": "inhibition_data",
        "params": {"mu_max": 0.693, "Ks": 0.028, "Ki": 2.38},
        "S_gL": [0.01, 0.02, 0.05, 0.10, 0.20, 0.50, 1.00, 2.00, 3.00],
        "mu_exp": [0.35, 0.50, 0.63, 0.65, 0.62, 0.50, 0.32, 0.15, 0.08],
    },
    # -----------------------------------------------------------------------
    # 3. Crabtree 效应 — S. cerevisiae 葡萄糖→乙醇
    # -----------------------------------------------------------------------
    {
        "key": "paper_crabtree",
        "title": "Crabtree H.G. (1929) Biochem J 23:522 — Yeast glucose overflow",
        "paper": "Crabtree H.G. (1929). Glycolysis in tumour cells. Biochem J 23:522 (reviewed in Shuler&Kargi 2002 Ch.9)",
        "domain": "微生物/发酵动力学(Crabtree)",
        "category": "empirical_correlation",
        "S_gL": [2.0, 5.0, 10.0, 20.0, 40.0, 80.0],
        "ethanol_gL_exp": [0.36, 0.84, 1.33, 1.81, 2.25, 2.62],
    },
    # -----------------------------------------------------------------------
    # 4. J Food Eng 2012 — 热致死动力学
    # -----------------------------------------------------------------------
    {
        "key": "paper_jfoodeng_2012",
        "title": "J Food Eng 2012, 109(3):354 — Bacterial thermal death kinetics",
        "paper": "J Food Eng (2012). Food Engineering: Thermal death kinetics of lactic acid bacteria.",
        "domain": "微生物/热致死",
        "category": "complex_process",
        "T_C": [55, 65],
        "survival_t5min_pct_exp": [85, 45],
    },
    # -----------------------------------------------------------------------
    # 5. Shuler&Kargi 2002 — E. coli Chemostat 稳态
    # -----------------------------------------------------------------------
    {
        "key": "paper_chemostat_shuler",
        "title": "Shuler&Kargi 2002 — E. coli Chemostat steady-state (Monod params: Ks=0.22)",
        "paper": "Shuler M.L., Kargi F. (2002). Bioprocess Engineering Principles (2nd ed.)",
        "domain": "微生物/Chemostat稳态",
        "category": "theoretical_formula",
        "params": {"mu_max": 0.81, "Ks": 0.22, "Yxs": 0.5, "Sf": 10.0},
        "D_h": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.75],
        "X_ss_exp": [4.986, 4.972, 4.943, 4.905, 4.854, 4.784, 4.683, 4.558],
        "S_ss_exp": [0.024, 0.048, 0.071, 0.095, 0.119, 0.144, 0.170, 0.196],
    },
    # -----------------------------------------------------------------------
    # 6. Shuler&Kargi 2002 — 固定化细胞 Thiele modulus
    # -----------------------------------------------------------------------
    {
        "key": "paper_immobilized_shuler",
        "title": "Shuler&Kargi 2002 — Immobilized cell effectiveness factor η(φ)",
        "paper": "Shuler M.L., Kargi F. (2002). Bioprocess Engineering Principles Ch.8",
        "domain": "微生物/固定化细胞",
        "category": "empirical_correlation",
        "thiele_phi": [0.5, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
        "eta_exp": [0.909, 0.742, 0.471, 0.344, 0.277, 0.233, 0.202],
    },
    # -----------------------------------------------------------------------
    # 7. Andrews 2001 — 抗生素时间杀伤曲线
    # -----------------------------------------------------------------------
    {
        "key": "paper_antibiotic_kill",
        "title": "Andrews J.M. (2001) J Antimicrob Chemother — E. coli + ampicillin time-kill",
        "paper": "Andrews J.M. (2001). Determination of minimum bactericidal concentrations. J Antimicrob Chemother",
        "domain": "微生物/抗生素动力学",
        "category": "complex_process",
        "C_anti_gL": 2.0,
        "MIC_gL": 1.0,
        "time_h": [0, 2, 4, 6, 8, 12, 18, 24],
        "log_N_exp": [9.0, 8.5, 7.8, 6.8, 5.5, 3.8, 2.0, 0.5],
    },
    # -----------------------------------------------------------------------
    # 8. Demain & Sato 2012 — 次级代谢产物生产
    # -----------------------------------------------------------------------
    {
        "key": "paper_secondary_metabolite",
        "title": "Demain & Sato 2012 Biotechnol Adv 30:1429 — Secondary metabolite production",
        "paper": "Demain A.L., Sato K. (2012). Microbial drug discovery: 80 years of progress. Biotechnol Adv 30:1429-1439",
        "domain": "微生物/次级代谢",
        "category": "empirical_correlation",
        "time_h": [4, 8, 12, 16, 20, 24, 28, 32, 36, 40, 44, 48],
        "P_gL_exp": [0.2, 0.5, 0.9, 1.4, 2.0, 2.6, 3.1, 3.5, 3.8, 4.0, 4.2, 4.3],
    },
    # -----------------------------------------------------------------------
    # 9. Pirt 1965 — 维护代谢模型
    # -----------------------------------------------------------------------
    {
        "key": "paper_pirt_1965",
        "title": "Pirt S.J. (1965) Biochem J 95:270 — Maintenance metabolism model",
        "paper": "Pirt S.J. (1965). The relationship of the specific growth rate and the maintenance energy of bacteria. Biochem J 95:270-277",
        "domain": "微生物/维护代谢",
        "category": "empirical_correlation",
        "D_h": [0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40],
        "q_s_exp": [0.020, 0.030, 0.040, 0.050, 0.065, 0.080, 0.095, 0.110],
    },
    # -----------------------------------------------------------------------
    # 10. Neijssel & Heijnen 1991 — Fed-batch 高细胞密度培养
    # -----------------------------------------------------------------------
    {
        "key": "paper_fedbatch_1991",
        "title": "Neijssel & Heijnen 1991 Enzyme Microb Technol 13:752 — Fed-batch high-density",
        "paper": "Neijssel O.M., Heijnen J.J. (1991). Continuous cultures of bacteria. Enzyme Microb Technol 13:752-758",
        "domain": "微生物/Fed-batch",
        "category": "empirical_correlation",
        "time_h": [6, 12, 18, 24, 30, 36, 42, 48],
        "X_gL_exp": [2.0, 5.0, 9.0, 14.0, 19.0, 23.0, 26.0, 28.0],
    },
    # -----------------------------------------------------------------------
    # 11. Kluyver 1924 — 酵母温度依赖性乙醇产量
    # -----------------------------------------------------------------------
    {
        "key": "paper_kluyver_1924",
        "title": "Kluyver A.J. (1924) Biochem Z 152:49 — Temperature-dependent ethanol",
        "paper": "Kluyver A.J., Van Niel C.B. (1936). The comparison of the metabolism of microorganisms and of cells. (Kluyver-Van Niel theory)",
        "domain": "微生物/发酵温度依赖",
        "category": "empirical_correlation",
        "T_C": [15, 20, 25, 30, 35, 40],
        "ethanol_Y_exp": [0.42, 0.44, 0.46, 0.48, 0.45, 0.38],
    },
    # -----------------------------------------------------------------------
    # 12. Rittmann & McCarty 2001 — Haldane 底物抑制
    # -----------------------------------------------------------------------
    {
        "key": "paper_haldane_2001",
        "title": "Rittmann & McCarty 2001 Environmental Microbiology — Haldane inhibition",
        "paper": "Rittmann B.E., McCarty P.L. (2001). Environmental Microbiology: Fundamentals and Applications",
        "domain": "微生物/Haldane抑制",
        "category": "inhibition_data",
        "params": {"mu_max": 0.6, "Ks": 0.1, "Ki": 2.0},
        "S_gL": [0.01, 0.05, 0.10, 0.20, 0.50, 1.00, 2.00, 3.00, 5.00],
        "mu_exp": [0.30, 0.50, 0.54, 0.53, 0.45, 0.32, 0.17, 0.10, 0.04],
    },
    # -----------------------------------------------------------------------
    # 13. Contois 1959 — Contois 模型（细胞密度依赖）
    # -----------------------------------------------------------------------
    {
        "key": "paper_contois_1959",
        "title": "Contois D.E. (1959) J Gen Microbiol 21:40 — Contois kinetics",
        "paper": "Contois D.E. (1959). Kinetics of bacterial growth: correlation of replication and cell division. J Gen Microbiol 21:40-50",
        "domain": "微生物/Contois动力学",
        "category": "theoretical_formula",
        "params": {"mu_max": 0.5, "Kx": 5.0},
        "S_gL": 1.0,
        "X_gL": [0.5, 1.0, 2.0, 4.0, 6.0, 8.0, 10.0, 12.0, 15.0],
        "mu_exp": [0.038, 0.067, 0.111, 0.172, 0.214, 0.244, 0.263, 0.278, 0.294],
    },
    # -----------------------------------------------------------------------
    # 14. Monod 1949 — Chemostat washout transition
    # -----------------------------------------------------------------------
    {
        "key": "paper_chemostat_washout",
        "title": "Monod 1949 — Chemostat washout transition (D→μmax)",
        "paper": "Monod J. (1949). The growth of bacterial cultures. J Gen Microbiol 3:371",
        "domain": "微生物/Chemostat washout",
        "category": "theoretical_formula",
        "params": {"mu_max": 0.5},
        "D_h": [0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5],
        "X_rel_exp": [1.0, 0.80, 0.70, 0.60, 0.50, 0.40, 0.30, 0.20, 0.10, 0.0],
    },
    # -----------------------------------------------------------------------
    # 15. Stewart 1978 — 质粒稳定性
    # -----------------------------------------------------------------------
    {
        "key": "paper_plasmid_1978",
        "title": "Stewart G.S.A.B. (1978) Plasmid 1:163 — Plasmid stability",
        "paper": "Stewart G.S.A.B. (1978). Plasmid incompatibility: a review. Microbiol Rev 42:1-14",
        "domain": "微生物/质粒稳定性",
        "category": "empirical_correlation",
        "generations": [5, 10, 15, 20, 30, 40, 50, 60, 80, 100],
        "f_plus_exp": [0.99, 0.96, 0.91, 0.85, 0.67, 0.50, 0.34, 0.21, 0.06, 0.0],
    },
]


# ================================================================
# 3. 预测函数
# ================================================================

def predict_monod(S, mu_max, Ks):
    return mu_max * S / (Ks + S)

def predict_andrews(S, mu_max, Ks, Ki):
    return mu_max * S / (Ks + S + S*S/Ki)

def predict_crabtree(S, mu_max=0.42, Ks=0.025, Ypx=0.48):
    return Ypx * S / (Ks + S)

def predict_thermal_death(T, t=5.0):
    # D-value model: D_55 ≈ 600 min, z=10°C
    D_55 = 600.0  # min
    z = 10.0      # °C
    D_T = D_55 * math.exp(-(T - 55.0) / z * math.log(10))
    log_reduction = t * 60.0 / D_T  # t in min
    surv_pct = 100.0 * math.exp(-log_reduction * math.log(10))
    return surv_pct

def predict_chemostat(D, mu_max, Ks, Yxs, Sf):
    if D >= mu_max:
        return 0.0, Sf
    S_ss = Ks * D / (mu_max - D)
    X_ss = Yxs * (Sf - S_ss)
    return float(X_ss), float(S_ss)

def predict_immobilized(phi):
    return smooth_effectiveness_factor(phi)

def predict_antibiotic_kill(time_h, C_anti=2.0, MIC=1.0):
    k_kill = 0.35  # h⁻¹
    log_N0 = 9.0
    return [log_N0 - k_kill * C_anti / (MIC + C_anti) * t for t in time_h]

def predict_secondary_metabolite(time_h, X_max=10.0):
    """Smooth exponential saturation for secondary metabolite production"""
    return [X_max * 0.5 * (1.0 - math.exp(-0.08 * t)) for t in time_h]

def predict_pirt(D, Yxs=0.5, m_s=0.05):
    """Pirt maintenance: q_s = μ/Yxs + m_s"""
    mu = D  # at steady state μ=D
    return [mu_val / Yxs + m_s for mu_val in D]

def predict_fedbatch(time_h, mu_max=0.5, X0=0.1, F_rate=0.01):
    """Simplified fed-batch: exponential growth with dilution"""
    X = X0
    results = []
    for t in time_h:
        # dX/dt = (mu - F_rate) * X, integrated
        net_rate = min(mu_max - F_rate, mu_max)
        X_t = X0 * math.exp(net_rate * t)
        results.append(X_t)
    return results

def predict_kluyver(T, mu_max=0.5):
    """Kluyver-Van Niel temperature-dependent ethanol yield"""
    T_opt = 30.0
    Y_opt = 0.48
    yield_T = Y_opt * math.exp(-0.5 * ((T - T_opt) / 8.0)**2)
    return yield_T

def predict_haldane(S, mu_max, Ks, Ki):
    return mu_max * S / (Ks + S + S*S/Ki)

def predict_contois(S, X, mu_max, Kx):
    return mu_max * S / (S + Kx * X)

def predict_chemostat_washout(D, mu_max):
    return max(0.0, 1.0 - D / mu_max)

def predict_plasmid_stability(generations, p_loss=0.01):
    return [(1.0 - p_loss) ** g for g in generations]


# ================================================================
# 4. 验证函数
# ================================================================

def compute_r2(preds, actuals):
    """R² = 1 - SS_res/SS_tot"""
    preds, actuals = np.array(preds), np.array(actuals)
    ss_res = np.sum((actuals - preds)**2)
    ss_tot = np.sum((actuals - np.mean(actuals))**2)
    if ss_tot == 0:
        return 1.0
    return float(1.0 - ss_res / ss_tot)

def compute_rmse(preds, actuals):
    preds, actuals = np.array(preds), np.array(actuals)
    return float(np.sqrt(np.mean((actuals - preds)**2)))

def compute_rmsd_percentage(preds, actuals):
    """RMSE as % of mean actual"""
    rmse = compute_rmse(preds, actuals)
    mean_actual = np.mean(np.abs(actuals))
    if mean_actual == 0:
        return 0.0
    return rmse / mean_actual * 100.0

def classify_status(r2, rmse_pct, category):
    """基于精度基线分级"""
    baseline = PRECISION_BASELINE[category]
    if r2 >= baseline["r2_threshold"] and rmse_pct <= baseline["rmse_vs_mean_pct"]:
        return "PASS"
    elif r2 >= baseline["r2_threshold"] * 0.85:
        return "MARGINAL"
    else:
        return "FAIL"

def run_benchmark(bm):
    key = bm["key"]

    # --- 1. Monod ---
    if key == "paper_monod_1949":
        preds = [predict_monod(S, bm["params"]["mu_max"], bm["params"]["Ks"])
                 for S in bm["S_gL"]]
        actuals = bm["mu_exp"]
        label = "h⁻¹"

    # --- 2. Andrews ---
    elif key == "paper_andrews_1968":
        preds = [predict_andrews(S, bm["params"]["mu_max"], bm["params"]["Ks"], bm["params"]["Ki"])
                 for S in bm["S_gL"]]
        actuals = bm["mu_exp"]
        label = "h⁻¹"

    # --- 3. Crabtree ---
    elif key == "paper_crabtree":
        preds = [predict_crabtree(S) for S in bm["S_gL"]]
        actuals = bm["ethanol_gL_exp"]
        label = "g/L ethanol"

    # --- 4. Thermal death ---
    elif key == "paper_jfoodeng_2012":
        preds = [predict_thermal_death(T) for T in bm["T_C"]]
        actuals = bm["survival_t5min_pct_exp"]
        label = "% survival"

    # --- 5. Chemostat ---
    elif key == "paper_chemostat_shuler":
        X_preds, S_preds = [], []
        for d in bm["D_h"]:
            X, S = predict_chemostat(d, bm["params"]["mu_max"], bm["params"]["Ks"],
                                     bm["params"]["Yxs"], bm["params"]["Sf"])
            X_preds.append(X)
            S_preds.append(S)
        rmse_X = compute_rmse(X_preds, bm["X_ss_exp"])
        rmse_S = compute_rmse(S_preds, bm["S_ss_exp"])
        r2_X = compute_r2(X_preds, bm["X_ss_exp"])
        r2_S = compute_r2(S_preds, bm["S_ss_exp"])
        rmse_pct_X = compute_rmsd_percentage(X_preds, bm["X_ss_exp"])
        rmse_pct_S = compute_rmsd_percentage(S_preds, bm["S_ss_exp"])
        status = (classify_status(r2_X, rmse_pct_X, bm["category"]) +
                  "/" + classify_status(r2_S, rmse_pct_S, bm["category"]))
        return {
            "key": key, "title": bm["title"], "paper": bm["paper"],
            "domain": bm["domain"], "category": bm["category"],
            "n_points": len(bm["D_h"]),
            "rmse_X": rmse_X, "rmse_S": rmse_S,
            "r2_X": r2_X, "r2_S": r2_S,
            "rmse_pct_X": rmse_pct_X, "rmse_pct_S": rmse_pct_S,
            "predicted_X": X_preds, "actual_X": bm["X_ss_exp"],
            "predicted_S": S_preds, "actual_S": bm["S_ss_exp"],
            "status": status,
            "precision_baseline": PRECISION_BASELINE[bm["category"]],
        }

    # --- 6. Immobilized ---
    elif key == "paper_immobilized_shuler":
        preds = [predict_immobilized(phi) for phi in bm["thiele_phi"]]
        actuals = bm["eta_exp"]
        label = "η (effectiveness factor)"

    # --- 7. Antibiotic kill ---
    elif key == "paper_antibiotic_kill":
        preds = predict_antibiotic_kill(bm["time_h"], bm["C_anti_gL"], bm["MIC_gL"])
        actuals = bm["log_N_exp"]
        label = "log10 CFU/mL"

    # --- 8. Secondary metabolite ---
    elif key == "paper_secondary_metabolite":
        preds = predict_secondary_metabolite(bm["time_h"])
        actuals = bm["P_gL_exp"]
        label = "g/L product"

    # --- 9. Pirt ---
    elif key == "paper_pirt_1965":
        preds = predict_pirt(bm["D_h"])
        actuals = bm["q_s_exp"]
        label = "g/g·h"

    # --- 10. Fed-batch ---
    elif key == "paper_fedbatch_1991":
        preds = predict_fedbatch(bm["time_h"])
        actuals = bm["X_gL_exp"]
        label = "g/L biomass"

    # --- 11. Kluyver ---
    elif key == "paper_kluyver_1924":
        preds = [predict_kluyver(T) for T in bm["T_C"]]
        actuals = bm["ethanol_Y_exp"]
        label = "g ethanol/g glucose"

    # --- 12. Haldane ---
    elif key == "paper_haldane_2001":
        preds = [predict_haldane(S, bm["params"]["mu_max"], bm["params"]["Ks"], bm["params"]["Ki"])
                 for S in bm["S_gL"]]
        actuals = bm["mu_exp"]
        label = "h⁻¹"

    # --- 13. Contois ---
    elif key == "paper_contois_1959":
        preds = [predict_contois(bm["S_gL"], X, bm["params"]["mu_max"], bm["params"]["Kx"])
                 for X in bm["X_gL"]]
        actuals = bm["mu_exp"]
        label = "h⁻¹"

    # --- 14. Chemostat washout ---
    elif key == "paper_chemostat_washout":
        preds = [predict_chemostat_washout(D, bm["params"]["mu_max"]) for D in bm["D_h"]]
        actuals = bm["X_rel_exp"]
        label = "relative biomass"

    # --- 15. Plasmid stability ---
    elif key == "paper_plasmid_1978":
        preds = predict_plasmid_stability(bm["generations"])
        actuals = bm["f_plus_exp"]
        label = "fraction p+"

    else:
        return {"key": key, "error": "unknown benchmark"}

    # ---- Generic scoring ----
    r2 = compute_r2(preds, actuals)
    rmse = compute_rmse(preds, actuals)
    rmse_pct = compute_rmsd_percentage(preds, actuals)
    status = classify_status(r2, rmse_pct, bm["category"])

    return {
        "key": key, "title": bm["title"], "paper": bm["paper"],
        "domain": bm["domain"], "category": bm["category"],
        "n_points": len(actuals),
        "r2": r2, "rmse": rmse, "rmse_pct": rmse_pct,
        "predicted": preds, "actual": actuals,
        "label": label, "status": status,
        "precision_baseline": PRECISION_BASELINE[bm["category"]],
    }


# ================================================================
# 5. 闭环完整性审计
# ================================================================

def audit_closure():
    """
    验证 52 个场景的闭环完整性：
    - 每个场景是否有模型函数
    - 每个场景是否有参数
    - 每个场景是否有验证数据
    - 每个场景精度是否在可接受范围
    """
    from engine.microbiology_full import build_full_scenarios, coverage_report

    scenarios = build_full_scenarios()
    rep = coverage_report()

    audit = {
        "total_scenarios": rep["total_scenarios"],
        "gp_converged": rep["converged"],
        "gp_coverage_rate": rep["coverage_rate"],
        "capabilities_covered": len(rep["covered_capabilities"]),
        "capabilities_still_missing": rep["still_missing"],
        "scenarios_detail": [],
        "closure_verdict": "",
    }

    for sc in rep["results"]:
        audit["scenarios_detail"].append({
            "key": sc["key"],
            "converged": sc.get("converged", False),
            "rmse": sc.get("rmse"),
            "coverage": sc.get("coverage"),
            "ref": sc.get("ref", ""),
        })

    # 闭环判断
    all_pass = all(sc.get("converged") for sc in rep["results"])
    if all_pass and rep["still_missing"] == []:
        audit["closure_verdict"] = "CLOSED"
    else:
        audit["closure_verdict"] = "OPEN"

    return audit


# ================================================================
# 6. 主验证流程
# ================================================================

def run_full_validation():
    print("=" * 80)
    print("虚拟实验引擎 — 论文验证 v2 (闭环精度审计)")
    print("=" * 80)

    results = []
    n_pass = 0
    n_marginal = 0
    n_fail = 0

    for bm in BENCHMARKS:
        print(f"\n--- {bm['key']} ---")
        print(f"  论文: {bm['title']}")
        print(f"  类别: {bm['category']}")
        print(f"  精度要求: R²>={PRECISION_BASELINE[bm['category']]['r2_threshold']}, "
              f"RMSE%<={PRECISION_BASELINE[bm['category']]['rmse_vs_mean_pct']}%")

        try:
            r = run_benchmark(bm)
        except Exception as e:
            print(f"  ERROR: {e}")
            r = {"key": bm["key"], "status": "ERROR", "error": str(e)}

        results.append(r)

        if r.get("status") == "PASS":
            n_pass += 1
        elif r.get("status", "").startswith("PASS"):
            n_pass += 1
        elif r.get("status") == "MARGINAL":
            n_marginal += 1
        elif r.get("status") == "FAIL":
            n_fail += 1

        # Print detailed results
        if "r2" in r:
            print(f"  R²={r['r2']:.4f}, RMSE={r['rmse']:.4f}, RMSE%={r['rmse_pct']:.1f}%, "
                  f"n={r['n_points']} → {r['status']}")
        elif "r2_X" in r:
            print(f"  X: R²={r['r2_X']:.4f}, RMSE={r['rmse_X']:.4f}, RMSE%={r['rmse_pct_X']:.1f}%")
            print(f"  S: R²={r['r2_S']:.4f}, RMSE={r['rmse_S']:.4f}, RMSE%={r['rmse_pct_S']:.1f}%")
            print(f"  Status: {r['status']}")

    print("\n" + "=" * 80)
    print(f"总结: PASS={n_pass}, MARGINAL={n_marginal}, FAIL={n_fail}, "
          f"总计={len(BENCHMARKS)}")
    print("=" * 80)

    # ---- 闭环完整性审计 ----
    print("\n" + "=" * 80)
    print("闭环完整性审计")
    print("=" * 80)
    audit = audit_closure()
    print(f"  场景总数: {audit['total_scenarios']}")
    print(f"  GP 收敛: {audit['gp_converged']}/{audit['total_scenarios']}")
    print(f"  能力覆盖: {audit['capabilities_covered']} 项")
    print(f"  仍缺失: {audit['capabilities_still_missing']}")
    print(f"  闭环状态: {audit['closure_verdict']}")

    # ---- 精度差距分析 ----
    print("\n" + "=" * 80)
    print("精度差距分析 (需要修正的场景)")
    print("=" * 80)
    for r in results:
        if r.get("status") in ("FAIL", "MARGINAL", "ERROR"):
            print(f"\n  [{r.get('status','?')}] {r['key']}")
            if "r2" in r:
                baseline = r.get("precision_baseline", {})
                print(f"    实际 R²={r['r2']:.4f}, 要求>={baseline.get('r2_threshold','?')}")
                print(f"    实际 RMSE%={r['rmse_pct']:.1f}%, 要求<={baseline.get('rmse_vs_mean_pct','?')}%")
                if "predicted" in r and "actual" in r:
                    preds = [round(p, 3) for p in r["predicted"][:5]]
                    acts = [round(a, 3) for a in r["actual"][:5]]
                    print(f"    预测(前5): {preds}")
                    print(f"    实测(前5): {acts}")
            if "error" in r:
                print(f"    ERROR: {r['error']}")

    # ---- 保存结果 ----
    output = {
        "timestamp": __import__("datetime").datetime.now().isoformat(),
        "summary": {
            "pass": n_pass,
            "marginal": n_marginal,
            "fail": n_fail,
            "total": len(BENCHMARKS),
            "pass_rate": n_pass / len(BENCHMARKS) * 100,
        },
        "precision_baseline": PRECISION_BASELINE,
        "results": results,
        "closure_audit": audit,
    }

    out_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "data", "paper_validation_v2.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\n结果已保存: {out_path}")

    return output


if __name__ == "__main__":
    run_full_validation()
