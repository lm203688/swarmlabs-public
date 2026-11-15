"""PlannerAgent —— 只读的实验规划者。

职责边界（刻意收窄）：
- 只能看目录和上一轮飞轮状态，**不能跑实验、不能写任何东西**；
- 它的产出是一份"做什么、为什么、按什么顺序"的计划，交给 Executor 执行。

为什么要单独设这个角色：把"选题"与"执行"分开，才能让选题依据（上一轮的
收敛状态、误差大小）被独立审计。否则执行者既定目标又交答卷，无法核验。

拓扑路由（INTERFACE.md Phase 3 / S4）
------------------------------------
当本次运行由 flybrain 的 `.swarm.json` 拓扑驱动时，PlannerAgent 额外拿到
`modules`（含 `capability` 能力声明）与 `agents`（含 `task_hint`）索引，
于是选题之后还能回答"这一步该交给哪个 agent 去做"：

    plan[i]["routing"] = {
      "step": "active_learning.rank_candidates",
      "task_hint": "candidate_rank",
      "module_index": 3, "profile_name": "integration",
      "capability_hit": "rank",
      "agents": ["m3_a0", "m3_a1", ...],
      "fallback": false
    }

**默认路径零变化**：`modules=None` 时不产出 `routing` 键，输出与历史逐字节一致。
路由是**建议 + 留痕**，不改变权限边界——Executor 仍只执行自己被授权的 Skill，
Planner 依然够不着任何写操作。
"""

from __future__ import annotations

import time

from ..core.contracts import TaskSpec, Worker, WorkerResult

#: 计划步骤 → 期望的 `task_hint` 候选（按优先级）。
#: 顺序即偏好：`experiment_run` 优先于泛化的 `numeric_integration`。
#: 候选同时覆盖 capability 与 task_hint 两种粒度，便于在稀疏拓扑上退化匹配。
STEP_TASK_HINTS = {
    "virtual_experiment.validate": ("experiment_run", "numeric_integration", "execute"),
    "active_learning.rank_candidates": ("candidate_rank", "elo_scoring", "rank"),
    "uq.calibration_report": ("output_check", "verify_output", "arbitrate_conflict"),
}

#: 蓝图语义路由表（INTERFACE.md R6 / Phase 5 前身）。
#: 键 = flybrain `.swarm.json` v1.1 **实际产出**的 `edge.semantic` 取值，
#: 已按当前 **28 个** organism 文件（2026-09-17 重新全量扫描）校准，覆盖全部
#: **29 种**真实语义标签——不留任何未映射项（需求文档 R6 片段只列了 9 种，已补全）。
#: 值 = 路由意图（全部落在 `_resolve_rule` 的 role_map 内）。
#: 意图分组：
#:   next_planner / back_to_planner / upstream_planner → planner（规划 / 反馈 / 误差上送）
#:   downstream_executor / skip_memory_direct / memory_record → executor（执行 / 快速反射 / 补记）
#:   guardrail_verifier → verifier（抑制 / 护栏）
#: 这是「建议 + 留痕」映射，不强制改写 dispatch（Phase 5 才接消息流转）。
SEMANTIC_ROUTE = {
    # —— 原始 12 类（baseline + 各蓝图额外产出）——
    "handoff": "next_planner",                 # 传递给下一层规划者
    "data_share": "downstream_executor",        # 数据共享给下游执行体
    "state_feedback": "back_to_planner",        # 递归状态回传 planner
    "cross_state_feedback": "back_to_planner",  # 跨态反馈回 planner
    "synthesis": "back_to_planner",             # 汇总回 planner
    "prediction_down": "downstream_executor",   # 预测编码：向下派发
    "predicted_action": "downstream_executor",  # 预测动作派发
    "error_up": "upstream_planner",             # 误差上送
    "action_error": "upstream_planner",         # 动作误差上送
    "bypass_fast_path": "skip_memory_direct",   # 快速反射跳跃（不进 memory）
    "async_record": "memory_record",            # 事后补记
    "halt": "guardrail_verifier",               # 抑制 / 护栏交由 verifier

    # —— 补全的真实语义标签（2026-09-17 重扫 28 文件发现，需求书片段未列）——
    "fanout_hypothesis": "downstream_executor", # 假设扇出到执行体验证
    "converge": "back_to_planner",              # 收敛信号回传 planner
    "candidate_vote": "back_to_planner",        # 竞争选择投票结果回传
    "suppress_loser": "guardrail_verifier",     # 竞争选择：抑制落败者
    "shared_stimulus": "downstream_executor",   # 共享刺激输入执行体
    "low_level_feedforward": "downstream_executor",
    "high_level_abstract": "back_to_planner",   # 高层抽象信号回传 planner
    "cognitive_command": "downstream_executor",  # 认知指令派发执行
    "hierarchical_refine": "back_to_planner",   # 层级细化回传 planner
    "surround_suppression": "guardrail_verifier",  # 侧向抑制交由护栏
    "feedforward_signal": "downstream_executor",
    "action_report": "back_to_planner",         # 动作上报
    "broadcast_observation": "back_to_planner", # 广播观测聚合回传
    "report_state": "back_to_planner",
    "report_action": "back_to_planner",
    "execute": "downstream_executor",           # 执行指令派发
    "report": "back_to_planner",                # 上报回传 planner
}


