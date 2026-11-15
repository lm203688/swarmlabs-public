#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_second_backend_replication.py — B-1：第二 surrogate 后端独立复算。

背景（第九轮科学性评估报告，B-1）：
    replication 维度 60.4% 零分——「独立复算」此前只有同后端异种子一种形式
    （report_generator._replication_check，seed_a=0 vs seed_b=101）。审稿人
    会问：换一个 surrogate 家族，结论还成立吗？

本脚本用**第二个 surrogate 后端**（Random Forest + Conformal，
engine/random_forest_baseline.py，与 GP 完全不同的模型家族、不同归纳偏置）
在**同一批训练/测试数据**上重跑，与 GP 的 verdict 比对。

判据（分档，不搞布尔）——「方向不矛盾」是复算的最低要求：
    agree_exact     两后端同分（PASS↔PASS 等）                 score 1.0
    agree_direction 第二端点数严一档（PASS→MARGINAL 等）        score 0.7
                    方向一致（都认为有信号/都判负），强度不同
    contradict      第二端严两档（PASS→REFUTED）               score 0.0
                    = GP 的 PASS 换家族就塌，复算失败
    contradict      第二端把 GP 的负判升级为 PASS               score 0.0
                    = 负判是模型家族特有的，同样复算失败

数据来源：collect_evidence(with_baseline=True) 已在同一 X/y/Xt/y_true 上
跑 RF 基线（baseline_comparison.rf），本脚本只消费该输出，不重复实现。

输出：data/second_backend_replication.json
    per-scenario: gp/rf 的 grade、r2、coverage + decision + score
    aggregate:    分档分布、一致率、矛盾案例清单

用法：
    python scripts/build_second_backend_replication.py
    python scripts/build_second_backend_replication.py --scenarios key1,key2
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.report_generator import all_scenarios, collect_evidence  # noqa: E402

#: verdict 严刻度：越大越严（REFUTED 最严）
_STRICTNESS = {"REFUTED": 0, "MARGINAL": 1, "PASS": 2}

#: 分档 → 分数
_DECISION_SCORE = {
    "agree_exact": 1.0,
    "agree_direction": 0.7,
    "contradict": 0.0,
    "unavailable": None,
}


def _decide(gp_grade: str, rf_grade: str | None) -> tuple[str, float | None]:
    if rf_grade is None or rf_grade not in _STRICTNESS:
        return "unavailable", None
    if gp_grade not in _STRICTNESS:
        return "unavailable", None
    gap = _STRICTNESS[gp_grade] - _STRICTNESS[rf_grade]   # >0: RF 更严
    if gap == 0:
        return "agree_exact", _DECISION_SCORE["agree_exact"]
    if gap == 1:
        return "agree_direction", _DECISION_SCORE["agree_direction"]
    if gap == 2:
        return "contradict", _DECISION_SCORE["contradict"]
    # gap < 0：RF 把 GP 的负判升级为 PASS——同样是复算失败
    return "contradict", _DECISION_SCORE["contradict"]


def audit_scenario(key: str, scenario: dict) -> dict:
    try:
        ev = collect_evidence(scenario, with_loo=False,
                              with_replication=False, with_baseline=True)
    except Exception as e:                       # noqa: BLE001
        return {"key": key, "decision": "unavailable",
                "error": f"{type(e).__name__}: {e}"}

    gp_grade = ev["verdict"]["grade"]
    bc = ev.get("baseline_comparison") or {}
    rf = bc.get("rf") or {}
    rf_grade = rf.get("grade")
    decision, score = _decide(gp_grade, rf_grade)

    return {
        "key": key,
        "domain": scenario.get("domain", ""),
        "gp": {
            "grade": gp_grade,
            "r2": ev["metrics"]["r2"],
            "coverage": ev["metrics"]["coverage"],
        },
        "rf": {
            "grade": rf_grade,
            "r2": rf.get("r2"),
            "coverage": rf.get("coverage"),
        },
        "decision": decision,
        "score": score,
        "second_backend": "RandomForest + conformal calibration (engine/random_forest_baseline.py)",
        "same_data": True,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scenarios", default="",
                    help="逗号分隔的场景 key 子集（默认全部）")
    ap.add_argument("--out", default=str(ROOT / "data" / "second_backend_replication.json"))
    args = ap.parse_args(argv)

    scenarios = all_scenarios()
    keys = ([k.strip() for k in args.scenarios.split(",") if k.strip()]
            if args.scenarios else sorted(scenarios))

    per: dict[str, dict] = {}
    for i, k in enumerate(keys, 1):
        if k not in scenarios:
            print(f"[{i}/{len(keys)}] {k}: 不在场景库，跳过", flush=True)
            continue
        rec = audit_scenario(k, scenarios[k])
        per[k] = rec
        if rec["decision"] == "unavailable":
            print(f"[{i}/{len(keys)}] {k:38s} unavailable "
                  f"{rec.get('error', '')[:50]}", flush=True)
        else:
            print(f"[{i}/{len(keys)}] {k:38s} GP={rec['gp']['grade']:8s} "
                  f"RF={str(rec['rf']['grade']):8s} → {rec['decision']}", flush=True)

    ok = [r for r in per.values() if r["decision"] != "unavailable"]
    dist: dict[str, int] = {}
    for r in ok:
        dist[r["decision"]] = dist.get(r["decision"], 0) + 1
    n = len(ok)
    mean_score = (sum(r["score"] for r in ok if r["score"] is not None) / n) if n else None
    contradicts = sorted(r["key"] for r in ok if r["decision"] == "contradict")

    result = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "method": (
            "B-1 第二 surrogate 后端独立复算：RandomForest+Conformal 与 GP 在"
            "同一批 LHS 训练/测试数据上重跑，按 verdict 分档比对。"
            "判据：agree_exact=1.0 / agree_direction=0.7（RF 严一档）/ "
            "contradict=0.0（RF 严两档，或把负判升级为 PASS）。"
        ),
        "second_backend": "RandomForest + conformal calibration",
        "n_scenarios": len(per),
        "n_unavailable": len(per) - n,
        "aggregate": {
            "decision_distribution": dist,
            "mean_score": round(mean_score, 4) if mean_score is not None else None,
            "contradict_scenarios": contradicts,
            "note": ("contradict = GP 的 PASS 换 surrogate 家族即塌（或负判被"
                     "第二端升级）——这些场景的结论对模型家族敏感，是负面结果，"
                     "不是 bug。"),
        },
        "scenarios": per,
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")

    print()
    print(f"复算完成：{n} 场景（unavailable {len(per) - n}）")
    print(f"  分档分布: {dist}")
    print(f"  均分: {mean_score:.4f}" if mean_score is not None else "  均分: n/a")
    print(f"  矛盾案例（{len(contradicts)}）: {contradicts}")
    print(f"输出: {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
