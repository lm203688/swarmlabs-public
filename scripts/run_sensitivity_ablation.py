#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_sensitivity_ablation.py — A-4：参数扰动稳健性审计（2026-10-18）。

背景（第九轮科学性评估报告，A-4）：
    ablation_plan 的 sensitivity 维度均分 0.044、95.6% 零分——「结论对参数
    扰动是否稳健」从未被测量过。本脚本对每个场景的决策相关参数施加
    ±10% / ±20% 扰动，重跑 V&V 的**生产判定路径**（report_generator.
    collect_evidence → _verdict），记录 verdict 翻转率。

    "哪些场景的结论一碰就翻"本身就是负面结果：它标出结论的可靠性边界，
    延续 paper-2 的叙事（外推失效是空间维度的边界，参数敏感是假设维度
    的边界）。

扰动集（每个配置 = 一个旋钮的 ±10% / ±20%）：
    ls       GP 长度尺度先验 × {0.9, 1.1, 0.8, 1.2}
    n_train  训练集规模     × {0.9, 1.1, 0.8, 1.2}（取整，下限 6）

刻意**不**扰动的量（纪律，不是遗漏）：
    - 噪声地板（3% relative）：项目硬约束「噪声地板 3% 不降」——扰动它等于
      放松诚实 UQ 的前提，测出来的"稳健"是自欺。
    - conformal 校准种子：换校准集属于独立复算（B-1）范畴，不与参数扰动混谈。

判定路径与账本一致：collect_evidence 的 r2/coverage → _verdict() 同一函数、
同一阈值（PASS_R2=0.90 / PASS_COV=0.90 / MARGINAL_R2=0.70 / MARGINAL_COV=0.80）。
基线 verdict 与 gate_ledger 逐场景核对，不一致即显式记录（fail-loud）。

输出：data/sensitivity_ablation.json
    per-scenario: baseline_verdict / 每个扰动配置的 verdict+r2+cov+flipped
                  / flip_rate / sensitivity_score
    aggregate:    翻转率分布、稳健场景清单、易翻场景清单、口径元数据

退出码：0 = 成功；1 = 内部错误（个别场景失败不中断，计入 failed 列表）。
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

#: 扰动因子：±10% / ±20%（<1 = 收紧，>1 = 放松）
FACTORS: tuple[tuple[str, float], ...] = (
    ("ls_x0.8", 0.8),
    ("ls_x0.9", 0.9),
    ("ls_x1.1", 1.1),
    ("ls_x1.2", 1.2),
    ("ntrain_x0.8", 0.8),
    ("ntrain_x0.9", 0.9),
    ("ntrain_x1.1", 1.1),
    ("ntrain_x1.2", 1.2),
)

#: n_train 下限（_select_config 在 n<6 时退回 ARD，仍可跑；低于 6 无意义）
MIN_N_TRAIN = 6


def _run_config(scenario: dict, name: str, factor: float,
                n_train: int) -> dict:
    """跑一个扰动配置，返回 {name, factor, verdict, r2, coverage, flipped, note}。"""
    domain = dict(scenario)
    note = ""
    if name.startswith("ls_"):
        base_ls = domain.get("ls")
        if base_ls is None:
            return {"name": name, "factor": factor, "verdict": None,
                    "r2": None, "coverage": None, "flipped": None,
                    "note": "场景无 ls 先验（纯 ARD），扰动不适用"}
        domain["ls"] = float(base_ls) * factor
    else:  # ntrain_
        domain["n_train"] = max(MIN_N_TRAIN, int(round(n_train * factor)))

    try:
        ev = collect_evidence(domain, with_loo=False,
                              with_replication=False, with_baseline=False)
    except Exception as e:                       # noqa: BLE001
        return {"name": name, "factor": factor, "verdict": None,
                "r2": None, "coverage": None, "flipped": None,
                "note": f"运行失败：{type(e).__name__}: {e}"}

    return {
        "name": name,
        "factor": factor,
        "verdict": ev["verdict"]["grade"],
        "r2": ev["metrics"]["r2"],
        "coverage": ev["metrics"]["coverage"],
        "flipped": None,                          # 由调用方填（需知基线）
        "note": note,
    }


