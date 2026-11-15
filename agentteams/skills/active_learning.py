"""主动学习 / 实验锦标赛 Skill。

把"下一步该做哪个实验"变成可排序的决策：候选点按 GP 后验方差
（≈ 期望信息增益）排名。对标 Google Co-Scientist 的 idea tournament，
区别是我们的排名依据是可计算的统计量，不是模型自评分。
"""

from __future__ import annotations

from typing import Any, Dict

from ..core.contracts import Skill, SkillSpec
from . import _engine


class RankCandidates(Skill):
    spec = SkillSpec(
        name="active_learning.rank_candidates",
        version="1.0.0",
        purpose="对候选下一步实验点按期望信息增益排名，产出可执行的『实验锦标赛』清单。",
        inputs={"scenario": "场景 key", "top_n": "返回前 N 个候选，默认 5"},
        outputs={"tournament": "[{point, std, info_gain}]"},
        preconditions="场景已有累积数据点（否则退化为种子点上的 GP）。",
        side_effect="read", risk="low", scope="run_local",
        failure_modes="候选点被已采样点全覆盖 → 返回空列表并说明'当前网格已充分采样'。",
        reuse_note="与具体学科无关，任何有代理模型的实验设计问题都可复用该排名器。",
        depends_on_tools=("scripts/paper_validation_loop.py", "numpy"),
    )

    def run(self, params: Dict[str, Any], ctx) -> Dict[str, Any]:
        pvl = _engine.engine()
        s = _engine.scenario_by_key(params["scenario"])
        top_n = int(params.get("top_n", 5))
        t = pvl.rank_next_experiments(s, top_n=top_n)
        note = "" if t else "当前候选网格已被采样点充分覆盖，建议扩大 bounds 或升保真度。"
        return {"tournament": t, "note": note}
