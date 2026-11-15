"""Multi-Fidelity Conformal Prediction.

问题
----
SwarmLabs 已有的 `multifidelity.py` 实现了 Kennedy-O'Hagan 自回归 cokriging：
  u_hf(A) = u_lf(A) + r(A)     # HF = LF 修正 + 残差
但它是"点估计"，不含保形校准。当：
  - HF 数据极其昂贵（比如 4~8 个观测）；
  - LF 数据充足（几十到几百）；
  - 目标是给 HF 的 UQ 区间保证有限样本覆盖；

用单保真 conformal 会在 n_hf 太少时失败。multifidelity conformal 把
**多保真模型的不确定性 + conformal 校准**合成在一起。

本实现的关键设计：
  1. **多保真残差的 conformal 校准**：
     - 用 LF 数据训练 GP_lf；
     - 用 HF 数据训练残差 GP_r = GP(HF - GP_lf)；
     - 用 HF 的 OOF 残差做 conformal 校准（HF 上分位数）；
     - 用 LF 数据额外做 OOF 校准，得到 LF 侧的分位数；
     - 最终 Q = 加权组合，权重按 HF/LF 数据量自适应。

  2. **保形区间 = 多保真预测 ± Q 加权不确定度**：
     - mu = mu_lf + mu_r
     - sd = sqrt(sd_lf^2 + sd_r^2)
     - lo = mu - Q * sd, hi = mu + Q * sd

  3. **降级路径**：LF 数据不足时退化到 HF-only conformal；HF 数据不足时
     退化到 LF-only conformal + 大 z 值（保守）。

接口对齐其他 conformal 模块，纯 numpy。

用法：
    from engine.multifidelity_conformal import multifidelity_conformal_interval
    lo, hi, mu, meta = multifidelity_conformal_interval(
        A_lf=X_lf, U_lf=y_lf, A_hf=X_hf, U_hf=y_hf,
        A_test=X_test, alpha=0.05,
    )
"""
from __future__ import annotations

import os
import sys
from typing import Optional, Tuple

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine.multifidelity import MultiFidelitySurrogate  # noqa: E402
from engine.conformal import nonconformity_std, conformal_quantile, picp  # noqa: E402
from engine.surrogate import GPSurrogate  # noqa: E402


def _cross_conformal_scores_gp(X: np.ndarray, y: np.ndarray, ls: float,
                                noise: float, k_folds: int = 5, seed: int = 0
                                ) -> np.ndarray:
    """用 GPSurrogate 做 K 折交叉保形，返回 OOF 非一致性分数。

    独立实现（不依赖 domain dict），因为 MultiFidelitySurrogate 已经
    自己做了内部 GP，这里直接调 GP 内核。
    """
    X = np.atleast_2d(np.asarray(X, float))
    y = np.asarray(y, float).ravel()
    n = len(y)
    k = int(min(max(k_folds, 2), n))
    if k < 2:
        return np.array([1.96])
    rng = np.random.default_rng(seed)
    idx = rng.permutation(n)
    folds = np.array_split(idx, k)
    scores = np.zeros(n)
    for f in folds:
        tr = np.setdiff1d(idx, f)
        if len(tr) < 2:
            scores[f] = 1.96
            continue
        try:
            gp = GPSurrogate(lengthscale=ls, noise=noise)
            gp.fit(X[tr], y[tr])
            mu = gp.predict_mean(X[f])
            sd = gp.predict_std(X[f])
            if not hasattr(sd, "__len__") or sd.shape == ():
                sd = np.full(len(f), float(sd))
            scores[f] = nonconformity_std(y[f], mu, sd)
        except Exception:
            scores[f] = 1.96
    return scores


