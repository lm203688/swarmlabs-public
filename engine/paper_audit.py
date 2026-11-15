"""
论文对抗审计（AUDIT 模式）—— GC-01「已发表论文反驳引擎」

与 Phase 1「复现打分」的区别：
  - 复现（run_paper_benchmark）：用论文发表的参数算预测，与论文发表的数据比对 → 打 PASS/MARGINAL/FAIL。
    这个模式下，拟合不好只说明「我们的实现/参数」不对，无法区分是谁的问题。
  - 审计（本模块）：把论文**自己的模型**重新拟合到论文**自己的数据**上，然后追问三件事：
      1. 重拟合参数 vs 论文发表参数，差多少？（参数层面的矛盾）
      2. 残差是随机的还是系统性的？（模型形式层面的矛盾）
      3. 即使最佳拟合，残差量级是否超出该领域合理实验噪声？（数据/模型不可调和）
    输出机器可查的审计证书（audit certificate），判定六选一：
      CONSISTENT / PARAM_DISCREPANCY / STRUCTURAL_MISFIT / NOISE_EXCESS /
      UNDERDETERMINED / DATA_PROVENANCE_SUSPECT

判定优先级（先判"能不能判"，再判"哪里坏"）：
  1. UNDERDETERMINED        数据点太少（n < n_params + 2）或重拟合撞边界 → 无法裁决，不做断言
  2. DATA_PROVENANCE_SUSPECT最佳拟合残差 < 数据范围 2% → 数据是模型生成的/被平滑过，
                            不可用于审计论文；此时的"参数偏离"是本仓库录入问题，不是论文问题
  3. PARAM_DISCREPANCY      重拟合参数与发表参数偏离 ≥2 倍，且重拟合显著降低 RMSE
                       → 论文发表的参数无法解释它自己的数据（Gauss 抓错型信号）
  4. OVERCLAIMED            重拟合参数与发表参数偏离 1.3~2 倍，且重拟合 RMSE 改善 ≥40%
                       → 论文把不可辨识呈现为确定结论（'silent over-claim'）
                       → GC-01 报告 P1 的第三种判定："结论过强"
  5. STRUCTURAL_MISFIT 残差呈系统性结构（游程检验 z 显著 / 一阶自相关高）
                       → 模型形式有问题，不是噪声问题
  6. NOISE_EXCESS      最佳拟合残差仍 > 数据范围的 15% → 数据与模型不可调和
  7. CONSISTENT        以上都不成立

证据纪律（重要，不可省略）：
  BENCHMARKS 里的 "exp" 数据是本仓库手工录入的（录自教材/论文图表的读数），
  **不是**从原始论文 supplementary 直接解析得到的。因此本审计的性质是
  「内部一致性审计」（internal consistency audit），**不是独立复现**。
  任何 PARAM_DISCREPANCY / STRUCTURAL_MISFIT 判定只可作为"值得人工核对原文"的候选信号，
  在人工比对原始文献之前，不得对外宣称"发现论文错误"。
  每个证书都带 evidence_grade 字段标明这一点。

用法：
  python engine/paper_audit.py                 # 审计全部 17 个基准
  python engine/paper_audit.py --marginal      # 只审当前状态为 MARGINAL 的基准
  python engine/paper_audit.py --key paper_andrews_1968

证据分级（2026-10-18 B-2，per-benchmark evidence_grade）：
  本报告的 17 个证书全部为 repo_entered_data__internal_consistency_only——
  数据为本仓库手工录入（录自教材/论文图表读数），仅内部一致性，不是独立复现。
  同宗的「已发表原始数据自动提取」审计（Zenodo 下载 + sha256 校验 + 程序化拟合）
  在 engine/published_data_audit.py，产物 data/published_data_audit.json，
  其证书为 auto_extracted_from_published_raw_data__sha256_verified 级。
  两库合并阅读才是 GC-01 的完整证据面；单一仓库内部口径不得混用两类等级。
"""
import sys, os, json, math
from typing import Any, Dict
import numpy as np
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import engine.paper_validation as pv

try:
    from scipy.optimize import least_squares
    from scipy.stats import norm as _norm
    _HAS_SCIPY = True
