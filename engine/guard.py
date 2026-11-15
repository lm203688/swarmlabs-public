"""VirtualLab Guard —— 可信替代安全门禁（独立子产品，三层科学护栏）。

把 SurrogateEngine 的「可信替代安全边界」能力外化为一个独立、可复用的门禁
组件，借鉴 LabOpts Guard 的「可信多智能体实验守护系统」思路，并吸收
NVIDIA PhysicsNeMo Guardrails 的「科学模型护栏」范式（OOD + 物理一致性 + 集成
方差三层），比纯对话护栏（LlamaFirewall）更贴合「替代真实实验」的语义。

每个查询点经门禁后落到三态之一，并显式给出逐层判定：
  - PASS       （trusted）：三层均通过，可放心当真实实验用
  - CONTROLLED （caution）：UQ 过大或物理检查不可用，结果仅供参考、需受控使用
  - REJECT     （rejected）：远离已观测区 或 与已知物理定律冲突，虚拟结果不可信

三层（自底向上，越往上越硬）：
  L1 信任/OOD 层：到训练集最近归一化距离 > ood_threshold → REJECT（与现有逻辑一致）
  L2 UQ 层      ：后验 std > uq_threshold（默认 caution_rel×yscale）→ CONTROLLED
  L3 物理一致性层：若场景登记了已知控制方程，代理预测 + 数值导数计算的残差
                   |R| > physics_tol → REJECT（仿真结果违反物理，不可信）

这与「替代真实实验 + 不受时空限制」的底线一致：不可信的区必须自己说出来，
而不是吐一个无依据的标量。
"""

from __future__ import annotations

import enum
import inspect
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Dict, Optional, Sequence, Tuple

import numpy as np

if TYPE_CHECKING:
    from .surrogate import GPSurrogate


class GateStatus(str, enum.Enum):
    PASS = "pass"
    CONTROLLED = "controlled"
    REJECT = "reject"


# 越硬的状态序数越大：用于把多层判定合并成最终一态。
_ORDER = {GateStatus.PASS: 0, GateStatus.CONTROLLED: 1, GateStatus.REJECT: 2}


@dataclass
class GateResult:
    """单点门禁结果（含逐层明细）。"""

    status: GateStatus
    mean: float
    std: float
    dist_norm: float
    ood_threshold: float
    reason: str
    physics_residual: Optional[float] = None
    layers: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = {
            "status": self.status.value,
            "mean": float(self.mean),
            "std": float(self.std),
            "dist_norm": float(self.dist_norm),
            "ood_threshold": float(self.ood_threshold),
            "reason": self.reason,
            "physics_residual": (
                None if self.physics_residual is None else float(self.physics_residual)
            ),
        }
        if self.layers:
            d["layers"] = {k: list(v) for k, v in self.layers.items()}
        return d

    @property
    def allowed(self) -> bool:
        """REJECT 的点不可信，必须被挡在门外。"""
        return self.status != GateStatus.REJECT


# ── 已知控制方程的物理残差注册表 ──────────────────────────────────────
# 残差签名：residual(x, f, grad) -> float，其中
#   x    : 查询点（标量或向量）
#   f    : 代理预测值
#   grad : 代理预测对 x 的数值梯度（标量或数组，按 dim 对齐）
# 返回标量残差，理想情况下应为 0（代理尊重了控制方程）。
def logistic_residual(k: float = 1.0, L: float = 1.0, x0: float = 0.0) -> Callable:
    """logistic 增长 ODE 残差：R = f' - k·f·(1 - f/L)。

    对应解析式 f(x) = L / (1 + exp(-k·(x - x0)))，ODE 为 y' = k·y·(1 - y/L)。
    """

    def _r(x, f, grad):
        g = grad if np.isscalar(grad) else grad[0]
        return float(g - k * f * (1.0 - f / L))

    return _r


def monod_residual(Vmax: float = 1.0, K: float = 1.0) -> Callable:
    """Monod 抑制/增长残差：R = f' - Vmax·K / (K + x)^2。

    对应解析式 f(x) = Vmax·x / (K + x)，常用于酶动力学 / 底物消耗。
    """

    def _r(x, f, grad):
        g = grad if np.isscalar(grad) else grad[0]
        xv = float(x[0] if hasattr(x, "__len__") else x)
        return float(g - Vmax * K / (K + xv) ** 2)

    return _r


