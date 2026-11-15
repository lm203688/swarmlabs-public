"""运行报告渲染 Skill（只写本次运行目录，属可丢弃产物）。"""

from __future__ import annotations

import json
import os
from typing import Any, Dict

from ..core.contracts import Skill, SkillSpec


class RenderReport(Skill):
    spec = SkillSpec(
        name="report.render",
        version="1.0.0",
        purpose="把团队运行结果与 AgentLoop 飞轮报告渲染为人类可读的 Markdown 交付物。",
        inputs={"team_result": "dict", "loop_report": "dict", "out_dir": "本次运行目录"},
        outputs={"path": "report.md 路径"},
        preconditions="AgentLoop 已完成 evaluate 阶段。",
        side_effect="write", risk="low", scope="run_local",
        failure_modes="目录不可写 → 抛异常并保留 result.json，不吞错。",
        reuse_note="渲染层与计算层解耦，换任何报告模板都不影响 Agent 逻辑。",
        depends_on_tools=(),
    )

    def run(self, params: Dict[str, Any], ctx) -> Dict[str, Any]:
        tr = params["team_result"]
        lr = params["loop_report"]
        out_dir = params["out_dir"]
        os.makedirs(out_dir, exist_ok=True)

        ev = lr.get("evaluate", {})
        rows = lr.get("rows", [])
        tel = lr.get("analyze", {})
        opt = lr.get("optimize", {})

        L = []
        L.append(f"# SwarmLabs AgentTeams 运行报告 · `{tr['run_id']}`")
        L.append("")
        L.append(f"- **目标**：{tr['goal']}")
        L.append(f"- **团队状态**：{tr['status']}")
        L.append(f"- **成熟度评分卡**：{ev.get('scorecard')} · 已收敛 {ev.get('converged_ratio')} · 团队得分 {ev.get('team_score')}")
        L.append("")
        L.append("## 一、验证结果（带误差棒）")
        L.append("")
        L.append("| 场景 | 领域 | 累积点 | rmse | 相对误差 | 95%CI 覆盖率 | 校准误差 | 成熟度 |")
        L.append("|---|---|---|---|---|---|---|---|")
        for r in rows:
            L.append(f"| `{r['key']}` | {r['domain']} | {r['n_points']} | {r['rmse']} | "
                     f"{r['rel']:.1%} | {r['coverage']:.0%} | {r['cal_err']} | **{r['maturity']}** |")
        L.append("")
        L.append("## 二、下一步实验锦标赛（按期望信息增益排名）")
        L.append("")
        for key, t in (opt.get("tournaments") or {}).items():
            L.append(f"**{key}**")
            if not t:
                L.append("- （候选网格已充分覆盖）")
            for i, c in enumerate(t, 1):
                L.append(f"{i}. 候选点 `{c['point']}` · 后验标准差 {c['std']} · 信息增益 {c['info_gain']}")
            L.append("")
        L.append("## 三、新实验方向建议")
        L.append("")
        for key, sug in (opt.get("suggestions") or {}).items():
            L.append(f"**{key}**")
            for s in sug:
                L.append(f"- {s}")
            L.append("")
        L.append("## 四、下一轮优先级（写入 AgentLoop 状态，下次运行生效）")
        L.append("")
        for i, p in enumerate(opt.get("next_priority") or [], 1):
            L.append(f"{i}. `{p['key']}` — {p['maturity']}（相对误差 {p['rel']:.1%}）")
        trend = opt.get("trend") or {}
        if trend:
            L.append("")
            L.append("### 飞轮趋势（与上次同场景相比）")
            for k, v in trend.items():
                L.append(f"- `{k}`：Δrel={v['delta_rel']} → {v['direction']}")
        L.append("")
        L.append("## 五、可观测与审计")
        L.append("")
        L.append(f"- span 总数 {tel.get('spans')}（Agent {tel.get('agent_calls')} / Skill {tel.get('skill_calls')}）")
        L.append(f"- 被策略拦截 {tel.get('blocked')} 次 · 错误 {tel.get('errors')} 次 · 审计事件 {tel.get('audit_events')} 条")
        L.append(f"- 原始链路：`trace.jsonl` · 审计：`audit.jsonl` · 回滚快照：`rollback/`")
        blocked = tr.get("blocked") or []
        if blocked:
            L.append("")
            L.append("### 待人工确认（Human-in-the-Loop）")
            for b in blocked:
                L.append(f"- `{b['skill']}`（发起者 {b['actor']}）：{b['reason']}")
        L.append("")

        path = os.path.join(out_dir, "report.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(L))
        with open(os.path.join(out_dir, "loop_report.json"), "w", encoding="utf-8") as f:
            json.dump(lr, f, ensure_ascii=False, indent=2, default=str)
        return {"path": path}
