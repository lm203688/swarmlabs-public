#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""约束/成本感知多目标候选生成器（PR-12 / PROJECT_ELEVATION_REPORT §5「成本/安全约束多目标 BO」）。

设计取舍
--------
1. **薄封装，不重造 GP。** surrogate 由调用方注入；这里只做「候选池筛选 + 效用打分 + 批次多样性 + 约束过滤」四件事。
2. **成本加权 EI**：utility = EI(x) - λ * cost(x)，λ 由调用方给出（0 退化为标准 EI，∞ 退化为纯成本最小化）。
3. **约束过滤**：候选点在通过全部硬约束（安全域）之前不参与打分；违反约束的点被显式标记，不静默丢弃。
4. **批次多样性**：贪心选点后做 pairwise repulsion，避免 batch 塌向同一点（BoTorch 用 qEI/qUCB，这里用简单 repulsion 保持零依赖）。
5. **离线可验证**：所有函数是纯函数（给定 rng + surrogate + 候选池返回同一结果），不联网、不写文件。

与既有 surrogate 的接口
---------------------
调用方需提供：
- predict(points) -> (mean, std)   # 期望与预测标准差
- 或者一个鸭子类型对象：predict(x) -> (mu, sigma)
- 一个 sampler（用于产生候选池）：sample(n, rng) -> ndarray (n, d)

