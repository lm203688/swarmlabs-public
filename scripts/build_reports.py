#!/usr/bin/env python
"""批量生成全部场景的 V&V 报告（HTML + JSON）并产出 index.json 清单。

设计要点（与项目红线一致）：
- 虚拟实验 = 仿真：训练/验证对照无噪真值（见 engine/report_generator._noise_free_f）。
- 报告由确定性计算生成（seed 固定），无 LLM 参与，可逐行复现。
- 输出落到 frontend_fixed/reports/，作为静态资产由 CF Pages 直接托管，
  因此线上 V&V 报告是「真实计算产物」而非占位说明——CF Pages 无法跑 Python，
  故采用离线生成 + 静态托管的诚实架构。

用法：
  python scripts/build_reports.py            # 全量生成
  python scripts/build_reports.py --only micro_ecoli_glucose,surrogate_branin
  python scripts/build_reports.py --limit 3  # 仅前 N 个（调试）
"""
import os
import sys
import json
import time
import argparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine import report_generator as RG  # noqa: E402
from engine import molecular_scenarios as _ms  # noqa: E402
from engine.scenario_trust_labels import (  # noqa: E402
    labels_for,
    wet_lab_grade_for_key,
)

OUT_ROOT = os.path.join(ROOT, "frontend_fixed", "reports")
HTML_DIR = os.path.join(OUT_ROOT, "html")
JSON_DIR = os.path.join(OUT_ROOT, "json")
DOCX_DIR = os.path.join(OUT_ROOT, "docx")
WET_LAB_PATH = os.path.join(OUT_ROOT, "wet_lab_anchors.json")

_wet_lab_cache = None


def _wet_lab_doc():
    """惰性加载湿实验锚点。首次运行 reports 循环时它可能还不存在，
    此时信任标签退回保守分支（synthetic/published）。下一次运行会读到。"""
    global _wet_lab_cache
    if _wet_lab_cache is None:
        try:
            with open(WET_LAB_PATH, encoding="utf-8") as f:
                _wet_lab_cache = json.load(f)
        except Exception:
            _wet_lab_cache = None
    return _wet_lab_cache