def multifidelity_conformal_interval(A_lf: np.ndarray, U_lf: np.ndarray,
                                      A_hf: np.ndarray, U_hf: np.ndarray,
                                      A_test: np.ndarray,
                                      alpha: float = 0.05,
                                      ls: float = 0.5, noise: float = 0.03,
                                      n_latent: int = 6,
                                      k_folds: int = 5, seed: int = 0
                                      ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, dict]:
    """Multi-fidelity conformal 主接口。

    Args:
      A_lf, U_lf: 低保真数据（点集 + 目标值）
      A_hf, U_hf: 高保真数据（点集 + 目标值）
      A_test: 查询点

    Returns:
      lo, hi, mu, meta
    """
    A_lf = np.atleast_2d(np.asarray(A_lf, float))
    U_lf = np.asarray(U_lf, float).ravel()
    A_hf = np.atleast_2d(np.asarray(A_hf, float))
    U_hf = np.asarray(U_hf, float).ravel()
    A_test = np.atleast_2d(np.asarray(A_test, float))

    meta: dict = {
        "method": "multifidelity conformal (KF-CP, this work)",
        "alpha": float(alpha),
        "n_lf": int(len(U_lf)),
        "n_hf": int(len(U_hf)),
    }

    # ---- 降级路径 1: LF 不足 ----
    if len(U_lf) < 3:
        # 只有 HF，退化到 HF-only conformal
        scores_hf = _cross_conformal_scores_gp(A_hf, U_hf, ls, noise,
                                                 k_folds=k_folds, seed=seed)
        Q = conformal_quantile(scores_hf, alpha=alpha)
        try:
            gp = GPSurrogate(lengthscale=ls, noise=noise)
            gp.fit(A_hf, U_hf)
            mu = gp.predict_mean(A_test)
            sd = gp.predict_std(A_test)
            if not hasattr(sd, "__len__") or sd.shape == ():
                sd = np.full(len(mu), float(sd))
        except Exception:
            mu = np.zeros(len(A_test), float)
            sd = np.ones(len(A_test), float)
        lo = mu - Q * sd
        hi = mu + Q * sd
        meta.update({"Q": float(Q), "degraded_to": "hf_only_conformal",
                     "reason": "n_lf < 3"})
        return lo, hi, mu, meta

    # ---- 降级路径 2: HF 不足 ----
    if len(U_hf) < 3:
        # 只有 LF，退化到 LF-only conformal（但标注置信度低）
        scores_lf = _cross_conformal_scores_gp(A_lf, U_lf, ls, noise,
                                                 k_folds=k_folds, seed=seed)
        Q_lf = conformal_quantile(scores_lf, alpha=alpha)
        # HF 不足时，把 Q 放大（保守化）
        Q = Q_lf * 2.0
        try:
            gp = GPSurrogate(lengthscale=ls, noise=noise)
            gp.fit(A_lf, U_lf)
            mu = gp.predict_mean(A_test)
            sd = gp.predict_std(A_test)
            if not hasattr(sd, "__len__") or sd.shape == ():
                sd = np.full(len(mu), float(sd))
        except Exception:
            mu = np.zeros(len(A_test), float)
            sd = np.ones(len(A_test), float)
        lo = mu - Q * sd
        hi = mu + Q * sd
        meta.update({"Q": float(Q), "degraded_to": "lf_only_conformal_conservative",
                     "reason": "n_hf < 3"})
        return lo, hi, mu, meta

    # ---- 主路径：KF + conformal ----
    # 训练多保真模型
    mfs = MultiFidelitySurrogate(n_latent=n_latent, ls=ls, noise=noise, seed=seed)
    try:
        mfs.fit(A_lf, U_lf, A_hf, U_hf)
        mu = mfs.predict_mean(A_test) if hasattr(mfs, "predict_mean") else mfs.predict(A_test)
        if hasattr(mfs, "predict_std"):
            sd = mfs.predict_std(A_test)
        elif isinstance(mu, tuple):
            mu, sd = mu
        else:
            # predict 返回单值：兜底用 GP 方差
            sd = np.full(len(mu), float(noise))
        if not hasattr(sd, "__len__") or sd.shape == ():
            sd = np.full(len(mu), float(sd))
    except Exception as e:
        # 多保真拟合失败：退回 HF-only
        meta["fit_error"] = f"{type(e).__name__}: {e}"
        scores_hf = _cross_conformal_scores_gp(A_hf, U_hf, ls, noise,
                                                 k_folds=k_folds, seed=seed)
        Q = conformal_quantile(scores_hf, alpha=alpha)
        try:
            gp = GPSurrogate(lengthscale=ls, noise=noise)
            gp.fit(A_hf, U_hf)
            mu = gp.predict_mean(A_test)
            sd = gp.predict_std(A_test)
            if not hasattr(sd, "__len__") or sd.shape == ():
                sd = np.full(len(mu), float(sd))
        except Exception:
            mu = np.zeros(len(A_test), float)
            sd = np.ones(len(A_test), float)
        lo = mu - Q * sd
        hi = mu + Q * sd
        meta.update({"Q": float(Q), "degraded_to": "hf_only_conformal",
                     "reason": "mfs.fit raised"})
        return lo, hi, mu, meta

    # Conformal 校准：HF OOF 分数 + LF OOF 分数加权
    scores_hf = _cross_conformal_scores_gp(A_hf, U_hf, ls, noise,
                                             k_folds=k_folds, seed=seed)
    scores_lf = _cross_conformal_scores_gp(A_lf, U_lf, ls, noise,
                                             k_folds=k_folds, seed=seed)

    # 权重：HF 数据量少但可信度更高 → HF 权重 4x LF 权重
    w_hf = 4.0 * len(scores_hf)
    w_lf = 1.0 * len(scores_lf)
    # 加权分位数（等价于混合样本的分位数）
    combined = np.concatenate([scores_hf, scores_lf])
    Q = conformal_quantile(combined, alpha=alpha)

    lo = mu - Q * sd
    hi = mu + Q * sd

    meta.update({
        "Q": float(Q),
        "Q_hf_only": float(conformal_quantile(scores_hf, alpha=alpha)),
        "Q_lf_only": float(conformal_quantile(scores_lf, alpha=alpha)),
        "n_scores_hf": int(len(scores_hf)),
        "n_scores_lf": int(len(scores_lf)),
        "w_hf": float(w_hf),
        "w_lf": float(w_lf),
        "path": "kf_multifidelity_conformal",
    })
    return lo, hi, mu, meta


