"""VirtualLab Guard Skill：可信替代三态门禁（对标 LabOpts Guard）。

输入一个科学场景 + 可选查询点，用该场景的 seed 观测拟合 GP 代理后做
三态门禁：PASS / CONTROLLED / REJECT。只读、低风险——门禁本身不改任何
状态，结论是否采信由上层 Agent / 审批闸门决定。
"""

from __future__ import annotations

import numpy as np
from typing import Any, Dict

from ..core.contracts import Skill, SkillSpec
from . import _engine


class LabGuard(Skill):
    spec = SkillSpec(
        name="labguard.gate",
        version="1.0.0",
        purpose="对指定科学场景的查询点做可信替代三态门禁（通过/受控/拒绝），显式标注不可信区。",
        inputs={
            "scenario": "场景 key（如 microbio_monod）",
            "points": "可选，查询点 [x,...]；缺省则在参数空间采样 30 点做覆盖门禁",
        },
        outputs={
            "n": "int",
            "counts": "{pass,controlled,reject}",
            "rejected": "被拒点索引",
            "results": "[{status,mean,std,dist_norm,reason}]",
        },
        preconditions="场景已注册；门禁基于该场景的 seed 观测拟合 GP 代理（只读）。",
        side_effect="read", risk="low", scope="run_local",
        failure_modes="场景不存在 → KeyError 显式失败；点数不足 → 引擎以最小种子集自举。",
        reuse_note="任何'虚拟结果能否当真'的判定都可复用；三态语义与 engine.guard.VirtualLabGuard 一致。",
        depends_on_tools=("engine/guard.py", "scripts/paper_validation_loop.py"),
    )

    def run(self, params: Dict[str, Any], ctx) -> Dict[str, Any]:
        from engine.guard import VirtualLabGuard
        from engine.surrogate import ExperimentSpace

        key = params["scenario"]
        s = _engine.scenario_by_key(key)
        X = np.atleast_2d(np.asarray(s["seed"], float))
        f = s["f"]
        y = np.array([float(f(x)) for x in X])

        guard = VirtualLabGuard.from_data(X, y, bounds=s["bounds"])

        points = params.get("points")
        if points is None:
            space = ExperimentSpace(s["bounds"])
            pts = space.sample(30, np.random.default_rng(7))
        else:
            pts = np.atleast_2d(np.asarray(points, float))
        return guard.gate_summary(pts)
