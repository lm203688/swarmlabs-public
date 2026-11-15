"""自适应保形预测：锐度优化模块。

响应评估报告 P0-3：CI 半宽/值 22× → < 5×。

核心问题
--------
SwarmLabs 当前的 conformal 区间过宽：
- 名义 coverage 0.95，实际区间半宽/值 = 22×（过度保守）
- 原因：固定 z=1.96 高斯区间 + cross-conformal 分位数过于保守
- 问题：区间太宽，用户无法使用（"我不知道答案在 [1, 10000] 之间"没用）

本模块提供三种锐度优化策略：
1. Adaptive Conformal（AC）：基于局部特征动态调整分位数
2. Quantile Regression（QR）：直接学习分位数函数
3. Split Conformal with Local Weights：按局部密度加权

诚实边界
--------
- 锐度优化会降低 coverage（coverage 和 sharpness 是 trade-off）
- 不宣称能同时达到 0.95 coverage 和 < 5× sharpness
- 所有方法都需要足够的数据点（< 10 点时不保证有限样本有效性）
"""

from __future__ import annotations

import numpy as np
from typing import Optional, Callable


# ---------------------------------------------------------------------------
# 1. Adaptive Conformal (AC)
# ---------------------------------------------------------------------------

def adaptive_conformal_interval(
    X_train: np.ndarray,
    y_train: np.ndarray,
    scores: np.ndarray,
    X_test: np.ndarray,
    alpha: float = 0.05,
    distance_weight: float = 1.0,
) -> tuple[np.ndarray, np.ndarray]:
    """自适应保形区间：基于局部密度动态调整分位数。

    原理：
    - 标准 conformal 用全局分位数，对所有测试点一视同仁
    - AC 用局部密度加权：密集区域用更小分位数（更锐），稀疏区域用更大分位数（更保守）
    - 这样在"模型知道"的区域给出更窄的区间，在"模型不知道"的区域保持保守

    Args:
        X_train: 训练输入 (n_train, d)
        y_train: 训练输出 (n_train,)
        scores: 非一致性分数 (n_train,)，来自 OOF 预测
        X_test: 测试输入 (n_test, d)
        alpha: 显著性水平（1 - coverage）
        distance_weight: 距离衰减权重（越大越局部）

    Returns:
        (lower, upper) 区间边界
    """
    X_train = np.atleast_2d(np.asarray(X_train, float))
    X_test = np.atleast_2d(np.asarray(X_test, float))
    scores = np.asarray(scores, float)

    # 1. 对每个测试点，计算到训练集的距离
    # 使用欧氏距离（简化，可替换为 Mahalanobis）
    distances = np.linalg.norm(X_test[:, None, :] - X_train[None, :, :], axis=2)

    # 2. 距离加权：近的点权重高
    weights = np.exp(-distance_weight * distances)
    weights = weights / (weights.sum(axis=1, keepdims=True) + 1e-12)

    # 3. 对每个测试点，计算加权分位数
    n_train = len(scores)
    sorted_indices = np.argsort(scores)
    sorted_scores = scores[sorted_indices]

    lower = np.empty(len(X_test))
    upper = np.empty(len(X_test))

    for i, x_test in enumerate(X_test):
        # 计算加权经验 CDF
        w = weights[i, sorted_indices]
        cum_w = np.cumsum(w)
        cum_w = cum_w / cum_w[-1]

        # 找到 alpha/2 和 1-alpha/2 分位数的权重
        k_lower = np.searchsorted(cum_w, alpha / 2)
        k_upper = np.searchsorted(cum_w, 1 - alpha / 2)

        k_lower = max(1, min(k_lower, n_train))
        k_upper = max(1, min(k_upper, n_train))

        lower[i] = sorted_scores[k_lower - 1]
        upper[i] = sorted_scores[k_upper - 1]

    return lower, upper


# ---------------------------------------------------------------------------
# 2. Quantile Regression (QR)
# ---------------------------------------------------------------------------

