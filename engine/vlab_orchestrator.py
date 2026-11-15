#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""VLab 编排器 —— 「客户发来一个实验」的六段流水线。

    intake -> scope -> build -> verify -> corroborate -> deliver

它把 `docs/feasibility-virtual-lab-2026-09-21.md` §5 的架构图变成可执行代码，
并且**在运行时强制**那条不可倒置的红线：

  - `build` 阶段只接收白名单输入（`vlab_contract.BUILD_INPUT_KEYS`），
    结构上拿不到任何真实观测；
  - `corroborate` 只做**事后作证**，产出的 chip 带 `affects_verdict: false`；
  - 跑完必调 `assert_order_integrity(job)`：阶段单调 + 数据隔离 +
    裁决在 corroborate 前后逐位不变。任一不成立即抛错。

fail-closed 语义
----------------
- 工作单非法 → intake 阶段即拒（不进 build/verify）；
- 专业未接入（无 gold 或内核场景不在账本）→ `NEEDS_ONBOARDING`，**不给裁决**，
  附上可复用脚手架清单，而不是编一个 verdict；
- 账本里没有该场景 / 账本已过期（引擎指纹变了）→ 拒绝沿用旧结论，
  给出 `UNVALIDATED` + `BLOCK_AUTONOMOUS_ACTION`，并说明原因。

裁决字段一律从 `engine/verification_gate.py` 搬运（不重算、不发明阈值）。