except ImportError:
    _HAS_SCIPY = False

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 每个基准参与重拟合的参数名（只列模型真正用到的；留空表示无可调参数）
AUDIT_PARAMS = {
    "paper_monod_1949":            ["mu_max", "Ks"],
    "paper_monod_1949_batch":      ["mu_max", "Ks", "Yxs"],
    "paper_andrews_1968":          ["mu_max", "Ks", "Ki"],
    "paper_contois_1959":          ["mu_max", "Kx"],
    "paper_contois_highdensity":   ["mu_max", "Kx"],
    "paper_haldane_1955":          ["mu_max", "Ks", "Ki"],
    "paper_pirt_1965":             ["Y_g", "m_s"],
    "paper_luedeking_piret_1959":  ["X_max", "Ypxg", "Ypxn", "mu", "t_lag"],
    "paper_tessier_1957":          ["mu_max", "Ks", "n"],
    "paper_baranyi_1994":          ["mu_max", "X0"],
    "paper_fedbatch_fick":         ["mu_max", "Yxs", "Sf", "F"],
    "paper_chemostat_shuler":      ["mu_max", "Ks", "Yxs", "Sf"],
    "paper_immobilized_shuler":    [],
    "paper_antibiotic_kill":       ["C_anti", "X0"],
    "paper_jfoodeng_2012":         [],
    "paper_crabtree_effect":       ["Ypx", "Ks"],
    "paper_competition_tilman":    ["mu_max_A", "Ks_A", "mu_max_B", "Ks_B"],
}

# 各领域合理的实验噪声 CV（相对数据均值），用于 NOISE_EXCESS 判定
# 依据：文件头「现实微生物实验误差基准」——生物重复 CV 5-15%，生长速率 10-20%
NOISE_CV_DEFAULT = 0.15
NOISE_CV = {
    "生长动力学（Monod）": 0.10,
    "生长动力学（批次培养）": 0.15,
    "底物抑制（Andrews）": 0.15,
    "生长动力学（Contois）": 0.15,
    "产物合成（Luedeking-Piret）": 0.15,
    "预测微生物学（Baranyi）": 0.15,
    "补料分批（Fed-batch）": 0.20,
    "竞争动力学（Tilman）": 0.20,
}

# ------------------------------------------------------------------
# 数据来源登记簿 + 证据分级（B-2，2026-10-18）
# ------------------------------------------------------------------
#: 每个基准的数据来源。evidence_grade 由此**计算**得出，fail-closed：
#: 未登记 ⇒ repo_entered_data__internal_consistency_only（保守档）。
#:
#: source_type:
#:   repo_transcription — 数据由本仓库从教材/论文图表读数手工录入（17 个经典基准全部如此）
#:   auto_download      — 数据从 Zenodo/Figshare 程序化下载、sha256 校验、程序化提取
#:                       （当前无经典基准达到此级；自动提取级审计见
#:                        engine/published_data_audit.py 的 E. coli 基准）
#:
#: transcription_note 记录每个基准数据的录入出处，供人工核对原文时定位。
DATA_PROVENANCE: Dict[str, Dict[str, Any]] = {
    "paper_monod_1949":            {"source_type": "repo_transcription",
                                    "transcription_note": "Monod 1949 论文图/表读数（二级文献转引）"},
    "paper_monod_1949_batch":      {"source_type": "repo_transcription",
                                    "transcription_note": "Monod 1949 批次生长曲线读数"},
    "paper_andrews_1968":          {"source_type": "repo_transcription",
                                    "transcription_note": "Andrews 1968 底物抑制数据表读数"},
    "paper_contois_1959":          {"source_type": "repo_transcription",
                                    "transcription_note": "Contois 1959 高密度生长数据读数"},
    "paper_haldane_1955":          {"source_type": "repo_transcription",
                                    "transcription_note": "Haldane 1955 酶动力学数据读数"},
    "paper_pirt_1965":             {"source_type": "repo_transcription",
                                    "transcription_note": "Pirt 1965 维持代谢数据读数"},
    "paper_luedeking_piret_1959":  {"source_type": "repo_transcription",
                                    "transcription_note": "Luedeking-Piret 1959 乳酸发酵数据读数"},
    "paper_tessier_1957":          {"source_type": "repo_transcription",
                                    "transcription_note": "Tessier 1957 生长数据读数"},
    "paper_baranyi_1994":          {"source_type": "repo_transcription",
                                    "transcription_note": "Baranyi & Roberts 1994 生长曲线读数"},
    "paper_fedbatch_fick":         {"source_type": "repo_transcription",
                                    "transcription_note": "补料分批 Fick 型数据读数"},
    "paper_chemostat_shuler":      {"source_type": "repo_transcription",
                                    "transcription_note": "Shuler 恒化器数据读数"},
    "paper_immobilized_shuler":    {"source_type": "repo_transcription",
                                    "transcription_note": "固定化酶有效因子数据读数"},
    "paper_antibiotic_kill":       {"source_type": "repo_transcription",
                                    "transcription_note": "抗生素杀菌动力学数据读数"},
    "paper_jfoodeng_2012":         {"source_type": "repo_transcription",
                                    "transcription_note": "J Food Eng 2012 热死亡数据读数"},
    "paper_crabtree_effect":       {"source_type": "repo_transcription",
                                    "transcription_note": "Crabtree 效应数据读数"},
    "paper_contois_highdensity":   {"source_type": "repo_transcription",
                                    "transcription_note": "Contois 高密度数据读数"},
    "paper_competition_tilman":    {"source_type": "repo_transcription",
                                    "transcription_note": "Tilman 竞争数据读数"},
}

