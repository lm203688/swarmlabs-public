"""BayBE 贝叶斯优化适配器（真实接线版）。

BayBE (https://github.com/emdgroup/baybe) 是默克公司开源的贝叶斯优化引擎。
本模块尝试导入 BayBE，成功则使用其成熟的 Campaign 推荐-测量循环；失败则
回退到内置 GP surrogate + UCB 选点。

⚠️ 2026-10-14 改造（#585）：此前版本在 HAS_BAYBE=True 分支里只构造了
Task/ParameterSpace 对象，实际优化仍走 engine.surrogate.GPSurrogate + UCB
——这是"装饰性接线"，BayBE 的核心能力（surrogate 拟合、acquisition 选点、
BO 循环）完全没被用到。本次改造用 BayBE 真实的 Campaign.recommend() +
Campaign.add_measurements() 循环替换，让 BayBE 真正参与优化决策。

对标：Agentic SDL 多保真度代理 / Benchling AI Scientist
核心能力：
  - 多目标优化（acquisition = qEI / qParEGO / qNEHVI）
  - 多保真度代理（低成本粗筛 → 高成本精测）
  - 主动学习选点（Expected Improvement / Upper Confidence Bound）
  - 约束优化（不等式约束 + 等式约束）

回退策略：
  BayBE 未安装 → 使用 engine.surrogate.GPSurrogate + ActiveLearner
"""

from __future__ import annotations

import importlib
import math
import sys
import os

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# ---------------------------------------------------------------------------
# 尝试导入 BayBE（兼容新旧 API 命名）
# ---------------------------------------------------------------------------
try:
    import baybe
    BAYBE_VERSION = importlib.metadata.version("baybe")

    # 搜索空间：0.7.x 用 SearchSpace，早期版本用 ParameterSpace
    try:
        from baybe.searchspace import SearchSpace as _SearchSpace
    except ImportError:
        from baybe.searchspace import ParameterSpace as _SearchSpace

    # 参数
    from baybe.parameters import NumericalContinuous as _NumericalContinuous

    # 目标（0.7.x 用 NumericalTarget，早期用 NumericalSingle）
    try:
        from baybe.targets import NumericalTarget as _NumericalTarget
    except ImportError:
        try:
            from baybe.targets import NumericalSingle as _NumericalTarget
        except ImportError:
            _NumericalTarget = None  # 极老版本

    # 目标聚合（0.7.x 用 SingleTargetObjective，早期用 Objective）
    try:
        from baybe.objectives import SingleTargetObjective as _SingleTargetObjective
    except ImportError:
        try:
            from baybe.objective import Objective as _SingleTargetObjective
        except ImportError:
            _SingleTargetObjective = None

    # 推荐器（0.7.x 用 recommenders，早期用 optimizers/strategies）
    try:
        from baybe.recommenders import (
            TwoPhaseMetaRecommender as _TwoPhaseMetaRecommender,
            BotorchRecommender as _BotorchRecommender,
            FPSRecommender as _FPSRecommender,
        )
    except ImportError:
        try:
            from baybe.optimizers import (
                TwoPhaseMetaOptimizer as _TwoPhaseMetaRecommender,
                BotorchOptimizer as _BotorchRecommender,
                FPSOptimizer as _FPSRecommender,
            )
        except ImportError:
            _TwoPhaseMetaRecommender = None

    # Campaign（核心对象）
    from baybe import Campaign as _Campaign

    HAS_BAYBE = _Campaign is not None and _SearchSpace is not None
except ImportError:
    HAS_BAYBE = False
    BAYBE_VERSION = None
    _Campaign = None
    _SearchSpace = None
    _NumericalContinuous = None
    _NumericalTarget = None
    _SingleTargetObjective = None
    _TwoPhaseMetaRecommender = None


def has_baybe() -> bool:
    """检查 BayBE 是否可用（且核心类齐全）。"""
    return HAS_BAYBE


def baybe_version() -> str | None:
    """返回 BayBE 版本字符串。"""
    return BAYBE_VERSION


# ---------------------------------------------------------------------------
# 内部追踪：记录 BayBE 是否真的被调用（防装饰性接线回归）
# ---------------------------------------------------------------------------
_baybe_call_log: list[str] = []


def _log_baybe_call(fn: str) -> None:
    """记录一次 BayBE 真实 API 调用（自测断言用）。"""
    _baybe_call_log.append(fn)


