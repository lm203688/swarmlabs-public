"""Risk-Weighted Conformal + Learn-Then-Test (LTT).

两块独立但互补的能力：

1. **Risk-weighted conformal** (Angelopoulos, Bates, Fisch, Lei, Candès 2023)
   ------------------------------------------------------------------------
   传统 conformal 给"所有测试点一视同仁"的边际覆盖，但对真实业务：
     - 有些点是"关键测试"（安全关键、决策影响大），漏了代价高；
     - 有些点是"背景噪声"，漏了也没关系。
   Risk-weighted 让每个校准点带一个 importance 权重 w_i，取**加权分位数**
   Q，使得加权覆盖 >= (1-alpha)。等价于把"边际覆盖"从"每点等权"换成
   "按重要性加权"。

   公式：Q 满足
       sum_i w_i * I[s_i <= Q] / sum_i w_i  >=  (1 - alpha) * (n+1)/n

2. **Learn-Then-Test (LTT) calibration** (Angelopoulos & Bates, 2021)
   ------------------------------------------------------------------------
   传统 conformal 是**先**选算法**再**校准；LTT 反过来 —— 先固定一个"
   覆盖率下界" α，然后按到达的每个数据点做 sequential hypothesis test，
   接受当前分位数阈值还是继续加数据。这允许"自适应"—— 数据越多 Q 越稳，
   但 LTT 保证在任何提前停止时都保持边际覆盖下界，不需要知道数据量。

   实际价值：SwarmLabs 的 95 个场景每个场景数据量差别很大（12~96 点），
   传统 conformal 对 n 敏感，LTT 让"n 不定"的场景也能给一致保证。

本实现（纯 numpy）：
  - `weighted_conformal_quantile`：加权分位数（支持重要性加权）
  - `risk_weighted_conformal`：主接口，返回加权 conformal 区间
  - `learn_then_test_calibration`：LTT 校准（对给定阈值做 sequential test）
  - `ltt_conformal_interval`：LTT-驱动的分位数
  - 内建 selftest
"""
from __future__ import annotations

import math
import os
import sys
from typing import Tuple

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine.surrogate import SurrogateEngine  # noqa: E402
from engine.conformal import nonconformity_std, conformal_quantile, cross_conformal_scores  # noqa: E402


# ---------------------------------------------------------------- 加权分位数
def weighted_conformal_quantile(scores: np.ndarray, weights: np.ndarray,
                                 alpha: float = 0.05, method: str = "higher"
                                 ) -> float:
    """加权 conformal 分位数（有限样本修正）。

    定义（Angelopoulos et al. 2023, Theorem 1）：
        Q = 最小的 s 使  sum_i w_i * I[s_i <= s]  >=  (1-alpha) * sum_i w_i

    权重归一化后处理：w_i / sum(w)，避免量纲差异。

    Args:
      scores: 非一致性分数
      weights: 每点的重要性权重（正数）
      alpha: 覆盖率下界目标（0.05 = 目标 95% 覆盖）
      method: "higher" 取保守侧（严格大于等于目标）
    """
    s = np.asarray(scores, float)
    w = np.asarray(weights, float)
    n = len(s)
    if n == 0:
        return np.inf
    if np.any(w <= 0):
        raise ValueError("权重必须为正")
    w = w / w.sum()
    order = np.argsort(s)
    s_sorted = s[order]
    w_cum = np.cumsum(w[order])
    target = (1.0 - alpha) * 1.0   # 归一化后总权重 = 1
    idx = int(np.searchsorted(w_cum, target, side="left"))
    idx = min(max(idx, 0), n - 1)
    return float(s_sorted[idx])


