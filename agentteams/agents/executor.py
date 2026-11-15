"""ExecutorAgent —— 执行虚拟实验，但**无权修改知识库**。

这是本系统权限设计里最值得看的一处：跑实验的人不能决定实验结果是否进入
共享知识库。Executor 的 allow_write = false，即使它调用写类 Skill 也会被
Context 拦下并记入审计。数据入库权归 Verifier，且必须过人工审批。
"""

from __future__ import annotations

import time

from ..core.contracts import TaskSpec, Worker, WorkerResult
from ..core.context import SkillBlocked


class ExecutorAgent(Worker):
    role = "executor"
    capabilities = ("GP 代理虚拟实验", "UQ 指标计算", "候选点信息增益排名")
    allowed_skills = ("virtual_experiment.validate", "active_learning.rank_candidates")

    def run(self, task: TaskSpec, ctx) -> WorkerResult:
        t0 = time.time()
        plan = (task.payload.get("_upstream") or {})
        plan_items = []
        for up in plan.values():
            plan_items += (up or {}).get("plan", [])
        if not plan_items:
            return self._result(task, "error", error="上游未提供计划", started=t0)

        validations, tournaments, evidence, failures = [], {}, [], []
        top_n = int(task.payload.get("top_n", 5))

        for item in plan_items:
            key = item["scenario"]
            try:
                v = ctx.invoke_skill(self.role, "virtual_experiment.validate",
                                     {"scenario": key})
            except SkillBlocked as e:
                failures.append({"scenario": key, "reason": str(e)})
                continue
            except Exception as e:                       # noqa: BLE001
                # 单场景失败不拖垮整批——这是长跑型科学任务的基本要求
                failures.append({"scenario": key, "reason": f"{type(e).__name__}: {e}"})
                continue
            v["domain"] = item.get("domain", v.get("domain", ""))
            validations.append(v)
            evidence.append(f"{key}: 论文基准 {item.get('ground_truth','')}")

            try:
                t = ctx.invoke_skill(self.role, "active_learning.rank_candidates",
                                     {"scenario": key, "top_n": top_n})
                tournaments[key] = t["tournament"]
            except Exception as e:                       # noqa: BLE001
                tournaments[key] = []
                failures.append({"scenario": key, "reason": f"锦标赛失败 {type(e).__name__}"})

        status = "ok" if validations else "error"
        out = {"validations": validations, "tournaments": tournaments,
               "failures": failures}
        # 义务图证据（Eureka 借鉴）：把每个场景的 rubric 判定一并带出去。
        # 这样「完成」由独立验收判定，而不是执行者自己声明 status=ok。
        try:
            from ..core.obligations import derive_rubric
            out["trust_rows"] = [dict(v, rubric=derive_rubric(v))
                                 for v in validations]
        except Exception:                                # noqa: BLE001
            pass                                         # 证据缺失只影响验收，不影响执行
        return self._result(task, status, out, evidence=evidence, started=t0,
                            error="" if validations else "全部场景执行失败")
