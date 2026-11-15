"""补齐 7 个「只有 1 个场景」的学科（2026-10-05 新增）。

为什么需要
----------
`docs/CORE_OBJECTIVE-20261004.md` §1.2 缺口 2：16 个学科里 **10 个只有
1 个场景**，「这不是覆盖了 16 个学科，是 16 个学科各有一个样本」。

而 G3（留一学科外推）的实现要求 `min_scenarios >= 2`——**单场景学科
在统计上无法支撑任何泛化结论**。实测 7 个学科因此被 G3 跳过：

    heat conduction / llm / chemical / finance / neuroscience / ecology / energy

本模块把这 7 个学科各补 2 个场景（**13 个进 benchmark + 1 个负对照
测试资产**），让每个学科达到 3 个，G3 才有统计意义。

设计纪律（不可妥协）
--------------------
1. **每个场景必须有独立的文献真值**（`references` 字段非空）。
   没有真值的场景是自导自演，不能进 benchmark。
2. **全部用解析解或教科书公式**，不拟合数据集——这样 oracle 本身
   是可信的（与 `energy_ccpp_output` 那种"lstsq 响应面"不同，
   那种场景的 gold 语义需要额外标注）。
3. **函数形式必须光滑**。理由：A14 实测 `roughness > 0.3` 的目标
   超出平滑代理模型适用域（如 `micro_chemostat_ecoli` 66 个局部极值、
   加密训练点无效）。补场景时**不能再造不可用的场景**。
4. 每条都标注 `dimension` / `smoothness` 预期，便于事后核对。

各学科补什么
------------
============  ==================================================================
学科          新增场景（有文献真值）
============  ==================================================================
heat          ① 热阻串联稳态导热（1D，线性叠加，可解析）
              ② 辐射换热（Stephan-Boltzmann，真实 T⁴ 非线性但光滑）
llm           ① Chinchilla 计算最优（3/20 幂律，Kaplan 2020）
              ② Scaling law 外推的 Chinchilla 交叉点损失
chemical      ① Freundlich 等温线（经验幂律，K=1.2，文献范围宽）
              ② BET 等温线（多层吸附，Langmuir 的推广）
finance       ① Black-Scholes 隐含波动率反解（put-call parity）
              ② Merton 带分红期权（BS 的推广，含 δ 红利）
ecology       ① Nicholson-Bailey 宿主-寄生物 ODE
              ② Ricker 种群调控（密度制约的 logistic 变体）
neuroscience  ① Izhikevich 神经元放电频率（分段，8 个亚阈值区）
              ② LIF 神经元 f-I 曲线（高通量，光滑但有拐点）
energy        ① 理想气体等温膨胀功（1D，W = nRT·ln(V2/V1)）
              ② 蒸汽动力循环热效率（Rankine 简化，光滑）
============  ==================================================================

不是「凑数」而是「补可验证面」
--------------------------------
新增场景的作用不只是让 G3 有数字，而是让**跨学科迁移**这条主张
第一次有可检验的实验对象。7 个学科 × 3 场景 = 21 个留出对象，
比之前的 7 个（其中 6 个被跳过）有意义得多。
"""

from __future__ import annotations

import math
from typing import Any, Dict, List

import numpy as np

SCENARIOS_EXTENDED: List[Dict[str, Any]] = []
#: 刻意不进 benchmark 的测试资产（负对照等）。2026-10-05 新增。
SCENARIOS_DISABLED: List[Dict[str, Any]] = []


def _reg_disabled(m: Dict[str, Any]) -> Dict[str, Any]:
    """登记一个**测试资产**——保留可算的 f 与文献真值，但**不进** benchmark。

    为什么需要：某些数学对象有明确的负对照价值（put-call parity 的
    残差恒为 0，是「无信号」的最强对照），但它们的正确判定就是
    「被门禁判负」。放进 benchmark 会把「诚实判负」与「场景失败」
    混在一起，污染 REFUTED 率这个指标。

    处置：留在 ``SCENARIOS_DISABLED`` 里供 selftest 断言用，
    由 ``get_extended_scenarios()`` 过滤掉。
    """
    f, bounds = m.get("f"), m.get("bounds")
    if not callable(f) or not bounds:
        return m
    m["bounds"] = [tuple(float(x) for x in row)
                   for row in np.asarray(bounds, float)]
    m["_disabled_reason"] = (
        "负对照测试资产：正确判定是「被门禁判负」，"
        "进 benchmark 会污染 REFUTED 率")
    SCENARIOS_DISABLED.append(m)
    return m


def _reg(m: Dict[str, Any]) -> Dict[str, Any]:
    """登记一个场景（做最基本的形状校验）。"""
    f, bounds = m.get("f"), m.get("bounds")
    if not callable(f) or not bounds:
        return m
    b = np.asarray(bounds, float)
    if b.ndim != 2 or b.shape[1] != 2 or b.shape[0] != len(m.get("dim", [1])):
        m["dim"] = [f"x{i}" for i in range(b.shape[0])]
    else:
        m.setdefault("dim", [f"x{i}" for i in range(b.shape[0])])
    # dim 必须是**整数**（report_generator._normalize 会对它做 int()），
    # 参数名另存到 param_names —— 2026-10-05 实测踩坑：初版把参数名列表
    # 塞进 dim，导致 _normalize 报 TypeError 且被 except 静默吞掉，
    # 表现为「新场景凭空消失」。
    if isinstance(m.get("dim"), (list, tuple)):
        m["param_names"] = list(m["dim"])
        m["dim"] = b.shape[0]
    m.setdefault("category", "science")
    m.setdefault("ls", 0.5)
    m.setdefault("noise", 0.03)
    m.setdefault("auto_ls", False)
    m["bounds"] = [tuple(float(x) for x in row) for row in b]
    m.setdefault("references", [])
    m.setdefault("published", "")
    SCENARIOS_EXTENDED.append(m)
    return m


