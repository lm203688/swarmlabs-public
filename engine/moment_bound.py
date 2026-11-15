"""MomentBoundGate —— 涨落定理「统一凸体」思想在 UQ 门禁上的落地（P1）。

涨落定理（Crooks 开放问题 / Fable 5）核心结论：
  - 所有满足详细涨落定理 p(σ)/p(−σ)=e^σ 的分布，其统计量联合可达区域是一个
    凸体（moment body）；给定前 n−1 阶矩，第 n 阶矩有**尖锐下界、无上界**；
    所有已发表的 TUR / 偏度 / 尾概率 / 信息量界，都只是这个统一凸体的低维投影（影子）。
  - 最可操作的物理结论（已落地为 bidirectional_parity）：单向估计器可任意差，双向才必需。

本模块把「统一凸体 / 尖锐矩下界」转成两条可直接用于我们 surrogate 后验的可量化保证：
  1. 双侧 Cantelli 认证覆盖率下界：P(|y−ŷ| ≤ ε) ≥ 1 − 2σ²/(σ²+ε²)。
     对任意分布（只知方差）都成立，且下界是**尖锐**的（等号由两结果极值分布取到）——
     这正是定理「下界尖锐、上界不存在」的不对称性，在我们 UQ 上的投影。我们报的是
     *保证*，不是点估计。
  2. Bhatia–Davis 可达域一致性：任一满足 X∈[lo,hi]、均值 μ、方差 σ² 的分布，
     必有 σ² ≤ (hi−μ)(μ−lo)（Bhatia–Davis 1970）。等号由两结果极值分布取到——
     与涨落定理「所有可达分布都是两结果基本分布的混合」同构。
     若 GP 后验给出的 (μ,σ²) 落在可达域外，说明 posterior 退化/物理不自洽，直接 REJECT。

与 bidirectional_parity / ContextAwareVirtualLabGuard 同属 engine 层门禁，旁路式，可独立调用，
也可在 VirtualLabGuard 之后链式组合（见模块底部 compose 示例）。
"""
from __future__ import annotations

import numpy as np
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from .guard import GateStatus


# ── 基础数学（凸体投影 / 可达域）──────────────────────────────────────────

def cantelli_coverage_lb(std: float, tol: float) -> float:
    """单点双侧 Cantelli 覆盖率下界。

    P(|Y−μ| ≤ tol) ≥ 1 − 2σ²/(σ²+tol²)   （对 Chebyshev 的锐化，仅依赖方差）
    性质：下界尖锐（等号由两结果极值分布取到），且只有下界、没有对应上界——
          即涨落定理不对称性的直接投影。tol≤0 视为 0 保证。
    """
    s = float(std)
    t = float(tol)
    if t <= 0:
        return 0.0
    s2 = s * s
    return max(0.0, 1.0 - 2.0 * s2 / (s2 + t * t))


def bhatia_davis_reachable(mean: float, var: float, lo: float, hi: float) -> bool:
    """Bhatia–Davis 可达性：X∈[lo,hi] 且 Var[X]=var 时，必须 var ≤ (hi−μ)(μ−lo)。"""
    m, lo, hi = float(mean), float(lo), float(hi)
    if not (lo <= m <= hi):
        return False
    return float(var) <= (hi - m) * (m - lo) + 1e-12


def bhatia_davis_mean_bounds(var: float, lo: float, hi: float) -> Tuple[float, float]:
    """给定方差 var 与区间 [lo,hi]，可达均值的上下分支（两结果极值分布取到）。"""
    v = float(var)
    d = np.sqrt(max(0.0, (hi - lo) ** 2 + 4.0 * v))
    mid = 0.5 * (lo + hi)
    return (mid - 0.5 * d, mid + 0.5 * d)   # (μ_low, μ_high)


# ── 结果载体 ─────────────────────────────────────────────────────────────

