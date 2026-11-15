#!/usr/bin/env python3
"""baseline_ladder.py — Enforce the SwarmLabs baseline ladder for model admission.

Source: PROJECT_ELEVATION_REPORT_2026-10-26 §3.2「建立 baseline ladder：最后值/均值
→ 纯机理 → 纯数据 → 混合模型。任何复杂模型必须击败更简单基线才允许上线」。

Ladder hierarchy (from simplest to most complex):
  L0  naive — last-value / mean (no model, no parameters)
  L1  mechanism — pure physics/ODE model (no learning)
  L2  data — pure statistical model (no physics prior, e.g. linear regression)
  L3  hybrid — mechanism + learned residual (mechanism + GP/data-driven correction)

Admission rule: a model at level Lx can only be admitted if it strictly beats
every lower-level baseline on the pre-registered metric. If L3 doesn't beat L2,
don't ship L3.

Usage:
    from baseline_ladder import LadderConfig, evaluate, ADMITTED, REJECTED
    cfg = LadderConfig(metric="rmse_rel", lower_is_better=True, min_improvement=0.01)
    verdict = evaluate(
        naive=0.45, mechanism=0.32, data=0.18, hybrid=0.09, cfg=cfg
    )
    # -> ADMITTED("hybrid", reasons=[...])

    python scripts/baseline_ladder.py --selftest
    python scripts/baseline_ladder.py --ci
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from typing import Any, Optional


# ---------- Ladder levels ----------

LEVELS = ("naive", "mechanism", "data", "hybrid")
LEVEL_RANK = {name: i for i, name in enumerate(LEVELS)}


@dataclass(frozen=True)
class LadderConfig:
    """Configuration for baseline ladder evaluation.

    metric: name of the comparison metric (e.g. "rmse_rel", "mae", "coverage")
    lower_is_better: True for error metrics (rmse, mae, loss), False for coverage
    min_improvement: minimum absolute improvement required over next-lower baseline.
        Default 0.01 (1%) is a reasonable tolerance for synthetic benchmarks;
        raise it to 0.03-0.05 for real experiments to avoid over-claiming.
    """
    metric: str = "rmse_rel"
    lower_is_better: bool = True
    min_improvement: float = 0.01


@dataclass(frozen=True)
class LadderVerdict:
    """Result of evaluating a model at some ladder level."""
    level: str
    admitted: bool
    reasons: tuple[str, ...]
    improvements: dict[str, float] = field(default_factory=dict)
    beats_baseline: Optional[str] = None  # which lower-level baseline was beaten


ADMITTED = "ADMITTED"
REJECTED = "REJECTED"


def _better(val_a: float, val_b: float, lower_is_better: bool) -> bool:
    """Return True if val_a strictly beats val_b under the metric direction."""
    if lower_is_better:
        return val_a < val_b
    return val_a > val_b


def _strict_beats(val_new: float, val_base: float,
                  cfg: LadderConfig) -> tuple[bool, float]:
    """Return (beats_by_at_least_min, improvement). Improvement is signed: positive = better.

    Convention: `min_improvement` is the minimum required improvement (>=).
    """
    if cfg.lower_is_better:
        improvement = val_base - val_new  # positive when new is lower
        return improvement >= cfg.min_improvement, improvement
    improvement = val_new - val_base  # positive when new is higher
    return improvement >= cfg.min_improvement, improvement


def evaluate(
    naive: float,
    mechanism: Optional[float] = None,
    data: Optional[float] = None,
    hybrid: Optional[float] = None,
    cfg: Optional[LadderConfig] = None,
) -> LadderVerdict:
    """Evaluate whether each ladder level is admitted.

    Args:
        naive: metric value of the naive baseline (last-value / mean)
        mechanism: metric value of pure mechanism model (optional)
        data: metric value of pure data model (optional)
        hybrid: metric value of hybrid model (optional)
        cfg: LadderConfig

    Returns:
        LadderVerdict for the HIGHEST available level, with improvements dict
        showing improvement over each lower level that was available.
    """
    cfg = cfg or LadderConfig()
    levels: dict[str, float] = {"naive": naive}
    if mechanism is not None:
        levels["mechanism"] = mechanism
    if data is not None:
        levels["data"] = data
    if hybrid is not None:
        levels["hybrid"] = hybrid

    # Highest available level determines the candidate
    candidate_level = max(levels.keys(), key=lambda k: LEVEL_RANK[k])
    candidate_val = levels[candidate_level]
    candidate_rank = LEVEL_RANK[candidate_level]

    improvements: dict[str, float] = {}
    reasons: list[str] = []

    # For every lower level, check that candidate strictly beats it
    for other_level, other_val in levels.items():
        if other_level == candidate_level:
            continue
        other_rank = LEVEL_RANK[other_level]
        if other_rank < candidate_rank:
            beats, improvement = _strict_beats(candidate_val, other_val, cfg)
            improvements[other_level] = improvement
            if not beats:
                reasons.append(
                    f"candidate {candidate_level} (={candidate_val:.4f}) does not "
                    f"strictly beat {other_level} (={other_val:.4f}) "
                    f"by >= {cfg.min_improvement} (improvement={improvement:+.4f})"
                )

    admitted = len(reasons) == 0
    return LadderVerdict(
        level=candidate_level,
        admitted=admitted,
        reasons=tuple(reasons),
        improvements=improvements,
    )


def _evaluate_candidate(
    candidate_level: str,
    candidate_val: float,
    lower_levels: dict[str, float],
    cfg: LadderConfig,
) -> LadderVerdict:
    """Evaluate `candidate_level` (val=candidate_val) against `lower_levels` baselines.

    Returns a LadderVerdict with .level=candidate_level and .admitted based on
    whether candidate strictly beats every lower level by >= cfg.min_improvement.
    """
    improvements: dict[str, float] = {}
    reasons: list[str] = []
    for other_level, other_val in lower_levels.items():
        beats, improvement = _strict_beats(candidate_val, other_val, cfg)
        improvements[other_level] = improvement
        if not beats:
            reasons.append(
                f"candidate {candidate_level} (={candidate_val:.4f}) does not strictly beat "
                f"{other_level} (={other_val:.4f}) by >= {cfg.min_improvement} "
                f"(improvement={improvement:+.4f})"
            )
    return LadderVerdict(
        level=candidate_level,
        admitted=len(reasons) == 0,
        reasons=tuple(reasons),
        improvements=improvements,
    )


def admit_up_to(
    naive: float,
    mechanism: Optional[float] = None,
    data: Optional[float] = None,
    hybrid: Optional[float] = None,
    cfg: Optional[LadderConfig] = None,
) -> tuple[str, LadderVerdict]:
    """Return the highest level that is admitted, cascading down from hybrid.

    Practical admission API: tells you the SIMPLEST model that meets the bar,
    avoiding over-engineering. Returns (level_name, verdict).
    """
    cfg = cfg or LadderConfig()
    candidates: list[tuple[str, Optional[float]]] = [
        ("hybrid", hybrid),
        ("data", data),
        ("mechanism", mechanism),
        ("naive", naive),
    ]
    for level, val in candidates:
        if val is None:
            continue
        # Collect lower-level baselines (only those actually provided)
        lower: dict[str, float] = {}
        for lower_level, lower_val in candidates:
            if LEVEL_RANK[lower_level] < LEVEL_RANK[level] and lower_val is not None:
                lower[lower_level] = lower_val
        if not lower:
            # naive with no lower baselines — trivially admitted
            return level, LadderVerdict(
                level=level, admitted=True, reasons=(), improvements={},
            )
        verdict = _evaluate_candidate(level, val, lower, cfg)
        if verdict.admitted:
            return level, verdict
    # Nothing except naive got admitted — but naive IS the baseline, admit it
    return "naive", LadderVerdict(level="naive", admitted=True, reasons=(), improvements={})


# ---------- Selftest ----------

def _selftest() -> dict[str, Any]:
    """Selftest for both evaluate() and admit_up_to().

    api="evaluate" tests the highest-available-level API.
    api="admit_up_to" tests the cascade API (return highest admitted).
    """
    cases = [
        {
            "name": "L3 hybrid strictly beats L0/L1/L2 — ADMITTED",
            "args": dict(naive=0.45, mechanism=0.32, data=0.18, hybrid=0.09),
            "cfg": LadderConfig(min_improvement=0.01),
            "api": "evaluate",
            "expect_admitted": True,
            "expect_level": "hybrid",
        },
        {
            "name": "L3 hybrid barely beats L2 (0.005 improvement < 0.01 min) — REJECTED",
            "args": dict(naive=0.45, mechanism=0.32, data=0.10, hybrid=0.095),
            "cfg": LadderConfig(min_improvement=0.01),
            "api": "evaluate",
            "expect_admitted": False,
            "expect_level": "hybrid",
        },
        {
            "name": "L2 data beats both L0 naive AND L1 mechanism — ADMITTED at data",
            "args": dict(naive=0.10, mechanism=0.15, data=0.05),
            "cfg": LadderConfig(min_improvement=0.01),
            "api": "evaluate",
            "expect_admitted": True,
            "expect_level": "data",
        },
        {
            "name": "Cascade via admit_up_to: data WORSE than mechanism → admit mechanism",
            "args": dict(naive=0.10, mechanism=0.05, data=0.08),
            "cfg": LadderConfig(min_improvement=0.01),
            "api": "admit_up_to",
            "expect_admitted": True,
            "expect_level": "mechanism",
        },
        {
            "name": "Cascade via admit_up_to: hybrid not admitted → fall back to data",
            "args": dict(naive=0.45, mechanism=0.32, data=0.10, hybrid=0.095),
            "cfg": LadderConfig(min_improvement=0.01),
            "api": "admit_up_to",
            "expect_admitted": True,
            "expect_level": "data",
        },
        {
            "name": "L2 data beats L0 by 0.5 with min_improvement=0.5 — ADMITTED",
            "args": dict(naive=0.50, data=0.00),
            "cfg": LadderConfig(min_improvement=0.50),
            "api": "evaluate",
            "expect_admitted": True,
            "expect_level": "data",
        },
        {
            "name": "Coverage metric (higher is better) — hybrid wins",
            "args": dict(naive=0.70, mechanism=0.85, data=0.92, hybrid=0.97),
            "cfg": LadderConfig(metric="coverage", lower_is_better=False, min_improvement=0.01),
            "api": "evaluate",
            "expect_admitted": True,
            "expect_level": "hybrid",
        },
        {
            "name": "Only naive provided — trivially admitted",
            "args": dict(naive=0.30),
            "cfg": LadderConfig(),
            "api": "evaluate",
            "expect_admitted": True,
            "expect_level": "naive",
        },
    ]

    results = []
    for c in cases:
        if c["api"] == "admit_up_to":
            level_name, verdict = admit_up_to(**c["args"], cfg=c["cfg"])
        else:
            verdict = evaluate(**c["args"], cfg=c["cfg"])
            level_name = verdict.level
        ok = (verdict.admitted == c["expect_admitted"] and level_name == c["expect_level"])
        results.append({
            "name": c["name"],
            "api": c["api"],
            "level": level_name,
            "admitted": verdict.admitted,
            "reasons": verdict.reasons,
            "improvements": verdict.improvements,
            "ok": ok,
        })

    passed = sum(1 for r in results if r["ok"])
    return {"total": len(results), "passed": passed, "failed": len(results) - passed,
            "results": results}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--ci", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        r = _selftest()
        if args.json:
            print(json.dumps(r, indent=2, ensure_ascii=False))
        else:
            print("=" * 68)
            print("baseline ladder selftest")
            print("=" * 68)
            for res in r["results"]:
                mark = "✅" if res["ok"] else "❌"
                print(f"  {mark} {res['name']}")
                print(f"     level={res['level']}  admitted={res['admitted']}")
                if res["reasons"]:
                    for reason in res["reasons"]:
                        print(f"     - {reason}")
                if res["improvements"]:
                    imp_str = ", ".join(f"{k}: {v:+.4f}" for k, v in res["improvements"].items())
                    print(f"     improvements: {imp_str}")
            print(f"\n  {r['passed']}/{r['total']} passed")
        return 0 if r["failed"] == 0 else 1

    if args.ci:
        r = _selftest()
        print(f"baseline ladder selftest: {r['passed']}/{r['total']} passed")
        if r["failed"]:
            print(f"[FAIL] {r['failed']} case(s) failed", file=sys.stderr)
            return 1
        print("[CI PASS]")
        return 0

    print("Use --selftest or --ci")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