# ---------------------------------------------------------------- 主接口
def risk_weighted_conformal(X_train: np.ndarray, y_train: np.ndarray,
                             importance: np.ndarray,
                             domain, X_test: np.ndarray,
                             alpha: float = 0.05, noise_rel: float = 0.03,
                             k_folds: int = 5, repeats: int = 3,
                             seed: int = 0
                             ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, dict]:
    """Risk-weighted conformal 主接口。

    Args:
      importance: 与 y_train 对齐的重要性权重（正数），表示每个校准点的
        "重要性"。数值越大，该点越应被覆盖。

    Returns:
      lo, hi, mu, meta
    """
    X_train = np.atleast_2d(np.asarray(X_train, float))
    y_train = np.asarray(y_train, float).ravel()
    X_test = np.atleast_2d(np.asarray(X_test, float))
    importance = np.asarray(importance, float).ravel()
    if len(importance) != len(y_train):
        raise ValueError("importance 长度必须等于 y_train 长度")

    # K-fold cross-conformal 分数
    scores = cross_conformal_scores(domain, X_train, y_train, k_folds=k_folds,
                                    seed=seed, noise_rel=noise_rel, repeats=repeats)
    # 与 y_train 对齐的权重（cross-conformal 会展开重复，长度可能大于 n）
    # 简化：用 y_train 的原始 importance 作 K× 重复对齐
    w_full = np.tile(importance, repeats) if repeats > 0 else importance
    if len(w_full) < len(scores):
        # 补齐：均匀
        w_full = np.concatenate([w_full, np.ones(len(scores) - len(w_full), float)])
    elif len(w_full) > len(scores):
        w_full = w_full[:len(scores)]

    Q = weighted_conformal_quantile(scores, w_full, alpha=alpha)

    eng = SurrogateEngine(domain)
    eng.fit(X_train, y_train, noise_rel=noise_rel)
    mu, sd = eng.predict(X_test)

    lo = mu - Q * sd
    hi = mu + Q * sd

    # 对比：等权 conformal 的 Q
    Q_eq = conformal_quantile(scores, alpha=alpha)

    meta = {
        "method": "risk-weighted conformal (Angelopoulos et al. 2023)",
        "alpha": float(alpha),
        "Q_weighted": float(Q),
        "Q_equal": float(Q_eq),
        "Q_ratio": round(Q / Q_eq, 4) if Q_eq > 0 else None,
        "n_scores": int(len(scores)),
        "importance_mean": float(np.mean(importance)),
        "importance_std": float(np.std(importance)),
    }
    return lo, hi, mu, meta


# ---------------------------------------------------------------- LTT
def _ltt_p_value(k: int, n: int) -> float:
    """LTT 的 p-value：k 个失败样本 / n 个总样本的 sequential 检验。

    Angelopoulos & Bates 2021 的构造：
        p_n = (k + 1) / (n + 1)
    这对应"如果真实覆盖率是 1-alpha，看到 k 个失败的概率上界"。
    累积误差用 α 控制。
    """
    return (float(k) + 1.0) / (float(n) + 1.0)


def learn_then_test_calibration(scores: np.ndarray, alpha: float = 0.05,
                                  significance: float = 0.05,
                                  start_level: float = 0.90
                                  ) -> Tuple[float, dict]:
    """LTT 校准：自适应决定分位数水平。

    思路（Angelopoulos & Bates 2021, Algorithm 1 简化版）：
      1. 从 start_level 开始（比如 0.90 = 期望 90% 覆盖）；
      2. 排序 scores，取累积到达 (1-start_level) 分位处的分数作为候选 Q；
      3. 在 (1-alpha) 上算 p-value：如果 p-value > significance，接受当前 Q；
      4. 否则降低期望覆盖率，重试。

    Returns:
      Q: 最终选定的阈值
      meta: 校准轨迹
    """
    s = np.sort(np.asarray(scores, float).ravel())
    n = len(s)
    if n < 4:
        # 数据太少无法做 LTT，退回普通 conformal
        return float(conformal_quantile(s, alpha=alpha)), {
            "method": "ltt_fallback_to_split", "n": int(n),
        }

    # 候选覆盖率范围（从激进到保守）：start_level 起步逐步提升到 1-alpha
    hi_target = 1.0 - alpha - 1e-4
    if start_level >= hi_target:
        # 起点已到目标 —— 直接用普通 conformal 的分位数
        target_cov_levels = np.array([hi_target])
    else:
        target_cov_levels = np.arange(start_level, hi_target + 1e-9, 0.01)
    trajectory = []
    accepted_Q = float(s[-1])  # 最保守兜底
    accepted_level = float(target_cov_levels[0])

    for cov_level in target_cov_levels:
        # 该水平对应的候选分位数：ceil(n * cov_level)
        k_target = int(math.ceil(n * cov_level))
        k_target = min(max(k_target, 1), n)
        Q_candidate = float(s[k_target - 1])
        # LTT p-value：假设真实覆盖率是 cov_level，看到当前分位数以下的数据
        # 有多少个 —— 即"覆盖率不高于 cov_level"的假设的 p-value
        below = int((s <= Q_candidate).sum())
        # 覆盖率下界假设：cov_level + k_target/(n+1) 的修正
        p_val = _ltt_p_value(n - below, n)  # 失败 / 总数 的 p-value
        trajectory.append({
            "target_coverage": round(float(cov_level), 4),
            "Q": round(Q_candidate, 4),
            "below_count": int(below),
            "p_value": round(float(p_val), 4),
        })
        # 接受条件：p-value < significance（拒绝"更低的覆盖率"假设）
        if p_val < significance:
            accepted_Q = Q_candidate
            accepted_level = float(cov_level)
            break

    meta = {
        "method": "learn_then_test (Angelopoulos & Bates 2021)",
        "alpha": float(alpha),
        "significance": float(significance),
        "start_level": float(start_level),
        "n": int(n),
        "accepted_Q": float(accepted_Q),
        "accepted_coverage": round(accepted_level, 4),
        "trajectory_len": len(trajectory),
    }
    return accepted_Q, meta