#: 升级级证据的等级字符串（与 published_data_audit.py 共用同一常量）
EVIDENCE_GRADE_REPO = "repo_entered_data__internal_consistency_only"
EVIDENCE_GRADE_AUTO = "auto_extracted_from_published_raw_data__sha256_verified"


def evidence_grade_for(key: str) -> str:
    """按登记簿计算基准的证据等级（fail-closed：未登记/未校验 ⇒ 保守档）。"""
    prov = DATA_PROVENANCE.get(key)
    if (prov is not None
            and prov.get("source_type") == "auto_download"
            and prov.get("sha256_verified") is True):
        return EVIDENCE_GRADE_AUTO
    return EVIDENCE_GRADE_REPO


# ------------------------------------------------------------------
# 参数化预测：与 run_paper_benchmark 同构，但接受被覆盖的参数
# ------------------------------------------------------------------
def predict_with_params(bm, P):
    """用给定参数 P（而非论文发表参数）复算预测序列。返回 (preds, actuals)。"""
    key = bm["key"]

    if key == "paper_monod_1949":
        preds = [pv.predict_monod(s, P["mu_max"], P["Ks"]) for s in bm["S_gL"]]
        actuals = bm["mu_exp"]
    elif key == "paper_monod_1949_batch":
        t = np.array(bm["time_h"])
        sim = pv.batch_simulation({"mu_max": P["mu_max"], "Ks": P["Ks"], "Yxs": P["Yxs"]},
                                  S0=10.0, X0=0.1, hours=24.0, dt=0.05, noise_std=0.0, seed=42)
        preds = [float(np.interp(tv, sim["time"], sim["X"])) for tv in t]
        actuals = bm["X_exp_gL"]
    elif key == "paper_andrews_1968":
        preds = [pv.predict_andrews(s, P["mu_max"], P["Ks"], P["Ki"]) for s in bm["S_gL"]]
        actuals = bm["mu_exp"]
    elif key == "paper_contois_1959":
        preds = pv.predict_contois(bm["S_gL"], bm["X_gL"], P["mu_max"], P["Kx"])
        actuals = bm["mu_at_X01"]
    elif key == "paper_contois_highdensity":
        preds = pv.predict_contois_density(bm["X_gL"], P["mu_max"], bm["params"]["S"], P["Kx"])
        actuals = bm["mu_exp"]
    elif key == "paper_haldane_1955":
        preds = [pv.predict_andrews(s, P["mu_max"], P["Ks"], P["Ki"]) for s in bm["S_gL"]]
        actuals = bm["mu_exp"]
    elif key == "paper_pirt_1965":
        preds = pv.predict_pirt(bm["mu_h"], P["Y_g"], P["m_s"])
        actuals = bm["qs_exp"]
    elif key == "paper_luedeking_piret_1959":
        preds = pv.predict_luedeking_piret(bm["time_h"], P["X_max"], P["Ypxg"], P["Ypxn"],
                                           P["mu"], P["t_lag"])
        actuals = bm["P_exp_gL"]
    elif key == "paper_tessier_1957":
        preds = pv.predict_tessier(bm["S_gL"], P["mu_max"], P["Ks"], P["n"])
        actuals = bm["mu_exp"]
    elif key == "paper_baranyi_1994":
        preds = pv.lag_phase_model(np.array(bm["time_h"]), {
            "mu_max": P["mu_max"], "X0": P["X0"],
            "Ks": 0.01, "S0": 1.0, "lag_duration": 2.0, "T_opt": 37.0,
        }, noise_std=0.0, seed=42)
        preds = [float(p) for p in preds]
        actuals = bm["X_exp_gL"]
    elif key == "paper_fedbatch_fick":
        t = np.array(bm["time_h"])
        result = pv.fed_batch_simulation({"mu_max": P["mu_max"], "Ks": 0.01,
                                          "Yxs": P["Yxs"], "X0": 0.5, "S0": 5.0},
                                         S_feed=P["Sf"], feed_rate=P["F"],
                                         hours=24.0, dt=0.05, noise_std=0.0, seed=42)
        n_steps = len(result["X"])
        t_sim = np.linspace(0, 24.0, n_steps)
        preds = [float(np.interp(tv, t_sim, result["X"])) for tv in t]
        actuals = bm["X_exp_gL"]
    elif key == "paper_chemostat_shuler":
        preds = []
        for d in bm["D_h"]:
            X, _S = pv.predict_chemostat_steady(d, P["mu_max"], P["Ks"], P["Yxs"], P["Sf"])
            preds.append(X)
        actuals = bm["X_ss_exp"]
    elif key == "paper_immobilized_shuler":
        preds = [pv.predict_immobilized(phi) for phi in bm["phi_values"]]
        actuals = bm["eta_exp"]
    elif key == "paper_antibiotic_kill":
        preds = pv.predict_antibiotic_kill(bm["time_h"], P["C_anti"], P["X0"])
        actuals = bm["log_N_exp"]
    elif key == "paper_jfoodeng_2012":
        preds = [pv.predict_thermal_death_arrhenius(T) for T in bm["T_C"]]
        actuals = bm["survival_t5min_pct_exp"]
    elif key == "paper_crabtree_effect":
        preds = pv.predict_crabtree(bm["S_gL"], P["Ypx"], P["Ks"])
        actuals = bm["ethanol_yield_exp"]
    elif key == "paper_competition_tilman":
        preds = pv.predict_competition(bm["S_gL"], P["mu_max_A"], P["Ks_A"],
                                       P["mu_max_B"], P["Ks_B"])
        actuals = bm["ratio_A_B_exp"]
    else:
        raise KeyError(f"未知基准: {key}")

    return np.asarray(preds, dtype=float), np.asarray(actuals, dtype=float)


