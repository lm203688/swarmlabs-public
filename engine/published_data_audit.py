"""
已发表原始数据自动提取审计（GC-01 的 auto-extracted 级）—— B-2，2026-10-18

与 engine/paper_audit.py 的关系（必读）：
  paper_audit.py 审计 17 个经典基准，数据是本仓库**手工录入**的，证据等级
  repo_entered_data__internal_consistency_only——只能算内部一致性排查。
  本模块把 GC-01 的同一套「重拟合 vs 发表参数」纪律应用到**机器可读的已发表
  原始数据**上：Zenodo 程序化下载（sha256 校验）+ 程序化提取 + 重拟合，
  证据等级 auto_extracted_from_published_raw_data__sha256_verified。
  这是 GC-01 目前唯一达到独立复现级的证书系列。

首个基准：González-Siso et al. (2026) "A logistic model for glucose-driven
asymmetry in multi-phase E. coli growth kinetics", Scientific Reports,
DOI 10.1038/s41598-026-54144-5（PMC13447829）。
  原始数据：Zenodo 10.5281/zenodo.19416053（v2, CC-BY-4.0）——36 个生长条件
  （4 葡萄糖 × 9 温度）的 OD600 时间序列，每时间点 8 个技术重复。
  论文模型（Eq.5）：OD(t) = c - c / [1 + τ·exp(b·(t - t_apm))]^(1/τ)
  论文发表的可证伪 claim（摘要/结果段，逐条见 ECOLI_PAPER["claims"]）：
    C1  b 从 0.014 h⁻¹（29°C/2.0%）到 0.068 h⁻¹（45°C/0.1%）
    C2  温度使 b 增加 2.4 倍（固定葡萄糖）
    C3  τ 从 3.950（0.1% 葡萄糖）降到 1.501（2.0%），2.6 倍
    C4  最大生长率翻倍时间与文献值（Table 1：20–65.5 min）一致
    C5  Arrhenius 外推下限温度 16.7 ± 1.2 °C
    C6  b 随葡萄糖升高而下降

审计纪律（与 paper_audit.py 同源，缺一条即失真）：
  1. sha256 fail-loud：原始文件与 data/raw/manifest.json 不符 ⇒ 拒绝审计。
  2. 拟合边界**宽**（τ∈[0.3,10]，b∈[5e-4,0.5]/min）——绝不把论文声称的区间
     拿来当搜索边界，否则「复现」是循环论证。
  3. 多起点（15 起点/条件）防局部最优；记录近优解个数（rmse≤1.05×best）
     作为 (b,τ) 联合可识别性的直接度量。
  4. 噪声地板用**技术重复**实测：rmse ≈ σ/√n 为真数据签名；rmse ≪ σ/√n
     （<0.3×）判 DATA_PROVENANCE_SUSPECT（数据被平滑/合成）。
  5. 单位歧义**双读**：论文标注 b 为 h⁻¹，但原始数据时间轴是分钟。本审计
     同时按 min⁻¹（论文实际拟合单位，量级相符）与 h⁻¹（字面）两种读法
     核对 C1/C2，两读结果都写入证书，不替论文圆场。
  6. 结论分级输出：claim 级 reproduced 布尔 + 证书级 verdict；部分复现就写
     PARTIALLY_REPRODUCED，不美化。

产物：data/published_data_audit.json + data/published_data_audit_report.md
用法：python scripts/run_published_data_audit.py
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

try:
    from scipy.optimize import least_squares
    _HAS_SCIPY = True
except ImportError:  # pragma: no cover - 环境缺 scipy 时 fail-loud
    _HAS_SCIPY = False

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST_PATH = os.path.join(ROOT, "data", "raw", "manifest.json")
DATA_DIR = os.path.join(ROOT, "data", "raw", "ecoli_monod_growth")

#: 证据等级常量（与 paper_audit.py 同值，单一真源在 paper_audit，此处 import 会
#: 造成循环依赖，故复制并加一致性测试 tests/test_published_data_audit.py）
EVIDENCE_GRADE = "auto_extracted_from_published_raw_data__sha256_verified"

#: 论文登记（所有数字均从 PMC 全文 XML 提取，2026-10-18）
ECOLI_PAPER: Dict[str, Any] = {
    "key": "paper_ecoli_logistic_2026",
    "title": "A logistic model for glucose-driven asymmetry in multi-phase E. coli growth kinetics",
    "authors": "González-Siso, Becerra, Torrecilla, Díaz-Díaz, López-Beceiro, Artiaga",
    "journal": "Scientific Reports",
    "year": 2026,
    "doi": "10.1038/s41598-026-54144-5",
    "pmcid": "PMC13447829",
    "data_doi": "10.5281/zenodo.19416053",
    "data_url": "https://zenodo.org/records/19416053",
    "data_license": "CC-BY-4.0",
    "model": "generalized logistic (Richards-type), Eq.5: OD(t)=c-c/[1+τ·exp(b(t-t_apm))]^(1/τ)",
    "claims": {
        "C1_b_min": {"published": 0.014, "condition": "29C/2.0%", "unit": "h^-1(labelled)"},
        "C1_b_max": {"published": 0.068, "condition": "45C/0.1%", "unit": "h^-1(labelled)"},
        "C2_b_temp_fold": {"published": 2.4},
        "C3_tau_low_glucose": {"published": 3.950, "condition": "0.1%"},
        "C3_tau_high_glucose": {"published": 1.501, "condition": "2.0%"},
        "C3_tau_fold": {"published": 2.6},
        "C4_td_literature_min_min": {"published": 20.0, "source": "Sezonov et al., LB 37C"},
        "C4_td_literature_max_min": {"published": 65.5, "source": "Kovarova et al., chemostat"},
        "C5_T_min_C": {"published": 16.7, "sd": 1.2},
    },
}

#: claim 复现容差（相对偏离；超出即未复现）
CLAIM_TOL = {
    "C1": 0.30,   # 参数极值：允许 30% 相对偏离（拟合细节/边界差异）
    "C2": 0.25,   # 倍率
    "C3": 0.25,   # 倍率与端点值
    "C4": 0.50,   # 翻倍时间区间比对（区间宽，取 50%）
    "C5": 0.50,   # 外推温度（外推本身不确定，取 50%）
}

#: 拟合宽边界（纪律 2：不采用论文声称区间）
_FIT_BOUNDS_LO = None  # 运行时按各条件 c 生成
_FIT_BOUNDS_HI = None
TAU_LO, TAU_HI = 0.3, 10.0
B_LO, B_HI = 5e-4, 0.5          # /min
_C_LO_FRAC, _C_HI_FRAC = 0.7, 1.4

#: 多起点网格（纪律 3）
_TAU_STARTS = (0.5, 1.0, 2.0, 4.0, 8.0)
_B_STARTS = (0.005, 0.02, 0.05)

#: 文件名 → 葡萄糖 %（与 Zenodo 记录对应）
_GLUCOSE_FILES = {
    "0.1": "Repo0·1pct.xlsx",
    "0.5": "Repo0·5pct.xlsx",
    "1":   "Repo1pct.xlsx",
    "2":   "Repo2pct.xlsx",
}


# ------------------------------------------------------------------
# 1. sha256 校验（fail-loud）
# ------------------------------------------------------------------
def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_source() -> Dict[str, Any]:
    """对照 manifest 校验原始数据文件。任一不符/缺失 ⇒ 抛错（fail-loud）。"""
    if not os.path.exists(MANIFEST_PATH):
        raise FileNotFoundError(
            f"manifest 缺失: {MANIFEST_PATH}；先运行 scripts/fetch_real_datasets.py")
    manifest = json.load(open(MANIFEST_PATH, encoding="utf-8"))
    want = {}
    for d in manifest.get("downloads", []):
        if d.get("key") == "ecoli_monod_growth":
            want[os.path.basename(d["path"])] = d
    if not want:
        raise ValueError("manifest 中无 ecoli_monod_growth 条目")
    checked = {}
    for fname, rec in sorted(want.items()):
        p = os.path.join(DATA_DIR, fname)
        if not os.path.exists(p):
            raise FileNotFoundError(f"原始数据缺失: {p}（gitignored，运行 fetch_real_datasets.py）")
        got = _sha256(p)
        if got != rec.get("sha256"):
            raise ValueError(f"sha256 不符: {fname} manifest={rec.get('sha256')} actual={got}")
        checked[fname] = {"sha256": got, "size_bytes": os.path.getsize(p),
                          "doi": rec.get("doi"), "url": rec.get("url")}
    return {"n_files": len(checked), "files": checked,
            "data_doi": ECOLI_PAPER["data_doi"]}


# ------------------------------------------------------------------
# 2. 提取：xlsx → 每条件 (t_min, OD 均值, 重复)
# ------------------------------------------------------------------
def extract_growth_curves(data_dir: Optional[str] = None) -> List[Dict[str, Any]]:
    """解析 4 个 Repo*.xlsx 的全部 sheet → 条件列表。

    每 sheet 一条件（温度_葡萄糖[重复标]），列 1=tcultivo(min)，列 11=Average(OD)，
    列 3-10=8 个技术重复。返回条件按 (葡萄糖, 温度) 排序，确定性输出。
    """
    import openpyxl  # 延迟 import：无 openpyxl 时本模块其余部分仍可 import

    ddir = data_dir or DATA_DIR
    conds: List[Dict[str, Any]] = []
    for gluc_s, fname in sorted(_GLUCOSE_FILES.items(), key=lambda kv: float(kv[0])):
        path = os.path.join(ddir, fname)
        if not os.path.exists(path):
            raise FileNotFoundError(f"原始数据缺失: {path}")
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        for sn in wb.sheetnames:
            head = sn.split("_")[0]
            try:
                temp_c = float(head)
            except ValueError:
                continue  # 跳过非条件 sheet
            ws = wb[sn]
            t: List[float] = []
            od: List[float] = []
            reps: List[List[float]] = []
            for r in ws.iter_rows(min_row=2, values_only=True):
                if len(r) < 12 or r[1] is None or r[11] is None:
                    continue
                try:
                    t.append(float(r[1]))
                    od.append(float(r[11]))
                except (TypeError, ValueError):
                    continue
                vals = [float(x) for x in r[3:11] if x is not None]
                reps.append(vals)
            if len(t) < 10:
                continue
            conds.append({
                "glucose_pct": float(gluc_s),
                "temperature_C": temp_c,
                "sheet": sn,
                "t_min": np.asarray(t, dtype=float),
                "od_mean": np.asarray(od, dtype=float),
                "replicates": reps,
            })
        wb.close()
    conds.sort(key=lambda c: (c["glucose_pct"], c["temperature_C"]))
    if not conds:
        raise ValueError("未提取到任何生长条件")
    return conds


# ------------------------------------------------------------------
# 3. 论文 Eq.5 模型与多起点拟合
# ------------------------------------------------------------------
def richards_od(p: Tuple[float, float, float, float], t: np.ndarray) -> np.ndarray:
    """论文 Eq.5：OD(t) = c - c / [1 + τ·exp(b·(t - t_apm))]^(1/τ)。t 单位分钟。"""
    c, tau, b, tapm = p
    u = tau * np.exp(b * (t - tapm))
    return c - c / np.power(1.0 + u, 1.0 / tau)


def fit_condition(cond: Dict[str, Any]) -> Dict[str, Any]:
    """多起点宽边界拟合单条件。返回参数 + 拟合质量 + 可识别性诊断。"""
    if not _HAS_SCIPY:
        raise RuntimeError("scipy 缺失，无法拟合（fail-loud，不静默降级）")
    t = cond["t_min"]
    od = cond["od_mean"]
    c_max = float(np.max(od))
    lo = [c_max * _C_LO_FRAC, TAU_LO, B_LO, 0.0]
    hi = [c_max * _C_HI_FRAC, TAU_HI, B_HI, float(np.max(t))]
    t_apm0 = float(np.clip(t[int(np.argmax(np.gradient(od, t)))], 1e-6, np.max(t) - 1e-6))

    results = []
    for tau0 in _TAU_STARTS:
        for b0 in _B_STARTS:
            x0 = [c_max * 1.02, tau0, b0, t_apm0]
            try:
                sol = least_squares(lambda p: richards_od(p, t) - od, x0,
                                    bounds=(lo, hi), method="trf", max_nfev=30000)
            except Exception:
                continue
            rmse = float(np.sqrt(np.mean(sol.fun ** 2)))
            results.append((rmse, [float(v) for v in sol.x]))
    if not results:
        raise RuntimeError(f"所有起点均失败: {cond['sheet']}")
    results.sort(key=lambda x: x[0])
    best_rmse, best_p = results[0]
    # 可识别性诊断：近优解中参数**互不相同**的个数（>5% 相对差异）。
    # 注意与「起点收敛到同一最优」区分：15 个起点全部落在同一参数点 ⇒ 拟合
    # 数值稳定（好事）；若多个近优解参数迥异 ⇒ 似然谷平坦、(b,τ) 不可识别。
    n_near = sum(1 for rmse, _ in results if rmse <= 1.05 * best_rmse)
    distinct = 1
    for rmse, p in results[1:]:
        if rmse > 1.05 * best_rmse:
            continue
        if any(abs(p[i] - best_p[i]) > 0.05 * max(abs(best_p[i]), 1e-12) for i in range(4)):
            distinct += 1

    # 技术重复噪声地板（纪律 4）
    rep_std = [float(np.std(v, ddof=1)) for v in cond["replicates"] if len(v) > 1]
    sigma = float(np.mean(rep_std)) if rep_std else float("nan")
    n_rep = float(np.mean([len(v) for v in cond["replicates"] if v])) if cond["replicates"] else 1.0
    sigma_mean = sigma / np.sqrt(n_rep) if sigma and np.isfinite(sigma) else float("nan")
    noise_ratio = best_rmse / sigma_mean if sigma_mean and np.isfinite(sigma_mean) else float("nan")

    ss_res = float(np.sum((richards_od(best_p, t) - od) ** 2))
    ss_tot = float(np.sum((od - od.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")

    at_bound = [bool(np.isclose(best_p[i], lo[i], rtol=1e-3) or np.isclose(best_p[i], hi[i], rtol=1e-3))
                for i in range(4)]

    c, tau, b, tapm = best_p
    return {
        "glucose_pct": cond["glucose_pct"],
        "temperature_C": cond["temperature_C"],
        "sheet": cond["sheet"],
        "n_points": len(t),
        "c": round(c, 5),
        "tau": round(tau, 4),
        "b_per_min": round(b, 6),
        "b_per_h": round(b * 60.0, 5),
        "t_apm_min": round(tapm, 3),
        "rmse": round(best_rmse, 6),
        "r2": round(r2, 5),
        "sigma_replicate": round(sigma, 5) if sigma == sigma else None,
        "sigma_of_mean": round(sigma_mean, 6) if sigma_mean == sigma_mean else None,
        "noise_ratio": round(noise_ratio, 3) if noise_ratio == noise_ratio else None,
        "n_starts": len(results),
        "n_near_optimal": n_near,
        "n_distinct_near_optimal": distinct,
        "params_at_bound": at_bound,
        "td_min_from_b": round(np.log(2) / b, 2) if b > 0 else None,
    }


# ------------------------------------------------------------------
# 4. claim 逐条核对
# ------------------------------------------------------------------
def _ratio(a: float, b: float) -> float:
    return max(a / b, b / a) if a > 0 and b > 0 else float("inf")


def check_claims(fits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把论文的 C1-C6 逐条与重拟合结果核对。返回 claim 级结论列表。"""
    checks: List[Dict[str, Any]] = []

    def by(g: float, T: float) -> List[Dict[str, Any]]:
        return [f for f in fits if f["glucose_pct"] == g and f["temperature_C"] == T]

    def mean_b(g: float, T: float) -> Optional[float]:
        v = [f["b_per_min"] for f in by(g, T)]
        return float(np.mean(v)) if v else None

    # ---- C1a: b 最小值（论文 0.014 @ 29C/2%，双读 + 逐条件） ----
    ref_min = min(fits, key=lambda f: f["b_per_min"])
    for reading in ("as_min^-1(论文实际拟合单位)", "as_h^-1(字面标注)"):
        pub = ECOLI_PAPER["claims"]["C1_b_min"]["published"]
        pub_in_min = pub / 60.0 if reading.startswith("as_h") else pub
        dev = _ratio(ref_min["b_per_min"], pub_in_min) - 1.0
        checks.append({
            "claim": "C1a_b_min_global", "reading": reading,
            "published": pub, "published_in_per_min": round(pub_in_min, 6),
            "refit": ref_min["b_per_min"],
            "refit_condition": f"{ref_min['glucose_pct']}%/{ref_min['temperature_C']}C",
            "paper_condition": ECOLI_PAPER["claims"]["C1_b_min"]["condition"],
            "rel_deviation": round(dev, 3),
            "reproduced": bool(dev <= CLAIM_TOL["C1"]),
        })
    # 逐条件：论文声称的条件本身（29°C/2.0%）
    for g, T, ck in ((2.0, 29.0, "C1a_b_at_paper_condition"),
                     (0.1, 45.0, "C1b_b_at_paper_condition")):
        pub = (ECOLI_PAPER["claims"]["C1_b_min"]["published"] if ck.startswith("C1a")
               else ECOLI_PAPER["claims"]["C1_b_max"]["published"])
        vals = [f["b_per_min"] for f in by(g, T)]
        ref = float(np.mean(vals)) if vals else None
        if ref is not None:
            dev = _ratio(ref, pub) - 1.0
            checks.append({
                "claim": ck, "reading": f"at {g}%/{T}C (paper's own condition), as min^-1",
                "published": pub, "refit": round(ref, 6),
                "rel_deviation": round(dev, 3),
                "reproduced": bool(dev <= CLAIM_TOL["C1"]),
            })
    # ---- C1b: b 最大值（全局极值，双读） ----
    ref_max = max(fits, key=lambda f: f["b_per_min"])
    for reading in ("as_min^-1(论文实际拟合单位)", "as_h^-1(字面标注)"):
        pub = ECOLI_PAPER["claims"]["C1_b_max"]["published"]
        pub_in_min = pub / 60.0 if reading.startswith("as_h") else pub
        dev = _ratio(ref_max["b_per_min"], pub_in_min) - 1.0
        checks.append({
            "claim": "C1b_b_max_global", "reading": reading,
            "published": pub, "published_in_per_min": round(pub_in_min, 6),
            "refit": ref_max["b_per_min"],
            "refit_condition": f"{ref_max['glucose_pct']}%/{ref_max['temperature_C']}C",
            "paper_condition": ECOLI_PAPER["claims"]["C1_b_max"]["condition"],
            "rel_deviation": round(dev, 3),
            "reproduced": bool(dev <= CLAIM_TOL["C1"]),
        })

    # ---- C2: 温度使 b 增加 2.4 倍（固定葡萄糖，逐葡萄糖） ----
    pub_fold = ECOLI_PAPER["claims"]["C2_b_temp_fold"]["published"]
    for g in (0.1, 0.5, 1.0, 2.0):
        b29, b45 = mean_b(g, 29.0), mean_b(g, 45.0)
        if b29 and b45 and b29 > 0:
            fold = b45 / b29
            dev = _ratio(fold, pub_fold) - 1.0
            checks.append({
                "claim": "C2_b_temp_fold", "reading": f"{g}% glucose",
                "published": pub_fold, "refit": round(fold, 3),
                "rel_deviation": round(dev, 3),
                "reproduced": bool(dev <= CLAIM_TOL["C2"]),
            })

    # ---- C3: τ 端点与 2.6 倍（双读：论文值 vs 重拟合逐条件均值） ----
    for g, ck in ((0.1, "C3_tau_low_glucose"), (2.0, "C3_tau_high_glucose")):
        pub = ECOLI_PAPER["claims"][ck]["published"]
        vals = [f["tau"] for f in fits if f["glucose_pct"] == g]
        ref = float(np.mean(vals)) if vals else float("nan")
        dev = _ratio(ref, pub) - 1.0 if ref == ref else float("inf")
        checks.append({
            "claim": f"C3_tau_at_{g}%", "reading": "mean over temperatures",
            "published": pub, "refit": round(ref, 3) if ref == ref else None,
            "n_conditions": len(vals),
            "rel_deviation": round(dev, 3),
            "reproduced": bool(dev <= CLAIM_TOL["C3"]),
        })
    taus = {g: float(np.mean([f["tau"] for f in fits if f["glucose_pct"] == g]))
            for g in (0.1, 2.0)}
    if all(v > 0 for v in taus.values()):
        fold = taus[0.1] / taus[2.0]
        dev = _ratio(fold, ECOLI_PAPER["claims"]["C3_tau_fold"]["published"]) - 1.0
        checks.append({
            "claim": "C3_tau_glucose_fold", "reading": "mean tau(0.1%)/mean tau(2.0%)",
            "published": ECOLI_PAPER["claims"]["C3_tau_fold"]["published"],
            "refit": round(fold, 3),
            "rel_deviation": round(dev, 3),
            "reproduced": bool(dev <= CLAIM_TOL["C3"]),
        })

    # ---- C4: 37°C 翻倍时间落在文献区间 [20, 65.5] min ----
    lo_td = ECOLI_PAPER["claims"]["C4_td_literature_min_min"]["published"]
    hi_td = ECOLI_PAPER["claims"]["C4_td_literature_max_min"]["published"]
    for g in (0.1, 0.5, 1.0, 2.0):
        b37 = mean_b(g, 37.0)
        if b37 and b37 > 0:
            td = np.log(2) / b37
            in_range = bool(lo_td <= td <= hi_td)
            checks.append({
                "claim": "C4_doubling_time_in_literature_range",
                "reading": f"37C/{g}% glucose, t_d=ln2/b",
                "published_range_min": [lo_td, hi_td],
                "refit_min": round(float(td), 1),
                "reproduced": in_range,
            })

    # ---- C5: 下限温度 16.7 ± 1.2 °C（双读：Ratkowsky sqrt + 线性；另查收敛性） ----
    # 论文原文「Arrhenius extrapolation of b to zero growth rate」——纯 Arrhenius
    # 形式下 b→0 只在 T→0K 渐近达到，16.7°C 不可能来自 Arrhenius 本身；论文自述
    # 「a linear model yielded similar values」。故本审计按两种可复算读法核对：
    #   R1 Ratkowsky: sqrt(b) = a·(T - T_min)
    #   R2 线性:      b = a·(T - T_min)
    # 并核对论文的「across all glucose levels 收敛」：逐葡萄糖 T_min 的散布。
    pub_t = ECOLI_PAPER["claims"]["C5_T_min_C"]["published"]
    for g in (None, 0.1, 0.5, 1.0, 2.0):
        temps, bs = [], []
        for gg in ((g,) if g else (0.1, 0.5, 1.0, 2.0)):
            for T in (29.0, 31.0, 33.0, 35.0, 37.0, 39.0, 41.0, 43.0, 45.0):
                b = mean_b(gg, T)
                if b and b > 0:
                    temps.append(T)
                    bs.append(b)
        if len(temps) < 4:
            continue
        label = "pooled" if g is None else f"{g}% glucose"
        # R1 Ratkowsky
        A = np.vstack([np.asarray(temps), np.ones(len(temps))]).T
        (a1, i1), *_ = np.linalg.lstsq(A, np.sqrt(np.asarray(bs)), rcond=None)
        tmin1 = -i1 / a1 if a1 > 0 else float("nan")
        # R2 linear
        (a2, i2), *_ = np.linalg.lstsq(A, np.asarray(bs), rcond=None)
        tmin2 = -i2 / a2 if a2 > 0 else float("nan")
        for name, tmin in (("C5_T_min_ratkowsky", tmin1), ("C5_T_min_linear", tmin2)):
            dev = abs(tmin - pub_t) / pub_t if tmin == tmin else float("inf")
            checks.append({
                "claim": name, "reading": f"{label}, b->0 extrapolation",
                "published": pub_t, "published_sd": ECOLI_PAPER["claims"]["C5_T_min_C"]["sd"],
                "refit": round(float(tmin), 2) if tmin == tmin else None,
                "n_points": len(temps),
                "rel_deviation": round(dev, 3),
                "reproduced": bool(dev <= CLAIM_TOL["C5"]),
            })
    # 收敛性：逐葡萄糖 Ratkowsky T_min 的极差（论文声称 across all glucose 收敛）
    per_g = []
    for g in (0.1, 0.5, 1.0, 2.0):
        temps, bs = [], []
        for T in (29.0, 31.0, 33.0, 35.0, 37.0, 39.0, 41.0, 43.0, 45.0):
            b = mean_b(g, T)
            if b and b > 0:
                temps.append(T)
                bs.append(b)
        if len(temps) >= 4:
            A = np.vstack([np.asarray(temps), np.ones(len(temps))]).T
            (a1, i1), *_ = np.linalg.lstsq(A, np.sqrt(np.asarray(bs)), rcond=None)
            if a1 > 0:
                per_g.append(-i1 / a1)
    if len(per_g) >= 3:
        spread = max(per_g) - min(per_g)
        checks.append({
            "claim": "C5_T_min_convergence_across_glucose",
            "reading": "per-glucose Ratkowsky T_min spread (paper: converged, sd~1.2C)",
            "published_sd": ECOLI_PAPER["claims"]["C5_T_min_C"]["sd"],
            "refit": round(float(spread), 2),
            "per_glucose": [round(v, 2) for v in per_g],
            "reproduced": bool(spread <= 2 * ECOLI_PAPER["claims"]["C5_T_min_C"]["sd"]),
        })

    # ---- C6: b 随葡萄糖升高而下降（Spearman 符号检验，逐温度） ----
    for T in (29.0, 37.0, 45.0):
        gs = [0.1, 0.5, 1.0, 2.0]
        bs = [mean_b(g, T) for g in gs]
        if all(b is not None and b > 0 for b in bs):
            decreasing = all(bs[i] >= bs[i + 1] for i in range(len(bs) - 1))
            checks.append({
                "claim": "C6_b_decreases_with_glucose", "reading": f"T={T}C",
                "published": "monotonic decrease",
                "refit": {str(g): round(b, 6) for g, b in zip(gs, bs)},
                "reproduced": bool(decreasing),
            })
    return checks