def quantile_regression_interval(
    X_train: np.ndarray,
    y_train: np.ndarray,
    X_test: np.ndarray,
    alpha: float = 0.05,
    ridge: float = 1e-6,
) -> tuple[np.ndarray, np.ndarray]:
    """分位数回归区间：直接学习分位数函数。

    原理：
    - 不用 GP 的均值 + 方差，而是直接拟合 q_alpha/2 和 q_1-alpha/2
    - 使用线性分位数回归（简化版，可扩展到核方法）
    - 优点：直接优化区间宽度，不依赖 GP 的方差估计

    Args:
        X_train: 训练输入
        y_train: 训练输出
        X_test: 测试输入
        alpha: 显著性水平
        ridge: 岭回归正则化参数

    Returns:
        (lower, upper) 分位数回归预测
    """
    X_train = np.atleast_2d(np.asarray(X_train, float))
    X_test = np.atleast_2d(np.asarray(X_test, float))
    y_train = np.asarray(y_train, float)

    n, d = X_train.shape

    # 添加偏置
    X_train_aug = np.hstack([X_train, np.ones((n, 1))])
    X_test_aug = np.hstack([X_test, np.ones((X_test.shape[0], 1))])

    # 下分位数
    tau_lower = alpha / 2
    # 上分位数
    tau_upper = 1 - alpha / 2

    def fit_pinball(X, y, tau, ridge_val=ridge, max_iter=50):
        """迭代加权最小二乘（IWR）拟合 pinball 损失。

        pinball 损失: rho_tau(r) = max(tau * r, (tau - 1) * r)
        IWR 每轮更新权重 w_i = tau if r_i >= 0 else (1 - tau)，
        对下分位数 (tau < 0.5) 和上分位数 (tau > 0.5) 统一适用。
        """
        n = X.shape[0]
        beta = np.zeros(X.shape[1])
        for _ in range(max_iter):
            residuals = y - X @ beta
            # pinball 损失的子梯度权重
            if tau < 0.5:
                w = np.where(residuals >= 0, 1 - tau, tau)
            else:
                w = np.where(residuals >= 0, tau, 1 - tau)
            w = np.maximum(w, 1e-8)
            W = np.diag(w)
            XtW = X.T @ W
            A = XtW @ X + ridge_val * np.eye(X.shape[1])
            b = XtW @ y
            new_beta = np.linalg.solve(A, b)
            if np.linalg.norm(new_beta - beta) < 1e-10:
                beta = new_beta
                break
            beta = new_beta
        return beta

    beta_lower = fit_pinball(X_train_aug, y_train, tau_lower)
    beta_upper = fit_pinball(X_train_aug, y_train, tau_upper)

    lower = X_test_aug @ beta_lower
    upper = X_test_aug @ beta_upper

    # 确保 lower <= upper
    mask = lower > upper
    if np.any(mask):
        swap = (lower[mask] + upper[mask]) / 2
        lower[mask] = swap
        upper[mask] = swap

    return lower, upper


# ---------------------------------------------------------------------------
# 3. Split Conformal with Local Weights
# ---------------------------------------------------------------------------