class PlannerAgent(Worker):
    role = "planner"
    capabilities = ("场景选题", "优先级排序", "计划生成", "拓扑能力路由", "蓝图语义路由")
    allowed_skills = ("scenario.catalog",)

    def __init__(self, modules=None, agents=None, blueprint=None,
                 blueprint_info=None, edges=None):
        """``modules`` / ``agents`` 来自 `.swarm.json` 拓扑；缺省即默认 4-agent 路径。

        * ``modules``: list of ``{index, lead, members[], capability[],
          profile_name, recurrent_weight}``
        * ``agents``: ``{agent_id: {id, role, swarm_role, module, task_hint,
          profile_name}}``
        * ``blueprint`` / ``blueprint_info``: v1.1 顶层新增的蓝图字段（R6）。
          缺省（默认 4-agent 路径）时 `route_next` 走 legacy，不抛错。
        * ``edges``: ``swarm.edges[]``（含 ``semantic``），用于 R6 的按边语义路由。
        """
        self._modules = list(modules or [])
        self._agents = dict(agents or {})
        self._blueprint = blueprint
        self._blueprint_info = blueprint_info
        self._edges = list(edges or [])
        # 模块索引：把 capability 与成员展开，避免每次路由都重扫
        self._cap_index = {}
        for m in self._modules:
            for cap in (m.get("capability") or []):
                self._cap_index.setdefault(cap, []).append(m)
        # 语义边索引：agent_id(to) → 指向它的语义边列表（R6 用）
        self._semantic_in = {}
        for e in self._edges:
            self._semantic_in.setdefault(e.get("to"), []).append(e)

    # -- 拓扑路由（Phase 3 / S4）------------------------------------------
    def route(self, task_hint: str) -> list:
        """按 ``task_hint`` 匹配 ``module.capability``，返回应激活的 agent id 列表。

        契约（INTERFACE.md R4）：``modules=None`` 时返回 ``[]`` 而不抛错。
        匹配优先级：module.capability 直命 → agent.task_hint 精命 → 模块内退化命中。
        """
        return self.route_detail(task_hint)["agents"]

    def route_detail(self, task_hint: str) -> dict:
        """`route()` 的完整版：附带命中依据与 fallback 归因（供审计留痕）。"""
        hint = (task_hint or "").strip()
        empty = {"task_hint": hint, "agents": [], "module_index": None,
                 "profile_name": None, "capability_hit": None,
                 "matched_by": None, "fallback": True,
                 "reason": "无拓扑（默认 4-agent 路径）" if not self._modules
                           else f"无模块声明能力 {hint!r}"}
        if not hint or not self._modules:
            return empty

        # 1) module.capability 直接命中 —— 最强的证据：生成器显式声明了该能力
        if hint in self._cap_index:
            m = self._cap_index[hint][0]
            return {"task_hint": hint, "agents": self._members_of(m),
                    "module_index": m.get("index"),
                    "profile_name": m.get("profile_name"),
                    "capability_hit": hint, "matched_by": "module.capability",
                    "fallback": False, "reason": ""}

        # 2) agent.task_hint 精确命中 —— 单点能力，只激活该 agent
        exact = [aid for aid, a in self._agents.items()
                 if a.get("task_hint") == hint]
        if exact:
            return {"task_hint": hint, "agents": sorted(exact),
                    "module_index": self._agents[exact[0]].get("module"),
                    "profile_name": self._agents[exact[0]].get("profile_name"),
                    "capability_hit": None, "matched_by": "agent.task_hint",
                    "fallback": False, "reason": ""}

        # 3) 退化：模块内存在同前缀/同词的 task_hint（如 candidate_rank ↔ rank）
        for aid, a in self._agents.items():
            th = a.get("task_hint") or ""
            if th and (th in hint or hint in th):
                mod = self._module_by_index(a.get("module"))
                if mod is not None:
                    return {"task_hint": hint, "agents": self._members_of(mod),
                            "module_index": mod.get("index"),
                            "profile_name": mod.get("profile_name"),
                            "capability_hit": hint,
                            "matched_by": "prefix_fuzzy",
                            "fallback": False,
                            "reason": f"{hint!r} 无显式声明，按 {th!r} 所在模块近似匹配"}
                break

        return empty

    def _members_of(self, module: dict) -> list:
        """模块成员：代表（lead）优先在前，其后是细胞。"""
        lead = module.get("lead")
        members = list(module.get("members") or [])
        return ([lead] if lead else []) + [m for m in members if m != lead]

    def _module_by_index(self, idx):
        for m in self._modules:
            if m.get("index") == idx:
                return m
        return None

    # -- 蓝图语义路由（Phase 5 前身 / R6）---------------------------------
    def route_next(self, task: dict, current_agent_id: str | None = None) -> dict:
        """按 ``edge.semantic`` 决定 next hop（INTERFACE.md R6）。

        契约（与 ``route`` 一致）：无 blueprint / 无 edges 时返回 legacy 路由，
        ``matched_by="legacy"``，不抛错、不改行为。这是「建议 + 留痕」，不是改写
        dispatch——实际派发仍由规范 role 键实例承担（Phase 5 才接消息流转）。
        """
        hint = (task or {}).get("task_hint")
        if not self._blueprint and not self._edges:
            return self._legacy_next(current_agent_id, hint)
        inc = self._semantic_in.get(current_agent_id) or []
        matched = []
        for e in inc:
            rule = SEMANTIC_ROUTE.get(e.get("semantic"))
            if rule:
                matched.append({"from": e.get("from"), "to": current_agent_id,
                                "semantic": e.get("semantic"), "kind": e.get("kind"),
                                "rule": rule})
        role, agents = self._resolve_rule(matched)
        return {
            "current_agent": current_agent_id,
            "task_hint": hint,
            "blueprint": self._blueprint,
            "matched_edges": matched,
            "primary_rule": (matched[0]["rule"] if matched else None),
            "next_role": role,
            "next_agents": agents,
            "matched_by": "edge.semantic" if matched else "legacy",
            "fallback": not matched,
            "reason": "" if matched else "无指向当前 agent 的语义边，回退 legacy 路由",
        }

    @staticmethod
    def _resolve_rule(matched: list) -> tuple:
        """把命中的路由规则解析成 (下一跳角色, 候选 agent 列表)。"""
        role_map = {
            "next_planner": "planner", "back_to_planner": "planner",
            "upstream_planner": "planner",
            "downstream_executor": "executor", "skip_memory_direct": "executor",
            "memory_record": "executor",
            "guardrail_verifier": "verifier",
        }
        if not matched:
            return None, []
        primary = matched[0]["rule"]
        agents = sorted({e["from"] for e in matched if e.get("from")})
        return role_map.get(primary, "executor"), agents

    @staticmethod
    def _legacy_next(current_agent_id, hint) -> dict:
        return {
            "current_agent": current_agent_id,
            "task_hint": hint,
            "blueprint": None,
            "matched_edges": [],
            "primary_rule": None,
            "next_role": None,
            "next_agents": [],
            "matched_by": "legacy",
            "fallback": True,
            "reason": "无 blueprint / 默认 4-agent 路径",
        }

    def _build_blueprint_routing(self) -> dict:
        """蓝图语义路由摘要 + 抽样 next-hop（写入 loop_report 的可审计证据）。"""
        sem_dist = {}
        for e in self._edges:
            s = e.get("semantic")
            sem_dist[s] = sem_dist.get(s, 0) + 1
        samples, seen = [], set()
        for e in self._edges:
            to = e.get("to")
            if not to or to in seen:
                continue
            seen.add(to)
            hint = (self._agents.get(to) or {}).get("task_hint")
            samples.append(self.route_next({"task_hint": hint}, to))
            if len(samples) >= 8:
                break
        return {
            "blueprint": self._blueprint,
            "blueprint_info": self._blueprint_info,
            "n_edges": len(self._edges),
            "semantic_distribution": sem_dist,
            "route_rules": SEMANTIC_ROUTE,
            "sample_next_hops": samples,
            "note": ("edge.semantic 驱动的建议+留痕路由：把每条语义边解析成『谁→我』的"
                     "下一步意图（next_planner / downstream_executor / "
                     "upstream_planner / guardrail_verifier 等）。当前仍**不改写 "
                     "dispatch**——实际派发由规范 role 键实例承担（Phase 5 才把消息"
                     "流转接上）；此处让蓝图从死数据变成可审计的路由建议。"),
        }

    def blueprint_note(self) -> dict:
        """给审计用的蓝图摘要（无蓝图时返回 None，不写空壳字段）。"""
        if not self._blueprint and not self._blueprint_info:
            return None
        return {
            "blueprint": self._blueprint,
            "blueprint_is_composed": bool(
                (self._blueprint_info or {}).get("is_composed")),
            "n_components": (self._blueprint_info or {}).get("n_components"),
            "n_edges": len(self._edges),
            "semantic_kinds": sorted({e.get("semantic") for e in self._edges
                                      if e.get("semantic")}),
        }

    def topology_note(self) -> dict:
        """给审计用的拓扑摘要（无拓扑时返回 None，不写空壳字段）。"""
        if not self._modules:
            return None
        return {
            "n_modules": len(self._modules),
            "n_agents_indexed": len(self._agents),
            "capabilities": sorted(self._cap_index),
            "profiles": sorted({m.get("profile_name") for m in self._modules
                                if m.get("profile_name")}),
        }

    # -- 主流程 ------------------------------------------------------------
    def run(self, task: TaskSpec, ctx) -> WorkerResult:
        t0 = time.time()
        req_scenarios = task.payload.get("scenarios") or []
        catalog = ctx.invoke_skill(self.role, "scenario.catalog", {})["scenarios"]
        known = {c["key"]: c for c in catalog}

        # 选题优先级：请求显式指定 > 上一轮飞轮给出的优先队列 > 目录默认顺序
        if req_scenarios:
            picked, source = [k for k in req_scenarios if k in known], "请求显式指定"
            unknown = [k for k in req_scenarios if k not in known]
        else:
            prev = (ctx.state.get("next_priority") or [])
            picked = [k for k in prev if k in known] or list(known)
            source = "上一轮 AgentLoop 优先队列" if prev else "目录默认顺序（首次运行）"
            unknown = []

        limit = int(task.payload.get("max_scenarios", 3))
        picked = picked[:limit]

        plan = []
        fallback_steps = []
        for k in picked:
            c = known[k]
            steps = ["virtual_experiment.validate",
                     "active_learning.rank_candidates",
                     "uq.calibration_report"]
            item = {
                "scenario": k,
                "domain": c["domain"],
                "ground_truth": c["published"],
                "steps": steps,
                "rationale": self._why(k, ctx),
            }
            if self._modules:
                # 仅拓扑路径产出 routing；默认路径保持输出逐字节不变
                item["routing"] = self._route_steps(steps, fallback_steps)
            plan.append(item)

        out = {
            "plan": plan,
            "selection_source": source,
            "unknown_scenarios": unknown,
            "catalog_size": len(catalog),
        }
        if self._modules:
            note = self.topology_note() or {}
            out["topology_routing"] = dict(
                note,
                n_steps_routed=sum(len(p.get("routing") or []) for p in plan),
                n_fallback=len(fallback_steps),
                fallback_steps=fallback_steps,
                note=("按 module.capability 路由；未命中者回退到 role 硬编码路径"
                      "（Executor 仍按 role 执行已授权 Skill）"),
            )
        # R6：蓝图语义路由（仅当本次运行由 blueprint / edge.semantic 驱动时产出）
        if self._blueprint or self._blueprint_info or self._edges:
            out["blueprint_routing"] = self._build_blueprint_routing()
        return self._result(task, "ok", out,
                            evidence=[f"catalog:{len(catalog)} scenarios"], started=t0)

    def _route_steps(self, steps, fallback_sink):
        """把一组步骤逐个路由；未命中记入 fallback_sink 并标 fallback=True。"""
        routed = []
        for step in steps:
            hit = None
            for hint in STEP_TASK_HINTS.get(step, ()):
                d = self.route_detail(hint)
                if not d["fallback"] and d["agents"]:
                    hit = d
                    break
            if hit is None:
                d = self.route_detail((STEP_TASK_HINTS.get(step) or [step])[0])
                d["agents"] = []
                d["fallback"] = True
                d["reason"] = d.get("reason") or f"步骤 {step} 无模块能力匹配"
                hit = d
                fallback_sink.append({"step": step, "task_hint": d["task_hint"],
                                      "reason": d["reason"]})
            routed.append({
                "step": step,
                "task_hint": hit["task_hint"],
                "module_index": hit["module_index"],
                "profile_name": hit["profile_name"],
                "capability_hit": hit["capability_hit"],
                "matched_by": hit["matched_by"],
                "agents": hit["agents"],
                "fallback": bool(hit["fallback"]),
                "reason": hit.get("reason") or "",
            })
        return routed

    @staticmethod
    def _why(key: str, ctx) -> str:
        hist = (ctx.state.get("history") or {}).get(key) or []
        if not hist:
            return "首次纳入验证，需要建立基线误差棒。"
        last = hist[-1]
        if last.get("maturity") == "已收敛":
            return f"上轮已收敛（rel={last['rel']}），本轮作为回归检查，防止修改引入退化。"
        return (f"上轮成熟度「{last.get('maturity')}」，相对误差 {last.get('rel')}，"
                f"仍有改进空间，优先安排。")