依赖：仅标准库（同目录模块按路径加载）。
"""

from __future__ import annotations

import importlib.util
import os
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))


def _sibling(name: str):
    spec = importlib.util.spec_from_file_location(
        f"_vlab_{name}", os.path.join(_HERE, f"{name}.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


S = _sibling("vlab_stdlib")
C = _sibling("vlab_contract")
DR = _sibling("discipline_registry")
AI = _sibling("anchor_intake")
VD = _sibling("vlab_delivery")

SCHEMA = "swarmlabs-vlab-job/1.0"

#: outcome 取值
REJECTED_AT_INTAKE = "REJECTED_AT_INTAKE"
NEEDS_ONBOARDING = "NEEDS_ONBOARDING"
NO_LEDGER_FAIL_CLOSED = "NO_LEDGER_FAIL_CLOSED"
STALE_LEDGER_FAIL_CLOSED = "STALE_LEDGER_FAIL_CLOSED"


# ------------------------------------------------------------------ 工具

def _stage(name: str, ok: bool, **detail) -> dict:
    rec = {"stage": name, "ok": bool(ok), "at_utc": S.utc_now()}
    rec.update(detail)
    return rec


def _resolve_discipline(name: str):
    """按 name / category 在「种子 + 已落盘」里找专业档案。"""
    specs = {s.name: s for s in DR.seeded_specs()}
    for nm, doc in DR.load_registered().items():
        specs[nm] = DR.DisciplineSpec.from_dict(doc)
    if name in specs:
        return specs[name]
    for spec in specs.values():
        if spec.category == name:
            return spec
    return None


def _scope(wo: dict, ledger: dict) -> dict:
    """把工作单落成 scenario spec + 专业路由。"""
    disc = wo.get("discipline")
    spec = _resolve_discipline(disc)
    scenarios = (ledger or {}).get("scenarios") or {}

    scenario_key = wo.get("scenario_key") or ""
    if not scenario_key and spec is not None:
        scenario_key = spec.kernel_scenario_key or ""

    if scenario_key and scenario_key in scenarios:
        mode = "existing_kernel"
    else:
        mode = "discipline_onboarding_required"

    out = {
        "mode": mode,
        "discipline_requested": disc,
        "discipline_matched": spec.name if spec else None,
        "category": (spec.category if spec else None),
        "discipline_status": (DR.status_of(spec) if spec else DR.AWAITING_GOLD),
        "scenario_key": scenario_key or None,
        "scenario_in_ledger": bool(scenario_key) and scenario_key in scenarios,
        "n_ledger_scenarios": len(scenarios),
    }
    if mode != "existing_kernel":
        out["onboarding_checklist"] = DR.onboarding_checklist(
            (spec.category if spec else disc) or ""
        )
        out["reason"] = (
            "该专业尚无「可解析 gold + 账本内内核场景」，按既有纪律不得给出裁决"
            "（编一个 verdict 才是真正的不诚实）"
        )
        # 路由建议：只给候选，**绝不**因此改判 mode。
        # 76 个场景此前只对知道确切 scenario_key 的人可达，这里让调用方
        # 看到可能相关的场景，自己调整查询再提交。路由器不能替调用方
        # 决定路由目标——那等于绕过 onboarding 这道门。
        out["suggested_scenarios"] = _route_suggestions(wo)
    return out


def _route_suggestions(wo: dict, top_k: int = 3) -> list:
    """离线模糊路由建议。任何异常都返回空列表——建议缺失不影响主流程。"""
    try:
        SR = _load_sibling("scenario_router")
        hits = SR.route_from_work_order(wo, top_k=top_k)
        return hits.get("matches") or []
    except Exception:
        return []


def _load_sibling(name: str):
    """按路径加载同目录模块（engine/ 不在 sys.path 里，不能直接 import）。"""
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), name + ".py")
    spec = importlib.util.spec_from_file_location("_vlab_" + name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _build(wo: dict, scope: dict) -> dict:
    """build 阶段：只吃白名单输入。真实观测在此阶段结构上不可达。"""
    build_inputs = {
        k: wo[k] for k in C.BUILD_INPUT_KEYS if k in wo
    }
    build_inputs["credential"] = wo.get("submitted_by")
    # 红线：若真实数据以任何形式混进 build → 抛 OrderInvertedError
    C.assert_corroboration_post_hoc(build_inputs, wo.get("corroboration"))

    if scope["mode"] != "existing_kernel":
        return {
            "kind": "not_built",
            "reason": "discipline_onboarding_required",
            "independent_of_corroboration": True,
            "inputs_digest": S.canonical_sha256(build_inputs),
            "inputs_keys": sorted(build_inputs.keys()),
        }
    return {
        "kind": "existing_kernel_scenario",
        "scenario_key": scope["scenario_key"],
        "note": ("虚拟实验由统一内核独立完成：裁决来自门禁账本，账本在**收到"
                 "本工作单之前**已算好，客户真实数据从未参与"),
        "independent_of_corroboration": True,
        "inputs_digest": S.canonical_sha256(build_inputs),
        "inputs_keys": sorted(build_inputs.keys()),
    }


def _verify(wo: dict, scope: dict, ledger: dict) -> dict:
    """verify 阶段：从门禁单一真源搬裁决，fail-closed。"""
    key = scope.get("scenario_key")
    coe = float(wo.get("cost_of_error", 1.0))
    g = S.load_gate()

    def _fail(reason: str, outcome: str, **extra) -> dict:
        """fail-closed 统一出口——**键集固定**。

        三种失败路径（无场景 / 账本无该条目 / 账本过期）必须给出同形状的
        verdict：否则调用方会因为"走了哪条分支"而拿不到某个字段，
        于是消费方不得不写 `if 'cite_ok' in v` 这种脆弱代码。
        """
        v = {
            "hard_verdict": "UNVALIDATED",
            "gate": g.GATE_BLOCK,
            "gate_label": g.gate_label(g.GATE_BLOCK),
            "reason": reason,
            "probabilities": None,
            "confidence": None,
            "recommendation": "HUMAN",
            "domain": None,
            "cite_ok": None,
            "cite_reasons": None,
            "cite_violations": None,
            "cite_gate_block": None,
            "thresholds_source": "engine/verification_gate.py::thresholds()",
        }
        v.update(extra)
        return {"verdict": v, "outcome": outcome, "cite": None,
                "ledger_generated_at_utc": (ledger or {}).get("generated_at_utc")}

    if not key or not ledger:
        return _fail("no ledger entry for this scenario — fail closed",
                     NO_LEDGER_FAIL_CLOSED)

    entry = g.ledger_entry(key, ledger)
    if entry is None:
        return _fail(f"'{key}' not present in the gate ledger — fail closed",
                     NO_LEDGER_FAIL_CLOSED)

    # 过期账本 → 拒绝沿用旧结论（绝不把旧 PASS 当有效）
    stale = g.ledger_is_stale(ledger)
    if stale.get("stale"):
        return _fail(
            "ledger engine fingerprint is stale — refusing to reuse old "
            "verdicts; recompute the ledger first",
            STALE_LEDGER_FAIL_CLOSED,
            ledger_all_digest=stale.get("ledger_all_digest"),
            current_all_digest=stale.get("current_all_digest"),
        )

    cal = g.calibrated_verdict(key, ledger, coe)
    hard = cal["hard_verdict"]
    gate = g.gate_for_verdict(hard)
    cite = g.cite_check_scenario(key, entry)
    cite_gate_block = None
    # "No Anchored Counter, No PASS" —— PASS 但无任何独立对照 → 硬阻断
    if hard == g.GRADE_PASS and not cite.get("ok"):
        gate = g.GATE_BLOCK
        cite_gate_block = ("PASS without any anchor/replication reference — "
                           "No Anchored Counter, No PASS")

    verdict = {
        "hard_verdict": hard,
        "gate": gate,
        "gate_label": g.gate_label(gate),
        "reason": None,
        "probabilities": cal["probabilities"],
        "confidence": cal["confidence"],
        "recommendation": cal["recommendation"],
        "cost_of_error": cal["cost_of_error"],
        "cost_thresholds": cal["cost_thresholds"],
        "domain": cal.get("domain") or entry.get("domain"),
        "cite_ok": cite.get("ok"),
        "cite_reasons": cite.get("reasons"),
        "cite_violations": cite.get("violations"),
        "cite_gate_block": cite_gate_block,
        "thresholds_source": "engine/verification_gate.py::thresholds()",
    }
    return {
        "verdict": verdict,
        "outcome": gate,
        "cite": cite,
        "ledger_generated_at_utc": ledger.get("generated_at_utc"),
    }


def _corroborate(wo: dict, verify: dict) -> dict:
    """corroborate 阶段：真实数据**事后**作证。只增信，不改判。"""
    corr = wo.get("corroboration") or {}
    pts = corr.get("points") or []
    goal = wo.get("goal") or {}
    unit = goal.get("unit") or ""
    pr = goal.get("plausible_range")
    plausible = (float(pr[0]), float(pr[1])) if isinstance(pr, list) and len(pr) == 2 else None

    if pts:
        cands = [AI.ReferenceCandidate.from_dict(p) for p in pts]
        screening = AI.screen_batch(cands, unit, plausible)
    else:
        screening = {
            "schema": "swarmlabs-vlab-anchor-screening/1.0",
            "spec_unit": unit,
            "plausible_range": list(plausible) if plausible else None,
            "counts": {AI.ACCEPTED: 0, AI.NEEDS_HUMAN: 0, AI.REJECTED: 0},
            "n": 0, "accepted": [], "needs_human": [], "rejected": [], "rows": [],
            "policy": "no corroboration points supplied",
        }

    return {
        "screening": screening,
        "verdict_before": verify["verdict"].get("hard_verdict"),
        "verdict_after": verify["verdict"].get("hard_verdict"),
        "verdict_unchanged": True,
        "affects_verdict": False,
        "parity": {
            "computed": False,
            "reason": ("prediction-vs-observation parity requires the numeric "
                       "engine (numpy); this stdlib layer screens the data and "
                       "records it, it does not fabricate a parity number"),
        },
    }


def assert_order_integrity(job: dict) -> None:
    """跑完必调：阶段单调 + 数据隔离 + 裁决未被 corroborate 改动。

    任一不成立即抛错（fail-closed）——这是把文档红线变成运行时不变式的地方。
    """
    stages = job.get("stages") or []
    C.assert_stage_order(stages)

    by_name = {s["stage"]: s for s in stages}

    # (1) verify 必须早于 corroborate
    order = {name: i for i, name in enumerate(C.STAGE_ORDER)}
    if "verify" in by_name and "corroborate" in by_name:
        if order["verify"] >= order["corroborate"]:
            raise C.OrderInvertedError(
                "verify must precede corroborate in the executed pipeline"
            )

    # (2) build 输入不得包含任何真实观测
    build = by_name.get("build") or {}
    wo = job.get("_work_order") or {}
    build_inputs = {k: wo[k] for k in C.BUILD_INPUT_KEYS if k in wo}
    build_inputs["credential"] = wo.get("submitted_by")
    C.assert_corroboration_post_hoc(build_inputs, wo.get("corroboration"))
    digest = S.canonical_sha256(build_inputs)
    if build.get("inputs_digest") and build["inputs_digest"] != digest:
        raise C.OrderInvertedError(
            "build stage inputs_digest does not match the recomputed digest"
        )

    # (3) corroborate 不得改变裁决
    corr = by_name.get("corroborate") or {}
    if corr:
        if corr.get("verdict_before") != corr.get("verdict_after"):
            raise C.OrderInvertedError(
                "corroboration changed the verdict — it must be post-hoc only"
            )
        if corr.get("affects_verdict") is not False:
            raise C.OrderInvertedError(
                "corroborate stage must declare affects_verdict=false"
            )
    if job.get("verdict") and by_name.get("verify"):
        if job["verdict"].get("hard_verdict") != by_name["verify"].get("hard_verdict"):
            raise C.OrderInvertedError(
                "final verdict differs from the verify-stage verdict"
            )


# ------------------------------------------------------------------ 主流程

def run_job(wo: dict, out_dir: Optional[str] = None,
            build_package: bool = False) -> dict:
    """跑一次完整流水线。返回 job 字典（含 stages / verdict / screening）。

    `out_dir` 给出时，落盘 job.json 与交付物（delivery.json + README + sha256）。
    """
    wo = dict(wo or {})
    if not wo.get("work_order_id"):
        wo["work_order_id"] = C.new_work_order_id(wo)

    job: Dict[str, Any] = {
        "schema": SCHEMA,
        "work_order_id": wo.get("work_order_id"),
        "work_order_digest": C.work_order_digest(wo),
        "work_order_summary": _summary(wo),
        "generated_at_utc": S.utc_now(),
        "stages": [],
        "_work_order": wo,          # 供 assert_order_integrity 复算
    }

    # ---- 1) intake ----
    v = C.validate_work_order(wo)
    job["stages"].append(_stage(
        "intake", v["ok"], errors=v["errors"], warnings=v["warnings"],
        digest=v["digest"]))
    if not v["ok"]:
        job["outcome"] = REJECTED_AT_INTAKE
        job["verdict"] = None
        job["screening"] = None
        _seal(job, out_dir, build_package)
        return job

    gate = S.load_gate()
    ledger = gate.load_ledger()

    # ---- 2) scope ----
    scope = _scope(wo, ledger or {})
    job["scope"] = scope
    job["stages"].append(_stage(
        "scope", scope["mode"] == "existing_kernel",
        mode=scope["mode"], discipline=scope.get("discipline_matched"),
        discipline_status=scope.get("discipline_status"),
        scenario_key=scope.get("scenario_key"),
        reason=scope.get("reason")))

    # ---- 3) build ----
    build = _build(wo, scope)
    job["stages"].append(_stage(
        "build", build["kind"] == "existing_kernel_scenario",
        kind=build["kind"], scenario_key=build.get("scenario_key"),
        independent_of_corroboration=True,
        inputs_digest=build["inputs_digest"], inputs_keys=build["inputs_keys"]))

    if scope["mode"] != "existing_kernel":
        job["outcome"] = NEEDS_ONBOARDING
        job["verdict"] = None
        job["screening"] = None
        job["onboarding_checklist"] = scope.get("onboarding_checklist")
        _seal(job, out_dir, build_package)
        return job

    # ---- 4) verify ----
    vres = _verify(wo, scope, ledger or {})
    job["verdict"] = vres["verdict"]
    job["stages"].append(_stage(
        "verify", vres["outcome"] not in (NO_LEDGER_FAIL_CLOSED,
                                         STALE_LEDGER_FAIL_CLOSED),
        hard_verdict=vres["verdict"].get("hard_verdict"),
        gate=vres["verdict"].get("gate"),
        cite_ok=(vres.get("cite") or {}).get("ok"),
        reason=vres["verdict"].get("reason"),
        ledger_generated_at_utc=vres.get("ledger_generated_at_utc")))

    # ---- 5) corroborate ----
    cres = _corroborate(wo, vres)
    job["screening"] = cres["screening"]
    job["stages"].append(_stage(
        "corroborate", True,
        counts=cres["screening"].get("counts"),
        verdict_before=cres["verdict_before"],
        verdict_after=cres["verdict_after"],
        verdict_unchanged=True, affects_verdict=False,
        parity=cres["parity"]))

    # ---- 6) deliver ----
    job["outcome"] = vres["outcome"]
    _seal(job, out_dir, build_package)

    # ---- 不变式：阶段单调 + 数据隔离 + 裁决未被作证改动 ----
    assert_order_integrity(job)
    return job


def _summary(wo: dict) -> dict:
    s = C.summarize(wo)
    s["goal"] = wo.get("goal")
    return s


def _seal(job: dict, out_dir: Optional[str], build_package: bool) -> None:
    """封口：记 deliver 阶段 → 落盘交付物 → 写 job.json。

    顺序有意为之——deliver 阶段先入列，交付物的 `stage_records` 才覆盖完整
    阶段序列（含 deliver 本身）。摘要自指的规避：交付物摘要只覆盖「payload
    去掉 integrity 块」，而 `job["delivery"]`（存放摘要本身）不参与 payload
    构建，故无循环依赖。
    """
    if build_package:
        job["vv_package"] = _build_vv_package(job, out_dir or ".")
    job["stages"].append(_stage("deliver", True, sealed=True,
                                vv_package=job.get("vv_package")))
    if not out_dir:
        return
    os.makedirs(out_dir, exist_ok=True)
    job["delivery"] = VD.finalize_delivery(job, out_dir)
    S.write_json(os.path.join(out_dir, "job.json"), _public(job))


def _build_vv_package(job: dict, out_dir: str) -> Optional[dict]:
    """可选：为已路由到内核场景的 job 构建可独立复验的 V&V 归档包。"""
    key = ((job.get("scope") or {}).get("scenario_key"))
    if not key:
        return None
    try:
        vp = S.load_module_by_path(
            "_vlab_vvpkg", os.path.join(S.ENGINE_DIR, "vv_package.py"))
        pkg_dir = os.path.join(out_dir, "vv_package")
        m = vp.build_package(pkg_dir, keys=[key], include_reports=False)
        ver = vp.verify_package(pkg_dir)
        return {
            "dir": pkg_dir,
            "package_id": (m.get("summary") or {}).get("package_id")
            or m.get("package_id"),
            "integrity": ver.get("status") or ver.get("verdict"),
            "n_files": (m.get("integrity") or {}).get("n_files"),
        }
    except Exception as e:                       # pragma: no cover
        return {"error": str(e)}


def _public(job: dict) -> dict:
    """去掉私有键后的可落盘副本。"""
    return {k: v for k, v in job.items() if not k.startswith("_")}


__all__ = [
    "SCHEMA", "REJECTED_AT_INTAKE", "NEEDS_ONBOARDING",
    "NO_LEDGER_FAIL_CLOSED", "STALE_LEDGER_FAIL_CLOSED",
    "run_job", "assert_order_integrity", "_scope", "_verify", "_corroborate",
]
