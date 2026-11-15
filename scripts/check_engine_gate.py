#!/usr/bin/env python
"""Engine 门禁自检（CI 阶段0 的唯一入口）。

为什么要单独成文件，而不是写在 ci-cd.yml 的内联 `python -c` 里：
  1. 内联脚本无法在本地原样复跑，导致「CI 红而本地绿」长期查不出（本项目 2026-09-18
     之前的实况）；提成文件后本地 `python scripts/check_engine_gate.py` 就是 CI 的那一条。
  2. 门禁逻辑集中一处，便于审计；断言项与阈值都来自引擎导出的唯一真源，不另抄字面量。

判据（与对外报告 gate_ledger 同一条规则，阈值来自 engine/report_generator）：
  · R² ≥ PASS_R2(0.90)        拟合质量
  · coverage ≥ PASS_COV(0.90) 校准诚实性（覆盖率取**单侧下界**：高于名义值属保守，不算失败）
  · 二者同时满足才算一个场景 PASS；`converged` 即该 PASS 计数
  · 另要求：无缺失能力、无未登记场景

退出码：0 = 全过；1 = 有断言失败（fail-closed）。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# CI 场景数下限。2026-09-18 起 `converged` 的语义是「按对外同一门禁判 PASS」，
# 因此这条断言同时管拟合质量与校准诚实性（此前只管 coverage>0.55，与质量无关）。
MIN_CONVERGED = 50


def _check_uq_methods():
    """Path A 门禁：4 个新 conformal 模块必须可用 + selftest 通过。

    这是 A15 外推守卫的方法学扩展：光有 split/adaptive 不足以支撑
    分布漂移、多保真、风险加权、在线流式 4 种场景。任何一个模块
    import 失败或 selftest 未通过都 fail-closed。
    """
    from importlib import import_module
    methods = {
        "online_conformal": "CoFact ICLR 2026 — online density-ratio",
        "kmm_conformal": "KMM-CP UAI 2026 — kernel mean matching",
        "risk_conformal": "Risk-weighted + LTT (Angelopoulos & Bates 2021)",
        "multifidelity_conformal": "Kennedy-O'Hagan + conformal",
    }
    failures = []
    for mod_name, desc in methods.items():
        try:
            mod = import_module(f"engine.{mod_name}")
            if not hasattr(mod, "_selftest"):
                failures.append(f"engine.{mod_name}: 缺少 _selftest()")
                continue
            ret = mod._selftest()
            if ret != 0:
                failures.append(f"engine.{mod_name}: selftest 返回 {ret} ({desc})")
        except Exception as e:
            failures.append(f"engine.{mod_name}: {type(e).__name__}: {e}")
    return failures


def main() -> int:
    from engine import coverage_report

    rpt = coverage_report()
    g = rpt["gate"]

    print(f"场景: {rpt['converged']}/{rpt['total_scenarios']} PASS")
    print(f"能力: {len(rpt['covered_capabilities'])} covered, "
          f"{len(rpt['still_missing'])} missing")
    print(f"门禁: R²>={g['r2_min_required']} 且 coverage>={g['coverage_min_required']}"
          f"（覆盖率单侧下界）")
    print(f"  实际最差: R²={rpt['r2_min']}  coverage={rpt['coverage_min']}")
    print(f"  实际均值: R²={rpt['r2_mean']}  coverage={rpt['coverage_mean']}")
    if rpt.get("unregistered"):
        print(f"  未登记进对外注册表的场景: {rpt['unregistered']} 个")

    failures = []

    # 1) 通过计数（现在的 PASS = R² 与 coverage 双达标，见 coverage_report 文档）
    if rpt["converged"] < MIN_CONVERGED:
        failures.append(f"场景 PASS 数不足: {rpt['converged']} < {MIN_CONVERGED}")

    # 2) 能力覆盖不得回退
    if len(rpt["still_missing"]) != 0:
        failures.append(f"仍有缺失能力 {len(rpt['still_missing'])} 项")

    # 3) 拟合质量：不得存在 R² 低于门禁的场景（coverage 达标不能掩盖拟合差）
    if rpt["r2_min"] is None or rpt["r2_min"] < g["r2_min_required"]:
        failures.append(f"存在拟合不足场景: r2_min={rpt['r2_min']} < {g['r2_min_required']}")

    # 4) 校准诚实性：不得存在过度自信（区间过窄）的场景
    if rpt["coverage_min"] is None or rpt["coverage_min"] < g["coverage_min_required"]:
        failures.append(
            f"存在校准不诚实场景: coverage_min={rpt['coverage_min']} < {g['coverage_min_required']}")

    # 5) CI 场景必须都能在对外注册表里取到同口径证据
    if rpt.get("unregistered"):
        failures.append(f"有 {rpt['unregistered']} 个场景未登记进对外注册表")

    # 6) 数据异常
    if rpt["nan_data"] != 0:
        failures.append(f"有 {rpt['nan_data']} 个场景数据为 NaN")

    # 7) Path A: 4 个新 conformal 方法门禁（UQ 方法深度）
    uq_failures = _check_uq_methods()
    failures.extend(uq_failures)
    if not uq_failures:
        print("[OK] 4 个 UQ 方法门禁通过 (online/kmm/risk/multifidelity)")
    else:
        print(f"[FAIL] UQ 方法门禁失败 {len(uq_failures)} 项")

    if failures:
        print("\n[FAIL] Engine 门禁未通过：")
        for f in failures:
            print(f"  · {f}")
        return 1

    print("\n[OK] Engine 门禁通过（阈值一字未改，口径与对外报告一致）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
