"""AgentLoop 数据飞轮：Collect → Analyze → Evaluate → Optimize。

与"跑完一次就结束"的 pipeline 的本质区别在最后一步：**Optimize 的产出会落盘，
并被下一次运行的 Planner 读取**（state/agentloop_state.json）。因此这是一个
真正的闭环——第 N 次运行的结论改变第 N+1 次运行的行为，而不是每次从零开始。

四步的具体含义（对科学验证场景的落地，不是套概念）：
- Collect ：本次运行的 trace/audit + 各场景验证指标；
- Analyze ：把原始指标换算成可比较的量（相对误差 rel、UQ 覆盖率、校准误差）；
- Evaluate：按阈值判定成熟度（已收敛 / 收敛中 / 需数据校准），并给出团队级评分卡；
- Optimize：产出下一步动作——优先级队列（下次先跑谁）+ 实验锦标赛候选点
            （按 GP 后验方差 = 期望信息增益排序）+ 结构化新方向建议。
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List

STATE_REL_PATH = os.path.join("state", "agentloop_state.json")


def load_state(pkg_root: str) -> Dict[str, Any]:
    p = os.path.join(pkg_root, STATE_REL_PATH)
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_state(pkg_root: str, state: Dict[str, Any]):
    p = os.path.join(pkg_root, STATE_REL_PATH)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


class AgentLoop:
    def __init__(self, ctx, pkg_root: str, thresholds: Dict[str, float]):
        self.ctx = ctx
        self.pkg_root = pkg_root
        self.th = thresholds

    # -- 1. Collect --------------------------------------------------------
    def collect(self, results: List[Dict[str, Any]]) -> Dict[str, Any]:
        spans, audits = self.ctx.tracer.collect()
        scenarios = []
        for r in results:
            for item in (r.get("output", {}) or {}).get("validations", []) or []:
                scenarios.append(item)
        return {"spans": spans, "audits": audits, "scenarios": scenarios}

    # -- 2. Analyze --------------------------------------------------------
    def analyze(self, collected: Dict[str, Any]) -> Dict[str, Any]:
        rows = []
        for s in collected["scenarios"]:
            scale = max(float(s.get("scale", 1.0)), 1e-9)
            rel = float(s["rmse_after"]) / scale
            rows.append({
                "key": s["key"],
                "domain": s.get("domain", ""),
                "n_points": s.get("n_points"),
                "rmse": round(float(s["rmse_after"]), 4),
                "rel": round(rel, 4),
                "coverage": round(float(s["coverage_after"]), 3),
                "cal_err": round(float(s["cal_err_after"]), 3),
            })
        spans = collected["spans"]
        agent_spans = [x for x in spans if x["kind"] == "agent"]
        skill_spans = [x for x in spans if x["kind"] == "skill"]
        return {
            "scenarios": rows,
            "telemetry": {
                "spans": len(spans),
                "agent_calls": len(agent_spans),
                "skill_calls": len(skill_spans),
                "blocked": len([x for x in spans if x["status"] == "blocked"]),
                "errors": len([x for x in spans if x["status"] == "error"]),
                "total_latency_ms": sum(x.get("latency_ms", 0) for x in agent_spans),
                "audit_events": len(collected["audits"]),
            },
        }

    # -- 3. Evaluate -------------------------------------------------------
    def maturity(self, row: Dict[str, Any]) -> str:
        if (row["rel"] <= self.th["rel_converged"]
                and self.th["cov_min"] <= row["coverage"] <= 1.0
                and row["cal_err"] <= self.th["cal_max"]):
            return "已收敛"
        if row["coverage"] < self.th["cov_bad"] or row["rel"] > self.th["rel_bad"]:
            return "需数据/校准"
        return "收敛中"

    def evaluate(self, analyzed: Dict[str, Any]) -> Dict[str, Any]:
        rows = analyzed["scenarios"]
        for r in rows:
            r["maturity"] = self.maturity(r)
        counts = {"已收敛": 0, "收敛中": 0, "需数据/校准": 0}
        for r in rows:
            counts[r["maturity"]] += 1
        n = max(len(rows), 1)
        score = round(100.0 * (counts["已收敛"] + 0.5 * counts["收敛中"]) / n, 1)
        return {
            "scorecard": counts,
            "converged_ratio": f"{counts['已收敛']}/{len(rows)}",
            "team_score": score,
            "rows": rows,
        }

    # -- 4. Optimize -------------------------------------------------------
    def optimize(self, evaluated: Dict[str, Any], tournaments: Dict[str, Any],
                 suggestions: Dict[str, List[str]]) -> Dict[str, Any]:
        rows = evaluated["rows"]
        # 优先级：需数据/校准 > 收敛中 > 已收敛；同档内 rel 大的优先
        rank = {"需数据/校准": 0, "收敛中": 1, "已收敛": 2}
        queue = sorted(rows, key=lambda r: (rank[r["maturity"]], -r["rel"]))
        priority = [{"key": r["key"], "maturity": r["maturity"], "rel": r["rel"]}
                    for r in queue]

        state = load_state(self.pkg_root)
        hist = state.get("history", {})
        for r in rows:
            h = hist.setdefault(r["key"], [])
            h.append({"ts": round(time.time()), "rel": r["rel"],
                      "coverage": r["coverage"], "maturity": r["maturity"]})
            del h[:-20]          # 只留最近 20 次，避免状态无限膨胀
        state["history"] = hist
        state["next_priority"] = [p["key"] for p in priority]
        state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")

        # 趋势：与上一次同场景相比 rel 是否下降（飞轮是否真在转）
        trend = {}
        for k, h in hist.items():
            if len(h) >= 2:
                d = h[-1]["rel"] - h[-2]["rel"]
                trend[k] = {"delta_rel": round(d, 4),
                            "direction": "improving" if d < 0 else ("flat" if d == 0 else "regressing")}
        state["trend"] = trend
        save_state(self.pkg_root, state)

        return {
            "next_priority": priority,
            "tournaments": tournaments,
            "suggestions": suggestions,
            "trend": trend,
            "state_file": os.path.join("agentteams", STATE_REL_PATH).replace("\\", "/"),
        }

    # -- 一次完整飞轮 -------------------------------------------------------
    def run(self, results, tournaments, suggestions) -> Dict[str, Any]:
        with self.ctx.tracer.span("agentloop", kind="loop") as sp:
            collected = self.collect(results)
            analyzed = self.analyze(collected)
            evaluated = self.evaluate(analyzed)
            optimized = self.optimize(evaluated, tournaments, suggestions)
            report = {
                "collect": {"spans": len(collected["spans"]),
                            "audits": len(collected["audits"]),
                            "scenarios": len(collected["scenarios"])},
                "analyze": analyzed["telemetry"],
                "evaluate": {k: v for k, v in evaluated.items() if k != "rows"},
                "optimize": optimized,
                "rows": evaluated["rows"],
            }
            sp.set_output(report["evaluate"])
        return report
