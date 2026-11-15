"""VerifierAgent —— 唯一有权把数据写进共享知识库的角色，且必须过审批。

它做三件事：
1. 判读 Executor 的指标（成熟度、UQ 是否失真）；
2. 产出新实验方向建议；
3. 只有当判读结论支持"补点"时，才发起高风险写操作 experiment.apply_correction；
   该动作会被策略闸门拦下，等待人工批准（--approve）。被拦下时任务返回
   partial 而不是 error——拿不到批准是**预期内的正常分支**，不是故障。
"""

from __future__ import annotations

import time

from ..core.contracts import TaskSpec, Worker, WorkerResult
from ..core.context import SkillBlocked


class VerifierAgent(Worker):
    role = "verifier"
    capabilities = ("UQ 校准判读", "成熟度裁定", "知识库修正（需审批）")
    allowed_skills = ("uq.calibration_report", "experiment.apply_correction")

    def run(self, task: TaskSpec, ctx) -> WorkerResult:
        t0 = time.time()
        upstream = task.payload.get("_upstream") or {}
        validations = []
        for up in upstream.values():
            validations += (up or {}).get("validations", [])
        if not validations:
            return self._result(task, "error", error="上游未提供验证结果", started=t0)

        thresholds = task.payload.get("thresholds") or {}
        allow_correction = bool(task.payload.get("allow_correction", True))

        judgements, suggestions, corrections, pending = [], {}, [], []
        for v in validations:
            j = ctx.invoke_skill(self.role, "uq.calibration_report",
                                 {"validation": v, "thresholds": thresholds})
            j["key"] = v["key"]
            judgements.append(j)
            suggestions[v["key"]] = j.get("suggestions", [])

            if allow_correction and j["needs_correction"]:
                try:
                    c = ctx.invoke_skill(self.role, "experiment.apply_correction",
                                         {"scenario": v["key"]})
                    c["key"] = v["key"]
                    corrections.append(c)
                except SkillBlocked as e:
                    pending.append({"scenario": v["key"], "reason": str(e),
                                    "action": "experiment.apply_correction"})

        status = "ok" if not pending else "partial"
        out = {"judgements": judgements, "suggestions": suggestions,
               "corrections": corrections, "pending_approval": pending}
        # 义务图证据（Eureka 借鉴）：Verifier 是**复核者**，更要自带可判定的
        # rubric 证据——它的"通过"必须能被下游独立重算，而不是一句结论。
        try:
            from ..core.obligations import derive_rubric
            out["trust_rows"] = [dict(v, rubric=derive_rubric(v))
                                 for v in validations]
        except Exception:                                # noqa: BLE001
            pass
        ev = [f"{j['key']}: {j['maturity']} · rel={j['rel']}" for j in judgements]
        return self._result(task, status, out, evidence=ev, started=t0)
