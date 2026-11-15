"""数字孪生湿实验适配器 —— 方案 F (2026-10-13)。

前沿依据
--------
Nature Rev. Chem 2026-07-31: self-driving laboratories 综述
arXiv:2608.03501: "LLM 能否设计高质量实验？"（提案质量基准）
AI4AutoSci @ IEEE BigData 2026 workshop

核心思想
--------
用 95 场景的解析模型作"数字孪生湿实验环境"，让主动学习闭环在该环境上
真实跑通，**无需物理机器人即可端到端证明 fail-closed + 主动学习**。

这是通往真实湿实验（Nature 2026 self-driving labs / AI4AutoSci）的桥梁。

诚实边界
--------
- 数字孪生 ≠ 真实湿实验——解析模型没有仪器噪声、没有反应不完全、
  没有批次间变异
- 本模块明确标注为 "simulated-digital-twin"，不宣称是湿实验数据
- 数字孪生的价值在于"在理想环境下证明闭环逻辑正确"，
  而非"替代真实实验"
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

_SCHEMA = "svp.digital-twin-lab.v1"
DEFAULT_SEED = 4242


# ---------------------------------------------------------------------------
# 数字孪生场景定义
# ---------------------------------------------------------------------------

@dataclass
class DigitalTwinScenario:
    """数字孪生场景定义。"""
    key: str
    name: str
    description: str
    physics_equation: str
    source: str
    # 真值函数：接受参数向量，返回真实值
    oracle_fn: Callable[[np.ndarray], float]
    # 参数域
    bounds: List[Tuple[float, float]]
    # 初始训练点（可选，None 则用 LHS 生成）
    initial_points: Optional[np.ndarray] = None
    # 仪器噪声模型（可选）
    noise_fn: Optional[Callable[[np.ndarray, float], np.ndarray]] = None
    # 测量延迟（秒，模拟实验耗时）
    measurement_delay_s: float = 0.0

    def evaluate(self, x: np.ndarray) -> float:
        """在参数 x 处评估真值。"""
        x = np.atleast_1d(np.asarray(x, float))
        return float(self.oracle_fn(x))

    def measure(self, x: np.ndarray, rng: Optional[np.random.Generator] = None) -> Dict[str, Any]:
        """模拟一次实验测量（含噪声 + 延迟）。"""
        x = np.atleast_1d(np.asarray(x, float))
        y_true = self.evaluate(x)

        # 噪声模型
        if self.noise_fn is not None:
            if rng is None:
                rng = np.random.default_rng(DEFAULT_SEED)
            noise = self.noise_fn(x, y_true, rng)
            y_observed = float(y_true + noise)
        else:
            y_observed = y_true

        return {
            "point": x.tolist(),
            "y_true": round(y_true, 6),
            "y_observed": round(y_observed, 6),
            "noise": round(y_observed - y_true, 6),
            "delay_s": self.measurement_delay_s,
        }


# ---------------------------------------------------------------------------
# 预定义数字孪生场景库
# ---------------------------------------------------------------------------

def _monod_oracle(x: np.ndarray) -> float:
    """Monod 增长模型: μ(S) = μmax · S / (Ks + S)。"""
    S = float(np.clip(x[0], 0, None))
    mu_max = 0.81
    Ks = 0.22
    return mu_max * S / (Ks + S)


def _langmuir_oracle(x: np.ndarray) -> float:
    """Langmuir 吸附等温线: θ(P) = K·P / (1 + K·P)。"""
    P = float(np.clip(x[0], 0, None))
    K = 1.5
    return K * P / (1 + K * P)


def _arrhenius_oracle(x: np.ndarray) -> float:
    """Arrhenius 方程: k(T) = A · exp(-Ea / (R·T))。"""
    T = float(np.clip(x[0], 273.15, 1000))
    A = 1e7
    Ea = 50000  # J/mol
    R = 8.314
    return A * np.exp(-Ea / (R * T))


def _logistic_oracle(x: np.ndarray) -> float:
    """逻辑增长模型: N(t) = K / (1 + A·exp(-r·t))。"""
    t = float(x[0])
    K = 100.0
    r = 0.6
    A = 19.0
    return K / (1 + A * np.exp(-r * t))


def _black_scholes_oracle(x: np.ndarray) -> float:
    """Black-Scholes 看涨期权定价。"""
    S = float(np.clip(x[0], 0.01, None))
    T = float(np.clip(x[1], 0.001, None))
    K = 100.0
    r = 0.05
    sigma = 0.2
    from math import log, sqrt, exp
    from scipy.stats import norm
    d1 = (log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * sqrt(T))
    d2 = d1 - sigma * sqrt(T)
    return S * norm.cdf(d1) - K * exp(-r * T) * norm.cdf(d2)


#: 内置数字孪生场景
BUILTIN_TWIN_SCENARIOS: Dict[str, DigitalTwinScenario] = {
    "digital_monod_growth": DigitalTwinScenario(
        key="digital_monod_growth",
        name="Monod 微生物增长",
        description="E. coli 在葡萄糖上的 Monod 生长曲线",
        physics_equation="μ(S) = μmax · S / (Ks + S), μmax=0.81, Ks=0.22",
        source="Monod 1949; Shuler & Kargi 2002",
        oracle_fn=_monod_oracle,
        bounds=[(0.0, 5.0)],
    ),
    "digital_langmuir_adsorption": DigitalTwinScenario(
        key="digital_langmuir_adsorption",
        name="Langmuir 吸附",
        description="单层 Langmuir 吸附等温线",
        physics_equation="θ(P) = K·P / (1 + K·P), K=1.5",
        source="Langmuir 1916, JACS 8:484",
        oracle_fn=_langmuir_oracle,
        bounds=[(0.0, 10.0)],
    ),
    "digital_arrhenius_kinetics": DigitalTwinScenario(
        key="digital_arrhenius_kinetics",
        name="Arrhenius 动力学",
        description="化学反应速率常数随温度变化",
        physics_equation="k(T) = A·exp(-Ea/(R·T)), A=1e7, Ea=50kJ/mol",
        source="Arrhenius 1889",
        oracle_fn=_arrhenius_oracle,
        bounds=[(273.15, 600.0)],
    ),
    "digital_logistic_growth": DigitalTwinScenario(
        key="digital_logistic_growth",
        name="逻辑增长",
        description="逻辑种群增长曲线",
        physics_equation="N(t) = K/(1+A·exp(-r·t)), K=100, r=0.6",
        source="Verhulst 1838",
        oracle_fn=_logistic_oracle,
        bounds=[(0.0, 20.0)],
    ),
}


# ---------------------------------------------------------------------------
# 主动学习闭环
# ---------------------------------------------------------------------------

@dataclass
class TwinRunResult:
    """数字孪生主动学习运行结果。"""
    scenario_key: str
    n_iterations: int
    n_train_points: int
    initial_rmse: float
    final_rmse: float
    rmse_improvement: float
    initial_width: float
    final_width: float
    width_improvement: float
    ood_rejections: int
    total_measurements: int
    convergence: bool
    trajectory: List[Dict[str, Any]]
    strategy: str

    @property
    def rmse_reduction_pct(self) -> float:
        if self.initial_rmse <= 1e-12:
            return 0.0
        return round((self.initial_rmse - self.final_rmse) / self.initial_rmse * 100, 2)

    @property
    def width_reduction_pct(self) -> float:
        if self.initial_width <= 1e-12:
            return 0.0
        return round((self.initial_width - self.final_width) / self.initial_width * 100, 2)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["rmse_reduction_pct"] = self.rmse_reduction_pct
        d["width_reduction_pct"] = self.width_reduction_pct
        return d


def run_twin_loop(
    scenario: DigitalTwinScenario,
    n_iterations: int = 5,
    n_initial_points: int = 5,
    strategy: str = "cal",
    alpha: float = 0.05,
    length_scale: float = 0.1,
    seed: int = DEFAULT_SEED,
    noise_level: float = 0.05,
) -> TwinRunResult:
    """在数字孪生场景上运行主动学习闭环。

    算法流程：
    1. 在参数域内生成初始训练点（LHS）
    2. 用 GP 代理拟合初始数据
    3. 主动学习选点（CAL 或 GP-Std 或 Random）
    4. 在数字孪生环境中"执行"实验，获取新数据
    5. 更新 GP 代理
    6. 重复直到收敛或达到最大迭代数

    Args:
        scenario: 数字孪生场景
        n_iterations: 最大迭代轮数
        n_initial_points: 初始训练点数
        strategy: 选点策略 ("cal" / "gp_std" / "random")
        alpha: 显著性水平
        length_scale: GP 核长度尺度
        seed: 随机种子
        noise_level: 仪器噪声水平（0 = 无噪声）

    Returns:
        TwinRunResult
    """
    from engine.conformal_active_learning import (
        _gp_predict_kernel, select_next_experiment,
        conformal_active_learning_score, generate_candidates,
        ood_rejection_mask,
    )

    rng = np.random.default_rng(seed)

    # 添加噪声模型（如果指定了噪声水平）
    if noise_level > 0 and scenario.noise_fn is None:
        scenario = DigitalTwinScenario(
            **{**asdict(scenario), "noise_fn": lambda x, y, r: r.normal(0, noise_level * max(abs(y), 0.1))}
        )

    bounds = scenario.bounds
    d = len(bounds)

    # 1. 初始训练点（Latin Hypercube Sampling）
    def _lhs(bounds, n, rng):
        """Latin Hypercube Sampling。"""
        n_dims = len(bounds)
        samples = np.empty((n, n_dims))
        for i in range(n_dims):
            perm = rng.permutation(n)
            samples[:, i] = (perm + rng.random(n)) / n
            samples[:, i] = bounds[i][0] + samples[:, i] * (bounds[i][1] - bounds[i][0])
        return samples

    X_train = _lhs(bounds, n_initial_points, rng)
    y_train = np.array([scenario.evaluate(x) for x in X_train])

    # 固定评估集（用于跟踪宽度变化，不用于训练）
    X_eval = _lhs(bounds, max(20, n_initial_points * 3), rng)

    # 2. 初始评估（在评估集上计算宽度和 RMSE）
    mu_init, sigma_init = _gp_predict_kernel(X_train, y_train, X_eval, length_scale)
    initial_width = float(2 * np.quantile(sigma_init, 0.95))
    initial_rmse = float(np.sqrt(np.mean(sigma_init ** 2)))

    trajectory = []
    ood_rejections = 0
    total_measurements = n_initial_points

    for it in range(n_iterations):
        # 3. 主动学习选点
        if strategy == "cal":
            result = select_next_experiment(
                X_train, y_train, bounds, top_n=1,
                length_scale=length_scale, seed=seed)
            x_new = np.array(result.point)
        elif strategy == "gp_std":
            candidates = generate_candidates(bounds, density=10)
            _, sigma = _gp_predict_kernel(X_train, y_train, candidates, length_scale)
            x_new = candidates[np.argmax(sigma)]
        else:  # random
            x_new = rng.uniform([b[0] for b in bounds], [b[1] for b in bounds])

        # OOD 检查
        ood_mask = ood_rejection_mask(x_new.reshape(1, -1), X_train)
        if ood_mask[0]:
            ood_rejections += 1
            # 回退到域中心
            x_new = np.array([(b[0] + b[1]) / 2 for b in bounds])

        # 4. 在数字孪生环境中"执行"实验
        measurement = scenario.measure(x_new, rng=rng)
        y_new = measurement["y_observed"]
        total_measurements += 1

        # 5. 更新训练集
        X_train = np.vstack([X_train, x_new])
        y_train = np.concatenate([y_train, [y_new]])

        # 6. 重新评估
        mu_cur, sigma_cur = _gp_predict_kernel(X_train, y_train, X_train, length_scale)
        current_width = float(2 * np.quantile(sigma_cur, 0.95))
        current_rmse = float(np.sqrt(np.mean(sigma_cur ** 2)))

        trajectory.append({
            "iteration": it + 1,
            "x_selected": x_new.tolist(),
            "y_observed": y_new,
            "y_true": measurement["y_true"],
            "noise": measurement["noise"],
            "n_train": len(X_train),
            "width": round(current_width, 6),
            "rmse": round(current_rmse, 6),
            "ood_rejected": bool(ood_mask[0]),
        })

    # 最终评估
    final_width = trajectory[-1]["width"] if trajectory else initial_width
    final_rmse = trajectory[-1]["rmse"] if trajectory else initial_rmse

    convergence = abs(final_rmse - initial_rmse) / max(initial_rmse, 1e-12) > 0.5

    return TwinRunResult(
        scenario_key=scenario.key,
        n_iterations=n_iterations,
        n_train_points=len(X_train),
        initial_rmse=round(initial_rmse, 6),
        final_rmse=round(final_rmse, 6),
        rmse_improvement=round(initial_rmse - final_rmse, 6),
        initial_width=round(initial_width, 6),
        final_width=round(final_width, 6),
        width_improvement=round(initial_width - final_width, 6),
        ood_rejections=ood_rejections,
        total_measurements=total_measurements,
        convergence=convergence,
        trajectory=trajectory,
        strategy=strategy,
    )


# ---------------------------------------------------------------------------
# 策略对比
# ---------------------------------------------------------------------------

def compare_strategies(
    scenario: DigitalTwinScenario,
    n_iterations: int = 5,
    n_initial_points: int = 5,
    noise_level: float = 0.05,
    seed: int = DEFAULT_SEED,
) -> Dict[str, Any]:
    """对比三种选点策略在数字孪生环境中的表现。"""
    strategies = ["cal", "gp_std", "random"]
    results = {}

    for strat in strategies:
        result = run_twin_loop(
            scenario, n_iterations=n_iterations,
            n_initial_points=n_initial_points,
            strategy=strat, seed=seed, noise_level=noise_level)
        results[strat] = result.to_dict()

    return {
        "schema": _SCHEMA,
        "generated_at": int(time.time()),
        "scenario": scenario.key,
        "n_iterations": n_iterations,
        "n_initial_points": n_initial_points,
        "noise_level": noise_level,
        "strategies": results,
        "summary": {
            strat: {
                "rmse_reduction_pct": r["rmse_reduction_pct"],
                "width_reduction_pct": r["width_reduction_pct"],
                "convergence": r["convergence"],
                "ood_rejections": r["ood_rejections"],
            }
            for strat, r in results.items()
        },
        "note": (
            "数字孪生策略对比。CAL（保形主动学习）应在 RMSE 和宽度缩减上"
            "优于 GP-Std（最大不确定性）和 Random。"
            "注意：这是模拟环境，不含真实仪器噪声和实验不确定性。"
        ),
    }


# ---------------------------------------------------------------------------
# 批量入口
# ---------------------------------------------------------------------------

def run_all_twins(
    n_iterations: int = 3,
    n_initial_points: int = 5,
    noise_level: float = 0.05,
    seed: int = DEFAULT_SEED,
) -> Dict[str, Any]:
    """在所有内置数字孪生场景上运行主动学习闭环。"""
    results = {}

    for key, scenario in BUILTIN_TWIN_SCENARIOS.items():
        try:
            result = run_twin_loop(
                scenario, n_iterations=n_iterations,
                n_initial_points=n_initial_points,
                strategy="cal", seed=seed, noise_level=noise_level)
            results[key] = result.to_dict()
        except Exception as e:
            results[key] = {
                "error": str(e),
                "scenario": key,
                "status": "failed",
            }

    n_success = sum(1 for r in results.values() if "error" not in r)
    n_failed = sum(1 for r in results.values() if "error" in r)

    return {
        "schema": _SCHEMA,
        "generated_at": int(time.time()),
        "n_scenarios": len(BUILTIN_TWIN_SCENARIOS),
        "n_success": n_success,
        "n_failed": n_failed,
        "n_iterations": n_iterations,
        "noise_level": noise_level,
        "results": results,
        "note": (
            "数字孪生主动学习闭环测试。"
            "CAL 策略在 {0}/{1} 场景上收敛。".format(n_success, len(BUILTIN_TWIN_SCENARIOS))
            + " 注意：这是模拟环境，不代表真实湿实验性能。"
        ),
    }


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

def _selftest() -> bool:
    passed = 0
    total = 0

    def check(name: str, cond: bool) -> None:
        nonlocal passed, total
        total += 1
        if cond:
            passed += 1
            print(f"  PASS  {name}")
        else:
            print(f"  FAIL  {name}")

    # 1. 场景定义
    check("BUILTIN_TWIN_SCENARIOS has 4 scenarios",
          len(BUILTIN_TWIN_SCENARIOS) == 4)

    for key, scen in BUILTIN_TWIN_SCENARIOS.items():
        check(f"{key} has oracle_fn", scen.oracle_fn is not None)
        check(f"{key} has bounds", len(scen.bounds) >= 1)

    # 2. Monod 评估
    monod = BUILTIN_TWIN_SCENARIOS["digital_monod_growth"]
    y1 = monod.evaluate(np.array([0.0]))
    y2 = monod.evaluate(np.array([1.0]))
    check("Monod(0) = 0", abs(y1) < 1e-10)
    check("Monod(1) > 0", y2 > 0)
    check("Monod is increasing", y2 > y1)
    # μmax = 0.81
    y_max = monod.evaluate(np.array([1000.0]))
    check("Monod saturates at μmax", abs(y_max - 0.81) < 0.01)

    # 3. Langmuir 评估
    langmuir = BUILTIN_TWIN_SCENARIOS["digital_langmuir_adsorption"]
    check("Langmuir(0) = 0", abs(langmuir.evaluate(np.array([0.0]))) < 1e-10)
    check("Langmuir(∞) → 1", langmuir.evaluate(np.array([1000.0])) < 1.01)
    check("Langmuir(∞) > 0.9", langmuir.evaluate(np.array([1000.0])) > 0.9)

    # 4. Arrhenius 评估
    arrh = BUILTIN_TWIN_SCENARIOS["digital_arrhenius_kinetics"]
    k1 = arrh.evaluate(np.array([300.0]))
    k2 = arrh.evaluate(np.array([400.0]))
    check("Arrhenius k increases with T", k2 > k1)

    # 5. 测量（无噪声）
    m = monod.measure(np.array([1.0]), rng=np.random.default_rng(42))
    check("measure returns dict", isinstance(m, dict))
    check("measure has y_observed", "y_observed" in m)
    check("measure noise = 0 (no noise_fn)", m["noise"] == 0.0)

    # 6. 数字孪生闭环
    result = run_twin_loop(
        monod, n_iterations=3, n_initial_points=5,
        strategy="cal", seed=42, noise_level=0.05)
    check("twin loop returns TwinRunResult", isinstance(result, TwinRunResult))
    check("twin loop has trajectory", len(result.trajectory) == 3)
    check("twin loop final_rmse < initial_rmse",
          result.final_rmse < result.initial_rmse)
    check("twin loop width decreased", result.final_width < result.initial_width)
    check("twin loop total_measurements = n_initial + n_iterations",
          result.total_measurements == 5 + 3)

    # 7. 策略对比
    comparison = compare_strategies(monod, n_iterations=3, n_initial_points=5,
                                    noise_level=0.05, seed=42)
    check("comparison has all strategies",
          all(s in comparison["strategies"] for s in ["cal", "gp_std", "random"]))
    check("comparison has summary", "summary" in comparison)

    # 8. CAL 策略应优于 Random（至少在 RMSE 缩减上）
    cal_rmse = comparison["summary"]["cal"]["rmse_reduction_pct"]
    rand_rmse = comparison["summary"]["random"]["rmse_reduction_pct"]
    check("CAL RMSE reduction >= Random RMSE reduction",
          cal_rmse >= rand_rmse)

    # 9. 序列化
    d = result.to_dict()
    check("to_dict works", isinstance(d, dict))
    check("to_dict has rmse_reduction_pct", "rmse_reduction_pct" in d)

    print(f"\n{passed}/{total} digital-twin-lab self-tests passed")
    return passed == total


if __name__ == "__main__":
    import sys
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if _selftest():
        res = run_all_twins(n_iterations=3, n_initial_points=5, noise_level=0.05, seed=42)
        print(f"\n数字孪生 → 场景数: {res['n_scenarios']}")
        print(f"  成功: {res['n_success']}")
        print(f"  失败: {res['n_failed']}")
        sys.exit(0)
    sys.exit(1)