@dataclass
class MomentBoundResult:
    status: GateStatus
    coverage_lb: float                 # 批次最坏情况认证覆盖率下界
    mean_bounds: Tuple[float, float]   # Bhatia–Davis 可达均值区间（凸体投影）
    body_consistent: bool              # posterior 是否落在可达凸体内
    reason: str
    per_point: List[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "coverage_lb": self.coverage_lb,
            "mean_bounds": list(self.mean_bounds),
            "body_consistent": self.body_consistent,
            "reason": self.reason,
            "per_point": self.per_point,
        }


# ── 门禁 ─────────────────────────────────────────────────────────────────

class MomentBoundGate:
    """涨落定理式 UQ 门禁：报**认证下界**，而非点估计。

    用法：
        g = MomentBoundGate(lo=0.0, hi=1.0, tol=0.1, min_coverage=0.90)
        r = g.check(means=[...], stds=[...])
        # r.status ∈ {PASS, CONTROLLED, REJECT}
    """

    def __init__(self, lo: float, hi: float, tol: float = 0.1,
                 min_coverage: float = 0.90, label: str = "moment_bound"):
        self.lo, self.hi = float(lo), float(hi)
        self.tol = float(tol)
        self.min_coverage = float(min_coverage)
        self.label = label

    def check(self, means: Sequence[float], stds: Sequence[float],
              tol: Optional[float] = None) -> MomentBoundResult:
        means = np.atleast_1d(np.asarray(means, float))
        stds = np.atleast_1d(np.asarray(stds, float))
        if means.shape != stds.shape:
            raise ValueError("means/stds 形状不一致")
        tol = self.tol if tol is None else float(tol)
        n = int(means.shape[0])

        per: List[dict] = []
        worst = 1.0
        reachable_all = True
        for i in range(n):
            mu, sg = float(means[i]), float(stds[i])
            lb = cantelli_coverage_lb(sg, tol)
            bd_ok = bhatia_davis_reachable(mu, sg * sg, self.lo, self.hi)
            if not bd_ok:
                reachable_all = False
            per.append({"i": i, "mean": mu, "std": sg,
                        "coverage_lb": lb, "bd_reachable": bd_ok})
            worst = min(worst, lb)

        # 批次整体均值视角下的可达凸体（Bhatia–Davis 均值区间）
        m_batch = float(means.mean())
        v_batch = float(stds.mean() ** 2)
        mb = bhatia_davis_mean_bounds(v_batch, self.lo, self.hi)

        if not reachable_all:
            status = GateStatus.REJECT
            bad = [p["i"] for p in per if not p["bd_reachable"]]
            reason = (f"posterior 落在 Bhatia–Davis 可达凸体之外 "
                      f"(点 {bad} 的 σ²>(hi−μ)(μ−lo): 物理不自洽/退化, 拒绝)")
        elif worst >= self.min_coverage:
            status = GateStatus.PASS
            reason = (f"认证覆盖率下界 {worst:.3f} ≥ 阈值 {self.min_coverage:.2f} "
                      f"— 统一凸体投影通过")
        else:
            status = GateStatus.CONTROLLED
            reason = (f"认证覆盖率下界 {worst:.3f} < 阈值 {self.min_coverage:.2f} "
                      f"— 不确定性过大, 需更多数据/双向校准(见 bidirectional_parity)")
        return MomentBoundResult(status, worst, mb, reachable_all, reason, per)


# ── 与既有 VirtualLabGuard 的组合入口（可选）──────────────────────────────
def compose_with_guard(guard_result, means, stds, lo, hi, tol=0.1, min_coverage=0.90):
    """把 MomentBoundGate 作为 VirtualLabGuard.gate() 结果之后的 UQ 校验层。

    返回 (GateStatus, MomentBoundResult)：取两者中更保守的状态。
    """
    mb = MomentBoundGate(lo, hi, tol=tol, min_coverage=min_coverage)
    res = mb.check(means, stds)
    from .guard import _ORDER  # 复用门禁优先级排序
    combined = max(guard_result.status, res.status, key=lambda s: _ORDER[s])
    return combined, res