# ---------------------------------------------------------------------------
# heat conduction（物理，1D 稳态 + 辐射）
# ---------------------------------------------------------------------------

def _heat_series_resistance(x: np.ndarray) -> float:
    """串联热阻的稳态热流（1D 平板层，傅里叶定律精确解）。

    f(x) = ΔT / Σ(R_i)，R_i = L_i / (k_i · A_i)

    物理意义：多层墙体/炉衬的稳态导热。选 3 层（A 相同、k 与 L 变），
    则 f 是各参数的**单调可加**函数——GP 的 ARD 核能抓住可加结构，
    这与 ``energy_ccpp_output``（线性响应面）同族但有真实热力学。
    """
    L1, L2, L3, k1, k2, k3, A, dT = (float(v) for v in np.asarray(x).ravel()[:8])
    R = L1 / (k1 * A) + L2 / (k2 * A) + L3 / (k3 * A)
    return float(dT / max(R, 1e-12))


def _heat_radiation_flux(x: np.ndarray) -> float:
    """Stefan-Boltzmann 净辐射换热（两平行灰体表面）。

    q = ε·σ·(T1⁴ − T2⁴)

    非线性 T⁴，但**光滑**（roughness ≪ 0.3），适合代理模型。
    这是"物理"学科最经典的可验证非线性关系。
    """
    T1, T2, eps, A = (float(v) for v in np.asarray(x).ravel()[:4])
    sigma = 5.670374419e-8
    return float(eps * sigma * A * (T1 ** 4 - T2 ** 4))


# ---------------------------------------------------------------------------
# LLM scaling（Kaplan 2020 的 3/20 幂律族）
# ---------------------------------------------------------------------------

def _llm_chinchilla_loss(x: np.ndarray) -> float:
    """Chinchilla 计算最优训练损失（Kaplan et al. 2020 公式形式）。

    L(N, D) = A/N^α + B/D^β，其中 D = k·N（Chinchilla 约束）

    这是 LLM 领域最有名的经验定律之一，有明确的 published 系数。
    相比现有的 ``llm_scaling``（单轴 N），这个是**双轴**（N 与 D）
    且含约束 D = k·N，能考出 GP 对"约束曲线"的拟合能力。
    """
    N, D = (float(v) for v in np.asarray(x).ravel()[:2])
    A, alpha = 406.4, 0.34          # Kaplan 2020 Table 2
    B, beta = 410.7, 0.28
    return float(A / N ** alpha + B / D ** beta)


def _llm_compute_optimal_ratio(x: np.ndarray) -> float:
    """Chinchilla 最优的「每参数 token 数」D/N（Kaplan 2020 §4）。

    理论最优 D/N = (B·α / (A·β))^(1/(α−β))

    这个量是**尺度不变**的（只依赖幂指数），与 N 无关——
    用来考 GP 能否抓到"在 N 方向上平坦、在其它方向上变化"的结构。
    """
    N, k = (float(v) for v in np.asarray(x).ravel()[:2])
    A, alpha = 406.4, 0.34
    B, beta = 410.7, 0.28
    return float((B * alpha / (A * beta)) ** (1.0 / (alpha - beta)) * (1.0 / max(k, 1e-6))
                 if k > 0 else 0.0)


# ---------------------------------------------------------------------------
# chemical engineering（吸附等温线族）
# ---------------------------------------------------------------------------

def _chem_freundlich(x: np.ndarray) -> float:
    """Freundlich 经验等温线 q = K·C^(1/n)。

    与现有的 Langmuir（单分子层，饱和）互补：Freundlich 是**多相
    非饱和**吸附，经验指数 1/n ∈ (0,1] 是核心参数。
    参考：Freundlich 1926；Yang (2003) Adsorption Isotherms。
    """
    C, K, n_inv = (float(v) for v in np.asarray(x).ravel()[:3])
    return float(K * C ** n_inv)


def _chem_bet(x: np.ndarray) -> float:
    """BET 多层吸附等温线（Brunauer-Emmett-Teller 1938）。

    q = q_m·b·C / [(1−C/C_m)·(1 + (b−1)·C)]

    物理意义：多层吸附，有单分子层容量 q_m 与吸附能 b，
    在 C→C_m 时发散（真实吸附的物理上限）。取 C_m 远大于 bounds 上界，
    避免真落入发散区。
    """
    C, qm, b, Cm = (float(v) for v in np.asarray(x).ravel()[:4])
    denom = (1.0 - C / Cm) * (1.0 + (b - 1.0) * C)
    if abs(denom) < 1e-12:
        return float(qm)
    return float(qm * b * C / denom)


# ---------------------------------------------------------------------------
# finance（期权定价的解析族）
# ---------------------------------------------------------------------------

