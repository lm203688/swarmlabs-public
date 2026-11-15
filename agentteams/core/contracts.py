"""接口契约（Interface Contracts）。

设计理念
--------
本文件是整个多 Agent 系统的**唯一耦合面**。Leader、Worker、Skill、Runtime
之间只通过这里定义的数据结构通信，不直接互相 import 实现。带来三个后果：

1. **可替换性**：任何一个 Worker / Skill / Runtime 都可以被第三方实现替换，
   只要满足契约。切换到 AgentTeams（HiClaw）等外部运行时，只需新增一个
   Runtime 适配器（见 core/runtime.py），业务代码零改动。
2. **必要性可审查**：每个 Skill 必须在 SkillSpec 里声明 side_effect / risk /
   failure_modes，评审可直接判断"这个工具是否必要、边界在哪"。
3. **可审计**：所有跨角色消息都是可序列化的 dataclass，天然可写入 trace。

注：本项目刻意不引入 LLM 依赖。Agent 的"智能"来自确定性的科学计算与
统计判据（GP 后验、UQ 校准），因此每一步都可复现、可验证、可回滚。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Sequence, Tuple

RISK_LEVELS = ("low", "medium", "high")
SIDE_EFFECTS = ("read", "write", "external")


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


# ---------------------------------------------------------------------------
# 任务 / 结果
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TaskSpec:
    """Leader 派发给 Worker 的最小任务单元。"""

    task_id: str
    role: str                      # 目标 Worker 角色：planner / executor / verifier
    intent: str                    # 语义意图，用于 trace 与人类可读
    payload: Dict[str, Any] = field(default_factory=dict)
    depends_on: Tuple[str, ...] = ()   # 依赖的 task_id，构成 DAG
    risk: str = "low"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class WorkerResult:
    """Worker 回传给 Leader 的结构化结果。"""

    task_id: str
    role: str
    status: str                    # ok / blocked / error
    output: Dict[str, Any] = field(default_factory=dict)
    evidence: List[str] = field(default_factory=list)   # 证据：文件路径 / 论文引用 / span id
    cost_ms: int = 0
    error: str = ""
    # 与 status 正交：标记"因上游失败而跳过"，而非自身出错。
    # 两者在 status 上都是 error，但归因完全不同——前者是连锁反应，
    # 后者才是真正的故障点。看板据此区分 cancelled(跳过) 与 failed(自错)。
    skipped: bool = False

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    @property
    def self_failed(self) -> bool:
        """真正的故障点：自身报错，不是被上游拖累。"""
        return self.status == "error" and not self.skipped

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TeamResult:
    """一次完整团队运行的产物。"""

    run_id: str
    goal: str
    status: str
    results: List[Dict[str, Any]] = field(default_factory=list)
    loop_report: Dict[str, Any] = field(default_factory=dict)
    artifacts: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Skill 契约
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SkillSpec:
    """Skill 自描述。字段对齐赛题对 Skill 清单的要求（用途/输入输出/调用条件/
    依赖/失败处理/安全边界/复用价值），可由 registry 直接导出成清单文档。"""

    name: str
    version: str
    purpose: str
    inputs: Dict[str, str]
    outputs: Dict[str, str]
    preconditions: str
    side_effect: str               # read / write / external
    risk: str                      # low / medium / high
    failure_modes: str
    reuse_note: str
    depends_on_tools: Sequence[str] = ()
    # 写操作的影响范围：run_local = 只写本次运行目录（可丢弃）；
    # shared_state = 写跨运行共享的知识库（污染后果持久，必须审批 + 可回滚）。
    scope: str = "run_local"

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["depends_on_tools"] = list(self.depends_on_tools)
        return d


class Skill:
    """Skill 基类：一个可复用、可替换、自带安全声明的能力单元。"""

    spec: SkillSpec

    def run(self, params: Dict[str, Any], ctx: "Context") -> Dict[str, Any]:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Worker 契约
# ---------------------------------------------------------------------------
class Worker:
    """职能 Agent 基类。

    权限边界由 `allowed_skills` 静态声明，运行时由 Context.invoke_skill 强制校验：
    Worker 无法调用未授权 Skill，即使代码里写了也会被拒绝并记入审计。
    """

    role: str = "worker"
    capabilities: Sequence[str] = ()
    allowed_skills: Sequence[str] = ()

    def run(self, task: TaskSpec, ctx: "Context") -> WorkerResult:
        raise NotImplementedError

    # -- 便捷封装 ---------------------------------------------------------
    def _result(self, task: TaskSpec, status: str, output=None,
                evidence=None, started: float = None, error: str = "") -> WorkerResult:
        cost = int((time.time() - started) * 1000) if started else 0
        return WorkerResult(
            task_id=task.task_id, role=self.role, status=status,
            output=output or {}, evidence=evidence or [], cost_ms=cost, error=error,
        )


# ---------------------------------------------------------------------------
# 决策（权限闸门返回值）
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Decision:
    allow: bool
    reason: str
    requires_approval: bool = False
    approved: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
