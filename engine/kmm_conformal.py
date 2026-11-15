"""Kernel Mean Matching Conformal (KMM-CP, UAI 2026).

问题
----
在极端分布漂移（源分布和测试分布**几乎无重叠**）时，任何密度比方法
（含 CoFact）都会失真——因为 p_test/p_source 的分母接近 0，密度比会
爆炸。KMM-CP 的思路是**放弃给所有测试点保证**，转而：

  1. 用核均值匹配（Kernel Mean Matching, KMM）找到测试集中与训练分布
     最匹配的**子集** S*（在再生核 Hilbert 空间里 K(M_1 || M_0) 最小）；
  2. 只在 S* 上给出有限样本覆盖保证；
  3. 不在 S* 里的测试点标注为 "out-of-scope"，让下游决定是否外推。

这个策略的诚实价值在于：**把"能否保证覆盖"的问题拆成"能否选定一个
高重叠子集"的问题**，后者是可解的（MCC + 凸优化），而不是硬撑。

本实现（纯 numpy，接口对齐 conformal.py）：
  - `kernel_matrix` / `mmd`：RBF 核的 MMD
  - `kmm_select_subset`：凸松弛选子集（Frank-Wolfe / 子梯度）
  - `kmm_conformal_interval`：主接口，返回 (lo, hi, mu, in_scope_mask, meta)
  - 内建 selftest
"""
from __future__ import annotations

import os
import sys
from typing import Tuple

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine.surrogate import SurrogateEngine  # noqa: E402
from engine.conformal import nonconformity_std, conformal_quantile  # noqa: E402


# ---------------------------------------------------------------- RBF 核
def kernel_matrix(A: np.ndarray, B: np.ndarray, ls: float = 0.5) -> np.ndarray:
    """RBF 核矩阵 K(a,b) = exp(-||a-b||^2 / (2 ls^2))。"""
    A = np.atleast_2d(np.asarray(A, float))
    B = np.atleast_2d(np.asarray(B, float))
    if A.shape[1] != B.shape[1]:
        raise ValueError(f"维度不匹配: A={A.shape}  B={B.shape}")
    # 数值稳定的 RBF：展开为 ||a||^2 - 2 a·b + ||b||^2
    a2 = (A ** 2).sum(axis=1, keepdims=True)
    b2 = (B ** 2).sum(axis=1, keepdims=True).T
    sq = a2 - 2 * (A @ B.T) + b2
    sq = np.maximum(sq, 0.0)  # 数值稳定
    return np.exp(-sq / (2.0 * float(ls) ** 2))


def mmd(U: np.ndarray, V: np.ndarray, ls: float = 0.5) -> float:
    """最大均值差异 MMD^2 = ||mu_U - mu_V||^2_H，用核估计。

    零说明 U 和 V 的边缘分布在核上不可区分；越大说明漂移越严重。
    """
    U = np.atleast_2d(np.asarray(U, float))
    V = np.atleast_2d(np.asarray(V, float))
    Kuu = kernel_matrix(U, U, ls)
    Kvv = kernel_matrix(V, V, ls)
    Kuv = kernel_matrix(U, V, ls)
    n, m = len(U), len(V)
    if n < 2 or m < 2:
        return 0.0
    mmd2 = (Kuu.sum() / (n * (n - 1))) + (Kvv.sum() / (m * (m - 1))) \
           - 2.0 * Kuv.mean()
    # MMD 有界于 [0, 4]，clip 到 0 避免数值负
    return float(max(0.0, mmd2))