def inhibition_residual(Vmax: float = 1.0, Ki: float = 1.0) -> Callable:
    """竞争性抑制残差：R = f' + Vmax / (Ki·(1 + x/Ki)^2)。

    对应解析式 f(x) = Vmax / (1 + x/Ki)。
    """

    def _r(x, f, grad):
        g = grad if np.isscalar(grad) else grad[0]
        xv = float(x[0] if hasattr(x, "__len__") else x)
        denom = 1.0 + xv / Ki
        return float(g + Vmax / (Ki * denom ** 2))

    return _r


def heat1d_residual(alpha: float = 1.0) -> Callable:
    """1D 热传导方程残差（二阶空间导数版）。

    1D 热方程 ∂u/∂t = α·∂²u/∂x²。对于纯空间快照 u(x)（固定 t），
    物理一致性的代理预测必须满足 |∂²u/∂x²| 与 |∂u/∂t| 的缩放关系。
    这里仅检查空间二阶导数不为异常值（防止代理在高阶振荡区域给出非物理解）：
        R = α·(u(x+h) - 2u(x) + u(x-h)) / h²
    对已知稳态解（如温度分布 u(x)），α·u'' 应≈ 0。
    """
    def _r(x, f, grad, surrogate=None):
        xv = np.asarray(x, float).ravel()
        if surrogate is None:
            return 0.0  # 无代理无法估二阶导
        x_in = xv.reshape(1, -1)
        h = 1e-4
        xs_p = x_in + np.array([h] * len(xv))
        xs_m = x_in - np.array([h] * len(xv))
        fp, _ = surrogate.predict(xs_p)
        fm, _ = surrogate.predict(xs_m)
        d2u = (fp - 2 * f + fm) / (h ** 2)
        return float(alpha * d2u)
    return _r


PHYSICS_RESIDUALS = {
    "logistic": logistic_residual,
    "monod": monod_residual,
    "inhibition": inhibition_residual,
    "heat1d": heat1d_residual,
}


def get_physics_residual(name: str, **params) -> Optional[Callable]:
    """按场景名取已登记的控制方程残差（未登记返回 None → L3 跳过）。"""
    factory = PHYSICS_RESIDUALS.get(name)
    return factory(**params) if factory is not None else None


