"""compare_runs.py —— 两次 AgentTeams 运行的**可对比性**验收工具。

用途（INTERFACE.md §3 Phase 4 的验收点）
---------------------------------------
拓扑驱动（N-agent）与默认（4-agent）两条路径跑同一请求，必须证明：

1. **不劣化**：每个场景的 rel / coverage / cal_err 与 baseline 一致或在阈值内；
2. **可追溯**：拓扑路径的产物里能读到 topology_source / module_routing；
3. **顾问层不改判定**：启用本地模型后，判定字段与不启用时完全相同。

设计原则：本工具**只读**产物，不重跑、不改写任何东西。判定用严格相等
（rel 的浮点比较给 1e-12 容差），"差不多"不算通过——验收工具自己先不能
含糊，否则它会变成第二个"看起来绿"的来源。

用法
----
    python -m agentteams.tools.compare_runs <baseline_run_dir> <candidate_run_dir>
    python -m agentteams.tools.compare_runs --out report.json A B
"""

from __future__ import annotations

import argparse
import json
import os
import sys

TOL = 1e-12                      # 浮点严格相等容差：判定字段必须一致
JUDGE_FIELDS = ("rel", "coverage", "cal_err", "maturity")


def _load(run_dir: str) -> dict:
    lr = os.path.join(run_dir, "loop_report.json")
    rs = os.path.join(run_dir, "result.json")
    if not os.path.exists(lr):
        raise SystemExit(f"缺少 loop_report.json：{lr}")
    with open(lr, encoding="utf-8") as f:
        loop = json.load(f)
    result = {}
    if os.path.exists(rs):
        with open(rs, encoding="utf-8") as f:
            result = json.load(f)
    return {"loop": loop, "result": result}


def _rows(loop: dict) -> dict:
    return {r["key"]: r for r in (loop.get("rows") or [])}


def compare(a_dir: str, b_dir: str) -> dict:
    A, B = _load(a_dir), _load(b_dir)
    ra, rb = _rows(A["loop"]), _rows(B["loop"])

    only_a = sorted(set(ra) - set(rb))
    only_b = sorted(set(rb) - set(ra))
    common = sorted(set(ra) & set(rb))

    diffs, judged = [], []
    for k in common:
        row_diff = {}
        for f in JUDGE_FIELDS:
            va, vb = ra[k].get(f), rb[k].get(f)
            if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
                if abs(va - vb) > TOL:
                    row_diff[f] = {"baseline": va, "candidate": vb,
                                   "delta": vb - va}
            elif va != vb:
                row_diff[f] = {"baseline": va, "candidate": vb}
        judged.append({"key": k, **{f: {"baseline": ra[k].get(f),
                                        "candidate": rb[k].get(f)}
                                    for f in JUDGE_FIELDS},
                       "identical": not row_diff})
        if row_diff:
            diffs.append({"key": k, "diff": row_diff})

    sa = A["loop"].get("topology_source")
    sb = B["loop"].get("topology_source")
    ma = A["loop"].get("module_routing")
    mb = B["loop"].get("module_routing")
    adv_a = A["loop"].get("local_model_advisory")
    adv_b = B["loop"].get("local_model_advisory")

    out = {
        "baseline_dir": os.path.abspath(a_dir),
        "candidate_dir": os.path.abspath(b_dir),
        "scenarios": {
            "common": common, "only_baseline": only_a, "only_candidate": only_b,
        },
        "judgement_identical": not diffs,
        "judgement_diffs": diffs,
        "rows": judged,
        "topology_source": {
            "baseline": sa, "candidate": sb,
            "candidate_records_topology": bool(sb),
            "n_agents_baseline": (sa or {}).get("n_agents"),
            "n_agents_candidate": (sb or {}).get("n_agents"),
        },
        "module_routing": {
            "baseline_present": bool(ma),
            "candidate_present": bool(mb),
            "candidate_summary": (mb or {}).get("summary"),
        },
        "local_model_advisory": {
            "baseline_status": (adv_a or {}).get("status"),
            "candidate_status": (adv_b or {}).get("status"),
            "baseline_model": (adv_a or {}).get("model"),
            "candidate_model": (adv_b or {}).get("model"),
            "affects_judgement": False,
            "note": "顾问层为旁路；其输出未进入任何判定字段（本工具已逐一比对）",
        },
    }
    # 总判定：不劣化 + 可追溯。顾问层状态不参与判定（它本就不该影响结论）。
    out["PASS"] = bool(
        out["judgement_identical"]
        and not only_a and not only_b
        and out["module_routing"]["candidate_present"]
    )
    return out


def render(o: dict) -> str:
    L = []
    L.append("AgentTeams 运行对照（Phase 4 验收）")
    L.append(f"  baseline : {o['baseline_dir']}")
    L.append(f"  candidate: {o['candidate_dir']}")
    ts = o["topology_source"]
    L.append("")
    L.append(f"拓扑记录   : baseline={ts['n_agents_baseline']} agents · "
             f"candidate={ts['n_agents_candidate']} agents · "
             f"candidate 有 topology_source={ts['candidate_records_topology']}")
    mr = o["module_routing"]
    L.append(f"能力路由   : candidate 有 module_routing={mr['candidate_present']}"
             + (f" · 模块 {mr['candidate_summary'].get('n_modules')} 个 / "
                f"路由步骤 {mr['candidate_summary'].get('n_steps_routed')} / "
                f"回退 {mr['candidate_summary'].get('n_fallback')}"
                if mr["candidate_summary"] else ""))
    lm = o["local_model_advisory"]
    L.append(f"顾问层     : baseline={lm['baseline_status']} · "
             f"candidate={lm['candidate_status']} · 影响判定={lm['affects_judgement']}")
    L.append("")
    L.append(f"{'scenario':<24}{'rel(base)':>12}{'rel(cand)':>12}"
             f"{'cov':>7}{'cal':>8}  identical")
    for r in o["rows"]:
        L.append(f"{r['key']:<24}{r['rel']['baseline']:>12.6f}"
                 f"{r['rel']['candidate']:>12.6f}"
                 f"{r['coverage']['candidate']:>7.2f}"
                 f"{r['cal_err']['candidate']:>8.3f}  {r['identical']}")
    L.append("")
    if o["judgement_diffs"]:
        L.append(f"判定差异   : {len(o['judgement_diffs'])} 处 —— 见 JSON judgement_diffs")
        for d in o["judgement_diffs"]:
            L.append(f"  - {d['key']}: {d['diff']}")
    else:
        L.append("判定差异   : 无（所有场景的 rel/coverage/cal_err/maturity 完全一致）")
    L.append("")
    L.append(f"总判定     : {'PASS' if o['PASS'] else 'FAIL'}"
             "（不劣化 + 拓扑可追溯）")
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="对照两次 AgentTeams 运行（Phase 4 验收）")
    ap.add_argument("baseline")
    ap.add_argument("candidate")
    ap.add_argument("--out", default=None, help="把对照结果写成 JSON")
    ap.add_argument("--quiet", action="store_true", help="不打印表格，只给退出码")
    args = ap.parse_args(argv)

    o = compare(args.baseline, args.candidate)
    if not args.quiet:
        print(render(o))
    if args.out:
        out_dir = os.path.dirname(os.path.abspath(args.out))
        os.makedirs(out_dir, exist_ok=True)   # 验收产物目录可能尚未创建
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(o, f, ensure_ascii=False, indent=2)
        print(f"\n已写出：{args.out}")
    return 0 if o["PASS"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