# ------------------------------------------------------------------
# 统计量
# ------------------------------------------------------------------
def runs_test_z(residuals):
    """残差符号游程检验。z 显著为负 = 游程过少 = 残差高度自相关（系统性偏差）。"""
    r = np.asarray(residuals, dtype=float)
    signs = np.sign(r)
    signs = signs[signs != 0]
    n1 = int(np.sum(signs > 0))
    n2 = int(np.sum(signs < 0))
    if n1 == 0 or n2 == 0:
        return 0.0, 1.0, 0
    runs = 1 + int(np.sum(signs[1:] != signs[:-1]))
    mu = 2.0 * n1 * n2 / (n1 + n2) + 1.0
    var = (mu - 1) * (mu - 2) / (n1 + n2 - 1) if (n1 + n2) > 2 else 0.0
    if var <= 0:
        return 0.0, 1.0, runs
    z = (runs - mu) / math.sqrt(var)
    p = 2.0 * (1.0 - _norm.cdf(abs(z))) if _HAS_SCIPY else 1.0
    return float(z), float(p), runs


def lag1_autocorr(residuals):
    r = np.asarray(residuals, dtype=float)
    r = r - r.mean()
    denom = float(np.sum(r * r))
    if denom <= 1e-12 or len(r) < 3:
        return 0.0
    return float(np.sum(r[1:] * r[:-1]) / denom)