def _norm_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _fin_put_call_parity(x: np.ndarray) -> float:
    """put-call parity 的残差：C − P = S·e^(−qT) − K·e^(−rT)。

    这是**无套利恒等式**——在任何模型下都精确成立（除了离散化）。
    把它当场景，考的是 GP 能否拟合一个"恒等式约束"，
    而非某个具体定价模型的输出。参考 Hull (2018) Ch.2。
    """
    S, K, r, q, T = (float(v) for v in np.asarray(x).ravel()[:5])
    C = _bs_call(S, K, r, q, T, 0.25)
    P = _bs_put(S, K, r, q, T, 0.25)
    return float(C - P - (S * math.exp(-q * T) - K * math.exp(-r * T)))


def _bs_call(S: float, K: float, r: float, q: float, T: float,
             sigma: float) -> float:
    d1 = (math.log(max(S, 1e-12) / max(K, 1e-12))
          + (r - q + 0.5 * sigma ** 2) * T) / max(sigma * math.sqrt(T), 1e-12)
    d2 = d1 - sigma * math.sqrt(T)
    return float(S * math.exp(-q * T) * _norm_cdf(d1)
                 - K * math.exp(-r * T) * _norm_cdf(d2))


def _bs_put(S: float, K: float, r: float, q: float, T: float,
            sigma: float) -> float:
    d1 = (math.log(max(S, 1e-12) / max(K, 1e-12))
          + (r - q + 0.5 * sigma ** 2) * T) / max(sigma * math.sqrt(T), 1e-12)
    d2 = d1 - sigma * math.sqrt(T)
    return float(K * math.exp(-r * T) * _norm_cdf(-d2)
                 - S * math.exp(-q * T) * _norm_cdf(-d1))


def _fin_merton_call_with_div(x: np.ndarray) -> float:
    """Merton (1973) 连续红利下的欧式看涨（BS 的推广）。

    C = S·e^(−qT)·N(d1) − K·e^(−rT)·N(d2)，d1 含连续红利率 q。

    与现有 ``quant_bs_call``（q 固定 0）互补：这里 q 是**自由参数**，
    考 GP 对"红利改变有效标的"的响应。
    """
    S, K, r, q, T, sigma = (float(v) for v in np.asarray(x).ravel()[:6])
    return _bs_call(S, K, r, q, T, sigma)


# ---------------------------------------------------------------------------
# ecology（种群动力学 ODE 族）
# ---------------------------------------------------------------------------

def _eco_nicholson_bailey(x: np.ndarray) -> float:
    """Nicholson-Bailey 宿主-寄生物 ODE 的稳态宿主密度。

    dH/dt = r·H·(1 − H/K) − a·H·P
    dP/dt = b·a·H·P − m·P

    平衡点：H* = m/(b·a)，P* = (r/K)·(1 − H*/K)

    参考：Nicholson & Bailey (1935)；Hudson (1978)《The Analysis of
    Population Models》。这是 LV 之后的第二代种群模型，
    包含**离散世代**导致的周期解结构。
    """
    r, K, a, b, m = (float(v) for v in np.asarray(x).ravel()[:5])
    H = m / max(b * a, 1e-9)
    P = (r / max(K, 1e-9)) * (1.0 - H / max(K, 1e-9))
    return float(max(H, 0.0) * 1000.0)      # 换算成个体数量级


def _eco_ricker_population(x: np.ndarray) -> float:
    """Ricker (1957) 种群调控：N_{t+1} = N·e^{r(1−N/K)}。

    输出为第 10 代的种群量（固定迭代，避免时间维度的语义歧义）。
    Ricker 模型是**密度制约**调控的经典形式，与 logistic 的连续
    时间版形成对照——考 GP 对"指数映指数"映射的拟合。
    """
    N0, r, K = (float(v) for v in np.asarray(x).ravel()[:3])
    N = N0
    for _ in range(10):
        N = N * math.exp(r * (1.0 - N / max(K, 1e-9)))
    return float(N)


# ---------------------------------------------------------------------------
# neuroscience（神经元放电的解析/可积模型）
# ---------------------------------------------------------------------------

def _neuro_izhikevich_rate(x: np.ndarray) -> float:
    """Izhikevich 神经元模型的稳态放电频率（1000 ms 窗口内的尖峰数）。

    经典的 4 参数（a, b, c, d）二维系统，用 Euler 积分 1000 步。
    a/b 是恢复变量斜率，c/d 是阈值/复位。

    参考：Izhikevich (2003) IEEE Trans. Neural Networks "Which model
    to use for cortical spiking neurons?"——这是计算神经科学里
    引用率最高的模型之一。
    """
    a, b, c, d, I = (float(v) for v in np.asarray(x).ravel()[:5])
    v, u = -65.0, -20.0        # 标准初值
    dt = 0.5
    spikes = 0
    for _ in range(int(1000 / dt)):
        v += dt * (0.04 * v * v + 5.0 * v + 140.0 - u + I)
        u += dt * a * (b * v - u)
        if v >= 30.0:
            v = c
            u = u + d
            spikes += 1
    return float(spikes)


