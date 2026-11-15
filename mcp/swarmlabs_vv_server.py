#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SwarmLabs V&V MCP server — an independent verification gate for ANY AI4S model.

Why this exists (strategic context, 2026-09):
  Frontier research agents (GPT-6 Astra on the Insilico DDD benchmark, Stanford
  Biomni, DrugAgent, ...) now *generate* scientific results with no bioinformatics
  plugin and no fine-tuning. The reviews are unanimous: generic LLM agents are
  "prone to plausible-but-wrong outputs, weak guardrails" and every deployment
  needs "a held-out benchmark + quantified uncertainty + a human checkpoint."

  SwarmLabs is that checkpoint. This server exposes the SwarmLabs virtual-experiment
  V&V engine (engine/surrogate.py + engine/report_generator.py) as MCP tools so a
  research agent built on ANY base model can:
    - list / inspect the 95 published scenarios
    - pull a full 10-chapter V&V report
    - run an INDEPENDENT verification gate on its OWN predictions
      (verify_prediction): we know the ground truth, the agent does not,
      so we return an honest R^2 / coverage / calibration verdict.
    - run a 5-stage agentic verification loop with a hard confidence gate
    - read the first-class uncertainty budget (noise floor, kappa, coverage)

This turns "being disrupted by GPT-6" into "being integrated into GPT-6's pipeline
as the trust layer."

Honesty constraints (carried from the engine):
  - 3% noise floor is never lowered.
  - coverage is single-sided & conservative (over-confident narrow bands FAIL).
  - calibration kappa only widens uncertainty, never tightens (capped at 40).

Run:  python mcp/swarmlabs_vv_server.py            # stdio MCP server
      python mcp/swarmlabs_vv_server.py --selftest # offline smoke test, no client
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# --- make the repo importable regardless of cwd ---------------------------------
ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

import numpy as np  # noqa: E402

from mcp.server.fastmcp import FastMCP  # noqa: E402

from engine import report_generator as RG  # noqa: E402
from engine.report_generator import (  # noqa: E402
    GRADE_MARGINAL,
    GRADE_PASS,
    GRADE_REFUTED,
    MARGINAL_R2,
    NOMINAL_COVERAGE,
    NOISE_FLOOR,
    PASS_R2,
    Z95,
    _noise_free_f,
    _r2,
    _verdict,
    build_report,
    collect_evidence,
    get_scenario,
    render_docx,
    render_html,
    render_markdown,
)

# 门禁口径唯一来源：engine/verification_gate.py（与 HTTP 账本、飞轮硬闸门共用）
from engine.verification_gate import gate_for_verdict as _gate_decision  # noqa: E402

mcp = FastMCP("swarmlabs-vv")

# =============================================================================
# Helpers
# =============================================================================

_INDEX_PATH = ROOT / "frontend_fixed" / "reports" / "index.json"


