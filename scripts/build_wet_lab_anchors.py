#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 oracle 反推隐含参数，与真实湿实验文献锚点对照，产出可静态托管的锚定报告。

## 为什么这一步是必要的

V&V 报告（reports/）回答「代理模型是否忠实复现 oracle」，判据是 R² 与区间覆盖率。
但 R²=1.0 只证明 **模型忠实于假想真实值**，不证明假想真实值符合真实生物学。
因此锚定是第二条独立证据链：

    模型 → oracle    （V&V 自洽性，报告库）
    oracle → 现实    （湿实验锚定，本模块）

## 关键设计：不抄场景元数据，而是从 oracle 拟合反推

实测发现场景库**自述互相矛盾**：
  - `microbio_monod`          声称 Ks=0.22 g/L
  - `micro_ecoli_glucose`     声称 Ks=0.004 g/L
  同一菌株、同一底物、引用同一本教材，差 55 倍。

所以本脚本对每个可锚定场景：
  1. 在场景自身 bounds 上密集采样**无噪** oracle（`_noise_free_f`）
  2. 用最小二乘拟合闭式模型，反推出隐含的 μmax / Ks / m_s
  3. 若拟合残差过大则标记 fit_failed，绝不硬报一个不可靠的「隐含值」
  4. 与 engine/wet_lab_anchors.py 里的文献**区间**（多来源）对照，分四档

输出：frontend_fixed/reports/wet_lab_anchors.json
用法：python scripts/build_wet_lab_anchors.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone

import numpy as np
from scipy.optimize import least_squares

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine import report_generator as RG          # noqa: E402
from engine import wet_lab_anchors as WLA          # noqa: E402

OUT = os.path.join(ROOT, "frontend_fixed", "reports", "wet_lab_anchors.json")

#: 拟合残差容忍（相对 oracle 值域）
FIT_TOL = 1e-3


# ---------------------------------------------------------------------------
# 采样：在 bounds 上按对数/线性混合密集取点
# ---------------------------------------------------------------------------
def sample_axis(lo: float, hi: float, n: int = 400) -> np.ndarray:
    """对数跨度大时用对数采样，否则线性。避免在平台区堆点、膝部缺点的病态。"""
    lo, hi = float(lo), float(hi)
    if hi <= 0 or lo <= 0 or hi / lo < 3:
        return np.linspace(lo, hi, n)
    return np.exp(np.linspace(np.log(lo), np.log(hi), n))


def _is_monotone_nonincreasing(v: np.ndarray, tol: float = 1e-9) -> bool:
    """判断曲线是否单调不增（允许极小平坦段）。用于产物抑制轴的语义自检。"""
    d = np.diff(np.asarray(v, float))
    return bool(np.all(d <= tol * (float(np.max(np.abs(v))) + 1e-15)))


# ---------------------------------------------------------------------------
# 闭式模型 + 拟合器
# ---------------------------------------------------------------------------
def _monod(S, mu_max, Ks):
    return mu_max * S / (Ks + S)


def _pirt(S, mu_max, Ks, m_s, Y_GS):
    """μ = max(μmax·S/(Ks+S) - m_s·Y_GS, 0) —— 见 engine/microbiology_full.pirt_maintenance"""
    return np.maximum(mu_max * S / (Ks + S) - m_s * Y_GS, 0.0)


def fit_monod(S, y, p0=(1.0, 0.01)) -> dict:
    """拟合 μ = μmax·S/(Ks+S)。返回 {mu_max, Ks, rel_resid, ok}"""
    def resid(p):
        return _monod(S, *p) - y

    try:
        r = least_squares(resid, p0, bounds=([1e-6, 1e-9], [10.0, 100.0]),
                          xtol=1e-12, ftol=1e-12, gtol=1e-12, max_nfev=400)
        rel = float(np.max(np.abs(r.fun)) / (float(np.max(y)) + 1e-12))
        return {"mu_max": float(r.x[0]), "Ks": float(r.x[1]),
                "rel_resid": rel, "ok": rel < FIT_TOL}
    except Exception as e:  # 优化失败也要诚实报
        return {"mu_max": None, "Ks": None, "rel_resid": float("nan"), "ok": False,
                "error": repr(e)[:120]}