# ------------------------------------------------------------------
# 5. 证书
# ------------------------------------------------------------------
def audit_ecoli_paper(data_dir: Optional[str] = None) -> Dict[str, Any]:
    """完整审计：校验 → 提取 → 拟合 → claim 核对 → 证书。fail-loud。"""
    src = verify_source() if data_dir is None else {"n_files": 0, "files": {},
                                                    "data_doi": ECOLI_PAPER["data_doi"],
                                                    "note": "custom data_dir, sha256 check skipped"}
    conds = extract_growth_curves(data_dir)
    fits = [fit_condition(c) for c in conds]

    # 数据真实性：噪声地板（纪律 4）
    nrs = [f["noise_ratio"] for f in fits if f["noise_ratio"] is not None]
    median_nr = float(np.median(nrs)) if nrs else float("nan")
    n_suspicious = sum(1 for f in fits
                       if f["noise_ratio"] is not None and f["noise_ratio"] < 0.3)

    # 可识别性（纪律 3）：τ 撞宽边界 = 数据把参数拉到声称区间之外；
    # 近优解参数迥异 = 似然谷平坦。两者都削弱「论文具体参数值」的可复现性。
    n_tau_bound = sum(1 for f in fits if f["params_at_bound"][1])
    n_valley = sum(1 for f in fits if f["n_distinct_near_optimal"] >= 3)

    checks = check_claims(fits)
    n_repro = sum(1 for c in checks if c.get("reproduced"))

    # ---- 证书级 verdict（优先级即诚实性优先） ----
    if n_suspicious >= max(1, len(fits) // 4):
        verdict = "DATA_PROVENANCE_SUSPECT"
        basis = (f"{n_suspicious}/{len(fits)} 条件拟合残差低于技术重复噪声地板 0.3× 以下"
                 "——数据疑被平滑或合成，参数比对无效")
    elif median_nr > 3.0:
        verdict = "STRUCTURAL_MISFIT"
        basis = (f"中位 noise_ratio={median_nr:.2f}——模型形式系统性失配"
                 "（残差远超技术重复噪声）")
    elif n_tau_bound >= len(fits) // 4:
        verdict = "UNDERDETERMINED"
        basis = (f"{n_tau_bound}/{len(fits)} 条件的不对称参数 τ 撞搜索边界，"
                 f"且 {n_valley} 条件存在 ≥3 个参数迥异的近优解——(b,τ) 联合不可识别，"
                 "论文的具体参数值无法被确认也无法被证伪")
    elif n_repro == len(checks):
        verdict = "CONSISTENT"
        basis = f"全部 {len(checks)} 项 claim 在容差内复现"
    else:
        verdict = "PARTIALLY_REPRODUCED"
        basis = (f"{n_repro}/{len(checks)} 项 claim 复现；未复现项见 claim_checks。"
                 "数据为真（noise_ratio 中位 %.2f）、模型形式充分（R² 见 conditions），"
                 "但论文的具体参数声明仅部分可复现" % median_nr)

    cert = {
        "key": ECOLI_PAPER["key"],
        "title": ECOLI_PAPER["title"],
        "paper": f"{ECOLI_PAPER['authors']} ({ECOLI_PAPER['year']}) {ECOLI_PAPER['journal']}",
        "doi": ECOLI_PAPER["doi"],
        "domain": "生长动力学（广义 logistic）",
        "model": ECOLI_PAPER["model"],
        "evidence_grade": EVIDENCE_GRADE,
        "data_source": {
            "type": "auto_download",
            "doi": ECOLI_PAPER["data_doi"],
            "url": ECOLI_PAPER["data_url"],
            "license": ECOLI_PAPER["data_license"],
            "sha256_verified": data_dir is None,
            "n_files": src.get("n_files"),
            "files": src.get("files"),
        },
        "n_conditions": len(fits),
        "data_quality": {
            "median_noise_ratio": round(median_nr, 3) if median_nr == median_nr else None,
            "n_below_noise_floor": n_suspicious,
            "interpretation": ("rmse≈σ/√n ⇒ 真实实验数据（重复噪声可解释拟合残差）"
                               if 0.5 <= median_nr <= 2.0 else
                               "rmse≫σ/√n ⇒ 模型形式失配" if median_nr > 2.0 else
                               "rmse≪σ/√n ⇒ 数据疑被平滑/合成"),
        },
        "identifiability": {
            "n_tau_at_bound": n_tau_bound,
            "n_conditions_valley": n_valley,
            "note": ("τ 与 b 沿似然谷强相关；τ 撞宽边界（0.3/10）或存在 ≥3 个参数迥异"
                     "近优解的条件，其 (b,τ) 具体值不应被单独引用。起点全部收敛到同一"
                     "参数点则表明拟合数值稳定（见 conditions.n_distinct_near_optimal）。"),
        },
        "claim_checks": checks,
        "n_claims_reproduced": n_repro,
        "n_claims_total": len(checks),
        "conditions": fits,
        "verdict": verdict,
        "verdict_basis": basis,
        "provenance_note": (
            "数据为 Zenodo 机器可读原始文件（sha256 校验），模型与拟合均由本仓库"
            "程序化执行；claim 数字逐条转录自论文 PMC 全文。本证书为独立复现级"
            "审计，但复现的是「论文 claim vs 论文自有原始数据」，"
            "不等同于对论文科学价值的整体裁决。"),
    }
    return cert


def run_published_data_audit(out_path: Optional[str] = None) -> Dict[str, Any]:
    """入口：跑审计并写 JSON + Markdown 报告。"""
    cert = audit_ecoli_paper()
    report = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "mode": "published_data_audit",
        "engine": "GC-01 auto-extracted grade",
        "evidence_grade": EVIDENCE_GRADE,
        "scope": "González-Siso et al. 2026 (Sci Rep), Zenodo 19416053 raw data",
        "n_certificates": 1,
        "counts": {cert["verdict"]: 1},
        "certificates": [cert],
    }
    op = out_path or os.path.join(ROOT, "data", "published_data_audit.json")
    json.dump(report, open(op, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    md = os.path.splitext(op)[0] + "_report.md"
    _write_md(report, md)
    print(f"[published-data-audit] verdict={cert['verdict']} "
          f"claims={cert['n_claims_reproduced']}/{cert['n_claims_total']} "
          f"conditions={cert['n_conditions']}")
    print(f"[published-data-audit] wrote {op} + {md}")
    return report


def _write_md(report: Dict[str, Any], path: str) -> None:
    c = report["certificates"][0]
    L = []
    L.append("# 已发表原始数据自动提取审计（GC-01 · auto-extracted 级）\n")
    L.append(f"生成时间：{report['generated_at']}　引擎：{report['engine']}\n")
    L.append(f"证书：{c['paper']}，DOI {c['doi']}\n")
    L.append(f"数据：Zenodo {c['data_source']['doi']}（{c['data_source']['license']}），"
             f"{c['data_source']['n_files']} 个文件 sha256 校验"
             f"{'通过' if c['data_source']['sha256_verified'] else '跳过（自定义目录）'}\n")
    L.append(f"判定：**{c['verdict']}**（{c['n_claims_reproduced']}/{c['n_claims_total']} 项 claim 复现）\n")
    L.append(f"> {c['verdict_basis']}\n")
    L.append("## Claim 逐条核对\n")
    L.append("| Claim | 读法 | 论文值 | 重拟合 | 相对偏离 | 复现 |")
    L.append("|---|---|---|---|---|---|")
    for ck in c["claim_checks"]:
        pub = ck.get("published", ck.get("published_range_min", "-"))
        ref = ck.get("refit", ck.get("refit_min", "-"))
        dev = ck.get("rel_deviation", "-")
        L.append("| {} | {} | {} | {} | {} | {} |".format(
            ck["claim"], ck.get("reading", "-"), pub, ref, dev,
            "是" if ck.get("reproduced") else "**否**"))
    L.append("\n## 数据质量与可识别性\n")
    dq, idf = c["data_quality"], c["identifiability"]
    L.append(f"- 噪声地板中位 noise_ratio = {dq['median_noise_ratio']}（{dq['interpretation']}）")
    L.append(f"- τ 撞宽边界条件 {idf['n_tau_at_bound']}/{c['n_conditions']}；"
             f"似然谷平坦（≥3 个参数迥异近优解）条件 {idf['n_conditions_valley']}/{c['n_conditions']}")
    L.append(f"- {idf['note']}\n")
    L.append("## 36 条件拟合明细\n")
    L.append("| 葡萄糖% | 温度°C | b (/min) | τ | c | R² | rmse | noise_ratio | 近优解(总数/迥异) |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for f in c["conditions"]:
        L.append("| {} | {} | {} | {} | {} | {} | {} | {} | {}/{} |".format(
            f["glucose_pct"], f["temperature_C"], f["b_per_min"], f["tau"], f["c"],
            f["r2"], f["rmse"], f["noise_ratio"], f["n_near_optimal"],
            f["n_distinct_near_optimal"]))
    L.append("")
    open(path, "w", encoding="utf-8").write("\n".join(L))
    print(f"[published-data-audit] wrote {path}")


if __name__ == "__main__":
    run_published_data_audit()
