#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""VLab 否定性门禁 —— 证明这些闸门**真的会拦**。

为什么必须做否定性验证
----------------------
一个"永远返回 ok"的校验器和没有校验器是一样的，而且更糟——它给出虚假的
安全感。本项目已确立这条纪律（见 `vv-package-gate` 的篡改测试）。所以本
脚本的每一条都**故意构造一个应当被拒的输入**，然后断言它确实被拒：

    不是「通过测试」，是「证明它会拦住」。

覆盖的否定用例
--------------
契约层
  N1  缺字段（discipline / goal.unit）→ 拒
  N2  **读法 B：corroboration.as_input=true → 拒**（这条是全篇红线）
  N3  设计空间区间倒置 → 拒
阶段不变式
  N4  阶段乱序（corroborate 在 verify 前）→ 抛 OrderInvertedError
  N5  build 输入含未白名单键（真实观测）→ 抛
  N6  build 输入与某条作证点**逐字节相同** → 抛
专业接入
  N7  无 gold 的专业不得为 VALIDATED
  N8  只有散文式"引用"（无年份/DOI）→ 拒
  N9  指针解析不到 → 拒
  N10 声称的内核场景不在账本里 → 拒
锚定筛选
  N11 无引用 → REJECTED
  N12 量纲不符（degC vs g/L）→ REJECTED
  N13 机械全过但无人确认 → NEEDS_HUMAN（永不自动 ACCEPTED）
  N14 超出声明合理区间 → NEEDS_HUMAN
交付物
  N15 交付物改一字节 → DELIVERY_TAMPERED
工程纪律
  N16 vlab 各模块零第三方依赖（能在无 numpy 的干净解释器里跑）

正向对照（防止"一律拒绝"也能全绿）
  P1  合法工作单 → 通过，且 stage 序列完整、不变式成立

退出码：0 全绿；2 有断言失败。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE = os.path.join(ROOT, "engine")
VLAB_MODULES = (
    "vlab_stdlib", "vlab_contract", "discipline_registry",
    "anchor_intake", "vlab_orchestrator", "vlab_delivery",
    "scenario_router",
)


