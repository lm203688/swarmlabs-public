#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Harness 自我进化引擎 —— 把 42 门禁从"人工维护"变成"可自我修复"。

为什么需要这一层
---------------
SwarmLabs 的 42 门禁 + 5 独立不变量 + 6 preflight 是**人工维护**的。当
用户反馈说"这个门禁在 X 场景下误拒了有效场景"或"应该补一条门禁抓 Y 情况"
时，现在只能人工改代码——这有两个致命问题：

1. **慢**：每次修改都是 PR + 42 门禁全跑 + 人工审阅，从"发现"到"上线"
   是数天到数周。
2. **退化不可见**：一次修改可能让整体 V&V 变差 0.1%，但单次看不出，
   一年累积下来就是 5 个百分点的隐性退化。

参考实现：ScienceBuddy (arXiv:2609.17523, 2026-09-15, PhAI Labs) 的
**内层递归**——固定模型，只改 harness；每次候选修改必须在**相同开发
任务集**上得分为正才接受，否则拒绝。这个"配对评测 ΔS≥0"接受规则是
本模块的核心设计。

三层架构
-------
  1. **Feedback** — 用户/系统反馈（gap / false_positive / coverage）
  2. **Candidate** — 候选修改（新增 / 修改 / 删除门禁）
  3. **Evaluator** — 配对评测（在基准场景集上打分）
  4. **Decider**   — ΔS ≥ 0 才接受，否则拒绝

设计纪律
-------
1. **只改 harness，不改引擎**：V&V 引擎、GP+UQ、锚点库都不变，
   只改门禁集合本身。
2. **配对评测**：候选必须与当前版本在**相同场景集**上比较，
   不允许"换个场景集显得更好"这种作弊。
3. **版本化 + 可回滚**：每次接受都产生新版本，历史版本可查可回滚。
4. **审计日志**：每次拒绝的候选也保留，说明为什么被拒。
5. **纯 stdlib**：不依赖 numpy / scipy / pandas，可以在任何
   Python 3.10+ 环境跑。

对齐：ScienceBuddy 的"harness 自进化"是他们的"内层递归"，
本模块是这个思路在**验证层**上的移植。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Dict, List, Optional, Tuple

_SCHEMA = "swarmlabs-harness-evolver/1.0"
_HERE = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------------------
# 核心数据结构
# ---------------------------------------------------------------------------

#: 反馈类型 —— 借用 ScienceBuddy 的 rubric 思想
#: 每种反馈都能映射到一个明确的 harness 修改方向
FeedbackType = str  # 取值：gap / false_positive / coverage / robustness

@dataclass(frozen=True)
class Feedback:
    """单条反馈：用户/系统对当前 harness 的观察。"""
    fb_id: str
    feedback_type: FeedbackType
    scenario_id: str
    message: str
    #: 建议方向；可选，LLM 可生成
    suggestion: str = ""
    ts: int = field(default_factory=lambda: int(time.time()))

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Gate:
    """单条门禁：一个可执行的布尔判定。

    对应现有 42 门禁中的一条，例如：
        - N31: decision_relevant anchors must be 3D complete
        - P6:  no DR anchor may have fewer than 3 dims
        - N1:  V&V r2 >= 0.90
    """
    id: str
    category: str  # anchoring / vv_metric / coverage / negation / ...
    description: str
    predicate: Callable[[Dict[str, Any]], bool]
    #: 是否是"否定性门禁"（ScienceBuddy 语境下的 scoped skill）
    negation: bool = False

    def evaluate(self, scenario: Dict[str, Any]) -> bool:
        """执行门禁。异常视为不通过（fail-closed）。"""
        try:
            return bool(self.predicate(scenario))
        except Exception:
            return False

    def to_dict(self, include_pred: bool = False) -> Dict[str, Any]:
        d = {
            "id": self.id,
            "category": self.category,
            "description": self.description,
            "negation": self.negation,
        }
        if include_pred:
            d["predicate_repr"] = getattr(self.predicate, "__name__", "?")
        return d