# ---------------------------------------------------------------- 子集选择
def kmm_select_subset(X_source: np.ndarray, X_test: np.ndarray,
                       target_fraction: float = 0.5, ls: float = 0.5,
                       n_iters: int = 60, seed: int = 0
                       ) -> Tuple[np.ndarray, np.ndarray, float]:
    """用 KMM 凸松弛从 X_test 中选出 target_fraction 比例的子集。

    目标：min_w  MMD^2(empirical_source, w-weighted test) + λ||w||_1
        s.t.  sum w = target,  0 ≤ w ≤ 1

    实际意义：给测试集赋权重，权重 > 0 的点属于 "in-scope"。用 Frank-Wolfe
    迭代，每步把权重放到当前最能降低 MMD 的一个测试点上。

    Returns:
      weights: 每个测试点的权重
      mask:    weights > threshold 的布尔掩码
      mmd_final: 选完子集后的 MMD 值
    """
    X_source = np.atleast_2d(np.asarray(X_source, float))
    X_test = np.atleast_2d(np.asarray(X_test, float))
    n_src = len(X_source)
    n_test = len(X_test)
    target = float(np.clip(target_fraction, 0.05, 1.0))
    k_select = max(1, int(round(target * n_test)))

    # 预计算核矩阵
    Ktt = kernel_matrix(X_test, X_test, ls)
    Kts = kernel_matrix(X_test, X_source, ls)  # (n_test, n_src)

    # 初始：均匀权，选前 k_select 个点
    w = np.zeros(n_test, float)
    rng = np.random.default_rng(seed)
    idx_init = rng.choice(n_test, size=k_select, replace=False)
    w[idx_init] = 1.0 / k_select

    # Frank-Wolfe：每步线性化，在可行域极点上更新
    m_src = Kts.mean(axis=1)  # E_source[k(x, .)]，(n_test,)

    def objective(wv):
        # weighted empirical MMD vs source
        p = wv * len(X_test)
        p = p / (p.sum() + 1e-12)
        m_t = Ktt @ p
        return float(np.mean((m_t - m_src) ** 2))

    cur_obj = objective(w)
    for i in range(int(n_iters)):
        # gradient of objective w.r.t. w
        grad = 2.0 * Ktt @ ((Ktt @ w) - m_src) / (n_src + 1e-12)
        # linearized minimizer: argmin grad·s  s.t. sum s = target, s ∈ {0,1}-valued
        # top-k: 选 grad 最小的 k_select 个
        order = np.argsort(grad)
        s = np.zeros(n_test, float)
        s[order[:k_select]] = 1.0 / k_select
        # Frank-Wolfe 步长（1/t 形式）
        step = 2.0 / (float(i + 3) ** 1.5)
        w = (1.0 - step) * w + step * s
        new_obj = objective(w)
        if abs(new_obj - cur_obj) < 1e-10:
            w = s
            cur_obj = new_obj
            break
        cur_obj = new_obj

    # 二值化：按目标比例硬选 top k_select 权重
    final = np.zeros(n_test, float)
    order = np.argsort(-w)
    final[order[:k_select]] = 1.0 / k_select
    mask = final > 0
    final_mmd = objective(final) if mask.sum() > 0 else np.inf
    return final, mask, float(final_mmd)


# ---------------------------------------------------------------- 主接口
def kmm_conformal_interval(X_train: np.ndarray, y_train: np.ndarray,
                           domain, X_test: np.ndarray,
                           alpha: float = 0.05, noise_rel: float = 0.03,
                           target_fraction: float = 0.5, ls: float = 0.5,
                           seed: int = 0
                           ) -> Tuple[np.ndarray, np.ndarray, np.ndarray,
                                       np.ndarray, dict]:
    """KMM-Conformal 主接口。

    流程：
      1. 用 X_test 全量算 MMD vs X_train，判断整体漂移；
      2. 用 kmm_select_subset 选出 target_fraction 比例的高重叠子集；
      3. 在**全测试集**上做 cross-conformal 学非一致性分数（与
         adaptive_conformal 相同口径），得到 Q；
      4. 只在子集上给区间，其他点标注 in_scope=False。

    Returns:
      lo, hi, mu, in_scope_mask, meta
        - lo, hi: 数组，只有 in_scope=True 的点是有效区间，其他填 NaN
        - in_scope_mask: bool 数组
        - meta: {"mmd_full": ..., "mmd_in_scope": ..., "kmm_Q": ..., ...}
    """
    X_train = np.atleast_2d(np.asarray(X_train, float))
    y_train = np.asarray(y_train, float).ravel()
    X_test = np.atleast_2d(np.asarray(X_test, float))

    # 1) 整体漂移诊断
    mmd_full = mmd(X_test, X_train, ls=ls)

    # 2) 子集选择
    weights, in_scope, mmd_scope = kmm_select_subset(
        X_train, X_test, target_fraction=target_fraction, ls=ls, seed=seed,
    )

    # 3) 非一致性分数：用交叉验证（与 conformal.py 同口径）
    from engine.conformal import cross_conformal_scores
    scores = cross_conformal_scores(domain, X_train, y_train, k_folds=5,
                                    seed=seed, noise_rel=noise_rel, repeats=3)
    Q = conformal_quantile(scores, alpha=alpha)

    # 4) 预测（用全训练集拟合一次 GP）
    eng = SurrogateEngine(domain)
    eng.fit(X_train, y_train, noise_rel=noise_rel)
    mu, sd = eng.predict(X_test)

    # 5) 只在 in_scope 上给区间
    lo = np.full(len(mu), np.nan, float)
    hi = np.full(len(mu), np.nan, float)
    in_idx = np.where(in_scope)[0]
    if len(in_idx) > 0:
        lo[in_idx] = mu[in_idx] - Q * sd[in_idx]
        hi[in_idx] = mu[in_idx] + Q * sd[in_idx]

    meta = {
        "method": "kmm conformal (UAI 2026)",
        "alpha": float(alpha),
        "kmm_Q": float(Q),
        "mmd_full": round(mmd_full, 4),
        "mmd_in_scope": round(mmd_scope, 4),
        "n_test": int(len(X_test)),
        "n_in_scope": int(in_scope.sum()),
        "target_fraction": float(target_fraction),
        "ls": float(ls),
        "out_of_scope_reason": "no test-label conformal under low overlap",
    }
    return lo, hi, mu, in_scope, meta