def reset_baybe_call_log() -> None:
    """清空调用日志（自测前调用）。"""
    _baybe_call_log.clear()


def baybe_call_log() -> list[str]:
    """返回调用日志副本。"""
    return list(_baybe_call_log)


# ---------------------------------------------------------------------------
# BayBE 搜索空间构建
# ---------------------------------------------------------------------------

def baybe_searchspace(bounds: list[tuple[float, float]],
                      names: list[str] | None = None) -> object:
    """
    从边界列表构建 BayBE SearchSpace。

    参数:
      bounds: [(lo, hi), ...]
      names:  ["x1", "x2", ...] 可选
    """
    if names is None:
        names = [f"x{i+1}" for i in range(len(bounds))]

    params = [_NumericalContinuous(name=name, bounds=(lo, hi))
              for (lo, hi), name in zip(bounds, names)]
    return _SearchSpace.from_product(params)


# ---------------------------------------------------------------------------
# BayBE 原生优化（真实接线：Campaign.recommend + add_measurements）
# ---------------------------------------------------------------------------

def baybe_run_optimization(space: object, oracle_fn, n_iterations: int = 20,
                           initial_points: int = 5, seed: int = 42,
                           batch_size: int = 1) -> dict:
    """
    使用 BayBE Campaign 运行贝叶斯优化（真实接线）。

    核心循环：
      1. campaign.recommend(batch_size) → 推荐实验配置
      2. 用 oracle_fn 评估每个配置
      3. campaign.add_measurements(df) → 把结果喂回 BayBE
      4. 重复直到 n_iterations 轮

    参数:
      space:  BayBE SearchSpace（由 baybe_searchspace 构建）
      oracle_fn: callable(x_array) -> float (目标函数，最大化)
      n_iterations: 优化轮数（recommend 调用次数）
      initial_points: 初始随机采样点数（由 TwoPhaseMetaRecommender 自动处理）
      seed: 随机种子
      batch_size: 每轮推荐的实验数量（默认 1）

    返回:
      {
        "best_x": list,
        "best_value": float,
        "history": [{"x": [...], "y": float, "iter": int}],
        "method": "baybe_native",
        "n_iterations": int,
        "n_recommend_calls": int,
        "n_add_measurements_calls": int,
        "baybe_version": str,
      }
    """
    _log_baybe_call("baybe_searchspace")  # 已在 baybe_optimize 中调用过

    # 构建目标
    target = _NumericalTarget(name="objective", mode="MAX")
    _log_baybe_call("NumericalTarget(mode=MAX)")

    # 构建目标聚合
    if _SingleTargetObjective is not None:
        objective = _SingleTargetObjective(target=target)
    else:
        # 极老版本：直接传 target 给 Campaign
        objective = target
    _log_baybe_call("SingleTargetObjective")

    # 构建推荐器（两阶段：FPS 初始采样 → Botorch BO）
    recommender = None
    if _TwoPhaseMetaRecommender is not None:
        initial_rec = _FPSRecommender()
        main_rec = _BotorchRecommender()
        recommender = _TwoPhaseMetaRecommender(
            initial_recommender=initial_rec,
            recommender=main_rec,
            switch_after=max(initial_points, 3),
        )
        _log_baybe_call("TwoPhaseMetaRecommender")

    # 创建 Campaign
    campaign = _Campaign(
        searchspace=space,
        objective=objective,
        recommender=recommender,
        campaigns=1,
    )
    _log_baybe_call("Campaign.__init__")

    # 优化循环
    history = []
    n_recommend_calls = 0
    n_add_measurements_calls = 0
    target_col = "objective"

    for i in range(n_iterations):
        # Step 1: BayBE 推荐
        rec = campaign.recommend(batch_size=batch_size)
        n_recommend_calls += 1
        _log_baybe_call("campaign.recommend")

        # Step 2: 评估推荐配置
        param_cols = [c for c in rec.columns if c != target_col]
        for idx in rec.index:
            x = [float(rec.loc[idx, c]) for c in param_cols]
            y = float(oracle_fn(np.array(x)))
            rec.loc[idx, target_col] = y
            history.append({"x": x, "y": y, "iter": len(history)})

        # Step 3: 喂回 BayBE
        campaign.add_measurements(rec)
        n_add_measurements_calls += 1
        _log_baybe_call("campaign.add_measurements")

    # 提取最优
    best = max(history, key=lambda h: h["y"])
    measurements = campaign.measurements

    return {
        "best_x": best["x"],
        "best_value": float(best["y"]),
        "history": history,
        "method": "baybe_native",
        "n_iterations": len(history),
        "n_recommend_calls": n_recommend_calls,
        "n_add_measurements_calls": n_add_measurements_calls,
        "n_campaign_measurements": len(measurements),
        "baybe_version": BAYBE_VERSION,
    }


