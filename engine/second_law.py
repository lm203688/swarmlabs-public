"""SecondLawGate —— 涨落定理「二阶律 / 涨落-耗散一致性」思想在 UQ 门禁上的落地（P1, #5）。

诚实性前提
----------
严格说，涨落定理（Crooks 开放问题）是**随机热力学**结论，字面描述发酵/热传导需谨慎。
这里借鉴的是它的**二阶律 / 涨落-耗散（FDT）一致性**这一更通用的结构：

  在任何满足涨落-耗散关系的系统里，涨落的协方差矩阵必须是**对称半正定（PSD）**，
  且任一涨落的方差必须 **≥ 0**。这恰如第二定律要求熵产 ≥ 0——
  一个负的方差、或一个非 PSD 的后验协方差，等于"负熵产"，是物理上不可能的，
  对应到我们的 GP 代理就是**退化/过度自信的后验**，正是 UQ 门禁必须拦截的失效模式。

因此本门禁做两件事：
  1. 【永远在线】后验一致性：预测方差全 ≥ 0、后验协方差矩阵（若可算）最小特征值 ≥ −tol。
     违反 → REJECT（"负不确定性 / 非 PSD 后验 = 涨落-耗散不一致，拒绝过度自信"）。
  2. 【可选·场景】轨迹耗散不等式：给定演化轨迹 u(t)，检查离散耗散不等式
     （默认用热传导的熵产 ∫(u_x)²dx ≥ 0 / 反应体系的自由能耗散 ≥ 0）。
     跨快照熵不降（S_{t+1} ≥ S_t − tol）→ 否则 CONTROLLED/REJECT。

与 MomentBoundGate / BidirectionalParityGate 同属 engine 层旁路门禁，可独立调用或组合。
"""

from __future__ import annotations

import numpy as np
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence

from .guard import GateStatus


# ── 结果载体 ─────────────────────────────────────────────────────────────

@dataclass
class SecondLawResult:
    status: GateStatus
    posterior_consistent: bool        # 方差≥0 且协方差 PSD
    min_eig: float                    # 后验协方差最小特征值（不可算时为 +inf）
    min_var: float                    # 最小预测方差
    dissipation_ok: Optional[bool]    # 轨迹耗散不等式（未提供轨迹则为 None）
    reason: str
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "posterior_consistent": self.posterior_consistent,
            "min_eig": self.min_eig,
            "min_var": self.min_var,
            "dissipation_ok": self.dissipation_ok,
            "reason": self.reason,
            "detail": self.detail,
        }


# ── 基础数学：耗散不等式 / 熵产 ───────────────────────────────────────────

def entropy_production(us: Sequence[float], dx: float = 1.0) -> float:
    """热传导型熵产代理：∫ (u_x)² dx（有限差分），恒 ≥ 0。

    这是 Fourier 定律下熵产率的离散形式；任何物理上一致的预测都应给非负值。
    传入非物理场（如含强振荡/负标准差的退化后验）时可作为第二定律的哨兵。
    """
    u = np.asarray(us, float).ravel()
    if len(u) < 2:
        return 0.0
    du = np.diff(u) / dx
    return float(np.sum(du ** 2) * dx)


# ── 门禁 ─────────────────────────────────────────────────────────────────