def audit_scenario(key: str, scenario: dict, ledger: dict) -> dict:
    """对单场景跑基线 + 全部扰动配置。"""
    # ── 基线（与账本同口径核对）────────────────────────────────
    try:
        base = collect_evidence(scenario, with_loo=False,
                                with_replication=False, with_baseline=False)
    except Exception as e:                       # noqa: BLE001
        return {"key": key, "error": f"基线失败：{type(e).__name__}: {e}"}

    base_verdict = base["verdict"]["grade"]
    base_r2 = base["metrics"]["r2"]
    base_cov = base["metrics"]["coverage"]
    n_train = int(base["design"]["n_train"])
    ledger_verdict = (ledger.get(key) or {}).get("verdict")

    configs = []
    for name, factor in FACTORS:
        c = _run_config(scenario, name, factor, n_train)
        c["flipped"] = (c["verdict"] is not None and c["verdict"] != base_verdict)
        configs.append(c)

    applicable = [c for c in configs if c["verdict"] is not None]
    n_flipped = sum(1 for c in applicable if c["flipped"])
    flip_rate = round(n_flipped / len(applicable), 4) if applicable else None

    return {
        "key": key,
        "domain": scenario.get("domain", ""),
        "baseline_verdict": base_verdict,
        "baseline_r2": base_r2,
        "baseline_coverage": base_cov,
        "ledger_verdict": ledger_verdict,
        "ledger_match": (ledger_verdict == base_verdict),
        "n_train": n_train,
        "configs": configs,
        "n_applicable": len(applicable),
        "n_flipped": n_flipped,
        "flip_rate": flip_rate,
        # sensitivity_score = 1 - flip_rate：扰动下 verdict 存活的比例。
        # 写回 ablation_plan_report.json 的 sensitivity 维度（A-4）。
        "sensitivity_score": (round(1.0 - flip_rate, 3)
                              if flip_rate is not None else None),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scenarios", default="",
                    help="逗号分隔的场景 key 子集（默认全部）")
    ap.add_argument("--out", default=str(ROOT / "data" / "sensitivity_ablation.json"))
    args = ap.parse_args(argv)

    scenarios = all_scenarios()
    keys = ([k.strip() for k in args.scenarios.split(",") if k.strip()]
            if args.scenarios else sorted(scenarios))

    ledger_path = ROOT / "data" / "gate_ledger.json"
    ledger: dict = {}
    if ledger_path.exists():
        led = json.loads(ledger_path.read_text(encoding="utf-8"))
        ledger = led.get("scenarios", led)

    per_scenario: dict[str, dict] = {}
    failed: list[str] = []
    for i, k in enumerate(keys, 1):
        if k not in scenarios:
            failed.append(k)
            print(f"[{i}/{len(keys)}] {k}: 不在场景库，跳过", flush=True)
            continue
        rec = audit_scenario(k, scenarios[k], ledger)
        if "error" in rec:
            failed.append(k)
            print(f"[{i}/{len(keys)}] {k}: {rec['error']}", flush=True)
            continue
        per_scenario[k] = rec
        fr = rec["flip_rate"]
        flag = "" if rec["ledger_match"] else "  ⚠ 基线≠账本"
        print(f"[{i}/{len(keys)}] {k:38s} base={rec['baseline_verdict']:8s} "
              f"flip={rec['n_flipped']}/{rec['n_applicable']}"
              f"{'' if fr is None else f' ({fr:.0%})'}{flag}", flush=True)

    # ── 聚合 ──────────────────────────────────────────────────
    ok = [r for r in per_scenario.values() if r["flip_rate"] is not None]
    flips = sorted(r["flip_rate"] for r in ok)
    n = len(ok)
    dist = {
        "robust_0": sum(1 for f in flips if f == 0.0),
        "low_le_0.25": sum(1 for f in flips if 0.0 < f <= 0.25),
        "mid_le_0.5": sum(1 for f in flips if 0.25 < f <= 0.5),
        "high_gt_0.5": sum(1 for f in flips if f > 0.5),
    }
    # 基线 PASS 的场景子集（论文报告口径：结论被信任的那批）
    pass_set = [r for r in ok if r["baseline_verdict"] == "PASS"]
    pass_dist = {
        "robust_0": sum(1 for r in pass_set if r["flip_rate"] == 0.0),
        "low_le_0.25": sum(1 for r in pass_set if 0.0 < r["flip_rate"] <= 0.25),
        "mid_le_0.5": sum(1 for r in pass_set if 0.25 < r["flip_rate"] <= 0.5),
        "high_gt_0.5": sum(1 for r in pass_set if r["flip_rate"] > 0.5),
    }
    median_flip = flips[n // 2] if n else None

    result = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "method": (
            "参数扰动稳健性审计（A-4）：对每个场景的 ls 先验与 n_train 施加 "
            "±10%/±20% 扰动，重跑 collect_evidence 生产判定路径，记录 verdict "
            "翻转率。噪声地板与 conformal 校准种子刻意不扰动（见脚本 docstring）。"
        ),
        "verdict_thresholds": {
            "PASS_R2": 0.90, "PASS_COV": 0.90,
            "MARGINAL_R2": 0.70, "MARGINAL_COV": 0.80,
        },
        "factors": [{"name": nm, "factor": f} for nm, f in FACTORS],
        "n_scenarios": len(per_scenario),
        "n_failed": len(failed),
        "failed": failed,
        "aggregate": {
            "all": {"n": n, "flip_distribution": dist,
                    "median_flip_rate": median_flip,
                    "mean_flip_rate": round(sum(flips) / n, 4) if n else None},
            "baseline_pass_subset": {
                "n": len(pass_set), "flip_distribution": pass_dist,
                "note": "论文报告口径：基线 verdict=PASS 的场景（结论被信任的那批）",
            },
            "ledger_mismatch": [r["key"] for r in ok if not r["ledger_match"]],
            "robust_scenarios": sorted(r["key"] for r in ok if r["flip_rate"] == 0.0),
            "fragile_scenarios": sorted(r["key"] for r in ok if r["flip_rate"] > 0.5),
        },
        "scenarios": per_scenario,
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")

    print()
    print(f"审计完成：{n} 场景（失败 {len(failed)}）")
    print(f"  翻转分布（全部）: {dist}")
    print(f"  翻转分布（PASS 子集, n={len(pass_set)}）: {pass_dist}")
    print(f"  中位翻转率: {median_flip}")
    print(f"  稳健场景（0 翻转）: {len(result['aggregate']['robust_scenarios'])}")
    print(f"  易翻场景（>50% 翻转）: {len(result['aggregate']['fragile_scenarios'])}")
    if result["aggregate"]["ledger_mismatch"]:
        print(f"  ⚠ 基线≠账本: {result['aggregate']['ledger_mismatch']}")
    print(f"输出: {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