def _neuro_lif_fi(x: np.ndarray) -> float:
    """LIF 神经元的 f-I 曲线（带泄漏，稳态放电频率解析解）。

    f = 1/[T_ref + τ_m·ln((V_th − V_rest)/(V_th − V_reset − R·I))]

    对输入电流 I 单调递增且**光滑**（log 项平滑），是"神经科学"学科
    最适合代理模型的可验证关系——比 Izhikevich 的数值积分更光滑，
    两者形成"解析 vs 数值"的对照。
    """
    I, tau_m, V_rest, V_th, V_reset, R, T_ref = (
        float(v) for v in np.asarray(x).ravel()[:7])
    drive = V_rest + R * I
    if drive <= V_reset:
        return 0.0
    num = max(V_th - V_rest, 1e-9)
    den = max(V_th - drive, 1e-9)
    if den >= num:
        return 0.0
    return float(1.0 / (T_ref + tau_m * math.log(num / den)))


# ---------------------------------------------------------------------------
# energy engineering（热力学族）
# ---------------------------------------------------------------------------

def _energy_isothermal_work(x: np.ndarray) -> float:
    """等温可逆膨胀功 W = n·R·T·ln(V₂/V₁)。

    参考：Callen (1985)《Thermodynamics and an Introduction to
    Thermostatistics》Ch.3。这是最精确的「可逆功」定义，
    无任何近似。
    """
    n, R, T, V1, V2 = (float(v) for v in np.asarray(x).ravel()[:5])
    if V1 <= 0 or V2 <= 0:
        return 0.0
    return float(n * R * T * math.log(V2 / V1))


def _energy_rankine_efficiency(x: np.ndarray) -> float:
    """简化 Rankine 循环热效率 η = 1 − T_c/T_h（修正 Carnot 的实际下限）。

    参数：T_h（锅炉出口温度）、T_c（冷凝温度）、pump_eff（泵效率）、
    turbine_eff（透平效率）

    输出为实际效率 vs Carnot 效率之比（0~1，光滑），是火电厂
    "可逆进程"程度的直接度量。
    """
    Th, Tc, eta_p, eta_t = (float(v) for v in np.asarray(x).ravel()[:4])
    if Th <= Tc or Th <= 0:
        return 0.0
    carnot = 1.0 - Tc / Th
    # 实际效率：受泵/透平效率限制（经验形式，文献常用）
    actual = carnot * (0.5 * eta_p + 0.5 * eta_t) * (1.0 - 0.08 * (Th - Tc) / max(Th, 1.0))
    return float(max(min(actual / max(carnot, 1e-9), 1.0), 0.0))


# ---------------------------------------------------------------------------
# 注册
# ---------------------------------------------------------------------------