@dataclass
class ScenarioResult:
    """单个场景在单个 harness 下的评测结果。"""
    scenario_id: str
    #: gate_id -> pass/fail
    gate_results: Dict[str, bool]
    #: 场景级结果：PASS / MARGINAL / REFUTED / ERROR
    vv_result: str = "PASS"
    #: 场景级分数（0..1，越高越好）
    score: float = 1.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Harness:
    """一个版本的 harness：门禁集合 + 元数据。"""
    version: str
    gates: List[Gate]
    #: 综合评分（在基准场景集上的平均）
    score: float = 0.0
    #: 场景级结果缓存
    per_scenario: Dict[str, ScenarioResult] = field(default_factory=dict)
    #: 创建时间
    created_at: int = field(default_factory=lambda: int(time.time()))
    #: 上一版本 ID（用于回滚链路）
    parent_version: str = ""

    def gate_ids(self) -> List[str]:
        return [g.id for g in self.gates]

    def fingerprint(self) -> str:
        """稳定指纹：内容哈希，与创建时间无关。"""
        parts = []
        for g in self.gates:
            parts.append(f"{g.id}|{g.category}|{g.negation}|{g.description}")
        return hashlib.sha256("\n".join(sorted(parts)).encode()).hexdigest()[:16]

    def to_dict(self, include_pred: bool = False) -> Dict[str, Any]:
        return {
            "schema": _SCHEMA,
            "version": self.version,
            "parent_version": self.parent_version,
            "score": self.score,
            "created_at": self.created_at,
            "fingerprint": self.fingerprint(),
            "gate_count": len(self.gates),
            "gates": [g.to_dict(include_pred) for g in self.gates],
        }


# ---------------------------------------------------------------------------
# 评测器
# ---------------------------------------------------------------------------

#: 场景集加载钩子；外部可替换成真实数据
ScenarioLoader = Callable[[], List[Dict[str, Any]]]


def default_scenario_loader() -> ScenarioLoader:
    """占位场景加载器：返回空列表。

    真实使用时应替换为 `scripts/vlab.py` 里加载的 82 场景基准。
    这里保留空实现，让 harness_evolver 可以被独立单元测试。
    """
    return []


def evaluate_harness(harness: Harness, scenarios: List[Dict[str, Any]]) -> Harness:
    """在一个场景集上跑 harness，返回填充了结果的副本。

    评分规则（简单版，可后续替换）：
    - 每个场景所有门禁都过 = PASS，score = 1.0
    - 部分门禁不过 = MARGINAL，score = 通过比例 * 0.8
    - 超过半数不过 = REFUTED，score = 通过比例 * 0.4
    - 门禁全部不过 = ERROR，score = 0.0
    - harness 总分数 = 场景分数平均
    """
    if not scenarios:
        # 空场景集：保留原分数，不做覆盖
        return harness

    per_scenario: Dict[str, ScenarioResult] = {}
    total_score = 0.0

    for sc in scenarios:
        sc_id = sc.get("id", sc.get("scenario_id", "?"))
        results = {}
        passed = 0
        for g in harness.gates:
            ok = g.evaluate(sc)
            results[g.id] = ok
            if ok:
                passed += 1
        n = len(harness.gates)
        ratio = passed / n if n else 0.0

        if n == 0:
            vv, score = "PASS", 1.0
        elif ratio == 1.0:
            vv, score = "PASS", 1.0
        elif ratio >= 0.5:
            vv, score = "MARGINAL", ratio * 0.8
        elif ratio > 0.0:
            vv, score = "REFUTED", ratio * 0.4
        else:
            vv, score = "ERROR", 0.0

        per_scenario[sc_id] = ScenarioResult(
            scenario_id=sc_id,
            gate_results=results,
            vv_result=vv,
            score=score,
        )
        total_score += score

    avg_score = total_score / len(scenarios)
    return Harness(
        version=harness.version,
        gates=harness.gates,
        score=avg_score,
        per_scenario=per_scenario,
        created_at=harness.created_at,
        parent_version=harness.parent_version,
    )


# ---------------------------------------------------------------------------
# 候选修改器
# ---------------------------------------------------------------------------