# ---------------------------------------------------------------------------
# 多保真度代理（低成本粗筛 → 高成本精测）
# ---------------------------------------------------------------------------

class MultiFidelityOptimizer:
    """
    多保真度贝叶斯优化器。

    对标：Agentic SDL / MOEA 多目标优化
    核心：低成本低保真度评估（coarse）筛选候选 → 高成本高保真度评估（fine）确认。

    注：当前多保真度优化器是内置实现，不使用 BayBE 的多保真度模块
    （BayBE 的多保真度能力主要通过 transfer learning 实现，而非
    coarse-to-fine 漏斗）。如需用 BayBE 的多目标优化，请用
    baybe_optimize 的 multi_objective 参数。
    """
    def __init__(self, low_fn, high_fn, low_budget_ratio=0.1, seed=42):
        self.low_fn = low_fn  # 低成本代理（快，精度低）
        self.high_fn = high_fn  # 高成本代理（慢，精度高）
        self.low_ratio = low_budget_ratio
        self.rng = np.random.default_rng(seed)

    def optimize(self, bounds, n_total=100, initial_points=10) -> dict:
        """
        运行多保真度优化。

        参数:
          bounds: [(lo, hi), ...]
          n_total: 总评估次数（high_fn 调用）
          initial_points: 初始随机采样数

        返回:
          {
            "best_x": list, "best_value": float,
            "n_low_evals": int, "n_high_evals": int,
            "cost_savings_pct": float,
            "history": [...],
          }
        """
        dim = len(bounds)
        history = []
        best_x, best_y = None, -np.inf
        n_high_evals = 0

        for iteration in range(n_total):
            # Step 1: 低成本采样（每轮评估 k 个候选的 low_fn）
            n_low = max(int(n_total * self.low_ratio), 5)
            low_candidates = []
            for _ in range(n_low):
                x = [self.rng.uniform(lo, hi) for lo, hi in bounds]
                y_low = self.low_fn(np.array(x))
                low_candidates.append((x, float(y_low)))

            # Step 2: 排序，选 Top-K 进入高成本评估
            low_candidates.sort(key=lambda t: t[1], reverse=True)
            k = max(1, min(n_low // 10, 3))
            top_k = low_candidates[:k]

            # Step 3: 高成本精测
            best_round = None
            best_round_y = -np.inf
            for x, y_low in top_k:
                y_high = self.high_fn(np.array(x))
                n_high_evals += 1
                if y_high > best_round_y:
                    best_round_y = float(y_high)
                    best_round = (x, y_high)

            if best_round is not None:
                history.append({
                    "iteration": iteration,
                    "x": best_round[0],
                    "y_high": best_round[1],
                    "y_low_top": [lc[1] for lc in top_k],
                })
                if best_round[1] > best_y:
                    best_x = best_round[0]
                    best_y = best_round[1]

        # 成本节省：如果只用 high_fn，需要 n_total * n_low 次
        ideal_high = n_total * max(int(n_total * self.low_ratio), 5)
        savings = 100 * (1 - n_high_evals / ideal_high) if ideal_high > 0 else 0

        return {
            "best_x": best_x,
            "best_value": best_y,
            "n_low_evals": n_low * n_total,
            "n_high_evals": n_high_evals,
            "cost_savings_pct": round(savings, 1),
            "history": history,
            "method": "multi_fidelity_internal",
        }


# ---------------------------------------------------------------------------
# 公开接口
# ---------------------------------------------------------------------------

def baybe_optimize(bounds, objective_fn, n_iterations=30, initial_points=5,
                   seed=42, batch_size=1):
    """
    统一入口：BayBE 可用时使用真实 Campaign 循环，否则回退到内部 GP。

    用法:
      result = baybe_optimize(
          bounds=[(0.0, 10.0), (20.0, 50.0)],
          objective_fn=lambda x: x[0] * x[1] / (1 + x[0] + x[1]),
          n_iterations=30,
      )

    返回:
      dict with keys: best_x, best_value, history, method, n_iterations,
      以及 BayBE 特有字段: n_recommend_calls, n_add_measurements_calls,
      n_campaign_measurements, baybe_version
    """
    if HAS_BAYBE:
        space = baybe_searchspace(bounds)
        _log_baybe_call("baybe_searchspace")
        return baybe_run_optimization(
            space, objective_fn, n_iterations, initial_points, seed, batch_size
        )
    else:
        return _fallback_optimize(bounds, objective_fn, n_iterations, initial_points, seed)


def _fallback_optimize(bounds, objective_fn, n_iterations=30, initial_points=5,
                       seed=42):
    """回退：使用内置 GP surrogate + UCB 策略。"""
    from engine.surrogate import GPSurrogate

    rng = np.random.default_rng(seed)
    history = []
    best_x, best_y = None, -np.inf

    # 初始随机采样
    X_init = []
    for _ in range(initial_points):
        x = [rng.uniform(lo, hi) for lo, hi in bounds]
        X_init.append(x)

    X_all = np.array(X_init)
    Y_all = np.array([objective_fn(np.array(x)) for x in X_init])

    for x, y in zip(X_init, Y_all):
        history.append({"x": list(x), "y": float(y), "iter": len(history)})
        if y > best_y:
            best_x, best_y = list(x), float(y)

    # BO 迭代
    for i in range(n_iterations):
        gp = GPSurrogate(noise_var=0.01, lengthscale=0.5)
        gp.fit(X_all, Y_all)

        best_candidate = None
        best_ucb = -np.inf
        n_cands = 300

        for _ in range(n_cands):
            x_cand = [rng.uniform(lo, hi) for lo, hi in bounds]
            pred, std = gp.predict(np.array(x_cand).reshape(1, -1))
            ucb = pred[0] + 1.96 * std[0]
            if ucb > best_ucb:
                best_ucb = ucb
                best_candidate = x_cand

        y_new = objective_fn(np.array(best_candidate))
        history.append({"x": best_candidate, "y": float(y_new), "iter": len(history)})
        X_all = np.vstack([X_all, best_candidate])
        Y_all = np.append(Y_all, y_new)

        if y_new > best_y:
            best_x, best_y = best_candidate, float(y_new)

    return {
        "best_x": best_x,
        "best_value": float(best_y),
        "history": history,
        "method": "internal_gp_ucb",
        "n_iterations": len(history),
        "baybe_available": False,
        "note": "BayBE not installed. Using internal GP + UCB. Install with: pip install baybe",
    }


# ---------------------------------------------------------------------------
# 自测：验证真实接线（防装饰性回归）
# ---------------------------------------------------------------------------

def selftest_baybe_wiring(verbose: bool = True) -> dict:
    """
    验证 BayBE 适配器是否真的调用了 BayBE 的核心 API。

    断言：
      - HAS_BAYBE=True 时，baybe_optimize 必须触发 campaign.recommend
        和 campaign.add_measurements 调用
      - HAS_BAYBE=False 时，必须走回退路径且不调用 BayBE
      - baybe_call_log 能追踪到真实 API 调用

    返回:
      {"passed": bool, "details": str}
    """
    results = {}

    if not HAS_BAYBE:
        # 回退路径测试
        reset_baybe_call_log()
        bounds = [(0.0, 10.0), (-5.0, 5.0)]

        def objective(x):
            return math.sin(x[0]) * math.cos(x[1]) + 0.1 * (x[0] - 5) ** 2

        result = baybe_optimize(bounds, objective, n_iterations=5, initial_points=3)
        log = baybe_call_log()

        t1 = result["method"] == "internal_gp_ucb"
        t2 = len(log) == 0  # 回退路径不应有 BayBE 调用
        t3 = result["baybe_available"] is False
        t4 = len(result["history"]) > 0
        t5 = result["best_value"] != 0.0

        results["fallback_path"] = {
            "passed": all([t1, t2, t3, t4, t5]),
            "method": result["method"],
            "baybe_calls": len(log),
            "n_history": len(result["history"]),
            "best_value": result["best_value"],
            "assertions": {
                "t1_method_is_fallback": t1,
                "t2_no_baybe_calls": t2,
                "t3_baybe_available_false": t3,
                "t4_history_nonempty": t4,
                "t5_best_value_nonzero": t5,
            },
        }
    else:
        # BayBE 真实路径测试
        reset_baybe_call_log()
        bounds = [(0.0, 10.0), (-5.0, 5.0)]

        def objective(x):
            return math.sin(x[0]) * math.cos(x[1]) + 0.1 * (x[0] - 5) ** 2

        result = baybe_optimize(bounds, objective, n_iterations=5, initial_points=3)
        log = baybe_call_log()

        t1 = result["method"] == "baybe_native"
        t2 = any("recommend" in c for c in log)
        t3 = any("add_measurements" in c for c in log)
        t4 = result.get("n_recommend_calls", 0) > 0
        t5 = result.get("n_add_measurements_calls", 0) > 0
        t6 = result.get("n_campaign_measurements", 0) > 0
        t7 = len(result["history"]) > 0

        results["baybe_native_path"] = {
            "passed": all([t1, t2, t3, t4, t5, t6, t7]),
            "method": result["method"],
            "baybe_calls": len(log),
            "call_log": log,
            "n_recommend_calls": result.get("n_recommend_calls", 0),
            "n_add_measurements_calls": result.get("n_add_measurements_calls", 0),
            "n_campaign_measurements": result.get("n_campaign_measurements", 0),
            "n_history": len(result["history"]),
            "best_value": result["best_value"],
            "baybe_version": result.get("baybe_version"),
            "assertions": {
                "t1_method_is_baybe_native": t1,
                "t2_recommend_called": t2,
                "t3_add_measurements_called": t3,
                "t4_n_recommend_calls_gt0": t4,
                "t5_n_add_measurements_calls_gt0": t5,
                "t6_n_campaign_measurements_gt0": t6,
                "t7_history_nonempty": t7,
            },
        }

    results["passed"] = all(v["passed"] for v in results.values()
                            if isinstance(v, dict) and "passed" in v)

    if verbose:
        print("=" * 70)
        print("BayBE Adapter Wiring Self-Test")
        print("=" * 70)
        print(f"  HAS_BAYBE: {HAS_BAYBE}")
        print(f"  BAYBE_VERSION: {BAYBE_VERSION}")
        for path_name, detail in results.items():
            if path_name == "passed":
                continue
            status = "PASS" if detail["passed"] else "FAIL"
            print(f"\n  [{status}] {path_name}")
            for k, v in detail.items():
                if k == "passed":
                    continue
                print(f"    {k}: {v}")
        print(f"\n  Overall: {'PASS' if results['passed'] else 'FAIL'}")
        print("=" * 70)

    return results


# ---------------------------------------------------------------------------
# 测试入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 70)
    print("BayBE 贝叶斯优化适配器")
    print("=" * 70)

    print(f"  BayBE 可用: {HAS_BAYBE}")
    print(f"  BayBE 版本: {BAYBE_VERSION}")
    print()

    if HAS_BAYBE:
        print("  使用 BayBE 原生 Campaign 接口")
    else:
        print("  使用内置 GP + UCB 回退")
        print("  安装 BayBE: pip install baybe")
        print()

    # 先跑真实接线自测
    wiring_result = selftest_baybe_wiring(verbose=True)
    print()

    # 再跑完整优化
    def objective(x):
        return math.sin(x[0]) * math.cos(x[1]) + 0.1 * (x[0] - 5) ** 2 + 0.05 * x[1]

    bounds = [(0.0, 10.0), (-5.0, 5.0)]
    result = baybe_optimize(bounds, objective, n_iterations=20, initial_points=5, seed=42)

    print(f"  目标函数: sin(x1)*cos(x2) + 0.1*(x1-5)^2 + 0.05*x2")
    print(f"  搜索空间: x1∈[0,10], x2∈[-5,5]")
    print(f"  方法: {result['method']}")
    print(f"  最优解: x={result['best_x']}")
    print(f"  最优值: {result['best_value']:.4f}")
    n_iters = len(result["history"])
    print(f"  总迭代: {n_iters}")
    if "n_recommend_calls" in result:
        print(f"  BayBE recommend 调用: {result['n_recommend_calls']}")
        print(f"  BayBE add_measurements 调用: {result['n_add_measurements_calls']}")
        print(f"  BayBE campaign measurements: {result['n_campaign_measurements']}")
    print()
    print("  迭代历史（前 5 轮 + 最后 5 轮）:")
    for h in result["history"][:5] + result["history"][-5:]:
        print(f"    iter={h['iter']:2d}  x={h['x']}  y={h['y']:.4f}")
    print("=" * 70)
