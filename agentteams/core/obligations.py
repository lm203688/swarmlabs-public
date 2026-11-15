"""Obligation Graph —— 义务图 + 显式验收语义。

借鉴来源
--------
Eureka 元代理架构（arXiv 2608.19047, ManXis/广工, 2026）的两项机制：

1. **Obligation graph（义务图）**：代理任务不是"做完了就算"，而是携带一张
   *机器可判定*的 obligation 清单；任务之间的依赖也是显式图，而非隐式约定。
2. **Explicit acceptance semantics（显式验收语义）**：验收标准是一组可执行的
   断言，不是散文式描述；由独立 verifier 复核，未满足则**不允许**进入完成态。
   Eureka 报告 3948 份可验证证书、零误接受（zero false accepts）——靠的就是这层。

为什么我方需要
--------------
我方已有两块"判定"资产，但此前**没有串成闭环**：

- `core/trust_layer.py` 的 7 项 rubric（gold_source_traced / rmse_rel_ok /
  coverage_two_sided_ok / cal_err_ok / ls_source_traced / sharpness_ok /
  function_nonconstant）——可机器判定的**质量断言**；
- `agentteams/core/board.py` 的五态机——可落盘的**状态机**，但"完成"此前由
  `WorkerResult.status == "ok"` **单方声明**，没有独立复核。

本模块把二者焊死：任务在落 `completed` 前，其义务必须由 evidence 独立满足，
否则**强制落 `blocked`**，未满足项写入 reason 与审计链。

零破坏保证
----------
义务是**可选项**：`TaskCard.obligations` 默认为空，空义务集不做任何拦截，
`transition()` 行为与改造前完全一致。只有显式 `attach_obligations()` 才启用。

evidence 约定
-------------
    {
      "rubric":    {rubric_key: bool, ...},   # 来自 trust_surface 行的 rubric 字典
      "metrics":   {"coverage_after": 0.95, "cal_err_after": 0.02, ...},
      "artifacts": {"<label>": "/abs/or/rel/path", ...},
      "gates":     {"labguard": "allow", ...},
    }

可用 `ObligationEvidence.from_trust_row(row)` 直接从 trust_surface.json 的行构造。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# ── 义务种类 ────────────────────────────────────────────────────────
KIND_RUBRIC = "rubric"      # 绑定 trust_layer rubric 键：断言该项为 True
KIND_METRIC = "metric"      # 绑定数值指标 + 比较算子
KIND_ARTIFACT = "artifact"  # 绑定产物文件：存在且非空
KIND_GATE = "gate"          # 绑定门禁状态：必须落在允许集合内

# ── 算子表（显式验收语义的"可执行断言"部分）─────────────────────────
def _op_true(a, b): return bool(a) is True
def _op_false(a, b): return bool(a) is False
def _op_eq(a, b): return a == b
def _op_ne(a, b): return a != b
def _op_lt(a, b): return a is not None and a < b
def _op_lte(a, b): return a is not None and a <= b
def _op_gt(a, b): return a is not None and a > b
def _op_gte(a, b): return a is not None and a >= b
def _op_exists(a, b): return bool(a)
def _op_in(a, b): return a in (b or [])

_OPS = {
    "true": _op_true, "false": _op_false,
    "eq": _op_eq, "ne": _op_ne,
    "lt": _op_lt, "lte": _op_lte, "gt": _op_gt, "gte": _op_gte,
    "exists": _op_exists, "in": _op_in,
}

# 每个 kind 在 evidence 中的取值路径
_EVIDENCE_KEY = {
    KIND_RUBRIC: "rubric",
    KIND_METRIC: "metrics",
    KIND_ARTIFACT: "artifacts",
    KIND_GATE: "gates",
}


@dataclass(frozen=True)
class Obligation:
    """一条可机器判定的义务。

    示例
    ----
        Obligation("coverage_ok", KIND_RUBRIC, "coverage_two_sided_ok",
                   op="true", desc="95% 区间双侧覆盖率必须达标")
        Obligation("cal_tight", KIND_METRIC, "cal_err_after",
                   op="lte", value=0.08, desc="校准误差 ≤ 0.08")
    """

    id: str
    kind: str
    target: str
    op: str = "true"
    value: Any = True
    desc: str = ""

    def __post_init__(self):
        if self.kind not in _EVIDENCE_KEY:
            raise ValueError(f"未知义务种类 {self.kind!r}，可选 {sorted(_EVIDENCE_KEY)}")
        if self.op not in _OPS:
            raise ValueError(f"未知算子 {self.op!r}，可选 {sorted(_OPS)}")

    def check(self, evidence: Dict[str, Any]) -> "ObligationResult":
        """用 evidence 判定本条义务。evidence 缺失 → 判为未满足（保守）。"""
        bucket = _EVIDENCE_KEY[self.kind]
        src = (evidence or {}).get(bucket) or {}
        observed = src.get(self.target) if isinstance(src, dict) else None

        if self.kind == KIND_ARTIFACT:
            # 产物义务：路径存在且非空字节
            path = observed
            if not path:
                return ObligationResult(self.id, False, None,
                                        f"产物 {self.target!r} 未提供路径")
            if not os.path.exists(path):
                return ObligationResult(self.id, False, path,
                                        f"产物 {self.target!r} 不存在：{path}")
            size = os.path.getsize(path)
            if size <= 0:
                return ObligationResult(self.id, False, path,
                                        f"产物 {self.target!r} 为空文件：{path}")
            return ObligationResult(self.id, True, path,
                                    f"产物存在（{size} 字节）")

        if observed is None:
            return ObligationResult(
                self.id, False, None,
                f"缺少证据 {bucket}.{self.target}（无法判定 → 按未满足处理）")

        ok = bool(_OPS[self.op](observed, self.value))
        shown = observed if not isinstance(observed, float) else round(observed, 4)
        detail = (f"{bucket}.{self.target}={shown} "
                  f"{'满足' if ok else '不满足'} {self.op} {self.value}")
        return ObligationResult(self.id, ok, observed, detail)

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "target": self.target,
                "op": self.op, "value": self.value, "desc": self.desc}


@dataclass
class ObligationResult:
    obligation_id: str
    satisfied: bool
    observed: Any = None
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"obligation": self.obligation_id, "satisfied": self.satisfied,
                "observed": self.observed, "detail": self.detail}


@dataclass
class ObligationReport:
    """一份验收报告——对应 Eureka 的"可验证证书"。"""

    task_id: str = ""
    results: List[ObligationResult] = field(default_factory=list)

    @property
    def satisfied(self) -> List[ObligationResult]:
        return [r for r in self.results if r.satisfied]

    @property
    def unsatisfied(self) -> List[ObligationResult]:
        return [r for r in self.results if not r.satisfied]

    @property
    def all_satisfied(self) -> bool:
        return bool(self.results) and not self.unsatisfied

    @property
    def verdict(self) -> str:
        if not self.results:
            return "no_obligations"
        return "accepted" if self.all_satisfied else "rejected"

    def summary(self) -> str:
        if not self.results:
            return "无义务（不拦截）"
        if self.all_satisfied:
            return f"义务 {len(self.satisfied)}/{len(self.results)} 全部满足"
        parts = [f"{r.obligation_id}[{r.detail}]" for r in self.unsatisfied]
        return (f"义务未满足 {len(self.unsatisfied)}/{len(self.results)}："
                + "; ".join(parts))

    def to_dict(self) -> Dict[str, Any]:
        return {"task_id": self.task_id, "verdict": self.verdict,
                "n_obligations": len(self.results),
                "n_satisfied": len(self.satisfied),
                "summary": self.summary(),
                "results": [r.to_dict() for r in self.results]}


class ObligationSet:
    """一组义务 + 批量评估。"""

    def __init__(self, obligations: Optional[List[Obligation]] = None):
        self.obligations: List[Obligation] = list(obligations or [])

    def add(self, ob: Obligation) -> "ObligationSet":
        self.obligations.append(ob)
        return self

    def evaluate(self, evidence: Dict[str, Any], task_id: str = "") -> ObligationReport:
        return ObligationReport(
            task_id=task_id,
            results=[ob.check(evidence) for ob in self.obligations],
        )

    def __len__(self) -> int:
        return len(self.obligations)

    def __bool__(self) -> bool:
        return bool(self.obligations)

    def to_dict(self) -> List[Dict[str, Any]]:
        return [ob.to_dict() for ob in self.obligations]


# ── evidence 构造助手 ───────────────────────────────────────────────
_METRIC_KEYS = (
    "coverage_after", "coverage_gaussian", "cal_err_after", "rmse_rel",
    "rmse_after", "sharpness_nmpiw", "conformal_q", "extrap_std_ratio",
    "n_store", "rubric_passed", "rubric_total",
)


# ── rubric 判定真源 ─────────────────────────────────────────────────
# 这 7 项是「可信」的口径定义，与 core/trust_layer.RUBRIC_KEYS 一一对应。
# 放在这里是刻意的：义务验收与公开可信面必须共用**同一套**判定，
# 否则"证书说通过、义务说没过"的漂移会让两边都失去意义。
# scripts/paper_validation_loop.py 的 trust_rows() 已改为调用 derive_rubric()。
RUBRIC_THRESHOLDS: Dict[str, float] = {
    "rel_rmse_max": 0.05,      # rmse_rel_ok
    "cov_min": 0.85,           # coverage_two_sided_ok（下界，欠覆盖 = 过自信）
    "cov_max": 1.00,           # coverage_two_sided_ok（上界，过覆盖 = 区间过宽）
    "cal_err_max": 0.08,       # cal_err_ok
    "sharpness_cap": 2.0,      # sharpness_ok（NMPIW 上限）
    "degenerate_y_std": 1e-12, # function_nonconstant（训练 y 标准差下限）
}


def _f(x: Any, default: float = 0.0) -> float:
    """安全 float：None / NaN / 非数值一律回落默认值（判定时"取不到"按最差处理）。"""
    try:
        v = float(x)
        return v if v == v else default
    except Exception:                                   # noqa: BLE001
        return default


def derive_rubric(row: Dict[str, Any],
                  thresholds: Optional[Dict[str, float]] = None) -> Dict[str, bool]:
    """从一条 validate_scenario 结果（或 trust_surface 行）判定 7 项 rubric。

    这是**唯一**的判定实现：可信面报告、AgentTeams 义务验收、证书模块
    都走这里，杜绝"三处各写一遍阈值"的口径漂移。
    """
    t = dict(RUBRIC_THRESHOLDS)
    if thresholds:
        t.update(thresholds)

    # rel：优先用原始 (rmse_after, scale) 复算，与 trust_rows 完全同式；
    # 存档行若没有 scale（旧数据），退回已归一化的 rmse_rel。
    if row.get("rmse_after") is not None and row.get("scale") is not None:
        rel = _f(row.get("rmse_after")) / max(_f(row.get("scale"), 1.0), 1e-9)
    else:
        rel = _f(row.get("rmse_rel"))
    cov = _f(row.get("coverage_after"))
    cal = _f(row.get("cal_err_after"))
    nmpiw = _f(row.get("sharpness_nmpiw", row.get("interval_width")))
    y_std = _f(row.get("y_train_std"))
    # 退化：优先用已存档的判定；否则从 y_train_std 现算（validate_scenario 原始输出）
    if "degenerate" in row:
        degenerate = bool(row.get("degenerate"))
    else:
        degenerate = y_std < t["degenerate_y_std"]

    return {
        "gold_source_traced": bool(row.get("published")),
        "rmse_rel_ok": rel <= t["rel_rmse_max"],
        "coverage_two_sided_ok": t["cov_min"] <= cov <= t["cov_max"],
        "cal_err_ok": cal <= t["cal_err_max"],
        "ls_source_traced": bool(row.get("ls_source")),
        "sharpness_ok": nmpiw <= t["sharpness_cap"],
        "function_nonconstant": not degenerate,
    }


def evidence_from_trust_row(row: Dict[str, Any],
                            artifacts: Optional[Dict[str, str]] = None,
                            gates: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """把 trust_surface.json 的一行转成义务判定所需的 evidence。

    这样义务可以直接绑定到**真实发货的可信面数据**，而不是另起一套指标口径。
    """
    return {
        "rubric": dict(row.get("rubric") or derive_rubric(row)),
        "metrics": {k: row.get(k) for k in _METRIC_KEYS if row.get(k) is not None},
        "artifacts": dict(artifacts or {}),
        "gates": dict(gates or {}),
    }


def evidence_from_trust_rows(rows: List[Dict[str, Any]],
                             artifacts: Optional[Dict[str, str]] = None,
                             gates: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """多场景聚合 evidence（一个任务常跑 N 个场景）。

    聚合语义是 **worst-of（取最差）**：
      - rubric 各项 = 所有场景的 AND（任一场景不过 → 该项不过）
      - coverage 取 min、cal_err / rmse_rel / nmpiw 取 max

    这是刻意的严格：任务级验收"有短板即不过"，对应 Eureka 的零误接受取向——
    宁可拦下人工复核，也不放一个"平均还行、个别翻车"的任务进完成态。
    """
    rows = [r for r in (rows or []) if isinstance(r, dict)]
    if not rows:
        return {"rubric": {}, "metrics": {},
                "artifacts": dict(artifacts or {}), "gates": dict(gates or {})}

    keys = sorted(set().union(*[set(derive_rubric(r).keys()) for r in rows]))
    rubric = {k: all(derive_rubric(r).get(k, False) for r in rows) for k in keys}

    def _worst(field_getter, agg, default=None):
        vals = [v for v in (field_getter(r) for r in rows) if v is not None]
        return agg(vals) if vals else default

    metrics = {
        "coverage_after": _worst(lambda r: r.get("coverage_after"), lambda v: min(v)),
        "cal_err_after": _worst(lambda r: r.get("cal_err_after"), lambda v: max(v)),
        "rmse_rel": _worst(lambda r: r.get("rmse_rel"), lambda v: max(v)),
        "sharpness_nmpiw": _worst(
            lambda r: r.get("sharpness_nmpiw", r.get("interval_width")), lambda v: max(v)),
    }
    metrics = {k: v for k, v in metrics.items() if v is not None}
    metrics["n_scenarios"] = len(rows)
    return {"rubric": rubric, "metrics": metrics,
            "artifacts": dict(artifacts or {}), "gates": dict(gates or {})}


# ── 角色默认义务（显式验收语义的"组织部分"）─────────────────────────
# 阈值与 core/trust_layer.py 的 rubric 口径保持一致；改动 rubric 时必须同步此处。
ROLE_OBLIGATIONS: Dict[str, List[Obligation]] = {
    "executor": [
        Obligation("coverage_two_sided", KIND_RUBRIC, "coverage_two_sided_ok",
                   op="true", desc="95% 区间双侧覆盖率必须达标（欠覆盖=过自信）"),
        Obligation("function_nonconstant", KIND_RUBRIC, "function_nonconstant",
                   op="true", desc="ground-truth 不得在训练域退化为常数"),
        Obligation("cal_err_bounded", KIND_METRIC, "cal_err_after",
                   op="lte", value=0.08, desc="校准误差 ≤ 0.08"),
    ],
    "verifier": [
        Obligation("gold_provenance", KIND_RUBRIC, "gold_source_traced",
                   op="true", desc="gold 来源必须可溯源到已发表数据"),
        Obligation("ls_provenance", KIND_RUBRIC, "ls_source_traced",
                   op="true", desc="长度尺度来源必须可追溯（prior / auto）"),
        Obligation("sharpness", KIND_RUBRIC, "sharpness_ok",
                   op="true", desc="区间锐度达标（不能靠拉宽误差棒蒙混）"),
    ],
}


def obligations_for_role(role: str) -> List[Obligation]:
    """取角色默认义务的**副本**（避免跨任务共享可变列表）。"""
    return [Obligation(**ob.to_dict()) for ob in ROLE_OBLIGATIONS.get(role, [])]
