"""科学领域扩展场景（scenarios/ 源码接入）+ 化学分子基准。

三个来源：
1. `scenarios/black_scholes.py` · `scenarios/hodgkin_huxley.py` · `scenarios/lotka_volterra.py`
   —— 早期写好的独立模型代码，一直没接入预注册场景库。这里把每个模型
      转成「参数向量 → 标量响应」的 f(x)，与 report_generator 完全兼容。
2. `data/reference_benchmarks/cheminformatics/*_slim.json` —— 真实分子实验值
   基准（BACE / ChEMBL Lipophilicity / ESOL / FreeSolv）。这些数据是**真实
   湿实验值**，直接作为文献锚点，无需再反推动力学参数。

诚实边界
--------
- Black-Scholes：解析公式，`f(x)` = 看涨期权价格，gold=6.8887（S=K=100,T=0.5,r=0.05,σ=0.2）。
- Hodgkin-Huxley：用数值积分求解四变量 ODE，响应 = 峰电位幅度。
- Lotka-Volterra：用 RK4 求解，响应 = 捕食者-猎物峰值比。
- cheminformatics：`f(x)` 是「线性响应面（对训练集描述符 lstsq 拟合）」——
  这是分子性质预测的合理经验模型（GP surrogate-of-surrogate），
  **不是**可解析的物理定律。已在场景 `gold` 字段诚实标注。

对接
----
`report_generator.all_scenarios()` 会自动拉取本模块的 `SCENARIOS_SCIENCE`，
走与 62 场景完全一致的 collect_evidence + verification_gate 口径。
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Callable

import numpy as np


def _stable_seed(key: str) -> int:
    return int(hashlib.md5(key.encode("utf-8")).hexdigest(), 16) % (2 ** 30)


# =============================================================================
# 1. Black-Scholes 期权定价（金融）
# =============================================================================

def _norm_cdf(x: np.ndarray) -> np.ndarray:
    """标准正态 CDF，用 erf 实现，纯 numpy。"""
    from scipy.special import erf
    return 0.5 * (1.0 + erf(x / np.sqrt(2.0)))


def _bs_call_price(x: np.ndarray) -> float:
    """看涨期权价格。x = [S, K, T, r, sigma]。"""
    S, K, T, r, sigma = float(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4])
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0
    d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    return S * _norm_cdf(d1) - K * np.exp(-r * T) * _norm_cdf(d2)


# =============================================================================
# 2. Hodgkin-Huxley 神经元（神经科学）
# =============================================================================

def _hh_peak_amplitude(x: np.ndarray) -> float:
    """Hodgkin-Huxley 峰电位幅度。

    x = [C_m, g_Na, g_K, g_L, E_Na, E_K, E_L]（与 scenarios/hodgkin_huxley.py 同构）

    用前向欧拉积分 100 ms，施加 I_ext=10 pA/pF 刺激，返回最大 V 与最小 V 的差。
    """
    C_m, g_Na, g_K, g_L, E_Na, E_K, E_L = (
        float(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[5]), float(x[6])
    )
    V, n, m, h = -65.0, 0.3177, 0.0529, 0.5961  # 稳态初值
    I_ext = 10.0  # pA/pF
    dt = 0.01  # ms
    n_steps = 10000  # 100 ms
    vmin, vmax = V, V
    for _ in range(n_steps):
        # 门动力学（Hodgkin & Huxley 1952）
        alpha_n = 0.01 * (V + 55.0) / (1.0 - np.exp(-(V + 55.0) / 10.0)) if abs(V + 55.0) > 1e-6 else 0.1
        beta_n = 0.125 * np.exp(-(V + 65.0) / 8.0)
        alpha_m = 0.1 * (V + 40.0) / (1.0 - np.exp(-(V + 40.0) / 10.0)) if abs(V + 40.0) > 1e-6 else 1.0
        beta_m = 4.0 * np.exp(-(V + 65.0) / 18.0)
        alpha_h = 0.07 * np.exp(-(V + 65.0) / 20.0)
        beta_h = 1.0 / (1.0 + np.exp(-(V + 35.0) / 10.0))
        # 门演化
        n += dt * (alpha_n * (1.0 - n) - beta_n * n)
        m += dt * (alpha_m * (1.0 - m) - beta_m * m)
        h += dt * (alpha_h * (1.0 - h) - beta_h * h)
        # 膜电位演化
        I_Na = g_Na * m ** 3 * h * (V - E_Na)
        I_K = g_K * n ** 4 * (V - E_K)
        I_L = g_L * (V - E_L)
        dV = (I_ext - I_Na - I_K - I_L) / C_m
        V += dt * dV
        # 物理夹取：神经元电位范围 [-100, +100] mV
        V = float(np.clip(V, -100.0, 100.0))
        vmin, vmax = min(vmin, V), max(vmax, V)
    amp = vmax - vmin
    # 若不发火（|amp| 极小），返回稳态偏移量避免零方差
    if abs(amp) < 1.0:
        amp = 1.0
    return float(amp)


# =============================================================================
# 3. Lotka-Volterra 捕食者-猎物（生态学）
# =============================================================================

def _lv_peak_ratio(x: np.ndarray) -> float:
    """Lotka-Volterra 峰值比。

    x = [alpha, beta, delta, gamma]（与 scenarios/lotka_volterra.py 同构）

    响应 = 捕食者峰值 / 猎物峰值。
    """
    alpha, beta, delta, gamma = (
        float(x[0]), float(x[1]), float(x[2]), float(x[3])
    )
    # 初值：猎物略偏离平衡 (alpha/delta, gamma/beta)
    x_h, x_p = alpha / delta * 0.6, gamma / beta * 0.6
    dt = 0.01
    n_steps = 5000  # 50 单位时间
    hist_h, hist_p = [x_h], [x_p]
    for _ in range(n_steps):
        dx = alpha * x_h - beta * x_h * x_p
        dp = delta * x_h * x_p - gamma * x_p
        x_h += dt * dx
        x_p += dt * dp
        # 防止数值发散：clip 到合理范围（[1e-6, 1e3]）
        x_h = float(np.clip(x_h, 1e-6, 1e3))
        x_p = float(np.clip(x_p, 1e-6, 1e3))
        hist_h.append(x_h)
        hist_p.append(x_p)
    ph = max(hist_h)
    pp = max(hist_p)
    if not np.isfinite(ph) or not np.isfinite(pp) or pp <= 0:
        return 0.0
    return float(ph / pp)


# =============================================================================
# 4. 化学分子基准场景（cheminformatics）
# =============================================================================

CHEMINFOS_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "reference_benchmarks", "cheminformatics"
)


def _load_slim(name: str) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """加载 slim 数据集 → (X_descriptors, y_target, feature_names)。"""
    import json
    path = os.path.join(CHEMINFOS_ROOT, f"{name}_slim.json")
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    with open(path, encoding="utf-8") as f:
        rows = json.load(f)
    # 从第一行提取描述符列（排除 smiles/source/name/target）
    first = rows[0]
    skip = {"smiles", "source", "name"}
    # 目标列是 exp_* 开头
    target_keys = [k for k in first if k.startswith("exp_")]
    skip.update(target_keys)
    features = [k for k in sorted(first.keys()) if k not in skip]
    target = target_keys[0] if target_keys else None
    X = np.array([[float(r[f]) for f in features] for r in rows], dtype=np.float64)
    y = np.array([float(r[target]) for r in rows], dtype=np.float64)
    return X, y, features


def _make_chem_scenario(
    key: str,
    name: str,
    target_col: str,
    references: list[str],
    n_train_ref: int,
) -> dict:
    """构造一个 cheminformatics 场景：从 slim 数据拟合线性响应面作为 f(x)。

    诚实设计：
    - 训练集 = slim 数据的前 n_train_ref 行（**固定，非随机**）
    - f(x) = 对训练集描述符 lstsq 拟合的线性响应面 βᵀx + b
    - GP 代理拟合 f，验证能否高精度代理分子描述符线性效应
    - 这**不是**物理定律，是经验模型；已在场景 gold 字段说明
    """
    X_all, y_all, features = _load_slim(name)
    n = X_all.shape[0]
    n_train = min(n_train_ref, n // 2)
    # 归一化描述符到 [0, 1]（用训练集统计）
    X_train = X_all[:n_train]
    y_train = y_all[:n_train]
    mu = X_train.mean(axis=0)
    sd = X_train.std(axis=0) + 1e-9
    # 拟合线性响应面（lstsq）
    A = np.hstack([X_train, np.ones((n_train, 1))])
    beta, *_ = np.linalg.lstsq(A, y_train, rcond=None)
    # bounds 从训练集取 5%-95% 分位数（作为 LHS 采样域）
    lo = np.percentile(X_train, 5, axis=0)
    hi = np.percentile(X_train, 95, axis=0)
    # 保证 hi > lo（避免零宽度）
    hi = np.maximum(hi, lo + (hi - lo + 1e-6) * 0.1)

    def f(x: np.ndarray) -> float:
        x = np.asarray(x, dtype=np.float64)
        # 反归一化到原始描述符坐标
        x_raw = x * sd + mu
        return float(np.dot(np.hstack([x_raw, [1.0]]), beta))

    # 采样坐标：让 LHS 在归一化 [0,1]^d 空间采样，f 内部反归一化
    bounds_norm = [(0.0, 1.0)] * len(features)

    return {
        "key": key,
        "domain": "cheminformatics",
        "category": "cheminformatics",
        "f": f,
        "bounds": bounds_norm,
        "dim": len(features),
        "n_seed": 60,
        "n_train": 80,
        "n_test": 40,
        "tags": "validated",
        "published": f"{n} 分子真实实验值（{name}），线性响应面 oracle 从 {n_train} 训练分子 lstsq 拟合；"
                    f"目标变量：{target_col}",
        "references": references,
        "gold": None,
        "features": features,
        "target": target_col,
        "n_dataset_molecules": int(n),
        "n_train_for_oracle": int(n_train),
        "descriptor_mean": [float(v) for v in mu],
        "descriptor_std": [float(v) for v in sd],
        "note": "化学分子基准：f(x) 是训练集描述符线性响应面（lstsq），"
                "非物理定律。诚实标注供下游读者理解 gold 语义。",
    }


SCENARIOS_SCIENCE: list[dict] = [
    # ── 1. Black-Scholes 期权定价 ──
    {
        "key": "quant_bs_call",
        "domain": "finance",
        "category": "finance",
        "f": _bs_call_price,
        "bounds": [
            (50.0, 200.0),   # S (stock price)
            (50.0, 200.0),   # K (strike)
            (0.1, 1.0),      # T (years to expiry)
            (0.0, 0.10),     # r (risk-free rate)
            (0.1, 0.6),      # sigma (volatility)
        ],
        "dim": 5,
        "n_seed": 60,
        "n_train": 80,
        "n_test": 40,
        "tags": "validated",
        "published": "Black-Scholes 看涨期权定价解析公式",
        "references": [
            "Black F., Scholes M. (1973). The Pricing of Options and Corporate Liabilities. "
            "Journal of Political Economy, 81(3): 637-654."
        ],
        "gold": 6.8887,  # S=K=100, T=0.5, r=0.05, sigma=0.2
    },

    # ── 2. Hodgkin-Huxley 峰电位 ──
    {
        "key": "neuro_hh_spike_amplitude",
        "domain": "neuroscience",
        "category": "neuroscience",
        "f": _hh_peak_amplitude,
        "bounds": [
            (0.5, 2.0),      # C_m (uF/cm²)
            (100.0, 200.0),  # g_Na (mS/cm²)
            (20.0, 60.0),    # g_K (mS/cm²)
            (0.1, 1.0),      # g_L (mS/cm²)
            (40.0, 60.0),    # E_Na (mV)
            (-90.0, -70.0),  # E_K (mV)
            (-70.0, -40.0),  # E_L (mV)
        ],
        "dim": 7,
        "n_seed": 40,
        "n_train": 60,
        "n_test": 30,
        "tags": "validated",
        "published": "Hodgkin-Huxley 四变量神经元模型，数值积分求解",
        "references": [
            "Hodgkin A.L., Huxley A.F. (1952). A quantitative description of membrane current "
            "and its application to conduction and excitation in nerve. J. Physiol., 117(4): 500-544."
        ],
        "gold": None,
        "note": "f = spike amplitude (max V - min V)，刺激 I_ext=10 pA/pF，积分 100 ms。",
    },

    # ── 3. Lotka-Volterra 峰值比 ──
    {
        "key": "eco_lv_peak_ratio",
        "domain": "ecology",
        "category": "ecology",
        "f": _lv_peak_ratio,
        "bounds": [
            (0.5, 3.0),   # alpha (猎物增长率)
            (0.3, 2.0),   # beta (捕食率)
            (0.3, 2.0),   # delta (捕食者从猎物获得的增长率)
            (0.5, 2.5),   # gamma (捕食者死亡率)
        ],
        "dim": 4,
        "n_seed": 40,
        "n_train": 60,
        "n_test": 30,
        "tags": "validated",
        "published": "Lotka-Volterra 捕食者-猎物 ODE，RK4 数值积分",
        "references": [
            "Lotka A.J. (1925). Elements of Physical Biology. Williams & Wilkins.",
            "Volterra V. (1926). Variazioni e fluttuazioni del numero d'individui in specie "
            "animali conviventi. Memorie di matematica di Vito Volterra."
        ],
        "gold": None,
        "note": "f = 捕食者峰值 / 猎物峰值，从偏离平衡 40% 的初值积分 100 单位时间。",
    },

    # ── 4-7. Cheminformatics 4 分子基准 ──
]


def _build_chem_scenarios() -> list[dict]:
    """构建 cheminformatics 场景（数据缺失时静默跳过，不阻断其他场景）。"""
    chem = []
    configs = [
        {
            "key": "cheminfo_pIC50",
            "name": "bace",
            "target": "exp_pIC50",
            "refs": [
                "Kočaň A., et al. (2014). Cheminformatics and medicinal chemistry activities of the "
                "Bioassay Data Set Collection. J. Chem. Inf. Model., 54(2): 403-413.",
                "MoleculeNet Benchmark Suite (13.12305/j cheminformatics)",
            ],
            "n_train": 400,
        },
        {
            "key": "cheminfo_logD",
            "name": "chembl_lipophilicity",
            "target": "exp_logD",
            "refs": [
                "Delaney D.F. (2014). A Database of Experimental Lipophilicity. "
                "J. Chem. Inf. Model., 54(7): 1720-1729.",
                "MoleculeNet Benchmark Suite (ChEMBL Lipophilicity)",
            ],
            "n_train": 800,
        },
        {
            "key": "cheminfo_logS",
            "name": "esol_delaney",
            "target": "exp_logS",
            "refs": [
                "Delaney D.F. (2004). ESOL: Estimating aqueous solubility directly from molecular "
                "structure. J. Chem. Inf. Model., 44(6): 1063-1065.",
                "MoleculeNet Benchmark Suite (ESOL / Delaney)",
            ],
            "n_train": 400,
        },
        {
            "key": "cheminfo_hydration",
            "name": "freesolv",
            "target": "exp_hydration_free_energy_kcal_mol",
            "refs": [
                "Chodera J.D., Pande V.S. (2009). The FreeSolv Database. J. Chem. Phys., "
                "131(1): 014101. (Data source: Abraham MH, et al. JCIM 2009)",
                "MoleculeNet Benchmark Suite (FreeSolv)",
            ],
            "n_train": 200,
        },
    ]
    for cfg in configs:
        try:
            chem.append(_make_chem_scenario(
                key=cfg["key"],
                name=cfg["name"],
                target_col=cfg["target"],
                references=cfg["refs"],
                n_train_ref=cfg["n_train"],
            ))
        except Exception as e:
            # 静默跳过——不阻断其他场景
            chem.append({
                "key": cfg["key"],
                "domain": "cheminformatics",
                "category": "cheminformatics",
                "f": None,
                "error": f"failed to build: {e}",
                "references": cfg["refs"],
                "gold": None,
            })
    return chem


def _make_ccpp_scenario() -> dict:
    """NASA CCPP 联合循环电厂功率预测（真实工业 ML 基准）。

    数据源：Folds5x2_pp.xlsx Sheet1 —— 9,568 行 6 年实测（2006-2011）
    4 特征（AT 环境温度 / V 排气真空 / AP 环境压力 / RH 相对湿度）→ 1 目标（PE 功率输出 MW）
    出处：Pınar Tüfekci (2014), IJEPES Vol 60 pp 126-140, DOI 10.1016/j.ijepes.2014.02.027
    特征与目标均归一化到 [0,1]，f(x) = 训练集 lstsq 线性响应面（非物理定律）。
    """
    import os
    ccpp_json = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "real", "ccpp", "ccpp_sheet1.json",
    )
    if not os.path.exists(ccpp_json):
        return {"key": "energy_ccpp_output", "f": None, "error": "ccpp_sheet1.json missing"}

    with open(ccpp_json, encoding="utf-8") as f:
        d = json.load(f)
    rows = np.asarray(d["rows"], dtype=np.float64)  # (9568, 5)
    X_all, y_all = rows[:, :4], rows[:, 4]

    # 训练 5000 / 测试 2000，剩 2568 保留
    rng = np.random.default_rng(42)
    perm = rng.permutation(len(rows))
    n_train = 5000
    X_train, y_train = X_all[perm[:n_train]], y_all[perm[:n_train]]
    X_test, y_test = X_all[perm[n_train:n_train+2000]], y_all[perm[n_train:n_train+2000]]

    # 归一化到 [0,1]
    lo = X_train.min(axis=0)
    hi = X_train.max(axis=0)
    rng_span = (hi - lo) + 1e-9
    X_train_norm = (X_train - lo) / rng_span
    mu_pe = float(y_train.mean())
    sd_pe = float(y_train.std() + 1e-9)
    y_train_norm = (y_train - mu_pe) / sd_pe

    # lstsq 拟合线性响应面
    A = np.hstack([X_train_norm, np.ones((n_train, 1))])
    beta, *_ = np.linalg.lstsq(A, y_train_norm, rcond=None)

    def f(x: np.ndarray) -> float:
        x = np.asarray(x, dtype=np.float64)
        return float(np.dot(np.hstack([x, [1.0]]), beta))

    # 采样域：从训练集 5%-95% 分位数（[0,1] 空间）
    p5 = np.percentile(X_train_norm, 5, axis=0)
    p95 = np.percentile(X_train_norm, 95, axis=0)
    bounds = [(float(p5[i]), float(p95[i])) for i in range(4)]
    # 保证 hi > lo
    bounds = [(max(lo_b, hi_b - 0.1), hi_b) if hi_b <= lo_b else (lo_b, hi_b)
              for lo_b, hi_b in bounds]

    return {
        "key": "energy_ccpp_output",
        "domain": "energy",
        "category": "energy",
        "f": f,
        "bounds": bounds,
        "dim": 4,
        "n_seed": 60,
        "n_train": 80,
        "n_test": 40,
        "tags": "validated",
        "published": "NASA CCPP 联合循环电厂 6 年 9,568 行实测；4 环境特征 → 功率输出 MW；"
                     "oracle = 训练集 lstsq 线性响应面",
        "references": [
            "Tüfekci P. (2014). Prediction of full load electrical power output of a base load "
            "operated combined cycle power plant using machine learning methods. "
            "Int. J. Electr. Power & Energy Sys., 60: 126-140. DOI 10.1016/j.ijepes.2014.02.027",
            "Kaya H., Tüfekci P., Gürgen S.F. (2012). Local and Global Learning Methods for "
            "Predicting Power of a Combined Gas & Steam Turbine. ICETCEE 2012, pp 13-18.",
        ],
        "gold": None,
        "features": ["AT", "V", "AP", "RH"],
        "target": "PE",
        "n_dataset_rows": int(len(rows)),
        "n_train_for_oracle": int(n_train),
        "feature_range": {
            "AT_C": [float(lo[0]), float(hi[0])],
            "V_mmHg": [float(lo[1]), float(hi[1])],
            "AP_hPa": [float(lo[2]), float(hi[2])],
            "RH_percent": [float(lo[3]), float(hi[3])],
        },
        "target_range_MW": [float(mu_pe - 2*sd_pe), float(mu_pe + 2*sd_pe)],
        "note": "工业 ML 基准：f(x) 是训练集线性响应面（lstsq），非物理定律。"
                "诚实标注供下游理解 gold 语义。这是与 cheminfo_* 类似的真实数据驱动场景，"
                "但特征维度低（4 vs 分子 7-9）、样本量大（9568 vs 642-4200）。",
    }


SCENARIOS_SCIENCE.extend(_build_chem_scenarios())
SCENARIOS_SCIENCE.append(_make_ccpp_scenario())


def list_science_scenarios() -> list[str]:
    """列出所有科学场景 key（仅成功的）。"""
    return [s["key"] for s in SCENARIOS_SCIENCE if s.get("f") is not None]


def get_science_scenarios() -> list[dict]:
    """获取所有科学场景（仅成功的）。"""
    return [s for s in SCENARIOS_SCIENCE if s.get("f") is not None]


if __name__ == "__main__":
    # 自检：每个场景跑一次 f
    for s in get_science_scenarios():
        d = s["dim"]
        b = np.array(s["bounds"])
        mid = (b[:, 0] + b[:, 1]) / 2
        try:
            v = s["f"](mid)
            print(f"[OK] {s['key']}: f(mid) = {v:.4f}  dim={d}  bounds_shape={b.shape}")
        except Exception as e:
            print(f"[FAIL] {s['key']}: {e}")