@dataclass
class Candidate:
    """一个候选修改：从当前 harness 生成的新版本。"""
    base_version: str
    target_version: str
    feedback_ids: List[str]
    #: 新增的门禁
    added: List[Gate] = field(default_factory=list)
    #: 要删除的门禁 ID
    removed: List[str] = field(default_factory=list)
    #: 修改的门禁（老 ID -> 新 Gate）
    modified: Dict[str, Gate] = field(default_factory=dict)
    rationale: str = ""


def apply_candidate(base: Harness, cand: Candidate) -> Harness:
    """把候选修改应用到 base，产生新版本 harness。"""
    gates: List[Gate] = []
    removed_set = set(cand.removed)

    for g in base.gates:
        if g.id in removed_set:
            continue
        if g.id in cand.modified:
            gates.append(cand.modified[g.id])
        else:
            gates.append(g)
    gates.extend(cand.added)

    # 去重（按 id 保序）
    seen = set()
    deduped: List[Gate] = []
    for g in gates:
        if g.id not in seen:
            deduped.append(g)
            seen.add(g.id)

    return Harness(
        version=cand.target_version,
        gates=deduped,
        created_at=int(time.time()),
        parent_version=cand.base_version,
    )


# ---------------------------------------------------------------------------
# 接受规则
# ---------------------------------------------------------------------------

@dataclass
class Decision:
    """配对评测的接受/拒绝决定。"""
    accepted: bool
    base_score: float
    candidate_score: float
    delta: float
    reason: str
    per_scenario_delta: Dict[str, float] = field(default_factory=dict)


def decide(
    base: Harness,
    candidate: Harness,
    min_delta: float = 0.0,
) -> Decision:
    """配对评测接受规则：ΔS ≥ min_delta 才接受。

    默认 min_delta = 0.0，即候选分数不能低于当前分数。
    严格模式可设 min_delta = 0.01 等正数。
    """
    delta = candidate.score - base.score
    per_scenario_delta: Dict[str, float] = {}
    all_scenarios = set(base.per_scenario.keys()) | set(candidate.per_scenario.keys())
    for sid in all_scenarios:
        b = base.per_scenario.get(sid)
        c = candidate.per_scenario.get(sid)
        b_score = b.score if b else 0.0
        c_score = c.score if c else 0.0
        per_scenario_delta[sid] = c_score - b_score

    if delta >= min_delta:
        # 检查是否有单场景大幅退化（保护性规则）
        worst = min(per_scenario_delta.values()) if per_scenario_delta else 0.0
        if worst <= -0.5:
            return Decision(
                accepted=False,
                base_score=base.score,
                candidate_score=candidate.score,
                delta=delta,
                reason=f"single_scenario_regression:{worst:.3f}",
                per_scenario_delta=per_scenario_delta,
            )
        return Decision(
            accepted=True,
            base_score=base.score,
            candidate_score=candidate.score,
            delta=delta,
            reason=f"delta_ok:{delta:+.4f}",
            per_scenario_delta=per_scenario_delta,
        )
    return Decision(
        accepted=False,
        base_score=base.score,
        candidate_score=candidate.score,
        delta=delta,
        reason=f"delta_negative:{delta:+.4f}",
        per_scenario_delta=per_scenario_delta,
    )


# ---------------------------------------------------------------------------
# 主编排器
# ---------------------------------------------------------------------------

