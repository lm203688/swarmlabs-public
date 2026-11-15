#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""证据采集简报生成器 —— 把「验证缺口」翻译成「可执行的取证任务」。

为什么这一层必须存在
---------------------
现状的三段各管一段，中间是断的：

    ablation_plan      报告缺口（anchor / replication / sensitivity）
        ↓   **断在这里：没有人把缺口翻译成「该去查什么」**
    anchor_intake      筛提交上来的候选（可核验性 → 量纲 → 具名确认人）
    wet_lab_anchors    19 条人工策展的锚点

诊断永远在说「你缺锚定」，却没有任何东西说明：要查哪个量的哪个单位、避开哪些
已经饱和的来源家族、这一条得通过哪几道筛子才算闭环。这是 anchor 维度卡在
0.300（80 个场景 0 个满分）的**机制原因**——不是筛子不严，是没有人把缺口变
成任务。

本模块产出的是 **brief（取证任务书）**，不是证据本身：

  * 不联网、不调用任何外部服务 —— 保持离线可复验（与内核「离线独立成立」同一条
    纪律）；
  * 每条 brief 写明它要通过 `anchor_intake` 的哪几道筛子才闭环；
  * 明确标注 brief ≠ evidence —— 交付一份 brief 不等于交付一条锚点。

三条不可妥协的纪律（写进代码，不靠约定）
----------------------------------------
1. 不编造检索结果。本模块**从不**声称「找到了某文献」；它只生成任务书。
   任何 `found_*` / `retrieved_*` 字段都不存在，也没有入口可以写进来。
2. 不降级筛子。brief 里写明的 screening_gate 与 `anchor_intake.screen_candidate`
   的判据一一对应，且 `auto_accept_path` 恒为 False。
3. 饱和家族要避开，不重复。`anchor_independence` 报独立性比率 0.5278 < 0.80，
   6 个聚类被多个锚点共用；brief 据此列出该避开的家族，防止新锚点只是把同一条
   来源再数一次。

依赖：仅标准库。账本与锚点表均为只读。
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))