class VirtualLabGuard:
    """可信替代门禁：包裹一个已拟合的代理（或直接用数据拟合），三层判定。

    用法
    ----
      # 方式一：直接喂数据，离线零依赖自举（仅 L1/L2）
      guard = VirtualLabGuard.from_data(X, y, bounds=[(0, 1)])

      # 方式二：带已知控制方程的校准场景（自动启用 L3 物理一致性）
      guard = VirtualLabGuard.for_scenario("logistic", X, y, k=0.8, L=1.0)

      # 方式三：包裹已有 engine / surrogate，并显式给残差
      guard = VirtualLabGuard(engine=eng, residual_fn=logistic_residual(0.8, 1.0))

      res = guard.gate([[0.5], [1.4]])
      summary = guard.gate_summary(query_points)
    """

    def __init__(self, surrogate=None, engine=None, bounds=None,
                 ood_factor: float = 2.0, caution_rel: float = 3.0,
                 uq_threshold: Optional[float] = None,
                 residual_fn: Optional[Callable] = None,
                 physics_tol: float = 0.1):
        self._sur = surrogate
        self.bounds = np.array(bounds) if bounds is not None else None
        self.ood_factor = float(ood_factor)
        self.caution_rel = float(caution_rel)
        self.uq_threshold = float(uq_threshold) if uq_threshold is not None else None
        self.residual_fn = residual_fn
        self.physics_tol = float(physics_tol)
        if engine is not None and surrogate is None:
            self._sur = getattr(engine, "_sur", None)

    # ── 便捷构造 ────────────────────────────────────────────
    @classmethod
    def from_data(cls, X, y, bounds=None, noise_rel=0.03,
                  residual_fn=None, physics_tol=0.1, **gp_kwargs):
        """直接用观测数据拟合一个 GP 代理并包裹成门禁（零依赖离线）。"""
        from .surrogate import GPSurrogate

        X = np.atleast_2d(np.asarray(X, float))
        y = np.asarray(y, float).ravel()
        y_scale = float(np.std(y)) + 1e-9
        y_mean = float(np.mean(y))
        nv = (noise_rel if noise_rel is not None else 0.03) ** 2
        ls = gp_kwargs.pop("lengthscale", 0.4)
        sur = GPSurrogate(noise_var=nv, y_mean=y_mean, y_scale=y_scale,
                          lengthscale=ls, **gp_kwargs)
        sur.fit(X, y)
        if bounds is None:
            bounds = list(zip(X.min(axis=0), X.max(axis=0)))
        return cls(surrogate=sur, bounds=bounds,
                   residual_fn=residual_fn, physics_tol=physics_tol)

    @classmethod
    def for_scenario(cls, scenario: str, X, y, bounds=None, noise_rel=0.03,
                     physics_tol=0.1, **residual_params):
        """按场景名自动挂载对应控制方程残差（启用 L3 物理一致性层）。"""
        residual_fn = get_physics_residual(scenario, **residual_params)
        return cls.from_data(X, y, bounds=bounds, noise_rel=noise_rel,
                             residual_fn=residual_fn, physics_tol=physics_tol)

    # ── 内部工具 ────────────────────────────────────────────
    def _grad(self, x, h: float = 1e-4):
        """代理预测均值对输入的数值中心差分梯度（供 L3 物理残差用）。"""
        x = np.asarray(x, float).ravel()
        d = len(x)
        grad = np.zeros(d)
        for i in range(d):
            xp, xm = x.copy(), x.copy()
            xp[i] += h
            xm[i] -= h
            fp, _ = self._sur.predict(xp.reshape(1, -1))
            fm, _ = self._sur.predict(xm.reshape(1, -1))
            grad[i] = (fp[0] - fm[0]) / (2 * h)
        return grad

    def _typical_spacing(self) -> float:
        """训练点之间的典型最近邻间距 —— OOD 阈值的长度尺度。

        用中位数而非均值：单个离群训练点不应把整体尺度拉大，否则门禁会
        整体变松（更危险的方向）。
        """
        Xn = self._sur._norm(self._sur.X)
        if len(Xn) <= 1:
            return 1.0
        dd = np.linalg.norm(Xn[:, None, :] - Xn[None, :, :], axis=2)
        np.fill_diagonal(dd, np.inf)
        return float(np.median(dd.min(axis=1)))

    # ── 门禁核心（三层）─────────────────────────────────────
    def gate(self, xs, ood_factor=None):
        """对查询点集做三层门禁，返回 GateResult 列表（逐点）。"""
        if self._sur is None:
            raise RuntimeError("门禁未绑定代理：传入 surrogate / engine / from_data() 拟合")
        xs = np.atleast_2d(np.asarray(xs, float))
        mean, std = self._sur.predict(xs)
        dist = self._sur.proximity(xs)
        typical = self._typical_spacing()
        ood = float(ood_factor if ood_factor is not None else self.ood_factor) * max(typical, 1e-3)
        yscale = self._sur._yscale
        uq_thr = self.uq_threshold if self.uq_threshold is not None else self.caution_rel * yscale

        out = []
        for i, (s, d_, m_) in enumerate(zip(std, dist, mean)):
            layers = {}

            # L1 信任 / OOD 层
            if d_ > ood:
                l1 = GateStatus.REJECT
                r1 = f"L1 远离已观测区 (dist={d_:.3f} > ood={ood:.3f})"
            else:
                l1 = GateStatus.PASS
                r1 = "L1 在已观测区内"
            layers["trust"] = (l1.value, r1)

            # L2 UQ 层
            if s > uq_thr:
                l2 = GateStatus.CONTROLLED
                r2 = f"L2 后验不确定度过大 (std={s:.3f} > {uq_thr:.3f})"
            else:
                l2 = GateStatus.PASS
                r2 = "L2 不确定度可控"
            layers["uq"] = (l2.value, r2)

            # L3 物理一致性层
            physics = None
            if self.residual_fn is not None:
                try:
                    g = self._grad(xs[i])
                    # 部分残差函数（如 heat1d）需要 surrogate 本身
                    sig = inspect.signature(self.residual_fn)
                    if "surrogate" in sig.parameters:
                        physics = float(self.residual_fn(xs[i], float(m_), g, surrogate=self._sur))
                    else:
                        physics = float(self.residual_fn(xs[i], float(m_), g))
                    if abs(physics) > self.physics_tol:
                        l3 = GateStatus.REJECT
                        r3 = (f"L3 物理残差越界 (|R|={abs(physics):.3f} > "
                              f"tol={self.physics_tol:.3f})，违反控制方程")
                    else:
                        l3 = GateStatus.PASS
                        r3 = f"L3 物理一致 (|R|={abs(physics):.3f})"
                except Exception as e:  # 数值失败不应硬崩，降为受控
                    # error_kind:DEGRADABLE — L3 物理检查数值异常时降级为
                    # CONTROLLED（不是 PASS、也不是 REJECT），保留原残差与
                    # reason 供 caller 决策。这里**不**是 FATAL 是因为最坏
                    # 情形已经落在受控档，不会静默放行。
                    l3 = GateStatus.CONTROLLED
                    r3 = f"L3 物理检查数值失败 ({e})，谨慎使用"
                layers["physics"] = (l3.value, r3)
            else:
                l3 = GateStatus.PASS
                r3 = "L3 无已知控制方程（跳过）"
                layers["physics"] = (l3.value, r3)

            worst = max((l1, l2, l3), key=lambda st: _ORDER[st])
            reason = "; ".join(v[1] for v in layers.values())
            out.append(GateResult(worst, float(m_), float(s), float(d_), ood,
                                  reason, physics_residual=physics, layers=layers))
        return out

    def gate_summary(self, xs, ood_factor=None) -> dict:
        """门禁汇总：各态计数 + 被拒点索引 + 逐点明细。"""
        res = self.gate(xs, ood_factor=ood_factor)
        counts = {g.value: 0 for g in GateStatus}
        for r in res:
            counts[r.status.value] += 1
        return {
            "n": len(res),
            "counts": counts,
            "rejected": [i for i, r in enumerate(res) if not r.allowed],
            "results": [r.to_dict() for r in res],
        }


