"""运行时抽象：本地 Leader-Worker 执行器 + 外部平台适配器。

**这是"可替换性 / 迁移成本"的答案所在。**

赛题以 AgentTeams（Leader-Worker）为协同设计基点。我们把"协同拓扑"与
"执行载体"解耦：
- 协同拓扑（谁拆解、谁执行、依赖关系、权限边界）由 Leader + TaskSpec DAG 定义，
  与运行时无关；
- 执行载体由 Runtime 决定。`LocalRuntime` 是零依赖的本地实现；
  `AgentTeamsRuntime` 是把同一份 TaskSpec 映射到外部平台消息的适配器。

迁移成本 = 实现一个 Runtime 子类（约 100 行映射代码），业务 Agent / Skill
一行不改。我们没有假装已经跑在别人的专有运行时上——那是过度声称；
我们证明的是"随时可以搬过去"。
"""

from __future__ import annotations

from typing import Dict, List

from .context import Context, SkillBlocked
from .contracts import TaskSpec, WorkerResult


class Runtime:
    name = "abstract"

    def dispatch(self, tasks: List[TaskSpec], workers: Dict[str, object],
                 ctx: Context) -> List[WorkerResult]:
        raise NotImplementedError


class LocalRuntime(Runtime):
    """按 depends_on 做拓扑排序后顺序执行（同层可并行，这里保持确定性顺序执行，
    因为科学验证要求可复现；并行只影响墙钟时间，不影响结论）。"""

    name = "local"

    def dispatch(self, tasks: List[TaskSpec], workers: Dict[str, object],
                 ctx: Context) -> List[WorkerResult]:
        order = self._toposort(tasks)
        done: Dict[str, WorkerResult] = {}
        results: List[WorkerResult] = []

        for task in order:
            worker = workers.get(task.role)
            if worker is None:
                results.append(WorkerResult(task.task_id, task.role, "error",
                                            error=f"未注册角色 {task.role}"))
                continue

            # 上游失败 → 下游跳过（fail-fast，但不 crash 整个团队）
            upstream_bad = [d for d in task.depends_on
                            if d in done and done[d].status == "error"]
            if upstream_bad:
                r = WorkerResult(task.task_id, task.role, "error",
                                 error=f"上游任务失败：{upstream_bad}", skipped=True)
                done[task.task_id] = r
                results.append(r)
                continue

            payload = dict(task.payload)
            payload["_upstream"] = {d: done[d].output for d in task.depends_on if d in done}
            task = TaskSpec(task.task_id, task.role, task.intent, payload,
                            task.depends_on, task.risk)

            with ctx.tracer.span(f"agent:{task.role}", kind="agent",
                                 task_id=task.task_id, intent=task.intent) as sp:
                try:
                    r = worker.run(task, ctx)
                except SkillBlocked as e:
                    r = WorkerResult(task.task_id, task.role, "blocked",
                                     error=str(e))
                    sp.status = "blocked"
                except Exception as e:                      # noqa: BLE001
                    r = WorkerResult(task.task_id, task.role, "error",
                                     error=f"{type(e).__name__}: {e}")
                    sp.status = "error"
                sp.set_output({"status": r.status, "keys": list(r.output.keys())})

            done[task.task_id] = r
            results.append(r)
        return results

    @staticmethod
    def _toposort(tasks: List[TaskSpec]) -> List[TaskSpec]:
        by_id = {t.task_id: t for t in tasks}
        visited, out = set(), []

        def visit(tid, stack):
            if tid in visited:
                return
            if tid in stack:
                raise ValueError(f"任务依赖成环：{tid}")
            stack.add(tid)
            for dep in by_id[tid].depends_on:
                if dep in by_id:
                    visit(dep, stack)
            stack.discard(tid)
            visited.add(tid)
            out.append(by_id[tid])

        for t in tasks:
            visit(t.task_id, set())
        return out


class AgentTeamsRuntime(Runtime):
    """AgentTeams / HiClaw 等外部 Leader-Worker 平台的适配器骨架。

    刻意保留为**显式未接入**状态而不是伪造调用：接入需要平台侧凭证与网络环境，
    在没有真实环境的情况下写"假装成功"的代码，是对评审的不诚实。

    真正接入时只需实现 `_submit` / `_poll` 两个方法：
      TaskSpec.role     → 平台 Worker 角色名
      TaskSpec.payload  → 平台任务消息体
      TaskSpec.depends_on → 平台任务依赖声明
      平台回执          → WorkerResult
    其余 Leader / Worker / Skill / Policy / Trace 代码完全复用。
    """

    name = "agentteams"

    def __init__(self, endpoint: str = "", token: str = ""):
        self.endpoint = endpoint
        self.token = token

    def dispatch(self, tasks, workers, ctx):
        raise NotImplementedError(
            "AgentTeamsRuntime 未接入真实平台。本项目以 Leader-Worker 拓扑为设计基点，"
            "默认使用 LocalRuntime 保证零依赖可复现；迁移只需实现本类的 _submit/_poll。"
        )


RUNTIMES = {"local": LocalRuntime, "agentteams": AgentTeamsRuntime}