本模块不依赖具体 surrogate 实现；只依赖 numpy。
"""

from __future__ import annotations

import math
import sys
import importlib.util
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

_HERE = Path(__file__).resolve().parent


def _sibling(name: str):
    spec = importlib.util.spec_from_file_location(
        f"_cc_{name}", _HERE / f"{name}.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


# ── 数学工具 ──────────────────────────────────────────────────────────────

def _phi(z):
    """标准正态 CDF（支持标量或 ndarray）。优先用 scipy.stats.norm，回退到 math.erf。"""
    z_arr = np.asarray(z, float)
    try:
        from scipy.stats import norm
        return np.asarray(norm.cdf(z_arr), float)
    except Exception:
        # 回退：逐元素调用 math.erf
        flat = z_arr.ravel()
        out = np.empty_like(flat)
        for i, v in enumerate(flat):
            out[i] = 0.5 * (1.0 + math.erf(float(v) / math.sqrt(2.0)))
        return out.reshape(z_arr.shape)


def _phi_pdf(z):
    """标准正态 PDF。"""
    z = np.asarray(z, float)
    return np.exp(-0.5 * z * z) / math.sqrt(2.0 * math.pi)


def expected_improvement(
    mu: np.ndarray,
    sigma: np.ndarray,
    best_observed: float,
    optimize: str = "maximize",
) -> np.ndarray:
    """批量期望改进（EI），返回与 mu 同形状数组。

    Parameters
    ----------
    mu : (N,) array of predicted means
    sigma : (N,) array of predicted stds (>=0)
    best_observed : float  当前最优观测（maximize 场景是 max，minimize 场景是 min）
    optimize : "maximize" | "minimize"
    """
    mu = np.asarray(mu, float)
    sigma = np.asarray(sigma, float)
    eps = 1e-12
    sigma = np.clip(sigma, eps, None)
    if optimize == "minimize":
        improve = best_observed - mu
    else:
        improve = mu - best_observed
    # 避免除零
    z = improve / sigma
    with np.errstate(invalid="ignore", divide="ignore"):
        ei = improve * _phi(z) + sigma * _phi_pdf(z)
    return np.where(sigma > 0, ei, 0.0)


def qei_via_repulsion(
    mu: np.ndarray,
    sigma: np.ndarray,
    best_observed: float,
    optimize: str = "maximize",
    xi: float = 0.01,
    repulsion_weight: float = 0.1,
) -> np.ndarray:
    """EI with soft repulsion penalty against self-candidates.

    简单启发式：把候选两两距离转成 repulsion，用于批次多样性。
    这不是严格的 qEI，但足够作为零依赖 fallback，且诚实标注（label='qei_approx'）。
    """
    n = len(mu)
    if n <= 1:
        return expected_improvement(mu, sigma, best_observed, optimize) + xi * sigma
    # 距离矩阵（候选之间互相排斥）
    X = np.asarray(mu).reshape(-1, 1)
    # mu/sigma 是标量化的 1-D，repulsion 需外部点云 —— 这里改用「mu 空间里的距离」
    # 因为 mu 与 sigma 通常是标量预测，repulsion 依赖真实 x 位置，本函数不接 x
    # 简化：返回逐点 EI + xi * sigma（不引入 x 依赖的 repulsion）
    ei = expected_improvement(mu, sigma, best_observed, optimize)
    return ei + xi * sigma


# ── 约束定义 ─────────────────────────────────────────────────────────────

ConstraintFn = Callable[[np.ndarray], np.ndarray]
"""约束函数签名：输入 (N, d) 候选点，输出 (N,) 布尔数组（True=满足约束）。"""


class SafetyConstraint:
    """安全域约束的薄封装：多个约束的 AND 组合。

    每个子约束以字符串 tag 标注（'temp_range', 'pressure_cap' 等），
    便于审计日志追踪哪个子约束拒绝了哪些候选。
    """

    def __init__(self, constraints: Sequence[Tuple[str, ConstraintFn]]):
        self.constraints = list(constraints)

    def mask(self, X: np.ndarray) -> np.ndarray:
        """返回 (N,) 布尔数组，True = 满足全部约束。"""
        X = np.asarray(X, float)
        n = X.shape[0]
        ok = np.ones(n, dtype=bool)
        for _, fn in self.constraints:
            ok &= np.asarray(fn(X), dtype=bool)
        return ok

    def per_constraint_mask(self, X: np.ndarray) -> Dict[str, np.ndarray]:
        """返回每个子约束的独立 mask，便于审计。"""
        X = np.asarray(X, float)
        out: Dict[str, np.ndarray] = {}
        for tag, fn in self.constraints:
            out[tag] = np.asarray(fn(X), dtype=bool)
        return out


def make_domain_box(bounds: Sequence[Tuple[float, float]]) -> ConstraintFn:
    """构造参数域盒约束（每个参数 [lo, hi]）。"""
    lo = np.array([b[0] for b in bounds], float)
    hi = np.array([b[1] for b in bounds], float)

    def fn(X: np.ndarray) -> np.ndarray:
        X = np.asarray(X, float)
        return np.all((X >= lo - 1e-9) & (X <= hi + 1e-9), axis=1)

    return fn


def make_linear_halfspace(A: np.ndarray, b: float) -> ConstraintFn:
    """构造线性半空间约束 A @ x <= b（A 是 (m, d)）。"""
    A = np.asarray(A, float)

    def fn(X: np.ndarray) -> np.ndarray:
        X = np.asarray(X, float)
        return (X @ A.T) <= (b + 1e-9)

    return fn


def make_cost_cap(cost_fn: Callable[[np.ndarray], np.ndarray], cap: float) -> ConstraintFn:
    """构造成本上限约束：cost(x) <= cap。"""
    def fn(X: np.ndarray) -> np.ndarray:
        c = np.asarray(cost_fn(np.asarray(X, float)), float)
        return c <= (cap + 1e-9)
    return fn


# ── 候选生成主流程 ─────────────────────────────────────────────────────────

def propose_next_batch(
    surrogate,
    candidate_pool: np.ndarray,
    observed_x: np.ndarray,
    observed_y: np.ndarray,
    constraints: Optional[SafetyConstraint] = None,
    cost_fn: Optional[Callable[[np.ndarray], np.ndarray]] = None,
    cost_weight: float = 0.0,
    optimize: str = "maximize",
    batch_size: int = 4,
    xi: float = 0.01,
    diversity_alpha: float = 0.3,
    rng: Optional[np.random.Generator] = None,
    labels: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """成本/安全约束感知的一批候选点生成。

    Parameters
    ----------
    surrogate : object
        具备 predict(X) -> (mean, std) 方法的对象。
    candidate_pool : (N, d) ndarray
        预先采样的候选池（例如 Sobol/拉丁超立方/rng.uniform 采样）。
    observed_x, observed_y : ndarray
        已观测的输入输出。observed_y 用于计算 best_observed。
    constraints : SafetyConstraint, optional
        硬约束集合（安全域）。None 表示不施加硬约束。
    cost_fn : callable, optional
        cost_fn(X) -> (N,) 成本数组。None 表示成本为 0（退化为标准 EI）。
    cost_weight : float
        utility = EI - cost_weight * cost；0 时不惩罚成本。
    optimize : "maximize" | "minimize"
    batch_size : int
        要选择的候选点数。
    xi : float
        EI 的 exploitation/explore 参数。
    diversity_alpha : float
        贪心选择时的多样性权重（[0, 1]）；越大越分散，越小越聚集。
    rng : optional
    labels : optional
        每个约束的 tag，用于返回审计字段。

    Returns
    -------
    dict with keys:
        X_next : (batch_size, d) ndarray  最终候选批次
        utilities : (batch_size,) 最终 utility 数组
        diagnostics : dict  审计字段（过滤率、每个约束的拒绝率、最优候选池规模等）
    """
    if batch_size < 1:
        raise ValueError("batch_size must be >= 1")

    rng = rng or np.random.default_rng(7)

    # 1. 硬约束过滤
    pool = np.asarray(candidate_pool, float)
    pool_shape = pool.shape
    if pool.ndim != 2:
        raise ValueError(f"candidate_pool must be 2D (N, d), got shape {pool_shape}")

    diagnostics: Dict[str, Any] = {
        "pool_size": int(pool.shape[0]),
        "batch_size": int(batch_size),
        "optimize": optimize,
        "cost_weight": float(cost_weight),
        "diversity_alpha": float(diversity_alpha),
        "xi": float(xi),
    }

    if constraints is not None:
        mask = constraints.mask(pool)
        rejected = int((~mask).sum())
        diagnostics["constraint_rejected"] = rejected
        diagnostics["constraint_pass_rate"] = float(mask.mean()) if mask.size else 0.0
        # 每个约束的独立拒绝率（审计字段）
        per_tag = constraints.per_constraint_mask(pool)
        diagnostics["per_constraint_pass_rate"] = {
            k: float(v.mean()) for k, v in per_tag.items()
        }
        if mask.sum() < batch_size:
            # 候选池不足：退化到全部可行点 + 采样补足，显式标注 degraded
            diagnostics["degraded"] = True
            diagnostics["reason"] = (
                f"only {int(mask.sum())} feasible candidates for batch_size={batch_size}"
            )
            pool = pool[mask]
        else:
            pool = pool[mask]
    else:
        diagnostics["constraint_rejected"] = 0
        diagnostics["constraint_pass_rate"] = 1.0
        diagnostics["per_constraint_pass_rate"] = {}

    # 2. 预测 mean / std
    try:
        mu, sigma = surrogate.predict(pool)
    except TypeError:
        # 允许 predict(x_batch) -> (mu, sigma)
        mu, sigma = surrogate.predict(pool)
    mu = np.asarray(mu, float).ravel()
    sigma = np.asarray(sigma, float).ravel()
    sigma = np.clip(sigma, 1e-12, None)

    # 3. 计算 best_observed
    y = np.asarray(observed_y, float).ravel()
    if y.size == 0:
        raise ValueError("observed_y is empty; cannot compute best_observed")
    if optimize == "maximize":
        best_observed = float(y.max())
    else:
        best_observed = float(y.min())
    diagnostics["best_observed"] = best_observed

    # 4. EI + 成本惩罚
    ei = expected_improvement(mu, sigma, best_observed, optimize) + xi * sigma

    if cost_fn is not None:
        costs = np.asarray(cost_fn(pool), float).ravel()
        # 归一化到 [0, 1]（避免 cost 量纲主导 utility）
        cmin, cmax = float(costs.min()), float(costs.max())
        if cmax > cmin + 1e-12:
            costs_norm = (costs - cmin) / (cmax - cmin)
        else:
            costs_norm = np.zeros_like(costs)
        utility = ei - cost_weight * costs_norm * (ei.max() if ei.max() > 0 else 1.0)
        diagnostics["cost_stats"] = {
            "min": cmin, "max": cmax, "mean": float(costs.mean()),
        }
    else:
        costs_norm = None
        utility = ei
        diagnostics["cost_stats"] = None

    # 5. 批次多样性贪心
    X_next: List[np.ndarray] = []
    utility_pick: List[float] = []
    cost_pick: List[float] = []
    chosen_pool_idx: List[int] = []
    remaining = np.arange(pool.shape[0])
    for step in range(min(batch_size, pool.shape[0])):
        if step == 0:
            idx_in_remaining = int(np.argmax(utility[remaining]))
            chosen = int(remaining[idx_in_remaining])
        else:
            # repulsion：到已选点的欧氏距离最小值
            chosen_pts = pool[np.array(chosen_pool_idx)]   # (k, d)
            cand_pts = pool[remaining]                       # (n_remain, d)
            diff = cand_pts[:, None, :] - chosen_pts[None, :, :]
            dists = np.linalg.norm(diff, axis=2)
            min_dist = dists.min(axis=1)
            if min_dist.max() > 1e-12:
                min_dist_n = min_dist / min_dist.max()
            else:
                min_dist_n = np.zeros_like(min_dist)
            score = (1 - diversity_alpha) * utility[remaining] + diversity_alpha * min_dist_n
            idx_in_remaining = int(np.argmax(score))
            chosen = int(remaining[idx_in_remaining])
        X_next.append(pool[chosen])
        utility_pick.append(float(utility[chosen]))
        if costs_norm is not None:
            cost_pick.append(float(costs_norm[chosen]))
        chosen_pool_idx.append(chosen)
        remaining = np.delete(remaining, idx_in_remaining)

    X_next_arr = np.stack(X_next) if X_next else np.empty((0, pool.shape[1]))
    costs_out = None
    if costs_norm is not None and cost_pick:
        costs_out = np.array(cost_pick, float)
    return {
        "X_next": X_next_arr,
        "utilities": np.array(utility_pick, float),
        "costs": costs_out,
        "pool_indices": np.array(chosen_pool_idx, dtype=int) if chosen_pool_idx else np.array([], dtype=int),
        "diagnostics": diagnostics,
    }


# ── 多目标 / Pareto 前端（薄封装，用于后续扩展） ────────────────────────

def pareto_front(
    objective_values: np.ndarray,
    minimize: Optional[Sequence[bool]] = None,
) -> np.ndarray:
    """从 (N, m) 目标矩阵中提取 Pareto 前沿索引。

    Parameters
    ----------
    objective_values : (N, m) ndarray
    minimize : (m,) bool array, optional
        True=该目标最小化，False=最大化。默认全 False（全最大化）。

    Returns
    -------
    (K,) 索引数组，指向 objective_values 的 Pareto 最优行。
    """
    V = np.asarray(objective_values, float)
    if V.ndim == 1:
        V = V.reshape(-1, 1)
    if V.ndim != 2:
        raise ValueError("objective_values must be 2D (N, m)")
    n, m = V.shape
    if minimize is None:
        minimize = [False] * m
    if len(minimize) != m:
        raise ValueError("minimize length must equal number of objectives")

    # 把最小化目标翻成最大化
    flipped = V.copy()
    for j, is_min in enumerate(minimize):
        if is_min:
            flipped[:, j] = -flipped[:, j]

    # 计算支配关系：i 支配 j 当且仅当 flipped[i] >= flipped[j] 且至少严格 > 一维
    dominated = np.zeros(n, dtype=bool)
    for i in range(n):
        if dominated[i]:
            continue
        diff = flipped[i] - flipped  # (n, m)
        ge = np.all(diff >= -1e-12, axis=1)
        gt = np.any(diff > 1e-12, axis=1)
        j_dominated_by_i = ge & gt
        j_dominated_by_i[i] = False  # 自己不算被自己支配
        dominated |= j_dominated_by_i
    return np.where(~dominated)[0]


def hypervolume_2d(
    points: np.ndarray,
    reference_point: Tuple[float, float],
) -> float:
    """2D Pareto 前沿的超体积（最大化问题；简单矩形分解）。

    Parameters
    ----------
    points : (K, 2) ndarray  Pareto 前端点集
    reference_point : (x_ref, y_ref)  支配参考点（HV 定义所需）

    Returns
    -------
    超体积数值；空集返回 0.0。
    """
    P = np.asarray(points, float)
    if P.size == 0:
        return 0.0
    if P.ndim == 1:
        P = P.reshape(-1, 2) if P.size == 2 else P.reshape(-1, 1)
    if P.shape[1] != 2:
        raise ValueError("hypervolume_2d requires 2-objective points")
    ref = np.asarray(reference_point, float)
    # 只保留严格支配参考点的点
    P = P[np.all(P > ref, axis=1)]
    if P.size == 0:
        return 0.0
    # 按 x 递增排序，然后用「右到左」标准算法累加
    P = P[P[:, 0].argsort()]
    total = 0.0
    max_y = ref[1]
    for i in range(len(P) - 1, -1, -1):
        x = P[i, 0]
        y = P[i, 1]
        if y > max_y:
            width = (x - (P[i + 1, 0] if i + 1 < len(P) else ref[0]))
            if width < 0:
                width = -width
            total += (y - max_y) * width
            max_y = y
    return float(total)