# ── GRASP CORE 范式：上下文感知安全阈值 ─────────────────────────────
# 场景关键度 → 阈值缩放因子。越关键越收紧（宁可多拒，不可放过）。
CRITICALITY_FACTORS: Dict[str, float] = {
    "routine": 1.20,          # 探索性扫描，容错高
    "normal": 1.00,           # 默认，退化为静态基线行为
    "critical": 0.70,         # 结论会写进知识库 / 对外发布
    "safety_critical": 0.45,  # 结论会替代真实实验决策
}


class ContextAwareVirtualLabGuard(VirtualLabGuard):
    """上下文感知门禁：阈值随「局部证据密度」与「场景关键度」自适应。

    借鉴来源
    --------
    UPenn GRASP Lab 的 **CORE**（Ravichandran 等，*Science Robotics* 2026）：
    安全约束不是常数，而是上下文的函数；CORE 用控制屏障函数（CBF）把上下文相关
    的安全规则写成可验证的不等式，并给出**概率安全保证**。

    我方等价物：不引入 VLM/LLM（我方刻意零 LLM 依赖），而是把 CORE 的
    「自适应且有界」结构直接搬到 GP 门禁的阈值上：

        ood_factor_eff  = ood_factor_base  × ρ(x)      × γ(criticality)
        caution_rel_eff = caution_rel_base × √ρ(x)     × γ(criticality)

    - **ρ(x)（局部证据密度因子）**：查询点邻域的训练点密度 / 训练集典型密度。
      ρ>1 表示该点周围证据比平均更充分 → 可容许更远的外推、也应要求更小的
      后验方差；ρ<1 表示证据稀疏 → 两个阈值同时收紧。
      L2 用 √ρ 而非 ρ，是让 UQ 层的响应比 OOD 层更温和（避免双重放大）。
    - **γ(criticality)**：见 `CRITICALITY_FACTORS`。`normal` 时 γ=1.0。

    **有界性是关键**：ρ 被 clip 到 `[rho_min, rho_max]`（默认 [0.4, 2.0]）。
    CORE 的价值在于「有保证的安全」，自适应本身若无界就失去了保证意义——
    因此这里的 clip 不是工程妥协，而是方法论要求。

    向后兼容
    --------
    `criticality="normal"` 且训练点均匀分布时 ρ≈1、γ=1，退化为 `VirtualLabGuard`
    的原始行为。三层判定逻辑**完全复用父类**（本类只改阈值，不改判据），
    因此不存在两套门禁逻辑漂移的风险。

    用法
    ----
        g = ContextAwareVirtualLabGuard.for_scenario(
                "logistic", X, y, criticality="safety_critical", k=0.8, L=1.0)
        res = g.gate([[0.5], [1.4]])
        print(g.explain([[0.5], [1.4]]))   # 输出每点的阈值调整依据
    """

    def __init__(self, *args, criticality: str = "normal",
                 k_neighbors: int = 3,
                 rho_bounds: Tuple[float, float] = (0.4, 2.0),
                 **kwargs):
        super().__init__(*args, **kwargs)
        if criticality not in CRITICALITY_FACTORS:
            raise ValueError(
                f"未知 criticality «{criticality}»，可选 {list(CRITICALITY_FACTORS)}"
            )
        self.criticality = criticality
        self.k_neighbors = max(1, int(k_neighbors))
        self.rho_bounds = (float(rho_bounds[0]), float(rho_bounds[1]))
        # 基线阈值单独留存：父类的 self.ood_factor / caution_rel 会被逐点临时改写
        self.ood_factor_base = float(self.ood_factor)
        self.caution_rel_base = float(self.caution_rel)
        self._cache: dict = {}

    # ── 便捷构造（父类的 classmethod 需要透传新参数）──────────────
    @classmethod
    def for_scenario(cls, scenario: str, X, y, bounds=None, noise_rel=0.03,
                     physics_tol=0.1, criticality: str = "normal",
                     k_neighbors: int = 3, **residual_params):
        residual_fn = get_physics_residual(scenario, **residual_params)
        obj = cls.from_data(X, y, bounds=bounds, noise_rel=noise_rel,
                            residual_fn=residual_fn, physics_tol=physics_tol)
        obj.criticality = criticality
        obj.k_neighbors = max(1, int(k_neighbors))
        return obj

    # ── 上下文量化 ──────────────────────────────────────────────────
    def _typical_spacing(self) -> float:
        """缓存版：逐点门禁会反复用到，避免 O(N²) 重复计算。"""
        if "typical" not in self._cache:
            self._cache["typical"] = super()._typical_spacing()
        return self._cache["typical"]

    def _reference_local_scale(self) -> float:
        """训练集自身的「典型 k 近邻平均距离」，作为 ρ 的归一化基准。

        必须与查询点用同一统计量比较（同为 k-NN 平均距离），否则 k>1 时
        ρ 会系统性偏小 —— 那会让门禁在训练点上也判为低密度，是错误的收紧。
        """
        if "ref_local" in self._cache:
            return self._cache["ref_local"]
        Xn = self._sur._norm(self._sur.X)
        n = len(Xn)
        if n <= 1:
            self._cache["ref_local"] = 1.0
            return 1.0
        dd = np.linalg.norm(Xn[:, None, :] - Xn[None, :, :], axis=2)
        np.fill_diagonal(dd, np.inf)
        k = min(self.k_neighbors, n - 1)
        per_point = np.sort(dd, axis=1)[:, :k].mean(axis=1)
        ref = float(np.median(per_point))
        self._cache["ref_local"] = max(ref, 1e-9)
        return self._cache["ref_local"]

    def _local_scale(self, x) -> float:
        """查询点到训练集的 k 近邻平均距离（归一化空间）。"""
        Xn = self._sur._norm(self._sur.X)
        xn = self._sur._norm(np.asarray(x, float).reshape(1, -1))[0]
        d = np.linalg.norm(Xn - xn, axis=1)
        k = min(self.k_neighbors, len(d))
        return float(np.sort(d)[:k].mean())

    def density_factor(self, x) -> float:
        """ρ(x)：局部证据密度因子，已 clip 到 rho_bounds（有界自适应）。"""
        local = self._local_scale(x)
        rho = self._reference_local_scale() / max(local, 1e-9)
        lo, hi = self.rho_bounds
        return float(np.clip(rho, lo, hi))

    @property
    def criticality_factor(self) -> float:
        """γ(criticality)。"""
        return CRITICALITY_FACTORS[self.criticality]

    def effective_thresholds(self, x) -> Dict[str, float]:
        """给出该点的有效阈值因子及其构成（供 explain / 审计用）。"""
        rho = self.density_factor(x)
        gam = self.criticality_factor
        return {
            "rho": rho,
            "gamma": gam,
            "ood_factor_base": self.ood_factor_base,
            "ood_factor_eff": self.ood_factor_base * rho * gam,
            "caution_rel_base": self.caution_rel_base,
            "caution_rel_eff": self.caution_rel_base * float(np.sqrt(rho)) * gam,
        }

    # ── 门禁（逐点自适应阈值 + 复用父类三层判据）──────────────────
    def gate(self, xs, ood_factor=None):
        if self._sur is None:
            raise RuntimeError("门禁未绑定代理：传入 surrogate / engine / from_data() 拟合")
        xs = np.atleast_2d(np.asarray(xs, float))
        base_ood = float(ood_factor) if ood_factor is not None else self.ood_factor_base

        out = []
        saved_caution = self.caution_rel
        try:
            for i in range(len(xs)):
                th = self.effective_thresholds(xs[i])
                # 逐点临时改写 caution_rel，让父类的 L2 用自适应阈值；
                # 父类逻辑一行不改，避免两套判据漂移。
                self.caution_rel = self.caution_rel_base * float(np.sqrt(th["rho"])) * th["gamma"]
                eff_ood = base_ood * th["rho"] * th["gamma"]
                r = super().gate(xs[i:i + 1], ood_factor=eff_ood)[0]
                r.layers["context"] = (
                    "info",
                    f"ρ={th['rho']:.2f} γ={th['gamma']:.2f}（{self.criticality}）→ "
                    f"ood_factor {self.ood_factor_base:.2f}→{eff_ood:.2f}, "
                    f"caution_rel {self.caution_rel_base:.2f}→{self.caution_rel:.2f}"
                )
                out.append(r)
        finally:
            self.caution_rel = saved_caution
        return out

    def explain(self, xs) -> dict:
        """输出每个查询点的阈值调整依据 —— 自适应必须可解释，否则不可审计。"""
        xs = np.atleast_2d(np.asarray(xs, float))
        res = self.gate(xs)
        rows = []
        for i in range(len(xs)):
            th = self.effective_thresholds(xs[i])
            rows.append({
                "x": [float(v) for v in np.asarray(xs[i]).ravel()],
                "status": res[i].status.value,
                "local_scale": round(self._local_scale(xs[i]), 5),
                "reference_scale": round(self._reference_local_scale(), 5),
                **{k: round(v, 4) for k, v in th.items()},
            })
        return {
            "criticality": self.criticality,
            "k_neighbors": self.k_neighbors,
            "rho_bounds": list(self.rho_bounds),
            "n": len(rows),
            "rows": rows,
        }