@dataclass
class EvolutionStep:
    """一次进化尝试的完整记录（无论接受还是拒绝）。"""
    step_id: int
    base_version: str
    candidate_version: str
    candidate: Dict[str, Any]
    base_score: float
    candidate_score: float
    decision: Decision
    feedback_ids: List[str]
    rationale: str
    ts: int = field(default_factory=lambda: int(time.time()))

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class HarnessEvolver:
    """Harness 自进化主编排器。

    生命周期：
        1. 初始化一个 baseline harness
        2. 收集 feedback → 提炼候选
        3. 在基准场景集上配对评测
        4. ΔS≥0 接受，写入新版本；否则拒绝
        5. 每次尝试都记入 audit log
    """

    def __init__(
        self,
        baseline: Harness,
        scenario_loader: Optional[ScenarioLoader] = None,
        min_delta: float = 0.0,
    ):
        self.current = baseline
        self.scenario_loader = scenario_loader or default_scenario_loader()
        self.min_delta = min_delta
        self.history: List[EvolutionStep] = []
        self.audit: List[EvolutionStep] = []
        self.version_counter = 1
        # 评测一次，填充 per_scenario
        self.current = evaluate_harness(self.current, self.scenario_loader())

    def submit_feedback(
        self,
        feedback: Feedback,
        proposed_gate: Optional[Gate] = None,
        remove_gate_ids: Optional[List[str]] = None,
    ) -> Tuple[bool, Decision]:
        """提交一条反馈 + 候选修改；返回是否被接受 + 决定详情。

        典型用法：
            evolver.submit_feedback(
                feedback=Feedback("fb_001", "gap", "micro_52",
                                  "缺少 DR 锚点三维覆盖门禁"),
                proposed_gate=Gate("N34", "anchoring", "...", lambda s: True),
            )
        """
        cand = Candidate(
            base_version=self.current.version,
            target_version=f"v{self.version_counter}",
            feedback_ids=[feedback.fb_id],
            added=[proposed_gate] if proposed_gate else [],
            removed=remove_gate_ids or [],
            rationale=feedback.message,
        )

        candidate_harness = apply_candidate(self.current, cand)
        candidate_harness = evaluate_harness(
            candidate_harness, self.scenario_loader()
        )

        decision = decide(self.current, candidate_harness, self.min_delta)

        step = EvolutionStep(
            step_id=len(self.audit) + 1,
            base_version=self.current.version,
            candidate_version=cand.target_version,
            candidate=cand.to_dict() if hasattr(cand, "to_dict") else asdict(cand),
            base_score=self.current.score,
            candidate_score=candidate_harness.score,
            decision=decision,
            feedback_ids=[feedback.fb_id],
            rationale=feedback.message,
        )
        self.audit.append(step)

        if decision.accepted:
            self.current = candidate_harness
            self.history.append(step)
            self.version_counter += 1
        return decision.accepted, decision

    def rollback(self, target_version: str) -> Optional[Harness]:
        """回滚到指定历史版本。当前实现：只支持回滚到初始 baseline。"""
        for step in reversed(self.audit):
            if step.base_version == target_version:
                # 找到那一步，重建
                return None  # 占位：完整版需要保存每版 gate 快照
        return None

    def state(self) -> Dict[str, Any]:
        """当前状态快照。"""
        return {
            "schema": _SCHEMA,
            "current_version": self.current.version,
            "current_score": self.current.score,
            "current_fingerprint": self.current.fingerprint(),
            "gate_count": len(self.current.gates),
            "total_attempts": len(self.audit),
            "accepted_count": len(self.history),
            "rejection_rate": (
                (len(self.audit) - len(self.history)) / len(self.audit)
                if self.audit else 0.0
            ),
            "audit_length": len(self.audit),
        }


# ---------------------------------------------------------------------------
# 便捷构造
# ---------------------------------------------------------------------------

def make_gate(
    gid: str,
    category: str,
    description: str,
    predicate: Callable[[Dict[str, Any]], bool],
    negation: bool = False,
) -> Gate:
    """门禁构造函数（简化创建语法）。"""
    return Gate(id=gid, category=category, description=description,
                predicate=predicate, negation=negation)


def from_current_system() -> Harness:
    """从 SwarmLabs 现有 42 门禁构造一个 baseline harness。

    实际门禁定义在 engine/verification_gate.py + scripts/check_vlab_gates.py。
    此处不 import，仅作为占位——外部脚本应传入真实的 gate 列表。
    """
    return Harness(version="v0.0", gates=[])


__all__ = [
    "Feedback",
    "Gate",
    "ScenarioResult",
    "Harness",
    "Candidate",
    "Decision",
    "EvolutionStep",
    "HarnessEvolver",
    "evaluate_harness",
    "apply_candidate",
    "decide",
    "make_gate",
    "from_current_system",
    "_SCHEMA",
]