def build_one(key: str, scenario: dict) -> dict:
    ev = RG.collect_evidence(scenario)
    rep = RG.build_report(ev)
    html = RG.render_html(rep)

    os.makedirs(HTML_DIR, exist_ok=True)
    os.makedirs(JSON_DIR, exist_ok=True)
    os.makedirs(DOCX_DIR, exist_ok=True)
    with open(os.path.join(HTML_DIR, f"{key}.html"), "w", encoding="utf-8") as f:
        f.write(html)
    with open(os.path.join(JSON_DIR, f"{key}.json"), "w", encoding="utf-8") as f:
        json.dump(ev, f, ensure_ascii=False, indent=2)
    with open(os.path.join(DOCX_DIR, f"{key}.docx"), "wb") as f:
        f.write(RG.render_docx(rep))

    s, m, d, v = ev["scenario"], ev["metrics"], ev["design"], ev["verdict"]
    # 信任标签（验证类型 / 适用域 / 禁止场景）—— 结果页第一眼决定「这份报告
    # 值不值得下钻」的答案。engine/scenario_trust_labels.py 单一真源。
    wet = _wet_lab_doc()
    wl_grade = wet_lab_grade_for_key(wet, key) if wet else None
    labels = labels_for(key,
                        s.get("published", ""),
                        s.get("references"),
                        wl_grade)

    return {
        "key": key,
        "domain": s.get("domain", s.get("category", "")),
        "dim": s.get("dim"),
        "bounds": s.get("bounds"),
        "published": s.get("published", ""),
        "references": s.get("references", []),
        "verdict": v.get("grade"),
        "rationale": v.get("rationale", ""),
        "r2": m.get("r2"),
        "r2_core": m.get("r2_core"),
        "coverage": m.get("coverage"),
        "rmse_rel": m.get("rmse_rel"),
        "cal_err": m.get("cal_err"),
        "kernel": d.get("kernel"),
        "lengthscale_source": d.get("lengthscale_source"),
        "n_train": d.get("n_train"),
        "n_test": d.get("n_test"),
        "calibration_scale": ev.get("calibration_scale"),
        "high_freq": bool(ev.get("smoothness", {}).get("high_freq")),
        "replication_passed": bool(ev.get("replication", {}).get("passed"))
        if ev.get("replication") else None,
        # ---- trust labels ----
        "validation_type": labels["validation_type"],
        "validation_type_label": labels["validation_type_label"],
        "applicable_domain": labels["applicable_domain"],
        "forbidden_use": labels["forbidden_use"],
        "wet_lab_grade": wl_grade,
        "html": f"/reports/html/{key}.html",
        "json": f"/reports/json/{key}.json",
        "docx": f"/reports/docx/{key}.docx",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="仅生成指定 key（逗号分隔）")
    ap.add_argument("--limit", type=int, help="仅前 N 个场景（调试）")
    args = ap.parse_args()

    reg = RG.all_scenarios()
    keys = sorted(reg)
    if args.only:
        keys = [k for k in keys if k in set(args.only.split(","))]
    if args.limit:
        keys = keys[: args.limit]

    os.makedirs(OUT_ROOT, exist_ok=True)
    manifest = {
        "product": "SwarmLabs Virtual Experiment V&V Report Gallery",
        "standard": "ASME V&V 10-2019 / FDA CM&S report outline",
        "generated_at_utc": RG._provenance()["generated_at_utc"],
        "engine": "engine/surrogate.py :: SurrogateEngine (GP + UQ)",
        "note": "报告由确定性离线计算生成（无 LLM），对照场景自带解析解/发表基准；"
                "用于真实决策前需按 mirror-run 做对等度验收。",
        "total": len(keys),
        # 注册表口径 vs 本次实际构建口径必须分开记录。
        # 2026-09-21 实测的事故：rdkit 缺失的解释器跑本脚本，注册表是 82 但
        # 只构建了 80 个，且唯一的 REFUTED 就在被丢掉的那两个里——
        # 只写 total=80 的话，从产物上完全看不出「有 2 个场景没被构建」。
        "registry_total": len(reg),
        "optional_deps": {
            "rdkit_available": bool(_ms._rdkit_available),
            "dropped_molecular_scenarios": list(_ms.dropped_molecular_scenarios()),
        },
        "counts": {"PASS": 0, "MARGINAL": 0, "REFUTED": 0, "ERROR": 0},
        "scenarios": [],
    }

    t0 = time.time()
    for i, k in enumerate(keys, 1):
        try:
            entry = build_one(k, reg[k])
            manifest["scenarios"].append(entry)
            g = entry["verdict"]
            if g in manifest["counts"]:
                manifest["counts"][g] += 1
            else:
                manifest["counts"]["ERROR"] += 1
            print(f"[{i}/{len(keys)}] {k}: {g}", flush=True)
        except Exception as e:
            manifest["counts"]["ERROR"] += 1
            manifest["scenarios"].append({"key": k, "verdict": "ERROR", "error": str(e)})
            print(f"[{i}/{len(keys)}] {k}: ERROR {e}", flush=True)

    with open(os.path.join(OUT_ROOT, "index.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    # 报告一变，门禁账本必须跟着刷新——否则 HTTP 闸门 /v3/gate 会给出过期决策。
    # 账本口径来自 engine/verification_gate.py（唯一真源）。
    try:
        import build_gate_ledger as _gl

        led = _gl.build_ledger()
        for _out in (_gl.OUT_PUBLIC, _gl.OUT_DATA):
            os.makedirs(os.path.dirname(_out), exist_ok=True)
            with open(_out, "w", encoding="utf-8") as f:
                json.dump(led, f, ensure_ascii=False, indent=2)
        print(f"门禁账本已刷新：blocked {led['n_blocked']} / "
              f"human_check {led['n_human_check_required']} / total {led['total']}")
    except Exception as e:  # 账本失败不应让报告产物作废
        print(f"[warn] 门禁账本生成失败：{e}")

    # 公开留出基准集同样跟着报告走——/v3/verify 的精确复算依赖它。
    try:
        import build_benchmark_sets as _bm

        _keys = sorted(RG.all_scenarios().keys())
        _res = {k: _bm.build_one(k) for k in _keys}
        _mani = _bm.build_manifest(_res)
        os.makedirs(_bm.OUT_DIR, exist_ok=True)
        for _k, _one in _res.items():
            with open(os.path.join(_bm.OUT_DIR, f"{_k}.json"), "w", encoding="utf-8") as f:
                json.dump(_one, f, ensure_ascii=False, separators=(",", ":"))
        with open(os.path.join(_bm.OUT_DIR, "index.json"), "w", encoding="utf-8") as f:
            json.dump(_mani, f, ensure_ascii=False, indent=2)
        print(f"公开留出基准集已刷新：{_mani['total']} 场景 / "
              f"{_mani['total_test_points']} 测试点 → {_bm.OUT_DIR}")
    except Exception as e:
        print(f"[warn] 公开留出基准集生成失败：{e}")

    # 湿实验锚定报告：V&V 证明「模型忠实于 oracle」，本步证明「oracle 符合真实实验」。
    # 两条独立证据链，缺一条都不能宣称可信。
    try:
        import build_wet_lab_anchors as _wla

        _doc = _wla.build()
        os.makedirs(os.path.dirname(_wla.OUT), exist_ok=True)
        with open(_wla.OUT, "w", encoding="utf-8") as f:
            json.dump(_doc, f, ensure_ascii=False, indent=2)
        _gc = _doc["summary"]["grade_counts"]
        print(f"湿实验锚定已刷新：AGREE {_gc['AGREE']} / NEAR {_gc['NEAR']} / "
              f"CONTRADICTED {_gc['CONTRADICTED']} / UNANCHORED {_gc['UNANCHORED']} "
              f"（{_doc['summary']['total']} 个可锚定场景）→ {_wla.OUT}")
        if _doc["summary"]["blocked"]:
            print(f"[warn] 与湿实验文献冲突的场景：{_doc['summary']['blocked']}")
    except Exception as e:
        print(f"[warn] 湿实验锚定生成失败：{e}")

    dt = time.time() - t0
    c = manifest["counts"]
    print(f"\n完成：PASS {c['PASS']} / MARGINAL {c['MARGINAL']} / REFUTED {c['REFUTED']} "
          f"/ ERROR {c['ERROR']}（{len(keys)} 个）→ {OUT_ROOT}  （耗时 {dt:.1f}s）")


if __name__ == "__main__":
    main()