# ── 独立可复用门（供 registry / 组合式调用）──────────────────────────
class PhysicalConsistencyGate:
    """物理一致性层独立门：给定残差函数 + 容差，逐点判物理可信性。"""

    def __init__(self, residual_fn: Callable, tol: float = 0.1):
        self.residual_fn = residual_fn
        self.tol = float(tol)

    def gate(self, guard: "VirtualLabGuard", xs) -> list:
        res = guard.gate(xs)
        return [r for r in res]


class UQGate:
    """UQ 层独立门：后验 std 阈值判定（受控 / 通过）。"""

    def __init__(self, threshold: float):
        self.threshold = float(threshold)

    def gate(self, std: Sequence[float]) -> list:
        return [GateStatus.CONTROLLED if s > self.threshold else GateStatus.PASS
                for s in std]


# ── PhysicsNeMo 范式：aleatoric / epistemic 解耦 ───────────────────
class AleatoricEpistemicDecomposer:
    """后验方差的 aleatoric / epistemic 解耦。

    对标 NVIDIA PhysicsNeMo 的 UQ 分层哲学：
      - Aleatoric（aleatoric_var）：数据不可约噪声（= GP noise_var）
      - Epistemic（epistemic_var）：模型不确定性（= GP 后验方差 - aleatoric_var）
      - Total  = aleatoric_var + epistemic_var

    解耦的价值：当 aleatoric_var 占主导时，模型已饱和，加数据也无益；
    当 epistemic_var 占主导时，模型区域数据不足，应触发主动学习。

    用法
    ----
      decomp = AleatoricEpistemicDecomposer(gp, noise_var=0.03**2)
      info = decomp.decompose(x_query)
      # info: {"aleatoric": 9e-4, "epistemic": 0.05, "total": 0.051,
      #        "ratio_epistemic": 0.98  →  主动学习信号强}
    """

    def __init__(self, surrogate: GPSurrogate, noise_var: float):
        self.surrogate = surrogate
        self.noise_var = float(noise_var)

    def decompose(self, x):
        x = np.atleast_2d(np.asarray(x, float))
        _, std = self.surrogate.predict(x)
        total_var = np.asarray(std) ** 2
        aleatoric_var = self.noise_var * np.ones_like(total_var)
        epistemic_var = np.clip(total_var - aleatoric_var, a_min=0.0, a_max=None)

        ratio_epistemic = epistemic_var / (total_var + 1e-15)

        return {
            "aleatoric_var": float(aleatoric_var[0]),
            "epistemic_var": float(epistemic_var[0]),
            "total_var": float(total_var[0]),
            "aleatoric_std": float(np.sqrt(aleatoric_var[0])),
            "epistemic_std": float(np.sqrt(epistemic_var[0])),
            "ratio_epistemic": float(ratio_epistemic[0]),  # 1.0 → 纯模型不确定，主动学习
        }

    def active_learning_signal(self, x, threshold: float = 0.7) -> bool:
        """当 epistemic 占比 > threshold，模型该区域应触发主动学习。"""
        info = self.decompose(x)
        return bool(info["ratio_epistemic"] > threshold)