def _load(name: str):
    path = os.path.join(ENGINE, f"{name}.py")
    spec = importlib.util.spec_from_file_location(f"_g_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    # 必须在 exec_module 之前注册进 sys.modules —— @dataclass 装饰器会通过
    # sys.modules 查回模块对象；不注册会 AttributeError: 'NoneType' ... __dict__
    # （wet_lab_anchors.py 用了 @dataclass，其它 vlab 模块没用所以此前没暴露）
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class Report:
    def __init__(self, quiet: bool = False):
        self.quiet = quiet
        self.passed = 0
        self.failed = 0
        self.rows = []

    def check(self, name: str, cond: bool, detail: str = "") -> None:
        if cond:
            self.passed += 1
            status = "PASS"
        else:
            self.failed += 1
            status = "FAIL"
        self.rows.append((status, name, detail))
        if not self.quiet:
            print(f"  [{status}] {name}" + (f"  -- {detail}" if detail else ""))

    def raises(self, name: str, fn, exc_types) -> None:
        try:
            fn()
        except exc_types as e:
            self.check(name, True, f"raised {type(e).__name__}")
        except Exception as e:                        # noqa: BLE001
            self.check(name, False, f"raised unexpected {type(e).__name__}: {e}")
        else:
            self.check(name, False, "did NOT raise (gate failed to fire)")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    R = Report(args.quiet)
    C = _load("vlab_contract")
    DR = _load("discipline_registry")
    AI = _load("anchor_intake")
    VD = _load("vlab_delivery")
    O = _load("vlab_orchestrator")

    if not args.quiet:
        print("VLab negative gate — proving the gates actually fire\n")

    # ---------------------------------------------------------------- 契约层
    good = C.schema_doc()["example"]

    wo = json.loads(json.dumps(good))
    wo.pop("discipline")
    R.check("N1a missing 'discipline' rejected",
            C.validate_work_order(wo)["ok"] is False)

    wo = json.loads(json.dumps(good))
    wo["goal"].pop("unit")
    R.check("N1b missing goal.unit rejected (dimensional screening needs it)",
            C.validate_work_order(wo)["ok"] is False)

    # --- 红线：读法 B ---
    wo = json.loads(json.dumps(good))
    wo["corroboration"]["as_input"] = True
    r = C.validate_work_order(wo)
    R.check("N2 read-order B (real data as INPUT) rejected",
            r["ok"] is False
            and any(e["code"] == "ORDER_INVERTED" for e in r["errors"]),
            "the whole point: it is unrepresentable, not merely discouraged")

    wo = json.loads(json.dumps(good))
    wo["design_space"]["variables"][0]["lo"] = 99
    R.check("N3 inverted variable range rejected",
            C.validate_work_order(wo)["ok"] is False)

    # --- goal 与场景输出的绑定：量纲不兼容是拒收，量名写法差异只是告警 ---
    wo = json.loads(json.dumps(good))
    wo["goal"]["unit"] = "kmol/h"
    r = C.validate_work_order(wo)
    R.check("N17 goal unit dimensionally incompatible with the scenario output "
            "is rejected (a PASS scenario cannot answer the question asked)",
            r["ok"] is False
            and any(e["code"] == "goal_output_mismatch" for e in r["errors"]),
            "unit dimension is mechanically checkable, so it is a hard gate")

    wo = json.loads(json.dumps(good))
    wo["goal"]["unit"] = "mg/L"
    r = C.validate_work_order(wo)
    R.check("N18 unit variant within the same dimension still accepted",
            r["ok"] is True and not r["errors"],
            "mg/L is dimensionally equivalent to g/L")

    wo = json.loads(json.dumps(good))
    wo["goal"]["quantity"] = "biomass yield"
    r = C.validate_work_order(wo)
    R.check("N19 differently-worded quantity is a warning, not a rejection",
            r["ok"] is True
            and any(w["code"] == "goal_binding_warn" for w in r["warnings"]),
            "quantity names are free text; rejecting wording would kill valid requests")

    wo = json.loads(json.dumps(good))
    wo["discipline"] = "Chemistry"
    r = C.validate_work_order(wo)
    R.check("N20 discipline that declares no output quantity warns "
            "goal_binding_unverifiable instead of pretending the binding held",
            r["ok"] is True
            and any(w["code"] == "goal_binding_unverifiable" for w in r["warnings"]))

    # --- claim id：稳定指向「这条主张」，必须跨 design_space / 路由不变 ---
    cid = C.new_claim_id(good)
    R.check("N21 claim_id is derivable and matches the required format",
            bool(C.CLAIM_ID_RE.match(cid)),
            "a claim id that cannot be re-derived is not a stable handle")

    wo = json.loads(json.dumps(good))
    wo["design_space"]["variables"][0]["hi"] = 55
    R.check("N22 changing the design space does NOT change the claim id "
            "(the claim is about the quantity, not the sweep)",
            C.new_claim_id(wo) == cid
            and C.new_work_order_id(wo) != C.new_work_order_id(good),
            "work_order_id must still move; only claim_id is claim-scoped")

    wo = json.loads(json.dumps(good))
    wo["goal"]["quantity"] = "heat release rate"
    R.check("N23 changing the goal changes the claim id (it is a different claim)",
            C.new_claim_id(wo) != cid,
            "otherwise a claim store would silently return the wrong history")

    wo = json.loads(json.dumps(good))
    wo["claim_id"] = "claim-ffffffff"
    r = C.validate_work_order(wo)
    R.check("N24 a well-formed but content-mismatched claim_id warns "
            "claim_id_mismatch rather than being silently trusted",
            r["ok"] is True
            and any(w["code"] == "claim_id_mismatch" for w in r["warnings"]),
            "silently accepting it would make lookups by claim_id return "
            "someone else's data — worse than having no claim_id at all")

    wo = json.loads(json.dumps(good))
    wo["claim_id"] = "nope"
    R.check("N25 a malformed claim_id is rejected (error, not warning)",
            C.validate_work_order(wo)["ok"] is False)

    # --- scope 路由：编造的 scenario_key 不能伪装成已接入内核 ---
    VG = _load("verification_gate")
    OR = _load("vlab_orchestrator")
    SR = _load("scenario_router")
    led = VG.load_ledger()

    wo = json.loads(json.dumps(good))
    wo["scenario_key"] = "micro_totally_fabricated_key"
    sc = OR._scope(wo, led)
    R.check("N26 a fabricated scenario_key is NOT routed to existing_kernel",
            sc["mode"] == "discipline_onboarding_required"
            and sc["scenario_in_ledger"] is False,
            "claiming 'existing kernel' for a key absent from the ledger "
            "would make the caller believe a validated scenario exists")

    R.check("N27 scope failure surfaces suggested scenarios (not silence)",
            len(sc.get("suggested_scenarios") or []) >= 1,
            "76 scenarios were unreachable without knowing the exact key; "
            "a bare onboarding error leaves the caller with no way forward")

    # 建议不得改变 mode —— 路由器不能替调用方决定路由目标
    sc2 = OR._scope(json.loads(json.dumps(good)), led)
    R.check("N28 suggestions never upgrade the routing mode",
            sc2["mode"] == "existing_kernel"
            and not sc2.get("suggested_scenarios"),
            "an existing kernel scenario needs no suggestion, and a "
            "suggestion must never silently flip mode")

    # 路由器必须 fail-closed：只有专业域、没有任何文本/单位证据时拒绝
    R.check("N29 router refuses a discipline-only query (no text or unit signal)",
            SR.route({"discipline": "Microbiology", "quantity": "",
                       "unit": "", "variables": []})["hit"] is False,
            "guessing a scenario from the discipline prefix alone would "
            "deliver a PASS report that answers a different question")

    R.check("N30 router is deterministic (same query, same answer)",
            SR.route({"discipline": "Chemistry",
                       "quantity": "adsorption isotherm", "unit": "mol/g"})
            == SR.route({"discipline": "Chemistry",
                          "quantity": "adsorption isotherm", "unit": "mol/g"}))

    # ------------------------------------------------------------ 阶段不变式
    R.raises("N4 out-of-order stages raise",
             lambda: C.assert_stage_order([
                 {"stage": "intake"}, {"stage": "corroborate"},
                 {"stage": "verify"}]),
             (C.OrderInvertedError,))

    R.raises("N5 build input outside the whitelist raises",
             lambda: C.assert_corroboration_post_hoc(
                 {"observations": [1, 2, 3]}, None),
             (C.OrderInvertedError,))

    leak = {"y": 1.23, "unit": "g/L"}
    R.raises("N6 real point byte-identical to a build input raises",
             lambda: C.assert_corroboration_post_hoc(
                 {"goal": leak}, {"points": [dict(leak)]}),
             (C.OrderInvertedError,))

    # ------------------------------------------------------------ 专业接入
    spec = DR.DisciplineSpec(name="Ghost", category="Chemistry",
                             kernel_scenario_key="micro_ecoli_glucose", gold=[])
    R.check("N7 discipline without gold can never be VALIDATED",
            DR.status_of(spec) != DR.VALIDATED
            and DR.validate_discipline(spec)["errors"][0]["code"] == "no_gold")

    prose = DR.DisciplineSpec(
        name="Prose", category="Chemistry", kernel_scenario_key="micro_ecoli_glucose",
        gold=[DR.GoldSource(kind="citation", citation="a colleague mentioned it",
                            unit="g/L", value=1.0)])
    R.check("N8 prose-only 'citation' (no year/DOI) rejected",
            any(e["code"] == "gold_unverifiable"
                for e in DR.validate_discipline(prose)["errors"]))

    badptr = DR.DisciplineSpec(
        name="BadPtr", category="Chemistry", kernel_scenario_key="micro_ecoli_glucose",
        gold=[DR.GoldSource(kind="in_repo_pointer",
                            pointer="nonexistent/file.json#a.b",
                            unit="g/L", value=1.0)])
    R.check("N9 unresolvable in-repo pointer rejected",
            any(e["code"] == "gold_unresolvable"
                for e in DR.validate_discipline(badptr)["errors"]))

    notinled = DR.DisciplineSpec(
        name="NotInLedger", category="Chemistry",
        kernel_scenario_key="micro_scenario_that_does_not_exist",
        gold=[DR.GoldSource(kind="in_repo_pointer",
                            pointer="frontend_fixed/reports/wet_lab_anchors.json",
                            unit="g/L", value=1.0)])
    R.check("N10 claimed kernel scenario absent from the ledger rejected",
            any(e["code"] == "kernel_scenario_not_in_ledger"
                for e in DR.validate_discipline(notinled)["errors"]))

    # 正向对照：真接入的那个专业必须仍为 VALIDATED
    seeded = {s.name: s for s in DR.seeded_specs()}["Microbiology"]
    R.check("P2 the genuinely onboarded discipline is VALIDATED",
            DR.status_of(seeded) == DR.VALIDATED,
            "guards against 'reject everything' passing all negatives")

    # ------------------------------------------------------------ 锚定筛选
    cand = AI.ReferenceCandidate
    R.check("N11 citation-less reference REJECTED",
            AI.screen_candidate(cand(value=1.0, unit="g/L",
                                    citation="internal note", confirmed_by="x"),
                                "g/L")["verdict"] == AI.REJECTED)

    R.check("N12 dimensionally incompatible unit REJECTED",
            AI.screen_candidate(cand(value=1.0, unit="degC",
                                    citation="Foo 2020", confirmed_by="x"),
                                "g/L")["verdict"] == AI.REJECTED)

    R.check("N13 mechanically valid but unconfirmed -> NEEDS_HUMAN "
            "(never auto-ACCEPTED)",
            AI.screen_candidate(cand(value=0.5, unit="mg/L",
                                    citation="Foo 2020"), "g/L",
                                (1e-5, 0.01))["verdict"] == AI.NEEDS_HUMAN)

    R.check("N14 value outside the declared plausible band -> NEEDS_HUMAN",
            AI.screen_candidate(cand(value=990.0, unit="g/L",
                                    citation="Foo 2020", confirmed_by="x"),
                                "g/L", (1e-5, 0.01))["verdict"] == AI.NEEDS_HUMAN)

    ok = AI.screen_candidate(cand(value=0.5, unit="mg/L", citation="Bader 2013",
                                 confirmed_by="lexing"), "g/L", (1e-5, 0.01))
    R.check("P3 fully valid + confirmed reference ACCEPTED with normalization",
            ok["verdict"] == AI.ACCEPTED
            and abs(ok["normalized_value"] - 5e-4) < 1e-12)

    # ---------------------------------------------------------- 三维证据判据
    # 一个锚点能否被复用到新场景，必须三维同时匹配：
    #   实验体系 × 版本 × 工况
    # 缺任一维 = 不可自动迁移，只能人工参考。缺维度一律标记为 UNANCHORED，
    # 不编造、不用 "unknown" 蒙混。灵感来源："质链 Agent"的三维证据判定。
    WLA = _load("wet_lab_anchors")

    # N31: decision_relevant 锚点缺任一维 → 必须报未完全锚定
    incomplete_anchor = WLA.Anchor(
        key="N31_test_incomplete", label="Test", unit="g/L",
        lo=1.0, hi=2.0, note="test",
        sources=("Foo 2020",), scenarios=(),
        decision_relevant=True,
        experiment_system="batch_culture",
        version="UNANCHORED",   # 缺版本
        operating_condition="37C",
    )
    N31_ec = WLA.evidence_completeness(incomplete_anchor)
    R.check("N31 decision_relevant anchor missing a dimension is flagged "
            "(never silently trusted as 3D)",
            not N31_ec["fully_anchored"]
            and N31_ec["filled"] == 2
            and "version" in N31_ec["missing"],
            f"filled={N31_ec['filled']} missing={N31_ec['missing']}")

    # N32 正向对照：全三维填好的锚点必须报 fully_anchored
    # （这条防的是"一律 UNANCHORED"的偷懒实现）
    complete_anchor = WLA.Anchor(
        key="N32_test_complete", label="Test", unit="g/L",
        lo=1.0, hi=2.0, note="test",
        sources=("Foo 2020",), scenarios=(),
        decision_relevant=True,
        experiment_system="batch_culture", version="some_strain",
        operating_condition="37C",
    )
    N32_ec = WLA.evidence_completeness(complete_anchor)
    R.check("N32 fully-dimensioned anchor is reported fully_anchored "
            "(guards against 'mark everything UNANCHORED' passing trivially)",
            N32_ec["fully_anchored"] and N32_ec["filled"] == 3)

    # N33 空字符串等价于 UNANCHORED（防止用 "" 或 None 蒙混）
    blank_anchor = WLA.Anchor(
        key="N33_test_blank", label="Test", unit="g/L",
        lo=1.0, hi=2.0, note="test",
        sources=("Foo 2020",), scenarios=(),
        experiment_system="batch_culture", version="",
        operating_condition=None,
    )
    N33_ec = WLA.evidence_completeness(blank_anchor)
    R.check("N33 empty-string / None dimension is treated as UNANCHORED "
            "(no way to sneak through a blank)",
            N33_ec["filled"] == 1
            and "version" in N33_ec["missing"]
            and "operating_condition" in N33_ec["missing"],
            f"filled={N33_ec['filled']} missing={N33_ec['missing']}")

    # P6 当前库诚实度：所有 decision_relevant=True 锚点必须三维完整
    # 这条会在有人添加一条「声明能自动决策但只有 1-2 维」的锚点时立刻报红
    cov = WLA.anchors_3d_coverage()
    R.check("P6 all decision_relevant anchors in the current library "
            "are fully 3D-anchored",
            not cov["decision_relevant_not_fully_anchored"],
            f"unanchored={cov['decision_relevant_not_fully_anchored']}")

    # -------------------------------------------------------------- 交付物
    with tempfile.TemporaryDirectory() as td:
        job = O.run_job(good, out_dir=td)
        dpath = job["delivery"]["json"]
        R.check("P4 delivery verifies as INTACT",
                VD.verify_delivery(dpath)["status"] == "DELIVERY_INTACT")

        doc = json.load(open(dpath, encoding="utf-8"))
        # 保证真的改变了字节：把裁决改成与当前不同的值
        cur = (doc.get("verdict") or {}).get("hard_verdict")
        doc["verdict"]["hard_verdict"] = "MARGINAL" if cur != "MARGINAL" else "PASS"
        bad = os.path.join(td, "tampered.json")
        with open(bad, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False)
        R.check("N15 a single-field change to the delivery -> TAMPERED",
                VD.verify_delivery(bad)["status"] == "DELIVERY_TAMPERED",
                f"flipped hard_verdict {cur!r} -> "
                f"{(doc.get('verdict') or {}).get('hard_verdict')!r}; "
                "a verifier that always says INTACT is not a verifier")

    # -------------------------------------------------------------- 工程纪律
    third_party = ("numpy", "scipy", "pandas", "sklearn", "torch", "requests")
    offenders = []
    for mod in VLAB_MODULES:
        src = open(os.path.join(ENGINE, f"{mod}.py"), encoding="utf-8").read()
        for line in src.splitlines():
            s = line.strip()
            if s.startswith(("import ", "from ")) and any(
                    f" {t}" in s or s.startswith(f"import {t}") for t in third_party):
                offenders.append(f"{mod}: {s}")
    R.check("N16 vlab modules are third-party-free (runs on a clean interpreter)",
            not offenders, "; ".join(offenders[:4]))

    # ------------------------------------------------------------ 端到端正向
    job = O.run_job(good)
    stages = [s["stage"] for s in job["stages"]]
    R.check("P1a valid work order completes the full stage sequence",
            stages == list(C.STAGE_ORDER), f"stages={stages}")

    # 账本新鲜度是**独立**的一条闸门。engine_fingerprint() 覆盖 engine/ 下
    # 全部 .py，所以**任何**引擎改动（包括新增一个无关模块）都会让 all_digest
    # 变化 → 账本过期 → 强制重验。这是既有设计（防"过期信任"），不是缺陷。
    # 但必须把它与"流水线坏了"分开报，否则一次引擎编辑会让 P1 以
    # STALE_LEDGER_FAIL_CLOSED 的形式伪装成流水线 bug。
    g = _load("verification_gate")
    stale = g.ledger_is_stale()
    R.check("P1b gate ledger is fresh w.r.t. the current engine",
            not stale.get("stale"),
            ("remedy: python scripts/build_gate_ledger.py "
             f"(ledger={str(stale.get('ledger_all_digest'))[:12]} vs "
             f"current={str(stale.get('current_all_digest'))[:12]})")
            if stale.get("stale") else "")

    R.check("P1c a ledger-backed scenario yields an actual verdict",
            job["outcome"] == "PROCEED",
            f"outcome={job['outcome']} — if this is STALE_LEDGER_FAIL_CLOSED, "
            "P1b above is the real failure, not the pipeline")

    R.check("P5 build stage never saw the real data",
            job["stages"][2].get("independent_of_corroboration") is True
            and "corroboration" not in (job["stages"][2].get("inputs_keys") or []))

    # ------------------------------------------------------------------ 汇总
    total = R.passed + R.failed
    if args.quiet:
        print(f"VLab gate: {R.passed}/{total} passed, {R.failed} failed")
    else:
        print(f"\n{'=' * 62}")
        print(f"VLab negative gate: {R.passed}/{total} passed, {R.failed} failed")
    return 0 if R.failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