def fit_pirt(S, y, Y_GS: float = 0.4) -> dict:
    """拟合 Pirt：μ = max(μmax·S/(Ks+S) - m_s·Y_GS, 0)。

    Y_GS（产率系数）在场景里没有独立文献锚点，固定取场景自身声明值 0.4。
    """
    def resid(p):
        return _pirt(S, p[0], p[1], p[2], Y_GS) - y

    try:
        r = least_squares(resid, [0.8, 0.01, 0.03],
                          bounds=([1e-4, 1e-9, 0.0], [10.0, 100.0, 5.0]),
                          xtol=1e-12, ftol=1e-12, gtol=1e-12, max_nfev=2000)
        rel = float(np.max(np.abs(r.fun)) / (float(np.max(y)) + 1e-12))
        return {"mu_max": float(r.x[0]), "Ks": float(r.x[1]), "m": float(r.x[2]),
                "rel_resid": rel, "ok": rel < FIT_TOL, "Y_GS_assumed": Y_GS}
    except Exception as e:
        return {"mu_max": None, "Ks": None, "m": None, "rel_resid": float("nan"),
                "ok": False, "Y_GS_assumed": Y_GS, "error": repr(e)[:120]}


# ---------------------------------------------------------------------------
# 每个场景的取数策略
# ---------------------------------------------------------------------------
FAMILY = {
    "microbio_monod": "monod",
    "micro_ecoli_glucose": "monod",
    "micro_mle_ecoli_monod": "monod",
    "micro_uq_ecoli_monod": "monod",
    "micro_sobol_ecoli": "monod",
    "micro_doe_ecoli_monod": "monod_2d_T",
    "micro_pirt_ecoli": "pirt",
    "micro_cerevisiae_ethanol": "monod_2d_P",
    "micro_crabtree_cerevisiae": "skip",
    "micro_thermal_death": "thermal",
}

#: 二维场景的固定坐标（取最有利/无抑制的切片，使拟合只反映底物效应）
T_FIX = 37.0
P_FIX = 0.0