# ------------------------------------------------------------------
# 单基准审计
# ------------------------------------------------------------------
def audit_benchmark(bm):
    key = bm["key"]
    names = AUDIT_PARAMS.get(key, [])
    published = dict(bm.get("params", {}))

    out = {
        "key": key,
        "title": bm.get("title", ""),
        "paper": bm.get("paper", ""),
        "domain": bm.get("domain", ""),
        "model": bm.get("model", "?"),
        "evidence_grade": evidence_grade_for(key),
        "data_source": DATA_PROVENANCE.get(key, {}).get("source_type", "unregistered"),
        "provenance_note": ("BENCHMARKS 中的实测数据为本仓库手工录入，非从原始论文 supplementary "
                            "直接解析。审计结论为内部一致性判定，人工核对原文前不得对外宣称发现论文错误。"),
    }

    # 发表参数下的预测
    try:
        pred_pub, act = predict_with_params(bm, published)
    except Exception as e:
        out.update({"verdict": "AUDIT_ERROR", "error": f"published-param predict failed: {e}"})
        return out
    if len(act) == 0 or not np.all(np.isfinite(pred_pub)):
        out.update({"verdict": "AUDIT_ERROR", "error": "non-finite published prediction"})
        return out

    n = len(act)
    data_range = float(np.max(act) - np.min(act)) or 1.0
    res_pub = pred_pub - act
    rmse_pub = float(np.sqrt(np.mean(res_pub ** 2)))
    out["n_points"] = n
    out["n_params"] = len(names)
    out["published_rmse"] = round(rmse_pub, 6)
    out["published_rel_rmse"] = round(rmse_pub / data_range, 4)

    # 无可调参数 → 只能做残差审计，不能做参数审计
    if not names or not _HAS_SCIPY:
        z, p, runs = runs_test_z(res_pub)
        ac1 = lag1_autocorr(res_pub)
        out.update({
            "refit": None,
            "residual_runs_z": round(z, 3),
            "residual_runs_p": round(p, 4),
            "residual_runs": runs,
            "residual_lag1_autocorr": round(ac1, 3),
            "verdict": ("STRUCTURAL_MISFIT" if (p < 0.05 and z < 0)
                        else "CONSISTENT" if rmse_pub / data_range <= 0.15 else "NOISE_EXCESS"),
            "verdict_basis": "无可调参数，仅做残差结构审计",
        })
        return out

    # 数据点不足以支撑参数识别
    if n < len(names) + 2:
        out.update({
            "refit": None,
            "verdict": "UNDERDETERMINED",
            "verdict_basis": f"数据点 n={n} 少于 参数数+2={len(names)+2}，参数不可识别，不做断言",
        })
        return out

    # 参数边界：以发表值为中心的**三个数量级**（下限不为 0）。
    # 2026-10-18 修复：旧边界 [0.05, 20]× 对 Ks / Ks_A / Ks_B 类参数过窄，
    # 会把 paper_competition_tilman 误判为 UNDERDETERMINED（诊断见
    # data/paper_audit_followup_2026-10-18.md §2.2：扩到 [0.01, 100]× 后
    # 全部参数收敛且不再撞边界）。扩到 [0.01, 100]× 仍比 3 数量级保守，
    # 因为真实 Monod 参数跨度一般不超过 4 数量级。
    x0, lb, ub = [], [], []
    for nm in names:
        v = float(published.get(nm, 1.0))
        if not np.isfinite(v) or v == 0:
            v = 1.0
        x0.append(v)
        lb.append(v * 0.01)
        ub.append(v * 100.0)

    def _resid(x):
        P = dict(published)
        P.update({nm: float(xi) for nm, xi in zip(names, x)})
        try:
            pr, ac = predict_with_params(bm, P)
        except Exception:
            return np.full(n, 1e6)
        d = pr - ac
        if not np.all(np.isfinite(d)):
            return np.full(n, 1e6)
        return d

    try:
        sol = least_squares(_resid, x0, bounds=(lb, ub), method="trf",
                            max_nfev=400 * max(1, len(names)))
        xbest = sol.x
        rmse_fit = float(np.sqrt(np.mean(sol.fun ** 2)))
    except Exception as e:
        out.update({"verdict": "AUDIT_ERROR", "error": f"refit failed: {e}"})
        return out

    at_bound = [bool(np.isclose(xi, lo, rtol=1e-3) or np.isclose(xi, hi, rtol=1e-3))
                for xi, lo, hi in zip(xbest, lb, ub)]
    refit_params = {nm: float(xi) for nm, xi in zip(names, xbest)}
    ratios = {}
    for nm in names:
        pv0 = float(published.get(nm, 0.0))
        pv1 = refit_params[nm]
        if pv0 == 0 or not np.isfinite(pv0):
            ratios[nm] = None
        else:
            r = pv1 / pv0
            ratios[nm] = round(max(r, 1.0 / r) if r > 0 else float("inf"), 3)
    valid_ratios = [v for v in ratios.values() if v is not None and np.isfinite(v)]
    max_ratio = max(valid_ratios) if valid_ratios else None

    # 重拟合后的残差结构
    try:
        pred_fit, _ = predict_with_params(bm, {**published, **refit_params})
    except Exception:
        pred_fit = pred_pub
    res_fit = pred_fit - act
    z, p, runs = runs_test_z(res_fit)
    ac1 = lag1_autocorr(res_fit)

    noise_cv = NOISE_CV.get(bm.get("domain", ""), NOISE_CV_DEFAULT)
    scale = float(np.mean(np.abs(act))) or 1.0
    rel_rmse_fit = rmse_fit / data_range

    out.update({
        "published_params": {nm: published.get(nm) for nm in names},
        "refit_params": {k: (round(v, 6)) for k, v in refit_params.items()},
        "param_fold_change": ratios,
        "max_param_fold_change": max_ratio,
        "refit_rmse": round(rmse_fit, 6),
        "refit_rel_rmse": round(rel_rmse_fit, 4),
        "rmse_improvement_pct": round((1 - rmse_fit / rmse_pub) * 100, 1) if rmse_pub > 0 else None,
        "residual_runs_z": round(z, 3),
        "residual_runs_p": round(p, 4),
        "residual_runs": runs,
        "residual_lag1_autocorr": round(ac1, 3),
        "residual_cv_vs_scale": round(rmse_fit / scale, 4),
        "plausible_noise_cv": noise_cv,
        "params_at_bound": at_bound,
    })

    # ---- 数据真伪自检（优先级最高之一）----
    # 真实实验数据（生物重复 CV 5-15%）不可能落在模型曲线上到 ~1%。若最佳拟合残差
    # 相对数据范围 < 2%，说明这批"实测数据"极可能是模型生成的或被理想化平滑过。
    # 此时 PARAM_DISCREPANCY 反映的是本仓库数据录入与注释参数不一致，而非论文问题
    # —— 必须降级为 DATA_PROVENANCE_SUSPECT，禁止当作论文发现对外宣称。
    if rel_rmse_fit < 0.02:
        out["data_synthetic_suspect"] = True
        worst = max(ratios, key=lambda k: (ratios[k] if ratios[k] is not None else -1)) if ratios else None
        cmp_txt = ""
        if worst:
            cmp_txt = (f"最大偏离参数 {worst}: 注释 {published.get(worst)} → 数据反推 "
                       f"{round(refit_params[worst], 4)}（{ratios[worst]}×）")
        out["verdict"] = "DATA_PROVENANCE_SUSPECT"
        out["verdict_basis"] = (
            f"最佳拟合残差仅占数据范围 {round(rel_rmse_fit * 100, 2)}%（真实实验数据应 ≥5%）——"
            f"该基准的「实测数据」极可能由模型生成或被理想化平滑，无法用于审计论文。"
            f"{cmp_txt}。属本仓库数据录入问题，须修正数据后再审计。")
        return out

    # ---- 判定（顺序即优先级）----
    if any(at_bound):
        out["verdict"] = "UNDERDETERMINED"
        out["verdict_basis"] = ("重拟合参数撞到搜索边界，说明该参数在本数据下不可识别"
                                f"（撞边界参数: {[nm for nm, b in zip(names, at_bound) if b]}）")
        return out

    if max_ratio is not None and max_ratio >= 2.0 and rmse_fit < 0.6 * rmse_pub:
        worst = max(ratios, key=lambda k: (ratios[k] if ratios[k] is not None else -1))
        out["verdict"] = "PARAM_DISCREPANCY"
        out["verdict_basis"] = (
            f"重拟合参数与发表参数偏离 {max_ratio}×（最大偏离参数 {worst}: "
            f"发表 {published.get(worst)} → 重拟合 {round(refit_params[worst], 4)}），"
            f"且重拟合使 RMSE 下降 {out['rmse_improvement_pct']}%。"
            "候选信号：论文发表参数可能无法解释其自身数据——需人工核对原文后方可断言。")
        return out

    # OVERCLAIMED（2026-10-17 P1 新增）：论文「结论过强」
    # 判据：发表参数拟合"看起来还行"（rmse_improvement 未达 PARAM_DISCREPANCY 阈值），
    # 但重拟合显著改善 RMSE（≥ 40%），且至少一个参数折变在 1.3×~2.0× 之间——
    # 即论文的**具体参数值**并未被数据唯一确定，但论文把它们当作已确定的结论。
    # 这类"静默过强"比 PARAM_DISCREPANCY 更常见也更隐蔽：论文不撒谎，但把不可辨识
    # 呈现为确定。这是 GC-01 报告 P1 的第三种判定（结论过强）。
    if (max_ratio is not None and 1.3 <= max_ratio < 2.0
            and out.get("rmse_improvement_pct") is not None
            and out["rmse_improvement_pct"] >= 40.0):
        worst = max(ratios, key=lambda k: (ratios[k] if ratios[k] is not None else -1))
        out["verdict"] = "OVERCLAIMED"
        out["verdict_basis"] = (
            f"论文报告的参数在本数据下并非唯一确定：重拟合参数 {worst} 折变 "
            f"{max_ratio}×（发表 {published.get(worst)} → 重拟合 "
            f"{round(refit_params[worst], 4)}），且 RMSE 显著下降 "
            f"{out['rmse_improvement_pct']}%——数据无法支撑论文给出的精确参数值。"
            "候选信号：论文把不可辨识呈现为确定结论（'silent over-claim'）——"
            "需人工核对论文是否明确披露参数不确定性区间。")
        return out

    if p < 0.05 and z < 0:
        out["verdict"] = "STRUCTURAL_MISFIT"
        out["verdict_basis"] = (f"最佳拟合下残差仍呈系统性结构（游程数 {runs}，"
                                f"游程检验 z={round(z,2)} p={round(p,4)}，一阶自相关 {round(ac1,3)}）——"
                                "指向模型形式失配，而非随机噪声。")
        return out

    if rel_rmse_fit > 0.15:
        out["verdict"] = "NOISE_EXCESS"
        out["verdict_basis"] = (f"最佳拟合残差相对数据范围仍达 {round(rel_rmse_fit*100,1)}% "
                                f"（该领域合理噪声 CV ≈ {noise_cv*100:.0f}%）——"
                                "数据与模型不可调和，或录入数据本身有问题。")
        return out

    out["verdict"] = "CONSISTENT"
    out["verdict_basis"] = ("重拟合参数与发表参数一致（最大偏离 "
                            f"{max_ratio if max_ratio is not None else 'n/a'}×），"
                            "残差无系统性结构，量级在该领域合理噪声范围内。")
    return out