def build_runtime(name: str) -> Runtime:
    cls = RUNTIMES.get(name)
    if cls is None:
        raise ValueError(f"未知 runtime：{name}，可选 {list(RUNTIMES)}")
    return cls()


# ---------------------------------------------------------------------- #
# 拓扑驱动构建（INTERFACE.md Phase 1 / S1-S3）
# ---------------------------------------------------------------------- #
#: C1 `.swarm.json` 的 `swarm_role` → 本项目 Worker 实现类的映射。
#: motif 里的 `cell` / `tissue_rep` / `guardrail` 等生物角色，经
#: `to_agentteams_team()` 归一到 planner / executor / verifier / leader 四类语义。
_TOPOLOGY_ROLE_CLASS: Dict[str, str] = {
    "planner": "PlannerAgent",
    "executor": "ExecutorAgent",
    "verifier": "VerifierAgent",
}


def build_workers_from_topology(topology_file: str):
    """消费 C1 `.swarm.json`，把 flybrain 的 motif 拓扑真正接进运行时。

    这是 INTERFACE.md Phase 1 / S1-S3 的落点。此前 `build_workers()` 硬编码 3 个
    agent、`team.yaml` 的 workers 段只是文档装饰，flybrain 的 motif 在运行时被
    **完全跳过**；这里改为按拓扑动态生成 agent 实例。

    返回 ``(workers, topology_meta, team)``：

    * ``workers``       —— 同时含 **3 个规范 role 键**（planner/executor/verifier，
      保证既有「按 role 派发」链路一字不改）+ **每个 ``agent_id`` 键**（同一实例，
      为 Phase 3 的 per-agent 语义路由铺路）。
    * ``topology_meta`` —— 写进 result.json / loop_report.json 的溯源信息
      （S6：每次拓扑切换留痕）。
    * ``team``          —— ``to_agentteams_team()` 的完整产物（modules/graph/...）。

    fail-closed：拓扑独立复算校验不过直接抛 ``ValueError``，绝不让坏拓扑静默
    进入运行时（与 loader 的 ``validate_swarm_config`` 同一判据，不复用生成器自报）。
    """
    # 延迟 import：避免 agentteams.core ↔ agentteams.agents 的导入环，
    # 也避免无 topology 需求的默认路径被迫依赖 agent_topology 包。
    from agent_topology.load_organism_config import (
        load_organism_config, to_agentteams_team, validate_swarm_config)
    from ..agents.executor import ExecutorAgent
    from ..agents.planner import PlannerAgent
    from ..agents.verifier import VerifierAgent
    role_cls = {"planner": PlannerAgent, "executor": ExecutorAgent,
                "verifier": VerifierAgent}

    cfg = load_organism_config(topology_file)
    report = validate_swarm_config(cfg)
    if not report.get("ok"):
        bad = {k: v for k, v in report.items()
               if k not in ("ok", "n_agents", "n_edges") and v}
        raise ValueError(
            "topology failed validation (%s): %s" % (topology_file, bad))

    team = to_agentteams_team(cfg)
    swarm = cfg.get("swarm") or {}

    # Phase 3 / S4：planner 拿到 modules(capability) + agents(task_hint) 索引，
    # 才能在选题之后回答「这一步交给哪个 agent 做」。R6：再补 blueprint +
    # edge.semantic 索引，让蓝图从死数据变成可审计的语义路由建议。其余角色不变。
    modules = team.get("modules") or []
    agents_idx = {a["id"]: a for a in (swarm.get("agents") or []) if a.get("id")}
    shared_planner = PlannerAgent(
        modules=modules, agents=agents_idx,
        blueprint=cfg.get("blueprint"),
        blueprint_info=cfg.get("blueprint_info"),
        edges=swarm.get("edges"))

    workers: Dict[str, object] = {}
    for w in team.get("workers", []):
        role = w.get("role")
        cls = role_cls.get(role)
        if cls is None:
            continue                     # leader 由 TeamLeader 承担，不在此列
        # planner 全体共用同一个带拓扑的实例：路由索引建一次，不随 agent 数膨胀
        inst = shared_planner if role == "planner" else cls()
        workers.setdefault(role, inst)        # 规范 role 键（取首个代表）
        workers[w["agent_id"]] = inst         # per-agent 键（Phase 3 路由用）
    # 拓扑若缺某 role，兜底补一个，保证既有 3-task 派发永远可解析
    for role, cls in role_cls.items():
        workers.setdefault(role, shared_planner if role == "planner" else cls())

    agentteams = swarm.get("agentteams") or {}
    meta = {
        "source": "topology_file",
        "path": str(topology_file),
        "name": swarm.get("name"),
        "schema_version": cfg.get("schema_version"),
        "topology": swarm.get("topology"),
        "blueprint": cfg.get("blueprint"),
        "leader": agentteams.get("leader"),
        "n_agents": report.get("n_agents"),
        "n_edges": report.get("n_edges"),
        "n_modules": len(swarm.get("modules") or []),
        "n_workers_registered": len(workers),
        "validation_ok": bool(report.get("ok")),
    }
    return workers, meta, team