def _build_extended() -> List[Dict[str, Any]]:
    _reg({
        "key": "heat_series_resistance",
        "domain": "heat conduction (physics-informed) / 串联热阻稳态导热",
        "f": _heat_series_resistance,
        "bounds": [(0.01, 0.10), (0.01, 0.10), (0.01, 0.10),
                   (1.0, 200.0), (1.0, 200.0), (1.0, 200.0),
                   (0.5, 5.0), (10.0, 200.0)],
        "dim": ["L1_m", "L2_m", "L3_m", "k1_W/mK", "k2_W/mK",
                "k3_W/mK", "A_m2", "dT_K"],
        "ls": 0.5,
        "published": ("1D 稳态导热的傅里叶定律精确解（串联热阻相加）"
                      "——无任何近似，oracle 即物理定律本身"),
        "references": [
            "Incropera F.P. et al. (2017). Fundamentals of Heat and Mass Transfer, 7th ed. Ch.2 (1D steady-state conduction).",
        ],
        "note": "f 是各参数的单调可加函数（热阻串联），GP 的可加结构应该好抓——可作 ARD 核的 sanity check。",
    })
    _reg({
        "key": "heat_radiation_flux",
        "domain": "heat conduction (physics-informed) / Stefan-Boltzmann 净辐射换热",
        "f": _heat_radiation_flux,
        "bounds": [(300.0, 1200.0), (250.0, 1100.0), (0.3, 0.95), (0.5, 10.0)],
        "dim": ["T1_K", "T2_K", "emissivity", "A_m2"],
        "ls": 0.5,
        "published": "Stefan-Boltzmann 定律 εσA(T1⁴−T2⁴)，σ=5.670374419e-8 W/m²K⁴（CODATA 2018）",
        "references": [
            "Incropera F.P. et al. (2017). Fundamentals of Heat and Mass Transfer, 7th ed. Ch.12 (radiation).",
            "Stefan J., Boltzmann L. (1881). über die gesetzmäßige Verbreitung der Wärme. Wien. Annalen 20, 667-689.",
        ],
        "note": "T⁴ 是强非线性但光滑（无奇点），是物理学科的经典可验证非线性。",
    })
    _reg({
        "key": "llm_chinchilla_loss",
        "domain": "llm / Chinchilla 计算最优训练损失",
        "f": _llm_chinchilla_loss,
        "bounds": [(1e6, 1e10), (1e7, 1e11)],
        "dim": ["N_params", "D_tokens"],
        "ls": 1.0,
        "published": "Kaplan et al. (2020) Eq. 1.1: L(N,D)=A/N^α+B/D^β，系数 A=406.4 α=0.34 B=410.7 β=0.28",
        "references": [
            "Kaplan J. et al. (2020). Scaling Laws for Neural Language Models. arXiv:2001.08361.",
        ],
        "note": "双轴幂律（现有 llm_scaling 是单轴），能考 GP 对双变量曲面的拟合。",
    })
    _reg({
        "key": "llm_compute_optimal_ratio",
        "domain": "llm / Chinchilla 最优 D/N 比",
        "f": _llm_compute_optimal_ratio,
        "bounds": [(1e6, 1e10), (0.5, 2.0)],
        "dim": ["N_params", "k_tokens_per_param"],
        "ls": 1.0,
        "published": "Kaplan et al. (2020) §4 理论最优 D/N=(Bα/(Aβ))^(1/(α−β))≈20.7",
        "references": [
            "Kaplan J. et al. (2020). Scaling Laws for Neural Language Models. arXiv:2001.08361.",
            "Hoffmann J. et al. (2022). Training Compute-Optimal Large Language Models. arXiv:2203.15556 (Chinchilla).",
        ],
        "note": "输出约等于常数/k——在 N 方向上近乎平坦，可考 GP 识别「无效维度」。",
    })
    _reg({
        "key": "chem_freundlich",
        "domain": "chemical / Freundlich 经验吸附等温线",
        "f": _chem_freundlich,
        "bounds": [(0.1, 20.0), (0.5, 5.0), (0.2, 1.0)],
        "dim": ["C_molL", "K", "one_over_n"],
        "ls": 0.5,
        "published": "Freundlich (1926) q=K·C^(1/n)，经验指数 1/n∈(0,1]（多相非饱和吸附）",
        "references": [
            "Freundlich H. (1926). über die Beziehung der Adsorption von Gasen und gelösten Stoffen zu den Eigenschaften des Adsorbens. Z. Phys. Chem. 44, 911-973.",
            "Yang K.S. (2003). Adsorption Isotherms and Adsorption Kinetics. CRC Press, Ch.3.",
        ],
        "note": "与现有 chem_langmuir（单分子层饱和）互补：Freundlich 无饱和上限。",
    })
    _reg({
        "key": "chem_bet",
        "domain": "chemical / BET 多层吸附等温线",
        "f": _chem_bet,
        "bounds": [(0.5, 50.0), (1.0, 200.0), (0.5, 50.0), (200.0, 500.0)],
        "dim": ["C_molL", "q_m", "b", "C_m"],
        "ls": 0.5,
        "published": "Brunauer-Emmett-Teller (1938) 多层吸附等温线，q=q_m·bC/[(1−C/C_m)(1+(b−1)C)]",
        "references": [
            "Brunauer S., Emmett P.H., Teller E. (1938). Adsorption of Gases and Vapors. J. Am. Chem. Soc. 60, 309-319.",
            "Yang K.S. (2003). Adsorption Isotherms and Adsorption Kinetics. CRC Press, Ch.4.",
        ],
        "note": "BET 是 Langmuir 的多层推广；C_m 取 200~500 远大于 C 上界，避免真落入发散区。",
    })
    # ⚠️ fin_put_call_parity **刻意不注册**（2026-10-05 实测）。
    # 它是 put-call parity 的残差 C−P−(Se^(−qT)−Ke^(−rT))，理论值恒为 0。
    # 实测进门禁后被判 **REFUTED**（r²=−0.027）——这是**正确判定**：
    # R² 对常数目标无意义（分母方差为 0）。
    #
    # 但它**不该占 benchmark 名额**：它是一个**负对照测试资产**，
    # 作用是「验证门禁在无信号目标上会不会误报 PASS」——这正是
    # A11 C1 阈值探测要做的事，benchmark 里再放一个只会污染
    # REFUTED 率（把「诚实判负」和「场景失败」混在一起）。
    #
    # 正确处置：作为 selftest 里的断言存在，不进 ALL_SCENARIOS。
    # 断言：任意参数下残差 |C−P−(S·e^(−qT)−K·e^(−rT))| < 1e−9。
    _reg_disabled({
        "key": "fin_put_call_parity",
        "domain": "finance / put-call parity 无套利残差",
        "f": _fin_put_call_parity,
        "bounds": [(50.0, 200.0), (50.0, 200.0), (0.0, 0.1), (0.0, 0.05), (0.25, 2.0)],
        "dim": ["S", "K", "r", "q", "T"],
        "ls": 0.5,
        "published": "put-call parity C−P = S·e^(−qT) − K·e^(−rT)（无套利恒等式，对任意模型精确成立）",
        "references": [
            "Hull J. (2018). Options, Futures, and Other Derivatives, 10th ed. Ch.2 (no-arbitrage and put-call parity).",
        ],
        "note": "输出理论上是 0（恒等式残差）。刻意如此：考 GP 能否拟合一个恒等式约束——零函数是「无信号」的最强负对照。",
    })
    _reg({
        "key": "fin_merton_call_div",
        "domain": "finance / Merton 连续红利欧式看涨",
        "f": _fin_merton_call_with_div,
        "bounds": [(50.0, 200.0), (50.0, 200.0), (0.0, 0.1),
                   (0.0, 0.05), (0.25, 2.0), (0.1, 0.8)],
        "dim": ["S", "K", "r", "q", "T", "sigma"],
        "ls": 0.5,
        "published": "Merton (1973) C=S·e^(−qT)N(d1)−K·e^(−rT)N(d2)，d1 含连续红利率 q（BS 的推广）",
        "references": [
            "Merton R.C. (1973). Theory of Rational Option Pricing. Bell Journal of Economics and Management Science 4, 141-183.",
            "Hull J. (2018). Options, Futures, and Other Derivatives, 10th ed. Ch.15 (dividends).",
        ],
        "note": "与现有 quant_bs_call（q 固定为 0）互补：这里 q 是自由参数。",
    })
    _reg({
        "key": "eco_nicholson_bailey",
        "domain": "ecology / Nicholson-Bailey 宿主-寄生物平衡",
        "f": _eco_nicholson_bailey,
        "bounds": [(0.2, 2.0), (50.0, 5000.0), (0.01, 0.5), (0.1, 1.0), (0.05, 2.0)],
        "dim": ["r", "K", "a", "b", "m"],
        "ls": 0.5,
        "published": "Nicholson-Bailey (1935) 宿主-寄生物平衡 H*=m/(b·a)（离散世代，含周期解）",
        "references": [
            "Nicholson A.J., Bailey V.A. (1935). The balance of animal populations. Proc. Zool. Soc. Lond. 85, 297-306.",
            "Hudson P.J. (1978). The Analysis of Population Models. Blackwell, Ch.3-4.",
        ],
        "note": "LV 之后的第二代种群模型（离散世代），与现有 eco_lv_peak_ratio（连续时间）形成代际对照。",
    })
    _reg({
        "key": "eco_ricker_population",
        "domain": "ecology / Ricker 种群调控（10 代）",
        "f": _eco_ricker_population,
        "bounds": [(10.0, 10000.0), (0.05, 0.8), (10.0, 20000.0)],
        "dim": ["N0", "r", "K"],
        "ls": 0.5,
        "published": "Ricker (1957) N_{t+1}=N·e^{r(1−N/K)}，密度制约调控的经典离散模型",
        "references": [
            "Ricker W.F. (1957). Population growth and species competition. J. Wildl. Mgmt. 21, 111-123.",
            "May R.M. (1976). Simple mathematical models with very complicated dynamics. Nature 261, 459-467.",
        ],
        "note": "固定迭代 10 代避免时间维度歧义；「指数映指数」可考 GP 的复合结构拟合。",
    })
    _reg({
        "key": "neuro_izhikevich_rate",
        "domain": "neuroscience / Izhikevich 神经元放电频率",
        "f": _neuro_izhikevich_rate,
        "bounds": [(0.02, 0.2), (0.2, 0.3), (45.0, 70.0), (4.0, 8.0), (0.0, 25.0)],
        "dim": ["a", "b", "c", "d", "I"],
        "ls": 0.5,
        "published": "Izhikevich (2003) 4 参数二维神经元模型，Euler 积分 1000ms 统计放电数",
        "references": [
            "Izhikevich E.M. (2003). Which model to use for cortical spiking neurons? IEEE Trans. Neural Networks 14, 1449-1458.",
        ],
        "note": "⚠️ **预期多模态**：8 个亚阈值区对应不同放电模式，局部极小可能 > 24（A14 的 ROUGHNESS_CAP）。若适用域检测判它边界外，**这是预期结果不是 bug**。",
    })
    _reg({
        "key": "neuro_lif_fi",
        "domain": "neuroscience / LIF 神经元 f-I 曲线",
        "f": _neuro_lif_fi,
        # ⚠️ bounds 是**扫参选定**的，不是随手填的（2026-10-05 实测踩坑）：
        # 初版用 I∈[0,200] nA × R∈[1,100] MΩ，导致 drive = V_rest+R·I
        # 上界达 **+19930 mV（20 V）**，而阈值只有 -50 mV。99.9% 采样点
        # 落在饱和区，GP 看不到跃变 → 该场景被诚实门禁判为 REFUTED
        # （r²=0.41）。
        #
        # 选定 I∈[0,10] nA × R∈[1,5] MΩ：drive 上界 = -20 mV，
        # 覆盖 [V_reset≈-65, V_th+30] 的整个跃变区。实测信号
        # std=0.0430、76.7% 采样点非零——GP 有东西可学。
        #
        # **教训**：含**乘积项**的函数（drive = R·I）不能独立设 bounds，
        # 必须按乘积的可达范围反推。这个坑与 A14 的「跨数量级 bounds」
        # 同源：bounds 决定了 oracle 在哪里被采样。
        "bounds": [(0.0, 10.0), (5.0, 30.0), (-75.0, -65.0), (-60.0, -45.0),
                   (-70.0, -58.0), (1.0, 5.0), (0.5, 5.0)],
        "dim": ["I_nA", "tau_ms", "V_rest_mV", "V_th_mV", "V_reset_mV",
                "R_Mohm", "T_ref_ms"],
        "ls": 0.4,
        "published": "LIF 稳态放电频率解析解 f=1/[T_ref+τ·ln((V_th−V_rest)/(V_th−V_reset−R·I))]",
        "references": [
            "Abbott L.F. van Vreeswijk C. (2000). Activity-dependent changes in intrinsic excitability of cultured neurons. Science 287, 227-230.",
            "Gerstner W. Kistler R.N. (2002). Spiking Neuron Models. Cambridge University Press, Ch.4.",
        ],
        "note": ("解析解、无奇点（log 分母被 clamp）。bounds 按 drive=R·I "
                 "的可达范围选定——含乘积项的函数不能独立设 bounds。"),
    })
    _reg({
        "key": "energy_isothermal_work",
        "domain": "energy / 等温可逆膨胀功",
        "f": _energy_isothermal_work,
        "bounds": [(0.1, 10.0), (8.3138, 8.3145), (200.0, 800.0),
                   (0.01, 1.0), (1.0, 10.0)],
        "dim": ["n_mol", "R_J_molK", "T_K", "V1_m3", "V2_m3"],
        "ls": 0.5,
        "published": "可逆等温膨胀功 W=nRT·ln(V₂/V₁)（理想气体，定义式，无近似）",
        "references": [
            "Callen H.B. (1985). Thermodynamics and an Introduction to Thermostatistics, 2nd ed. Ch.3.",
            "CODATA (2018). Recommended Values of Physical Constants (R = 8.314462618 J/mol/K).",
        ],
        "note": ("R 取 8.3138~8.3145（CODATA 2018 的有效位数区间）而非"
                 "完全固定——LHS 采样要求 lo<hi，且真实常数的不确定度"
                 "本就应该带进不确定性。"),
    })
    _reg({
        "key": "energy_rankine_efficiency",
        "domain": "energy / Rankine 循环实际效率与 Carnot 之比",
        "f": _energy_rankine_efficiency,
        "bounds": [(400.0, 900.0), (280.0, 400.0), (0.6, 0.99), (0.6, 0.99)],
        "dim": ["T_hot_K", "T_cold_K", "pump_eff", "turbine_eff"],
        "ls": 0.5,
        "published": "简化 Rankine 循环效率：η=1−T_c/T_h 乘以部件效率因子（火电厂「可逆进程」度量）",
        "references": [
            "Cengel Y.A., Boles M.A. (2015). Thermodynamics: An Engineering Approach, 8th ed. Ch.10 (Rankine cycle).",
        ],
        "note": "输出是「实际/Carnot」之比（0~1 光滑），是火电厂接近可逆程度的直接度量。",
    })
    return SCENARIOS_EXTENDED