def implied_params(key: str) -> dict:
    """反推场景 oracle 的隐含动力学参数。"""
    fam = FAMILY.get(key, "skip")
    if fam == "skip":
        return {"ok": False, "reason": "无可锚定的闭式模型"}

    scn = RG.get_scenario(key)
    dom = dict(scn)
    dom["f"] = RG._noise_free_f(dom)
    f = dom["f"]
    bounds = dom["bounds"]
    S = sample_axis(*bounds[0])

    if fam == "monod":
        y = np.array([f(np.array([s])) for s in S], float)
        return fit_monod(S, y)

    if fam == "monod_2d_T":
        # 固定温度在 T_opt，只拟合底物维度
        y = np.array([f(np.array([s, T_FIX])) for s in S], float)
        r = fit_monod(S, y)
        return {**r, "fixed": {"T": T_FIX}}

    if fam == "monod_2d_P":
        # (S, 产物抑制) 二维：固定产物浓度为 0，得到纯 Monod 切片。
        # 先做轴语义自检：产物抑制轴上 μ 必须对 x[1] 单调不增；
        # 若不单调（中段隆起），说明该轴实际不是抑制轴 → 拒绝拟合，报告语义错配。
        s_probe = float(np.sqrt(bounds[0][0] * bounds[0][1]))
        grid = np.linspace(*bounds[1], 61)
        curve = np.array([float(f(np.array([s_probe, float(v)]))) for v in grid])
        if not _is_monotone_nonincreasing(curve):
            peak_v = float(grid[int(np.argmax(curve))])
            return {"ok": False, "reason": (
                f"轴语义错配：第二轴标注为产物抑制（{bounds[1][0]}–{bounds[1][1]}），"
                f"但 oracle 在该轴上非单调、峰值出现在 x[1]={peak_v:g}"
                f"（μ={float(curve.max()):g}），两端 x[1]={bounds[1][0]:g}/{bounds[1][1]:g} "
                f"处 μ≈{curve[0]:g}/{curve[-1]:g}。"
                f"产物抑制轴的预期形态是 x[1]=0 最大、随抑制剂增多单调下降——"
                f"实际观测到的是钟形响应，与该轴被当作温度处理（钟形高斯 f_T）一致。"
                f"无法从此切片反推 μmax/Ks。")}
        y = np.array([f(np.array([s, P_FIX])) for s in S], float)
        r = fit_monod(S, y)
        return {**r, "fixed": {"product": P_FIX}}

    if fam == "pirt":
        y = np.array([f(np.array([s])) for s in S], float)
        r = fit_pirt(S, y)
        r["Y_GS_assumed"] = 0.4
        return r

    if fam == "thermal":
        # smooth_thermal_death(T, t=30) = 0.003·(T-60)·t
        t_ref = 30.0
        y60 = float(f(np.array([60.0])))
        y_hi = float(f(np.array([100.0])))
        return {"ok": True, "kill_at_60C": y60, "kill_at_100C": y_hi,
                "implied_z_degC": None, "t_reference_s": t_ref,
                "note": ("oracle 为温度线性函数（kill = 0.003·(T-60)·t），"
                         "60°C 处灭活量为 0 —— 真实 E. coli 在 60°C 有有限 D 值")}

    return {"ok": False, "reason": f"未知模型族 {fam}"}


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def build() -> dict:
    t0 = time.time()
    entries = []
    all_keys = set()
    for a in WLA.ANCHORS.values():
        all_keys.update(a.scenarios)

    for key in sorted(all_keys):
        imp = implied_params(key)
        params = {"mu_max": imp.get("mu_max"), "Ks": imp.get("Ks"),
                  "m": imp.get("m"), "m_s": imp.get("m"),
                  "kill_at_60C": imp.get("kill_at_60C"),
                  "kill_at_100C": imp.get("kill_at_100C"),
                  "implied_z_degC": imp.get("implied_z_degC")}
        sa = WLA.ScenarioAnchor(scenario_key=key, params=params,
                                checks=[])
        fit_ok = bool(imp.get("ok"))
        if not fit_ok and "reason" in imp:
            reason = imp["reason"]
            tag = ("SEMANTIC_MISMATCH" if "轴语义错配" in reason
                   else "NO_CLOSED_FORM" if "闭式模型" in reason
                   else "FIT_FAILED")
            sa.checks.append({
                "anchor_key": "diagnosis", "value": None, "unit": "",
                "lit_range": None, "grade": tag,
                "ratio": None, "why": reason})
            sa.grade = WLA.Grade.UNANCHORED
            sa.gate = ("BLOCK_AUTONOMOUS_ACTION" if tag == "SEMANTIC_MISMATCH"
                       else "NO_WET_LAB_ANCHOR")
            entries.append(sa)
            continue

        matched = False
        for name, a in WLA.ANCHORS.items():
            if key not in a.scenarios:
                continue
            matched = True
            # 热死灭走专门逻辑
            if name == "ecoli_thermal_D60":
                g, ratio, why = _grade_thermal(imp, a)
                val = imp.get("kill_at_60C")
            else:
                val = params.get(a.value_key)
                g, ratio, why = WLA.grade_against_anchor(val, a)
            sa.checks.append({
                "anchor_key": name, "value": val,
                "unit": a.unit, "lit_range": [a.lo, a.hi],
                "grade": g, "ratio": ratio, "why": why})

        if not matched:
            sa.checks.append({"anchor_key": "none", "value": None, "unit": "",
                              "lit_range": None, "grade": "NO_ANCHOR",
                              "ratio": None,
                              "why": "本场景无对应文献锚点（只有 V&V 自洽性证据）"})

        if imp.get("rel_resid") is not None:
            sa.params["fit_rel_resid"] = imp.get("rel_resid")
            sa.params["fit_ok"] = fit_ok
        sa.params.update({k: v for k, v in imp.items()
                          if k in ("fixed", "Y_GS_assumed", "t_reference_s", "note")})
        sa.grade = WLA.worst_grade([(c["grade"], c.get("ratio") or 1.0, c["why"])
                                    for c in sa.checks
                                    if c["grade"] in WLA.ANCHOR_GATE_MAP])
        sa.gate = WLA.ANCHOR_GATE_MAP[sa.grade]
        entries.append(sa)

    summary = WLA.summarize(entries)
    coverage = WLA.anchors_3d_coverage()
    summary["evidence_3d"] = coverage
    return {
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "purpose": (
            "Second, independent evidence chain. The V&V reports prove the surrogate "
            "model reproduces the ORACLE (R^2 + coverage). This file asks the other "
            "question: does the oracle's assumed ground truth agree with real wet-lab "
            "measurements? R^2 = 1.0 on a 40x-off parameter is still only 'perfectly "
            "faithful to the wrong physics'. "
            "Oracle parameters are NOT copied from scenario metadata — they are "
            "reverse-engineered by least-squares fitting of the noise-free oracle, "
            "because the scenario metadata is internally inconsistent "
            "(microbio_monod claims Ks=0.22 g/L while micro_ecoli_glucose claims "
            "0.004 g/L for the same strain/substrate, 55x apart)."),
        "anchors": WLA.anchors_index(),
        "summary": summary,
        "scenarios": [e.to_json() for e in entries],
        "note": (
            "UNANCHORED is not a failure — most of the 62 scenarios are dimensional / "
            "mathematical test functions with no physical counterpart, and only the "
            "kinetics-anchored ones can be checked this way. What this file does "
            "NOT do: edit scenario parameters to match literature. Contradictions "
            "are reported, not papered over."),
        "elapsed_s": round(time.time() - t0, 2),
    }