# ------------------------------------------------------------------
# 入口
# ------------------------------------------------------------------
def marginal_keys():
    """从结构化结果库读取当前 MARGINAL 基准的 key 集合。"""
    path = os.path.join(ROOT, "data", "validation_results.json")
    try:
        db = json.load(open(path, encoding="utf-8"))
        return [r["benchmark_key"] for r in db.get("results", [])
                if r.get("status") == "MARGINAL"]
    except Exception:
        return []


def run_audit(keys=None, only_marginal=False):
    targets = list(pv.BENCHMARKS)
    if only_marginal:
        mk = set(marginal_keys())
        targets = [b for b in targets if b["key"] in mk]
    elif keys:
        ks = set(keys)
        targets = [b for b in targets if b["key"] in ks]

    print(f"\n{'#'*72}")
    print(f"# 论文对抗审计（AUDIT）—— GC-01 已发表论文反驳引擎")
    print(f"# 目标: {len(targets)} 个基准   时间: {datetime.utcnow().isoformat()}Z")
    print(f"# 性质: 内部一致性审计（数据为仓库录入），非独立复现")
    print(f"{'#'*72}\n")

    certs = []
    for bm in targets:
        c = audit_benchmark(bm)
        certs.append(c)
        v = c.get("verdict", "?")
        extra = ""
        if v == "PARAM_DISCREPANCY":
            extra = f"  偏离{c['max_param_fold_change']}×  RMSE↓{c['rmse_improvement_pct']}%"
        elif v == "STRUCTURAL_MISFIT":
            extra = f"  游程z={c.get('residual_runs_z')} ac1={c.get('residual_lag1_autocorr')}"
        elif v == "NOISE_EXCESS":
            extra = f"  rel_rmse={c.get('refit_rel_rmse') or c.get('published_rel_rmse')}"
        elif v == "UNDERDETERMINED":
            extra = f"  n={c.get('n_points')} params={c.get('n_params')}"
        print(f"[{v:20s}] {c['key']:32s}{extra}")

    counts = {}
    grade_dist = {}
    for c in certs:
        counts[c.get("verdict", "?")] = counts.get(c.get("verdict", "?"), 0) + 1
        g = c.get("evidence_grade", EVIDENCE_GRADE_REPO)
        grade_dist[g] = grade_dist.get(g, 0) + 1

    print(f"\n{'='*64}")
    print("审计判定汇总")
    print(f"{'='*64}")
    for k in sorted(counts):
        print(f"  {k:20s} {counts[k]}")
    print(f"{'='*64}")

    flagged = [c for c in certs if c.get("verdict") in ("PARAM_DISCREPANCY", "STRUCTURAL_MISFIT")]
    suspect = [c for c in certs if c.get("verdict") == "DATA_PROVENANCE_SUSPECT"]
    if suspect:
        print("\n--- 本仓库数据问题（须先修数据，不得当作论文发现）---")
        for c in suspect:
            print(f"  [{c['verdict']}] {c['key']}")
            print(f"     {c.get('verdict_basis','')}")
    if flagged:
        print("\n--- 需人工核对原文的候选信号（审计有效）---")
        for c in flagged:
            print(f"  [{c['verdict']}] {c['key']}")
            print(f"     {c.get('verdict_basis','')}")
    else:
        print("\n无 PARAM_DISCREPANCY / STRUCTURAL_MISFIT 信号。")

    report = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "mode": "audit",
        "scope": "marginal_only" if only_marginal else ("all" if not keys else "selected"),
        "evidence_grade": EVIDENCE_GRADE_REPO,  # 本报告 17 个证书全部为此级（保守公共档）
        "evidence_grade_distribution": grade_dist,
        "evidence_grade_note": (
            "本报告仅覆盖 repo_entered 级（手工录入数据，内部一致性审计）。"
            "已发表原始数据自动提取级（auto_extracted_from_published_raw_data__"
            "sha256_verified）审计见 data/published_data_audit.json"
            "（engine/published_data_audit.py）。两类等级不得混用。"),
        "counts": counts,
        "certificates": certs,
    }
    out_path = os.path.join(ROOT, "data", "paper_audit.json")
    json.dump(report, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\n审计证书已保存: {out_path}")

    _write_md(report, os.path.join(ROOT, "data", "paper_audit_report.md"))
    return report


def _write_md(report, path):
    L = []
    L.append("# 论文对抗审计报告（AUDIT · GC-01）\n")
    L.append(f"生成时间：{report['generated_at']}　范围：{report['scope']}\n")
    L.append(f"判定分布：{json.dumps(report['counts'], ensure_ascii=False)}\n")
    L.append(f"证据等级分布：{json.dumps(report.get('evidence_grade_distribution', {}), ensure_ascii=False)}\n")
    L.append("> **证据等级**：`repo_entered_data__internal_consistency_only`。")
    L.append("> 基准中的「实测」数据为本仓库手工录入，非从原始论文 supplementary 直接解析。")
    L.append("> 本报告为**内部一致性审计**，任何异常判定仅为「值得人工核对原文」的候选信号；")
    L.append("> 在人工比对原始文献之前，**不得对外宣称发现论文错误**。")
    L.append("> 自动提取级（Zenodo 下载 + sha256 校验）的已发表论文审计见")
    L.append("> `data/published_data_audit.json`（González-Siso et al. 2026, Sci Rep）。\n")
    L.append("| 基准 | 判定 | 偏离倍数 | RMSE↓% | 游程z | 一阶自相关 | 依据 |")
    L.append("|---|---|---|---|---|---|---|")
    for c in report["certificates"]:
        L.append("| {} | {} | {} | {} | {} | {} | {} |".format(
            c.get("key", ""), c.get("verdict", ""),
            c.get("max_param_fold_change", "-"), c.get("rmse_improvement_pct", "-"),
            c.get("residual_runs_z", "-"), c.get("residual_lag1_autocorr", "-"),
            (c.get("verdict_basis") or c.get("error") or "").replace("|", "/")[:180]))
    L.append("")
    open(path, "w", encoding="utf-8").write("\n".join(L))
    print(f"可读报告已保存: {path}")


if __name__ == "__main__":
    args = sys.argv[1:]
    ks = None
    if "--key" in args:
        ks = [args[args.index("--key") + 1]]
    run_audit(keys=ks, only_marginal=("--marginal" in args))