def _load_index() -> Optional[dict]:
    try:
        return json.loads(_INDEX_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def _clean_oracle(key: str):
    """Return the noise-free ground-truth oracle for a scenario (we know truth)."""
    scn = get_scenario(key)
    return _noise_free_f(dict(scn))


# =============================================================================
# Tools
# =============================================================================


@mcp.tool()
def list_scenarios() -> dict:
    """List all published V&V scenarios with metadata.

    Returns {count, scenarios:[{key, domain, category, dim, published, references}]}.
    Use this to discover which scenario a model's prediction should be verified against.
    """
    reg = RG.all_scenarios()
    out = []
    for k, s in reg.items():
        out.append(
            {
                "key": k,
                "domain": s.get("domain", ""),
                "category": s.get("category", ""),
                "dim": int(s.get("dim", len(s.get("bounds", [])))),
                "published": s.get("published", ""),
                "references": s.get("references", [])[:3],
            }
        )
    return {"count": len(out), "scenarios": out}


@mcp.tool()
def get_verdict_summary() -> dict:
    """Return the published, machine-readable verdict summary (PASS/MARGINAL/REFUTED/ERROR).

    Numbers come from the live report index (frontend_fixed/reports/index.json) —
    the same numbers shown on swarmlabs.tools/report-gallery. Honest and reproducible.
    """
    idx = _load_index()
    if idx is None:
        return {"error": "index.json not found; run scripts/build_reports.py first"}
    return {
        "counts": idx.get("counts"),
        "total": idx.get("total"),
        "standard": idx.get("standard"),
        "generated_at_utc": idx.get("generated_at_utc"),
    }


@mcp.tool()
def get_report(key: str, fmt: str = "md") -> str:
    """Generate a full 10-chapter V&V report for one scenario.

    Args:
      key: scenario key (see list_scenarios).
      fmt: "md" (markdown, default), "html", "json", or "docx-b64" (base64 of .docx).
    Returns the rendered report as a string (docx returned base64-encoded).
    """
    ev = collect_evidence(get_scenario(key))
    rep = build_report(ev)
    if fmt == "json":
        return json.dumps(rep, ensure_ascii=False, indent=2, default=str)
    if fmt == "html":
        return render_html(rep)
    if fmt == "docx-b64":
        import base64

        return base64.b64encode(render_docx(rep)).decode("ascii")
    return render_markdown(rep)


@mcp.tool()
def get_uncertainty_budget(key: str) -> dict:
    """First-class uncertainty budget for a scenario — the numbers that decide trust.

    Returns noise_floor, calibration_scale (kappa), coverage, nominal_coverage,
    cal_err, r2, r2_core. These are the quantitative honesty guarantees a research
    agent should read before acting on a prediction.
    """
    ev = collect_evidence(get_scenario(key))
    m = ev["metrics"]
    return {
        "key": key,
        "noise_floor": NOISE_FLOOR,
        "calibration_scale_kappa": ev["calibration_scale"],
        "coverage": m["coverage"],
        "nominal_coverage": m["nominal_coverage"],
        "coverage_gap": m["coverage_gap"],
        "cal_err": m["cal_err"],
        "r2": m["r2"],
        "r2_core": m["r2_core"],
        "verdict": ev["verdict"]["grade"],
    }


@mcp.tool()
def verify_prediction(scenario_key: str, predictions: List[dict]) -> dict:
    """INDEPENDENT VERIFICATION GATE for an external model's own predictions.

    The strongest value of SwarmLabs: a research agent (GPT-6 / Biomni / DrugAgent /
    your own) calls this with the points it predicted. We own the ground truth, the
    agent does not — so we return an honest verdict on ITS predictions.

    Args:
      scenario_key: which scenario the predictions correspond to (see list_scenarios).
      predictions: list of {"x": [float,...], "y_pred": float, "y_std"?: float}.
                    x is the input point (length == scenario dim).
                    y_pred is the agent's predicted output.
                    y_std is OPTIONAL — the agent's own uncertainty estimate (1-sigma).
                    If provided, coverage & calibration are computed; if absent, only
                    R^2 is computed and coverage is reported null.

    Returns:
      {n_points, r2, coverage (or null), calibration_kappa_suggestion,
       verdict (PASS/MARGINAL/REFUTED), rationale, gate (PROCEED/.../BLOCK)}
    """
    oracle = _clean_oracle(scenario_key)
    xs, yp, ys = [], [], []
    has_std = True
    for p in predictions:
        xs.append(np.asarray(p["x"], float))
        yp.append(float(p["y_pred"]))
        s = p.get("y_std")
        if s is None:
            has_std = False
            ys.append(np.nan)
        else:
            ys.append(float(s))
    xs = np.asarray(xs, float)
    yp = np.asarray(yp, float)
    ys = np.asarray(ys, float)
    yt = np.array([float(oracle(x)) for x in xs], float)

    r2 = float(_r2(yt, yp))
    out: Dict[str, Any] = {"scenario_key": scenario_key, "n_points": int(len(xs)), "r2": round(r2, 4)}

    if has_std:
        within = np.abs(yt - yp) <= Z95 * ys
        coverage = float(np.mean(within))
        # calibration: agent's stated 1-sigma should match empirical residual spread
        resid_std = float(np.std(yt - yp)) + 1e-9
        mean_agent_std = float(np.mean(ys)) + 1e-9
        kappa_sugg = float(min(max(resid_std / mean_agent_std, 1.0), 40.0))
        verdict, why = _verdict(r2, coverage)
        out.update(
            {
                "coverage": round(coverage, 4),
                "nominal_coverage": NOMINAL_COVERAGE,
                "calibration_kappa_suggestion": round(kappa_sugg, 3),
                "agent_overconfident": bool(coverage < 0.90 and kappa_sugg > 1.2),
            }
        )
    else:
        # no interval supplied: provisional verdict on R^2 only
        if r2 >= PASS_R2:
            verdict, why = GRADE_PASS, f"R²={r2:.3f} ≥ {PASS_R2} (coverage not supplied)"
        elif r2 >= MARGINAL_R2:
            verdict, why = GRADE_MARGINAL, f"R²={r2:.3f} ≥ {MARGINAL_R2} (coverage not supplied)"
        else:
            verdict, why = GRADE_REFUTED, f"R²={r2:.3f} < {MARGINAL_R2} (coverage not supplied)"
        out["coverage"] = None
        out["calibration_kappa_suggestion"] = None

    out.update(
        {
            "verdict": verdict,
            "rationale": why,
            "gate": _gate_decision(verdict),
        }
    )
    return out


@mcp.tool()
def verification_gate(key: Optional[str] = None) -> dict:
    """Static trust decision from the published gate ledger (no engine needed).

    Answers "can this scenario be trusted right now?" — the same machine-readable
    ledger served over HTTP at GET /v3/gate (and /v3/gate/{key}).

    Args:
      key: optional scenario key. If omitted, returns the summary + blocked list.

    gate values: PROCEED / PROCEED_WITH_HUMAN_CHECK / BLOCK_AUTONOMOUS_ACTION.
    A BLOCK means: do not act autonomously on this scenario until a human or more
    data intervenes.
    """
    try:
        from engine.verification_gate import (
            gate_for_scenario,
            load_ledger,
            ledger_is_stale,
            summarize,
        )
    except Exception as e:  # pragma: no cover
        return {"error": f"gate ledger unavailable: {e}"}

    # 指纹新鲜度：账本对哪版引擎算的？如果 stale，提示调用方（fail-closed 由
    # assert_ledger_not_stale 在 assert_action_allowed 里强制）。
    _stale = ledger_is_stale()
    staleness_hint = ({
        "available": _stale.get("available"),
        "stale": _stale.get("stale"),
        "ledger_all_digest": _stale.get("ledger_all_digest"),
        "current_all_digest": _stale.get("current_all_digest"),
        "note": ("ledger reflects an older engine revision; recompute via "
                 "scripts/build_gate_ledger.py before trusting any verdict below"),
    } if _stale.get("stale") else None)

    if key:
        from engine.verification_gate import load_ledger

        led = load_ledger()
        if not led:
            return {"error": "gate ledger missing; run scripts/build_gate_ledger.py"}
        entry = (led.get("scenarios") or {}).get(key)
        if not entry:
            return {"error": f"unknown scenario key {key!r}", "total": led.get("total")}
        return {
            "scenario_key": key,
            "gate": entry.get("gate"),
            "verdict": entry.get("verdict"),
            "staleness": staleness_hint,
            "rationale": entry.get("rationale"),
            "uncertainty": entry.get("uncertainty"),
        }
    return summarize()


@mcp.tool()
def agentic_verify_loop(scenario_key: str, n_train: Optional[int] = None,
                       n_test: Optional[int] = None) -> dict:
    """5-stage agentic verification loop with a HARD confidence gate.

    Stages map to the SwarmLabs trust loop (and align with the 5-state board +
    the daily flywheel):
      1. surrogate_fit      — GP surrogate fit from training points (R^2, RMSE)
      2. prediction_uq      — out-of-sample coverage + calibration kappa
      3. ood_gate          — out-of-distribution red-zone probe
      4. replication        — re-run reproducibility check
      5. confidence_gate    — final verdict + BLOCK/PROCEED decision

    A research agent should treat stage 5 as a hard stop: if verdict is REFUTED,
    autonomous action on this scenario is blocked until a human or more data.

    Returns the structured loop + the gate decision.
    """
    ev = collect_evidence(get_scenario(scenario_key),
                          n_train=n_train, n_test=n_test)
    m = ev["metrics"]
    return {
        "scenario_key": scenario_key,
        "stage_1_surrogate_fit": {
            "r2": m["r2"], "r2_core": m["r2_core"], "rmse_rel": m["rmse_rel"]
        },
        "stage_2_prediction_uq": {
            "coverage": m["coverage"],
            "nominal_coverage": m["nominal_coverage"],
            "calibration_scale_kappa": ev["calibration_scale"],
            "cal_err": m["cal_err"],
        },
        "stage_3_ood_gate": ev.get("ood_gate"),
        "stage_4_replication": ev.get("replication"),
        "stage_5_confidence_gate": {
            "verdict": ev["verdict"]["grade"],
            "rationale": ev["verdict"]["rationale"],
            "gate": _gate_decision(ev["verdict"]["grade"]),
        },
    }


@mcp.tool()
def calibrated_verdict(
    key: str,
    cost_of_error: float = 1.0,
) -> dict:
    """Calibrated-probability version of the verification gate.

    借鉴点（2026-09-20）：
      - Jev noul 模式：把离散 verdict 映射为连续概率分布（4 档）
      - Jev Confidence 派生：confidence 从概率分布派生，不是独立字段
      - Jev Act/Review/Human 三路径：错误成本参数化阈值
      - Laya typed-decisions：一次返回 4 档概率，多问题并行

    与 verification_gate 的区别：
      - verification_gate 返回离散 verdict（PASS/MARGINAL/REFUTED/UNVALIDATED）
        + gate（PROCEED/PROCEED_WITH_HUMAN_CHECK/BLOCK_AUTONOMOUS_ACTION）
      - calibrated_verdict 在同一份 ledger 上**并行独立评估**同一个场景，
        返回 4 档概率 + 从分布派生的 confidence + Act/Review/Human 建议

    Args:
      key: 场景 key（例如 "bio_logistic"、"microbio_monod"）
      cost_of_error: 错误成本权重（0-10），决定 Act/Review/Human 阈值
        - <1.0  低风险（默认 1.0）：宽松阈值，Act 更容易触发
        - 1.5-3.0 中等风险：默认阈值
        - >=3.0 高风险（涉及人/资金/合规）：保守阈值，更多 REVIEW

    Returns:
      {
        "scenario_key": str,
        "hard_verdict": "PASS|MARGINAL|REFUTED|UNVALIDATED|ERROR",
        "probabilities": {"PASS", "MARGINAL", "REFUTED", "UNVALIDATED"},
        "confidence": float,
        "recommendation": "ACT|REVIEW|HUMAN",
        "evidence": {...},
        "cost_of_error": float,
        "cost_thresholds": {"pass", "review"},
        "rationale": str,
        "domain": str,
        "staleness": {optional ledger-staleness hint},
      }
    """
    try:
        from engine.verification_gate import (
            calibrated_verdict as _cal,
            ledger_is_stale,
        )
    except Exception as e:  # pragma: no cover
        return {"error": f"calibrated_verdict unavailable: {e}"}

    _stale = ledger_is_stale()
    staleness_hint = ({
        "stale": _stale.get("stale"),
        "note": "ledger engine fingerprint stale — recompute via "
                "scripts/build_gate_ledger.py before trusting any probability",
    } if _stale.get("stale") else None)

    result = _cal(key, cost_of_error=cost_of_error)
    result["scenario_key"] = key
    result["staleness"] = staleness_hint
    return result


@mcp.tool()
def calibrated_verdict_batch(
    keys: list,
    cost_of_error: float = 1.0,
) -> dict:
    """Batch version of calibrated_verdict (borrowed from Jev parallel evaluation).

    一次调用返回多个场景的校准概率 —— Laya/Jev 都强调"多问题并行独立评估，
    增加问题几乎不改变响应时间"，这里是等价的批量入口。

    Args:
      keys: 场景 key 列表
      cost_of_error: 错误成本权重，同 calibrated_verdict

    Returns:
      { "scenarios": { "<key>": <calibrated_verdict result>, ... },
        "summary": { "n", "by_recommendation", "mean_confidence" } }
    """
    try:
        from engine.verification_gate import calibrated_verdict_batch as _batch

        led = None
        try:
            from engine.verification_gate import load_ledger
            led = load_ledger()
        except Exception:
            pass  # noqa: silent-ok — Exception 可安全忽略（已审计 2026-10-08）

        results = _batch(keys, led, cost_of_error)
        # 摘要
        recos: dict = {}
        confs: list = []
        for r in results.values():
            rec = r.get("recommendation", "?")
            recos[rec] = recos.get(rec, 0) + 1
            confs.append(r.get("confidence", 0.0))
        return {
            "scenarios": results,
            "summary": {
                "n": len(results),
                "by_recommendation": recos,
                "mean_confidence": round(float(sum(confs) / len(confs)), 4) if confs else None,
            },
        }
    except Exception as e:  # pragma: no cover
        return {"error": f"calibrated_verdict_batch failed: {e}"}


# =============================================================================
# Self-test (no MCP client needed)
# =============================================================================

def _selftest() -> None:
    print("== list_scenarios ==", list_scenarios()["count"], "scenarios")
    print("== get_verdict_summary ==", get_verdict_summary().get("counts"))
    s = RG.all_scenarios()
    key = next(iter(s))
    print("== get_uncertainty_budget ==", get_uncertainty_budget(key))
    # gate: feed the model its own (noiseless) predictions -> should PASS
    oracle = _clean_oracle(key)
    scn = s[key]
    dim = int(scn.get("dim", len(scn.get("bounds", []))))
    rng = np.random.default_rng(0)
    b = np.asarray(scn["bounds"], float)
    X = (b[:, 0:1] + rng.random((dim, 5)) * (b[:, 1:2] - b[:, 0:1])).T
    preds = [{"x": X[i].tolist(), "y_pred": float(oracle(X[i]))} for i in range(5)]
    print("== verify_prediction (oracle self, no std) ==", verify_prediction(key, preds)["verdict"])
    print("== agentic_verify_loop ==", agentic_verify_loop(key)["stage_5_confidence_gate"]["gate"])
    print("== verification_gate (summary) ==", verification_gate().get("n_blocked"), "blocked")
    print("SELFTEST OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        mcp.run()
