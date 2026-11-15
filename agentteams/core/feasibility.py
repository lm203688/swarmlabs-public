"""FeasibilityChecker —— 派发前可行性预校验。

借鉴来源
--------
UPenn GRASP Lab 的 **SPINE-HT**（IROS 2026）：LLM 生成 grounded 子任务后，
**先做可行性预校验，再按能力分配**，而不是让机器人跑到现场才发现任务不可能。
论文报告 87% 成功率，可行性前置拦截是其中的关键环节之一。

为什么本项目需要
----------------
改造前的链路是：`planner` 产计划 → `executor` 跑实验 → 失败。不可行的任务
（场景不存在、角色未注册、依赖悬空、bounds 反了）要跑到下游才暴露，浪费的是
真实的 GP 拟合算力，而且错误信息出现在错误的层——是 executor 报 KeyError，
而不是 planner 说「这个场景我没有」。

预校验把这些前移到派发前，产出结构化裁定：
  - **feasible**   ：可派发
  - **infeasible** ：结构性不可行（场景不存在 / 角色缺失 / 依赖悬空 / 依赖成环）
                     → 直接标 blocked，不派发
  - **degraded**   ：可派发但需降级（请求的部分场景不存在，其余仍可跑）

设计原则
--------
- **零引擎依赖**：本模块只做结构与元数据校验，不 import numpy、不跑 GP。
  真正的数值可信性由 `engine/guard.py` 的三层门禁负责，两者分工不重叠。
- **不改任务**：只裁定不重写。降级方案作为建议返回，是否采纳由 Leader 决定。
- **诚实**：不可行就说不可行，绝不静默把未知场景当默认场景跑。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence

FEASIBLE = "feasible"
DEGRADED = "degraded"
INFEASIBLE = "infeasible"


@dataclass
class FeasibilityVerdict:
    """一次预校验的裁定结果。"""

    status: str                                     # feasible / degraded / infeasible
    reasons: List[str] = field(default_factory=list)        # 不可行 / 降级的原因
    checks: Dict[str, str] = field(default_factory=dict)    # 逐项检查明细
    blocked_tasks: List[str] = field(default_factory=list)  # 应标 blocked 的 task_id
    dropped_scenarios: List[str] = field(default_factory=list)
    kept_scenarios: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """可否派发。degraded 仍可派发（部分能力可用）。"""
        return self.status != INFEASIBLE

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "ok": self.ok,
            "reasons": self.reasons,
            "checks": self.checks,
            "blocked_tasks": self.blocked_tasks,
            "dropped_scenarios": self.dropped_scenarios,
            "kept_scenarios": self.kept_scenarios,
        }

    def summary(self) -> str:
        if self.status == FEASIBLE:
            return "可行性预校验通过"
        head = "结构性不可行" if self.status == INFEASIBLE else "可行但需降级"
        return f"{head}：" + "；".join(self.reasons)


class FeasibilityChecker:
    """派发前预校验器。

    用法
    ----
        chk = FeasibilityChecker(known_roles=set(workers))
        verdict = chk.check(tasks, requested_scenarios=req.get("scenarios"),
                            catalog_keys=None)     # catalog 未知时跳过场景检查
        if not verdict.ok:
            # 标 blocked，不派发
    """

    def __init__(self, known_roles: Optional[Iterable[str]] = None,
                 catalog_keys: Optional[Iterable[str]] = None,
                 valid_risks: Sequence[str] = ("low", "medium", "high")):
        self.known_roles = set(known_roles or ())
        self.catalog_keys = set(catalog_keys) if catalog_keys is not None else None
        self.valid_risks = tuple(valid_risks)

    # ── 主入口 ──────────────────────────────────────────────────────
    def check(self, tasks: Sequence[Any],
              requested_scenarios: Optional[Sequence[str]] = None,
              bounds: Optional[Sequence[Sequence[float]]] = None,
              catalog_keys: Optional[Iterable[str]] = None) -> FeasibilityVerdict:
        v = FeasibilityVerdict(status=FEASIBLE)
        keys = set(catalog_keys) if catalog_keys is not None else self.catalog_keys

        self._check_roles(tasks, v)
        self._check_dependencies(tasks, v)
        self._check_risk(tasks, v)
        self._check_scenarios(requested_scenarios, keys, v)
        if bounds is not None:
            self._check_bounds(bounds, v)

        # 汇总：有 blocked_tasks → infeasible；只有场景丢弃 → degraded
        if v.blocked_tasks:
            v.status = INFEASIBLE
        elif v.dropped_scenarios:
            v.status = DEGRADED
        elif v.reasons:
            v.status = DEGRADED
        return v

    # ── 分项检查 ────────────────────────────────────────────────────
    def _check_roles(self, tasks, v: FeasibilityVerdict):
        """角色必须已注册，否则 runtime 会返回 error 但已浪费一次派发。"""
        if not self.known_roles:
            v.checks["roles"] = "skip（未提供已注册角色集）"
            return
        missing = []
        for t in tasks:
            role = getattr(t, "role", None)
            if role and role not in self.known_roles:
                missing.append((getattr(t, "task_id", "?"), role))
        if missing:
            for tid, role in missing:
                v.blocked_tasks.append(tid)
                v.reasons.append(f"任务 {tid} 的角色 «{role}» 未注册")
            v.checks["roles"] = f"fail（{len(missing)} 个任务角色缺失）"
        else:
            v.checks["roles"] = f"pass（{len(self.known_roles)} 个角色已注册）"

    def _check_dependencies(self, tasks, v: FeasibilityVerdict):
        """依赖必须闭合（指向的 task 存在）且无环。"""
        ids = {getattr(t, "task_id", None) for t in tasks}
        dangling = []
        for t in tasks:
            tid = getattr(t, "task_id", "?")
            for dep in (getattr(t, "depends_on", ()) or ()):
                if dep not in ids:
                    dangling.append((tid, dep))
        if dangling:
            for tid, dep in dangling:
                if tid not in v.blocked_tasks:
                    v.blocked_tasks.append(tid)
                v.reasons.append(f"任务 {tid} 依赖不存在的 «{dep}»")

        cycle = self._find_cycle(tasks)
        if cycle:
            for tid in cycle:
                if tid not in v.blocked_tasks:
                    v.blocked_tasks.append(tid)
            v.reasons.append("任务依赖成环：" + " → ".join(cycle))

        if dangling or cycle:
            v.checks["dependencies"] = "fail（悬空依赖或成环）"
        else:
            v.checks["dependencies"] = f"pass（{len(tasks)} 个任务 DAG 闭合）"

    @staticmethod
    def _find_cycle(tasks) -> List[str]:
        """返回首个发现的环路（节点列表），无环返回 []。"""
        by_id = {getattr(t, "task_id", None): t for t in tasks}
        state: Dict[str, int] = {}                 # 0=未访 1=在栈 2=已完成
        path: List[str] = []

        def dfs(tid) -> List[str]:
            if state.get(tid, 0) == 1:
                idx = path.index(tid)
                return path[idx:] + [tid]
            if state.get(tid, 0) == 2 or tid not in by_id:
                return []
            state[tid] = 1
            path.append(tid)
            for dep in (getattr(by_id[tid], "depends_on", ()) or ()):
                found = dfs(dep)
                if found:
                    return found
            path.pop()
            state[tid] = 2
            return []

        for tid in list(by_id):
            found = dfs(tid)
            if found:
                return found
        return []

    def _check_risk(self, tasks, v: FeasibilityVerdict):
        """risk 必须是合法枚举值，否则 policy.gate 的判定会走到未定义分支。"""
        bad = [(getattr(t, "task_id", "?"), getattr(t, "risk", None))
               for t in tasks
               if getattr(t, "risk", "low") not in self.valid_risks]
        if bad:
            for tid, r in bad:
                v.reasons.append(f"任务 {tid} 的 risk «{r}» 非法（合法：{self.valid_risks}）")
            v.checks["risk"] = f"fail（{len(bad)} 个非法 risk）"
        else:
            v.checks["risk"] = "pass"

    def _check_scenarios(self, requested, catalog_keys, v: FeasibilityVerdict):
        """请求的场景必须在目录里。全都不存在 → infeasible；部分不存在 → degraded。"""
        if not requested:
            v.checks["scenarios"] = "skip（未显式指定场景，由 planner 按优先队列选题）"
            return
        if catalog_keys is None:
            v.checks["scenarios"] = "skip（目录未提供，延后到 planner 校验）"
            return
        kept = [k for k in requested if k in catalog_keys]
        dropped = [k for k in requested if k not in catalog_keys]
        v.kept_scenarios = kept
        v.dropped_scenarios = dropped
        if dropped:
            v.reasons.append(f"场景不在目录中，已丢弃：{dropped}")
        if not kept:
            v.reasons.append("请求的场景全部不存在，无可执行实验")
            v.checks["scenarios"] = "fail（全部未知）"
            # 全不可行：把执行/验证任务标 blocked
            v.status = INFEASIBLE
        elif dropped:
            v.checks["scenarios"] = f"degraded（保留 {len(kept)} / 丢弃 {len(dropped)}）"
        else:
            v.checks["scenarios"] = f"pass（{len(kept)} 个场景全部命中目录）"

    def _check_bounds(self, bounds, v: FeasibilityVerdict):
        """bounds 必须是 (lo, hi) 且 lo < hi；反了会让主动学习采样退化。"""
        bad = []
        for i, b in enumerate(bounds or []):
            try:
                lo, hi = float(b[0]), float(b[1])
            except Exception:                      # noqa: BLE001
                bad.append(f"dim{i} 格式非法 ({b!r})")
                continue
            if not (lo < hi):
                bad.append(f"dim{i} 下界 {lo} 不小于上界 {hi}")
        if bad:
            v.reasons.extend(bad)
            v.checks["bounds"] = f"fail（{len(bad)} 维非法）"
        else:
            v.checks["bounds"] = f"pass（{len(bounds or [])} 维合法）"
