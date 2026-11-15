"""实验输出接口 —— SwarmLabs 作为「科研验证产品」的分界点。

为什么需要这个模块
------------------
`docs/CORE_OBJECTIVE-20261004.md` §3.5 的核心主张：

> SwarmLabs 的输出**不是**「预测值」，而是「下一个最该做的实验，
> 以及做完之后结论会变成什么样」。

形式化后，一个完整的科研结论输出应该长这样::

    {
      conclusion:   {value, ci95, applicable_range},   # 带校准的结论
      boundary:     {valid_when, fails_when},           # 适用域边界（A14）
      next_experiment: {                                # ★ 实验作为输出
        priority, params, expected_improvement,
        falsification_test,
      },
    }

**现状（2026-10-05 实测）**：三个零件都存在，但**从未接在一起**——

| 零件 | 位置 | 状态 |
|---|---|---|
| ``conclusion`` | ``trust_surface.json`` 每行的 rmse/cov/cal | ✅ 有 |
| ``boundary`` | ``assess_applicability``（A14） | ✅ 有 |
| ``next_experiment`` | ``rank_next_experiments`` | ✅ 有雏形 |
| **组合成一个可验收整体** | — | ❌ **缺** |

缺的不是零件，是**接线**。而没有接线，就没有「实验是可验证输出」
这个主张——只有一个可查询的 API。

这个模块做什么
--------------
1. 把三个零件接成统一接口 :func:`propose_experiment`
2. 让 ``expected_improvement`` 本身**可被证伪**：预测「做完这个实验
   rmse_rel 会从 0.35 降到 0.12」，且这个预测本身要能被检验
   （:func:`verify_prediction`，用于闭环校准）
3. 显式给出**证伪条件**（falsification test）：什么结果会推翻这个假设

关键设计：expected_improvement 的**可证伪性**
----------------------------------------
这是最容易被做成「自适应一���」的地方。一个不可证伪的
「预计会改善」等于没说。

本模块的做法：把预测写成**量化的、可事后核对的断言**
（target_rmse + 预测区间），并提供 :func:`verify_prediction`
在实验做完后核对。若实测改善落在预测区间外 → 记为
``prediction_missed``，累积的 miss 率会暴露「这个预测器不可信」。

**不宣称**：
- 不宣称 predicted improvement 会实现（GP 残差学习本来就有
  ``expected_improvement``，但那是模型内的启发式，本模块做的是
  **事后可核对**的版本）
- 不宣称这构成「科学发现」——它只把「结论 + 边界 + 下一步」
  变成结构化输出，结论对错仍由 A1~A15 与门禁负责
- ``verify_prediction`` 需要真实或虚拟实验数据才能核对，没有数据时
  返回 ``unverified``（**不假装能验证**）

**实测代价：conformal 区间偏宽**
------------------------------
全 82 场景的「CI 半宽 / 值」比值：**中位 22.03×**（p25=4.99、
p75=47.33、max=144.13）。这不是 bug：

- ``conformal q`` 本身就是保守分位数（实测 0.44~75.66）
- ``std_out`` 是**测试集上** epistemic std 的均值，而它在训练点附近
  极小、在域边缘很大

这与 ``coverage_ok = 80/82`` 不矛盾——coverage 问的是「95% 的真值落在
区间内」，区间**可以**宽而位置正确。代价体现在 ``sharpness`` 门禁
（``SHARPNESS_CAP``）：宽区间意味着「预测不够锐利」。

**若要更锐的区间**，正确做法不是调窄区间（那会破坏 coverage），
而是加训练点密度以降低 ``std_out``——这正是 ``next_experiment``
在做的事。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field, asdict, replace
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from scripts.paper_validation_loop import rank_next_experiments

_SCHEMA = "svp.experiment-proposal.v1"
DEFAULT_SEED = 4242

#: expected_improvement 的**相对**容差：实测改善落在
#: ``[pred * (1-tol), pred * (1+tol)]`` 之外就算 prediction_missed。
#:
#: 为什么给容差而不是要求精确命中：GP 的残差学习效果依赖数据分布，
#: 精确命中不可能。但容差太大就失去意义——0.30 意味着「预测改善 50%」
#: 时实测要落在 35%~65% 才算命中。
EXPECTED_IMPROVEMENT_TOL = 0.30

#: 结论的 95% 区间由 conformal q × epistemic std 给出。
#: 这里不做额外的正态假设，只做区间端点计算。
CI_LEVEL = 0.95

#: OOD 拒答阈值（2026-10-08 新增，A15 的运行时落地）。
#:
#: A15 已证明：GP 在训练域外的预测**退化为训练集均值**（核函数趋零，
#: 换核换 ls 都改不了）。因此在 OOD 点给数不是"精度不够"，
#: 而是**给了一个与查询无关的常数**——比明说"不知道"更坏。
#:
#: 判据用 `GPSurrogate.proximity()`：查询点到训练集的最近归一化距离。
#: 阈值取「训练集内两两最近距离的中位数 × 该倍数」——
#: 即"若这个点比训练点彼此之间的典型间隔还远，就不认"。
OOD_PROXIMITY_MULTIPLIER = 2.0


def ood_threshold_from_train(train_X: np.ndarray) -> float:
    """从训练集算出 OOD 拒答阈值。

    做法：算训练集两两之间的最近归一化距离，取中位数作为
    "典型间隔"，再乘 :data:`OOD_PROXIMITY_MULTIPLIER`。

    这样阈值随场景的采样密度自适应——密集采样的场景阈值小、
    稀疏采样的阈值大，不会用同一个绝对值卡所有场景。

    Args:
        train_X: 训练点（未归一化的原始参数）

    Returns:
        阈值（``inf`` 表示训练点不足，无法判定 → 保守拒答一切）
    """
    X = np.atleast_2d(np.asarray(train_X, float))
    if len(X) < 3:
        # 点太少：无法估计"典型间隔"。保守起见判为全部 OOD。
        return float("inf")
    lo, hi = X.min(axis=0), X.max(axis=0)
    span = np.where(hi - lo < 1e-12, 1.0, hi - lo)
    Xn = (X - lo) / span
    d = np.linalg.norm(Xn[:, None, :] - Xn[None, :, :], axis=2)
    np.fill_diagonal(d, np.inf)
    nearest = d.min(axis=1)
    med = float(np.median(nearest))
    if not np.isfinite(med) or med <= 0:
        return float("inf")
    return med * OOD_PROXIMITY_MULTIPLIER


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class Conclusion:
    """带校准的定量结论。"""

    value: float
    ci_low: float
    ci_high: float
    rmse_rel: float
    coverage: float
    cal_err: float
    n_train: int
    n_total: int
    notes: str = ""

    @property
    def ci_width(self) -> float:
        return self.ci_high - self.ci_low

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["ci_width"] = round(self.ci_width, 6)
        return d


@dataclass
class Boundary:
    """结论的适用域边界。"""

    in_applicability: bool
    roughness: Optional[float]
    n_extrema: int
    valid_when: str
    fails_when: str
    extrapolation_verified: bool = False    # 是否真的测过外推（G1/G2）
    # ── OOD 拒答（2026-10-08 新增，A15 运行时落地）──
    ood_threshold: Optional[float] = None   # proximity 超过它即判 OOD
    query_proximity: Optional[float] = None # 被查询点到训练集的最近距离
    query_ood: Optional[bool] = None        # 该查询是否落在训练域外

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class NextExperiment:
    """下一步实验建议（**输出**，不是中间步骤）。"""

    point: List[float]
    priority: float                      # 预期信息增益 0~1
    std: float
    proximity: Optional[float]           # 到训练集的最近距离（OOD 守卫）
    rationale: str
    # ── 可证伪部分 ──
    current_rmse_rel: float
    #: 预测的下一步实验 rmse_rel。``None`` = 未知（无法诚实估计改善幅度时，
    #: 不给误导性的精确数字——PR-09，报告 §10）。调用方必须把 None 当作
    #: 「未知」渲染，不得当作 0 或伪造比率。
    predicted_rmse_rel: Optional[float] = None
    predicted_coverage: Optional[float] = None
    confidence: str = "heuristic-low"   # 预测的置信度（heuristic，非概率）
    falsification_test: str = ""
    #: 该建议点是否落在训练域外。True 时意味着「先扩边界，别在此补点」。
    ood_flagged: bool = False

    @property
    def predicted_improvement_ratio(self) -> Optional[float]:
        """预测改善倍数（当前/预测）。

        当 ``predicted_rmse_rel`` 为 None（收益未知）时返回 None，
        不伪造数字。调用方用 ``None`` 渲染为「未知」。
        """
        if self.predicted_rmse_rel is None:
            return None
        if self.current_rmse_rel <= 1e-12:
            return 1.0
        return self.current_rmse_rel / max(self.predicted_rmse_rel, 1e-12)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        ratio = self.predicted_improvement_ratio
        d["predicted_improvement_ratio"] = (
            round(ratio, 3) if ratio is not None else None)
        return d


@dataclass
class ExperimentProposal:
    """完整的科研结论输出。"""

    key: str
    domain: str
    conclusion: Conclusion
    boundary: Boundary
    next_experiment: Optional[NextExperiment]
    alternatives: List[NextExperiment] = field(default_factory=list)
    rubric_passed: int = 0
    rubric_total: int = 0
    generated_at: int = 0

    @property
    def is_actionable(self) -> bool:
        """结论是否「可用」——达标的场景才给实验建议。

        不达标的场景给建议是误导：那个模型的结论本身不可信。
        但仍返回完整的结构（让调用方知道**为什么**不可用）。
        """
        return (self.next_experiment is not None
                and self.rubric_total > 0
                and self.rubric_passed == self.rubric_total)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": _SCHEMA,
            "key": self.key,
            "domain": self.domain,
            "is_actionable": self.is_actionable,
            "conclusion": self.conclusion.to_dict(),
            "boundary": self.boundary.to_dict(),
            "next_experiment": (self.next_experiment.to_dict()
                                if self.next_experiment else None),
            "alternatives": [a.to_dict() for a in self.alternatives],
            "rubric": {"passed": self.rubric_passed,
                       "total": self.rubric_total},
            "generated_at": self.generated_at,
        }


# ---------------------------------------------------------------------------
# 核心：组合三个零件
# ---------------------------------------------------------------------------

def propose_experiment(
    row: Dict[str, Any],
    next_exps: Optional[List[Dict[str, Any]]] = None,
    gen_bench: Optional[Dict[str, Any]] = None,
    train_X: Optional[np.ndarray] = None,
    query_x: Optional[np.ndarray] = None,
) -> ExperimentProposal:
    """把 conclusion / boundary / next_experiment 组合成一个可验收对象。

    Args:
        row: ``trust_surface.json`` 的一行（必须有 key/domain/rmse_rel/
             coverage_after/cal_err_after/std_out 等）
        next_exps: ``rank_next_experiments`` 的输出（None 则不算实验建议）
        gen_bench: ``data/generalization_bench.json``（可选，用于标注
             该场景所属学科的外推证据是否存在）
        train_X: 该场景的**训练点原始参数**（可选）。给了才会做 OOD 判定。
        query_x: 实际被查询的点（可选）。给了会算它的 proximity 并可能
             触发拒答。

    Returns:
        ExperimentProposal
    """
    key = str(row.get("key", "?"))
    domain = str(row.get("domain", ""))

    # ── 1) conclusion：带校准的定量结论 ───────────────────────────
    rmse_rel = float(row.get("rmse_rel") or 0.0)
    cov = float(row.get("coverage_after") or 0.0)
    cal = float(row.get("cal_err_after") or 0.0)
    # epistemic std 用于构造区间。
    #
    # 量纲纪律（2026-10-05 实测修正）：`rmse_after` 与 `std_out` **都已
    # 归一化**（除以 y_scale），所以区间半宽 = q × std_out 就够了，
    # **不能**再乘 `scale`——那是把归一化量纲又放大了回去。
    # 初版误乘 scale，导致 CI 半宽比值高达 150~6181×（例如
    # surrogate_forrester 值 0.0039、区间 [-0.584, 0.592]，
    # 区间比值 300 倍——毫无信息量）。
    std_out = float(row.get("std_out") or 0.0)
    scale = float(row.get("scale") or 1.0)
    q = float(row.get("conformal_q") or 1.96)
    half = float(q * std_out)
    val = float(row.get("rmse_after") or 0.0)
    # 退化场景（y_train_std≈0）没有有意义的区间
    degenerate = bool(row.get("degenerate"))
    if degenerate:
        half = 0.0

    conclusion = Conclusion(
        value=round(val, 6),
        ci_low=round(val - half, 6),
        ci_high=round(val + half, 6),
        rmse_rel=round(rmse_rel, 6),
        coverage=round(cov, 4),
        cal_err=round(cal, 6),
        n_train=int(row.get("n_store") or 0),
        n_total=int(row.get("n_store") or 0),
        notes=("退化场景（y_std≈0），区间无意义" if degenerate else
               f"95% 区间 = ±q({q:.3f})×std_out，均为归一化量纲"
               f"（y_std={scale:.4g}）"),
    )

    # ── 2) boundary：适用域（A14）+ OOD 拒答（A15）────────────────
    in_app = row.get("in_applicability")
    in_app = True if in_app is None else bool(in_app)
    rough = row.get("roughness")
    n_ext = int(row.get("n_extrema") or -1)

    # ── OOD 判定（A15 运行时落地）───────────────────────────────
    # 只有在拿到训练点与查询点时才算；否则留 None（不假装判过）。
    ood_thr: Optional[float] = None
    q_prox: Optional[float] = None
    q_ood: Optional[bool] = None
    if train_X is not None:
        ood_thr = ood_threshold_from_train(train_X)
        if query_x is not None:
            Xtr = np.atleast_2d(np.asarray(train_X, float))
            lo, hi = Xtr.min(axis=0), Xtr.max(axis=0)
            span = np.where(hi - lo < 1e-12, 1.0, hi - lo)
            Xn = (Xtr - lo) / span
            q = np.atleast_2d(np.asarray(query_x, float))
            qn = (q - lo) / span
            d = np.linalg.norm(Xn[:, None, :] - qn[None, :, :], axis=2)
            q_prox = float(d.min(axis=0).min())
            q_ood = bool(q_prox > ood_thr)

    if q_ood:
        # **真正拒答**：结论值不可信，区间无意义，明确说"不知道"。
        valid = (f"❌ 查询点落在训练域外（proximity={q_prox:.4g} > "
                 f"阈值 {ood_thr:.4g}）——本结论不适用")
        fails = ("A15 已实测：GP 在训练域外预测退化为「训练集均值」，"
                 "给数等于给一个与查询无关的常数。故此处**显式拒答**。"
                 "要得到该点的结论，必须先在该区域补实验点扩边界。")
        conclusion.notes = (
            f"[OOD 拒答] proximity={q_prox:.4g} > 阈值 {ood_thr:.4g}；"
            "该点的预测值与区间均不可信，请先扩边界")
        conclusion.ci_low = conclusion.value
        conclusion.ci_high = conclusion.value
    elif in_app:
        valid = (f"目标函数在平滑代理模型适用域内"
                 f"（roughness={rough if rough is not None else 'n/a'}）")
        # 适用域内也**不代表**能外推——这是 A15 的硬约束
        fails = ("超出采样参数域（GP 外推会退化为「预测=训练集均值」，"
                 "已实测：G1 参数外推 0/10 通过）；"
                 "本引擎对 OOD 查询显式拒答而非给数")
    else:
        valid = "无——该目标超出平滑代理模型适用域"
        fails = (f"高频震荡目标（roughness={rough}，{n_ext} 个局部极值），"
                 f"加密训练点无效（实测 30→480 点 rel 只降 1.46→1.00）")
    b = Boundary(
        in_applicability=in_app,
        roughness=None if rough is None else float(rough),
        n_extrema=n_ext,
        valid_when=valid,
        fails_when=fails,
        extrapolation_verified=bool(_has_extrap_evidence(gen_bench, domain)),
        ood_threshold=(None if ood_thr is None or not np.isfinite(ood_thr)
                       else round(float(ood_thr), 6)),
        query_proximity=(None if q_prox is None else round(q_prox, 6)),
        query_ood=q_ood,
    )

    # ── 3) next_experiment：实验作为输出 ──────────────────────────
    nxt: Optional[NextExperiment] = None
    alts: List[NextExperiment] = []
    if next_exps:
        cand = []
        for t in next_exps:
            prox = (None if t.get("proximity") is None
                    else float(t.get("proximity")))
            # 该建议点自身是否越界（proximity > 阈值）——用训练点同尺度算
            pt_ood = False
            if train_X is not None and prox is not None and ood_thr is not None:
                pt_ood = bool(np.isfinite(ood_thr) and prox > ood_thr)
            if pt_ood:
                rat = ("⚠️ 该点落在训练域外（proximity 超过阈值）——"
                       "在此补点不会改善已有区域的结论，"
                       "应先扩边界而非在此加样本")
                fals = ("若在该点补点后，训练域内点的 rmse_rel 无改善，"
                        "则「在最大不确定性处补点」这一策略被证伪，"
                        "应改为在训练域内做加密")
            else:
                rat = ("在已观测邻域内最大化信息增益（已过 OOD 守卫："
                       f"proximity={prox if prox is None else round(prox, 6)}"
                       f" ≤ 阈值 {round(ood_thr, 6) if ood_thr and np.isfinite(ood_thr) else 'n/a'}）")
                fals = (
                    f"做完该实验后若 rmse_rel 仍 ≥ {rmse_rel * 0.8:.4f}"
                    f"（即改善 < 20%），则本建议的 expected_improvement "
                    f"被证伪，应改用其他补点策略或缩小信任域")
            cand.append(NextExperiment(
                point=list(t.get("point") or []),
                priority=float(t.get("info_gain") or 0.0),
                std=float(t.get("std") or 0.0),
                proximity=prox,
                rationale=rat,
                # PR-09：rank_next_experiments 只给「下一个最该做的点」，
                # 并不提供对改善幅度的可校准预测（没有真实 posterior 抽样
                # 的 rmse_rel 分布）。诚实做法是标注「收益未知」，而不是
                # 编一个「降一半」的精确数字——那既不可证伪也无依据。
                current_rmse_rel=round(rmse_rel, 6),
                predicted_rmse_rel=None,       # 未知：不给误导性精确数字
                predicted_coverage=None,       # 无法诚实预测 coverage 变化
                confidence="heuristic-low",    # 明确标注这不是概率
                falsification_test=fals,
                ood_flagged=pt_ood,
            ))
        if cand:
            cand.sort(key=lambda c: -c.priority)
            nxt = cand[0]
            alts = cand[1:3]

    # ── 启发式回退：适用域内但无 rank_next_experiments 输出时，
    #    给出保守的「沿边界加密」建议，确保三零件完整性 ──────────
    # 设计原则：适用域内的场景**必须**有实验建议（即使是占位），
    # 因为核心目标是「结论+边界+下一步实验」三零件齐备。
    # 若无法从 row 获取 bounds，则用空点列表 + 明确标注需要主动学习。
    if nxt is None and in_app and not degenerate:
        bnd = row.get("bounds")
        pt: List[float] = []
        if isinstance(bnd, (list, tuple)) and len(bnd) >= 2:
            # 在参数域中心偏左 10% 处建议一个加密点（保守探索）
            for lo, hi in bnd:
                span = float(hi) - float(lo)
                pt.append(round(float(lo) + 0.10 * span, 6))
        elif isinstance(bnd, (list, tuple)) and len(bnd) == 1:
            lo, hi = float(bnd[0][0]), float(bnd[0][1])
            pt.append(round(lo + 0.10 * (hi - lo), 6))

        if pt:
            rat = ("启发式回退：无 rank_next_experiments 输出时，"
                   "沿参数域边界加密以降低 std_out。"
                   "该建议未经过 OOD 守卫，置信度低。")
            conf = "heuristic-fallback"
        else:
            # bounds 不可用（trust_surface.json 不含 bounds 字段）：
            # 创建占位建议，明确标注需要 rank_next_experiments 才能给具体点
            rat = ("占位建议：trust_surface.json 不含 bounds 字段，"
                   "无法生成具体加密点。请调用 rank_next_experiments "
                   "获取基于最大不确定性原则的主动学习建议。"
                   "该建议仅确保三零件完整性，不是可执行的具体点。")
            conf = "placeholder-needs-active-learning"
            # 不伪造具体点：无 bounds 就没有可信的「下一个点」，
            # 空列表 + rationale 已明确标注需要主动学习。
            pt = []

        # PR-09：启发式回退同样**不提供**改善幅度的精确预测——
        # 它只是「沿边界加密降 std」的策略声明，不是对 rmse_rel
        # 的可校准预测。诚实标注 predicted_rmse_rel=None（未知）。
        nxt = NextExperiment(
            point=pt,
            priority=0.15,
            std=round(std_out * 0.5, 6),
            proximity=None,
            rationale=rat,
            current_rmse_rel=round(rmse_rel, 6),
            predicted_rmse_rel=None,       # 未知：启发式无真实预测依据
            predicted_coverage=None,
            confidence=conf,
            falsification_test=(
                f"若在该点补点后 rmse_rel 仍 ≥ {rmse_rel * 0.8:.4f}"
                f"（改善 < 20%），则边界加密策略被证伪，"
                f"应改用 rank_next_experiments 的主动学习建议"),
            ood_flagged=False,
        )

    rb = row.get("rubric") or {}
    return ExperimentProposal(
        key=key,
        domain=domain,
        conclusion=conclusion,
        boundary=b,
        next_experiment=nxt,
        alternatives=alts,
        rubric_passed=int(row.get("rubric_passed") or 0),
        rubric_total=int(row.get("rubric_total") or len(rb) or 0),
        generated_at=int(time.time()),
    )


def _has_extrap_evidence(gen_bench: Optional[Dict[str, Any]],
                         domain: str) -> bool:
    """该场景所属学科是否有任何外推通过的记录。

    刻意保守：只在「该学科的 G1 或 G2 至少有一个场景通过」时返回 True。
    G3（留一学科）单独不算——它的语义是"别的学科能不能预测你"，
    与"你能不能外推"是不同的问题。
    """
    if not gen_bench:
        return False
    disc = (domain.split("/")[0].strip() or "").lower()
    for key in ("g1_param_extrapolation", "g2_holdout_dim"):
        blk = gen_bench.get(key) or {}
        for r in (blk.get("results") or []):
            if r.get("ok") and disc and disc in str(r.get("key", "")):
                return True
            # key 前缀含学科名的情况（如 micro_* → microorganisms）
            if r.get("ok"):
                kk = str(r.get("key", "")).split("_")[0].lower()
                if kk and disc and (disc in kk or kk in disc):
                    return True
    return False


# ---------------------------------------------------------------------------
# expected_improvement 的可证伪性
# ---------------------------------------------------------------------------

def verify_prediction(
    proposal: ExperimentProposal,
    observed_rmse_rel: float,
    observed_coverage: Optional[float] = None,
) -> Dict[str, Any]:
    """核对 next_experiment 的 ``expected_improvement`` 是否兑现。

    这是本模块的核心——**让「预计会改善」变成可事后检验的断言**。
    一个不可证伪的「预计会改善」等于没说。

    判据：实测 rmse_rel 落在
    ``[pred * (1-tol), pred * (1+tol)]`` 之外 → ``prediction_missed``。
    默认 tol = 0.30（见 ``EXPECTED_IMPROVEMENT_TOL``）。

    Args:
        proposal: 先前生成的提案
        observed_rmse_rel: 实验做完后的实测 rel rmse
        observed_coverage: 实测 coverage（可选）

    Returns:
        核对结果 dict（``verdict`` ∈ verified / prediction_missed / unverified）
    """
    ne = proposal.next_experiment
    if ne is None:
        return {"verdict": "unverified", "reason": "提案无 next_experiment",
                "key": proposal.key}

    pred = ne.predicted_rmse_rel
    # PR-09：predicted_rmse_rel=None 表示「收益未知」（启发式回退/占位/
    # 无 rank 输出），没有可校准的预测幅度，**不可证伪也无需证伪**——
    # 诚实返回 unverified，而不是拿 None 做算术。
    if pred is None:
        return {
            "verdict": "unverified",
            "reason": "predicted_rmse_rel 为 None（收益未知，"
                      "启发式/占位建议未提供精确预测），不可验证",
            "key": proposal.key,
            "predicted_rmse_rel": None,
            "observed_rmse_rel": round(float(observed_rmse_rel), 6),
            "current_rmse_rel": ne.current_rmse_rel,
        }
    obs = float(observed_rmse_rel)
    lo = pred * (1.0 - EXPECTED_IMPROVEMENT_TOL)
    hi = pred * (1.0 + EXPECTED_IMPROVEMENT_TOL)
    hit = lo <= obs <= hi

    out: Dict[str, Any] = {
        "key": proposal.key,
        "verdict": "verified" if hit else "prediction_missed",
        "predicted_rmse_rel": round(pred, 6),
        "observed_rmse_rel": round(obs, 6),
        "band": [round(lo, 6), round(hi, 6)],
        "tolerance": EXPECTED_IMPROVEMENT_TOL,
        "current_rmse_rel": ne.current_rmse_rel,
    }
    if observed_coverage is not None:
        out["observed_coverage"] = round(float(observed_coverage), 4)
    if not hit:
        out["reason"] = (f"实测 {obs:.6f} 不在预测区间 [{lo:.6f}, {hi:.6f}]"
                         f"——该实验建议的 expected_improvement 预测能力存疑")
    return out


def audit_predictor(history: List[Dict[str, Any]]) -> Dict[str, Any]:
    """累积核对历史，暴露「这个预测器是否可信」。

    单次 miss 可能只是运气；**累积 miss 率**高就说明
    ``expected_improvement`` 不可信，应下调 confidence 或改用别的策略。

    Args:
        history: :func:`verify_prediction` 的结果列表

    Returns:
        汇总 dict（命中率、是否可信、建议）
    """
    checked = [h for h in history
               if h.get("verdict") in ("verified", "prediction_missed")]
    n = len(checked)
    if n == 0:
        return {"n_checked": 0, "hit_rate": None,
                "trustworthy": None,
                "advice": "尚无核对记录——先跑几轮实验再评估预测器可信度"}
    n_hit = sum(1 for h in checked if h["verdict"] == "verified")
    rate = n_hit / n
    # 判据：命中率 ≥ 0.6 才认为预测器基本可信（不是 0.9——GP 残差
    # 学习的期望改善本来就有很大方差，0.9 是做不到的）
    trustworthy = rate >= 0.6
    return {
        "n_checked": n,
        "n_verified": n_hit,
        "n_missed": n - n_hit,
        "hit_rate": round(rate, 4),
        "trustworthy": trustworthy,
        "advice": (
            "预测器基本可信，继续使用并累积更多记录" if trustworthy else
            "**预测器不可信**：命中率先验下调 `confidence` 到 heuristic-none，"
            "或改用「只补点、不承诺改善」的保守模式——"
            "宁可不给 expected_improvement，也不要给一个已知不准的"),
    }


# ---------------------------------------------------------------------------
# 批量入口
# ---------------------------------------------------------------------------

def build_all(
    trust_surface_path: Optional[str] = None,
    gen_bench_path: Optional[str] = None,
    repo_root: Optional[str] = None,
    top_n: int = 4,
) -> Dict[str, Any]:
    """为全部场景生成实验建议。

    Args:
        trust_surface_path: 可选，覆盖默认路径
        gen_bench_path: 可选，覆盖默认路径
        repo_root: 仓库根（默认由本文件位置推断）
        top_n: 每场景候选实验数

    Returns:
        dict：逐场景提案 + 汇总
    """
    if repo_root is None:
        repo_root = os.path.abspath(
            os.path.join(os.path.dirname(__file__), ".."))
    if trust_surface_path is None:
        trust_surface_path = os.path.join(
            repo_root, "data", "trust_surface.json")
    if gen_bench_path is None:
        gp = os.path.join(repo_root, "data", "generalization_bench.json")
        gen_bench_path = gp if os.path.isfile(gp) else None

    with open(trust_surface_path, encoding="utf-8") as f:
        ts = json.load(f)
    gb = None
    if gen_bench_path and os.path.isfile(gen_bench_path):
        with open(gen_bench_path, encoding="utf-8") as f:
            gb = json.load(f)

    # next_experiment 需要重新算（trust_surface 里不存）
    import sys
    root = repo_root
    for p in (os.path.join(root, "scripts"), root,
              os.path.join(root, "engine")):
        if p not in sys.path:
            sys.path.insert(0, p)
    try:
        from engine.scenario_registry import all_scenarios as _all_scns
        scns = _all_scns()
    except Exception as e:                       # noqa: BLE001
        scns = {}
        err = f"{type(e).__name__}: {e}"
    else:
        err = None

    props: List[Dict[str, Any]] = []
    n_ne = 0
    n_actionable = 0
    for row in ts.get("rows", []):
        nexps = None
        sc = scns.get(str(row.get("key")))
        if sc is not None:
            try:
                nexps = rank_next_experiments(sc, top_n=top_n)
            except Exception:                   # noqa: BLE001
                nexps = None
        p = propose_experiment(row, next_exps=nexps, gen_bench=gb)
        d = p.to_dict()
        props.append(d)
        if d["next_experiment"]:
            n_ne += 1
        if d["is_actionable"]:
            n_actionable += 1

    n = max(len(props), 1)
    return {
        "schema": _SCHEMA,
        "generated_at": int(time.time()),
        "repo_root": root,
        "n_scenarios": len(props),
        "n_with_next_experiment": n_ne,
        "n_actionable": n_actionable,
        "import_error": err,
        "summary": {
            "has_next_experiment_rate": round(n_ne / n, 4),
            "actionable_rate": round(n_actionable / n, 4),
            "note": ("actionable = rubric 7/7 全通过且有合规的实验建议。"
                     "不达标的场景**不给**建议——那个模型的结论本身"
                     "不可信，给建议是误导。"),
        },
        "proposals": props,
    }


def save(result: Dict[str, Any], path: str) -> str:
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    return os.path.abspath(path)


def default_output_path(repo_root: Optional[str] = None) -> str:
    if repo_root is None:
        repo_root = os.path.abspath(
            os.path.join(os.path.dirname(__file__), ".."))
    return os.path.join(repo_root, "data", "experiment_proposals.json")


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

def _selftest() -> bool:
    passed = 0
    total = 0

    def check(name: str, cond: bool) -> None:
        nonlocal passed, total
        total += 1
        if cond:
            passed += 1
            print(f"  PASS  {name}")
        else:
            print(f"  FAIL  {name}")

    def _row(**kw):
        base = {
            "key": "test_scn", "domain": "biology / test",
            "rmse_after": 1.234, "rmse_rel": 0.043, "scale": 28.7,
            "coverage_after": 0.93, "cal_err_after": 0.021,
            "std_out": 0.11, "conformal_q": 2.1,
            "n_store": 42, "degenerate": False,
            "in_applicability": True, "roughness": 0.004,
            "n_extrema": 0, "rubric_passed": 7, "rubric_total": 7,
        }
        base.update(kw)
        return base

    def _nexp(n=3):
        return [{"point": [0.3 + 0.1 * i], "info_gain": 0.5 - 0.1 * i,
                 "std": 0.2 - 0.02 * i, "proximity": 0.01 * (i + 1),
                 "n_ood_rejected": 200} for i in range(n)]

    # 1. 三零件齐全时输出完整
    p = propose_experiment(_row(), _nexp(), None)
    d = p.to_dict()
    check("has conclusion", d["conclusion"]["value"] == 1.234)
    check("has boundary", "fails_when" in d["boundary"])
    check("has next_experiment", d["next_experiment"] is not None)
    check("actionable when rubric 7/7", d["is_actionable"])

    # 2. CI 端点对称
    c = d["conclusion"]
    check("CI symmetric",
          abs((c["value"] - c["ci_low"]) - (c["ci_high"] - c["value"])) < 1e-6)
    check("CI width recorded", c["ci_width"] > 0)

    # 2b. 量纲纪律回归（2026-10-05 修正的 bug）
    #     `rmse_after` 与 `std_out` 都已归一化，区间半宽 = q × std_out。
    #     初版误乘 `scale`，导致 CI 比值高达 150~6181×，毫无信息量。
    #     用 surrogate_forrester 的真实数值做回归。
    check("CI half-width = q * std_out (no scale multiplication)",
          abs(c["ci_high"] - c["value"] - 2.1 * 0.11) < 1e-6)
    p_real = propose_experiment(
        _row(key="surrogate_forrester", rmse_after=0.0039, rmse_rel=0.0009,
             std_out=0.0038, conformal_q=35.63, scale=4.343), None, None)
    cr = p_real.conclusion
    ratio = cr.ci_width / max(abs(cr.value), 1e-12)
    # 修量纲后该场景的比值是 35.63×（而非误乘 scale 时的 300×）。
    # 仍然 >1 是**真实的**，不是 bug：conformal q 本身就是保守分位数，
    # 且 std_out 是测试集上 epistemic std 的均值（训练点附近极小、
    # 域边缘很大）。全 82 场景的该比值中位数是 22.03×（p25=4.99、
    # p75=47.33、max=144.13）。
    #
    # 这与 coverage_ok=80/82 不矛盾——coverage 问的是「95% 的真值落在
    # 区间内」，区间**可以**宽而位置正确。代价是 sharpness（见
    # SHARPNESS_CAP 门禁）：宽区间意味着「预测不够锐利」。
    # 所以这里只断言「修量纲后没有再放大 7 倍以上」。
    # 注意 `ci_width` = 2 × half，所以 width/value 是 half/value 的 2 倍。
    # 修量纲后该场景 half/value = 35.63× → width/value = 71.26×
    # （误乘 scale 时是 2×35.63×4.343 = 309×，即多了 4.343 倍）。
    check("real-world CI not amplified by scale (<120x)", ratio < 120.0)
    check("notes mention normalized units", "归一化" in cr.notes)

    # 3. 退化场景 → 无区间 + 不可 actionable
    pd = propose_experiment(_row(degenerate=True, y_train_std=0.0), _nexp(), None)
    check("degenerate -> zero CI width", pd.conclusion.ci_width == 0.0)
    check("degenerate noted", "退化" in pd.conclusion.notes)

    # 4. 超出适用域 → valid_when 说明无
    pb = propose_experiment(_row(in_applicability=False, roughness=1.91,
                                 n_extrema=66), _nexp(), None)
    check("out-of-applicability: valid_when says 无",
          pb.boundary.valid_when.startswith("无"))

    # 5. 适用域内也必须声明外推会崩（A15 硬约束）
    check("in-applicability still warns extrapolation fails",
          "训练集均值" in pb.boundary.fails_when
          or "退化" in p.boundary.fails_when)

    # 6. rubric 未达标 → 不可 actionable（不给误导性建议）
    pf = propose_experiment(_row(rubric_passed=5, rubric_total=7), _nexp(), None)
    check("not actionable when rubric fails", not pf.is_actionable)

    # 7. 无 next_experiment → unverified
    #    注意：适用域内 + nexp=None 会触发启发式回退**刻意创建**占位
    #    next_experiment（保证「结论+边界+下一步」三零件完整性），
    #    所以真正的「无 next_experiment」只出现在**不适用域**。
    pn = propose_experiment(_row(in_applicability=False), None, None)
    check("no next_experiment -> not actionable", not pn.is_actionable)
    v = verify_prediction(pn, 0.02)
    check("verify on empty -> unverified", v["verdict"] == "unverified")

    # 7b. PR-09：predicted_rmse_rel=None（收益未知）→ 不可验证
    #     这是「诚实不给精确数字」的直接后果，必须显式回归。
    check("PR-09: heuristic proposal yields predicted=None",
          p.next_experiment.predicted_rmse_rel is None)
    check("PR-09: improvement ratio is None (not fabricated)",
          p.next_experiment.predicted_improvement_ratio is None)
    v_unknown = verify_prediction(p, 0.02)
    check("PR-09: verify unknown -> unverified",
          v_unknown["verdict"] == "unverified")
    check("PR-09: unverified reason mentions 未知",
          "未知" in v_unknown.get("reason", ""))

    # 8. expected_improvement 可证伪（核心）——需**显式数值预测**
    #    才能构造带可校准幅度的提案（PR-09 后 rank/回退都给 None）。
    p_num = replace(
        p,
        next_experiment=replace(
            p.next_experiment, predicted_rmse_rel=0.02))
    pred = p_num.next_experiment.predicted_rmse_rel
    v_ok = verify_prediction(p_num, pred)             # 精确命中
    check("verify exact hit -> verified",
          v_ok["verdict"] == "verified")
    v_miss = verify_prediction(p_num, pred * 3.0)     # 远超容差
    check("verify far miss -> prediction_missed",
          v_miss["verdict"] == "prediction_missed")
    check("miss gives reason", "存疑" in v_miss.get("reason", ""))
    # 容差边界
    v_edge = verify_prediction(p_num, pred * (1 + EXPECTED_IMPROVEMENT_TOL + 0.01))
    check("verify just outside tolerance -> missed",
          v_edge["verdict"] == "prediction_missed")
    v_in = verify_prediction(p_num, pred * (1 - EXPECTED_IMPROVEMENT_TOL + 0.001))
    check("verify just inside tolerance -> verified",
          v_in["verdict"] == "verified")

    # 9. 预测器审计：累积 miss 必须暴露不可信
    a1 = audit_predictor([v_ok])
    check("audit with 1 hit -> trustworthy", a1["trustworthy"] is True)
    a2 = audit_predictor([v_miss] * 5)
    check("audit with 5 misses -> untrustworthy", a2["trustworthy"] is False)
    check("untrustworthy gives advice", "不可信" in a2["advice"])
    a3 = audit_predictor([])
    check("audit empty -> None trustworthy", a3["trustworthy"] is None)

    # 10. 混合：命中率先验
    a4 = audit_predictor([v_ok] * 6 + [v_miss] * 4)
    check("audit 60% hit -> trustworthy", a4["trustworthy"] is True)
    a5 = audit_predictor([v_ok] * 5 + [v_miss] * 5)
    check("audit 50% hit -> untrustworthy", a5["trustworthy"] is False)

    # 11. 证伪条件必须可读
    check("falsification_test non-empty",
          bool(p.next_experiment.falsification_test))
    check("falsification mentions rmse",
          "rmse_rel" in p.next_experiment.falsification_test)

    # 12. confidence 必须标注为启发式（不能冒充概率）
    check("confidence marked heuristic",
          p.next_experiment.confidence.startswith("heuristic"))

    # 13. gen_bench 外推证据探测
    check("no gen_bench -> not verified",
          not p.boundary.extrapolation_verified)
    gb_fake = {"g1_param_extrapolation": {"results": [
        {"key": "opt_good", "ok": True}]},
        "g2_holdout_dim": {"results": []}}
    p_gb = propose_experiment(
        _row(domain="optimization / x"), _nexp(), gb_fake)
    check("g1 pass for matching discipline -> verified",
          p_gb.boundary.extrapolation_verified)
    p_gb2 = propose_experiment(
        _row(domain="biology / y"), _nexp(), gb_fake)
    check("g1 pass for other discipline -> not verified",
          not p_gb2.boundary.extrapolation_verified)

    # 14. 排序：priority 高的排前面
    p_alt = propose_experiment(_row(), _nexp(4), None)
    check("alternatives collected", len(p_alt.alternatives) == 2)
    if len(p_alt.alternatives) >= 1:
        check("alternatives sorted by priority desc",
              p_alt.alternatives[0].priority <= p_alt.next_experiment.priority)

    # 15. schema 一致
    check("schema tag present", d["schema"] == _SCHEMA)
    check("generated_at set", d["generated_at"] > 0)

    # ── 16. OOD 拒答（A15 运行时落地，2026-10-08 新增）──────────────
    # 合成一个 2D 训练集：密集铺在 [0,1]^2
    tr = np.array([[i / 20.0, j / 20.0]
                   for i in range(21) for j in range(21)])
    thr = ood_threshold_from_train(tr)
    check("ood threshold finite for dense grid", np.isfinite(thr))
    check("ood threshold positive", thr > 0)

    # 训练点太少 → 无法判定 → 保守返回 inf（全拒）
    check("too few points -> inf (conservative)",
          not np.isfinite(ood_threshold_from_train(tr[:2])))

    # 域内查询点 → 不拒答
    p_in = propose_experiment(_row(), _nexp(), None,
                              train_X=tr, query_x=np.array([[0.5, 0.5]]))
    check("in-domain query not flagged OOD", p_in.boundary.query_ood is False)
    check("in-domain keeps interval width", p_in.conclusion.ci_width > 0)
    check("in-domain valid_when not OOD-rejected",
          "OOD 拒答" not in p_in.boundary.valid_when)

    # 域外查询点 → **真拒答**：区间塌缩 + valid_when 明说
    p_out = propose_experiment(_row(), _nexp(), None,
                               train_X=tr, query_x=np.array([[9.0, 9.0]]))
    check("out-of-domain query flagged OOD", p_out.boundary.query_ood is True)
    check("OOD rejection collapses CI (refuse to give a number)",
          p_out.conclusion.ci_width == 0.0)
    check("OOD valid_when says 不适用",
          "不适用" in p_out.boundary.valid_when)
    check("OOD notes mention 拒答", "OOD 拒答" in p_out.conclusion.notes)
    check("OOD fails_when cites A15 mean-collapse",
          "训练集均值" in p_out.boundary.fails_when)
    check("OOD threshold recorded",
          p_out.boundary.ood_threshold is not None
          and p_out.boundary.ood_threshold > 0)
    check("OOD query_proximity recorded",
          p_out.boundary.query_proximity is not None
          and p_out.boundary.query_proximity > p_out.boundary.ood_threshold)

    # 域外建议点 → ood_flagged=True + rationale 改成「先扩边界」
    nex_ood = [{"point": [9.0, 9.0], "info_gain": 0.9, "std": 1.0,
                "proximity": 5.0, "n_ood_rejected": 0}]
    p_nex = propose_experiment(_row(), nex_ood, None, train_X=tr)
    check("OOD next_experiment flagged",
          p_nex.next_experiment.ood_flagged is True)
    check("OOD next_experiment says expand-boundary-first",
          "扩边界" in p_nex.next_experiment.rationale)
    check("OOD next_experiment falsification changed",
          "最大不确定性处补点" in p_nex.next_experiment.falsification_test)

    # 域内建议点 → 不 flag
    nex_in = [{"point": [0.5, 0.5], "info_gain": 0.1, "std": 0.01,
               "proximity": 0.001, "n_ood_rejected": 0}]
    p_ok = propose_experiment(_row(), nex_in, None, train_X=tr)
    check("in-domain next_experiment not flagged",
          p_ok.next_experiment.ood_flagged is False)

    # 不给 train_X → 不假装判过（None 而不是 False）
    p_noinfo = propose_experiment(_row(), _nexp(), None)
    check("no train_X -> query_ood is None (not faked)",
          p_noinfo.boundary.query_ood is None)
    check("no train_X -> ood_threshold is None",
          p_noinfo.boundary.ood_threshold is None)
    check("no train_X -> still usable in-domain",
          p_noinfo.conclusion.ci_width > 0)

    print(f"\n{passed}/{total} experiment-proposal self-tests passed")
    return passed == total


if __name__ == "__main__":
    import sys
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if _selftest():
        res = build_all(repo_root=root)
        out = save(res, default_output_path(root))
        s = res["summary"]
        print(f"\n实验输出接口 → {out}")
        print("=" * 70)
        print(f"  场景数            {res['n_scenarios']}")
        print(f"  有实验建议        {res['n_with_next_experiment']}"
              f"  ({s['has_next_experiment_rate']*100:.1f}%)")
        print(f"  actionable        {res['n_actionable']}"
              f"  ({s['actionable_rate']*100:.1f}%)")
        if res.get("import_error"):
            print(f"  [warn] 场景表导入失败：{res['import_error']}")
        print(f"\n  {s['note']}")
        # 展示一个可 actionable 的例子
        ex = next((x for x in res["proposals"] if x["is_actionable"]), None)
        if ex:
            print(f"\n  ── 示例（{ex['key']}）──")
            c = ex["conclusion"]
            print(f"     结论     {c['value']} [{c['ci_low']}, {c['ci_high']}]"
                  f"  rel={c['rmse_rel']} cov={c['coverage']}")
            print(f"     边界     {ex['boundary']['valid_when'][:60]}")
            ne = ex["next_experiment"]
            print(f"     下一步   {ne['point']}  priority={ne['priority']:.3f}"
                  f" proximity={ne['proximity']}")
            pred_str = (f"{ne['predicted_rmse_rel']}"
                        if ne['predicted_rmse_rel'] is not None else "未知")
            ratio = ne['predicted_improvement_ratio']
            ratio_str = (f"{ratio}×" if ratio is not None else "未知")
            print(f"     预测     rel {ne['current_rmse_rel']} → "
                  f"{pred_str}"
                  f"（{ratio_str}, "
                  f"confidence={ne['confidence']}）")
            print(f"     证伪     {ne['falsification_test'][:70]}")
        sys.exit(0)
    sys.exit(1)