def local_weighted_conformal(
    X_calib: np.ndarray,
    y_calib: np.ndarray,
    scores_calib: np.ndarray,
    X_test: np.ndarray,
    alpha: float = 0.05,
    bandwidth: float = 1.0,
) -> tuple[np.ndarray, np.ndarray]:
    """局部加权 split conformal：按局部密度调整分位数。

    原理：
    - 标准 split conformal 用全局分位数
    - 局部加权版：对每个测试点，用标定集中"附近"的分数加权
    - 附近分数大 → 区间宽（保守）；附近分数小 → 区间窄（锐）

    Args:
        X_calib: 标定输入 (n_calib, d)
        y_calib: 标定输出 (n_calib,)
        scores_calib: 标定非一致性分数 (n_calib,)
        X_test: 测试输入 (n_test, d)
        alpha: 显著性水平
        bandwidth: 核带宽（越小越局部）

    Returns:
        (lower, upper) 区间边界
    """
    X_calib = np.atleast_2d(np.asarray(X_calib, float))
    X_test = np.atleast_2d(np.asarray(X_test, float))
    scores_calib = np.asarray(scores_calib, float)

    n_calib = len(scores_calib)

    # 距离矩阵
    distances = np.linalg.norm(X_test[:, None, :] - X_calib[None, :, :], axis=2)

    # 核权重
    weights = np.exp(-0.5 * (distances / bandwidth) ** 2)
    weights = weights / (weights.sum(axis=1, keepdims=True) + 1e-12)

    # 加权分位数
    sorted_indices = np.argsort(scores_calib)
    sorted_scores = scores_calib[sorted_indices]
    sorted_weights = weights[:, sorted_indices]

    k = int(np.ceil((n_calib + 1) * (1 - alpha)))
    k = max(1, min(k, n_calib))

    lower = np.empty(len(X_test))
    upper = np.empty(len(X_test))

    for i in range(len(X_test)):
        w = sorted_weights[i]
        # 加权选择第 k 小分数
        cum_w = np.cumsum(w)
        target_w = cum_w[-1] * (1 - alpha)
        idx = np.searchsorted(cum_w, target_w)
        idx = max(0, min(idx, n_calib - 1))
        upper[i] = sorted_scores[idx]
        lower[i] = -upper[i]  # 简化：对称区间

    return lower, upper


# ---------------------------------------------------------------------------
# 4. 锐度评估
# ---------------------------------------------------------------------------

def evaluate_sharpness(
    y_true: np.ndarray,
    lower: np.ndarray,
    upper: np.ndarray,
    nominal_coverage: float = 0.95,
) -> dict:
    """评估区间的锐度指标。

    Args:
        y_true: 真实值
        lower: 区间下界
        upper: 区间上界
        nominal_coverage: 名义覆盖率

    Returns:
        锐度指标字典
    """
    y_true = np.asarray(y_true, float)
    lower = np.asarray(lower, float)
    upper = np.asarray(upper, float)

    n = len(y_true)

    # 1. Coverage
    covered = (y_true >= lower) & (y_true <= upper)
    coverage = np.mean(covered)

    # 2. Sharpness（区间宽度 / 真实值范围）
    widths = upper - lower
    y_range = np.ptp(y_true) if np.ptp(y_true) > 1e-12 else 1.0
    half_widths = widths / 2
    sharpness = np.mean(half_widths / (np.abs(y_true) + 1e-12))

    # 3. Interval Score（越小越好）
    alpha = 1 - nominal_coverage
    interval_score = np.mean(widths + (2 / alpha) * (np.maximum(lower - y_true, 0) + np.maximum(y_true - upper, 0)))

    # 4. 过宽率（区间太宽的场景比例）
    # 定义：区间宽度 > 10 * |y_true| 为过宽
    too_wide = widths > 10 * (np.abs(y_true) + 1e-12)
    too_wide_rate = np.mean(too_wide)

    return {
        "coverage": round(coverage, 4),
        "nominal_coverage": nominal_coverage,
        "coverage_gap": round(coverage - nominal_coverage, 4),
        "sharpness": round(sharpness, 4),
        "sharpness_target": "< 5.0",
        "sharpness_met": sharpness < 5.0,
        "mean_width": round(float(np.mean(widths)), 4),
        "mean_half_width": round(float(np.mean(half_widths)), 4),
        "interval_score": round(float(interval_score), 4),
        "too_wide_rate": round(float(too_wide_rate), 4),
        "n": n,
    }


# ---------------------------------------------------------------------------
# 5. 便捷接口
# ---------------------------------------------------------------------------