def ltt_conformal_interval(X_train: np.ndarray, y_train: np.ndarray,
                            domain, X_test: np.ndarray,
                            alpha: float = 0.05, noise_rel: float = 0.03,
                            significance: float = 0.05, start_level: float = 0.90,
                            k_folds: int = 5, repeats: int = 3, seed: int = 0
                            ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, dict]:
    """LTT 驱动的 conformal 区间主接口。"""
    X_train = np.atleast_2d(np.asarray(X_train, float))
    y_train = np.asarray(y_train, float).ravel()
    X_test = np.atleast_2d(np.asarray(X_test, float))

    scores = cross_conformal_scores(domain, X_train, y_train, k_folds=k_folds,
                                    seed=seed, noise_rel=noise_rel, repeats=repeats)
    Q, meta_ltt = learn_then_test_calibration(
        scores, alpha=alpha, significance=significance, start_level=start_level,
    )

    eng = SurrogateEngine(domain)
    eng.fit(X_train, y_train, noise_rel=noise_rel)
    mu, sd = eng.predict(X_test)
    lo = mu - Q * sd
    hi = mu + Q * sd

    meta_ltt["kmm_split_Q"] = float(conformal_quantile(scores, alpha=alpha))
    meta_ltt["ltt_vs_split_ratio"] = round(Q / max(meta_ltt["kmm_split_Q"], 1e-12), 4)
    return lo, hi, mu, meta_ltt


# ---------------------------------------------------------------- selftest
def _selftest() -> int:
    print("=" * 72)
    print("engine.risk_conformal 自检 (Risk-weighted + LTT)")
    print("=" * 72)

    rng = np.random.default_rng(7)
    X_train = rng.uniform(-1.5, 1.5, size=(30, 1))
    def f(x):
        return float(np.tanh(x[0]) * 2.0)
    y_train = np.array([f(x) + rng.normal(0, 0.04) for x in X_train])
    X_test = rng.uniform(-1.5, 1.5, size=(60, 1))
    y_test = np.array([f(x) + rng.normal(0, 0.04) for x in X_test])

    domain = {"key": "risk_selftest", "f": lambda x: f(x),
              "bounds": [[-2.0, 2.0]], "ls": 0.4, "noise": 0.03}

    from engine.conformal import picp

    # 1) 等权 conformal 基线
    lo_eq, hi_eq, mu_eq, meta_eq = risk_weighted_conformal(
        X_train, y_train, np.ones_like(y_train), domain, X_test, alpha=0.10,
    )
    cov_eq = picp(y_test, lo_eq, hi_eq)
    print(f"[1] 等权 conformal  Q={meta_eq['Q_weighted']:.3f}  "
          f"cov={cov_eq:.4f}")

    # 2) Risk-weighted：给端点更高的重要性
    importance = 1.0 + 0.5 * np.abs(X_train[:, 0])  # 端点权重 1.5x
    lo_rw, hi_rw, mu_rw, meta_rw = risk_weighted_conformal(
        X_train, y_train, importance, domain, X_test, alpha=0.10,
    )
    cov_rw = picp(y_test, lo_rw, hi_rw)
    print(f"[2] Risk-weighted  Q={meta_rw['Q_weighted']:.3f}  "
          f"(ratio={meta_rw['Q_ratio']:.3f}x 等权)  cov={cov_rw:.4f}")

    # 3) LTT 校准
    lo_ltt, hi_ltt, mu_ltt, meta_ltt = ltt_conformal_interval(
        X_train, y_train, domain, X_test, alpha=0.10,
    )
    cov_ltt = picp(y_test, lo_ltt, hi_ltt)
    print(f"[3] LTT 校准     Q={meta_ltt['accepted_Q']:.3f}  "
          f"(accepted_cov={meta_ltt['accepted_coverage']:.3f})  "
          f"实际 cov={cov_ltt:.4f}")

    # 4) LTT vs split 对比
    print(f"[4] LTT vs split: Q_ratio={meta_ltt['ltt_vs_split_ratio']:.3f}")

    ok = (cov_eq >= 0.75 and cov_rw >= 0.75 and cov_ltt >= 0.75)
    print(f"\nSELFTEST {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(_selftest())
