"""运行上下文：Worker 唯一能接触外部世界的入口。

Worker **不允许**直接 import Skill 实现或直接读写文件。所有能力调用都必须走
`ctx.invoke_skill()`，这样才能保证：权限校验 → 审批闸门 → 回滚快照 → Trace
四件事一个都不落。这是"权限边界"能被评审核验的前提——不是写在文档里的承诺，
而是代码里绕不过去的通道。
"""

from __future__ import annotations

from typing import Any, Dict

from .contracts import Decision


class SkillBlocked(Exception):
    def __init__(self, decision: Decision):
        super().__init__(decision.reason)
        self.decision = decision


class Context:
    def __init__(self, cfg: Dict[str, Any], policy, tracer, registry,
                 run_dir: str, root: str, state: Dict[str, Any] = None,
                 run_id: str = ""):
        self.cfg = cfg
        self.run_id = run_id or getattr(tracer, "run_id", "")
        self.policy = policy
        self.tracer = tracer
        self.registry = registry
        self.run_dir = run_dir
        self.root = root
        self.state = state or {}
        self.blocked = []          # 被拦截的动作，供 Leader 汇总为"待人工确认"

    def stash_observation(self, label: str, content: str) -> Dict[str, Any]:
        """SoL-Pi ObservationPack 入口：大块工具输出存盘，上下文只留句柄。

        调用方拿到返回的 handle 后，只需把 handle 放进 trace / state，
        需要全文时再 ``recall_observation(handle_id)``。避免把 GP 训练日志、
        validation 长表等长文本反复塞进上下文。
        """
        from .compactor import ObservationPack
        pack = ObservationPack(self.run_dir)
        return pack.stash(label, content)

    def recall_observation(self, handle_id: str) -> Optional[str]:
        """按句柄取回 ObservationsPack 中存盘的长文本。"""
        from .compactor import ObservationPack
        pack = ObservationPack(self.run_dir)
        return pack.recall(handle_id)

    def invoke_skill(self, actor_role: str, name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        skill = self.registry.get(name)
        spec = skill.spec
        with self.tracer.span(f"skill:{name}", kind="skill",
                              actor=actor_role, params=params) as sp:
            decision = self.policy.gate(
                actor=actor_role, skill_name=name,
                side_effect=spec.side_effect, risk=spec.risk,
                scope=getattr(spec, "scope", "run_local"),
                detail={"intent": spec.purpose[:60]},
            )
            if not decision.allow:
                self.blocked.append({
                    "skill": name, "actor": actor_role,
                    "reason": decision.reason,
                    "requires_approval": decision.requires_approval,
                })
                sp.set_output({"blocked": decision.reason})
                sp.status = "blocked"
                raise SkillBlocked(decision)
            out = skill.run(params, self)
            sp.set_output(out)
            return out
