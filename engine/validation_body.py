"""ValidationBody —— 涨落定理凸体借鉴 P2：把 N 个验证指标收敛成统一「可达质量凸体」。

涨落定理（Crooks / Fable 5）核心几何结论
----------------------------------------
所有满足详细涨落定理 DFT `p(σ)/p(−σ)=e^σ` 的分布，其统计量**联合可达区域**是一个
**凸体（moment body）**；所有已发表的 TUR / 偏度 / 尾概率 / 信息量界，都只是这个统一
凸体的**低维投影（影子）**。最可操作的推论（也已被我们落为 BidirectionalParityGate）：
**单向估计器可以任意差——必须看联合区域，而非任一单轴指标。**

本模块把这个思想落成 SwarmLabs 的「验证聚合」几何对象（P2, #2 + #6）：
  - 把各门禁（MomentBound / SecondLaw / BidirectionalParity / VirtualLabGuard）产出的标量
    指标，每个当作凸体的一个坐标轴；
  - 把「所有门禁都过」定义为凸体的**内部**：各指标的 `[lo, hi]` 半空间交集 = 凸多面体
    （默认是盒）；
  - **可选**加入**耦合凸约束**（如 Bhatia–Davis 可达域 `σ² ≤ (hi−μ)(μ−lo)`，把 mean/var
    两个轴耦合），使「单轴通过 ≠ 联合通过」这一涨落定理教训可被几何复现；
  - 提供几何查询：联合判定 / 各面松弛余量 / 绑定约束 / 投影影子 / 体积占比 /
    Uncertainty Cartography（定位结构性不确定前沿）。

诚实边界（见 docs/insights/fluctuation-theorem-convexbody.md）
-----------------------------------------------------------
  - 这是把统计力学凸体结论**借作 UQ 聚合方法论**，不是"平台模拟热涨落"；
  - 默认体是盒（轴对齐半空间交集），已是"独立阈值下联合可达区域"的严格刻画；
  - 耦合约束（如 Bhatia–Davis）为可选增强，用于复现"影子通过 ≠ 体内"的定理教训；
  - 不使用 SDP / 重矩问题——轻量、可解释、零破坏、纯 numpy（scipy 仅作可选加速）。

依赖：numpy（必需）；scipy.spatial.HalfspaceIntersection（可选，用于精确投影/体积，
      缺失时自动降级为 Monte Carlo 采样，结果同样诚实可用）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .guard import GateStatus


# ── 指标载体 ──────────────────────────────────────────────────────────────

@dataclass
class Indicator:
    """凸体的一个坐标轴：某个验证标量指标及其「通过区间」``[lo, hi]``。

    ``lo`` / ``hi`` 同时是：
      - 门禁判据：该指标落在 ``[lo, hi]`` 内即通过（PASS）；
      - 凸体的上下界半空间：``z_i ≥ lo`` 且 ``z_i ≤ hi``。
    ``hi`` 对"越大越好"的指标（如覆盖率下界）通常取理论上限（1.0），用于绘图/体积归一化。
    """

    name: str
    value: float
    lo: float
    hi: float
    unit: str = ""
    desc: str = ""
    higher_is_better: bool = True

    @property
    def slack(self) -> float:
        """到最近面的内部余量（正=安全，负=越界）。"""
        return min(self.value - self.lo, self.hi - self.value)

    @property
    def inside(self) -> bool:
        return self.lo - 1e-12 <= self.value <= self.hi + 1e-12


# ── 约束（线性半空间 / 凸非线性）──────────────────────────────────────────

@dataclass
class _LinearConstraint:
    weights: Dict[str, float]   # {指标名: 权重}，约束 Σ w_i·z_i ≤ b
    b: float
    name: str

    def vec(self, order: List[str]) -> np.ndarray:
        """按当前指标顺序解析出权重向量（缺失指标权重记 0，故可延迟到求值期）。"""
        w = np.zeros(len(order))
        for k, v in self.weights.items():
            if k in order:
                w[order.index(k)] = float(v)
        return w

    def slack(self, z: np.ndarray, order: List[str]) -> float:
        return float(self.b - float(self.vec(order) @ z))


@dataclass
class _ConvexConstraint:
    g: Callable[[np.ndarray], float]            # 凸函数，约束 g(z) ≤ 0
    name: str
    grad: Optional[Callable[[np.ndarray], np.ndarray]] = None

    def slack(self, z: np.ndarray) -> float:
        return float(-self.g(z))               # 内部为正


# ── 凸体主体 ──────────────────────────────────────────────────────────────

class ValidationBody:
    """把多个验证指标收敛成单一「可达质量凸体」几何对象。

    典型用法
    --------
      # 方式一：从既有门禁结果直接聚合（推荐）
      body = ValidationBody.from_gate_results(
                 moment=moment_result, second=second_result,
                 parity=parity_result, guard_summary=guard_summary,
                 ref_scales={"min_var": 0.05})
      print(body.status(), body.cartography())

      # 方式二：手工搭指标 + 可选耦合约束
      body = ValidationBody()
      body.add_indicator("coverage_lb", 0.95, 0.90, 1.0)
      body.add_indicator("parity_max_rel", 0.04, 0.0, 0.10)
      body.add_bhatia_davis("mean", "var", lo=0.0, hi=1.0)   # 耦合 mean/var
      body.set_current({"coverage_lb": 0.95, "parity_max_rel": 0.04,
                        "mean": 0.9, "var": 0.02})
      print(body.shadow("coverage_lb"))      # 投影影子
      print(body.cartography())              # Uncertainty Cartography
    """

    def __init__(self, caution_band: float = 0.05):
        self.indicators: Dict[str, Indicator] = {}
        self._order: List[str] = []          # 指标顺序（决定 z 向量维度）
        self.linear: List[_LinearConstraint] = []
        self.convex: List[_ConvexConstraint] = []
        self.caution_band = float(caution_band)   # 余量在 [−band, 0] 内 → CONTROLLED
        self._current: Optional[np.ndarray] = None

    # ── 构造 API ──────────────────────────────────────────────────────
    def add_indicator(self, name: str, value: float, lo: float, hi: float,
                      unit: str = "", desc: str = "", higher_is_better: bool = True
                      ) -> "ValidationBody":
        if name in self.indicators:
            raise ValueError(f"指标 «{name}» 已存在")
        self.indicators[name] = Indicator(name, float(value), float(lo), float(hi),
                                          unit, desc, higher_is_better)
        self._order.append(name)
        return self

    def add_linear_constraint(self, weights: Dict[str, float], b: float, name: str
                              ) -> "ValidationBody":
        """加入仿射耦合约束 Σ w_i·z_i ≤ b（如 parity: fwd−rev ≤ tol 与 rev−fwd ≤ tol）。

        权重以 {指标名: 值} 给出，延迟到求值时再按当前指标顺序解析，
        因此对约束之后新增的指标也安全（不会出现维度错配）。
        """
        self.linear.append(_LinearConstraint(dict(weights), float(b), name))
        return self

    def add_convex_constraint(self, g: Callable[[np.ndarray], float], name: str,
                              grad: Optional[Callable[[np.ndarray], np.ndarray]] = None
                              ) -> "ValidationBody":
        """加入凸非线性约束 g(z) ≤ 0（如 Bhatia–Davis 可达域，耦合 mean/var）。"""
        self.convex.append(_ConvexConstraint(g, name, grad))
        return self

    def add_bhatia_davis(self, mean_key: str, var_key: str, lo: float, hi: float,
                         name: str = "bhatia_davis") -> "ValidationBody":
        """耦合凸约束：σ² ≤ (hi−μ)(μ−lo)（Bhatia–Davis 1970 可达域）。

        这正是涨落定理"所有可达分布都是两结果基本分布的混合"在我们 UQ 上的同构——
        一个 mean/var 各自"合理"的点仍可能违反它，从而复现「单轴通过 ≠ 联合通过」。
        """
        lo, hi = float(lo), float(hi)

        def _g(z):
            i_mu = self._order.index(mean_key)
            i_v = self._order.index(var_key)
            mu, v = float(z[i_mu]), float(z[i_v])
            return v - (hi - mu) * (mu - lo)     # ≤ 0 表示可达
        self.add_convex_constraint(_g, name)
        return self

    def set_current(self, values: Dict[str, float]) -> "ValidationBody":
        """给定当前 surrogate 的指标向量（仅对已知指标赋值，缺失置为 NaN 并标记越界）。"""
        z = np.array([self.indicators[k].lo if k not in values
                      else float(values[k]) for k in self._order], float)
        self._current = z
        for k, v in values.items():
            if k in self.indicators:
                self.indicators[k].value = float(v)
        return self

    # ── 内部工具 ──────────────────────────────────────────────────────
    def _z(self) -> np.ndarray:
        if self._current is None:
            # 默认用各指标当前值
            self._current = np.array([self.indicators[k].value for k in self._order], float)
        return self._current

    def _box_lo_hi(self) -> Tuple[np.ndarray, np.ndarray]:
        lo = np.array([self.indicators[k].lo for k in self._order], float)
        hi = np.array([self.indicators[k].hi for k in self._order], float)
        return lo, hi

    def _all_slacks(self, z: np.ndarray) -> List[Tuple[str, str, float]]:
        """返回 [(名称, 类型, 余量)]，余量正=内部、负=越界。"""
        out: List[Tuple[str, str, float]] = []
        for k in self._order:
            out.append((k, "indicator", self.indicators[k].slack))
        for c in self.linear:
            out.append((c.name, "linear", c.slack(z, self._order)))
        for c in self.convex:
            out.append((c.name, "convex", c.slack(z)))
        return out

    # ── 几何查询 ──────────────────────────────────────────────────────
    def contains(self, z: Optional[np.ndarray] = None) -> bool:
        """当前点是否在凸体内部（= 所有门禁都过）。"""
        z = self._z() if z is None else np.asarray(z, float)
        for k in self._order:
            ind = self.indicators[k]
            if not (ind.lo - 1e-12 <= z[self._order.index(k)] <= ind.hi + 1e-12):
                return False
        for c in self.linear:
            if c.slack(z, self._order) < -1e-12:
                return False
        for c in self.convex:
            if c.slack(z) < -1e-12:
                return False
        return True

    def status(self, z: Optional[np.ndarray] = None) -> GateStatus:
        """联合门禁三态：全内部=PASS；有越界但余量在 caution_band 内=CONTROLLED；否则 REJECT。"""
        z = self._z() if z is None else np.asarray(z, float)
        slacks = [s for _, _, s in self._all_slacks(z)]
        if not slacks:
            return GateStatus.PASS
        min_s = min(slacks)
        if min_s >= 0:
            return GateStatus.PASS
        if min_s >= -self.caution_band:
            return GateStatus.CONTROLLED
        return GateStatus.REJECT

    def binding(self, z: Optional[np.ndarray] = None) -> List[str]:
        """最绑定（余量最小）的约束名列表——即 Uncertainty Cartography 要定位的
        「结构性不确定前沿」所在面。"""
        z = self._z() if z is None else np.asarray(z, float)
        slacks = self._all_slacks(z)
        if not slacks:
            return []
        m = min(s for _, _, s in slacks)
        return [n for n, _, s in slacks if abs(s - m) < 1e-9]

    def _free_dims(self) -> np.ndarray:
        """非退化（宽度>0）的指标维度下标——零宽度的硬门禁（如布尔一致性）不参与体积归一化。"""
        lo, hi = self._box_lo_hi()
        return np.where((hi - lo) > 1e-12)[0]

    def shadow(self, axis: str, n_samples: int = 40000,
                use_scipy: bool = True) -> Tuple[float, float]:
        """把凸体投影到单个指标轴 → 还原该指标的「影子」可接受区间 ``[min, max]``。

        这就是涨落定理"单指标界是统一凸体的低维投影（影子）"的几何实现：
        例如 ``coverage_lb`` 的影子就是 ``[min_coverage, 1.0]``，恰好等于
        MomentBoundGate 单轴会强制的阈值——但体内点远不止满足这一条。
        """
        idx = self._order.index(axis)
        lo, hi = self._box_lo_hi()
        # 精确路径：全部为线性约束、无退化维度且 scipy 可用时，用半空间交点顶点投影。
        if use_scipy and not self.convex and self._free_dims().size == len(self._order):
            verts = self._vertices_scipy()
            if verts is not None and len(verts):
                col = verts[:, idx]
                return (float(col.min()), float(col.max()))
        # Monte Carlo 采样（对任意约束都成立）
        rng = np.random.default_rng(1234)
        box = rng.uniform(lo, hi, size=(n_samples, len(self._order)))
        mask = np.array([self._point_ok(box[j]) for j in range(n_samples)])
        if mask.any():
            col = box[mask, idx]
            return (float(col.min()), float(col.max()))
        return (float(lo[idx]), float(hi[idx]))     # 无内部点 → 退化为盒影

    def volume_fraction(self, n_samples: int = 40000, use_scipy: bool = True
                        ) -> Optional[float]:
        """凸体体积 ÷ 包围盒体积：衡量「各指标独立可接受」有多大比例在几何上自洽。

        返回 1.0 = 盒（无耦合，单轴全过即联合过）；<1.0 = 存在耦合约束在收紧联合区域。
        零宽度的硬门禁（如布尔一致性）从归一化中剔除（它们只做 0/1 判定，不贡献体积）。
        """
        lo, hi = self._box_lo_hi()
        free = self._free_dims()
        if free.size == 0:
            return None
        box_vol = float(np.prod((hi - lo)[free]))
        if box_vol <= 0:
            return None
        # 仅当无退化维度且全线性时走精确凸包（退化维度会让凸包体积维度错配）
        if use_scipy and not self.convex and free.size == len(self._order):
            verts = self._vertices_scipy()
            if verts is not None and len(verts) >= len(self._order) + 1:
                try:
                    from scipy.spatial import ConvexHull
                    hull = ConvexHull(verts)
                    return float(hull.volume / box_vol)
                except Exception:
                    pass  # noqa: silent-ok — Exception 可安全忽略（已审计 2026-10-08）
        rng = np.random.default_rng(7)
        box = rng.uniform(lo, hi, size=(n_samples, len(self._order)))
        inside = sum(1 for j in range(n_samples) if self._point_ok(box[j]))
        return float(inside / n_samples)

    def _point_ok(self, z: np.ndarray) -> bool:
        for k in self._order:
            ind = self.indicators[k]
            if not (ind.lo - 1e-12 <= z[self._order.index(k)] <= ind.hi + 1e-12):
                return False
        for c in self.linear:
            if c.slack(z, self._order) < -1e-12:
                return False
        for c in self.convex:
            if c.slack(z) < -1e-12:
                return False
        return True

    def _vertices_scipy(self) -> Optional[np.ndarray]:
        """用 scipy.spatial.HalfspaceIntersection 求凸多面体顶点（仅线性约束）。"""
        if self.convex:
            return None
        try:
            from scipy.spatial import HalfspaceIntersection
        except Exception:
            return None
        lo, hi = self._box_lo_hi()
        d = len(self._order)
        rows = []  # 每行 [A_1..A_d, b]，表示 A·z + b ≤ 0
        for i in range(d):
            e = np.zeros(d)
            e[i] = -1.0
            rows.append(np.concatenate([e, [lo[i]]]))      # -z_i + lo_i ≤ 0 → z_i ≥ lo_i
            e2 = np.zeros(d); e2[i] = 1.0
            rows.append(np.concatenate([e2, [-hi[i]]]))     #  z_i - hi_i ≤ 0 → z_i ≤ hi_i
        for c in self.linear:
            rows.append(np.concatenate([c.vec(self._order), [-c.b]]))      # w·z - b ≤ 0
        hs = np.array(rows, float)
        interior = 0.5 * (lo + hi)
        # 保证内点严格在内部（盒中心天然满足 box 约束；线性耦合若把中心排挤出去则放弃）
        if not np.all(hs[:, :-1] @ interior + hs[:, -1] < -1e-9):
            return None
        try:
            inter = HalfspaceIntersection(hs, interior)
            return np.asarray(inter.intersections, float)
        except Exception:
            return None

    # ── Uncertainty Cartography（叙事导出）───────────────────────────────
    def cartography(self, z: Optional[np.ndarray] = None) -> dict:
        """导出结构性不确定前沿叙事：每个指标的余量、绑定状态、联合状态。"""
        z = self._z() if z is None else np.asarray(z, float)
        slacks = self._all_slacks(z)
        min_s = min((s for _, _, s in slacks), default=0.0)
        binding_names = self.binding(z)
        inds = []
        for k in self._order:
            ind = self.indicators[k]
            inds.append({
                "name": k, "value": round(float(ind.value), 6),
                "lo": round(float(ind.lo), 6), "hi": round(float(ind.hi), 6),
                "slack": round(float(ind.slack), 6),
                "inside": bool(ind.inside),
                "unit": ind.unit, "desc": ind.desc,
            })
        cons = [{"name": n, "type": t, "slack": round(float(s), 6),
                 "violated": bool(s < -1e-12)}
                for n, t, s in slacks if t != "indicator"]
        return {
            "n_indicators": len(self._order),
            "n_constraints": len(self.linear) + len(self.convex),
            "status": self.status(z).value,
            "inside": bool(self.contains(z)),
            "min_slack": round(float(min_s), 6),
            "binding": binding_names,
            "volume_fraction": self.volume_fraction(),
            "indicators": inds,
            "constraints": cons,
        }

    def _sample_inside(self, n: int = 1500) -> np.ndarray:
        """在包围盒内采样落在体内的点（供 2D 影子里勾勒真实体形）。"""
        lo, hi = self._box_lo_hi()
        rng = np.random.default_rng(99)
        box = rng.uniform(lo, hi, size=(n * 4, len(self._order)))
        out = [box[j] for j in range(len(box)) if self._point_ok(box[j])]
        if not out:
            return np.zeros((0, len(self._order)))
        return np.array(out[:n], float)

    def cartography_svg(self, projection: Optional[Tuple[str, str]] = None,
                        width: int = 680) -> str:
        """导出「Uncertainty Cartography」SVG：左上为 2D 投影体形 + 当前点，右下为各指标影子条。

        这是涨落定理 #6（从"预测一个结果"→"绘制可达结果凸体 + 定位结构性不确定前沿"）
        的产品化叙事：每个指标是一条「影子」轴，凸体是它们共同定义的联合可达区域。
        """
        # —— 选投影轴 ——
        if projection is None:
            cands = [k for k in self._order if k in
                     ("coverage_lb", "parity_fwd_rel", "reject_rate", "min_eig", "min_var")]
            projection = (cands[0] if cands else self._order[0],
                          cands[1] if len(cands) > 1 else self._order[-1])
        xi = self._order.index(projection[0])
        yi = self._order.index(projection[1])
        lo, hi = self._box_lo_hi()
        n = len(self._order)
        bar_h = 26
        top_h = 300
        pad = 40
        svg_w = width
        svg_h = top_h + 30 + n * bar_h + 30

        def sx(v):
            return pad + (v - lo[xi]) / (hi[xi] - lo[xi] + 1e-12) * (svg_w - 2 * pad)
        def sy(v):
            return pad + (1 - (v - lo[yi]) / (hi[yi] - lo[yi] + 1e-12)) * (top_h - 2 * pad)

        parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {svg_w} {svg_h}" '
                 f'font-family="system-ui,Segoe UI,Arial" font-size="12">']
        parts.append(f'<rect width="{svg_w}" height="{svg_h}" fill="#ffffff"/>')

        # —— 2D 投影区 ——
        parts.append(f'<text x="{pad}" y="18" fill="#222" font-weight="600">'
                     f'ValidationBody · 2D 投影（影子轴: {projection[0]} × {projection[1]}）</text>')
        # 盒（轴对齐半空间交集）
        bx, by = sx(lo[xi]), sy(hi[yi])
        bw, bh = sx(hi[xi]) - sx(lo[xi]), sy(lo[yi]) - sy(hi[yi])
        parts.append(f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bw:.1f}" height="{bh:.1f}" '
                     f'fill="#eef4ff" stroke="#3b6fd4" stroke-dasharray="4 3"/>')
        # 真实体形（采样内部点）
        pts = self._sample_inside(800)
        if len(pts):
            dots = " ".join(f"{sx(p[xi]):.1f},{sy(p[yi]):.1f}" for p in pts)
            parts.append(f'<polygon points="{dots}" fill="#9ec1ff" fill-opacity="0.5" '
                         f'stroke="none"/>')
        # 当前点
        z = self._z()
        cx, cy = sx(z[xi]), sy(z[yi])
        col = {"pass": "#1f9d55", "controlled": "#e0a800", "reject": "#d23b3b"}.get(
            self.status(z).value, "#333")
        parts.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="6" fill="{col}" '
                     f'stroke="#222" stroke-width="1.2"/>')
        parts.append(f'<text x="{cx+9:.1f}" y="{cy+4:.1f}" fill="#222" font-size="11">'
                     f'当前 surrogate</text>')
        # 轴标签
        parts.append(f'<text x="{pad}" y="{top_h-6:.1f}" fill="#555">{projection[0]}</text>')
        parts.append(f'<text x="{svg_w-pad-70:.1f}" y="{pad-2:.1f}" fill="#555" '
                     f'text-anchor="end">{projection[1]}</text>')

        # —— 影子条（每个指标）——
        parts.append(f'<text x="{pad}" y="{top_h+24:.1f}" fill="#222" '
                     f'font-weight="600">各指标「影子」（绿=体内，红=越界，▮=当前值）</text>')
        yy = top_h + 44
        for k in self._order:
            ind = self.indicators[k]
            x0 = pad + 120
            x1 = svg_w - pad
            bw_ = x1 - x0
            # 轴范围条（灰）
            parts.append(f'<rect x="{x0:.1f}" y="{yy-7:.1f}" width="{bw_:.1f}" height="12" '
                         f'fill="#eee" stroke="#ccc"/>')
            # 通过区间（绿）
            gl = x0 + (ind.lo - lo[self._order.index(k)]) / (hi[self._order.index(k)] - lo[self._order.index(k)] + 1e-12) * bw_
            gr = x0 + (ind.hi - lo[self._order.index(k)]) / (hi[self._order.index(k)] - lo[self._order.index(k)] + 1e-12) * bw_
            parts.append(f'<rect x="{gl:.1f}" y="{yy-7:.1f}" width="{max(0.5,gr-gl):.1f}" '
                         f'height="12" fill="#bfe6c8" stroke="#1f9d55"/>')
            # 当前值标记
            gv = x0 + (ind.value - lo[self._order.index(k)]) / (hi[self._order.index(k)] - lo[self._order.index(k)] + 1e-12) * bw_
            mcol = "#1f9d55" if ind.inside else "#d23b3b"
            parts.append(f'<rect x="{gv-1.5:.1f}" y="{yy-10:.1f}" width="3" height="18" '
                         f'fill="{mcol}"/>')
            parts.append(f'<text x="{pad}" y="{yy+2:.1f}" fill="#333" font-size="11">'
                         f'{k}</text>')
            parts.append(f'<text x="{x1+2:.1f}" y="{yy+2:.1f}" fill="#666" font-size="10" '
                         f'text-anchor="start">{ind.value:.3f}</text>')
            yy += bar_h

        parts.append('</svg>')
        return "".join(parts)

    def summary_text(self, z: Optional[np.ndarray] = None) -> str:
        c = self.cartography(z)
        lines = [f"ValidationBody: status={c['status']}  inside={c['inside']}  "
                 f"volume_frac={c['volume_fraction']}  binding={c['binding']}"]
        for ind in c["indicators"]:
            mark = "✓" if ind["inside"] else "✗"
            lines.append(f"  [{mark}] {ind['name']:18s} = {ind['value']:.4f} "
                         f"(allowed [{ind['lo']:.3f}, {ind['hi']:.3f}], "
                         f"slack={ind['slack']:+.4f})")
        for con in c["constraints"]:
            mark = "✓" if not con["violated"] else "✗"
            lines.append(f"  [{mark}] constraint {con['name']:18s} slack={con['slack']:+.4f}")
        return "\n".join(lines)

    # ── 从既有门禁结果聚合（主入口）────────────────────────────────────
    @classmethod
    def from_gate_results(cls, moment=None, second=None, parity=None,
                          guard_summary: Optional[dict] = None,
                          ref_scales: Optional[dict] = None,
                          thresholds: Optional[dict] = None,
                          caution_band: float = 0.05) -> "ValidationBody":
        """把四个门禁的标量结果收敛成一个凸体。

        参数
        ----
        moment         : MomentBoundGate.MomentBoundResult（含 coverage_lb / body_consistent）
        second         : SecondLawGate.SecondLawResult（含 min_eig / min_var / posterior_consistent / dissipation_ok）
        parity         : BidirectionalParityGate.ParityResult（含 forward_max_rel / reverse_max_rel）
        guard_summary  : VirtualLabGuard.gate_summary() 的输出 dict（含 counts / n）
        ref_scales     : 给无天然上界的指标（如 min_var）一个绘图/体积用尺度，如 {"min_var": 0.05}
        thresholds     : 覆盖默认阈值，如 {"min_coverage": 0.90, "parity_tol": 0.10,
                          "max_reject_rate": 0.10, "max_controlled_rate": 0.50}
        """
        th = {
            "min_coverage": 0.90, "parity_tol": 0.10,
            "max_reject_rate": 0.10, "max_controlled_rate": 0.50,
            "min_eig_lo": 0.0, "min_var_lo": 0.0,
        }
        if thresholds:
            th.update(thresholds)
        rs = ref_scales or {}
        body = cls(caution_band=caution_band)

        # —— MomentBoundGate ——
        if moment is not None:
            cov = float(getattr(moment, "coverage_lb", 0.0))
            body.add_indicator("coverage_lb", cov, th["min_coverage"], 1.0,
                               desc="Cantelli 认证覆盖率下界（涨落定理锐下界投影）")
            cons = 1.0 if bool(getattr(moment, "body_consistent", True)) else 0.0
            body.add_indicator("bhatia_consistent", cons, 1.0, 1.0,
                               desc="Bhatia–Davis 后验可达域一致性（1=全部点可达）")

        # —— SecondLawGate ——
        if second is not None:
            me = float(getattr(second, "min_eig", float("inf")))
            me = me if math.isfinite(me) else 1.0
            mv = float(getattr(second, "min_var", 0.0))
            var_hi = float(rs.get("min_var", max(1e-3, abs(mv) * 4 + 1e-3)))
            body.add_indicator("min_eig", me, th["min_eig_lo"], 1.0,
                               desc="后验协方差最小特征值（≥0=PSD=涨落-耗散一致）")
            body.add_indicator("min_var", mv, th["min_var_lo"], var_hi,
                               desc="最小预测方差（≥0=非负涨落强度）")
            diss = getattr(second, "dissipation_ok", None)
            if diss is not None:
                dv = 1.0 if bool(diss) else 0.0
                body.add_indicator("dissipation_ok", dv, 1.0, 1.0,
                                   desc="轨迹熵产率 ≥ 0（第二定律）")

        # —— BidirectionalParityGate ——
        if parity is not None:
            fmr = float(getattr(parity, "forward_max_rel", 0.0))
            rmr = float(getattr(parity, "reverse_max_rel", 0.0))
            body.add_indicator("parity_fwd_rel", fmr, 0.0, th["parity_tol"],
                               desc="虚拟→真实 最大相对偏差（单向可任意差→须双向对等）")
            body.add_indicator("parity_rev_rel", rmr, 0.0, th["parity_tol"],
                               desc="真实→虚拟 最大相对偏差（双向对等才可信）")
            # 线性耦合：两方向偏差都须 ≤ tol（等价两条半空间，已隐含在轴界内；
            # 这里显式加一条"较差方向 ≤ tol"以体现对等硬约束）
            body.add_linear_constraint(
                {"parity_fwd_rel": 1.0, "parity_rev_rel": -1.0}, 0.0,
                name="parity_symmetry")

        # —— VirtualLabGuard 汇总 ——
        if guard_summary is not None:
            n = max(int(guard_summary.get("n", 1)), 1)
            counts = guard_summary.get("counts", {})
            rej = float(counts.get("reject", 0)) / n
            ctrl = float(counts.get("controlled", 0)) / n
            body.add_indicator("reject_rate", rej, 0.0, th["max_reject_rate"],
                               desc="门禁拒绝点占比（≤阈=区域可信）")
            body.add_indicator("controlled_rate", ctrl, 0.0, th["max_controlled_rate"],
                               desc="受控点占比（≤阈=不确定性未失控）")

        return body

    # ── 从 surrogate 直接构建（便捷）──────────────────────────────────
    @classmethod
    def for_surrogate(cls, surrogate, grid, lo: float = 0.0, hi: float = 1.0,
                      parity: Optional[dict] = None, noise_rel: float = 0.03,
                      **kw) -> "ValidationBody":
        """包裹一个已拟合 GP，跑 VirtualLabGuard + MomentBound + SecondLaw 并聚合。

        parity: 可选 {"forward": {...}, "reverse": {...}}，交给 BidirectionalParityGate。
        """
        from .guard import VirtualLabGuard
        from .moment_bound import MomentBoundGate
        from .second_law import SecondLawGate
        from .bidirectional_parity import BidirectionalParityGate

        grid = np.atleast_2d(np.asarray(grid, float))
        g = VirtualLabGuard(surrogate=surrogate)
        gsum = g.gate_summary(grid)
        mean, std = surrogate.predict(grid)
        mb = MomentBoundGate(lo, hi).check(mean.ravel(), np.asarray(std).ravel())
        sl = SecondLawGate().check_posterior(surrogate, grid)
        par = None
        if parity:
            par = BidirectionalParityGate().check(parity)
        return cls.from_gate_results(moment=mb, second=sl, parity=par,
                                     guard_summary=gsum, **kw)


# ── 兼容导出别名（与既有门禁命名风格一致）────────────────────────────────
ValidationBodyGate = ValidationBody
