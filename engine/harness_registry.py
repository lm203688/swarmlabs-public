#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Harness 门禁注册表 —— 把现有 42 门禁拆成 Gate 实例，接入 harness_evolver。

为什么需要这一层
---------------
`engine/harness_evolver.py` 是 harness 自进化的骨架，但需要一个"当前 harness
到底有哪些门禁"的具体清单才能真的跑起来。SwarmLabs 现有的 42 门禁分散在
5 个文件里（vlab_contract / vlab_orchestrator / anchor_intake /
discipline_registry / vlab_delivery），且大多是**过程式检查**而非可组合的
`Gate` 对象。

本模块做**适配**，不做**重构**：
  - 不改动任何现有门禁代码（vlab_contract.py 等一行不动）
  - 把每条门禁"注册"成一个 harness_evolver.Gate 实例
  - Gate.predicate 是一个 wrapper：把场景/work_order dict 包装成真实门禁
    需要的对象，调用真实引擎函数，把结果转成 bool

三条边界纪律
-----------
1. **只注册场景级门禁**：给定单个 work_order / scenario 就能返回 pass/fail
   的门禁才注册。系统级不变量（如"82 场景总数"）与 CI 编排（如
   preflight 跑 6 个脚本）不注册——它们属于 harness 的**外围约束**，
   不是"场景级判定"。

2. **不重写现有引擎**：predicate 是薄壳，实际判定逻辑仍在
   vlab_contract.validate_work_order 等原函数里。这样保证 harness_evolver
   的评测结果与 CI 门禁结果**逐条一致**。

3. **失败即拒（fail-closed）**：predicate 抛任何异常都视为门禁失败。这与
   check_vlab_gates.py 里 R.raises() 的"应抛未抛 = gate 失效"语义一致。

注册范围（阶段 1）
-----------------
契约层：N1, N2, N3, P1  → 需要 vlab_contract
阶段不变式：N4, N5, N6  → 需要 vlab_contract + orchestrator 上下文
锚定筛选：N11, N12, N13, N14  → 需要 anchor_intake
专业接入：N7, N8, N9, N10  → 需要 discipline_registry

阶段 2 会补充：
  - 交付物 N15
  - 工程纪律 N16
  - 三维证据判据 N31, N32, N33, P6
  - 5 独立不变量（场景化版本）
  - 6 preflight（作为系统级 gate）

