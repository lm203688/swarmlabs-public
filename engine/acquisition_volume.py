"""ConvexBodyAcquisition —— 涨落定理「统一凸体」思想在主动学习上的落地（P1, #4）。

诚实性前提
----------
在纯高斯假设下，最大化后验可达椭球体积 ≡ 最大化预测方差（max_std）≡ BALD
信息增益（两者差一个常数）。所以"凸体"本身并不提供比 BALD 更好的**方差型**收购函数。

涨落定理真正给出的额外结构是 **可达矩区域的认证边界**（见 moment_bound.py）：
  - 双侧 Cantelli 认证覆盖率下界：P(|y−ŷ|≤ε) ≥ 1 − 2σ²/(σ²+ε²)
  - Bhatia–Davis 可达均值区间（凸体投影）
这些边界是**下界、尖锐、只有下界没有上界**——与定理不对称性同构。

因此本模块把收购目标从"方差"换成"**可达矩区域的认证质量**"：
  选下一个真实实验点 x，使整批评估网格上的**最坏认证覆盖下界**收紧最多。
  score(x) = worst_lb(σ_after(x)) − worst_lb(σ_before)
这直接复用了 moment_bound 的矩界，且天然区别于 BALD：BALD 只看方差，
本收购函数看的是"我们敢保证的覆盖率提高了多少"。这是凸体思想在我们 UQ 上的真实增量。

旁路式、零破坏：surrogate 缺 _K / L 时自动降级到 max_std（仍标注为对角近似）。
"""

from __future__ import annotations

import numpy as np
from scipy.linalg import solve_triangular
from typing import Optional, Sequence


class ConvexBodyAcquisition:
    """选"使整批认证覆盖下界收紧最多"的下一个真实实验点。

    用法：
        acq = ConvexBodyAcquisition(tol=0.1)
        x_next = acq.next(surrogate, space, n_candidates=200, grid_size=40)
    """

    def __init__(self, tol: float = 0.1, min_improvement: float = 1e-4,
                 label: str = "convex_body"):
        self.tol = float(tol)
        self.min_improvement = float(min_improvement)
        self.label = label

    # ── 内部：整批最坏 Cantelli 覆盖下界 ──────────────────────────────────
    @staticmethod
    def _worst_lb(stds, tol):
        from .moment_bound import cantelli_coverage_lb
        if len(stds) == 0:
            return 0.0
        return float(min(cantelli_coverage_lb(s, tol) for s in stds))

    def next(self, surrogate, space, n_candidates=200, grid_size=40,
             noise=None, rng=None):
        rng = rng or np.random.default_rng(7)
        cands = space.sample(n_candidates, rng)
        grid = space.sample(grid_size, rng)

        # 当前网格预测 std（基线，由 surrogate 直接给出）
        try:
            _, cur_std = surrogate.predict(grid)
            cur_std = np.asarray(cur_std, float).ravel()
            cur_worst = self._worst_lb(cur_std, self.tol)
        except Exception:
            return cands[0]   # 连 predict 都没有 → 彻底降级

        # 需要交叉协方差（surrogate 须有 _K + L）
        L = getattr(surrogate, "L", None)
        if L is None:
            # 降级：无交叉协方差，退化为 max_std
            _, std = surrogate.predict(cands)
            return cands[int(np.argmax(std))]

        if noise is None:
            noise = float(getattr(surrogate, "nv", 0.03 ** 2))
        nu2 = max(noise, 1e-12)

        best_i, best_score, best_x = 0, -np.inf, cands[0]
        for i in range(n_candidates):
            cand = cands[i]
            # 候选点自身预测方差 σ_x² = K(x,x) − K(x,X)·Kinv·K(X,x)
            Kxx = float(surrogate._K(cand.reshape(1, -1), cand.reshape(1, -1))[0, 0])
            Kx_tr = surrogate._K(cand.reshape(1, -1), surrogate.X).ravel()  # (n_train,)
            w = solve_triangular(L, Kx_tr, lower=True)
            z = solve_triangular(L.T, w, lower=False)
            sig2_x = max(Kxx - float(Kx_tr @ z), 1e-12)
            # 网格每点与候选的核向量 k_j = K(grid_j, cand)（直接核，不经训练 Kinv）
            kx = surrogate._K(grid, cand.reshape(1, -1)).ravel()           # (grid_size,)
            # 投影方差：σ'_j² = σ_j² − k_j²/(ν²+σ_x²)
            proj = np.maximum(cur_std ** 2 - (kx ** 2) / (nu2 + sig2_x), 1e-12)
            proj_std = np.sqrt(proj)
            proj_worst = self._worst_lb(proj_std, self.tol)
            score = proj_worst - cur_worst
            if score > best_score:
                best_score, best_i, best_x = score, i, cand

        # 若没有任何点能改善认证下界（已收敛 / 噪声主导），退回 max_std 不强行挑
        if best_score < self.min_improvement:
            _, std = surrogate.predict(cands)
            return cands[int(np.argmax(std))]
        return best_x