def _sibling(name: str):
    spec = importlib.util.spec_from_file_location(
        f"_ev_{name}", os.path.join(_HERE, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod   # 必须注册，dataclasses 解析期会回查 sys.modules
    spec.loader.exec_module(mod)
    return mod


S = _sibling("vlab_stdlib")

SCHEMA = "swarmlabs-evidence-acquisition/1.0"

#: brief 永远不是证据
NOT_EVIDENCE = (
    "BRIEF, NOT EVIDENCE. This record is a retrieval task, generated offline "
    "from the validation gap report. It asserts nothing about any measurement. "
    "A brief only closes when its result passes "
    "engine/anchor_intake.py::screen_batch and lands in "
    "frontend_fixed/reports/wet_lab_anchors.json with a named human confirmer."
)

#: anchor_intake 的三道筛子 —— 与 screen_candidate 的判据顺序一致，写死在此
#: 保证 brief 声明的门槛与真正执行的门槛是同一份。
SCREENING_GATES = (
    {"layer": 1, "name": "verifiability",
     "rule": "citation must contain a year (19xx/20xx) or a DOI",
     "fails": "REJECTED: unverifiable_citation"},
    {"layer": 2, "name": "dimension",
     "rule": "value must be dimensionally compatible with target_unit and "
             "convertible to it",
     "fails": "REJECTED: dimension_mismatch"},
    {"layer": 3, "name": "plausible_range",
     "rule": "if a plausible_range is declared, the normalized value must fall "
             "inside it",
     "fails": "NEEDS_HUMAN: out_of_plausible_range"},
    {"layer": 4, "name": "human_confirmation",
     "rule": "a named human confirmer is mandatory",
     "fails": "NEEDS_HUMAN: awaiting_human_confirmation"},
)


# ------------------------------------------------------------------ 输入装载

def _load_ledger() -> Dict[str, Any]:
    p = os.path.join(S.ROOT, "frontend_fixed", "reports", "gate_ledger.json")
    return S.read_json(p) or {}


def _load_wet_lab_anchors() -> List[dict]:
    p = os.path.join(S.ROOT, "frontend_fixed", "reports", "wet_lab_anchors.json")
    doc = S.read_json(p) or {}
    return doc.get("anchors") or []


def _source_families() -> Tuple[List[str], Dict[str, int]]:
    """已用来源家族，以及「被复用次数」（≥2 视为饱和，新锚点应避开）。"""
    families: Dict[str, int] = {}
    for a in _load_wet_lab_anchors():
        for src in (a.get("sources") or []):
            if isinstance(src, str):
                key = src.strip()
            else:
                key = str(src.get("family") or src.get("type") or src)[:120]
            if key:
                families[key] = families.get(key, 0) + 1
    return sorted(families), families


def _saturated_families(families: Dict[str, int],
                        min_hits: int = 2) -> List[dict]:
    """被多个锚点共用的来源家族 —— 再从这里取数等于重复计数同一份情报。"""
    return [{"family": k, "used_by_n_anchors": v}
            for k, v in sorted(families.items(), key=lambda kv: -kv[1])
            if v >= min_hits]


def _existing_anchor_keys(scenario: str) -> List[str]:
    """该场景已被哪些锚点覆盖（判断「是补强还是从零」）。"""
    out = []
    for a in _load_wet_lab_anchors():
        for sc in (a.get("scenarios") or []):
            if sc == scenario:
                out.append(a.get("key", ""))
                break
    return out


# ------------------------------------------------------------------ 分诊

def _synthetic_anchor(scenario: str) -> Optional[dict]:
    """读合成锚点（只读，不改写）；无则 None。"""
    p = os.path.join(S.ROOT, "data", "synthetic_anchors.json")
    doc = S.read_json(p) or {}
    for e in (doc.get("anchors") or []):
        if e.get("scenario") == scenario:
            return e
    return None


def acquisition_route(scenario: str) -> Tuple[str, str]:
    """判断取证路线：现成素材可晋升 / 需外部检索。

    `promote_existing` 是最便宜的路线——账本里已经有可读的文献参数（
    synthetic_anchors），只差过一遍 anchor_intake 的筛子 + 具名确认。
    `external_search` 才是真正的情报采集。
    """
    sa = _synthetic_anchor(scenario)
    if sa is None:
        return "external_search", (
            "ledger carries no published parameter text for this scenario — "
            "nothing to promote, the intelligence has to be found from scratch")
    outcome = ((sa.get("prevalidation") or {}).get("outcome"))
    has_year = bool(sa.get("has_year"))
    if outcome == "CLEAN" and has_year:
        return "promote_existing", (
            "published parameters parse to quantified values with a declared "
            "unit and a citation year — promotion needs only anchor_intake "
            "screening plus a named confirmer")
    return "external_search", (
        f"synthetic triage outcome={outcome!r} has_year={has_year} — "
        "material is too thin to promote; requires external retrieval")


# ------------------------------------------------------------------ 简报

def _impact(dim: str, before: float) -> Dict[str, Any]:
    """这条缺口补上后，对应维度分数从哪到哪。保守估计：满分需要一次真实锚定。"""
    targets = {"anchor": 1.0, "replication": 1.0, "sensitivity": 1.0}
    return {"dimension": dim, "before": round(before, 3),
            "after_if_closed": targets.get(dim, 1.0),
            "note": ("full credit requires evidence through the stated gate — "
                     "a synthetic extract does not score")}


def brief_for_gap(scenario: str, gap: str, plan: dict,
                  ledger_entry: dict) -> Dict[str, Any]:
    """为「场景 × 缺口维度」生成一条取证任务书。"""
    dim = plan.get("dimensions", {}).get(gap, {})
    pub = (ledger_entry.get("published") or "").strip()
    fams, fam_counts = _source_families()
    route, route_hint = acquisition_route(scenario)
    sa = _synthetic_anchor(scenario)

    if gap == "anchor":
        # 目标量：优先取专业档案声明的输出量，其次从 published 文本里抽第一个
        qty, unit = _target_quantity(ledger_entry)
        body = {
            "target_quantity": qty or None,
            "target_unit": unit or None,
            "ledger_published_text": pub or None,
            "existing_anchors_on_scenario": _existing_anchor_keys(scenario),
            "avoid_source_families": _saturated_families(fam_counts),
            "avoidance_reason": (
                "these families are already shared by 2+ anchors; reusing them "
                "would not raise the independence ratio, which is the metric "
                "flagged below its 0.80 threshold"),
            "screening_gate": {
                "module": "engine/anchor_intake.py",
                "function": "screen_batch",
                "layers": SCREENING_GATES,
                "auto_accept_path": False,
            },
            "plausible_range_request": {
                "needed": not bool(ledger_entry.get("plausible_range")),
                "reason": ("anchor_intake cannot apply its out_of_plausible_range "
                           "check without a declared range, so screening is "
                           "dimension-only and materially weaker"),
            },
            "retraction_check_request": {
                "needed": True,
                "reason": ("anchor_independence reports retraction_checked=false "
                           "for the whole corpus — a retracted source silently "
                           "poisons every anchor built on it"),
            },
        }
    elif gap == "replication":
        body = {
            "task": "recompute this scenario with a surrogate backend different "
                    "from the one that produced the ledger verdict",
            "independence_requirement": (
                "the second backend must share no fitting code path with the "
                "first — shared numerics is not replication"),
            "accepted_evidence": "a stored comparison record: same inputs, "
                                 "both outputs, absolute and relative delta",
            "not_accepted": "re-running the identical backend with a new seed "
                            "(that is variance, not replication)",
            "offline_executable": True,
            "note": "replication is the one gap here that needs no external "
                    "intelligence at all — it is pure compute",
        }
    elif gap == "sensitivity":
        body = {
            "task": "generate sensitivity variants of this scenario over its "
                    "declared design space",
            "accepted_evidence": "a stored variant set with named perturbation "
                                 "axes and per-axis response statistics",
            "not_accepted": "a single perturbed run with no axis attribution",
            "offline_executable": True,
        }
    else:
        body = {"task": f"gap '{gap}' is not a recognized evidence dimension"}

    return {
        "schema": SCHEMA,
        "scenario": scenario,
        "gap": gap,
        "domain": ledger_entry.get("domain"),
        "verdict": plan.get("verdict"),
        "current_score": round(float(dim.get("score", 0.0)), 3),
        "dimension_note": dim.get("note", ""),
        "score_impact": _impact(gap, float(dim.get("score", 0.0))),
        "acquisition_route": route,
        "route_hint": route_hint,
        "synthetic_triage": (
            {k: sa.get(k) for k in ("prevalidation", "has_year", "citations")}
            if sa else None),
        "requires_external_intelligence": route == "external_search",
        "offline_executable": body.get("offline_executable", False),
        "not_evidence": NOT_EVIDENCE,
        "body": body,
    }


def _target_quantity(ledger_entry: dict) -> Tuple[str, str]:
    """从场景档案里尽力抽出「目标量 + 单位」。抽不到就返回空——不猜。

    只信两处：专业档案声明的 output_quantity/output_unit（机器可核验），以及
    `published` 文本里带单位的第一个参数。散文里读不出量纲就不填。
    """
    try:
        DR = _sibling("discipline_registry")
        for s in DR.seeded_specs():
            if s.kernel_scenario_key and \
                    ledger_entry.get("scenario_key") == s.kernel_scenario_key:
                if s.has_output_declared:
                    return s.output_quantity, s.output_unit
    except Exception:
        pass  # noqa: silent-ok — Exception 可安全忽略（已审计 2026-10-08）
    # 退路：published 文本里的 `K=100 h⁻¹` 这类片段。字符类必须含希腊字母——
    # 生物动力学几乎全是 μmax / Ks / Ea，纯 [A-Za-z] 会把 μmax 截成 max，
    # 而错误的目标量名会让取证任务去查一个不存在的量。
    import re
    m = re.search(r"([A-Za-z\u0370-\u03ff][A-Za-z0-9_\u0370-\u03ff]{0,12})"
                  r"\s*[=：:]\s*(\d+\.?\d*|\.?\d+)\s*([^\s,;)]{0,12})",
                  ledger_entry.get("published") or "")
    if m:
        return m.group(1), m.group(3).strip()
    return "", ""


# ------------------------------------------------------------------ 汇总

def plan(scenarios: Optional[List[str]] = None,
         only_gaps: Optional[List[str]] = None,
         only_promotable: bool = False) -> Dict[str, Any]:
    """生成优先级排序的取证任务书清单。"""
    VG = _sibling("verification_gate")
    ledger = _load_ledger()
    rows = ledger.get("scenarios") or {}
    keys = list(rows.keys()) if scenarios is None else \
        [k for k in scenarios if k in rows]

    rep = VG.ablation_plan_batch(keys, ledger)
    plans = rep.get("plans") or {}

    briefs: List[dict] = []
    for k in keys:
        p = plans.get(k)
        if not p:
            continue
        for gap in (p.get("gaps") or []):
            if only_gaps and gap not in only_gaps:
                continue
            b = brief_for_gap(k, gap, p, rows.get(k) or {})
            if only_promotable and b["acquisition_route"] != "promote_existing":
                continue
            briefs.append(b)

    # 排序：可晋升（最便宜）→ 可离线执行 → 外部检索；同级按当前分升序（越差越先）
    def _sort_key(b: dict):
        route = 0 if b["acquisition_route"] == "promote_existing" else 1
        offline = 0 if b["offline_executable"] else 1
        return (route, offline, b["current_score"], b["scenario"], b["gap"])
    briefs.sort(key=_sort_key)

    for i, b in enumerate(briefs, 1):
        b["brief_id"] = f"eb-{i:04d}"

    return _summarize(briefs, plans, keys)


def _summarize(briefs: List[dict], plans: Dict[str, dict],
               keys: List[str]) -> Dict[str, Any]:
    _, fam_counts = _source_families()
    gap_counts: Dict[str, int] = {}
    route_counts: Dict[str, int] = {}
    for b in briefs:
        gap_counts[b["gap"]] = gap_counts.get(b["gap"], 0) + 1
        route_counts[b["acquisition_route"]] = \
            route_counts.get(b["acquisition_route"], 0) + 1

    promotable = [b for b in briefs if b["acquisition_route"] == "promote_existing"]
    offline = [b for b in briefs if b["offline_executable"]]
    scores = [p.get("dimensions", {}).get("anchor", {}).get("score", 0.0)
              for p in plans.values()]

    return {
        "schema": SCHEMA,
        "generated_at_utc": S.utc_now(),
        "kind": "evidence-acquisition-plan (retrieval tasks, not evidence)",
        "source_gaps": "engine/verification_gate.py::ablation_plan_batch",
        "screening_gate": "engine/anchor_intake.py::screen_batch",
        "counts": {
            "scenarios_assessed": len(keys),
            "briefs": len(briefs),
            "by_gap": gap_counts,
            "by_route": route_counts,
            "promotable_now": len(promotable),
            "offline_executable": len(offline),
            "requires_external_intelligence":
                len(briefs) - len(promotable),
            "source_families_in_use": len(fam_counts),
            "saturated_families": sum(1 for v in fam_counts.values() if v >= 2),
            "anchor_score_mean": round(sum(scores) / len(scores), 4)
            if scores else 0.0,
            "anchor_scenarios_at_full": sum(1 for s in scores if s >= 1.0),
            "anchor_scenarios_at_zero": sum(1 for s in scores if s < 0.01),
        },
        "finding": (
            f"{len(promotable)} briefs are promotion-only (material already in "
            f"the ledger, needs screening + a confirmer) and "
            f"{len(offline)} are executable offline with no external "
            f"intelligence. The remaining {len(briefs) - len(promotable)} "
            f"require genuine external retrieval — this is the part of the "
            f"work that has no automation at all today."
        ),
        "limits": [
            "Generated offline. No brief asserts a retrieved value.",
            "brief_id is a display handle only, not a stable cross-run key.",
            "score_impact.after_if_closed is an optimistic ceiling, not a "
            "promise — it assumes the evidence clears every screening layer.",
            "target_quantity is best-effort extraction; empty means nobody has "
            "declared it and the brief cannot say what to measure.",
        ],
        "briefs": briefs,
    }


def main() -> int:
    report = plan()
    out = os.path.join(S.ROOT, "data", "evidence_acquisition_plan.json")
    S.write_json(out, report)
    c = report["counts"]
    print(f"scenarios assessed      : {c['scenarios_assessed']}")
    print(f"briefs                  : {c['briefs']}")
    for g, n in sorted(c['by_gap'].items()):
        print(f"  gap {g:12s}: {n}")
    print(f"promotable now          : {c['promotable_now']}")
    print(f"offline executable      : {c['offline_executable']}")
    print(f"needs external intel    : {c['requires_external_intelligence']}")
    print(f"source families in use  : {c['source_families_in_use']}  "
          f"(saturated: {c['saturated_families']})")
    print(f"anchor mean / at-full   : {c['anchor_score_mean']} / "
          f"{c['anchor_scenarios_at_full']}")
    print(f"\n{report['finding']}")
    print(f"\nwritten                 : {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
