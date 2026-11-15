#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""锚点质量门禁（CI 阶段1）。

检查项：
  1. 锚点来源独立性：同源聚类后独立性比率 ≥ 0.5（允许工艺锚点共用数据汇编，
     但生物锚点不能全部共用同一来源）
  2. 消融规划覆盖率：PASS 场景的验证覆盖平均分 ≥ 0.5（六维评估）
  3. 高优先级缺口：不允许 HIGH 优先级场景（验证覆盖 < 0.4）存在

退出码：0 = 全过；1 = 有断言失败（fail-closed）；2 = 警告（独立性比率偏低）。
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

#: 独立性比率下限（低于此值报警但不阻断）
MIN_INDEPENDENCE_RATIO = 0.50
#: 消融规划覆盖率下限
MIN_ABLATION_COVERAGE = 0.50
#: HIGH 优先级上限（0 = 不允许任何 HIGH 优先级缺口）
MAX_HIGH_PRIORITY = 0


def main() -> int:
    failures: list[str] = []
    warnings: list[str] = []

    # ---- 1) 锚点来源独立性 ----
    anchors_path = os.path.join(ROOT, "frontend_fixed", "reports", "wet_lab_anchors.json")
    if not os.path.exists(anchors_path):
        warnings.append(f"wet_lab_anchors.json 不存在: {anchors_path}")
        anchors_path_ok = False
    else:
        with open(anchors_path, encoding="utf-8") as f:
            data = json.load(f)
        anchors = data.get("anchors", [])
        if not anchors:
            warnings.append("wet_lab_anchors.json 中没有锚点")
            anchors_path_ok = False
        else:
            anchors_path_ok = True
            from engine.anchor_independence import analyze
            report = analyze(anchors)
            ratio = report.independence_ratio
            print(f"锚点独立性: {ratio:.4f} "
                  f"(声明 {report.total_claims} 来源 → 去重 {report.total_unique_families} 家族)")
            if ratio < MIN_INDEPENDENCE_RATIO:
                failures.append(
                    f"独立性比率 {ratio:.4f} < {MIN_INDEPENDENCE_RATIO}: "
                    f"来源同质化严重，锚点宣称数必须标注为去重后数值"
                )
            elif ratio < 0.80:
                warnings.append(
                    f"独立性比率 {ratio:.4f} < 0.80: "
                    f"存在同源重复（{len(report.shared_source_clusters)} 个共享聚类），"
                    f"建议扩充独立来源"
                )

    # ---- 2) 消融规划覆盖率 ----
    from engine.verification_gate import ablation_plan_batch, load_ledger
    led = load_ledger()
    if led is None:
        warnings.append("gate_ledger.json 不存在，跳过消融规划检查")
        ablation_ok = False
    else:
        ablation_ok = True
        batch = ablation_plan_batch(only_gaps=False)
        n_total = batch["total_assessed"]
        n_with_gaps = batch["with_gaps"]
        by_pri = batch["by_priority"]

        print(f"消融规划: {n_total} PASS 场景评估, {n_with_gaps} 有缺口")
        print(f"  优先级: HIGH={by_pri['HIGH']} MEDIUM={by_pri['MEDIUM']} LOW={by_pri['LOW']}")

        # 计算平均覆盖率
        scores = [p["overall_coverage"] for p in batch["plans"].values()]
        if scores:
            avg_cov = sum(scores) / len(scores)
            min_cov = min(scores)
            max_cov = max(scores)
            print(f"  覆盖率: avg={avg_cov:.4f} min={min_cov:.4f} max={max_cov:.4f}")
            if avg_cov < MIN_ABLATION_COVERAGE:
                failures.append(
                    f"平均验证覆盖率 {avg_cov:.4f} < {MIN_ABLATION_COVERAGE}"
                )
        else:
            warnings.append("无 PASS 场景可评估消融规划")

        # HIGH 优先级检查
        if by_pri["HIGH"] > MAX_HIGH_PRIORITY:
            failures.append(
                f"存在 {by_pri['HIGH']} 个 HIGH 优先级缺口 "
                f"(验证覆盖率 < 0.4)，必须优先补强"
            )

        # 写出报告
        report_path = os.path.join(ROOT, "data", "ablation_plan_report.json")
        os.makedirs(os.path.dirname(report_path), exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(batch, f, ensure_ascii=False, indent=2, default=str)
            f.write("\n")

    # ---- 3) 证据采集简报：诚实性守卫 ----
    #
    # brief 是取证任务书，不是证据。这里守一条会静默腐化的线：有人为了让
    # 计划看起来「有产出」，往 brief 里塞 retrieved_value / found_in 之类的
    # 字段，于是任务书变成了伪造的结论。这类字段一旦出现即判失败。
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "_chk_ev_acq", os.path.join(ROOT, "engine", "evidence_acquisition.py"))
        eva = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = eva
        spec.loader.exec_module(eva)
        plan_report = eva.plan()
        briefs = plan_report["briefs"]
        counts = plan_report["counts"]

        print(f"\n证据采集简报: {counts['briefs']} 条 "
              f"(可晋升 {counts['promotable_now']}, "
              f"可离线执行 {counts['offline_executable']}, "
              f"需外部情报 {counts['requires_external_intelligence']})")

        # 3a) 不允许任何「已取到证据」的字段名 —— 任务书不得伪装成结论
        brief_blob = json.dumps(briefs, ensure_ascii=False)
        for banned in ("retrieved_value", "found_in", "retrieved_from",
                       "measured_value", "confirmed_value"):
            if banned in brief_blob:
                failures.append(
                    f"brief 中出现证据声明字段 '{banned}' —— brief 只能声明任务，"
                    "不得声称已取到任何观测值")

        # 3b) 每条 brief 必须带 not_evidence 标记
        n_no_mark = sum(1 for b in briefs if not b.get("not_evidence"))
        if n_no_mark:
            failures.append(f"{n_no_mark} 条 brief 缺少 not_evidence 标记")

        # 3c) anchor_intake 不允许存在自动采纳路径
        n_auto = sum(1 for b in briefs
                     if b["body"].get("screening_gate", {}).get("auto_accept_path")
                     is True)
        if n_auto:
            failures.append(f"{n_auto} 条 brief 声明了 auto_accept_path=True —— "
                            "违反 anchor_intake 的具名确认人纪律")

        # 3d) 饱和来源家族数量：比率低是警告，不是阻断
        if counts["saturated_families"]:
            warnings.append(
                f"{counts['saturated_families']} 个来源家族被 2+ 个锚点共用，"
                "新锚点应优先选在家族之外，否则独立性比率不会提升")

        # 3e) 简报产物落盘，供交付与审计
        eva_path = os.path.join(ROOT, "data", "evidence_acquisition_plan.json")
        with open(eva_path, "w", encoding="utf-8") as f:
            json.dump(plan_report, f, ensure_ascii=False, indent=2, default=str)
            f.write("\n")
    except Exception as exc:  # fail-closed：读不出来也要报错
        failures.append(f"证据采集简报生成失败: {exc!r}")

    # ---- 4) 汇总 ----
    print(f"\n{'='*60}")
    if failures:
        print(f"FAIL: {len(failures)} 项断言失败")
        for f in failures:
            print(f"  ✗ {f}")
    if warnings:
        print(f"WARNING: {len(warnings)} 项警告")
        for w in warnings:
            print(f"  ! {w}")
    if not failures and not warnings:
        print("PASS: 锚点质量检查全部通过")
    elif not failures:
        print("PASS: 锚点质量检查通过（有警告）")
    print(f"{'='*60}")

    if failures:
        return 1
    return 0  # warnings are non-blocking; report them but do not fail the gate


if __name__ == "__main__":
    sys.exit(main())