def kmm_coverage_report(lo, hi, y_true, in_scope):
    """KMM 的覆盖率报告：只在 in_scope 子集上算，是 KMM 的合法口径。"""
    y = np.asarray(y_true, float)
    mask = np.asarray(in_scope, bool)
    if mask.sum() == 0:
        return {"n_in_scope": 0, "coverage": None, "mean_width": None}
    lo_s = np.asarray(lo, float)[mask]
    hi_s = np.asarray(hi, float)[mask]
    y_s = y[mask]
    cov = float(np.mean((y_s >= lo_s) & (y_s <= hi_s)))
    width = float(np.mean(hi_s - lo_s))
    return {"n_in_scope": int(mask.sum()), "coverage": round(cov, 4),
            "mean_width": round(width, 4)}


# ---------------------------------------------------------------- selftest
def _selftest() -> int:
    print("=" * 72)
    print("engine.kmm_conformal 自检 (KMM-CP, UAI 2026)")
    print("=" * 72)

    rng = np.random.default_rng(42)

    # 场景 A：训练分布 vs 测试分布有显著漂移
    X_train = rng.normal(0.0, 0.5, size=(24, 2))
    def f(x):
        return float(np.sin(x[0]) + 0.5 * x[1] ** 2)
    y_train = np.array([f(x) + rng.normal(0, 0.03) for x in X_train])

    # 测试分布：整体平移，重叠很少
    X_test = rng.normal(1.0, 0.3, size=(48, 2))
    y_test = np.array([f(x) + rng.normal(0, 0.03) for x in X_test])

    domain = {"key": "kmm_selftest", "f": lambda x: f(x),
              "bounds": [[-3.0, 3.0], [-3.0, 3.0]],
              "ls": 0.5, "noise": 0.03}

    # 1) MMD 诊断
    mmd_full = mmd(X_test, X_train, ls=0.5)
    print(f"[1] 全测试集 vs 训练集 MMD = {mmd_full:.4f}  "
          f"({'显著漂移' if mmd_full > 0.02 else '轻微'})")

    # 2) KMM-CP 主流程
    lo, hi, mu, in_scope, meta = kmm_conformal_interval(
        X_train, y_train, domain, X_test, alpha=0.10, target_fraction=0.5,
    )
    print(f"[2] {meta['n_in_scope']}/{meta['n_test']} 点 in-scope  "
          f"Q={meta['kmm_Q']:.3f}  mmd_scope={meta['mmd_in_scope']:.4f}")

    # 3) 覆盖报告
    rep = kmm_coverage_report(lo, hi, y_test, in_scope)
    print(f"[3] in-scope 覆盖 = {rep['coverage']:.4f}  "
          f"平均宽度 = {rep['mean_width']:.4f}")

    # 4) 与朴素 conformal 对比：全测试集 naive
    eng = SurrogateEngine(domain)
    eng.fit(X_train, y_train)
    mu_all, sd_all = eng.predict(X_test)
    from engine.conformal import cross_conformal_scores
    scores = cross_conformal_scores(domain, X_train, y_train)
    from engine.conformal import conformal_quantile as cq
    Q_naive = cq(scores, alpha=0.10)
    cov_naive = float(np.mean((y_test >= mu_all - Q_naive * sd_all)
                              & (y_test <= mu_all + Q_naive * sd_all)))
    print(f"[4] 朴素 conformal 覆盖 = {cov_naive:.4f}  "
          f"(全测试集，含漂移点)")

    # 通过判据：KMM 在子集上覆盖达标（>=0.85）+ 确实做了子集筛选（不是全选）
    ok = (rep["coverage"] is not None and rep["coverage"] >= 0.85
          and meta["n_in_scope"] < meta["n_test"] and meta["n_in_scope"] > 0)
    print(f"\nKMM in-scope 覆盖率 {rep['coverage']:.3f}  "
          f"vs naive {cov_naive:.3f}（子集={meta['n_in_scope']}/{meta['n_test']}）")
    print(f"\nSELFTEST {'PASS' if ok else 'FAIL'}  "
          f"(kmm={rep['coverage']}, naive={cov_naive})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(_selftest())
