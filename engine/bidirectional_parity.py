"""BidirectionalParityGate —— 受详细涨落定理启发的『双向对等』门禁。

背景（见 docs/insights/fluctuation-theorem-convexbody.md）：
Gavin Crooks 把随机热力学开放问题交给 Claude(Fable 5) 后被统一——
所有满足详细涨落定理(DFT) p(σ)/p(−σ)=e^σ 的分布，其统计量联合可达区域是一个
**凸体(moment body)**。最可操作的物理结论是：

    ⚠️ 单向自由能估计器可以任意差，双向（正向+反向）估计才是必需的。

映射到 SwarmLabs 的「虚拟↔真实对等(mirror_run)」：
- 只做了 虚拟→真实 单方向校准 → 等价于单向估计器，可能系统性误导 → CONTROLLED
- 同时有 虚拟→真实 与 真实→虚拟 双向校准且都在阈值内 → PASS
- 双向都在但任一超阈值 → REJECT（对等失败，虚拟不可信）
- 两个方向都缺 → REJECT（无校准证据）

这是把一条热力学硬结论转成我们「可信替代边界」的可执行治理规则，零破坏式增量：
不修改任何既有门禁，独立成件、可被 serve.py / agentteams 组合调用。
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Dict, Optional


class ParityStatus(str, enum.Enum):
    PASS = "pass"          # 双向对等都在阈值内：虚拟结果可信
    CONTROLLED = "controlled"  # 只做了单向：可能误导，受控使用
    REJECT = "reject"      # 双向都缺 / 对等失败：虚拟不可信


# 越硬序数越大，便于合并多门禁结论。
_ORDER = {ParityStatus.PASS: 0, ParityStatus.CONTROLLED: 1, ParityStatus.REJECT: 2}


@dataclass
class ParityResult:
    """单次双向对等门禁结果。"""

    status: ParityStatus
    reason: str
    tol: float
    forward: Optional[Dict[str, float]] = None
    reverse: Optional[Dict[str, float]] = None
    forward_max_rel: float = 0.0
    reverse_max_rel: float = 0.0

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "reason": self.reason,
            "tol": self.tol,
            "forward": self.forward,
            "reverse": self.reverse,
            "forward_max_rel": round(float(self.forward_max_rel), 4),
            "reverse_max_rel": round(float(self.reverse_max_rel), 4),
        }

    @property
    def ok(self) -> bool:
        return self.status == ParityStatus.PASS


def _max_rel_dev(record: Dict[str, float], ref: Dict[str, float]) -> float:
    """计算 record 相对 ref 在各共有指标上的最大相对偏差（无共有指标返回 1.0）。"""
    shared = [k for k in record if k in ref]
    if not shared:
        return 1.0
    devs = []
    for k in shared:
        denom = max(abs(ref[k]), 1e-9)
        devs.append(abs(record[k] - ref[k]) / denom)
    return float(max(devs))


class BidirectionalParityGate:
    """双向对等门禁：硬性要求虚拟↔真实双向校准证据。

    用法：
        gate = BidirectionalParityGate(tol=0.10)
        res = gate.check({"forward": fwd_record, "reverse": rev_record})
        # 或从两次 mirror_run 结果构造：
        res = gate.from_mirror_runs(mirror_a, mirror_b)
    """

    def __init__(self, tol: float = 0.10):
        self.tol = float(tol)

    def check(self, parity: Dict[str, object]) -> ParityResult:
        fwd = parity.get("forward")
        rev = parity.get("reverse")

        # 两个方向都缺 → 无校准证据，直接拒。
        if not fwd and not rev:
            return ParityResult(
                status=ParityStatus.REJECT,
                reason="无校准证据：forward/reverse 双向记录均缺失，虚拟结果不可信",
                tol=self.tol,
            )

        # 只做单向 → 等价于单向自由能估计器，可能任意误导 → CONTROLLED。
        if bool(fwd) != bool(rev):
            which = "虚拟→真实(forward)" if fwd else "真实→虚拟(reverse)"
            return ParityResult(
                status=ParityStatus.CONTROLLED,
                reason=f"仅完成单向校准（{which}）。"
                       f"详细涨落定理结论：单向估计器可任意偏，需双向对等方可信任",
                tol=self.tol,
                forward=dict(fwd) if fwd else None,
                reverse=dict(rev) if rev else None,
            )

        # 双向都在：以彼此为参照算最大相对偏差；任一超阈值即拒。
        fwd_d = dict(fwd)
        rev_d = dict(rev)
        f_rel = _max_rel_dev(fwd_d, rev_d)
        r_rel = _max_rel_dev(rev_d, fwd_d)
        if f_rel <= self.tol and r_rel <= self.tol:
            return ParityResult(
                status=ParityStatus.PASS,
                reason=f"双向对等通过（max_rel≤{self.tol}）：虚拟↔真实互为可逆校准",
                tol=self.tol,
                forward=fwd_d, reverse=rev_d,
                forward_max_rel=f_rel, reverse_max_rel=r_rel,
            )
        bad = "forward" if f_rel > r_rel else "reverse"
        return ParityResult(
            status=ParityStatus.REJECT,
            reason=f"双向对等失败：{bad} 相对偏差 {max(f_rel, r_rel):.3f} > tol {self.tol}，"
                   f"虚拟不可信",
            tol=self.tol,
            forward=fwd_d, reverse=rev_d,
            forward_max_rel=f_rel, reverse_max_rel=r_rel,
        )

    def from_mirror_runs(self, forward_mirror: Dict[str, object],
                         reverse_mirror: Dict[str, object]) -> ParityResult:
        """由两个 mirror_run / run_parity_loop 输出构造双向记录并判定。

        forward_mirror / reverse_mirror 应含 {rmse, coverage, cal_err} 风格的指标 dict
        （取各自首轮或汇总指标即可）。
        """
        def _extract(m):
            if not isinstance(m, dict):
                return {}
            # 兼容 mirror_run 的 {seed_a:{rmse,...}, seed_b:{...}} 与裸 {rmse,...}
            if "seed_a" in m and isinstance(m["seed_a"], dict):
                return {k: m["seed_a"][k] for k in ("rmse", "coverage", "cal_err")
                        if k in m["seed_a"]}
            return {k: m[k] for k in ("rmse", "coverage", "cal_err") if k in m}

        return self.check({
            "forward": _extract(forward_mirror),
            "reverse": _extract(reverse_mirror),
        })