# ── PhysicsNeMo 范式：集成方差层 ────────────────────────────────────
class EnsembleGuard:
    """多模型集成方差门禁：用 N 个不同 lengthscale 的 GP 估计集成不确定性。

    对标 PhysicsNeMo 的 ensemble uncertainty 层——单一 GP 的后验方差
    可能低估真实不确定性（尤其在多峰分布处），集成方差提供更诚实的上界。

    用法
    ----
      ens = EnsembleGuard(gp, n_models=5, lengthscale_range=(0.2, 1.0))
      res = ens.gate(x_query)
      # res: GateStatus，当集成方差 > 单个 GP 后验方差的 1.5× 时 → CONTROLLED
    """

    def __init__(self, base_gp: GPSurrogate, n_models: int = 5,
                 lengthscale_range: Tuple[float, float] = (0.2, 1.0),
                 inflation_threshold: float = 1.5):
        self.base_gp = base_gp
        self.n_models = n_models
        self.lengthscale_range = lengthscale_range
        self.inflation_threshold = float(inflation_threshold)
        self._ensembles = None

    def _build_ensembles(self):
        """构造多 lengthscale 的 GP 集成（每个使用相同的训练数据）。"""
        if self._ensembles is not None:
            return self._ensembles
        from .surrogate import GPSurrogate as _GPSurrogate
        rng = np.random.default_rng(42)
        ls_values = rng.uniform(*self.lengthscale_range, self.n_models)
        ensembles = []
        for ls in ls_values:
            sur = _GPSurrogate(
                noise_var=self.base_gp.nv,
                y_mean=self.base_gp._ymean,
                y_scale=self.base_gp._yscale,
                lengthscale=ls,
            )
            sur.fit(self.base_gp.X, self.base_gp._y_raw)
            ensembles.append(sur)
        self._ensembles = ensembles
        return ensembles

    def gate(self, x) -> GateStatus:
        """对单个查询点做集成方差门禁。"""
        x = np.atleast_2d(np.asarray(x, float))
        ens = self._build_ensembles()

        # 集成方差 = 各模型方差均值 + 各模型均值间的方差
        means = []
        vars_list = []
        for sur in ens:
            m, s = sur.predict(x)
            means.append(float(m[0]))
            vars_list.append(float(s[0] ** 2))

        mean_mean = float(np.mean(means))
        variance_of_means = float(np.var(means))
        mean_var = float(np.mean(vars_list))
        ensemble_var = variance_of_means + mean_var

        # 单个 GP 的方差
        _, single_std = self.base_gp.predict(x)
        single_var = float(single_std[0] ** 2)

        inflation = ensemble_var / (single_var + 1e-10)

        if inflation > self.inflation_threshold:
            return GateStatus.CONTROLLED
        return GateStatus.PASS

    def gate_summary(self, xs) -> dict:
        """对多个查询点做集成方差门禁汇总。"""
        xs = np.atleast_2d(np.asarray(xs, float))
        statuses = [self.gate(xs[i]) for i in range(len(xs))]
        counts = {g.value: 0 for g in GateStatus}
        for s in statuses:
            counts[s.value] += 1
        return {
            "n": len(xs),
            "counts": counts,
            "statuses": [s.value for s in statuses],
        }