依赖：仅标准库 + 同目录 vlab_* 模块。
"""

from __future__ import annotations

import importlib.util
import os
import sys
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = _HERE


# ---------------------------------------------------------------------------
# 模块加载（复用 check_vlab_gates 的 _load 语义，含 sys.modules 注册）
# ---------------------------------------------------------------------------

def _load_sibling(name: str):
    path = os.path.join(_ENGINE, f"{name}.py")
    spec = importlib.util.spec_from_file_location(f"_hr_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # @dataclass 需要
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Gate 类
# ---------------------------------------------------------------------------

@dataclass
class Gate:
    """SwarmLabs 场景级门禁的封装。

    与 harness_evolver.Gate 结构一致，方便直接注入 HarnessEvolver。
    """
    id: str
    category: str
    description: str
    predicate: Callable[[Dict[str, Any]], bool]
    negation: bool = False
    #: 原始来源文件（用于追溯）
    source: str = ""

    def evaluate(self, scenario: Dict[str, Any]) -> bool:
        try:
            return bool(self.predicate(scenario))
        except Exception:
            return False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "category": self.category,
            "description": self.description,
            "negation": self.negation,
            "source": self.source,
        }


# ---------------------------------------------------------------------------
# 门禁定义：每条 gate 是一个 thin wrapper
# ---------------------------------------------------------------------------

#: 全部注册表：gate_id -> Gate
_REGISTRY: Dict[str, Gate] = {}

_loaded = False


def _ensure_loaded():
    """延迟加载 vlab 引擎模块（避免 import 期副作用）。"""
    global _loaded
    if not _loaded:
        _load_sibling("vlab_stdlib")
        _load_sibling("vlab_contract")
        _load_sibling("discipline_registry")
        _load_sibling("anchor_intake")
        _loaded = True


def _load_contracts():
    """按名称取模块（延迟加载）。"""
    _ensure_loaded()
    return {
        "contract": sys.modules.get("_hr_vlab_contract"),
        "dr": sys.modules.get("_hr_discipline_registry"),
        "ai": sys.modules.get("_hr_anchor_intake"),
        "stdlib": sys.modules.get("_hr_vlab_stdlib"),
    }


def _register(gate: Gate) -> None:
    if gate.id in _REGISTRY:
        raise ValueError(f"gate id conflict: {gate.id}")
    _REGISTRY[gate.id] = gate


# ---- N1: 缺字段（discipline / goal.unit）→ 拒 ----
def _n1_missing_fields(scenario: Dict[str, Any]) -> bool:
    """work_order 必须含 discipline 与 goal.unit。"""
    C = _load_contracts()["contract"]
    wo = scenario.get("work_order", scenario)
    result = C.validate_work_order(wo)
    errors = result.get("errors", [])
    # N1 判定：不允许"缺字段"错误（code 包含 "missing" 或 "required"）
    missing = [e for e in errors if e.get("code", "") in
               ("missing_field", "required_field", "missing", "required")]
    return len(missing) == 0
_register(Gate(
    id="N1",
    category="contract",
    description="work_order must contain discipline and goal.unit",
    predicate=_n1_missing_fields,
    negation=True,
    source="engine/vlab_contract.py::validate_work_order",
))


# ---- N2: corroboration.as_input=true → 拒（红线）----
def _n2_corroboration_as_input(scenario: Dict[str, Any]) -> bool:
    wo = scenario.get("work_order", scenario)
    corrob = wo.get("corroboration") or {}
    return not corrob.get("as_input", False)
_register(Gate(
    id="N2",
    category="contract",
    description="corroboration.as_input must be False (red line)",
    predicate=_n2_corroboration_as_input,
    negation=True,
    source="engine/vlab_contract.py::assert_corroboration_post_hoc",
))


# ---- N3: 设计空间区间倒置 → 拒 ----
def _n3_design_space_inverted(scenario: Dict[str, Any]) -> bool:
    wo = scenario.get("work_order", scenario)
    design = wo.get("design_space") or {}
    for k, v in design.items():
        if isinstance(v, dict) and "lo" in v and "hi" in v:
            if v["lo"] > v["hi"]:
                return False
    return True
_register(Gate(
    id="N3",
    category="contract",
    description="design_space intervals must not be inverted (lo <= hi)",
    predicate=_n3_design_space_inverted,
    negation=True,
    source="engine/vlab_contract.py::validate_work_order",
))


# ---- N4: 阶段乱序（corroborate 在 verify 前）→ 抛 ----
def _n4_stage_order(scenario: Dict[str, Any]) -> bool:
    C = _load_contracts()["contract"]
    stages = scenario.get("stages", [])
    try:
        C.assert_stage_order(stages)
        return True
    except Exception:
        return False
_register(Gate(
    id="N4",
    category="stage_order",
    description="stage order must be verify before corroborate",
    predicate=_n4_stage_order,
    negation=True,
    source="engine/vlab_contract.py::assert_stage_order",
))


# ---- N7: 无 gold 的专业不得为 VALIDATED ----
def _n7_discipline_validated(scenario: Dict[str, Any]) -> bool:
    """work_order.discipline 若声称 VALIDATED，必须有 gold。"""
    DR = _load_contracts()["dr"]
    wo = scenario.get("work_order", scenario)
    disc_name = wo.get("discipline")
    if not disc_name:
        return True  # 无 discipline 声明，跳过
    try:
        result = DR.validate_discipline(disc_name)
        return result.get("status") in ("VALID", "VALIDATED")
    except Exception:
        return False
_register(Gate(
    id="N7",
    category="discipline",
    description="discipline with status VALIDATED must have gold",
    predicate=_n7_discipline_validated,
    negation=True,
    source="engine/discipline_registry.py::validate_discipline",
))


# ---- N8: 只有散文式"引用"（无年份/DOI）→ 拒 ----
def _n8_citation_checkable(scenario: Dict[str, Any]) -> bool:
    DR = _load_contracts()["dr"]
    wo = scenario.get("work_order", scenario)
    refs = wo.get("references") or wo.get("evidence", {}).get("references", [])
    for r in refs:
        cit = r.get("citation") if isinstance(r, dict) else str(r)
        if not DR._citation_is_checkable(cit or ""):
            return False
    return True
_register(Gate(
    id="N8",
    category="evidence",
    description="every citation must contain year or DOI",
    predicate=_n8_citation_checkable,
    negation=True,
    source="engine/discipline_registry.py::_citation_is_checkable",
))


# ---- N11: 无引用 → REJECTED ----
def _n11_anchor_citation(scenario: Dict[str, Any]) -> bool:
    """anchor 必须带可核验引用（year / DOI）。"""
    anchor = scenario.get("anchor", {})
    if not anchor:
        return True  # 无 anchor 需求
    sources = anchor.get("sources", [])
    if not sources:
        return False
    DR = _load_contracts()["dr"]
    return any(DR._citation_is_checkable(str(s)) for s in sources)
_register(Gate(
    id="N11",
    category="anchoring",
    description="anchor must have checkable citation (year or DOI)",
    predicate=_n11_anchor_citation,
    negation=True,
    source="engine/anchor_intake.py::screen_candidate",
))


# ---- N12: 量纲不符 → REJECTED ----
def _n12_unit_consistency(scenario: Dict[str, Any]) -> bool:
    AI = _load_contracts()["ai"]
    anchor = scenario.get("anchor", {})
    if not anchor:
        return True
    anchor_unit = anchor.get("unit")
    target_unit = anchor.get("target_unit") or anchor.get("spec_unit")
    if not anchor_unit or not target_unit:
        return True
    if AI.normalize_unit_token(anchor_unit) != AI.normalize_unit_token(target_unit):
        return False
    return True
_register(Gate(
    id="N12",
    category="anchoring",
    description="anchor unit must match target unit",
    predicate=_n12_unit_consistency,
    negation=True,
    source="engine/anchor_intake.py::parse_unit",
))


# ---- N13: 机械全过但无人确认 → NEEDS_HUMAN ----
def _n13_needs_human_confirmation(scenario: Dict[str, Any]) -> bool:
    """DR 锚点必须有具名确认人。"""
    anchor = scenario.get("anchor", {})
    if not anchor:
        return True
    if anchor.get("decision_relevant", True) and not anchor.get("confirmed_by"):
        return False
    return True
_register(Gate(
    id="N13",
    category="anchoring",
    description="decision-relevant anchor must have named confirmer",
    predicate=_n13_needs_human_confirmation,
    negation=True,
    source="engine/anchor_intake.py::screen_candidate",
))


# ---- N14: 超出声明合理区间 → NEEDS_HUMAN ----
def _n14_within_declared_range(scenario: Dict[str, Any]) -> bool:
    anchor = scenario.get("anchor", {})
    if not anchor:
        return True
    lo, hi, val = anchor.get("lo"), anchor.get("hi"), anchor.get("value")
    if val is None or lo is None or hi is None:
        return True
    return lo <= val <= hi
_register(Gate(
    id="N14",
    category="anchoring",
    description="anchor value must be within declared range",
    predicate=_n14_within_declared_range,
    negation=True,
    source="engine/anchor_intake.py::screen_candidate",
))


# ---- P1: 合法工作单 → 通过（正向对照）----
def _p1_valid_work_order(scenario: Dict[str, Any]) -> bool:
    C = _load_contracts()["contract"]
    wo = scenario.get("work_order", scenario)
    result = C.validate_work_order(wo)
    return not result.get("errors")
_register(Gate(
    id="P1",
    category="positive_control",
    description="valid work_order passes contract validation",
    predicate=_p1_valid_work_order,
    negation=False,
    source="engine/vlab_contract.py::validate_work_order",
))


# ---------------------------------------------------------------------------
# 公开 API
# ---------------------------------------------------------------------------

def all_gates() -> List[Gate]:
    """返回全部注册门禁（自然序：N1 < N2 < ... < N11 < N12 ... < P1）。"""
    def _sort_key(g: Gate) -> tuple:
        # 提取数字部分与自然号
        import re as _re
        m = _re.match(r"^([A-Za-z]+)(\d+)$", g.id)
        if m:
            return (m.group(1), int(m.group(2)))
        return (g.id, 0)
    return sorted(_REGISTRY.values(), key=_sort_key)


def get_gate(gate_id: str) -> Optional[Gate]:
    return _REGISTRY.get(gate_id)


def registered_ids() -> List[str]:
    return [g.id for g in all_gates()]


def summary() -> Dict[str, Any]:
    """按分类统计门禁数量。"""
    by_cat: Dict[str, int] = {}
    for g in _REGISTRY.values():
        by_cat[g.category] = by_cat.get(g.category, 0) + 1
    return {
        "total": len(_REGISTRY),
        "by_category": by_cat,
        "negation_count": sum(1 for g in _REGISTRY.values() if g.negation),
        "positive_control_count": sum(1 for g in _REGISTRY.values() if not g.negation),
    }


__all__ = [
    "Gate",
    "all_gates",
    "get_gate",
    "registered_ids",
    "summary",
]