def optimize_sharpness(
    X_train: np.ndarray,
    y_train: np.ndarray,
    X_test: np.ndarray,
    y_test: np.ndarray = None,
    method: str = "ac",
    alpha: float = 0.05,
    **kwargs,
) -> dict:
    """优化区间锐度（统一接口）。

    Args:
        X_train: 训练输入
        y_train: 训练输出
        X_test: 测试输入
        y_test: 测试真实值（用于评估，可选）
        method: 方法选择 ("ac" / "qr" / "lwc")
        alpha: 显著性水平
        **kwargs: 方法特定参数

    Returns:
        包含 lower, upper, metrics 的字典
    """
    if method == "ac":
        # 需要 OOF scores，这里用简化版（直接用训练数据）
        scores = np.abs(y_train)  # 简化：直接用训练输出作为分数
        lower, upper = adaptive_conformal_interval(
            X_train, y_train, scores, X_test, alpha=alpha, **kwargs
        )
    elif method == "qr":
        lower, upper = quantile_regression_interval(
            X_train, y_train, X_test, alpha=alpha, **kwargs
        )
    elif method == "lwc":
        # 需要标定数据，这里用训练数据
        lower, upper = local_weighted_conformal(
            X_train, y_train, y_train, X_test, alpha=alpha, **kwargs
        )
    else:
        raise ValueError(f"未知方法: {method}")

    result = {"lower": lower, "upper": upper}

    if y_test is not None:
        result["metrics"] = evaluate_sharpness(y_test, lower, upper, nominal_coverage=1 - alpha)

    return result


def __main__():
    """Self-test: 验证模块基本功能。"""
    print("Adaptive Conformal Self-Test")
    print("=" * 60)

    # 生成测试数据
    rng = np.random.default_rng(42)
    n_train = 50
    n_test = 20
    d = 2

    X_train = rng.uniform(-2, 2, (n_train, d))
    # 非线性函数
    y_train = np.sin(X_train[:, 0]) + X_train[:, 1] ** 2 + rng.normal(0, 0.1, n_train)

    X_test = rng.uniform(-2, 2, (n_test, d))
    y_test = np.sin(X_test[:, 0]) + X_test[:, 1] ** 2

    print(f"\n数据: n_train={n_train}, n_test={n_test}, d={d}")

    # 测试 1: AC 方法
    print("\n1. Adaptive Conformal (AC)...")
    result_ac = optimize_sharpness(X_train, y_train, X_test, y_test, method="ac")
    metrics_ac = result_ac["metrics"]
    print(f"   Coverage: {metrics_ac['coverage']:.2%}")
    print(f"   Sharpness: {metrics_ac['sharpness']:.2f}")
    print(f"   Sharpness met: {metrics_ac['sharpness_met']}")
    print(f"   Too wide rate: {metrics_ac['too_wide_rate']:.2%}")

    # 测试 2: QR 方法
    print("\n2. Quantile Regression (QR)...")
    result_qr = optimize_sharpness(X_train, y_train, X_test, y_test, method="qr")
    metrics_qr = result_qr["metrics"]
    print(f"   Coverage: {metrics_qr['coverage']:.2%}")
    print(f"   Sharpness: {metrics_qr['sharpness']:.2f}")
    print(f"   Sharpness met: {metrics_qr['sharpness_met']}")

    # 测试 3: LWC 方法
    print("\n3. Local Weighted Conformal (LWC)...")
    result_lwc = optimize_sharpness(X_train, y_train, X_test, y_test, method="lwc")
    metrics_lwc = result_lwc["metrics"]
    print(f"   Coverage: {metrics_lwc['coverage']:.2%}")
    print(f"   Sharpness: {metrics_lwc['sharpness']:.2f}")
    print(f"   Sharpness met: {metrics_lwc['sharpness_met']}")

    # 汇总
    print("\n" + "=" * 60)
    print("汇总对比")
    print("=" * 60)
    print(f"{'方法':<5} {'Coverage':<12} {'Sharpness':<12} {'达标':<8}")
    print("-" * 50)
    for name, metrics in [("AC", metrics_ac), ("QR", metrics_qr), ("LWC", metrics_lwc)]:
        print(f"{name:<5} {metrics['coverage']:<12.2%} {metrics['sharpness']:<12.2f} {str(metrics['sharpness_met']):<8}")

    print("\n" + "=" * 60)
    print("测试完成 ✓")


if __name__ == "__main__":
    __main__()