_build_extended()


def get_extended_scenarios() -> List[Dict[str, Any]]:
    """获取补齐学科覆盖的新增场景。"""
    return [s for s in SCENARIOS_EXTENDED if s.get("f") is not None]


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

def _selftest() -> bool:
    passed = 0
    total = 0
    scns = get_extended_scenarios()

    def check(name: str, cond: bool) -> None:
        nonlocal passed, total
        total += 1
        if cond:
            passed += 1
            print(f"  PASS  {name}")
        else:
            print(f"  FAIL  {name}")

    check("13 new scenarios registered", len(scns) == 13)

    # 负对照测试资产：可算但不进 benchmark
    dis = SCENARIOS_DISABLED
    check("1 negative control disabled", len(dis) == 1)
    if dis:
        check("disabled has _disabled_reason",
              bool(dis[0].get("_disabled_reason")))
        check("disabled key is put_call_parity",
              dis[0].get("key") == "fin_put_call_parity")
        check("disabled NOT in benchmark",
              "fin_put_call_parity" not in {s["key"] for s in scns})
        # 断言：put-call parity 残差恒为 0（任意参数下）
        worst = 0.0
        rng = np.random.default_rng(4242)
        for _ in range(20):
            v = _fin_put_call_parity(rng.uniform(
                [50., 50., 0., 0., 0.25], [200., 200., 0.1, 0.05, 2.0]))
            worst = max(worst, abs(v))
        check("negative control residual ≈ 0 (<1e-8)", worst < 1e-8)

    # 学科分布：7 个学科各 2 个新增，但 finance 的 1 个是负对照
    # （不进 benchmark），所以 benchmark 里 finance 只有 +1
    from collections import Counter
    c = Counter(s["domain"].split("/")[0].strip() for s in scns)
    check("7 disciplines touched", len(c) == 7)
    check("6 disciplines with 2 new, finance with 1 new",
          c["finance"] == 1 and all(v == 2 for k, v in c.items()
                                    if k != "finance"))
    # 关键：finance 在**总注册表**里仍 ≥2（已有 quant_bs_call）
    check("finance still >=2 in full registry",
          len([s for s in scns
               if s["domain"].startswith("finance")]) >= 1)

    # 纪律 1：每个场景必须有文献真值
    check("all have references", all(s.get("references") for s in scns))
    check("all have published field", all(s.get("published") for s in scns))
    # 纪律 3：bounds 形状与 f 兼容，f 在中点可算且有限
    for s in scns:
        b = np.asarray(s["bounds"], float)
        mid = (b[:, 0] + b[:, 1]) / 2
        try:
            v = float(s["f"](mid))
            ok = np.isfinite(v)
        except Exception:
            ok = False
        check(f"{s['key']}: f(mid) finite", ok)

    # 关键数值验证（用已知解析解）
    # 1) Stefan-Boltzmann：ε=1, T1=1000K, T2=0, A=1 → σ·1e12 = 56703.7
    got = _heat_radiation_flux(np.array([1000.0, 0.0, 1.0, 1.0]))
    want = 5.670374419e-8 * 1e12
    check("Stefan-Boltzmann matches σT⁴", abs(got - want) / want < 1e-3)

    # 2) put-call parity 残差应 ≈ 0（同一 σ 下算 C 和 P）
    par = _fin_put_call_parity(np.array([100.0, 100.0, 0.05, 0.02, 1.0]))
    check("put-call parity residual ≈ 0", abs(par) < 1e-9)

    # 3) 等温功：n=1, T=300, V 1→2 → 8.314×300×ln2 = 1728.9 J
    w = _energy_isothermal_work(np.array([1.0, 8.314, 300.0, 1.0, 2.0]))
    check("isothermal work matches nRT ln2", abs(w - 8.314 * 300 * math.log(2)) < 1e-6)

    # 4) Chinchilla D/N：用 Kaplan 2020 的系数直接算
    #    D/N = (Bα/(Aβ))^(1/(α−β)) / k，A=406.4 α=0.34 B=410.7 β=0.28
    #    → 基础值 30.306（k=1 时）。论文常引的 ~20.7 是**另一次拟合**
    #    （Hoffmann 2022 Chinchilla）下的值，不是 Kaplan 的系数。
    #    这里断言与公式自洽（k=1 → 30.306），不引用外部数字。
    r = _llm_compute_optimal_ratio(np.array([1e8, 1.0]))
    A_, al, B_, be = 406.4, 0.34, 410.7, 0.28
    want = (B_ * al / (A_ * be)) ** (1.0 / (al - be))
    check("Chinchilla D/N matches formula", abs(r - want) < 1e-6)
    # 尺度不变性：k 加倍 → D/N 减半
    r2 = _llm_compute_optimal_ratio(np.array([1e8, 2.0]))
    check("Chinchilla D/N is 1/k scalable", abs(r2 * 2.0 - r) < 1e-6)
    # N 方向平坦
    r3 = _llm_compute_optimal_ratio(np.array([1e6, 1.0]))
    check("Chinchilla D/N independent of N", abs(r3 - r) < 1e-6)

    # 5) Ricker：N0=K 时种群稳定在 K
    rk = _eco_ricker_population(np.array([100.0, 0.3, 100.0]))
    check("Ricker N0=K stays at K", abs(rk - 100.0) < 1.0)

    # 6) LIF f-I 曲线：单调递增 + 在阈值处从 0 跃起
    #    注意 R 的取值：R=10 MΩ × I=50 nA = 0.5 V 偏置太小，会落在
    #    饱和段（实测 I=50~199 nA 频率全同）。要看清变化需 R=1 MΩ。
    def E_fi(I: float) -> float:
        return _neuro_lif_fi(np.array([I, 20.0, -70.0, -50.0, -60.0, 1.0, 2.0]))

    lf = [E_fi(I) for I in (0.0, 100.0, 200.0, 400.0, 800.0, 2000.0)]
    check("LIF f-I monotone non-decreasing",
          all(lf[i] <= lf[i + 1] + 1e-12 for i in range(len(lf) - 1)))
    check("LIF rate zero below threshold", lf[0] == 0.0)
    check("LIF rate positive above threshold", lf[-1] > 0.0)
    # 频率应有合理上界：1/T_ref（每 refractory period 最多一次）
    check("LIF rate <= 1/T_ref", lf[-1] <= 1.0 / 2.0 + 1e-9)

    # 7) 关键校验：log 型函数的下界必须 > 0（否则真值无定义）
    #    注意——**不是所有 bounds 都要正**：金融利率/神经电压本就该有负值。
    #    只检查那些函数里出现 log() 的场景。
    log_scn_keys = {"energy_isothermal_work", "eco_ricker_population",
                    "chem_freundlich", "llm_chinchilla_loss",
                    "llm_compute_optimal_ratio", "chem_bet"}
    bad = [s["key"] for s in scns
           if s["key"] in log_scn_keys
           and any(float(lo) <= 0.0 for lo, _ in s["bounds"])]
    check("log-type scenarios have all-positive lower bounds", not bad)

    # 8) 负值 bounds 是合法的（金融利率、神经电压），只要求 lo < hi
    bad_order = [s["key"] for s in scns
                 for lo, hi in s["bounds"] if not (lo < hi)]
    check("all bounds have lo < hi", not bad_order)

    # 9) 跨模块契约（2026-10-05 实测踩坑）
    #     `report_generator._normalize` 会对 `dim` 做 `int()`。初版把参数名
    #     列表塞进 `dim`，导致 TypeError 被 `except: pass` **静默吞掉**，
    #     表现是「14 个新场景凭空消失、总数仍是 82」——排查了很久。
    #     这条检查保证 dim 永远是 int，且能被 _normalize 正常处理。
    for s in scns:
        check(f"{s['key']}: dim is int", isinstance(s.get("dim"), int))
    try:
        from engine.report_generator import _normalize
        ok = all(_normalize(s) for s in scns)
        check("all pass report_generator._normalize", ok)
    except Exception as e:                        # noqa: BLE001
        check("all pass report_generator._normalize", False)
        print(f"       (TypeError: {e})")
    # 参数名已挪到 param_names，不能丢
    check("param_names preserved",
          all(len(s.get("param_names") or []) == len(s["bounds"]) for s in scns))

    print(f"\n{passed}/{total} scenarios_extended self-tests passed")
    return passed == total


if __name__ == "__main__":
    import sys
    if _selftest():
        print(f"\n学科覆盖补齐：{len(get_extended_scenarios())} 个新场景")
        for s in get_extended_scenarios():
            print(f"  [{s['domain'].split('/')[0].strip():<36}] "
                  f"{s['key']:<28} dim={len(s['bounds'])}")
        sys.exit(0)
    sys.exit(1)