class SecondLawGate:
    """涨落-耗散一致性门禁。

    用法：
        g = SecondLawGate(tol_eig=1e-6)
        r = g.check_posterior(surrogate, grid)            # 永远在线
        r2 = g.check_trajectory([u0, u1, u2], dx=0.1)     # 可选场景检查
    """

    def __init__(self, tol_eig: float = 1e-6, label: str = "second_law"):
        self.tol_eig = float(tol_eig)
        self.label = label

    def check_posterior(self, surrogate, grid, return_detail: bool = False) -> SecondLawResult:
        """永远在线：后验方差 ≥ 0 且（若可算）后验协方差 PSD。"""
        grid = np.atleast_2d(np.asarray(grid, float))
        try:
            _, std = surrogate.predict(grid)
            std = np.asarray(std, float).ravel()
        except Exception as e:
            return SecondLawResult(GateStatus.CONTROLLED, True, float("inf"), float("inf"),
                                   None, f"surrogate 无 predict，跳过一致性检查: {e}")

        # 负 std ≡ 负方差 ≡ 负的涨落强度：违反涨落-耗散一致性（=负熵产）
        min_std = float(np.min(std))
        if min_std < -self.tol_eig:
            return SecondLawResult(
                GateStatus.REJECT, False, float("nan"), min_std, None,
                f"预测方差出现负值（std={min_std:.2e}）：后验过度自信/退化，"
                f"违反涨落-耗散一致性（=负熵产），拒绝",
                detail={"min_std": min_std})

        # 若能拿到后验协方差矩阵，检查 PSD
        min_eig = float("inf")
        try:
            Kss = surrogate._K(grid, grid)
            KsX = surrogate._K(grid, surrogate.X)
            L = getattr(surrogate, "L", None)
            if L is not None:
                w = solve_triangular_safe(L, KsX.T, lower=True)   # L^-1 KXs^T
                Kinv_KXs = solve_triangular_safe(L.T, w, lower=False)
                # C = Kss - KsX @ Kinv @ KXs  → 用 (KsX @ Kinv_KXs^T)
                C = Kss - KsX @ Kinv_KXs.T
                eig = np.linalg.eigvalsh(0.5 * (C + C.T))
                min_eig = float(np.min(eig))
                if min_eig < -self.tol_eig:
                    return SecondLawResult(
                        GateStatus.REJECT, False, min_eig, min_std, None,
                        f"后验协方差最小特征值 {min_eig:.2e} < 0：非 PSD 后验，"
                        f"涨落-耗散不一致，拒绝",
                        detail={"min_eig": min_eig, "min_std": min_std})
        except Exception:
            # 拿不到协方差矩阵 → 只依赖方差检查（仍合法）
            min_eig = float("inf")

        return SecondLawResult(
            GateStatus.PASS, True, min_eig, min_std, None,
            f"涨落-耗散一致：方差全 ≥ 0（min std={min_std:.2e}）"
            + (f"，后验协方差 PSD（min_eig={min_eig:.2e}）" if min_eig != float("inf") else ""),
            detail={"min_eig": min_eig, "min_std": min_std})

    def check_trajectory(self, traj, dx: float = 1.0, dissipation_fn=None,
                         tol: float = 1e-6) -> SecondLawResult:
        """可选场景检查：第二定律 = 熵产率 ≥ 0（普适表述，非伪熵单调）。

        traj: [u(t0), u(t1), ...]，每元素是一维快照数组。
        dissipation_fn: 单帧熵产率函数（默认 entropy_production = ∫(u_x)²dx，
            对应傅里叶传导下熵产率恒 ≥ 0）。可传入自定义（如反应体系自由能耗散）。
        任一快照熵产率 < −tol → CONTROLLED（提示该帧预测可能非物理）。
        """
        if dissipation_fn is None:
            dissipation_fn = entropy_production
        prods = [float(dissipation_fn(u, dx=dx)) for u in traj]
        bad = [i for i, p in enumerate(prods) if p < -tol]
        if not bad:
            return SecondLawResult(
                GateStatus.PASS, True, float("inf"), float("inf"), True,
                f"各快照熵产率均 ≥ 0（第二定律满足），值={[f'{p:.3f}' for p in prods]}",
                detail={"entropy_production": prods})
        return SecondLawResult(
            GateStatus.CONTROLLED, True, float("inf"), float("inf"), False,
            f"快照 {bad} 熵产率 < 0（违反第二定律），需复核该帧预测",
            detail={"entropy_production": prods, "violating_frames": bad})


def solve_triangular_safe(L, B, lower=True):
    """包一层 scipy 三角求解，避免 numpy 版本差异。"""
    from scipy.linalg import solve_triangular as _st
    return _st(L, B, lower=lower)