def _grade_thermal(imp: dict, a: WLA.Anchor):
    """热死灭场景：真实 D60 = 25–132 s，即 60°C 有有限灭活速率。"""
    kill = imp.get("kill_at_60C")
    if kill is None:
        return WLA.Grade.UNANCHORED, float("nan"), "未能读取 60°C 灭活量"
    if kill <= 0:
        return (WLA.Grade.CONTRADICTED, float("inf"),
                f"oracle 在 60°C 的灭活量为 {kill:g}（≈0），而实测 E. coli "
                f"D60 = {a.lo:g}–{a.hi:g} s，即 60°C 必然有有限灭活速率；"
                f"且该 oracle 为温度线性函数，隐含 z 值不存在（实测 z = 2.8–6.74°C）")
    return WLA.grade_against_anchor(kill, a)


def main() -> int:
    doc = build()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)

    print(f"== 湿实验锚定（oracle 参数由拟合反推，非抄元数据）==  {doc['elapsed_s']}s")
    print(f"  锚点数 {len(doc['anchors'])} / 受检场景 {doc['summary']['total']}")
    print(f"  {doc['summary']['grade_counts']}")
    if doc["summary"]["blocked"]:
        print(f"  CONTRADICTED: {doc['summary']['blocked']}")
    e3 = doc["summary"]["evidence_3d"]
    bc = e3["by_completeness"]
    print(f"  三维覆盖: 3D={bc[3]} 2D={bc[2]} 1D={bc[1]} 0D={bc[0]} / "
          f"{e3['total_anchors']}")
    if e3["decision_relevant_not_fully_anchored"]:
        print(f"  ⚠ decision_relevant 未全三维: "
              f"{e3['decision_relevant_not_fully_anchored']}")
    print()
    w = max(len(e["scenario_key"]) for e in doc["scenarios"])
    print(f"  {'scenario':<{w}}  {'grade':<14}  params(fitted)")
    for e in doc["scenarios"]:
        p = e["implied_params"]
        parts = []
        for k in ("mu_max", "Ks", "m", "kill_at_60C"):
            if p.get(k) is not None:
                parts.append(f"{k}={p[k]:g}")
        fit = ""
        if p.get("fit_ok") is False:
            fit = " [FIT_FAIL]"
        print(f"  {e['scenario_key']:<{w}}  {e['grade']:<14}  {' '.join(parts)}{fit}")
        for c in e["checks"]:
            if (c["grade"] in (WLA.Grade.CONTRADICTED, WLA.Grade.NEAR)
                    or c["grade"] in ("FIT_FAILED", "NO_ANCHOR", "NO_CLOSED_FORM",
                                      "SEMANTIC_MISMATCH")):
                print(f"      └─ [{c['grade']}] {c['anchor_key']}: {c['why']}")
    print(f"\n  → {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