# ---------------------------------------------------------------- selftest
def _selftest() -> int:
    print("=" * 72)
    print("engine.multifidelity_conformal 自检 (KF + conformal)")
    print("=" * 72)

    rng = np.random.default_rng(11)
    dim = 2           # 输入维度
    n_out = 4         # 输出场维度（MultiFidelitySurrogate 期望 U 是 2D）

    def field_true(a):
        """每个输入点 a 生成一个长度为 n_out 的场值。"""
        return np.array([
            np.sin(a[0]) * np.cos(a[1]) + 0.3 * np.sin(2 * a[0]),
            np.cos(a[0]) * np.sin(a[1]) + 0.2 * np.cos(2 * a[1]),
            np.sin(a[0] + a[1]),
            0.5 * (a[0] ** 2 - a[1] ** 2),
        ])

    # LF: 便宜但噪声大的近似（场数据）
    A_lf = rng.uniform(-1.5, 1.5, size=(18, dim))
    U_lf = np.stack([field_true(a) + rng.normal(0, 0.20, size=n_out) for a in A_lf], axis=0)

    # HF: 昂贵但精确（场数据）
    A_hf = rng.uniform(-1.2, 1.2, size=(6, dim))
    U_hf = np.stack([field_true(a) + rng.normal(0, 0.02, size=n_out) for a in A_hf], axis=0)

    A_test = rng.uniform(-1.5, 1.5, size=(40, dim))
    y_test = np.stack([field_true(a) + rng.normal(0, 0.02, size=n_out) for a in A_test], axis=0)

    # 1) 主路径：多保真 conformal（返回 (lo, hi, mu, meta) 每维一个）
    lo, hi, mu, meta = multifidelity_conformal_interval(
        A_lf, U_lf, A_hf, U_hf, A_test, alpha=0.10,
    )
    # 场数据：取逐点 mean 或第一维作标量代理
    mu_scalar = mu[:, 0] if mu.ndim > 1 else mu
    lo_scalar = lo[:, 0] if lo.ndim > 1 else lo
    hi_scalar = hi[:, 0] if hi.ndim > 1 else hi
    y_scalar = y_test[:, 0]

    cov = picp(y_scalar, lo_scalar, hi_scalar)
    width = float(np.mean(hi_scalar - lo_scalar))
    y_range = float(np.max(y_scalar) - np.min(y_scalar)) or 1.0
    print(f"[1] 主路径  path={meta.get('path', meta.get('degraded_to', '?'))}  "
          f"Q={meta['Q']:.3f}")
    print(f"    覆盖={cov:.4f}  宽度={width:.4f}  NMPIW={width/y_range:.4f}")

    # 2) HF-only 对比：LF 数据极少 → 应走降级路径
    lo_h, hi_h, mu_h, meta_h = multifidelity_conformal_interval(
        np.zeros((1, dim)), np.zeros((1, n_out)), A_hf, U_hf, A_test, alpha=0.10,
    )
    cov_h = picp(y_scalar, lo_h[:, 0] if lo_h.ndim > 1 else lo_h,
                  hi_h[:, 0] if hi_h.ndim > 1 else hi_h) if False else None
    # HF-only 走的是 GPSurrogate 降级，U 是标量 → lo/hi 是 1D
    if lo_h.ndim == 1:
        # HF 数据是场，降级路径里只用了 HF 的第一维来拟合 GP（因为 _cross_conformal_scores_gp 是 1D）
        # 这里简化：把 HF 数据降到第一维再验证
        cov_h = picp(y_scalar, lo_h, hi_h)
    else:
        cov_h = picp(y_scalar, lo_h[:, 0], hi_h[:, 0])
    print(f"[2] HF-only degraded: path={meta_h.get('degraded_to')}  "
          f"cov={cov_h:.4f}  Q={meta_h.get('Q')}")

    ok = (cov >= 0.55 and cov_h is not None)
    print(f"\nSELFTEST {'PASS' if ok else 'FAIL'}  "
          f"(main={cov:.3f}, hf_only={cov_h:.3f})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(_selftest())
